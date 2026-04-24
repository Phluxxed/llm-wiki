#!/usr/bin/env python3
"""
query.py — query YAML frontmatter across all wiki pages (Dataview CLI equivalent).

Usage:
    python3 scripts/query.py                       # all pages summary table
    python3 scripts/query.py --status Draft
    python3 scripts/query.py --category "AI / Claude"
    python3 scripts/query.py --type entity
    python3 scripts/query.py --tag claude-api
    python3 scripts/query.py --stale 90            # not reviewed in 90+ days
    python3 scripts/query.py --risks               # aggregate ⚠️ / 🔲 risk rows
"""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required: pip3 install pyyaml")

WIKI_ROOT = Path(__file__).parent.parent
EXCLUDE_FILES = {"wiki-agent.md", "CLAUDE.md", "AGENTS.md", "GEMINI.md", "CONVENTIONS.md", "README.md", "index.md", "log.md"}
EXCLUDE_DIRS = {"sources", "_templates", "scripts", ".git", ".obsidian"}

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


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Query wiki YAML frontmatter")
    parser.add_argument("--status",   help="Filter by status (Draft|Active|Deprecated)")
    parser.add_argument("--category", help="Filter by category (substring match)")
    parser.add_argument("--type",     help="Filter by page type (entity|concept|use-case|meta)")
    parser.add_argument("--tag",      help="Filter by tag")
    parser.add_argument("--stale",    type=int, metavar="DAYS",
                        help="Pages not reviewed in N+ days")
    parser.add_argument("--risks",    action="store_true",
                        help="Aggregate open risk register rows (⚠️ / 🔲)")
    args = parser.parse_args()

    pages = collect_pages()
    pages = apply_filters(pages, args)

    if args.risks:
        cmd_risks(pages)
    else:
        cmd_summary(pages)


if __name__ == "__main__":
    main()
