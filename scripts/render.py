#!/usr/bin/env python3
"""
render.py — generate wiki.html: a single self-contained reader for the wiki.

Replaces scripts/graph.py. Same call pattern (the agent runs this after every
wiki change), but produces a richer artifact with eight views: Home, Page,
Search, Graph, Risks, Recent changes, Open questions, Entities.

Usage:
    python3 scripts/render.py            # writes wiki.html to wiki root
    python3 scripts/render.py --output path/to/out.html
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required: pip3 install pyyaml")

try:
    import markdown as md_lib
except ImportError:
    sys.exit("markdown required: pip3 install markdown")

WIKI_ROOT = Path(__file__).parent.parent
EXCLUDE_FILES = {"wiki-agent.md", "CLAUDE.md", "AGENTS.md", "GEMINI.md", "CONVENTIONS.md", "README.md", "index.md", "log.md"}
EXCLUDE_DIRS = {"sources", "_templates", "scripts", ".git", ".obsidian", "evals", "docs", "tests"}


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


def page_type(fm: dict) -> str:
    t = fm.get("type", "")
    if t in ("entity", "concept"):
        return t
    cat = (fm.get("category") or "").lower()
    if "meta" in cat:
        return "meta"
    return "use-case"


def split_frontmatter_and_body(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm = parse_frontmatter(text)
    body = text[end + 4:].lstrip("\n")
    return fm, body


_MD = md_lib.Markdown(extensions=["extra", "sane_lists", "tables", "toc"])


def render_markdown(body: str) -> str:
    _MD.reset()
    return _MD.convert(body)


def build_search_index(pages: dict) -> list[dict]:
    return [
        {
            "id": path,
            "title": page["title"],
            "category": page["fm"].get("category") or "",
            "tags": list(page["tags"]),
            "body": page["body"],
        }
        for path, page in pages.items()
    ]


OPEN_Q_RE = re.compile(r"^>\s*\*\*Open question:\*\*\s*(.+?)\s*$", re.MULTILINE)


def extract_open_qs(pages: dict) -> list[dict]:
    out = []
    for path, page in pages.items():
        for m in OPEN_Q_RE.finditer(page["body"]):
            out.append({
                "page": path,
                "page_title": page["title"],
                "question": m.group(1),
            })
    return out


RISK_OPEN_SYMBOLS = ("⚠️", "🔲")


def _parse_risk_rows(body: str) -> list[dict]:
    rows = []
    in_register = False
    in_table = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("##"):
            in_register = "risk register" in stripped.lower()
            in_table = False
            continue
        if not in_register:
            continue
        if stripped.startswith("|") and "---" in stripped:
            in_table = True
            continue
        if not in_table or not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 5:
            continue
        risk, likelihood, impact, mitigation, status = cells[:5]
        if risk.lower() == "risk":
            continue
        rows.append({
            "risk": risk,
            "likelihood": likelihood,
            "impact": impact,
            "mitigation": mitigation,
            "status": status,
        })
    return rows


def extract_risks(pages: dict) -> list[dict]:
    risks = []
    for path, page in pages.items():
        for row in _parse_risk_rows(page["body"]):
            symbol = next((s for s in RISK_OPEN_SYMBOLS if row["status"].startswith(s)), None)
            if symbol is None:
                continue
            risks.append({
                "page": path,
                "page_title": page["title"],
                "status_symbol": symbol,
                **row,
            })
    return risks


LOG_LINE_RE = re.compile(r"^##\s*\[(\d{4}-\d{2}-\d{2})\]\s*([^|]+?)\s*\|\s*(.+?)\s*$")


def collect_log(wiki_root: Path = WIKI_ROOT) -> list[dict]:
    log_path = wiki_root / "log.md"
    if not log_path.exists():
        return []
    entries = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        m = LOG_LINE_RE.match(line)
        if m:
            entries.append({"date": m.group(1), "action": m.group(2), "detail": m.group(3)})
    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries


def collect_edges(pages: dict) -> list[tuple[str, str]]:
    edges = set()
    link_re = re.compile(r'\[(?:[^\]]+)\]\(\.?/?([^)#\s]+\.md)\)')
    for src_file, page in pages.items():
        for raw in link_re.findall(page["body"]):
            tgt = raw[2:] if raw.startswith("./") else raw
            tgt = tgt.replace("\\", "/")
            if tgt in pages and tgt != src_file:
                edges.add((src_file, tgt))
        mentioned = page["fm"].get("mentioned_in") or []
        for referrer in mentioned:
            referrer = str(referrer).replace("\\", "/")
            if referrer.startswith("./"):
                referrer = referrer[2:]
            if referrer in pages and referrer != src_file:
                edges.add((referrer, src_file))
    return sorted(edges)


def collect_pages(wiki_root: Path = WIKI_ROOT) -> dict:
    pages = {}
    for path in sorted(wiki_root.rglob("*.md")):
        rel = path.relative_to(wiki_root)
        if rel.parts[0] in EXCLUDE_DIRS:
            continue
        if path.name in EXCLUDE_FILES:
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
            "rendered_html": render_markdown(body),
        }
    return pages


HTML_HEAD_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0f1117; color: #e2e8f0; font-family: system-ui, -apple-system, sans-serif; line-height: 1.55; }
a { color: #93c5fd; text-decoration: none; } a:hover { text-decoration: underline; }
#layout { display: grid; grid-template-columns: 240px 1fr; min-height: 100vh; }
#sidebar { background: #0a0d14; border-right: 1px solid #1a2030; padding: 18px 14px; overflow-y: auto; }
#sidebar h1 { font-size: 14px; color: #cbd5e1; margin-bottom: 14px; }
#sidebar nav { display: flex; flex-direction: column; gap: 2px; }
#sidebar nav button { background: none; border: none; color: #94a3b8; text-align: left; padding: 6px 8px; border-radius: 4px; cursor: pointer; font-size: 13px; }
#sidebar nav button:hover { background: #11151f; color: #e2e8f0; }
#sidebar nav button.active { background: #172033; color: #93c5fd; }
#main { padding: 24px 32px; overflow-y: auto; max-height: 100vh; }
.view { display: none; }
.view.active { display: block; }
h2 { font-size: 18px; color: #cbd5e1; margin-bottom: 16px; font-weight: 600; }
.muted { color: #64748b; font-size: 12px; }
.card { background: #11151f; border: 1px solid #1f2937; border-radius: 6px; padding: 14px 16px; margin-bottom: 10px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; background: #1e2130; color: #94a3b8; margin-right: 6px; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid #1f2937; font-size: 13px; vertical-align: top; }
th { color: #94a3b8; font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
input[type="text"] { width: 100%; background: #1e2130; border: 1px solid #2d3748; color: #e2e8f0; padding: 8px 10px; border-radius: 4px; font-size: 13px; outline: none; }
input[type="text"]:focus { border-color: #60a5fa; }
.markdown-body h1 { font-size: 22px; margin: 18px 0 10px; color: #e2e8f0; }
.markdown-body h2 { font-size: 17px; margin: 18px 0 8px; color: #cbd5e1; }
.markdown-body h3 { font-size: 14px; margin: 14px 0 6px; color: #cbd5e1; }
.markdown-body p { margin-bottom: 10px; }
.markdown-body ul, .markdown-body ol { margin: 0 0 10px 24px; }
.markdown-body code { background: #11151f; padding: 1px 4px; border-radius: 3px; font-size: 12px; }
.markdown-body pre { background: #11151f; padding: 10px; border-radius: 4px; overflow-x: auto; margin-bottom: 10px; }
.markdown-body blockquote { border-left: 3px solid #334155; padding-left: 12px; color: #94a3b8; margin: 10px 0; }
.markdown-body table { margin: 10px 0; }
"""


