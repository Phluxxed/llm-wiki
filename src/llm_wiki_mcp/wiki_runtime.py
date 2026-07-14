from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from llm_wiki_core.config import ContentConfig, inspect_wiki_config
from llm_wiki_core.compiler import compile_context as compile_wiki_context
from llm_wiki_core.contracts import CompileRequest, ContractError
from llm_wiki_core.legacy import LegacyRuntime
from llm_wiki_core.maintenance import build_candidate_proposal, build_maintenance_packet
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
    return _legacy_runtime(alias).overview()


def query_pages(
    alias: str,
    status: str | None = None,
    category: str | None = None,
    type: str | None = None,
    tag: str | None = None,
    stale: int | None = None,
    risks: bool = False,
) -> dict[str, Any]:
    return _legacy_runtime(alias).query(
        status=status,
        category=category,
        page_type=type,
        tag=tag,
        stale=stale,
        risks=risks,
    )


def links(alias: str, page: str) -> dict[str, Any]:
    runtime = _legacy_runtime(alias)
    return runtime.links(_page_or_error(page, runtime))


def backlinks(alias: str, page: str) -> dict[str, Any]:
    runtime = _legacy_runtime(alias)
    return runtime.backlinks(_page_or_error(page, runtime))


def around(alias: str, page: str, depth: int = 1) -> dict[str, Any]:
    runtime = _legacy_runtime(alias)
    resolved = _page_or_error(page, runtime)
    safe_depth = max(1, min(int(depth), 5))
    return runtime.around(resolved, depth=safe_depth)


def context_pack(alias: str, page: str, tokens: int = 12_000) -> dict[str, Any]:
    runtime = _legacy_runtime(alias)
    resolved = _page_or_error(page, runtime)
    safe_tokens = max(500, min(int(tokens), MAX_CONTEXT_TOKENS))
    return runtime.context_pack(resolved, tokens=safe_tokens)


def compiled_context(
    alias: str,
    question: str,
    *,
    seeds: list[str] | None = None,
    state_view: str = "current",
    target_bytes: int = 48_000,
    max_bytes: int = 192_000,
    target_items: int = 24,
    max_items: int = 96,
    max_estimated_tokens: int | None = None,
    contract_version: str = "1",
) -> dict[str, Any]:
    record = get_wiki(alias)
    request_data = {
        "contract_version": contract_version,
        "alias": record["alias"],
        "question": question,
        "seeds": seeds or [],
        "state_view": state_view,
        "budget": {
            "target_bytes": target_bytes,
            "max_bytes": max_bytes,
            "target_items": target_items,
            "max_items": max_items,
            "max_estimated_tokens": max_estimated_tokens,
        },
    }
    try:
        request = CompileRequest.from_mapping(request_data)
        return compile_wiki_context(record["path"], request).to_dict()
    except ContractError as exc:
        raise WikiMcpError(exc.code, exc.message, exc.details) from exc


def graph_health(alias: str) -> dict[str, Any]:
    return _legacy_runtime(alias).health()


def maintenance_candidates(alias: str, *, stale_after_days: int = 180) -> dict[str, Any]:
    record = get_wiki(alias)
    threshold = _bounded_int(stale_after_days, default=180, upper=3650)
    return build_maintenance_packet(
        record["path"],
        alias=record["alias"],
        stale_after_days=threshold,
    )


def maintenance_candidate_proposal(
    alias: str,
    *,
    kind: str,
    diagnostic: str,
    review_question: str,
    pages: list[str],
    evidence: list[dict[str, str]],
) -> dict[str, Any]:
    record = get_wiki(alias)
    try:
        return build_candidate_proposal(
            alias=record["alias"],
            kind=kind,
            diagnostic=diagnostic,
            review_question=review_question,
            pages=pages,
            evidence=evidence,
        )
    except ValueError as exc:
        raise WikiMcpError(
            "INVALID_INPUT",
            str(exc),
            {"surface": "wiki_build_maintenance_candidate"},
        ) from exc


def get_page(alias: str, page: str, max_chars: int = 4_000) -> dict[str, Any]:
    runtime = _legacy_runtime(alias)
    resolved = _page_or_error(page, runtime)
    limit = _bounded_int(max_chars, default=4_000, upper=MAX_PAGE_CHARS)
    return {
        "kind": "page",
        **runtime.page_record(resolved),
        "content": runtime.page_content(resolved, max_chars=limit),
    }


def get_source_excerpt(
    alias: str,
    page: str | None = None,
    source: str | None = None,
    max_chars: int = 1_600,
) -> dict[str, Any]:
    runtime = _legacy_runtime(alias)
    if bool(page) == bool(source):
        raise WikiMcpError(
            "INVALID_INPUT",
            "Provide exactly one of page or source",
            {"page": page, "source": source},
        )

    if page:
        resolved = _page_or_error(page, runtime)
        source = str(runtime.pages[resolved].frontmatter.get("source") or "")
        if not source:
            raise WikiMcpError(
                "SOURCE_NOT_FOUND",
                "Page has no source frontmatter",
                {"page": resolved},
            )

    assert source is not None
    source_path = runtime.source_path(source)
    if source_path is None:
        raise WikiMcpError(
            "INVALID_INPUT",
            "Source must be a relative path inside the configured sources directory",
            {"source": source},
        )
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


def _legacy_runtime(alias: str) -> LegacyRuntime:
    record = get_wiki(alias)
    wiki_root = Path(record["path"]).expanduser().resolve()
    inspection = inspect_wiki_config(wiki_root)
    if inspection.config is not None:
        content = inspection.config.content
    elif inspection.status == "legacy_missing":
        content = ContentConfig()
    else:
        assert inspection.error is not None
        raise WikiMcpError(
            inspection.error.code,
            inspection.error.message,
            inspection.error.details,
        )
    return LegacyRuntime(wiki_root, content=content)


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


def _page_or_error(raw: str, runtime: LegacyRuntime) -> str:
    page = str(raw).replace("\\", "/")
    page = page[2:] if page.startswith("./") else page
    if page in runtime.pages:
        return page
    matches = difflib.get_close_matches(page, sorted(runtime.pages), n=5)
    raise WikiMcpError(
        "PAGE_NOT_FOUND",
        "Unknown wiki page",
        {"page": raw, "suggestions": matches},
    )


def _bounded_int(value: int, *, default: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, upper))
