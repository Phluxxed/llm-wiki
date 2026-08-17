from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Protocol

from mcp import Client

from ..contracts import Diagnostic
from ..graph_adapter import (
    GraphAdapterError,
    canonical_page_roots,
    open_graph_mirror,
)
from ..state import normalize_knowledge_state
from .base import CandidateEvidence, ProviderContext, ProviderResult
from .local import bounded_content, page_sections
from .loci_transport import LociGatewayError, LociMcpClient, tool_mapping, tool_payload
from .utils import question_terms


GRAPH_NAMESPACE = "llm-wiki"
GRAPH_EDGE_TYPES = ("body_link", "mentioned_in")
GRAPH_RESOLUTIONS = ("declared",)
MAX_PATHS = 8
MAX_ANCHORS = 10
MAX_HOPS = 3
MAX_NODES = 64
MAX_EVIDENCE_BYTES = 32_768
MAX_ESTIMATED_TOKENS = 8_192
_REJECTION_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_CONTENT_HASH = re.compile(r"^[0-9a-f]{64}$")


class LociGraphGateway(Protocol):
    def retrieve(self, context: ProviderContext) -> Mapping[str, Any]: ...


class LociGraphMcpGateway:
    def __init__(
        self,
        *,
        client: LociMcpClient | None = None,
        cache_dir: str | Path | None = None,
    ):
        self._client = client or LociMcpClient()
        self._cache_dir = cache_dir

    def retrieve(self, context: ProviderContext) -> Mapping[str, Any]:
        return self._client.run(lambda session: self._retrieve_session(session, context))

    async def _retrieve_session(
        self,
        session: Client,
        context: ProviderContext,
    ) -> Mapping[str, Any]:
        with open_graph_mirror(context, cache_dir=self._cache_dir) as mirror:
            roots = mirror.page_roots
            if roots is None:
                await _index_mirror(session, mirror.root)
                outline = await session.call_tool(
                    "loci_outline",
                    arguments={"path": str(mirror.root)},
                )
                files = tool_payload(outline, "files")
                if not isinstance(files, list):
                    raise LociGatewayError(
                        "LOCI_RESULT_INVALID",
                        "loci_outline returned an invalid files payload",
                    )
                roots = canonical_page_roots(files, context.pages)
                mirror.write_contributions(roots)
                indexed = await _index_mirror(session, mirror.root)
                if indexed.get("graph_status") != "healthy":
                    raise LociGatewayError(
                        "LOCI_GRAPH_DEGRADED",
                        "Loci rejected the generated wiki graph contribution",
                        {"diagnostics": indexed.get("graph_diagnostics", [])},
                    )
                mirror.commit(roots)

            arguments = _retrieve_arguments(context, mirror.root, roots)
            try:
                result = await session.call_tool("loci_graph_retrieve", arguments=arguments)
                return tool_mapping(result)
            except LociGatewayError as exc:
                if exc.code != "REPO_NOT_INDEXED":
                    raise
                await _index_mirror(session, mirror.root)
                result = await session.call_tool("loci_graph_retrieve", arguments=arguments)
                return tool_mapping(result)


class LociGraphProvider:
    name = "graph"

    def __init__(self, *, gateway: LociGraphGateway | None = None):
        self._gateway = gateway or LociGraphMcpGateway()

    def collect(self, context: ProviderContext) -> ProviderResult:
        if "relationship" not in context.shapes:
            return ProviderResult()
        try:
            response = self._gateway.retrieve(context)
            return _provider_result(response, context)
        except Exception as exc:
            return ProviderResult(diagnostics=(_gateway_failure(exc),))


async def _index_mirror(session: Client, root: Path) -> Mapping[str, Any]:
    result = await session.call_tool(
        "loci_index",
        arguments={"path": str(root), "incremental": True},
    )
    return tool_mapping(result)


