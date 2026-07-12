from __future__ import annotations

from collections import deque

from ..graph import BODY_LINK_RE, Edge, collect_typed_edges, resolve_link
from ..state import normalize_knowledge_state
from .base import CandidateEvidence, ProviderContext
from .utils import page_matches_question, question_terms


class GraphProvider:
    name = "graph"

    def collect(self, context: ProviderContext) -> list[CandidateEvidence]:
        if "relationship" not in context.shapes:
            return []
        edges = collect_typed_edges(context.pages)
        terms = question_terms(context.request.question)
        anchors = set(context.resolved_seeds)
        if len(anchors) < 2:
            anchors.update(
                path
                for path, page in context.pages.items()
                if page_matches_question(page, context.request.question, terms)
            )
        if not anchors:
            return []

        path_edges: set[Edge] = set()
        sorted_anchors = sorted(anchors)
        path_edges.update(
            edge
            for edge in edges
            if edge.source in anchors and edge.target in anchors
        )
        for index, source in enumerate(sorted_anchors):
            for target in sorted_anchors[index + 1 :]:
                path_edges.update(_shortest_path(source, target, edges, max_depth=3))
        if len(anchors) == 1:
            path_edges.update(edge for edge in edges if edge.source in anchors or edge.target in anchors)

        candidates = [_edge_candidate(edge, context) for edge in sorted(path_edges)]
        return [candidate for candidate in candidates if candidate is not None]


def _shortest_path(source: str, target: str, edges: list[Edge], max_depth: int) -> tuple[Edge, ...]:
    adjacency: dict[str, list[tuple[str, Edge]]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source, []).append((edge.target, edge))
        adjacency.setdefault(edge.target, []).append((edge.source, edge))
    queue = deque([(source, ())])
    seen = {source}
    while queue:
        node, path = queue.popleft()
        if len(path) >= max_depth:
            continue
        for neighbor, edge in sorted(adjacency.get(node, []), key=lambda item: (item[0], item[1])):
            next_path = (*path, edge)
            if neighbor == target:
                return next_path
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, next_path))
    return ()


def _edge_candidate(edge: Edge, context: ProviderContext) -> CandidateEvidence | None:
    explicit_seed_bridge = edge.source in context.resolved_seeds and edge.target in context.resolved_seeds
    if edge.type == "body_link":
        page = context.pages[edge.source]
        for line_number, line in enumerate(page.text.splitlines(), start=1):
            raw_links = BODY_LINK_RE.findall(line)
            for raw in raw_links:
                if resolve_link(raw, edge.source, context.pages) == edge.target:
                    state = normalize_knowledge_state(page.frontmatter)
                    linked_seed_targets = {
                        target
                        for target in (
                            resolve_link(link, edge.source, context.pages) for link in raw_links
                        )
                        if target in context.resolved_seeds
                    }
                    if edge.source in context.resolved_seeds and len(linked_seed_targets) >= 2:
                        bridge_signals = (
                            "seed_authored_multi_bridge",
                            "multi_seed_bridge",
                            "explicit_seed_bridge",
                            "connecting_path",
                            f"edge:{edge.type}",
                        )
                    elif explicit_seed_bridge:
                        bridge_signals = (
                            "explicit_seed_bridge",
                            "connecting_path",
                            f"edge:{edge.type}",
                        )
                    elif len(linked_seed_targets) >= 2:
                        bridge_signals = (
                            "multi_seed_bridge",
                            "connecting_path",
                            f"edge:{edge.type}",
                        )
                    else:
                        bridge_signals = ("connecting_path", f"edge:{edge.type}")
                    return CandidateEvidence(
                        id=f"graph:{edge.source}->{edge.target}:{edge.type}",
                        provider="graph",
                        route="connecting_path",
                        page=edge.source,
                        source=str(page.frontmatter.get("source") or "") or None,
                        locator={"section": None, "start_line": line_number, "end_line": line_number},
                        content=line.strip(),
                        roles=("bridge",),
                        selection_signals=bridge_signals,
                        authored_state=state.normalized,
                        derived_flags=state.derived_flags,
                        authority_signals=("explicit_current",) if state.normalized == "current" else (),
                    )
        return None

    target_page = context.pages[edge.target]
    state = normalize_knowledge_state(target_page.frontmatter)
    bridge_signals = (
        ("explicit_seed_bridge", "connecting_path", f"edge:{edge.type}")
        if explicit_seed_bridge
        else ("connecting_path", f"edge:{edge.type}")
    )
    return CandidateEvidence(
        id=f"graph:{edge.source}->{edge.target}:{edge.type}",
        provider="graph",
        route="connecting_path",
        page=edge.target,
        source=None,
        locator={"field": "mentioned_in"},
        content=f"mentioned_in: {edge.source}",
        roles=("bridge",),
        selection_signals=bridge_signals,
        authored_state=state.normalized,
        derived_flags=state.derived_flags,
        authority_signals=("explicit_current",) if state.normalized == "current" else (),
    )
