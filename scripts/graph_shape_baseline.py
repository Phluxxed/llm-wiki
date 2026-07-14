#!/usr/bin/env python3
"""Read-only graph-shape traversal baseline for approved wiki fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from collections import deque
from itertools import combinations
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping
from unittest.mock import patch

from mcp import ClientSession
from llm_wiki_core.compiler import _effective_config, compile_context
from llm_wiki_core.contracts import CompileRequest, Diagnostic
from llm_wiki_core.documents import WikiPage, collect_pages
from llm_wiki_core.graph import Edge, collect_typed_edges
from llm_wiki_core.providers.base import CandidateEvidence, ProviderContext, ProviderResult
from llm_wiki_core.providers.graph import _edge_candidate
from llm_wiki_core.providers.local import FrontmatterProvider, SeedProvider, TextProvider
from llm_wiki_core.providers.loci import LociGateway, LociMcpGateway, LociRetrieval, LociProvider
from llm_wiki_core.providers.source import SourceProvider
from llm_wiki_core.providers.utils import page_matches_question, question_terms
from llm_wiki_core.query_shape import classify_question, required_roles
from llm_wiki_core.selection import coverage, select_candidates


def load_contract(path: str | Path) -> dict[str, Any]:
    contract = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_contract(contract)
    return contract


def validate_contract(contract: Mapping[str, Any]) -> None:
    if contract.get("schema_version") != "1":
        raise ValueError("unsupported fixture schema version")
    corpora = contract.get("corpora")
    fixtures = contract.get("fixtures")
    if not isinstance(corpora, Mapping) or not corpora:
        raise ValueError("fixture contract needs corpora")
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("fixture contract needs fixtures")

    seen_ids: set[str] = set()
    required_fields = {
        "id",
        "corpus",
        "shape",
        "question",
        "expected_pages",
        "bridge_paths_any",
        "bridge_literals_any",
        "forbidden_paths",
        "required_literals",
        "answerable",
        "graph_expected",
    }
    for fixture in fixtures:
        if not isinstance(fixture, Mapping):
            raise ValueError("fixture must be an object")
        missing = required_fields - set(fixture)
        if missing:
            raise ValueError(f"fixture missing field: {sorted(missing)[0]}")
        fixture_id = fixture["id"]
        if not isinstance(fixture_id, str) or not fixture_id:
            raise ValueError("fixture id must be a non-empty string")
        if fixture_id in seen_ids:
            raise ValueError(f"duplicate fixture id: {fixture_id}")
        seen_ids.add(fixture_id)
        if fixture["corpus"] not in corpora:
            raise ValueError(f"unknown fixture corpus: {fixture['corpus']}")


def score_trace(fixture: Mapping[str, Any], trace: Mapping[str, Any]) -> dict[str, Any]:
    pages = set(trace.get("pages") or [])
    expected_pages = list(fixture.get("expected_pages") or [])
    endpoint_hits = [page for page in expected_pages if page in pages]
    endpoint_recall = len(endpoint_hits) / len(expected_pages) if expected_pages else 1.0

    paths = list(trace.get("paths") or [])
    required_paths = list(fixture.get("bridge_paths_any") or [])
    path_complete = any(_path_present(candidate, paths) for candidate in required_paths) if required_paths else None

    content = str(trace.get("content") or "")
    lowered_content = content.lower()
    bridge_literals = list(fixture.get("bridge_literals_any") or [])
    bridge_evidence_complete = (
        any(str(literal).lower() in lowered_content for literal in bridge_literals)
        if bridge_literals
        else None
    )

    required_literals = list(fixture.get("required_literals") or [])
    literal_hits = [literal for literal in required_literals if str(literal).lower() in lowered_content]
    required_literal_recall = len(literal_hits) / len(required_literals) if required_literals else 1.0

    forbidden_paths = list(fixture.get("forbidden_paths") or [])
    unsupported_shortcut = any(_path_present(candidate, paths) for candidate in forbidden_paths)
    if fixture.get("shape") == "false_hub_shortcut":
        expected_pairs = {
            _normalized_pair(source, target)
            for source, target in combinations(sorted(set(expected_pages)), 2)
        }
        unsupported_shortcut = unsupported_shortcut or any(
            len(path) >= 2 and _normalized_pair(path[0], path[-1]) in expected_pairs
            for path in paths
        )
    answerable = bool(fixture.get("answerable"))
    refusal_ready = not answerable and trace.get("sufficient") is False

    return {
        "endpoint_hits": endpoint_hits,
        "endpoint_recall": round(endpoint_recall, 3),
        "path_complete": path_complete,
        "bridge_evidence_complete": bridge_evidence_complete,
        "required_literal_hits": literal_hits,
        "required_literal_recall": round(required_literal_recall, 3),
        "unsupported_shortcut": unsupported_shortcut,
        "refusal_ready": refusal_ready,
    }


def record_trace(
    records: list[Mapping[str, Any]],
    *,
    fixture: Mapping[str, Any],
    generic_hubs: set[str],
    sufficient: bool | None,
    tool_calls: int,
    latency_ms: float,
    classified_shapes: list[str],
    diagnostics: list[Mapping[str, Any]],
) -> dict[str, Any]:
    paths = _selected_graph_paths(records, fixture)
    legacy_edges = _graph_edges(
        [record for record in records if record.get("route") != "evidence_backed_path"]
    )
    pages = {
        str(record["page"])
        for record in records
        if isinstance(record.get("page"), str) and record.get("page")
    }
    for source, target in legacy_edges:
        pages.update((source, target))
    for path in paths:
        pages.update(path)

    content = "\n".join(str(record.get("content") or "") for record in records)
    evidence_bytes = sum(len(str(record.get("content") or "").encode("utf-8")) for record in records)
    hub_paths = [
        path for path in paths if any(node in generic_hubs for node in path[1:-1])
    ]
    return {
        "pages": sorted(pages),
        "paths": paths,
        "path_count": len(paths),
        "content": content,
        "records": [dict(record) for record in records],
        "evidence_bytes": evidence_bytes,
        "estimated_tokens": math.ceil(evidence_bytes / 4),
        "tool_calls": tool_calls,
        "latency_ms": round(latency_ms, 3),
        "classified_shapes": list(classified_shapes),
        "diagnostics": [dict(item) for item in diagnostics],
        "sufficient": sufficient,
        "generic_hub_path_rate": round(len(hub_paths) / len(paths), 3) if paths else 0.0,
    }


def _selected_graph_paths(
    records: list[Mapping[str, Any]],
    fixture: Mapping[str, Any],
) -> list[list[str]]:
    selected: list[list[str]] = []
    legacy: list[Mapping[str, Any]] = []
    for record in records:
        if record.get("route") != "evidence_backed_path":
            legacy.append(record)
            continue
        locator = record.get("locator")
        nodes = locator.get("nodes") if isinstance(locator, Mapping) else None
        if not isinstance(nodes, list):
            continue
        path = [
            node.get("file")
            for node in nodes
            if isinstance(node, Mapping) and isinstance(node.get("file"), str)
        ]
        if len(path) == len(nodes) and len(path) >= 2:
            selected.append(path)
    selected.extend(_fixture_paths(_graph_edges(legacy), fixture))
    return [list(path) for path in dict.fromkeys(tuple(path) for path in selected)]


def _graph_edges(records: list[Mapping[str, Any]]) -> list[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for record in records:
        locator = record.get("locator")
        nodes = locator.get("nodes") if isinstance(locator, Mapping) else None
        if record.get("route") == "evidence_backed_path" and isinstance(nodes, list):
            files = [
                node.get("file")
                for node in nodes
                if isinstance(node, Mapping) and isinstance(node.get("file"), str)
            ]
            if len(files) == len(nodes) and len(files) >= 2:
                edges.update(zip(files, files[1:]))
            continue
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id.startswith("graph:"):
            continue
        relation = record_id.removeprefix("graph:").rsplit(":", 1)[0]
        if "->" not in relation:
            continue
        source, target = relation.split("->", 1)
        edges.add((source, target))
    return sorted(edges)


def _fixture_paths(
    edges: list[tuple[str, str]],
    fixture: Mapping[str, Any],
) -> list[list[str]]:
    adjacency: dict[str, set[str]] = {}
    for source, target in edges:
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)

    pairs: set[tuple[str, str]] = set()
    for source, target in combinations(sorted(set(fixture.get("expected_pages") or [])), 2):
        pairs.add((source, target))
    for key in ("bridge_paths_any", "forbidden_paths"):
        for candidate in fixture.get(key) or []:
            if len(candidate) >= 2:
                pairs.add((candidate[0], candidate[-1]))

    paths = []
    for source, target in sorted(pairs):
        path = _shortest_node_path(adjacency, source, target, max_depth=3)
        if path is not None:
            paths.append(path)
    return paths


def _shortest_node_path(
    adjacency: Mapping[str, set[str]],
    source: str,
    target: str,
    *,
    max_depth: int,
) -> list[str] | None:
    queue = deque([(source, [source])])
    seen = {source}
    while queue:
        node, path = queue.popleft()
        if len(path) - 1 >= max_depth:
            continue
        for neighbor in sorted(adjacency.get(node, set())):
            if neighbor in seen:
                continue
            next_path = [*path, neighbor]
            if neighbor == target:
                return next_path
            seen.add(neighbor)
            queue.append((neighbor, next_path))
    return None


def _path_present(candidate: list[str], paths: list[list[str]]) -> bool:
    if not candidate:
        return False
    for path in paths:
        if _contains_path(path, candidate) or _contains_path(path, list(reversed(candidate))):
            return True
    return False


def _contains_path(path: list[str], candidate: list[str]) -> bool:
    width = len(candidate)
    return any(path[index : index + width] == candidate for index in range(len(path) - width + 1))


class CountingLociGateway(LociGateway):
    def __init__(self) -> None:
        self._gateway = LociMcpGateway()
        self.tool_calls = 0

    def retrieve(self, wiki_root: Path, query: str, *, limit: int) -> LociRetrieval:
        retrieval = self._gateway.retrieve(wiki_root, query, limit=limit)
        hydrated = any(
            isinstance(item, Mapping) and isinstance(item.get("content"), str)
            for item in retrieval.results
        )
        self.tool_calls = 1 + int(hydrated)
        return retrieval


def run_baseline(
    contract: Mapping[str, Any],
    roots: Mapping[str, Path],
) -> dict[str, Any]:
    corpora = {
        name: _load_corpus(root)
        for name, root in roots.items()
    }
    fixture_results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="llm-wiki-graph-benchmark-") as cache_dir:
        with patch.dict(os.environ, {"LLM_WIKI_GRAPH_CACHE_DIR": cache_dir}):
            for fixture in contract["fixtures"]:
                corpus_name = fixture["corpus"]
                corpus = corpora[corpus_name]
                hubs = set(contract["corpora"][corpus_name]["generic_hubs"])
                request = CompileRequest.from_mapping(
                    {
                        "alias": contract["corpora"][corpus_name]["alias"],
                        "question": fixture["question"],
                    }
                )

                no_graph = _run_without_graph(corpus, request, fixture, hubs)
                routes = {
                    "no_graph": no_graph,
                    "direct_links": _run_graph_depth(corpus, request, fixture, hubs, max_depth=1),
                    "graph_depth_2": _run_graph_depth(corpus, request, fixture, hubs, max_depth=2),
                    "graph_depth_3": _run_graph_depth(corpus, request, fixture, hubs, max_depth=3),
                    "current_compiler": _run_current_compiler(
                        corpus,
                        request,
                        fixture,
                        hubs,
                    ),
                }
                fixture_results.append(
                    {
                        "id": fixture["id"],
                        "corpus": corpus_name,
                        "shape": fixture["shape"],
                        "question": fixture["question"],
                        "expected_pages": list(fixture["expected_pages"]),
                        "answerable": bool(fixture["answerable"]),
                        "routes": routes,
                    }
                )

    _assert_corpora_unchanged(corpora)

    result = {
        "schema_version": "1",
        "contract_approved_on": contract["approved_on"],
        "roots": {name: str(path) for name, path in roots.items()},
        "corpus_digests": {
            name: corpus["input_digest"]
            for name, corpus in corpora.items()
        },
        "fixtures": fixture_results,
    }
    result["summary"] = summarize_results(fixture_results)
    result["deterministic_digest"] = _deterministic_digest(result)
    return result


def _load_corpus(root: Path) -> dict[str, Any]:
    resolved = root.expanduser().resolve()
    config = _effective_config(resolved)
    pages = collect_pages(resolved, content=config.content)
    return {
        "root": resolved,
        "config": config,
        "pages": pages,
        "edges": collect_typed_edges(pages),
        "input_digest": _corpus_digest(pages),
    }


def _corpus_digest(pages: Mapping[str, WikiPage]) -> str:
    payload = [
        [path, hashlib.sha256(page.text.encode("utf-8")).hexdigest()]
        for path, page in sorted(pages.items())
    ]
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _assert_corpora_unchanged(corpora: Mapping[str, Mapping[str, Any]]) -> None:
    for name, corpus in corpora.items():
        current = collect_pages(corpus["root"], content=corpus["config"].content)
        if _corpus_digest(current) != corpus["input_digest"]:
            raise RuntimeError(f"{name} corpus changed during benchmark")


def _run_without_graph(
    corpus: Mapping[str, Any],
    request: CompileRequest,
    fixture: Mapping[str, Any],
    hubs: set[str],
) -> dict[str, Any]:
    start = perf_counter()
    shapes = classify_question(request.question)
    roles = required_roles(shapes)
    context = ProviderContext(
        corpus["root"],
        corpus["config"],
        request,
        corpus["pages"],
        shapes,
        roles,
        (),
    )
    loci_gateway = CountingLociGateway()
    providers = (
        SeedProvider(),
        FrontmatterProvider(),
        TextProvider(),
        SourceProvider(),
        LociProvider(gateway=loci_gateway),
    )
    enabled = [
        provider
        for provider in providers
        if provider.name in corpus["config"].compiler.providers and provider.name != "graph"
    ]
    candidates, diagnostics = _collect_candidates(enabled, context)
    selected, omissions = select_candidates(candidates, request, roles)
    selected_coverage = coverage(roles, selected)
    records = [item.to_dict() for item in selected]
    trace = record_trace(
        records,
        fixture=fixture,
        generic_hubs=hubs,
        sufficient=not selected_coverage.uncovered_roles,
        tool_calls=loci_gateway.tool_calls,
        latency_ms=(perf_counter() - start) * 1000,
        classified_shapes=list(shapes),
        diagnostics=[item.to_dict() for item in diagnostics],
    )
    trace["candidate_count"] = len(candidates)
    trace["omission_count"] = len(omissions)
    return {"trace": trace, "score": score_trace(fixture, trace)}


def _collect_candidates(
    providers: list[Any],
    context: ProviderContext,
) -> tuple[list[CandidateEvidence], list[Diagnostic]]:
    candidates: list[CandidateEvidence] = []
    diagnostics: list[Diagnostic] = []
    for provider in providers:
        try:
            output = provider.collect(context)
        except Exception as exc:
            diagnostics.append(
                Diagnostic(
                    code="PROVIDER_FAILED",
                    message="Candidate provider failed during baseline",
                    provider=provider.name,
                    details={"type": type(exc).__name__},
                )
            )
            continue
        if isinstance(output, ProviderResult):
            candidates.extend(output.candidates)
            diagnostics.extend(output.diagnostics)
        else:
            candidates.extend(output)
    return candidates, diagnostics


def _run_graph_depth(
    corpus: Mapping[str, Any],
    request: CompileRequest,
    fixture: Mapping[str, Any],
    hubs: set[str],
    *,
    max_depth: int,
) -> dict[str, Any]:
    start = perf_counter()
    shapes = ("relationship",)
    context = ProviderContext(
        corpus["root"],
        corpus["config"],
        request,
        corpus["pages"],
        shapes,
        required_roles(shapes),
        (),
    )
    terms = question_terms(request.question)
    anchors = sorted(
        path
        for path, page in corpus["pages"].items()
        if page_matches_question(page, request.question, terms)
    )
    paths, path_edges = _graph_paths(anchors, corpus["edges"], max_depth=max_depth)
    candidates = [
        candidate
        for candidate in (_edge_candidate(edge, context) for edge in sorted(path_edges))
        if candidate is not None
    ]
    records = [_candidate_dict(candidate) for candidate in candidates]
    trace = _graph_route_trace(
        records,
        fixture=fixture,
        hubs=hubs,
        paths=paths,
        anchors=anchors,
        max_depth=max_depth,
        latency_ms=(perf_counter() - start) * 1000,
    )
    return {"trace": trace, "score": score_trace(fixture, trace)}


def _graph_paths(
    anchors: list[str],
    edges: list[Edge],
    *,
    max_depth: int,
) -> tuple[list[list[str]], set[Edge]]:
    adjacency: dict[str, list[tuple[str, Edge]]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source, []).append((edge.target, edge))
        adjacency.setdefault(edge.target, []).append((edge.source, edge))
    for values in adjacency.values():
        values.sort(key=lambda item: (item[0], item[1]))

    if len(anchors) == 1:
        source = anchors[0]
        incident = {edge for _, edge in adjacency.get(source, [])}
        return [[source, neighbor] for neighbor, _ in adjacency.get(source, [])], incident

    anchor_set = set(anchors)
    paths: list[list[str]] = []
    used_edges: set[Edge] = set()
    for index, source in enumerate(anchors):
        target_set = set(anchors[index + 1 :])
        if not target_set:
            continue
        queue = deque([(source, 0)])
        parent: dict[str, tuple[str, Edge] | None] = {source: None}
        while queue:
            node, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for neighbor, edge in adjacency.get(node, []):
                if neighbor in parent:
                    continue
                parent[neighbor] = (node, edge)
                queue.append((neighbor, depth + 1))
        for target in sorted(target_set & set(parent)):
            node_path, edge_path = _reconstruct_path(parent, source, target)
            paths.append(node_path)
            used_edges.update(edge_path)
    return paths, used_edges


def _reconstruct_path(
    parent: Mapping[str, tuple[str, Edge] | None],
    source: str,
    target: str,
) -> tuple[list[str], list[Edge]]:
    nodes = [target]
    edges = []
    cursor = target
    while cursor != source:
        previous = parent[cursor]
        assert previous is not None
        cursor, edge = previous
        nodes.append(cursor)
        edges.append(edge)
    return list(reversed(nodes)), list(reversed(edges))


def _candidate_dict(candidate: CandidateEvidence) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "provider": candidate.provider,
        "route": candidate.route,
        "page": candidate.page,
        "source": candidate.source,
        "locator": dict(candidate.locator),
        "content": candidate.content,
        "roles": list(candidate.roles),
        "selection_signals": list(candidate.selection_signals),
        "authored_state": candidate.authored_state,
        "derived_flags": list(candidate.derived_flags),
        "authority_signals": list(candidate.authority_signals),
        "truncated": candidate.truncated,
    }


def _graph_route_trace(
    records: list[Mapping[str, Any]],
    *,
    fixture: Mapping[str, Any],
    hubs: set[str],
    paths: list[list[str]],
    anchors: list[str],
    max_depth: int,
    latency_ms: float,
) -> dict[str, Any]:
    evidence_bytes = sum(len(str(record.get("content") or "").encode("utf-8")) for record in records)
    pages = sorted({node for path in paths for node in path})
    relevant_pairs = _fixture_endpoint_pairs(fixture)
    relevant_paths = [
        path
        for path in paths
        if _normalized_pair(path[0], path[-1]) in relevant_pairs
    ]
    relevant_edges = {edge for path in relevant_paths for edge in zip(path, path[1:])}
    kept_records = [
        record
        for record in records
        if _record_edge(record) in relevant_edges or _reverse_edge(_record_edge(record)) in relevant_edges
    ]
    hub_paths = [path for path in paths if any(node in hubs for node in path[1:-1])]
    trace = {
        "pages": pages,
        "paths": relevant_paths,
        "path_count": len(paths),
        "content": "\n".join(str(record.get("content") or "") for record in kept_records),
        "records": kept_records,
        "record_count": len(records),
        "evidence_bytes": evidence_bytes,
        "estimated_tokens": math.ceil(evidence_bytes / 4),
        "tool_calls": 0,
        "latency_ms": round(latency_ms, 3),
        "classified_shapes": ["relationship"],
        "diagnostics": [],
        "sufficient": None,
        "generic_hub_path_rate": round(len(hub_paths) / len(paths), 3) if paths else 0.0,
        "anchor_count": len(anchors),
        "max_depth": max_depth,
    }
    return trace


def _fixture_endpoint_pairs(fixture: Mapping[str, Any]) -> set[tuple[str, str]]:
    pairs = {
        _normalized_pair(source, target)
        for source, target in combinations(sorted(set(fixture.get("expected_pages") or [])), 2)
    }
    for key in ("bridge_paths_any", "forbidden_paths"):
        for path in fixture.get(key) or []:
            if len(path) >= 2:
                pairs.add(_normalized_pair(path[0], path[-1]))
    return pairs


def _normalized_pair(source: str, target: str) -> tuple[str, str]:
    return tuple(sorted((source, target)))


def _record_edge(record: Mapping[str, Any]) -> tuple[str, str] | None:
    edges = _graph_edges([record])
    return edges[0] if edges else None


def _reverse_edge(edge: tuple[str, str] | None) -> tuple[str, str] | None:
    return (edge[1], edge[0]) if edge is not None else None


def _run_current_compiler(
    corpus: Mapping[str, Any],
    request: CompileRequest,
    fixture: Mapping[str, Any],
    hubs: set[str],
) -> dict[str, Any]:
    tool_calls = 0
    original_call_tool = ClientSession.call_tool

    async def counted_call_tool(session, *args, **kwargs):
        nonlocal tool_calls
        tool_calls += 1
        return await original_call_tool(session, *args, **kwargs)

    start = perf_counter()
    with patch.object(ClientSession, "call_tool", new=counted_call_tool):
        response = compile_context(corpus["root"], request).to_dict()
    trace = record_trace(
        response["evidence"],
        fixture=fixture,
        generic_hubs=hubs,
        sufficient=bool(response["stop"]["sufficient"]),
        tool_calls=tool_calls,
        latency_ms=(perf_counter() - start) * 1000,
        classified_shapes=compiled_response_shapes(response),
        diagnostics=list(response["diagnostics"]),
    )
    trace["evidence_bytes"] = response["budget"]["evidence_bytes"]
    trace["estimated_tokens"] = response["budget"]["estimated_tokens"]
    trace["stop"] = response["stop"]
    trace["coverage"] = response["coverage"]
    trace["budget"] = response["budget"]
    trace["omissions"] = response["omissions"]
    return {"trace": trace, "score": score_trace(fixture, trace)}


def compiled_response_shapes(response: Mapping[str, Any]) -> list[str]:
    query = response.get("query")
    if not isinstance(query, Mapping) or not isinstance(query.get("shapes"), list):
        raise ValueError("compiled response is missing query.shapes")
    return [str(shape) for shape in query["shapes"]]


def summarize_results(fixtures: list[Mapping[str, Any]]) -> dict[str, Any]:
    routes = sorted(next(iter(fixtures))["routes"]) if fixtures else []
    summary: dict[str, Any] = {}
    for route in routes:
        results = [fixture["routes"][route] for fixture in fixtures]
        scores = [item["score"] for item in results]
        traces = [item["trace"] for item in results]
        path_scores = [score["path_complete"] for score in scores if score["path_complete"] is not None]
        bridge_scores = [
            score["bridge_evidence_complete"]
            for score in scores
            if score["bridge_evidence_complete"] is not None
        ]
        shortcut_scores = [
            score["unsupported_shortcut"]
            for fixture, score in zip(fixtures, scores)
            if fixture["shape"] == "false_hub_shortcut"
        ]
        refusal_scores = [
            score["refusal_ready"]
            for fixture, score in zip(fixtures, scores)
            if fixture["shape"] in {"false_hub_shortcut", "cannot_answer"}
        ]
        summary[route] = {
            "mean_endpoint_recall": _mean(
                score["endpoint_recall"]
                for fixture, score in zip(fixtures, scores)
                if fixture.get("expected_pages")
            ),
            "path_complete_rate": _mean(path_scores),
            "bridge_evidence_rate": _mean(bridge_scores),
            "unsupported_shortcut_rate": _mean(shortcut_scores),
            "refusal_ready_rate": _mean(refusal_scores),
            "mean_required_literal_recall": _mean(
                score["required_literal_recall"]
                for fixture, score in zip(fixtures, scores)
                if fixture["shape"] == "exact_attribute"
            ),
            "mean_evidence_bytes": _mean(trace["evidence_bytes"] for trace in traces),
            "mean_estimated_tokens": _mean(trace["estimated_tokens"] for trace in traces),
            "mean_tool_calls": _mean(trace["tool_calls"] for trace in traces),
            "mean_latency_ms": _mean(trace["latency_ms"] for trace in traces),
            "mean_generic_hub_path_rate": _mean(trace["generic_hub_path_rate"] for trace in traces),
        }
    return summary


def _mean(values: Any) -> float | None:
    materialized = list(values)
    return round(sum(materialized) / len(materialized), 3) if materialized else None


def _deterministic_digest(result: Mapping[str, Any]) -> str:
    stable = json.loads(json.dumps(result))
    stable.pop("deterministic_digest", None)
    stable.pop("roots", None)
    for fixture in stable.get("fixtures", []):
        for route in fixture.get("routes", {}).values():
            route.get("trace", {}).pop("latency_ms", None)
    for route in stable.get("summary", {}).values():
        route.pop("mean_latency_ms", None)
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--ai-graph-root", type=Path, required=True)
    parser.add_argument("--brain-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    contract = load_contract(args.contract)
    result = run_baseline(
        contract,
        {
            "ai_graph_ideas": args.ai_graph_root,
            "brain": args.brain_root,
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"digest": result["deterministic_digest"], "summary": result["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
