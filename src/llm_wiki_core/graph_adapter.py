from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import TYPE_CHECKING, Any

from .documents import WikiPage
from .graph import BODY_LINK_RE, collect_typed_edges, resolve_link

if TYPE_CHECKING:
    from .providers.base import ProviderContext


ADAPTER_SCHEMA_VERSION = 1
PROFILE_PATH = Path(".loci/graph/profiles/llm-wiki.json")
CONTRIBUTION_DIR = Path(".loci/graph/contributions")
MANIFEST_PATH = Path(".llm-wiki-graph-cache.json")
MAX_CONTRIBUTION_BYTES = 240_000
MAX_CONTRIBUTION_EDGES = 10_000
MAX_CONTRIBUTION_SHARDS = 256
_FRONTMATTER_FIELD = re.compile(r"^[ \t]*mentioned_in[ \t]*:")


class GraphAdapterError(RuntimeError):
    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class PreparedGraphMirror:
    root: Path
    input_digest: str
    page_roots: Mapping[str, str] | None


class PreparedGraphMirrorSession:
    def __init__(self, context: ProviderContext, prepared: PreparedGraphMirror):
        self._context = context
        self._prepared = prepared
        self._contributions_written = False

    @property
    def root(self) -> Path:
        return self._prepared.root

    @property
    def input_digest(self) -> str:
        return self._prepared.input_digest

    @property
    def page_roots(self) -> Mapping[str, str] | None:
        return self._prepared.page_roots

    def write_contributions(self, page_roots: Mapping[str, str]) -> None:
        roots = _validated_page_roots(page_roots, self._context.pages)
        records = _contribution_records(self._context.pages, roots)
        directory = self.root / CONTRIBUTION_DIR
        directory.mkdir(parents=True, exist_ok=True)
        for path in directory.glob("*.json"):
            if path.is_symlink() or not path.is_file():
                raise GraphAdapterError(
                    "LOCI_GRAPH_CACHE_UNSAFE",
                    "Graph contribution cache contains an unsafe entry",
                    {"path": str(path)},
                )
            path.unlink()
        _write_contribution_shards(directory, records)
        self._contributions_written = True

    def commit(self, page_roots: Mapping[str, str]) -> None:
        if not self._contributions_written:
            raise GraphAdapterError(
                "LOCI_GRAPH_CACHE_INCOMPLETE",
                "Graph mirror contributions must be written before commit",
            )
        roots = _validated_page_roots(page_roots, self._context.pages)
        payload = {
            "schema_version": ADAPTER_SCHEMA_VERSION,
            "input_digest": self.input_digest,
            "page_roots": roots,
        }
        _atomic_json(self.root / MANIFEST_PATH, payload)
        self._prepared = PreparedGraphMirror(self.root, self.input_digest, roots)


def graph_profile() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "namespace": "llm-wiki",
        "node_rules": [
            {
                "selector": {"language": "markdown", "page_root": True},
                "attributes": [
                    {
                        "name": "mentioned_in_refs",
                        "source": "frontmatter.mentioned_in",
                        "value_type": "string_list",
                        "allowed_values": [],
                    }
                ],
            }
        ],
        "edge_types": [
            {
                "type": "body_link",
                "directed": True,
                "allowed_resolutions": ["declared"],
            },
            {
                "type": "mentioned_in",
                "directed": True,
                "allowed_resolutions": ["declared"],
            },
        ],
        "edge_rules": [],
    }


@contextmanager
def open_graph_mirror(
    context: ProviderContext,
    *,
    cache_dir: str | Path | None = None,
) -> Iterator[PreparedGraphMirrorSession]:
    base = _cache_base(context.wiki_root, cache_dir)
    key = hashlib.sha256(str(context.wiki_root).encode("utf-8")).hexdigest()[:24]
    root = base / key
    lock_path = base / f"{key}.lock"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            digest = _input_digest(context.pages)
            roots = _cached_roots(root, digest, context.pages)
            if roots is None:
                _rebuild_mirror(root, context.pages)
            yield PreparedGraphMirrorSession(
                context,
                PreparedGraphMirror(root, digest, roots),
            )
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def canonical_page_roots(
    outline_files: Sequence[Mapping[str, Any]],
    expected_pages: Iterable[str],
) -> dict[str, str]:
    expected = tuple(sorted(dict.fromkeys(expected_pages)))
    candidates: dict[str, list[Mapping[str, Any]]] = {path: [] for path in expected}
    for file_group in outline_files:
        if not isinstance(file_group, Mapping):
            continue
        symbols = file_group.get("symbols")
        if not isinstance(symbols, list):
            continue
        for symbol in symbols:
            if not isinstance(symbol, Mapping):
                continue
            file_path = symbol.get("file_path")
            if not isinstance(file_path, str):
                file_path = file_group.get("file")
            metadata = symbol.get("metadata")
            markdown = metadata.get("markdown") if isinstance(metadata, Mapping) else None
            is_page_root = symbol.get("span_kind") == "page_root" or (
                isinstance(markdown, Mapping) and markdown.get("page_root") is True
            )
            if (
                file_path in candidates
                and is_page_root
                and isinstance(symbol.get("id"), str)
                and symbol.get("id")
                and str(symbol["id"]).startswith(f"{file_path}::")
            ):
                candidates[file_path].append(symbol)

    missing = sorted(path for path, values in candidates.items() if not values)
    if missing:
        raise GraphAdapterError(
            "LOCI_GRAPH_ROOT_MISSING",
            "Loci did not return a canonical page root for every wiki page",
            {"pages": missing[:20], "missing_count": len(missing)},
        )
    return {
        path: str(
            min(
                values,
                key=lambda item: (
                    int(item.get("line") or 0),
                    int(item.get("byte_offset") or 0),
                    str(item.get("id") or ""),
                ),
            )["id"]
        )
        for path, values in candidates.items()
    }