def _retrieve_arguments(
    context: ProviderContext,
    mirror_root: Path,
    roots: Mapping[str, str],
) -> dict[str, Any]:
    budget = context.request.budget
    estimated_tokens = budget.max_estimated_tokens or max(1, budget.max_bytes // 4)
    arguments = {
        "repo": str(mirror_root),
        "question": context.request.question,
        "namespaces": [GRAPH_NAMESPACE],
        "edge_types": list(GRAPH_EDGE_TYPES),
        "resolutions": list(GRAPH_RESOLUTIONS),
        "direction": "either",
        "max_anchors": min(10, max(1, budget.max_items)),
        "max_hops": MAX_HOPS,
        "max_nodes": MAX_NODES,
        "max_paths": min(MAX_PATHS, budget.max_items),
        "path_offset": 0,
        "max_evidence_bytes": min(MAX_EVIDENCE_BYTES, budget.max_bytes),
        "max_estimated_tokens": min(MAX_ESTIMATED_TOKENS, estimated_tokens),
    }
    if context.resolved_seeds:
        arguments["seed_ids"] = [roots[path] for path in context.resolved_seeds]
    return arguments


def _provider_result(response: Mapping[str, Any], context: ProviderContext) -> ProviderResult:
    if not isinstance(response, Mapping) or response.get("schema_version") != 1:
        return ProviderResult(diagnostics=(_invalid_result("Graph response schema is invalid"),))
    paths = response.get("paths")
    rejected = response.get("rejected_paths")
    diagnostics = response.get("diagnostics")
    if (
        not isinstance(paths, list)
        or not isinstance(rejected, list)
        or not isinstance(diagnostics, list)
        or len(paths) > MAX_PATHS
        or len(rejected) > MAX_PATHS
        or len(diagnostics) > 64
    ):
        return ProviderResult(diagnostics=(_invalid_result("Graph response collections are invalid"),))

    selection = response.get("selection")
    if selection not in {"explicit", "inferred"}:
        return ProviderResult(diagnostics=(_invalid_result("Graph response selection is invalid"),))

    try:
        anchor_groups = _relationship_anchor_groups(response, context, selection)
    except GraphAdapterError as exc:
        return ProviderResult(diagnostics=(_invalid_result(str(exc), details=exc.details),))

    candidates: list[CandidateEvidence] = []
    result_diagnostics: list[Diagnostic] = []
    for retrieval_rank, path in enumerate(paths):
        try:
            candidates.append(
                _path_candidate(
                    path,
                    context,
                    selection,
                    retrieval_rank,
                    anchor_groups,
                )
            )
        except GraphAdapterError as exc:
            result_diagnostics.append(_invalid_result(str(exc), details=exc.details))

    for rejected_path in rejected:
        diagnostic = _rejected_path_diagnostic(rejected_path)
        if diagnostic is None:
            result_diagnostics.append(_invalid_result("Rejected graph path is invalid"))
        else:
            result_diagnostics.append(diagnostic)

    for diagnostic in diagnostics:
        converted = _graph_diagnostic(diagnostic)
        if converted is None:
            result_diagnostics.append(_invalid_result("Graph diagnostic is invalid"))
        else:
            result_diagnostics.append(converted)
    candidates.extend(_path_node_candidates(candidates, context))
    return ProviderResult(tuple(candidates), tuple(result_diagnostics))


def _path_node_candidates(
    paths: list[CandidateEvidence],
    context: ProviderContext,
) -> list[CandidateEvidence]:
    first_seen: dict[str, tuple[int, int]] = {}
    for path_rank, candidate in enumerate(paths):
        nodes = candidate.locator.get("nodes")
        if not isinstance(nodes, list):
            continue
        for node_rank, node in enumerate(nodes):
            if not isinstance(node, Mapping):
                continue
            file_path = node.get("file")
            if isinstance(file_path, str) and file_path in context.pages:
                first_seen.setdefault(file_path, (path_rank, node_rank))

    hydrated: list[CandidateEvidence] = []
    ordered = sorted(
        first_seen.items(),
        key=lambda item: (*item[1], item[0]),
    )
    for hydration_rank, (file_path, (path_rank, _)) in enumerate(ordered):
        page = context.pages[file_path]
        sections = page_sections(page)
        if not sections:
            continue
        terms = question_terms(context.request.question)
        section = max(
            sections,
            key=lambda item: (
                sum(term in item.content.lower() for term in terms),
                -item.start_line,
            ),
        )
        description = page.frontmatter.get("description")
        content_parts = []
        if isinstance(description, str) and description.strip():
            content_parts.append(f"description: {description.strip()}")
        content_parts.append(section.content)
        content, truncated = bounded_content("\n\n".join(content_parts))
        if not content:
            continue
        state = normalize_knowledge_state(
            page.frontmatter,
            field_name=context.config.state.field,
        )
        identity = f"{file_path}:{section.start_line}:{section.end_line}"
        hydrated.append(
            CandidateEvidence(
                id=f"graph:loci-node:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}",
                provider="graph",
                route="path_node_section",
                page=file_path,
                source=None,
                locator={
                    "file": file_path,
                    "section": section.name,
                    "start_line": section.start_line,
                    "end_line": section.end_line,
                    "content_hash": hashlib.sha256(page.text.encode("utf-8")).hexdigest(),
                    "path_rank": path_rank,
                    "includes_description": bool(content_parts[:-1]),
                },
                content=content,
                roles=("endpoint",),
                selection_signals=("loci_path_node", f"path_rank:{path_rank}"),
                authored_state=state.normalized,
                derived_flags=state.derived_flags,
                authority_signals=(),
                retrieval_rank=MAX_PATHS + hydration_rank,
                truncated=truncated,
            )
        )
    return hydrated


def _path_candidate(
    path: Any,
    context: ProviderContext,
    selection: str,
    retrieval_rank: int,
    anchor_groups: tuple[frozenset[str], frozenset[str]] | None,
) -> CandidateEvidence:
    if not isinstance(path, Mapping):
        raise _invalid_path("Selected graph path is not an object")
    support_kind = path.get("support_kind")
    if support_kind not in {"direct_authored_edge", "semantic_bridge"}:
        raise _invalid_path("Selected graph path support kind is invalid")
    nodes = _validated_nodes(path.get("nodes"), context)
    steps = path.get("steps")
    if not isinstance(steps, list) or len(steps) != len(nodes) - 1:
        raise _invalid_path("Selected graph path steps do not match its nodes")
    semantic_bridge = _validated_semantic_bridge(path.get("semantic_bridge"), support_kind, len(steps))
    score = path.get("retrieval_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise _invalid_path("Selected graph path retrieval score is invalid")
    score_components = path.get("score_components")
    if not isinstance(score_components, Mapping) or len(_bounded_json(score_components)) > 4_096:
        raise _invalid_path("Selected graph path score components are invalid")

    validated_steps = [
        _validated_step(step, nodes[index], nodes[index + 1], context)
        for index, step in enumerate(steps)
    ]
    content = "\n\n".join(_step_content(step) for step in validated_steps)
    if not content:
        raise _invalid_path("Selected graph path has no authored evidence")
    node_identity = [node["id"] for node in nodes]
    edge_identity = [step["edge"] for step in validated_steps]
    identity = json.dumps(
        {"nodes": node_identity, "edges": edge_identity},
        sort_keys=True,
        separators=(",", ":"),
    )
    candidate_id = f"graph:loci:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
    evidence_files = tuple(dict.fromkeys(step["evidence"]["file"] for step in validated_steps))
    state, derived_flags = _path_state(evidence_files, context)
    page = nodes[1]["file"] if len(nodes) > 2 else evidence_files[0]
    relationship_support = _relationship_support(nodes, selection, anchor_groups)
    signals = [
        "loci_evidence_backed_path",
        f"support:{support_kind}",
        f"relationship_{relationship_support}",
        *(f"edge:{edge_type}" for edge_type in dict.fromkeys(step["edge"]["type"] for step in validated_steps)),
    ]
    if selection == "explicit":
        signals.append("explicit_seed_bridge")
    if semantic_bridge["required"]:
        signals.append("semantic_bridge_matched")
    return CandidateEvidence(
        id=candidate_id,
        provider="graph",
        route="evidence_backed_path",
        page=page,
        source=None,
        locator={
            "support_kind": support_kind,
            "nodes": nodes,
            "steps": [_path_locator_step(step) for step in validated_steps],
            "semantic_bridge": semantic_bridge,
            "relationship_support": relationship_support,
            "retrieval_score": float(score),
            "score_components": dict(score_components),
        },
        content=content,
        roles=("bridge",) if relationship_support == "claim_bridge" else ("support",),
        selection_signals=tuple(signals),
        authored_state=state,
        derived_flags=derived_flags,
        authority_signals=(),
        retrieval_rank=retrieval_rank,
        atomic=True,
    )


def _relationship_anchor_groups(
    response: Mapping[str, Any],
    context: ProviderContext,
    selection: str,
) -> tuple[frozenset[str], frozenset[str]] | None:
    if selection == "explicit":
        return None
    anchors = response.get("anchors")
    max_anchors = min(MAX_ANCHORS, context.request.budget.max_items)
    if not isinstance(anchors, list) or len(anchors) > max_anchors:
        raise _invalid_path("Inferred graph response anchors are invalid")
    if not anchors:
        if response.get("paths"):
            raise _invalid_path("Inferred graph paths lack question anchors")
        return None

    validated: list[tuple[str, frozenset[str]]] = []
    seen: set[str] = set()
    for value in anchors:
        if not isinstance(value, Mapping):
            raise _invalid_path("Inferred graph anchor is not an object")
        node = value.get("node")
        reason = value.get("reason")
        if not isinstance(node, Mapping) or not isinstance(reason, Mapping):
            raise _invalid_path("Inferred graph anchor shape is invalid")
        node_id = node.get("id")
        attributes = node.get("attributes")
        file_path = attributes.get("file") if isinstance(attributes, Mapping) else None
        line = attributes.get("line") if isinstance(attributes, Mapping) else None
        end_line = attributes.get("end_line") if isinstance(attributes, Mapping) else None
        matched_terms = reason.get("matched_terms")
        if (
            not isinstance(node_id, str)
            or not node_id
            or node_id in seen
            or not isinstance(attributes, Mapping)
            or not isinstance(file_path, str)
            or file_path not in context.pages
            or not node_id.startswith(f"{file_path}::")
            or isinstance(line, bool)
            or not isinstance(line, int)
            or isinstance(end_line, bool)
            or not isinstance(end_line, int)
            or line < 1
            or end_line < line
            or reason.get("kind") != "inferred"
            or not _string_list(matched_terms)
            or not matched_terms
            or len(matched_terms) > 64
            or any(len(term) > 128 for term in matched_terms)
        ):
            raise _invalid_path("Inferred graph anchor identity is invalid")
        seen.add(node_id)
        validated.append((node_id, frozenset(matched_terms)))

    primary_terms = validated[0][1]
    primary_ids = frozenset(
        node_id for node_id, terms in validated if not primary_terms.isdisjoint(terms)
    )
    distinct_ids = frozenset(
        node_id for node_id, terms in validated if primary_terms.isdisjoint(terms)
    )
    if not distinct_ids:
        return None
    return primary_ids, distinct_ids


def _relationship_support(
    nodes: list[dict[str, Any]],
    selection: str,
    anchor_groups: tuple[frozenset[str], frozenset[str]] | None,
) -> str:
    if selection == "explicit" or anchor_groups is None:
        return "claim_bridge"
    node_ids = {node["id"] for node in nodes}
    primary_ids, distinct_ids = anchor_groups
    if node_ids & primary_ids and node_ids & distinct_ids:
        return "claim_bridge"
    return "ancillary_path"


def _validated_nodes(value: Any, context: ProviderContext) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 2 <= len(value) <= MAX_HOPS + 1:
        raise _invalid_path("Selected graph path nodes are invalid")
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise _invalid_path("Selected graph node is not an object")
        node_id = item.get("id")
        attributes = item.get("attributes")
        if not isinstance(node_id, str) or not node_id or node_id in seen or not isinstance(attributes, Mapping):
            raise _invalid_path("Selected graph node identity is invalid")
        file_path = attributes.get("file")
        line = attributes.get("line")
        end_line = attributes.get("end_line")
        if (
            not isinstance(file_path, str)
            or file_path not in context.pages
            or isinstance(line, bool)
            or not isinstance(line, int)
            or isinstance(end_line, bool)
            or not isinstance(end_line, int)
            or line < 1
            or end_line < line
        ):
            raise _invalid_path("Selected graph node locator is invalid")
        seen.add(node_id)
        nodes.append({"id": node_id, "file": file_path, "line": line, "end_line": end_line})
    return nodes


def _validated_semantic_bridge(value: Any, support_kind: str, step_count: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid_path("Selected graph path semantic bridge is invalid")
    required = value.get("required")
    required_terms = value.get("required_terms")
    matched_terms = value.get("matched_terms")
    if (
        not isinstance(required, bool)
        or not _string_list(required_terms)
        or not isinstance(matched_terms, list)
        or not all(isinstance(item, str) and item for item in matched_terms)
    ):
        raise _invalid_path("Selected graph path semantic bridge terms are invalid")
    if step_count > 1 and (support_kind != "semantic_bridge" or not required or not matched_terms):
        raise _invalid_path("Multi-hop graph path lacks a matched semantic bridge")
    if step_count == 1 and support_kind != "direct_authored_edge":
        raise _invalid_path("Direct graph path has an invalid support kind")
    return {
        "required": required,
        "required_terms": list(required_terms),
        "matched_terms": list(matched_terms),
    }


def _validated_step(
    value: Any,
    current: Mapping[str, Any],
    following: Mapping[str, Any],
    context: ProviderContext,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid_path("Selected graph path step is not an object")
    traversed = value.get("traversed")
    edge = value.get("edge")
    span = value.get("evidence_span")
    if traversed not in {"forward", "reverse"} or not isinstance(edge, Mapping) or not isinstance(span, Mapping):
        raise _invalid_path("Selected graph path step shape is invalid")
    from_id = edge.get("from")
    to_id = edge.get("to")
    expected = (
        (current["id"], following["id"])
        if traversed == "forward"
        else (following["id"], current["id"])
    )
    if (from_id, to_id) != expected:
        raise _invalid_path("Selected graph path step direction is invalid")
    edge_type = edge.get("type")
    if (
        edge_type not in GRAPH_EDGE_TYPES
        or edge.get("namespace") != GRAPH_NAMESPACE
        or edge.get("resolution") != "declared"
        or edge.get("directed") is not True
    ):
        raise _invalid_path("Selected graph path edge contract is invalid")
    evidence = edge.get("evidence")
    if not isinstance(evidence, Mapping):
        raise _invalid_path("Selected graph path edge evidence is invalid")
    file_path = evidence.get("file")
    line = evidence.get("line")
    content_hash = evidence.get("content_hash")
    if (
        not isinstance(file_path, str)
        or file_path not in context.pages
        or isinstance(line, bool)
        or not isinstance(line, int)
        or line < 1
        or not isinstance(content_hash, str)
        or not _CONTENT_HASH.fullmatch(content_hash)
    ):
        raise _invalid_path("Selected graph path evidence locator is invalid")
    if (
        span.get("file") != file_path
        or span.get("start_line") != line
        or span.get("end_line") != line
        or not isinstance(span.get("content"), str)
    ):
        raise _invalid_path("Selected graph path hydrated evidence does not match its edge")
    page = context.pages[file_path]
    lines = page.text.splitlines()
    content = str(span["content"]).rstrip("\r\n")
    expected_hash = hashlib.sha256(page.text.encode("utf-8")).hexdigest()
    if line > len(lines) or lines[line - 1] != content or content_hash != expected_hash:
        raise _invalid_path("Selected graph path evidence does not match the source wiki snapshot")
    return {
        "traversed": traversed,
        "edge": {
            "from": from_id,
            "to": to_id,
            "type": edge_type,
            "directed": True,
            "namespace": GRAPH_NAMESPACE,
            "resolution": "declared",
        },
        "evidence": {
            "file": file_path,
            "start_line": line,
            "end_line": line,
            "content_hash": content_hash,
            "content": content,
        },
    }


def _step_content(step: Mapping[str, Any]) -> str:
    edge = step["edge"]
    evidence = step["evidence"]
    return (
        f"{edge['from']} --{edge['type']}--> {edge['to']}\n"
        f"{evidence['file']}:{evidence['start_line']}: {evidence['content']}"
    )


def _path_locator_step(step: Mapping[str, Any]) -> dict[str, Any]:
    evidence = step["evidence"]
    return {
        "traversed": step["traversed"],
        "edge": dict(step["edge"]),
        "evidence": {
            "file": evidence["file"],
            "start_line": evidence["start_line"],
            "end_line": evidence["end_line"],
            "content_hash": evidence["content_hash"],
        },
    }


def _path_state(
    evidence_files: tuple[str, ...],
    context: ProviderContext,
) -> tuple[str, tuple[str, ...]]:
    states = [
        normalize_knowledge_state(
            context.pages[file_path].frontmatter,
            field_name=context.config.state.field,
        )
        for file_path in evidence_files
    ]
    normalized = {state.normalized for state in states}
    flags = [flag for state in states for flag in state.derived_flags]
    if len(normalized) == 1:
        return states[0].normalized, tuple(dict.fromkeys(flags))
    return "unspecified", tuple(dict.fromkeys((*flags, "mixed_path_states")))


def _rejected_path_diagnostic(value: Any) -> Diagnostic | None:
    if not isinstance(value, Mapping):
        return None
    reason = value.get("reason")
    nodes = value.get("nodes")
    if (
        not isinstance(reason, str)
        or not _REJECTION_CODE.fullmatch(reason)
        or not isinstance(nodes, list)
        or not 1 <= len(nodes) <= MAX_HOPS + 1
        or not all(isinstance(item, str) and item for item in nodes)
    ):
        return None
    details: dict[str, Any] = {"reason": reason, "nodes": list(nodes)}
    for key in ("required_bridge_terms", "high_degree_nodes"):
        items = value.get(key)
        if items is not None:
            if not isinstance(items, list) or not all(isinstance(item, str) and item for item in items):
                return None
            details[key] = list(items[:16])
    return Diagnostic(
        code="LOCI_GRAPH_PATH_REJECTED",
        message="Loci rejected a candidate graph path",
        provider="graph",
        details=details,
    )


def _graph_diagnostic(value: Any) -> Diagnostic | None:
    if not isinstance(value, Mapping):
        return None
    code = value.get("code")
    message = value.get("message")
    if not isinstance(code, str) or not _REJECTION_CODE.fullmatch(code) or not isinstance(message, str):
        return None
    details = value.get("details")
    if details is None:
        details = {}
    if not isinstance(details, Mapping) or len(_bounded_json(details)) > 8_192:
        return None
    return Diagnostic(
        code=code if code.startswith("LOCI_GRAPH_") else f"LOCI_GRAPH_{code}",
        message=message,
        provider="graph",
        details=dict(details),
    )


def _gateway_failure(exc: Exception) -> Diagnostic:
    source_code = str(getattr(exc, "code", ""))
    code = {
        "LOCI_MCP_UNAVAILABLE": "LOCI_GRAPH_MCP_UNAVAILABLE",
        "LOCI_MCP_TIMEOUT": "LOCI_GRAPH_MCP_TIMEOUT",
        "LOCI_MCP_FAILED": "LOCI_GRAPH_MCP_FAILED",
        "LOCI_RESULT_INVALID": "LOCI_GRAPH_RESULT_INVALID",
        "REPO_NOT_INDEXED": "LOCI_GRAPH_REPO_NOT_INDEXED",
    }.get(source_code, source_code if source_code.startswith("LOCI_GRAPH_") else "LOCI_GRAPH_PROVIDER_FAILED")
    details = {"type": type(exc).__name__}
    source_details = getattr(exc, "details", None)
    if isinstance(source_details, Mapping):
        details.update(source_details)
    return Diagnostic(
        code=code,
        message="Loci graph retrieval could not provide evidence-backed paths",
        provider="graph",
        details=details,
    )


def _invalid_result(message: str, *, details: Mapping[str, Any] | None = None) -> Diagnostic:
    return Diagnostic(
        code="LOCI_GRAPH_RESULT_INVALID",
        message=message,
        provider="graph",
        details=dict(details or {}),
    )


def _invalid_path(message: str) -> GraphAdapterError:
    return GraphAdapterError("LOCI_GRAPH_RESULT_INVALID", message)


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def _bounded_json(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _invalid_path("Graph payload contains a non-JSON value") from exc
