from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping

from .documents import WikiPage


BODY_LINK_RE = re.compile(r"\[(?:[^\]]+)\]\(([^)#\s]+\.md)\)")
OPEN_QUESTION_RE = re.compile(r"^>\s*\*\*Open question:\*\*\s*(.+?)\s*$", re.MULTILINE)
ATTENTION_RE = re.compile(
    r"^>\s*\*\*(Risk|Caveat|Failure mode):\*\*\s*(.+?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
LOG_LINE_RE = re.compile(r"^##\s*\[(\d{4}-\d{2}-\d{2})\]\s*([^|]+?)\s*\|\s*(.+?)\s*$")
OPEN_RISK_MARKERS = ("⚠️", "🔲")


@dataclass(frozen=True, order=True)
class Edge:
    source: str
    target: str
    type: str
    weight: float


def normalize_page_ref(raw: str) -> str:
    reference = str(raw).replace("\\", "/")
    return reference[2:] if reference.startswith("./") else reference


def resolve_link(raw: str, source_file: str, targets: set[str] | Mapping[str, object]) -> str | None:
    reference = str(raw).replace("\\", "/")
    cleaned = reference[2:] if reference.startswith("./") else reference
    if cleaned in targets:
        return cleaned

    parts = source_file.split("/")[:-1]
    for component in cleaned.split("/"):
        if component == "..":
            if not parts:
                return None
            parts.pop()
        elif component and component != ".":
            parts.append(component)
    resolved = "/".join(parts)
    return resolved if resolved in targets else None


def collect_typed_edges(pages: Mapping[str, WikiPage]) -> list[Edge]:
    edges: dict[tuple[str, str, str], Edge] = {}

    def add(source: str, target: str, edge_type: str, weight: float) -> None:
        if source == target:
            return
        key = (source, target, edge_type)
        edge = Edge(source, target, edge_type, weight)
        if key not in edges or edges[key].weight < weight:
            edges[key] = edge

    for source_file, page in pages.items():
        for raw in BODY_LINK_RE.findall(page.body):
            target = resolve_link(raw, source_file, pages)
            if target:
                add(source_file, target, "body_link", 1.0)

        mentioned_in = page.frontmatter.get("mentioned_in")
        references = mentioned_in if isinstance(mentioned_in, list) else [mentioned_in] if mentioned_in else []
        for referrer in references:
            referrer_key = normalize_page_ref(str(referrer))
            if referrer_key in pages:
                add(referrer_key, source_file, "mentioned_in", 2.0)

    return sorted(edges.values())


def outgoing_edges(edges: list[Edge], page: str) -> list[Edge]:
    return sorted(edge for edge in edges if edge.source == page)


def incoming_edges(edges: list[Edge], page: str) -> list[Edge]:
    return sorted(edge for edge in edges if edge.target == page)


def neighborhood(
    seed: str,
    pages: Mapping[str, WikiPage],
    edges: list[Edge],
    *,
    depth: int = 1,
) -> list[dict]:
    seed = normalize_page_ref(seed)
    if seed not in pages:
        return []
    visited = {seed}
    frontier = {seed}
    items: dict[str, dict] = {}
    for distance in range(1, max(1, depth) + 1):
        next_frontier = set()
        for page in sorted(frontier):
            for neighbor, reason, edge in _edge_steps(edges, page):
                if neighbor == seed or neighbor not in pages:
                    continue
                item = items.setdefault(
                    neighbor,
                    {
                        "page": neighbor,
                        "title": pages[neighbor].title,
                        "distance": distance,
                        "reasons": set(),
                        "score": 0.0,
                    },
                )
                item["distance"] = min(item["distance"], distance)
                item["reasons"].add(reason)
                item["score"] += edge.weight / distance
                if neighbor not in visited:
                    next_frontier.add(neighbor)
        visited.update(next_frontier)
        frontier = next_frontier
        if not frontier:
            break
    output = [
        {**item, "reasons": sorted(item["reasons"]), "score": round(item["score"], 3)}
        for item in items.values()
    ]
    return sorted(output, key=lambda item: (item["distance"], -item["score"], item["page"]))


def related_pages(
    seed: str,
    pages: Mapping[str, WikiPage],
    edges: list[Edge],
    *,
    depth: int = 2,
) -> list[dict]:
    seed = normalize_page_ref(seed)
    if seed not in pages:
        return []
    candidates: dict[str, dict] = {}

    def add(page: str, reason: str, score: float) -> None:
        if page == seed or page not in pages:
            return
        item = candidates.setdefault(
            page,
            {"page": page, "title": pages[page].title, "reasons": set(), "score": 0.0},
        )
        item["reasons"].add(reason)
        item["score"] += score

    for item in neighborhood(seed, pages, edges, depth=depth):
        for reason in item["reasons"]:
            add(item["page"], reason, item["score"])

    seed_source = str(pages[seed].frontmatter.get("source") or "")
    if seed_source:
        for path, page in pages.items():
            if str(page.frontmatter.get("source") or "") == seed_source:
                add(path, f"shared_source:{seed_source}", 0.8)

    seed_tags = {str(tag).lower() for tag in pages[seed].tags}
    for path, page in pages.items():
        for tag in sorted(seed_tags & {str(value).lower() for value in page.tags})[:3]:
            add(path, f"shared_tag:{tag}", 0.3)

    seed_neighbors = _neighbor_set(seed, edges)
    for path in pages:
        if path == seed:
            continue
        common = seed_neighbors & _neighbor_set(path, edges)
        if common:
            add(path, f"common_neighbors:{len(common)}", min(0.5, 0.2 * len(common)))

    output = [
        {**item, "reasons": sorted(item["reasons"]), "score": round(item["score"], 3)}
        for item in candidates.values()
    ]
    return sorted(output, key=lambda item: (-item["score"], item["page"]))


def graph_health(pages: Mapping[str, WikiPage], edges: list[Edge], hub_limit: int = 10) -> dict:
    degrees = {}
    for path in pages:
        outgoing = len(outgoing_edges(edges, path))
        incoming = len(incoming_edges(edges, path))
        degrees[path] = {"page": path, "out": outgoing, "in": incoming, "degree": outgoing + incoming}
    hubs = sorted(degrees.values(), key=lambda item: (-item["degree"], item["page"]))
    hubs = [hub for hub in hubs if hub["degree"] > 0][:hub_limit]
    orphans = sorted(path for path, item in degrees.items() if item["degree"] == 0)
    without_source = sorted(
        path
        for path, page in pages.items()
        if not page.frontmatter.get("source") and page.type not in {"entity", "concept", "meta"}
    )
    type_counts: dict[str, int] = {}
    for page in pages.values():
        type_counts[page.type] = type_counts.get(page.type, 0) + 1
    return {
        "page_count": len(pages),
        "edge_count": len({(edge.source, edge.target) for edge in edges}),
        "type_counts": dict(sorted(type_counts.items())),
        "orphans": orphans,
        "hubs": hubs,
        "components": connected_components(pages, edges),
        "pages_without_source": without_source,
    }


def connected_components(pages: Mapping[str, WikiPage], edges: list[Edge]) -> list[list[str]]:
    unseen = set(pages)
    components: list[list[str]] = []
    while unseen:
        start = min(unseen)
        component = set()
        frontier = [start]
        while frontier:
            page = frontier.pop()
            if page in component:
                continue
            component.add(page)
            frontier.extend(sorted(_neighbor_set(page, edges) - component, reverse=True))
        unseen -= component
        components.append(sorted(component))
    return sorted(components, key=lambda component: (-len(component), component[0]))


def extract_open_questions(page: WikiPage) -> list[str]:
    return [match.strip() for match in OPEN_QUESTION_RE.findall(page.body)]


def extract_open_risks(page: WikiPage) -> list[dict]:
    rows = [
        {
            "kind": match.group(1).lower(),
            "risk": match.group(2).strip(),
            "likelihood": "",
            "impact": "",
            "mitigation": "",
            "status": "⚠️ Attention",
        }
        for match in ATTENTION_RE.finditer(page.body)
    ]
    in_table = False
    header_seen = False
    for line in page.body.splitlines():
        stripped = line.strip()
        if "Risk" in stripped and "Likelihood" in stripped and "|" in stripped:
            in_table = True
            header_seen = False
            continue
        if in_table and stripped.startswith("|") and not stripped.replace("|", "").replace("-", "").strip():
            header_seen = True
            continue
        if in_table and stripped.startswith("|") and header_seen:
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) >= 5 and any(marker in cells[4] for marker in OPEN_RISK_MARKERS):
                rows.append(
                    {
                        "risk": cells[0],
                        "likelihood": cells[1],
                        "impact": cells[2],
                        "mitigation": cells[3],
                        "status": cells[4],
                    }
                )
        elif in_table and not stripped.startswith("|"):
            in_table = False
            header_seen = False
    return rows


def collect_log(wiki_root: Path) -> list[dict]:
    path = wiki_root / "log.md"
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = LOG_LINE_RE.match(line.strip())
        if match:
            entries.append(
                {
                    "date": match.group(1),
                    "action": match.group(2).strip(),
                    "detail": match.group(3).strip(),
                }
            )
    return list(reversed(entries))


def _edge_steps(edges: list[Edge], page: str) -> list[tuple[str, str, Edge]]:
    steps = [(edge.target, f"outlink:{edge.type}", edge) for edge in outgoing_edges(edges, page)]
    steps.extend((edge.source, f"backlink:{edge.type}", edge) for edge in incoming_edges(edges, page))
    return sorted(steps, key=lambda item: (item[0], item[1]))


def _neighbor_set(page: str, edges: list[Edge]) -> set[str]:
    neighbors = {edge.target for edge in outgoing_edges(edges, page)}
    neighbors.update(edge.source for edge in incoming_edges(edges, page))
    return neighbors