def _cache_base(wiki_root: Path, configured: str | Path | None) -> Path:
    raw = configured or os.environ.get("LLM_WIKI_GRAPH_CACHE_DIR")
    if raw is None:
        xdg = os.environ.get("XDG_CACHE_HOME")
        raw = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
        raw = Path(raw) / "llm-wiki" / "graph"
    base = Path(raw).expanduser().resolve()
    root = wiki_root.resolve()
    if base == root or base.is_relative_to(root):
        raise GraphAdapterError(
            "LOCI_GRAPH_CACHE_UNSAFE",
            "Graph cache directory must be outside the source wiki",
            {"cache": str(base)},
        )
    return base


def _input_digest(pages: Mapping[str, WikiPage]) -> str:
    payload = {
        "adapter_schema_version": ADAPTER_SCHEMA_VERSION,
        "profile": graph_profile(),
        "pages": [
            [path, hashlib.sha256(page.text.encode("utf-8")).hexdigest()]
            for path, page in sorted(pages.items())
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cached_roots(
    root: Path,
    digest: str,
    pages: Mapping[str, WikiPage],
) -> dict[str, str] | None:
    if not _mirror_matches(root, pages):
        return None
    path = root / MANIFEST_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != ADAPTER_SCHEMA_VERSION
        or payload.get("input_digest") != digest
    ):
        return None
    roots = payload.get("page_roots")
    if not isinstance(roots, Mapping):
        return None
    try:
        validated = _validated_page_roots(roots, pages)
    except GraphAdapterError:
        return None
    if not _contributions_match(root, pages, validated):
        return None
    return validated


def _mirror_matches(root: Path, pages: Mapping[str, WikiPage]) -> bool:
    if not root.is_dir() or root.is_symlink():
        return False
    try:
        if any(path.is_symlink() for path in root.rglob("*")):
            return False
        profile = root / PROFILE_PATH
        if json.loads(profile.read_text(encoding="utf-8")) != graph_profile():
            return False
        expected = set(pages)
        actual = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*.md")
            if path.is_file()
        }
        if actual != expected:
            return False
        return all((root / path).read_text(encoding="utf-8") == page.text for path, page in pages.items())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False


