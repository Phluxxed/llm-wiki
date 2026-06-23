from __future__ import annotations

import difflib
import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from llm_wiki_mcp.errors import WikiMcpError
from llm_wiki_mcp.registry import doctor, get_wiki


MAX_MANUAL_CHARS = 120_000
MAX_PAGE_CHARS = 40_000
MAX_SOURCE_CHARS = 40_000
MAX_CONTEXT_TOKENS = 50_000


def agent_manual(alias: str, include_conventions: bool = True, max_chars: int = MAX_MANUAL_CHARS) -> dict[str, Any]:
    record = get_wiki(alias)
    wiki_root = Path(record["path"]).expanduser().resolve()
    limit = _bounded_int(max_chars, default=MAX_MANUAL_CHARS, upper=MAX_MANUAL_CHARS)
    manual = _read_control_file(wiki_root, "wiki-agent.md", max_chars=limit, required=True)
    conventions = None
    if include_conventions:
        conventions = _read_control_file(wiki_root, "CONVENTIONS.md", max_chars=limit, required=False)

    return {
        "kind": "wiki_agent_manual",
        "alias": record["alias"],
        "path": str(wiki_root),
        "operating_manual_path": "wiki-agent.md",
        "operating_manual": manual["content"],
        "operating_manual_truncated": manual["truncated"],
        "conventions_path": "CONVENTIONS.md" if conventions is not None else None,
        "conventions": conventions["content"] if conventions is not None else None,
        "conventions_truncated": conventions["truncated"] if conventions is not None else False,
        "must_follow": [
            "Read and obey operating_manual before mutating this wiki",
            "Do not edit sources/",
            "Update index.md when adding or moving pages",
            "Append log.md for wiki changes",
            "Run lint/render after ingest or structural changes",
        ],
        "doctor": doctor(alias),
    }


def overview(alias: str) -> dict[str, Any]:
    query = _load_query(alias)
    pages, edges = query._graph()
    return query.agent_overview_data(pages, edges)


def query_pages(
    alias: str,
    status: str | None = None,
    category: str | None = None,
    type: str | None = None,
    tag: str | None = None,
    stale: int | None = None,
    risks: bool = False,
) -> dict[str, Any]:
    query = _load_query(alias)
    pages = query.collect_pages()
    args = SimpleNamespace(
        status=status,
        category=category,
        type=type,
        tag=tag,
        stale=stale,
    )
    filtered = query.apply_filters(pages, args)
    return query.risks_data(filtered) if risks else query.summary_data(filtered)


def links(alias: str, page: str) -> dict[str, Any]:
    query = _load_query(alias)
    pages, edges = query._graph()
    resolved = _page_or_error(query, page, pages)
    return {
        "kind": "links",
        **query._page_record(resolved, pages),
        "links": query._link_records(resolved, pages, edges),
    }


def backlinks(alias: str, page: str) -> dict[str, Any]:
    query = _load_query(alias)
    pages, edges = query._graph()
    resolved = _page_or_error(query, page, pages)
    return {
        "kind": "backlinks",
        **query._page_record(resolved, pages),
        "backlinks": query._backlink_records(resolved, pages, edges),
    }


def around(alias: str, page: str, depth: int = 1) -> dict[str, Any]:
    query = _load_query(alias)
    pages, edges = query._graph()
    resolved = _page_or_error(query, page, pages)
    safe_depth = max(1, min(int(depth), 5))
    return {
        "kind": "around",
        "seed": query._page_record(resolved, pages),
        "depth": safe_depth,
        "pages": query._around_records(resolved, pages, edges, safe_depth),
    }


def context_pack(alias: str, page: str, tokens: int = 12_000) -> dict[str, Any]:
    query = _load_query(alias)
    pages, edges = query._graph()
    resolved = _page_or_error(query, page, pages)
    safe_tokens = max(500, min(int(tokens), MAX_CONTEXT_TOKENS))
    return query.build_context_pack_data(resolved, pages, edges, safe_tokens)


def graph_health(alias: str) -> dict[str, Any]:
    query = _load_query(alias)
    pages, edges = query._graph()
    return query.graph_health_data(pages, edges)


def get_page(alias: str, page: str, max_chars: int = 4_000) -> dict[str, Any]:
    query = _load_query(alias)
    pages, _edges = query._graph()
    resolved = _page_or_error(query, page, pages)
    limit = _bounded_int(max_chars, default=4_000, upper=MAX_PAGE_CHARS)
    return {
        "kind": "page",
        **query._page_record(resolved, pages),
        "content": query._page_content(pages[resolved], max_chars=limit),
    }


