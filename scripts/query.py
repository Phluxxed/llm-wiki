#!/usr/bin/env python3
"""
query.py — query YAML frontmatter across all wiki pages (Dataview CLI equivalent).

Usage:
    .venv/bin/python3 scripts/query.py                       # all pages summary table
    .venv/bin/python3 scripts/query.py --status Draft
    .venv/bin/python3 scripts/query.py --category "AI / Claude"
    .venv/bin/python3 scripts/query.py --type entity
    .venv/bin/python3 scripts/query.py --tag claude-api
    .venv/bin/python3 scripts/query.py --stale 90            # not reviewed in 90+ days
    .venv/bin/python3 scripts/query.py --risks               # aggregate ⚠️ / 🔲 risk rows
    .venv/bin/python3 scripts/query.py --agent-overview       # first-pass agent map
    .venv/bin/python3 scripts/query.py --context-pack page.md --json # agent working context
"""

import argparse
import difflib
import json as json_lib
import sys
from datetime import date, datetime
from pathlib import Path

import wiki_graph

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required: run `uv venv && uv pip install pyyaml markdown`, then use `.venv/bin/python3`")

WIKI_ROOT = Path(__file__).parent.parent
EXCLUDE_FILES = {"wiki-agent.md", "CLAUDE.md", "AGENTS.md", "GEMINI.md", "CONVENTIONS.md", "README.md", "index.md", "log.md"}
EXCLUDE_DIRS = {"sources", "_templates", "scripts", ".git", ".obsidian", ".venv"}

STATUS_ICONS = {"⚠️": "⚠️", "🔲": "🔲", "✅": "✅"}
OPEN_STATUSES = {"⚠️", "🔲"}


# ── parsing ──────────────────────────────────────────────────────────────────

def parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return {}


def collect_pages() -> list[dict]:
    pages = []
    for md in sorted(WIKI_ROOT.rglob("*.md")):
        rel = md.relative_to(WIKI_ROOT)
        if rel.parts[0] in EXCLUDE_DIRS:
            continue
        if md.name in EXCLUDE_FILES:
            continue
        text = md.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        if not fm:
            continue
        fm["_file"] = str(rel)
        fm["_text"] = text
        pages.append(fm)
    return pages


def parse_risk_rows(text: str, filename: str) -> list[dict]:
    """Extract Risk Register table rows with open status (⚠️ or 🔲)."""
    rows = []
    in_table = False
    header_seen = False

    for line in text.splitlines():
        stripped = line.strip()
        if "Risk" in stripped and "Likelihood" in stripped and "|" in stripped:
            in_table = True
            header_seen = False
            continue
        if in_table and stripped.startswith("|") and set(stripped.replace("|", "").replace("-", "").strip()) == set():
            header_seen = True
            continue
        if in_table and stripped.startswith("|") and header_seen:
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) >= 5:
                status_cell = cells[4]
                if any(icon in status_cell for icon in OPEN_STATUSES):
                    rows.append({
                        "file": filename,
                        "risk": cells[0],
                        "likelihood": cells[1],
                        "impact": cells[2],
                        "status": status_cell,
                    })
        elif in_table and not stripped.startswith("|"):
            in_table = False
            header_seen = False

    return rows


# ── formatting ────────────────────────────────────────────────────────────────

