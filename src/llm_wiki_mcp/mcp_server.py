from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
from typing import Annotated, Any, Callable, Literal

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from llm_wiki_mcp.errors import WikiMcpError
from llm_wiki_mcp.input_models import (
    MaintenanceClaimInput,
    MaintenanceEvidenceInput,
    TemporalMaintenanceSourceInput,
    UnifiedMaintenanceSourceInput,
    WholeSecondTimestamp,
)
from llm_wiki_mcp.output_models import (
    WikiAgentManualOutput,
    WikiAroundOutput,
    WikiBacklinksOutput,
    WikiBuildMaintenanceCandidateOutput,
    WikiBuildMaintenanceOutput,
    WikiBuildTemporalCandidatesOutput,
    WikiCompileContextOutput,
    WikiContextPackOutput,
    WikiDoctorOutput,
    WikiGetPageOutput,
    WikiGetSourceExcerptOutput,
    WikiGraphHealthOutput,
    WikiLinksOutput,
    WikiListOutput,
    WikiMaintenanceCandidatesOutput,
    WikiOverviewOutput,
    WikiQueryOutput,
    WikiReconcileTemporalCandidatesOutput,
    WikiRegisterOutput,
    WikiUnregisterOutput,
)
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


SUCCESS_MARKER = "OK: structured result available; inspect structuredContent."


class _LlmWikiMCPServer(MCPServer):
    """Publish the maintenance input shape without requiring local-ref resolution."""

    async def list_tools(self) -> list[Any]:
        tools = await super().list_tools()
        return [
            tool.model_copy(
                update={"input_schema": _inline_maintenance_input_schema(tool.input_schema)}
            )
            if tool.name == "wiki_build_maintenance"
            else tool
            for tool in tools
        ]


