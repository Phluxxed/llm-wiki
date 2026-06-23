"""Shared graph helpers for agent-facing wiki traversal."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

try:
    import yaml
except ImportError:  # pragma: no cover - scripts fail loudly at import sites too
    yaml = None


DEFAULT_EXCLUDE_FILES = {
    "wiki-agent.md",
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    "CONVENTIONS.md",
    "README.md",
    "index.md",
    "log.md",
}
DEFAULT_EXCLUDE_DIRS = {"sources", "_templates", "scripts", ".git", ".obsidian", ".venv", "evals", ".eval"}

BODY_LINK_RE = re.compile(r'\[(?:[^\]]+)\]\(([^)#\s]+\.md)\)')
OPEN_Q_RE = re.compile(r"^>\s*\*\*Open question:\*\*\s*(.+?)\s*$", re.MULTILINE)
LOG_LINE_RE = re.compile(r"^##\s*\[(\d{4}-\d{2}-\d{2})\]\s*([^|]+?)\s*\|\s*(.+?)\s*$")
OPEN_RISK_MARKERS = ("\u26a0\ufe0f", "\U0001f532")


@dataclass(frozen=True, order=True)
class Edge:
    source: str
    target: str
    type: str
    weight: float


def parse_frontmatter(text: str) -> dict:
    if yaml is None:
        raise RuntimeError("pyyaml required: run `uv venv && uv pip install pyyaml markdown`, then use `.venv/bin/python3`")
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return {}


def split_frontmatter_and_body(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm = parse_frontmatter(text)
    body = text[end + 4:].lstrip("\n")
    return fm, body


def page_type(fm: dict) -> str:
    t = (fm.get("type") or "").strip()
    if t:
        return t
    cat = (fm.get("category") or "").lower()
    if "meta" in cat:
        return "meta"
    return "primary"


def collect_pages(
    wiki_root: Path,
    exclude_files: set[str] | None = None,
    exclude_dirs: set[str] | None = None,
) -> dict[str, dict]:
    exclude_files = DEFAULT_EXCLUDE_FILES if exclude_files is None else exclude_files
    exclude_dirs = DEFAULT_EXCLUDE_DIRS if exclude_dirs is None else exclude_dirs
    pages = {}

    for path in sorted(wiki_root.rglob("*.md")):
        rel = path.relative_to(wiki_root)
        if rel.parts[0] in exclude_dirs:
            continue
        if path.name in exclude_files:
            continue
        text = path.read_text(encoding="utf-8")
        fm, body = split_frontmatter_and_body(text)
        if not fm:
            continue
        key = str(rel).replace("\\", "/")
        pages[key] = {
            "path": key,
            "title": fm.get("title") or path.stem.replace("-", " ").title(),
            "type": page_type(fm),
            "tags": list(fm.get("tags") or []),
            "fm": fm,
            "body": body,
            "text": text,
        }

    return pages


def normalize_page_ref(raw: str) -> str:
    ref = str(raw).replace("\\", "/")
    return ref[2:] if ref.startswith("./") else ref


def resolve_link(raw: str, src_file: str, targets: set | dict) -> str | None:
    """Resolve a markdown link target to a wiki-root-relative key."""
    raw = str(raw).replace("\\", "/")
    cleaned = raw[2:] if raw.startswith("./") else raw

    if cleaned in targets:
        return cleaned

    src_dir_parts = src_file.split("/")[:-1]
    parts = list(src_dir_parts)
    for component in cleaned.split("/"):
        if component == "..":
            if parts:
                parts.pop()
        elif component and component != ".":
            parts.append(component)
    resolved = "/".join(parts)
    return resolved if resolved in targets else None


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def collect_typed_edges(pages: dict[str, dict]) -> list[Edge]:
    edges: dict[tuple[str, str, str], Edge] = {}

    def add(source: str, target: str, edge_type: str, weight: float) -> None:
        if source == target:
            return
        key = (source, target, edge_type)
        edge = Edge(source, target, edge_type, weight)
        if key not in edges or edges[key].weight < weight:
            edges[key] = edge

    for src_file, page in pages.items():
        for raw in BODY_LINK_RE.findall(page["body"]):
            target = resolve_link(raw, src_file, pages)
            if target:
                add(src_file, target, "body_link", 1.0)

        for referrer in _as_list(page["fm"].get("mentioned_in")):
            referrer_key = normalize_page_ref(referrer)
            if referrer_key in pages:
                add(referrer_key, src_file, "mentioned_in", 2.0)

    return sorted(edges.values())


def edge_pairs(edges: list[Edge]) -> list[tuple[str, str]]:
    return sorted({(edge.source, edge.target) for edge in edges})


def outgoing_edges(edges: list[Edge], page: str) -> list[Edge]:
    return [edge for edge in edges if edge.source == page]


def incoming_edges(edges: list[Edge], page: str) -> list[Edge]:
    return [edge for edge in edges if edge.target == page]


def _edge_steps(edges: list[Edge], page: str) -> list[tuple[str, str, Edge]]:
    steps = []
    for edge in outgoing_edges(edges, page):
        steps.append((edge.target, f"outlink:{edge.type}", edge))
    for edge in incoming_edges(edges, page):
        steps.append((edge.source, f"backlink:{edge.type}", edge))
    return steps


def neighborhood(seed: str, pages: dict[str, dict], edges: list[Edge], depth: int = 1) -> list[dict]:
    seed = normalize_page_ref(seed)
    if seed not in pages:
        return []

    max_depth = max(1, depth)
    visited = {seed}
    frontier = {seed}
    items: dict[str, dict] = {}

    for distance in range(1, max_depth + 1):
        next_frontier = set()
        for page in sorted(frontier):
            for neighbor, reason, edge in _edge_steps(edges, page):
                if neighbor == seed or neighbor not in pages:
                    continue
                item = items.setdefault(
                    neighbor,
                    {
                        "page": neighbor,
                        "title": pages[neighbor]["title"],
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

    out = []
    for item in items.values():
        out.append({
            **item,
            "reasons": sorted(item["reasons"]),
            "score": round(item["score"], 3),
        })
    return sorted(out, key=lambda x: (x["distance"], -x["score"], x["page"]))


def _neighbor_set(page: str, edges: list[Edge]) -> set[str]:
    neighbors = set()
    for edge in edges:
        if edge.source == page:
            neighbors.add(edge.target)
        if edge.target == page:
            neighbors.add(edge.source)
    return neighbors


def related_pages(seed: str, pages: dict[str, dict], edges: list[Edge], depth: int = 2) -> list[dict]:
    seed = normalize_page_ref(seed)
    if seed not in pages:
        return []

    candidates: dict[str, dict] = {}

    def add(page: str, reason: str, score: float) -> None:
        if page == seed or page not in pages:
            return
        item = candidates.setdefault(
            page,
            {"page": page, "title": pages[page]["title"], "reasons": set(), "score": 0.0},
        )
        item["reasons"].add(reason)
        item["score"] += score

    for item in neighborhood(seed, pages, edges, depth=depth):
        for reason in item["reasons"]:
            add(item["page"], reason, item["score"])

    seed_fm = pages[seed]["fm"]
    seed_source = str(seed_fm.get("source") or "")
    if seed_source:
        for path, page in pages.items():
            if str(page["fm"].get("source") or "") == seed_source:
                add(path, f"shared_source:{seed_source}", 0.8)

    seed_tags = {str(tag).lower() for tag in pages[seed].get("tags") or []}
    if seed_tags:
        for path, page in pages.items():
            tags = {str(tag).lower() for tag in page.get("tags") or []}
            shared = sorted(seed_tags & tags)
            for tag in shared[:3]:
                add(path, f"shared_tag:{tag}", 0.3)

    seed_neighbors = _neighbor_set(seed, edges)
    for path in pages:
        if path == seed:
            continue
        common = sorted(seed_neighbors & _neighbor_set(path, edges))
        if common:
            add(path, f"common_neighbors:{len(common)}", min(0.5, 0.2 * len(common)))

    out = []
    for item in candidates.values():
        out.append({
            **item,
            "reasons": sorted(item["reasons"]),
            "score": round(item["score"], 3),
        })
    return sorted(out, key=lambda x: (-x["score"], x["page"]))


def connected_components(pages: dict[str, dict], edges: list[Edge]) -> list[list[str]]:
    unseen = set(pages)
    components = []

    while unseen:
        start = min(unseen)
        stack = [start]
        component = set()
        unseen.remove(start)
        while stack:
            page = stack.pop()
            component.add(page)
            for neighbor in _neighbor_set(page, edges):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
        components.append(sorted(component))

    return sorted(components, key=lambda c: (-len(c), c[0] if c else ""))


def graph_health(pages: dict[str, dict], edges: list[Edge], hub_limit: int = 10) -> dict:
    degrees = {}
    for path in pages:
        out_count = len(outgoing_edges(edges, path))
        in_count = len(incoming_edges(edges, path))
        degrees[path] = {"page": path, "out": out_count, "in": in_count, "degree": out_count + in_count}

    hubs = sorted(degrees.values(), key=lambda x: (-x["degree"], x["page"]))
    hubs = [hub for hub in hubs if hub["degree"] > 0][:hub_limit]
    orphans = sorted(path for path, degree in degrees.items() if degree["degree"] == 0)
    components = connected_components(pages, edges)
    pages_without_source = sorted(
        path for path, page in pages.items()
        if not page["fm"].get("source") and page["type"] not in {"entity", "concept", "meta"}
    )

    type_counts: dict[str, int] = {}
    for page in pages.values():
        type_counts[page["type"]] = type_counts.get(page["type"], 0) + 1

    return {
        "page_count": len(pages),
        "edge_count": len(edge_pairs(edges)),
        "type_counts": dict(sorted(type_counts.items())),
        "orphans": orphans,
        "hubs": hubs,
        "components": components,
        "pages_without_source": pages_without_source,
    }


def extract_open_questions(page: dict) -> list[str]:
    return [match.strip() for match in OPEN_Q_RE.findall(page.get("body") or "")]


def extract_open_risks(page: dict) -> list[dict]:
    rows = []
    in_table = False
    header_seen = False

    for line in (page.get("body") or "").splitlines():
        stripped = line.strip()
        if "Risk" in stripped and "Likelihood" in stripped and "|" in stripped:
            in_table = True
            header_seen = False
            continue
        if in_table and stripped.startswith("|") and set(stripped.replace("|", "").replace("-", "").strip()) == set():
            header_seen = True
            continue
        if in_table and stripped.startswith("|") and header_seen:
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) >= 5 and any(marker in cells[4] for marker in OPEN_RISK_MARKERS):
                rows.append({
                    "risk": cells[0],
                    "likelihood": cells[1],
                    "impact": cells[2],
                    "mitigation": cells[3],
                    "status": cells[4],
                })
        elif in_table and not stripped.startswith("|"):
            in_table = False
            header_seen = False

    return rows


def collect_log(wiki_root: Path) -> list[dict]:
    path = wiki_root / "log.md"
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = LOG_LINE_RE.match(line.strip())
        if match:
            entries.append({
                "date": match.group(1),
                "action": match.group(2).strip(),
                "detail": match.group(3).strip(),
            })
    return list(reversed(entries))