HTML_NAV_BUTTONS = [
    ("home", "Home"),
    ("search", "Search"),
    ("graph", "Graph"),
    ("risks", "Risks"),
    ("recent", "Recent changes"),
    ("open-qs", "Open questions"),
    ("entities", "Entities"),
]


def _nav_html() -> str:
    buttons = "\n".join(
        f'      <button data-view="{key}">{label}</button>'
        for key, label in HTML_NAV_BUTTONS
    )
    return f"""<nav id="sidebar">
  <h1>Wiki</h1>
  <nav>
{buttons}
  </nav>
</nav>"""


HTML_SCRIPT_VIEW_SWITCH = """
const buttons = document.querySelectorAll('#sidebar nav button');
const views = document.querySelectorAll('.view');
function showView(name) {
  views.forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
  buttons.forEach(b => b.classList.toggle('active', b.dataset.view === name));
  if (name === 'graph' && window.renderGraph) window.renderGraph();
}
buttons.forEach(b => b.addEventListener('click', () => showView(b.dataset.view)));
showView('home');
"""


def render_html(
    pages: dict,
    edges: list,
    log: list,
    risks: list,
    open_qs: list,
    search_docs: list,
) -> str:
    data = {
        "pages": {
            path: {
                "path": path,
                "title": p["title"],
                "type": p["type"],
                "category": p["fm"].get("category") or "",
                "status": p["fm"].get("status") or "",
                "owner": p["fm"].get("owner") or "",
                "tags": list(p["tags"]),
                "created": str(p["fm"].get("created") or ""),
                "last_reviewed": str(p["fm"].get("last_reviewed") or ""),
                "source": p["fm"].get("source") or "",
                "rendered_html": p["rendered_html"],
            }
            for path, p in pages.items()
        },
        "edges": list(edges),
        "log": list(log),
        "risks": list(risks),
        "open_qs": list(open_qs),
        "search": list(search_docs),
    }
    data_json = json.dumps(data, ensure_ascii=False)

    view_ids = ["home", "page", "search", "graph", "risks", "recent", "open-qs", "entities"]
    view_divs = "\n".join(f'    <section class="view" id="view-{vid}"></section>' for vid in view_ids)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wiki</title>
<style>{HTML_HEAD_CSS}</style>
</head>
<body>
<div id="layout">
{_nav_html()}
  <main id="main">
{view_divs}
  </main>
</div>
<script>
window.WIKI_DATA = {data_json};
</script>
<script>
{HTML_SCRIPT_VIEW_SWITCH}
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(WIKI_ROOT / "wiki.html"))
    args = parser.parse_args()
    Path(args.output).write_text("<!DOCTYPE html><html><body>Empty wiki.</body></html>", encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