def _inline_maintenance_input_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline the acyclic maintenance models for reference-limited MCP hosts.

    ``MaintenanceJsonValue`` intentionally remains a local reference: it is the
    sole recursive model, while source, claim, time, entity, and evidence
    shapes become traversable directly from the tool's input schema.
    """

    definitions = schema.get("$defs")
    if not isinstance(definitions, Mapping):
        return deepcopy(schema)

    def inline(value: Any) -> Any:
        if isinstance(value, list):
            return [inline(item) for item in value]
        if not isinstance(value, Mapping):
            return value

        reference = value.get("$ref")
        if (
            isinstance(reference, str)
            and reference.startswith("#/$defs/")
            and reference != "#/$defs/MaintenanceJsonValue"
        ):
            definition = definitions.get(reference.removeprefix("#/$defs/"))
            if isinstance(definition, Mapping):
                return {
                    **inline(definition),
                    **{key: inline(item) for key, item in value.items() if key != "$ref"},
                }

        return {
            key: deepcopy(item) if key == "$defs" else inline(item)
            for key, item in value.items()
        }

    return inline(schema)


def create_server() -> MCPServer:
    mcp = _LlmWikiMCPServer(
        "llm-wiki",
        log_level="ERROR",
        instructions=(
            "Local stdio MCP server for registered llm-wiki folders. "
            "Tools expose registry, navigation, graph health, page, source, "
            "context-pack, and compiled-context data without mutating wiki content. "
            "Successful text content is only a marker; structuredContent is the "
            "authoritative result payload."
        ),
    )

    @mcp.tool()
    def wiki_list() -> Annotated[CallToolResult, WikiListOutput]:
        """List wikis registered for the current agent runtime."""
        return _handle_wiki_error(list_wikis)

    @mcp.tool()
    def wiki_register(
        alias: str, path: str, created_by: str = "manual"
    ) -> Annotated[CallToolResult, WikiRegisterOutput]:
        """Register an existing wiki path under an alias in the current agent registry."""
        return _handle_wiki_error(lambda: register_wiki(alias, path, created_by=created_by))

    @mcp.tool()
    def wiki_unregister(alias: str) -> Annotated[CallToolResult, WikiUnregisterOutput]:
        """Remove an alias from the current agent registry."""
        return _handle_wiki_error(lambda: unregister_wiki(alias))

    @mcp.tool()
    def wiki_doctor(alias: str) -> Annotated[CallToolResult, WikiDoctorOutput]:
        """Validate that a registered wiki exists and has context tooling."""
        return _handle_wiki_error(lambda: doctor(alias))

    @mcp.tool()
    def wiki_agent_manual(
        alias: str,
        include_conventions: bool = True,
        max_chars: int = 120_000,
    ) -> Annotated[CallToolResult, WikiAgentManualOutput]:
        """Return the selected wiki's operating manual before any file mutation."""
        return _handle_wiki_error(
            lambda: agent_manual(
                alias,
                include_conventions=include_conventions,
                max_chars=max_chars,
            )
        )

    @mcp.tool()
    def wiki_overview(alias: str) -> Annotated[CallToolResult, WikiOverviewOutput]:
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
    ) -> Annotated[CallToolResult, WikiQueryOutput]:
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
    def wiki_links(alias: str, page: str) -> Annotated[CallToolResult, WikiLinksOutput]:
        """Return outgoing graph links for a page."""
        return _handle_wiki_error(lambda: links(alias, page))

    @mcp.tool()
    def wiki_backlinks(
        alias: str, page: str
    ) -> Annotated[CallToolResult, WikiBacklinksOutput]:
        """Return incoming graph links for a page."""
        return _handle_wiki_error(lambda: backlinks(alias, page))

    @mcp.tool()
    def wiki_around(
        alias: str, page: str, depth: int = 1
    ) -> Annotated[CallToolResult, WikiAroundOutput]:
        """Return a bounded graph neighborhood around a page."""
        return _handle_wiki_error(lambda: around(alias, page, depth=depth))

    @mcp.tool()
    def wiki_context_pack(
        alias: str, page: str, tokens: int = 12_000
    ) -> Annotated[CallToolResult, WikiContextPackOutput]:
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
        max_content_bytes: int | None = None,
        target_items: int = 24,
        max_items: int = 96,
        max_estimated_tokens: int | None = None,
        max_delivery_bytes: int | None = None,
        delivery_format: Literal["anvil_prompt_evidence_v1"] | None = None,
        contract_version: str = "1",
        temporal_view: str | None = None,
        request_time: str | None = None,
        world_at: str | None = None,
        known_at: str | None = None,
        transition_from: str | None = None,
        transition_to: str | None = None,
        workspace_identity: Mapping[str, Any] | None = None,
    ) -> Annotated[CallToolResult, WikiCompileContextOutput]:
        """Compile bounded, question-shaped evidence from a registered wiki."""
        return _handle_wiki_error(
            lambda: compiled_context(
                alias,
                question,
                seeds=seeds,
                state_view=state_view,
                target_bytes=target_bytes,
                max_bytes=max_bytes,
                max_content_bytes=max_content_bytes,
                target_items=target_items,
                max_items=max_items,
                max_estimated_tokens=max_estimated_tokens,
                max_delivery_bytes=max_delivery_bytes,
                delivery_format=delivery_format,
                contract_version=contract_version,
                temporal_view=temporal_view,
                request_time=request_time,
                world_at=world_at,
                known_at=known_at,
                transition_from=transition_from,
                transition_to=transition_to,
                workspace_identity=workspace_identity,
            )
        )

    @mcp.tool()
    def wiki_maintenance_candidates(
        alias: str,
        stale_after_days: int = 180,
    ) -> Annotated[CallToolResult, WikiMaintenanceCandidatesOutput]:
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
    ) -> Annotated[CallToolResult, WikiBuildMaintenanceCandidateOutput]:
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
    ) -> Annotated[CallToolResult, WikiBuildTemporalCandidatesOutput]:
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
        source: UnifiedMaintenanceSourceInput,
        intent: str,
        proposed_at: WholeSecondTimestamp,
        claims: list[MaintenanceClaimInput] | None = None,
        pages: list[str] | None = None,
        evidence: list[MaintenanceEvidenceInput] | None = None,
    ) -> Annotated[CallToolResult, WikiBuildMaintenanceOutput]:
        """Build a candidate-only proposal; never mutate a wiki.

        Nested input guide (also applies when a host displays types as unknown):
        With claims, supply the temporal source below. Omit content_hash to derive
        it from exact UTF-8 payload_text; a supplied hash must be 64 lowercase hex
        characters and match those bytes. Without claims, source has ONLY
        source_kind, source_ref, content_hash (required; no payload to hash).
        observed_at/proposed_at are timezone-aware whole-second RFC3339 instants.
        Times are objects: {kind: "known", value: ISO date or RFC3339 instant},
        {kind: "unknown", reason: text}, or {kind: "open"} for an interval end only.
        Claims require subject, predicate, object, claim_scope,
        proposed_world_validity: {from: time, to: time}, signals: [{kind, detail?}],
        and unknowns: [{field, reason}]. Entity examples are below; external IDs
        use {kind: "external_id", namespace, value}. Evidence is optional:
        [{ref: text, content_hash?: lowercase SHA-256, kind?: JSON, note?: JSON}].
        locator maps string keys to strings or non-negative integers.

        Example call (illustrative facts only; use actual evidence and pages):
        ```json
        {
          "alias": "brain",
          "intent": "durable_learning",
          "proposed_at": "2026-08-10T01:00:00Z",
          "source": {
            "source_kind": "source:manual", "source_ref": "sources/status.md",
            "locator": {"line": 10, "section": "status"}, "input_type": "input:markdown",
            "observed_at": "2026-08-10T00:00:00Z",
            "source_event_time": {"kind": "unknown", "reason": "not stated"},
            "retention": "immutable_source", "payload_text": "The service is ready."
          },
          "claims": [{
            "subject": {"kind": "resolved_page", "page": "a.md"},
            "predicate": "status:has_state",
            "object": {"kind": "literal", "datatype": "type:text", "value": "ready"},
            "claim_scope": "default",
            "proposed_world_validity": {"from": {"kind": "known", "value": "2026-01-01"}, "to": {"kind": "open"}},
            "signals": [{"kind": "signal:direct", "detail": "explicit statement"}], "unknowns": []
          }]
        }
        ```
        """
        source_value = source.model_dump(exclude_none=True)
        if isinstance(source, TemporalMaintenanceSourceInput) and "content_hash" not in source_value:
            source_value["content_hash"] = hashlib.sha256(
                source.payload_text.encode("utf-8")
            ).hexdigest()
        return _handle_wiki_error(
            lambda: unified_maintenance_proposal(
                alias,
                source=source_value,
                intent=intent,
                claims=[
                    claim.model_dump(by_alias=True, exclude_none=True)
                    for claim in claims or []
                ],
                pages=pages or [],
                evidence=[item.model_dump(exclude_none=True) for item in evidence or []],
                proposed_at=proposed_at,
            )
        )

    @mcp.tool()
    def wiki_reconcile_temporal_candidates(
        alias: str,
        proposals: list[Mapping[str, Any]],
    ) -> Annotated[CallToolResult, WikiReconcileTemporalCandidatesOutput]:
        """Reconcile read-only temporal candidate proposals for one wiki."""
        return _handle_wiki_error(lambda: reconcile_temporal_candidates_runtime(alias, proposals))

    @mcp.tool()
    def wiki_get_page(
        alias: str, page: str, max_chars: int = 4_000
    ) -> Annotated[CallToolResult, WikiGetPageOutput]:
        """Return page metadata and bounded page content."""
        return _handle_wiki_error(lambda: get_page(alias, page, max_chars=max_chars))

    @mcp.tool()
    def wiki_get_source_excerpt(
        alias: str,
        page: str | None = None,
        source: str | None = None,
        max_chars: int = 1_600,
    ) -> Annotated[CallToolResult, WikiGetSourceExcerptOutput]:
        """Return a bounded excerpt from a source file referenced by page or source path."""
        return _handle_wiki_error(
            lambda: get_source_excerpt(alias, page=page, source=source, max_chars=max_chars)
        )

    @mcp.tool()
    def wiki_graph_health(alias: str) -> Annotated[CallToolResult, WikiGraphHealthOutput]:
        """Return graph health, hubs, orphans, components, and source gaps."""
        return _handle_wiki_error(lambda: graph_health(alias))

    return mcp


def _handle_wiki_error(operation: Callable[[], dict[str, Any]]) -> CallToolResult:
    try:
        return _success(operation())
    except WikiMcpError as exc:
        return CallToolResult(
            content=[TextContent(type="text", text=f"{exc.code}: {exc.message}")],
            structured_content={"error": exc.to_dict()},
            is_error=True,
        )
    except Exception as exc:
        return CallToolResult(
            content=[TextContent(type="text", text=f"UNEXPECTED_ERROR: {type(exc).__name__}")],
            structured_content={
                "error": {
                    "code": "UNEXPECTED_ERROR",
                    "message": "Unexpected llm-wiki MCP server error",
                    "details": {"type": type(exc).__name__},
                }
            },
            is_error=True,
        )


def _success(payload: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=SUCCESS_MARKER)],
        structured_content=payload,
        is_error=False,
    )


mcp = create_server()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
