from __future__ import annotations

from collections import deque
from dataclasses import replace

from ..graph import BODY_LINK_RE, Edge, collect_typed_edges, resolve_link
from ..state import normalize_knowledge_state
from .base import CandidateEvidence, ProviderContext
from .utils import page_matches_question, question_terms, title_matches_question


class GraphProvider:
    name = "graph"

    def collect(self, context: ProviderContext) -> list[CandidateEvidence]:
        if "relationship" not in context.shapes:
            return []
        edges = collect_typed_edges(context.pages)
        terms = question_terms(context.request.question)
        named_anchors = {
            path for path, page in context.pages.items()
            if title_matches_question(page.title, context.request.question)
        }
        # Body mentions discover candidates only when the question names no
        # page. Otherwise a nearby mention could replace a missing endpoint.
        question_anchors = named_anchors or {
            path for path, page in context.pages.items()
            if page_matches_question(page, context.request.question, terms)
        }
        explicit_anchors = set(context.resolved_seeds)
        scope_anchors = set(context.scope_seeds)
        anchors = explicit_anchors | scope_anchors
        if len(anchors) < 2:
            anchors.update(question_anchors)
        if not anchors:
            return []

        candidates: list[CandidateEvidence] = []
        sorted_anchors = sorted(anchors)
        for index, source in enumerate(sorted_anchors):
            for target in sorted_anchors[index + 1 :]:
                paths = [(edge,) for edge in edges
                         if {edge.source, edge.target} == {source, target}]
                if not paths:
                    path = _shortest_path(source, target, edges, max_depth=3)
                    paths = [path] if path else []
                discovery: tuple[str, ...] = ()
                question_derived = source in question_anchors or target in question_anchors
                if not question_derived:
                    discovery = tuple(
                        signal for signal, seeds in (
                            ("scope_seed_discovery", scope_anchors),
                            ("explicit_seed_discovery", explicit_anchors),
                        ) if source in seeds or target in seeds
                    )
                for path in paths:
                    candidate = _path_candidate(path, context, discovery)
                    if candidate is not None:
                        # Internal scope anchors discover relationships but do not
                        # supply a user-requested endpoint on their own.
                        if not ({source, target} <= question_anchors or {source, target} <= explicit_anchors):
                            candidate = replace(candidate, roles=("support",))
                        candidates.append(candidate)
        if len(anchors) == 1:
            discovery = tuple(
                signal for signal, seeds in (
                    ("scope_seed_discovery", scope_anchors),
                    ("explicit_seed_discovery", explicit_anchors),
                ) if anchors & seeds
            )
            for edge in edges:
                if edge.source in anchors or edge.target in anchors:
                    candidate = _edge_candidate(edge, context, discovery)
                    if candidate is not None:
                        candidates.append(replace(candidate, roles=("support",)))
        return candidates


def _path_candidate(
    path: tuple[Edge, ...],
    context: ProviderContext,
    discovery_signals: tuple[str, ...],
) -> CandidateEvidence | None:
    spans = [_edge_candidate(edge, context, discovery_signals) for edge in path]
    if any(span is None for span in spans):
        return None
    if len(spans) == 1:
        # A declared relationship is indivisible even when its authored line
        # is longer than the caller's remaining content allowance.
        return replace(spans[0], atomic=True)
    first = spans[0]
    return replace(
        first,
        id="graph:path:" + "|".join(span.id.removeprefix("graph:") for span in spans),
        locator={"spans": [
            {"id": span.id, "page": span.page, "authored_state": span.authored_state, **span.locator} for span in spans
        ]},
        content="\n\n".join(f"{span.page}: {span.content}" for span in spans),
        selection_signals=tuple(dict.fromkeys(
            signal for span in spans for signal in span.selection_signals
            if signal not in {"seed_authored_multi_bridge", "multi_seed_bridge"}
        )),
        authored_state=min(
            (span.authored_state for span in spans),
            key=("superseded", "historical", "contradicted", "weak", "inferred", "unspecified", "current").index,
        ),
        derived_flags=tuple(dict.fromkeys(flag for span in spans for flag in span.derived_flags)),
        authority_signals=("explicit_current",) if all(span.authored_state == "current" for span in spans) else (),
        atomic=True,
    )


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


def _edge_candidate(
    edge: Edge,
    context: ProviderContext,
    discovery_signals: tuple[str, ...] = (),
) -> CandidateEvidence | None:
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
                            *discovery_signals,
                        )
                    elif explicit_seed_bridge:
                        bridge_signals = (
                            "explicit_seed_bridge",
                            "connecting_path",
                            f"edge:{edge.type}",
                            *discovery_signals,
                        )
                    elif len(linked_seed_targets) >= 2:
                        bridge_signals = (
                            "multi_seed_bridge",
                            "connecting_path",
                            f"edge:{edge.type}",
                            *discovery_signals,
                        )
                    else:
                        bridge_signals = (
                            "connecting_path",
                            f"edge:{edge.type}",
                            *discovery_signals,
                        )
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
        (
            "explicit_seed_bridge",
            "connecting_path",
            f"edge:{edge.type}",
            *discovery_signals,
        )
        if explicit_seed_bridge
        else ("connecting_path", f"edge:{edge.type}", *discovery_signals)
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