def _rebuild_mirror(root: Path, pages: Mapping[str, WikiPage]) -> None:
    if root.exists():
        if root.is_symlink() or not root.is_dir():
            raise GraphAdapterError(
                "LOCI_GRAPH_CACHE_UNSAFE",
                "Graph cache mirror is not a regular directory",
                {"path": str(root)},
            )
        shutil.rmtree(root)
    root.mkdir(parents=True, mode=0o700)
    for relative, page in sorted(pages.items()):
        path = _safe_page_path(root, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page.text, encoding="utf-8")
    profile_path = root / PROFILE_PATH
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(
        json.dumps(graph_profile(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / CONTRIBUTION_DIR).mkdir(parents=True, exist_ok=True)


def _safe_page_path(root: Path, relative: str) -> Path:
    value = PurePosixPath(relative)
    if (
        value.is_absolute()
        or ".." in value.parts
        or not value.parts
        or value.parts[0] == ".loci"
        or value.as_posix() != relative
    ):
        raise GraphAdapterError(
            "LOCI_GRAPH_PAGE_UNSAFE",
            "Wiki page path cannot be mirrored safely",
            {"page": relative},
        )
    path = root.joinpath(*value.parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise GraphAdapterError(
            "LOCI_GRAPH_PAGE_UNSAFE",
            "Wiki page path escapes the graph mirror",
            {"page": relative},
        )
    return path


def _validated_page_roots(
    page_roots: Mapping[str, Any],
    pages: Mapping[str, WikiPage],
) -> dict[str, str]:
    if set(page_roots) != set(pages):
        raise GraphAdapterError(
            "LOCI_GRAPH_ROOT_MISMATCH",
            "Loci page-root mapping does not match the wiki corpus",
        )
    roots: dict[str, str] = {}
    for path in sorted(pages):
        node_id = page_roots.get(path)
        if (
            not isinstance(node_id, str)
            or not node_id
            or not node_id.startswith(f"{path}::")
        ):
            raise GraphAdapterError(
                "LOCI_GRAPH_ROOT_MISMATCH",
                "Loci page-root mapping contains an invalid node id",
                {"page": path},
            )
        roots[path] = node_id
    return roots


def _contributions_match(
    root: Path,
    pages: Mapping[str, WikiPage],
    roots: Mapping[str, str],
) -> bool:
    expected = _contribution_records(pages, roots)
    directory = root / CONTRIBUTION_DIR
    actual: list[Mapping[str, Any]] = []
    try:
        for path in sorted(directory.glob("*.json")):
            if path.is_symlink() or not path.is_file():
                return False
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, Mapping)
                or payload.get("schema_version") != 1
                or payload.get("namespace") != "llm-wiki"
                or payload.get("nodes") != []
                or not isinstance(payload.get("edges"), list)
            ):
                return False
            actual.extend(payload["edges"])
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return actual == expected


def _contribution_records(
    pages: Mapping[str, WikiPage],
    roots: Mapping[str, str],
) -> list[dict[str, Any]]:
    hashes = {
        path: hashlib.sha256(page.text.encode("utf-8")).hexdigest()
        for path, page in pages.items()
    }
    records: list[dict[str, Any]] = []
    for edge in collect_typed_edges(pages):
        evidence_file, evidence_line = _edge_evidence(edge.type, edge.source, edge.target, pages)
        records.append(
            {
                "from": roots[edge.source],
                "to": roots[edge.target],
                "type": edge.type,
                "directed": True,
                "namespace": "llm-wiki",
                "resolution": "declared",
                "evidence": {
                    "file": evidence_file,
                    "line": evidence_line,
                    "content_hash": hashes[evidence_file],
                },
            }
        )
    return records


def _edge_evidence(
    edge_type: str,
    source_file: str,
    target_file: str,
    pages: Mapping[str, WikiPage],
) -> tuple[str, int]:
    if edge_type == "mentioned_in":
        lines = pages[target_file].text.splitlines()
        end = next((index for index, line in enumerate(lines[1:], start=2) if line.strip() == "---"), None)
        if end is not None:
            for line_number, line in enumerate(lines[1 : end - 1], start=2):
                if _FRONTMATTER_FIELD.match(line):
                    return target_file, line_number
        raise GraphAdapterError(
            "LOCI_GRAPH_EVIDENCE_MISSING",
            "mentioned_in edge has no exact authored frontmatter line",
            {"page": target_file},
        )
    if edge_type != "body_link":
        raise GraphAdapterError(
            "LOCI_GRAPH_EDGE_UNSUPPORTED",
            "Wiki graph contains an unsupported edge type",
            {"type": edge_type},
        )
    page = pages[source_file]
    start_line = _body_start_line(page.text)
    for line_number, line in enumerate(page.body.splitlines(), start=start_line):
        for raw in BODY_LINK_RE.findall(line):
            if resolve_link(raw, source_file, pages) == target_file:
                return source_file, line_number
    raise GraphAdapterError(
        "LOCI_GRAPH_EVIDENCE_MISSING",
        "body_link edge has no exact authored source line",
        {"from": source_file, "to": target_file},
    )


def _body_start_line(text: str) -> int:
    end = text.find("\n---", 3)
    if end < 0:
        return 1
    offset = end + 4
    while offset < len(text) and text[offset] == "\n":
        offset += 1
    return text[:offset].count("\n") + 1


def _write_contribution_shards(
    directory: Path,
    records: Sequence[Mapping[str, Any]],
) -> None:
    chunks: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    for record in records:
        candidate = [*current, record]
        encoded = _contribution_bytes(candidate)
        if (
            current
            and (len(encoded) > MAX_CONTRIBUTION_BYTES or len(candidate) > MAX_CONTRIBUTION_EDGES)
        ):
            chunks.append(current)
            current = [record]
            encoded = _contribution_bytes(current)
        else:
            current = candidate
        if len(encoded) > MAX_CONTRIBUTION_BYTES:
            raise GraphAdapterError(
                "LOCI_GRAPH_CONTRIBUTION_TOO_LARGE",
                "One graph edge exceeds the contribution shard limit",
            )
    if current:
        chunks.append(current)
    if len(chunks) > MAX_CONTRIBUTION_SHARDS:
        raise GraphAdapterError(
            "LOCI_GRAPH_CONTRIBUTION_TOO_LARGE",
            "Graph contribution requires too many shards",
            {"shards": len(chunks), "maximum": MAX_CONTRIBUTION_SHARDS},
        )
    for index, chunk in enumerate(chunks):
        (directory / f"llm-wiki-{index:03d}.json").write_bytes(_contribution_bytes(chunk))


def _contribution_bytes(records: Sequence[Mapping[str, Any]]) -> bytes:
    payload = {
        "schema_version": 1,
        "namespace": "llm-wiki",
        "nodes": [],
        "edges": list(records),
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
