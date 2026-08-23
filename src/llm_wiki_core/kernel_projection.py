from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Mapping

from .contracts import Coverage, Diagnostic, EvidenceRecord, Omission, ResponseReporting, StopState
from .snapshot import SnapshotResolution, resolve_snapshot


KERNEL_PROJECTION_CONTRACT_VERSION = "1"
KERNEL_TARGET_CONTENT_BYTES = 3_072
KERNEL_MAX_CONTENT_BYTES = 4_096
_ATX_HEADING = re.compile(r"^(?: {0,3})(#{1,6})(?:[ \t]+(.*?)[ \t]*|[ \t]*)$")
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_SETEXT_UNDERLINE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")


class KernelProjectionError(ValueError):
    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True)
class KernelSource:
    role: str
    page: str
    section: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "KernelSource":
        if not isinstance(raw, Mapping):
            raise KernelProjectionError(
                "KERNEL_SOURCE_INVALID",
                "Each kernel source must be an object",
            )
        required = {"role", "page", "section"}
        if set(raw) != required:
            raise KernelProjectionError(
                "KERNEL_SOURCE_INVALID",
                "Each kernel source must contain exactly role, page, and section",
                {"missing": sorted(required - set(raw)), "unknown": sorted(set(raw) - required)},
            )
        values: dict[str, str] = {}
        for field_name in ("role", "page", "section"):
            value = raw[field_name]
            if not isinstance(value, str) or not value or value != value.strip():
                raise KernelProjectionError(
                    "KERNEL_SOURCE_INVALID",
                    "Kernel source fields must be non-empty strings without surrounding whitespace",
                    {"field": field_name},
                )
            values[field_name] = value
        return cls(**values)

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "page": self.page, "section": self.section}


@dataclass(frozen=True)
class KernelBudgetUsage:
    content_bytes: int
    target_exceeded: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "limits": {
                "target_content_bytes": KERNEL_TARGET_CONTENT_BYTES,
                "max_content_bytes": KERNEL_MAX_CONTENT_BYTES,
            },
            "content_bytes": self.content_bytes,
            "target_exceeded": self.target_exceeded,
            "accounting": "utf-8",
        }


@dataclass(frozen=True)
class KernelProjection:
    snapshot: SnapshotResolution
    sources: tuple[KernelSource, ...]
    evidence: tuple[EvidenceRecord, ...]
    omissions: tuple[Omission, ...]
    coverage: Coverage
    budget: KernelBudgetUsage
    stop: StopState
    diagnostics: tuple[Diagnostic, ...]
    reporting: ResponseReporting

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "collaboration_kernel_projection",
            "contract_version": KERNEL_PROJECTION_CONTRACT_VERSION,
            "wiki": {
                "alias": self.snapshot.alias,
                "digest": self.snapshot.digest,
                "snapshot_status": self.snapshot.status,
            },
            "projection": {
                "selection": "explicit_ordered_sources",
                "sources": [source.to_dict() for source in self.sources],
                "mandatory_sections": True,
            },
            "evidence": [record.to_dict() for record in self.evidence],
            "omissions": [omission.to_dict() for omission in self.omissions],
            "coverage": self.coverage.to_dict(),
            "budget": self.budget.to_dict(),
            "stop": self.stop.to_dict(),
            "continuation": None,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "reporting": self.reporting.to_dict(),
        }


