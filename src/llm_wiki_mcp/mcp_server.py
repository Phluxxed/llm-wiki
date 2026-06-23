from __future__ import annotations

from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from llm_wiki_mcp.errors import WikiMcpError
from llm_wiki_mcp.registry import doctor, list_wikis, register_wiki, unregister_wiki
from llm_wiki_mcp.wiki_runtime import (
    around,
    backlinks,
    context_pack,
    get_page,
    get_source_excerpt,
    graph_health,
    links,
    overview,
    query_pages,
)


def create_server() -> FastMCP:
    mcp = FastMCP(
        "llm-wiki",
        log_level="ERROR",
        instructions=(
            "Local stdio MCP server for registered llm-wiki folders. "
            "Tools expose registry, navigation, graph health, page, source, "
            "and context-pack data without mutating wiki content."
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