def col_widths(rows: list[list[str]]) -> list[int]:
    if not rows:
        return []
    return [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    all_rows = [headers] + rows
    widths = col_widths(all_rows)
    fmt = lambda row: "| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) + " |"
    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    lines = [fmt(headers), sep] + [fmt(r) for r in rows]
    return "\n".join(lines)


def fmt_date(val) -> str:
    if val is None:
        return ""
    if isinstance(val, (date, datetime)):
        return str(val)[:10]
    return str(val)


def infer_type(fm: dict) -> str:
    t = fm.get("type", "")
    if t in ("entity", "concept"):
        return t
    cat = fm.get("category", "").lower()
    if "meta" in cat:
        return "meta"
    return "use-case"


# ── filters ───────────────────────────────────────────────────────────────────

def days_since(val) -> int | None:
    if val is None:
        return None
    try:
        if isinstance(val, (date, datetime)):
            d = val if isinstance(val, date) else val.date()
        else:
            d = datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
        return (date.today() - d).days
    except ValueError:
        return None


def apply_filters(pages: list[dict], args: argparse.Namespace) -> list[dict]:
    out = pages

    if args.status:
        out = [p for p in out if str(p.get("status", "")).lower() == args.status.lower()]

    if args.category:
        out = [p for p in out if args.category.lower() in str(p.get("category", "")).lower()]

    if args.type:
        out = [p for p in out if infer_type(p) == args.type.lower()]

    if args.tag:
        out = [p for p in out if args.tag.lower() in [str(t).lower() for t in (p.get("tags") or [])]]

    if args.stale is not None:
        out = [p for p in out if (days_since(p.get("last_reviewed")) or 0) >= args.stale]

    return out


# ── commands ──────────────────────────────────────────────────────────────────

def cmd_summary(pages: list[dict]) -> None:
    if not pages:
        print("No pages match.")
        return
    headers = ["File", "Title", "Category", "Status", "Last Reviewed"]
    rows = [
        [
            p["_file"],
            str(p.get("title") or "")[:50],
            str(p.get("category") or ""),
            str(p.get("status") or ""),
            fmt_date(p.get("last_reviewed")),
        ]
        for p in pages
    ]
    print(md_table(headers, rows))
    print(f"\n{len(pages)} page(s)")


def cmd_risks(pages: list[dict]) -> None:
    all_risks = []
    for p in pages:
        all_risks.extend(parse_risk_rows(p["_text"], p["_file"]))

    if not all_risks:
        print("No open risk rows found (⚠️ or 🔲).")
        return

    headers = ["File", "Risk", "Likelihood", "Impact", "Status"]
    rows = [
        [r["file"], r["risk"][:60], r["likelihood"], r["impact"], r["status"]]
        for r in all_risks
    ]
    print(md_table(headers, rows))
    print(f"\n{len(all_risks)} open risk row(s) across {len({r['file'] for r in all_risks})} page(s)")


def risks_data(pages: list[dict]) -> dict:
    risks = []
    for page in pages:
        risks.extend(parse_risk_rows(page["_text"], page["_file"]))
    return {"kind": "risks", "count": len(risks), "risks": risks}


def summary_data(pages: list[dict]) -> dict:
    return {
        "kind": "summary",
        "count": len(pages),
        "pages": [
            {
                "page": page["_file"],
                "title": str(page.get("title") or ""),
                "category": str(page.get("category") or ""),
                "status": str(page.get("status") or ""),
                "type": infer_type(page),
                "tags": list(page.get("tags") or []),
                "last_reviewed": fmt_date(page.get("last_reviewed")),
            }
            for page in pages
        ],
    }


def _print_json(payload: dict) -> None:
    print(json_lib.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _page_record(path: str, pages: dict[str, dict]) -> dict:
    page = pages[path]
    fm = page["fm"]
    return {
        "page": path,
        "title": page["title"],
        "type": page["type"],
        "category": str(fm.get("category") or ""),
        "tags": list(page.get("tags") or []),
        "source": str(fm.get("source") or ""),
        "description": str(fm.get("description") or ""),
    }


def _graph() -> tuple[dict[str, dict], list[wiki_graph.Edge]]:
    pages = wiki_graph.collect_pages(WIKI_ROOT, exclude_files=EXCLUDE_FILES, exclude_dirs=EXCLUDE_DIRS | {"evals", ".eval"})
    return pages, wiki_graph.collect_typed_edges(pages)


def _page_or_exit(raw: str, pages: dict[str, dict]) -> str:
    page = wiki_graph.normalize_page_ref(raw)
    if page in pages:
        return page
    matches = difflib.get_close_matches(page, sorted(pages), n=5)
    suggestion = f" Did you mean: {', '.join(matches)}?" if matches else ""
    raise SystemExit(f"unknown page: {raw}.{suggestion}")


def _print_edge_table(title: str, rows: list[list[str]], empty: str) -> None:
    print(f"# {title}")
    if not rows:
        print(empty)
        return
    print(md_table(["Page", "Title", "Type", "Weight"], rows))


def _link_records(page: str, pages: dict[str, dict], edges: list[wiki_graph.Edge]) -> list[dict]:
    return [
        {**_page_record(edge.target, pages), "edge_type": edge.type, "weight": edge.weight}
        for edge in wiki_graph.outgoing_edges(edges, page)
        if edge.target in pages
    ]


def _backlink_records(page: str, pages: dict[str, dict], edges: list[wiki_graph.Edge]) -> list[dict]:
    return [
        {**_page_record(edge.source, pages), "edge_type": edge.type, "weight": edge.weight}
        for edge in wiki_graph.incoming_edges(edges, page)
        if edge.source in pages
    ]


def cmd_links(page: str, pages: dict[str, dict], edges: list[wiki_graph.Edge], as_json: bool = False) -> None:
    links = _link_records(page, pages, edges)
    if as_json:
        _print_json({"kind": "links", **_page_record(page, pages), "links": links})
        return
    rows = [
        [item["page"], item["title"], item["edge_type"], f"{item['weight']:.2f}"]
        for item in links
    ]
    _print_edge_table(f"Links: {page}", rows, "No outgoing links.")


def cmd_backlinks(page: str, pages: dict[str, dict], edges: list[wiki_graph.Edge], as_json: bool = False) -> None:
    backlinks = _backlink_records(page, pages, edges)
    if as_json:
        _print_json({"kind": "backlinks", **_page_record(page, pages), "backlinks": backlinks})
        return
    rows = [[item["page"], item["title"], item["edge_type"], f"{item['weight']:.2f}"] for item in backlinks]
    _print_edge_table(f"Backlinks: {page}", rows, "No backlinks.")


def _around_records(page: str, pages: dict[str, dict], edges: list[wiki_graph.Edge], depth: int) -> list[dict]:
    return [
        {
            **_page_record(item["page"], pages),
            "distance": item["distance"],
            "reasons": item["reasons"],
            "score": item["score"],
        }
        for item in wiki_graph.neighborhood(page, pages, edges, depth=depth)
    ]


def cmd_around(page: str, pages: dict[str, dict], edges: list[wiki_graph.Edge], depth: int, as_json: bool = False) -> None:
    around = _around_records(page, pages, edges, depth)
    if as_json:
        _print_json({"kind": "around", "seed": _page_record(page, pages), "depth": depth, "pages": around})
        return
    rows = [
        [item["page"], item["title"], str(item["distance"]), ", ".join(item["reasons"]), f"{item['score']:.2f}"]
        for item in around
    ]
    print(f"# Around: {page}")
    if not rows:
        print("No connected pages found.")
        return
    print(md_table(["Page", "Title", "Distance", "Reasons", "Score"], rows))


def _open_question_records(pages: dict[str, dict], limit: int = 10) -> list[dict]:
    rows = []
    for path, page in pages.items():
        for question in wiki_graph.extract_open_questions(page):
            rows.append({"page": path, "title": page["title"], "question": question})
    return rows[:limit]


def _open_risk_records(pages: dict[str, dict], limit: int = 10) -> list[dict]:
    rows = []
    for path, page in pages.items():
        for risk in wiki_graph.extract_open_risks(page):
            rows.append({"page": path, "title": page["title"], **risk})
    return rows[:limit]


def agent_overview_data(pages: dict[str, dict], edges: list[wiki_graph.Edge]) -> dict:
    health = wiki_graph.graph_health(pages, edges)
    return {
        "kind": "agent_overview",
        "page_count": health["page_count"],
        "edge_count": health["edge_count"],
        "type_counts": health["type_counts"],
        "suggested_entry_pages": [
            {**_page_record(hub["page"], pages), "in": hub["in"], "out": hub["out"], "degree": hub["degree"]}
            for hub in health["hubs"][:10]
        ],
        "orphans": [_page_record(path, pages) for path in health["orphans"][:20]],
        "open_questions": _open_question_records(pages),
        "open_risks": _open_risk_records(pages),
        "recent_log": wiki_graph.collect_log(WIKI_ROOT)[:5],
    }


def cmd_agent_overview(pages: dict[str, dict], edges: list[wiki_graph.Edge], as_json: bool = False) -> None:
    data = agent_overview_data(pages, edges)
    if as_json:
        _print_json(data)
        return
    print("# Agent Overview")
    print(f"Pages: {data['page_count']}")
    print(f"Edges: {data['edge_count']}")

    if data["type_counts"]:
        print("\n## Page Types")
        rows = [[page_type, str(count)] for page_type, count in data["type_counts"].items()]
        print(md_table(["Type", "Count"], rows))

    print("\n## Suggested Entry Pages")
    if data["suggested_entry_pages"]:
        rows = [[hub["page"], hub["title"], str(hub["degree"])] for hub in data["suggested_entry_pages"]]
        print(md_table(["Page", "Title", "Degree"], rows))
    else:
        print("No linked hubs found yet.")

    print("\n## Orphans")
    if data["orphans"]:
        rows = [[item["page"], item["title"]] for item in data["orphans"]]
        print(md_table(["Page", "Title"], rows))
    else:
        print("No orphan pages.")

    questions = data["open_questions"]
    print("\n## Open Questions")
    question_rows = [[item["page"], item["question"][:100]] for item in questions]
    print(md_table(["Page", "Question"], question_rows) if question_rows else "No open questions found.")

    risks = data["open_risks"]
    print("\n## Open Risks")
    risk_rows = [[item["page"], item["risk"][:80], item["status"]] for item in risks]
    print(md_table(["Page", "Risk", "Status"], risk_rows) if risk_rows else "No open risks found.")

    log_entries = data["recent_log"]
    print("\n## Recent Log Context")
    if log_entries:
        rows = [[entry["date"], entry["action"], entry["detail"]] for entry in log_entries]
        print(md_table(["Date", "Action", "Detail"], rows))
    else:
        print("No log entries found.")


def graph_health_data(pages: dict[str, dict], edges: list[wiki_graph.Edge]) -> dict:
    health = wiki_graph.graph_health(pages, edges)
    return {
        "kind": "graph_health",
        "page_count": health["page_count"],
        "edge_count": health["edge_count"],
        "type_counts": health["type_counts"],
        "components": health["components"],
        "hubs": [
            {**_page_record(hub["page"], pages), "in": hub["in"], "out": hub["out"], "degree": hub["degree"]}
            for hub in health["hubs"]
        ],
        "orphans": [_page_record(path, pages) for path in health["orphans"]],
        "pages_without_source": [_page_record(path, pages) for path in health["pages_without_source"]],
    }


def cmd_graph_health(pages: dict[str, dict], edges: list[wiki_graph.Edge], as_json: bool = False) -> None:
    data = graph_health_data(pages, edges)
    if as_json:
        _print_json(data)
        return
    print("# Graph Health")
    print(f"Pages: {data['page_count']}")
    print(f"Edges: {data['edge_count']}")
    print(f"Components: {len(data['components'])}")

    print("\n## Hubs")
    if data["hubs"]:
        rows = [[hub["page"], str(hub["in"]), str(hub["out"]), str(hub["degree"])] for hub in data["hubs"]]
        print(md_table(["Page", "In", "Out", "Degree"], rows))
    else:
        print("No hubs found.")

    print("\n## Orphans")
    if data["orphans"]:
        rows = [[item["page"], item["title"]] for item in data["orphans"]]
        print(md_table(["Page", "Title"], rows))
    else:
        print("No orphan pages.")

    print("\n## Pages Without Source")
    if data["pages_without_source"]:
        rows = [[item["page"], item["title"]] for item in data["pages_without_source"]]
        print(md_table(["Page", "Title"], rows))
    else:
        print("No source gaps found.")


def _page_content(page: dict, max_chars: int = 4000) -> str:
    body = (page.get("body") or "").strip()
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "\n\n[truncated]"
    return body or "[empty body]"


def _page_block(path: str, page: dict, max_chars: int = 4000) -> str:
    body = _page_content(page, max_chars=max_chars)
    return f"### {path} - {page['title']}\n\n{body or '[empty body]'}"


def _source_path(wiki_root: Path, source: str) -> Path | None:
    if not source:
        return None
    normalized = str(source).replace("\\", "/")
    if normalized.startswith("/") or not normalized.startswith("sources/") or ".." in Path(normalized).parts:
        return None
    sources_root = (wiki_root / "sources").resolve()
    path = (wiki_root / normalized).resolve()
    if not path.is_relative_to(sources_root):
        return None
    return path


def _source_excerpt(wiki_root: Path, source: str, max_chars: int = 1600) -> str | None:
    path = _source_path(wiki_root, source)
    if path is None:
        return None
    if not path.exists() or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n[truncated]"
    return text


def _relevant_log_entries(paths: list[str], pages: dict[str, dict], limit: int = 8) -> list[dict]:
    terms = set()
    for path in paths:
        terms.add(path.lower())
        terms.add(Path(path).name.lower())
        title = str(pages[path].get("title") or "").lower()
        if title:
            terms.add(title)

    matches = []
    for entry in wiki_graph.collect_log(WIKI_ROOT):
        detail = entry["detail"].lower()
        if any(term and term in detail for term in terms):
            matches.append(entry)
    return matches[:limit]


def build_context_pack_data(page: str, pages: dict[str, dict], edges: list[wiki_graph.Edge], tokens: int) -> dict:
    candidates = wiki_graph.related_pages(page, pages, edges, depth=2)
    included = candidates[:12]
    included_paths = [page] + [item["page"] for item in included]
    char_budget = max(1000, tokens * 4)

    included_pages = [
        {
            **_page_record(item["page"], pages),
            "reasons": item["reasons"],
            "score": item["score"],
            "content": _page_content(pages[item["page"]], max_chars=2200),
        }
        for item in included
    ]

    source_refs = []
    for path in included_paths:
        source = str(pages[path]["fm"].get("source") or "")
        if source:
            source_refs.append({"page": path, "source": source})

    source_seen = set()
    source_excerpts = []
    for ref in source_refs:
        source = ref["source"]
        if source in source_seen:
            continue
        source_seen.add(source)
        excerpt = _source_excerpt(WIKI_ROOT, source)
        if excerpt:
            source_excerpts.append({"source": source, "content": excerpt})

    open_questions = []
    open_risks = []
    for path in included_paths:
        for question in wiki_graph.extract_open_questions(pages[path]):
            open_questions.append({"page": path, "question": question})
        for risk in wiki_graph.extract_open_risks(pages[path]):
            open_risks.append({"page": path, **risk})

    gaps = []
    for path in included_paths:
        source = str(pages[path]["fm"].get("source") or "")
        source_path = _source_path(WIKI_ROOT, source)
        if source and (source_path is None or not source_path.exists()):
            gaps.append({"page": path, "gap": f"source_missing:{source}"})
    if not wiki_graph.incoming_edges(edges, page) and not wiki_graph.outgoing_edges(edges, page):
        gaps.append({"page": page, "gap": "seed_has_no_graph_edges"})

    return {
        "kind": "context_pack",
        "seed": {**_page_record(page, pages), "content": _page_content(pages[page])},
        "budget": {"requested_tokens": tokens, "approx_chars": char_budget},
        "included_pages": included_pages,
        "source_refs": source_refs,
        "source_excerpts": source_excerpts,
        "open_questions": open_questions,
        "open_risks": open_risks,
        "recent_log": _relevant_log_entries(included_paths, pages),
        "gaps": gaps,
    }


def build_context_pack(page: str, pages: dict[str, dict], edges: list[wiki_graph.Edge], tokens: int) -> str:
    data = build_context_pack_data(page, pages, edges, tokens)
    char_budget = data["budget"]["approx_chars"]

    parts = [
        f"# Context Pack: {page}",
        "",
        "## How To Use This Pack",
        "- Treat source-linked pages and raw source excerpts as stronger evidence than summaries.",
        "- Treat risks and open questions as unresolved, not factual.",
        "- Use inclusion reasons to decide what to inspect next.",
        "",
        "## Seed",
        f"- {page}",
        "",
        "## Included Pages",
    ]

    rows = [[item["page"], ", ".join(item["reasons"]), f"{item['score']:.2f}"] for item in data["included_pages"]]
    parts.append(md_table(["Page", "Reason", "Score"], rows) if rows else "No related pages found.")

    parts.extend(["", "## Seed Page", f"### {data['seed']['page']} - {data['seed']['title']}\n\n{data['seed']['content']}"])

    if data["included_pages"]:
        parts.extend(["", "## Nearby Pages"])
        for item in data["included_pages"]:
            parts.append(f"### {item['page']} - {item['title']}\n\n{item['content']}")

    source_rows = [[ref["page"], ref["source"]] for ref in data["source_refs"]]
    parts.extend(["", "## Source References"])
    parts.append(md_table(["Page", "Source"], source_rows) if source_rows else "No source references found.")

    source_blocks = [f"### {item['source']}\n\n{item['content']}" for item in data["source_excerpts"]]
    if source_blocks:
        parts.extend(["", "## Source Excerpts", *source_blocks])

    question_rows = [[item["page"], item["question"]] for item in data["open_questions"]]
    risk_rows = [
        [item["page"], item["risk"], item["likelihood"], item["impact"], item["status"]]
        for item in data["open_risks"]
    ]
    parts.extend(["", "## Risks And Open Questions"])
    parts.append("### Open Questions")
    parts.append(md_table(["Page", "Question"], question_rows) if question_rows else "No open questions found.")
    parts.append("")
    parts.append("### Open Risks")
    parts.append(md_table(["Page", "Risk", "Likelihood", "Impact", "Status"], risk_rows) if risk_rows else "No open risks found.")

    log_entries = data["recent_log"]
    parts.extend(["", "## Recent Log Context"])
    if log_entries:
        rows = [[entry["date"], entry["action"], entry["detail"]] for entry in log_entries]
        parts.append(md_table(["Date", "Action", "Detail"], rows))
    else:
        parts.append("No relevant log entries found.")

    gaps = [[item["page"], item["gap"]] for item in data["gaps"]]
    parts.extend(["", "## Gaps"])
    parts.append(md_table(["Page", "Gap"], gaps) if gaps else "No immediate graph/source gaps found.")

    rendered = "\n".join(parts).rstrip() + "\n"
    if len(rendered) > char_budget:
        rendered = rendered[:char_budget].rstrip() + f"\n\n[truncated to approximate {tokens} token budget]\n"
    return rendered


def cmd_context_pack(page: str, pages: dict[str, dict], edges: list[wiki_graph.Edge], tokens: int, as_json: bool = False) -> None:
    if as_json:
        _print_json(build_context_pack_data(page, pages, edges, tokens))
        return
    print(build_context_pack(page, pages, edges, tokens), end="")


# ── main ──────────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(description="Query wiki YAML frontmatter")
    parser.add_argument("--status",   help="Filter by status (Draft|Active|Deprecated)")
    parser.add_argument("--category", help="Filter by category (substring match)")
    parser.add_argument("--type",     help="Filter by page type (entity|concept|use-case|meta)")
    parser.add_argument("--tag",      help="Filter by tag")
    parser.add_argument("--stale",    type=int, metavar="DAYS",
                        help="Pages not reviewed in N+ days")
    parser.add_argument("--risks",    action="store_true",
                        help="Aggregate open risk register rows (⚠️ / 🔲)")
    parser.add_argument("--agent-overview", action="store_true",
                        help="Show agent-oriented wiki overview and first entry points")
    parser.add_argument("--links", metavar="PAGE",
                        help="List outgoing graph links for PAGE")
    parser.add_argument("--backlinks", metavar="PAGE",
                        help="List incoming graph links for PAGE")
    parser.add_argument("--around", metavar="PAGE",
                        help="List graph neighborhood around PAGE")
    parser.add_argument("--depth", type=int, default=1,
                        help="Traversal depth for --around (default: 1)")
    parser.add_argument("--graph-health", action="store_true",
                        help="Report graph health: hubs, orphans, components, source gaps")
    parser.add_argument("--context-pack", metavar="PAGE",
                        help="Build deterministic agent context around PAGE")
    parser.add_argument("--tokens", type=int, default=12000,
                        help="Approximate token budget for --context-pack (default: 12000)")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="Emit machine-readable JSON for agents")
    args = parser.parse_args(argv)

    if args.agent_overview or args.links or args.backlinks or args.around or args.graph_health or args.context_pack:
        graph_pages, edges = _graph()
        if args.agent_overview:
            cmd_agent_overview(graph_pages, edges, as_json=args.as_json)
            return
        if args.graph_health:
            cmd_graph_health(graph_pages, edges, as_json=args.as_json)
            return
        if args.links:
            page = _page_or_exit(args.links, graph_pages)
            cmd_links(page, graph_pages, edges, as_json=args.as_json)
            return
        if args.backlinks:
            page = _page_or_exit(args.backlinks, graph_pages)
            cmd_backlinks(page, graph_pages, edges, as_json=args.as_json)
            return
        if args.around:
            page = _page_or_exit(args.around, graph_pages)
            cmd_around(page, graph_pages, edges, args.depth, as_json=args.as_json)
            return
        if args.context_pack:
            page = _page_or_exit(args.context_pack, graph_pages)
            cmd_context_pack(page, graph_pages, edges, args.tokens, as_json=args.as_json)
            return

    pages = collect_pages()
    pages = apply_filters(pages, args)

    if args.as_json:
        _print_json(risks_data(pages) if args.risks else summary_data(pages))
        return

    if args.risks:
        cmd_risks(pages)
    else:
        cmd_summary(pages)


if __name__ == "__main__":
    main()