def compile_kernel_projection(
    *,
    alias: str,
    output_root: str | Path,
    sources: Iterable[KernelSource | Mapping[str, Any]],
) -> KernelProjection:
    """Project explicitly named Markdown sections from one verified immutable snapshot."""
    requested = _normalize_sources(sources)
    snapshot = resolve_snapshot(alias=alias, output_root=output_root)
    records: list[EvidenceRecord] = []
    for index, source in enumerate(requested):
        page_path = _snapshot_page(snapshot, source.page)
        try:
            text = page_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise KernelProjectionError(
                "KERNEL_PAGE_UNREADABLE",
                "Kernel source page could not be read as UTF-8",
                {"page": source.page, "reason": type(exc).__name__},
            ) from exc
        content, locator = _exact_section(text, page=source.page, section=source.section)
        content_bytes = len(content.encode("utf-8"))
        identity = json.dumps(
            {
                "digest": snapshot.digest,
                "index": index,
                "role": source.role,
                "page": source.page,
                "section": source.section,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        records.append(
            EvidenceRecord(
                id=f"kernel:{index}:{hashlib.sha256(identity).hexdigest()[:16]}",
                provider="snapshot_section",
                route="explicit_ordered_source",
                page=source.page,
                source=f"snapshot:{snapshot.alias}:{snapshot.digest}:{source.page}",
                locator={"role": source.role, "source_index": index, **locator},
                content=content,
                roles=(source.role,),
                authored_state="authored",
                derived_flags=(),
                authority_signals=("immutable_snapshot", "exact_section_match"),
                selection_reasons=("explicit_source", "source_order"),
                byte_cost=content_bytes,
                truncated=False,
                atomic=True,
            )
        )

    content_bytes = sum(record.byte_cost for record in records)
    roles = tuple(source.role for source in requested)
    if content_bytes > KERNEL_MAX_CONTENT_BYTES:
        raise KernelProjectionError(
            "KERNEL_CONTENT_CEILING_EXCEEDED",
            "Mandatory kernel sections exceed the hard UTF-8 content ceiling",
            {
                "content_bytes": content_bytes,
                "max_content_bytes": KERNEL_MAX_CONTENT_BYTES,
                "roles": list(roles),
                "fitting": "refused_atomic_sources",
            },
        )
    diagnostics: tuple[Diagnostic, ...] = ()
    if content_bytes > KERNEL_TARGET_CONTENT_BYTES:
        diagnostics = (
            Diagnostic(
                code="KERNEL_TARGET_EXCEEDED",
                message="Mandatory kernel sections exceed the target UTF-8 content size",
                provider="snapshot_section",
                details={
                    "content_bytes": content_bytes,
                    "target_content_bytes": KERNEL_TARGET_CONTENT_BYTES,
                    "max_content_bytes": KERNEL_MAX_CONTENT_BYTES,
                },
            ),
        )
    return KernelProjection(
        snapshot=snapshot,
        sources=requested,
        evidence=tuple(records),
        omissions=(),
        coverage=Coverage(required_roles=roles, covered_roles=roles, uncovered_roles=()),
        budget=KernelBudgetUsage(
            content_bytes=content_bytes,
            target_exceeded=content_bytes > KERNEL_TARGET_CONTENT_BYTES,
        ),
        stop=StopState(
            reason="all_sources_projected",
            sufficient=True,
            detail="Every explicit mandatory source section was projected in caller order",
        ),
        diagnostics=diagnostics,
        reporting=ResponseReporting(0, 0, len(diagnostics), len(diagnostics)),
    )


def _normalize_sources(
    sources: Iterable[KernelSource | Mapping[str, Any]],
) -> tuple[KernelSource, ...]:
    if isinstance(sources, (str, bytes, Mapping)):
        raise KernelProjectionError(
            "KERNEL_SOURCE_INVALID",
            "Kernel sources must be an ordered iterable of source objects",
        )
    requested = tuple(
        KernelSource.from_mapping(source.to_dict() if isinstance(source, KernelSource) else source)
        for source in sources
    )
    if not requested:
        raise KernelProjectionError(
            "KERNEL_ROLE_MISSING",
            "At least one explicit semantic role is required",
        )
    seen: set[str] = set()
    for index, source in enumerate(requested):
        if source.role in seen:
            raise KernelProjectionError(
                "KERNEL_ROLE_DUPLICATE",
                "Each kernel semantic role must appear exactly once",
                {"role": source.role, "source_index": index},
            )
        seen.add(source.role)
    return requested


def _snapshot_page(snapshot: SnapshotResolution, page: str) -> Path:
    relative = PurePosixPath(page)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "\\" in page
        or "\x00" in page
        or relative.as_posix() != page
        or relative.suffix.lower() != ".md"
    ):
        raise KernelProjectionError(
            "KERNEL_PAGE_PATH_UNSAFE",
            "Kernel source page must be a canonical safe snapshot-relative Markdown path",
            {"page": page},
        )
    path = snapshot.snapshot_wiki_root.joinpath(*relative.parts)
    if not path.is_file():
        raise KernelProjectionError(
            "KERNEL_PAGE_NOT_FOUND",
            "Kernel source page does not exist in the resolved snapshot",
            {"page": page},
        )
    return path


def _exact_section(text: str, *, page: str, section: str) -> tuple[str, dict[str, Any]]:
    lines = text.splitlines(keepends=True)
    headings = _markdown_headings(lines)
    matches = [heading for heading in headings if heading[2] == section]
    if len(matches) != 1:
        code = "KERNEL_SECTION_NOT_FOUND" if not matches else "KERNEL_SECTION_AMBIGUOUS"
        raise KernelProjectionError(
            code,
            "Kernel source section must resolve to exactly one Markdown heading",
            {"page": page, "section": section, "matches": len(matches)},
        )
    heading_line, level, _, body_start_line = matches[0]
    end_line = len(lines)
    for candidate_line, candidate_level, _, _ in headings:
        if candidate_line > heading_line and candidate_level <= level:
            end_line = candidate_line
            break
    return "".join(lines[body_start_line:end_line]), {
        "section": section,
        "heading_level": level,
        "heading_line": heading_line + 1,
        "content_start_line": body_start_line + 1,
        "content_end_line": end_line,
    }


def _markdown_headings(lines: list[str]) -> list[tuple[int, int, str, int]]:
    headings: list[tuple[int, int, str, int]] = []
    fence_character: str | None = None
    fence_length = 0
    frontmatter_end = -1
    if lines and lines[0].rstrip("\r\n") == "---":
        for index in range(1, len(lines)):
            if lines[index].rstrip("\r\n") == "---":
                frontmatter_end = index
                break
    for index, line in enumerate(lines):
        if index <= frontmatter_end:
            continue
        bare = line.rstrip("\r\n")
        if fence_character is not None:
            closing = re.fullmatch(
                rf" {{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*",
                bare,
            )
            if closing is not None:
                fence_character = None
                fence_length = 0
            continue
        opening = _FENCE_OPEN.match(bare)
        if opening is not None:
            marker, info = opening.groups()
            if marker[0] == "~" or "`" not in info:
                fence_character = marker[0]
                fence_length = len(marker)
                continue
        match = _ATX_HEADING.match(bare)
        if match is not None:
            heading_text = re.sub(r"[ \t]+#+[ \t]*$", "", match.group(2) or "")
            headings.append((index, len(match.group(1)), heading_text, index + 1))
            continue
        underline = _SETEXT_UNDERLINE.match(bare)
        if underline is None or index == 0 or index - 1 <= frontmatter_end:
            continue
        title = lines[index - 1].rstrip("\r\n")
        if not title.strip() or _ATX_HEADING.match(title) is not None:
            continue
        level = 1 if underline.group(1).startswith("=") else 2
        headings.append((index - 1, level, title.strip(), index + 1))
    return headings
