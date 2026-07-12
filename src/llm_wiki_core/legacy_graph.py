"""Legacy dictionary-shaped graph API backed by the canonical page and graph core."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .config import ContentConfig, inspect_wiki_config
from .documents import (
    SYSTEM_EXCLUDE_DIRS,
    SYSTEM_EXCLUDE_FILES,
    WikiPage,
    collect_pages as collect_canonical_pages,
    page_type,
    parse_frontmatter,
    split_frontmatter_and_body,
)
from .graph import (
    ATTENTION_RE,
    BODY_LINK_RE,
    Edge,
    collect_log,
    collect_typed_edges as collect_canonical_edges,
    connected_components as canonical_components,
    extract_open_questions as canonical_open_questions,
    extract_open_risks as canonical_open_risks,
    graph_health as canonical_graph_health,
    incoming_edges,
    neighborhood as canonical_neighborhood,
    normalize_page_ref,
    outgoing_edges,
    related_pages as canonical_related_pages,
    resolve_link,
)


DEFAULT_EXCLUDE_FILES = set(SYSTEM_EXCLUDE_FILES)
DEFAULT_EXCLUDE_DIRS = set(SYSTEM_EXCLUDE_DIRS)


def collect_pages(
    wiki_root: Path,
    exclude_files: set[str] | None = None,
    exclude_dirs: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    root = Path(wiki_root).expanduser().resolve()
    inspection = inspect_wiki_config(root)
    if inspection.config is not None:
        content = inspection.config.content
    elif inspection.status == "legacy_missing":
        content = ContentConfig(exclude_directories=tuple(exclude_dirs or DEFAULT_EXCLUDE_DIRS))
    else:
        assert inspection.error is not None
        raise RuntimeError(f"{inspection.error.code}: {inspection.error.message}")
    pages = collect_canonical_pages(root, content=content)
    blocked_files = DEFAULT_EXCLUDE_FILES if exclude_files is None else exclude_files
    return {
        path: page.as_legacy_dict()
        for path, page in pages.items()
        if Path(path).name not in blocked_files
    }


def collect_typed_edges(pages: Mapping[str, dict[str, Any]]) -> list[Edge]:
    return collect_canonical_edges(_canonical_pages(pages))


def edge_pairs(edges: list[Edge]) -> list[tuple[str, str]]:
    return sorted({(edge.source, edge.target) for edge in edges})


def neighborhood(seed: str, pages: Mapping[str, dict[str, Any]], edges: list[Edge], depth: int = 1) -> list[dict]:
    return canonical_neighborhood(seed, _canonical_pages(pages), edges, depth=depth)


def related_pages(seed: str, pages: Mapping[str, dict[str, Any]], edges: list[Edge], depth: int = 2) -> list[dict]:
    return canonical_related_pages(seed, _canonical_pages(pages), edges, depth=depth)


def connected_components(pages: Mapping[str, dict[str, Any]], edges: list[Edge]) -> list[list[str]]:
    return canonical_components(_canonical_pages(pages), edges)


def graph_health(pages: Mapping[str, dict[str, Any]], edges: list[Edge], hub_limit: int = 10) -> dict:
    return canonical_graph_health(_canonical_pages(pages), edges, hub_limit=hub_limit)


def extract_open_questions(page: dict[str, Any]) -> list[str]:
    return canonical_open_questions(_canonical_page(page))


def extract_open_risks(page: dict[str, Any]) -> list[dict]:
    return canonical_open_risks(_canonical_page(page))


def _canonical_pages(pages: Mapping[str, dict[str, Any]]) -> dict[str, WikiPage]:
    return {path: _canonical_page(page, path=path) for path, page in pages.items()}


def _canonical_page(page: dict[str, Any], *, path: str | None = None) -> WikiPage:
    frontmatter = page.get("fm") or {}
    resolved_path = path or str(page.get("path") or "")
    return WikiPage(
        path=resolved_path,
        title=str(page.get("title") or Path(resolved_path).stem),
        type=str(page.get("type") or page_type(frontmatter)),
        tags=tuple(str(tag) for tag in page.get("tags") or []),
        frontmatter=frontmatter,
        body=str(page.get("body") or ""),
        text=str(page.get("text") or ""),
    )


__all__ = [
    "ATTENTION_RE",
    "BODY_LINK_RE",
    "DEFAULT_EXCLUDE_DIRS",
    "DEFAULT_EXCLUDE_FILES",
    "Edge",
    "collect_log",
    "collect_pages",
    "collect_typed_edges",
    "connected_components",
    "edge_pairs",
    "extract_open_questions",
    "extract_open_risks",
    "graph_health",
    "incoming_edges",
    "neighborhood",
    "normalize_page_ref",
    "outgoing_edges",
    "page_type",
    "parse_frontmatter",
    "related_pages",
    "resolve_link",
    "split_frontmatter_and_body",
]