def get_source_excerpt(
    alias: str,
    page: str | None = None,
    source: str | None = None,
    max_chars: int = 1_600,
) -> dict[str, Any]:
    query = _load_query(alias)
    wiki_root = Path(query.WIKI_ROOT).resolve()
    if bool(page) == bool(source):
        raise WikiMcpError(
            "INVALID_INPUT",
            "Provide exactly one of page or source",
            {"page": page, "source": source},
        )

    if page:
        pages, _edges = query._graph()
        resolved = _page_or_error(query, page, pages)
        source = str(pages[resolved]["fm"].get("source") or "")
        if not source:
            raise WikiMcpError(
                "SOURCE_NOT_FOUND",
                "Page has no source frontmatter",
                {"page": resolved},
            )

    assert source is not None
    source_path = _safe_source_path(wiki_root, source)
    if not source_path.is_file():
        raise WikiMcpError(
            "SOURCE_NOT_FOUND",
            "Source file not found",
            {"source": source},
        )
    try:
        text = source_path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as exc:
        raise WikiMcpError(
            "SOURCE_NOT_TEXT",
            "Source file is not UTF-8 text",
            {"source": source},
        ) from exc

    limit = _bounded_int(max_chars, default=1_600, upper=MAX_SOURCE_CHARS)
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n\n[truncated]"
    return {
        "kind": "source_excerpt",
        "source": str(source).replace("\\", "/"),
        "content": text,
    }


def _load_query(alias: str) -> ModuleType:
    record = get_wiki(alias)
    wiki_root = Path(record["path"]).expanduser().resolve()
    scripts_dir = wiki_root / "scripts"
    query_path = scripts_dir / "query.py"
    graph_path = scripts_dir / "wiki_graph.py"
    missing = [str(path.relative_to(wiki_root)) for path in (query_path, graph_path) if not path.is_file()]
    if missing:
        raise WikiMcpError(
            "TOOLING_MISSING",
            "Wiki is missing graph/context tooling",
            {"alias": alias, "missing": missing},
        )

    digest = hashlib.sha1(str(wiki_root).encode("utf-8")).hexdigest()[:12]
    graph_name = f"_llm_wiki_{digest}_wiki_graph"
    query_name = f"_llm_wiki_{digest}_query"

    graph = _load_module(graph_name, graph_path)
    previous_graph = sys.modules.get("wiki_graph")
    sys.modules["wiki_graph"] = graph
    try:
        query = _load_module(query_name, query_path)
    finally:
        if previous_graph is None:
            sys.modules.pop("wiki_graph", None)
        else:
            sys.modules["wiki_graph"] = previous_graph

    query.WIKI_ROOT = wiki_root
    return query


def _read_control_file(wiki_root: Path, filename: str, max_chars: int, required: bool) -> dict[str, Any] | None:
    path = (wiki_root / filename).resolve()
    if path.parent != wiki_root:
        raise WikiMcpError(
            "INVALID_INPUT",
            "Control file must be at wiki root",
            {"file": filename},
        )
    if not path.is_file():
        if required:
            raise WikiMcpError(
                "CONTROL_FILE_MISSING",
                "Wiki control file is missing",
                {"file": filename, "path": str(path)},
            )
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise WikiMcpError(
            "CONTROL_FILE_NOT_TEXT",
            "Wiki control file is not UTF-8 text",
            {"file": filename},
        ) from exc

    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars].rstrip() + "\n\n[truncated]"
    return {"content": content, "truncated": truncated}


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise WikiMcpError(
            "TOOLING_LOAD_FAILED",
            "Could not load wiki tooling module",
            {"path": str(path)},
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise WikiMcpError(
            "TOOLING_LOAD_FAILED",
            "Wiki tooling module failed to load",
            {"path": str(path), "error": str(exc)},
        ) from exc
    return module


def _page_or_error(query: ModuleType, raw: str, pages: dict[str, dict]) -> str:
    page = query.wiki_graph.normalize_page_ref(raw)
    if page in pages:
        return page
    matches = difflib.get_close_matches(page, sorted(pages), n=5)
    raise WikiMcpError(
        "PAGE_NOT_FOUND",
        "Unknown wiki page",
        {"page": raw, "suggestions": matches},
    )


def _safe_source_path(wiki_root: Path, source: str) -> Path:
    normalized = str(source).replace("\\", "/")
    if not normalized or normalized.startswith("/") or "\x00" in normalized:
        raise WikiMcpError(
            "INVALID_INPUT",
            "Source must be a relative path inside sources/",
            {"source": source},
        )
    if not normalized.startswith("sources/") or ".." in Path(normalized).parts:
        raise WikiMcpError(
            "INVALID_INPUT",
            "Source must be a relative path inside sources/",
            {"source": source},
        )
    sources_root = (wiki_root / "sources").resolve()
    source_path = (wiki_root / normalized).resolve()
    if not source_path.is_relative_to(sources_root):
        raise WikiMcpError(
            "INVALID_INPUT",
            "Source must stay inside the wiki sources directory",
            {"source": source},
        )
    return source_path


def _bounded_int(value: int, *, default: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, upper))
