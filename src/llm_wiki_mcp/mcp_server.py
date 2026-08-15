from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from llm_wiki_mcp.errors import WikiMcpError
from llm_wiki_mcp.registry import doctor, list_wikis, register_wiki, unregister_wiki
from llm_wiki_mcp.wiki_runtime import (
    agent_manual,
    around,
    backlinks,
    context_pack,
    compiled_context,
    get_page,
    get_source_excerpt,
    graph_health,
    links,
    maintenance_candidate_proposal,
    maintenance_candidates,
    overview,
    query_pages,
    temporal_candidate_proposal,
    wiki_build_maintenance as unified_maintenance_proposal,
    wiki_reconcile_temporal_candidates as reconcile_temporal_candidates_runtime,
)


def create_server() -> FastMCP:
    mcp = FastMCP(
        "llm-wiki",
        log_level="ERROR",
        instructions=(
            "Local stdio MCP server for registered llm-wiki folders. "
            "Tools expose registry, navigation, graph health, page, source, "
            "context-pack, and compiled-context data without mutating wiki content."
        ),
    )

    @mcp.tool()
    def wiki_list() -> CallToolResult:
        """List wikis registered for the current agent runtime."""
        return _handle_wiki_error(list_wikis)

    @mcp.tool()
    def wiki_register(alias: str, path: str, created_by: str = "manual") -> CallToolResult:
        """Register an existing wiki path under an alias in the current agent registry."""
        return _handle_wiki_error(lambda: register_wiki(alias, path, created_by=created_by))

    @mcp.tool()
    def wiki_unregister(alias: str) -> CallToolResult:
        """Remove an alias from the current agent registry."""
        return _handle_wiki_error(lambda: unregister_wiki(alias))

    @mcp.tool()
    def wiki_doctor(alias: str) -> CallToolResult:
        """Validate that a registered wiki exists and has context tooling."""
        return _handle_wiki_error(lambda: doctor(alias))

    @mcp.tool()
    def wiki_agent_manual(
        alias: str,
        include_conventions: bool = True,
        max_chars: int = 120_000,
    ) -> CallToolResult:
        """Return the selected wiki's operating manual before any file mutation."""
        return _handle_wiki_error(
            lambda: agent_manual(
                alias,
                include_conventions=include_conventions,
                max_chars=max_chars,
            )
        )

    @mcp.tool()
    def wiki_overview(alias: str) -> CallToolResult:
        """Return the agent overview for a registered wiki alias."""
        return _handle_wiki_error(lambda: overview(alias))

    @mcp.tool()
    def wiki_query(
        alias: str,
        status: str | None = None,
        category: str | None = None,
        type: str | None = None,
        tag: str | None = None,
        stale: int | None = None,
        risks: bool = False,
    ) -> CallToolResult:
        """Return frontmatter query results for a registered wiki alias."""
        return _handle_wiki_error(
            lambda: query_pages(
                alias,
                status=status,
                category=category,
                type=type,
                tag=tag,
                stale=stale,
                risks=risks,
            )
        )

    @mcp.tool()
    def wiki_links(alias: str, page: str) -> CallToolResult:
        """Return outgoing graph links for a page."""
        return _handle_wiki_error(lambda: links(alias, page))

    @mcp.tool()
    def wiki_backlinks(alias: str, page: str) -> CallToolResult:
        """Return incoming graph links for a page."""
        return _handle_wiki_error(lambda: backlinks(alias, page))

    @mcp.tool()
    def wiki_around(alias: str, page: str, depth: int = 1) -> CallToolResult:
        """Return a bounded graph neighborhood around a page."""
        return _handle_wiki_error(lambda: around(alias, page, depth=depth))

    @mcp.tool()
    def wiki_context_pack(alias: str, page: str, tokens: int = 12_000) -> CallToolResult:
        """Return deterministic task context around a seed page."""
        return _handle_wiki_error(lambda: context_pack(alias, page, tokens=tokens))

    @mcp.tool()
    def wiki_compile_context(
        alias: str,
        question: str,
        seeds: list[str] | None = None,
        state_view: str = "current",
        target_bytes: int = 48_000,
        max_bytes: int = 192_000,
        target_items: int = 24,
        max_items: int = 96,
        max_estimated_tokens: int | None = None,
        contract_version: str = "1",
        temporal_view: str | None = None,
        request_time: str | None = None,
        world_at: str | None = None,
        known_at: str | None = None,
        transition_from: str | None = None,
        transition_to: str | None = None,
    ) -> CallToolResult:
        """Compile bounded, question-shaped evidence from a registered wiki."""
        return _handle_wiki_error(
            lambda: compiled_context(
                alias,
                question,
                seeds=seeds,
                state_view=state_view,
                target_bytes=target_bytes,
                max_bytes=max_bytes,
                target_items=target_items,
                max_items=max_items,
                max_estimated_tokens=max_estimated_tokens,
                contract_version=contract_version,
                temporal_view=temporal_view,
                request_time=request_time,
                world_at=world_at,
                known_at=known_at,
                transition_from=transition_from,
                transition_to=transition_to,
            )
        )

    @mcp.tool()
    def wiki_maintenance_candidates(
        alias: str,
        stale_after_days: int = 180,
    ) -> CallToolResult:
        """Return read-only, evidence-backed candidates for steward review."""
        return _handle_wiki_error(
            lambda: maintenance_candidates(alias, stale_after_days=stale_after_days)
        )

    @mcp.tool()
    def wiki_build_maintenance_candidate(
        alias: str,
        kind: str,
        diagnostic: str,
        review_question: str,
        pages: list[str],
        evidence: list[dict[str, str]],
    ) -> CallToolResult:
        """Build one canonical read-only maintenance proposal for later steward review."""
        return _handle_wiki_error(
            lambda: maintenance_candidate_proposal(
                alias,
                kind=kind,
                diagnostic=diagnostic,
                review_question=review_question,
                pages=pages,
                evidence=evidence,
            )
        )

    @mcp.tool()
    def wiki_build_temporal_candidates(
        alias: str,
        source: Mapping[str, Any],
        claims: list[Mapping[str, Any]],
        proposed_at: str,
    ) -> CallToolResult:
        """Build a read-only temporal candidate proposal from Codex semantic claims."""
        return _handle_wiki_error(
            lambda: temporal_candidate_proposal(
                alias,
                source=source,
                claims=claims,
                proposed_at=proposed_at,
            )
        )

    @mcp.tool()
    def wiki_build_maintenance(
        alias: str,
        source: Mapping[str, Any],
        intent: str,
        proposed_at: str,
        claims: list[Mapping[str, Any]] | None = None,
        pages: list[str] | None = None,
        evidence: list[Mapping[str, Any]] | None = None,
    ) -> CallToolResult:
        """Build one unified, candidate-only maintenance proposal."""
        return _handle_wiki_error(
            lambda: unified_maintenance_proposal(
                alias,
                source=source,
                intent=intent,
                claims=claims or [],
                pages=pages or [],
                evidence=evidence or [],
                proposed_at=proposed_at,
            )
        )

    @mcp.tool()
    def wiki_reconcile_temporal_candidates(
        alias: str,
        proposals: list[Mapping[str, Any]],
    ) -> CallToolResult:
        """Reconcile read-only temporal candidate proposals for one wiki."""
        return _handle_wiki_error(lambda: reconcile_temporal_candidates_runtime(alias, proposals))

    @mcp.tool()
    def wiki_get_page(alias: str, page: str, max_chars: int = 4_000) -> CallToolResult:
        """Return page metadata and bounded page content."""
        return _handle_wiki_error(lambda: get_page(alias, page, max_chars=max_chars))

    @mcp.tool()
    def wiki_get_source_excerpt(
        alias: str,
        page: str | None = None,
        source: str | None = None,
        max_chars: int = 1_600,
    ) -> CallToolResult:
        """Return a bounded excerpt from a source file referenced by page or source path."""
        return _handle_wiki_error(
            lambda: get_source_excerpt(alias, page=page, source=source, max_chars=max_chars)
        )

    @mcp.tool()
    def wiki_graph_health(alias: str) -> CallToolResult:
        """Return graph health, hubs, orphans, components, and source gaps."""
        return _handle_wiki_error(lambda: graph_health(alias))

    return mcp


def _handle_wiki_error(operation: Callable[[], dict[str, Any]]) -> CallToolResult:
    try:
        return _success(operation())
    except WikiMcpError as exc:
        return CallToolResult(
            content=[TextContent(type="text", text=f"{exc.code}: {exc.message}")],
            structuredContent={"error": exc.to_dict()},
            isError=True,
        )
    except Exception as exc:
        return CallToolResult(
            content=[TextContent(type="text", text=f"UNEXPECTED_ERROR: {type(exc).__name__}")],
            structuredContent={
                "error": {
                    "code": "UNEXPECTED_ERROR",
                    "message": "Unexpected llm-wiki MCP server error",
                    "details": {"type": type(exc).__name__},
                }
            },
            isError=True,
        )


def _success(payload: dict[str, Any]) -> CallToolResult:
    return CallToolResult(content=[], structuredContent=payload, isError=False)


mcp = create_server()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
