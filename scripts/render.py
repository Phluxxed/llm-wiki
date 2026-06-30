#!/usr/bin/env python3
"""
render.py — generate wiki.html: a single self-contained reader for the wiki.

Replaces scripts/graph.py. Same call pattern (the agent runs this after every
wiki change), but produces a richer artifact with eight views: Home, Page,
Search, Graph, Risks, Recent changes, Open questions, Entities.

Usage:
    .venv/bin/python3 scripts/render.py            # writes wiki.html to wiki root
    .venv/bin/python3 scripts/render.py --output path/to/out.html
"""

import argparse
import json
import re
import sys
from pathlib import Path

import wiki_graph

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required: run `uv venv && uv pip install pyyaml markdown`, then use `.venv/bin/python3`")

try:
    import markdown as md_lib
except ImportError:
    sys.exit("markdown required: run `uv venv && uv pip install pyyaml markdown`, then use `.venv/bin/python3`")

WIKI_ROOT = Path(__file__).parent.parent
EXCLUDE_FILES = {"wiki-agent.md", "CLAUDE.md", "AGENTS.md", "GEMINI.md", "CONVENTIONS.md", "README.md", "index.md", "log.md"}
EXCLUDE_DIRS = {"sources", "_templates", "scripts", ".git", ".obsidian", ".venv", "evals", ".eval", "docs", "tests"}


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
    """Resolve the type label used for grouping, colouring, and filtering.

    - Explicit `type:` field is honoured for any non-empty value (entity,
      concept, meta, or any custom value like article/policy/control —
      this is what makes multi-primary-type wikis work).
    - If no `type:` is set, the page is "primary" by default. Pages with
      `category:` containing "meta" still resolve to "meta" for back-compat
      with the old category-only meta convention.
    """
    t = (fm.get("type") or "").strip()
    if t:
        return t
    cat = (fm.get("category") or "").lower()
    if "meta" in cat:
        return "meta"
    return "primary"


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
ATTENTION_RE = re.compile(r"^>\s*\*\*(Risk|Caveat|Failure mode):\*\*\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)


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


def _parse_attention_items(body: str) -> list[dict]:
    return [
        {
            "kind": match.group(1).lower(),
            "risk": match.group(2).strip(),
            "likelihood": "",
            "impact": "",
            "mitigation": "",
            "status": "⚠️ Attention",
        }
        for match in ATTENTION_RE.finditer(body)
    ]


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
        for row in _parse_attention_items(page["body"]):
            risks.append({
                "page": path,
                "page_title": page["title"],
                "status_symbol": "⚠️",
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


def resolve_link(raw: str, src_file: str, targets: set | dict) -> str | None:
    """Resolve a markdown link target to a wiki-root-relative key.

    Tries wiki-root-relative interpretation first (`./components/X.md`,
    `components/X.md`), then falls back to source-relative resolution so
    sibling `./X.md` and `../other-dir/X.md` links from inside a sub-dir
    also resolve. Returns the matched key, or None.

    `targets` may be a dict (membership test against keys) or a set.
    """
    return wiki_graph.resolve_link(raw, src_file, targets)


def collect_edges(pages: dict) -> list[tuple[str, str]]:
    return wiki_graph.edge_pairs(wiki_graph.collect_typed_edges(pages))


_INTERNAL_LINK_RE = re.compile(r'<a\s+href="([^"]+\.md)"')


def rewrite_internal_links(html: str, src_file: str, pages: dict) -> str:
    """Add data-page attributes to <a> links pointing at wiki pages.

    Markdown like [X](./sibling.md) renders as <a href="./sibling.md">X</a>
    with no SPA hook, so the browser tries to navigate to the file URL.
    This injects data-page="<resolved-key>" so the existing renderPage
    click handler intercepts the click and opens the page in-app.

    Links that don't resolve to a wiki page (external URLs, source files,
    anchors) are left untouched and behave as standard browser links.
    """
    def repl(m: re.Match) -> str:
        href = m.group(1)
        resolved = resolve_link(href, src_file, pages)
        if resolved is None:
            return m.group(0)
        return f'<a href="{href}" data-page="{resolved}"'

    return _INTERNAL_LINK_RE.sub(repl, html)


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
    # Post-process: rewrite intra-wiki .md links to include data-page so the
    # SPA click handler picks them up. Runs after the dict is fully populated
    # so cross-page links resolve regardless of write order.
    for key, page in pages.items():
        page["rendered_html"] = rewrite_internal_links(page["rendered_html"], key, pages)
    return pages


HTML_HEAD_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --font-body: 'IBM Plex Sans', system-ui, -apple-system, sans-serif;
  --font-display: 'Newsreader', Georgia, 'Times New Roman', serif;
  --font-mono: 'IBM Plex Mono', ui-monospace, 'SF Mono', Menlo, monospace;
}
body { background: #0f1117; color: #e2e8f0; font-family: var(--font-body); line-height: 1.55; -webkit-font-smoothing: antialiased; }
/* Editorial serif for display/titles/section heads; mono for instrumentation. */
.masthead h1, .view-title, .article-title, .ph-title,
.markdown-body h1, .markdown-body h2, .markdown-body h3 { font-family: var(--font-display); }
.kicker, .article-meta, .toc, .toc-title, .view-count, .badge, .ttag,
.sb-section-title, .sb-brand-text, .rail-block-title, .cat-label, .home-section-title,
.stat .l, .sb-mark + .sb-brand-text { font-family: var(--font-mono); }
a { color: #93c5fd; text-decoration: none; } a:hover { text-decoration: underline; }
#layout { display: grid; grid-template-columns: 260px 1fr; min-height: 100vh; }
#sidebar { background: #0a0d14; border-right: 1px solid #1a2030; padding: 18px 14px; overflow-y: auto; max-height: 100vh; }
.sb-brand { display: flex; align-items: center; gap: 9px; margin-bottom: 16px; padding: 0 4px; }
.sb-mark { width: 10px; height: 10px; border-radius: 3px; background: linear-gradient(135deg, #fbbf24, #fb7185); flex-shrink: 0; }
.sb-brand-text { font-size: 12.5px; font-weight: 650; color: #e2e8f0; line-height: 1.25; }
#sidebar > nav { display: flex; flex-direction: column; gap: 2px; }
#sidebar > nav > button { background: none; border: none; color: #94a3b8; text-align: left; padding: 6px 8px; border-radius: 4px; cursor: pointer; font-size: 13px; }
#sidebar > nav > button:hover { background: #11151f; color: #e2e8f0; }
#sidebar > nav > button.active { background: #172033; color: #93c5fd; }
.sb-divider { border: none; border-top: 1px solid #1a2030; margin: 14px 0 10px; }
.sb-section-title { font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; color: #475569; margin: 0 8px 6px; font-weight: 600; }
.sb-cat { margin-bottom: 2px; }
.sb-cat-header { font-size: 11px; color: #cbd5e1; padding: 4px 8px; cursor: pointer; user-select: none; display: flex; align-items: center; gap: 4px; border-radius: 3px; }
.sb-cat-header:hover { background: #11151f; }
.sb-chev { font-size: 9px; color: #64748b; width: 10px; display: inline-block; }
.sb-cat-body { display: block; }
.sb-cat.collapsed .sb-cat-body { display: none; }
.sb-page { background: none; border: none; color: #94a3b8; text-align: left; padding: 4px 8px 4px 22px; border-radius: 3px; cursor: pointer; font-size: 12px; width: 100%; display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sb-page:hover { background: #11151f; color: #e2e8f0; }
.sb-page.active { background: #172033; color: #93c5fd; }
#main { padding: 24px 32px; overflow-y: auto; max-height: 100vh; }
.view { display: none; }
.view.active { display: block; }
h2 { font-size: 18px; color: #cbd5e1; margin-bottom: 16px; font-weight: 600; }
.muted { color: #94a3b8; font-size: 12px; }
.card { background: #11151f; border: 1px solid #1f2937; border-radius: 7px; padding: 14px 16px; margin-bottom: 10px; transition: border-color 0.12s, background 0.12s; }
.card[data-page]:hover, .card[style*="cursor"]:hover { border-color: #2d3a4f; background: #131825; }
/* Unified view header */
.view-header { margin-bottom: 22px; }
.view-title { font-size: 27px; font-weight: 560; color: #f5f8fc; letter-spacing: -0.005em; }
.view-count { font-size: 15px; color: #475569; font-weight: 500; margin-left: 3px; }
.view-sub { color: #94a3b8; font-size: 13px; margin-top: 5px; max-width: 660px; line-height: 1.5; }
/* Unified entrance fade (graph excluded — it's a live canvas) */
@keyframes viewIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
.view.active { animation: viewIn 0.22s ease both; }
#view-graph.active { animation: none; }
@media (prefers-reduced-motion: reduce) { .view.active { animation: none; } }
/* Open-question cards + risk page cell */
.oq-list { display: flex; flex-direction: column; gap: 10px; }
.oq-card { max-width: 820px; }
.oq-q { font-size: 14px; line-height: 1.55; color: #d7dee8; }
.oq-src { display: flex; align-items: center; gap: 7px; margin-top: 9px; font-size: 12px; color: #93c5fd; }
.cell-page { min-width: 180px; }
.cell-page .tdot { margin-right: 7px; vertical-align: middle; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; background: #1e2130; color: #94a3b8; margin-right: 6px; }
.badge.prov-source { background: #102036; border: 1px solid #1d3357; color: #93c5fd; }
.badge.prov-synth  { background: #0e2a20; border: 1px solid #14533d; color: #6ee7b7; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #1f2937; font-size: 13px; vertical-align: top; line-height: 1.5; }
th { color: #94a3b8; font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
tbody tr:nth-child(even) { background: #0c1019; }
.lvl { display: inline-block; min-width: 22px; text-align: center; padding: 1px 7px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.lvl-h { background: #3a1518; color: #fca5a5; }
.lvl-m { background: #382c10; color: #fcd34d; }
.lvl-l { background: #102619; color: #6ee7b7; }
.recent-detail { line-height: 1.55; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.recent-detail.expandable { cursor: pointer; }
.recent-detail.expanded { display: block; -webkit-line-clamp: unset; }
.recent-detail-more { font-size: 11px; color: #60a5fa; cursor: pointer; margin-top: 4px; user-select: none; }
.recent-detail code { background: #1b2230; border: 1px solid #2a3344; padding: 0 4px; border-radius: 3px; font-size: 0.9em; }
input[type="text"] { width: 100%; background: #1e2130; border: 1px solid #2d3748; color: #e2e8f0; padding: 8px 10px; border-radius: 4px; font-size: 13px; outline: none; }
input[type="text"]:focus { border-color: #60a5fa; }
/* ── Reading column ───────────────────────────────────────────────────── */
.markdown-body { font-size: 15.5px; line-height: 1.7; color: #d7dee8; }
.markdown-body > h1:first-child { display: none; }  /* title already shown in page header */
.markdown-body h1 { font-size: 30px; font-weight: 560; margin: 28px 0 12px; color: #f1f5f9; line-height: 1.2; }
.markdown-body h2 { font-size: 24px; font-weight: 560; margin: 34px 0 13px; padding-bottom: 8px; border-bottom: 1px solid #1c2433; color: #f1f5f9; line-height: 1.25; letter-spacing: -0.005em; }
.markdown-body h3 { font-size: 18px; font-weight: 600; margin: 24px 0 8px; color: #dbe3ee; }
.markdown-body p { margin-bottom: 14px; }
.markdown-body li { margin-bottom: 5px; }
.markdown-body ul, .markdown-body ol { margin: 0 0 14px 24px; }
.markdown-body strong { font-weight: 600; color: #f1f5f9; }
.markdown-body a { border-bottom: 1px solid rgba(147,197,253,0.25); }
.markdown-body a:hover { border-bottom-color: #93c5fd; text-decoration: none; }
.markdown-body code { background: #1b2230; border: 1px solid #2a3344; padding: 1px 5px; border-radius: 4px; font-size: 0.88em; color: #cbd5e1; }
.markdown-body pre { background: #0b0e16; border: 1px solid #1f2937; padding: 14px 16px; border-radius: 6px; overflow-x: auto; margin-bottom: 14px; }
.markdown-body pre code { background: none; border: none; padding: 0; color: #cbd5e1; }
.markdown-body blockquote { border-left: 3px solid #3b82f6; background: #0d1520; padding: 10px 16px; color: #cbd5e1; margin: 14px 0; border-radius: 0 5px 5px 0; }
.markdown-body table { margin: 14px 0; }
#view-page aside { font-size: 13px; }
#view-page aside ul li { margin-bottom: 9px; line-height: 1.45; }
#view-page aside a { font-size: 12.5px; }

/* ── Type colour language ─────────────────────────────────────────────── */
.tdot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.ttag { display: inline-block; font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; padding: 2px 8px; border: 1px solid; border-radius: 10px; vertical-align: middle; }

/* ── Home ─────────────────────────────────────────────────────────────── */
.masthead { margin-bottom: 26px; }
.masthead h1 { font-size: 34px; color: #f5f8fc; font-weight: 560; letter-spacing: -0.01em; margin-bottom: 8px; }
.masthead .tagline { color: #94a3b8; font-size: 13px; max-width: 620px; line-height: 1.5; }
.stats { display: flex; gap: 26px; margin: 18px 0 6px; flex-wrap: wrap; }
.stat { display: flex; align-items: baseline; gap: 7px; }
.stat .n { font-family: var(--font-display); font-size: 28px; font-weight: 500; color: #f5f8fc; }
.stat .l { font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; }
.home-section-title { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #64748b; font-weight: 600; margin: 30px 0 12px; display: flex; align-items: center; gap: 8px; }
.home-section-title::after { content: ""; flex: 1; height: 1px; background: #1a2030; }
.cat-label { font-size: 12px; color: #7c8aa0; font-weight: 600; margin: 22px 0 10px; }
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 12px; }
.tcard { background: #11151f; border: 1px solid #1f2937; border-radius: 7px; padding: 13px 15px; cursor: pointer; transition: border-color 0.12s, background 0.12s; }
.tcard:hover { border-color: #2d3a4f; background: #131825; }
.tcard .tcard-title { display: flex; align-items: center; gap: 8px; font-weight: 600; color: #e2e8f0; font-size: 14px; line-height: 1.35; }
.tcard .tcard-summary { color: #94a3b8; font-size: 12.5px; line-height: 1.5; margin-top: 6px; }
.tcard .tcard-meta { color: #64748b; font-size: 11px; margin-top: 8px; display: flex; gap: 10px; align-items: center; }
.position-hero { background: linear-gradient(180deg, #1a1217 0%, #11151f 60%); border: 1px solid #3a2230; border-left: 3px solid #fb7185; border-radius: 8px; padding: 18px 20px; cursor: pointer; transition: border-color 0.12s; }
.position-hero:hover { border-left-color: #fda4af; }
.position-hero .ph-title { font-size: 17px; font-weight: 650; color: #f1f5f9; line-height: 1.35; }
.position-hero .ph-summary { color: #cbd5e1; font-size: 13.5px; line-height: 1.6; margin-top: 8px; max-width: 760px; }
.position-hero .ph-meta { margin-top: 12px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.search-result-summary { color: #94a3b8; font-size: 12.5px; line-height: 1.45; margin-top: 5px; }
.entity-gloss { color: #94a3b8; font-size: 12px; line-height: 1.4; margin-top: 3px; }
.cites-row { margin-top: 10px; font-size: 12px; }
.cites-row .cites-label { color: #64748b; margin-right: 6px; }

/* ── Reading page ─────────────────────────────────────────────────────── */
/* Desk layout: full-width header, then [contents · wide reading · evidence].
   The reading column is fluid so it fills a widescreen rather than capping narrow. */
.article-shell { --accent: #60a5fa; }
.article-header { padding-bottom: 24px; border-bottom: 1px solid #1a2230; margin-bottom: 30px; }
.article-cols { display: grid; grid-template-columns: 184px minmax(0, 1fr) 244px; gap: 48px; align-items: start; }
.kicker { display: inline-flex; align-items: center; gap: 8px; font-size: 11px; font-weight: 600; letter-spacing: 0.16em; text-transform: uppercase; color: var(--accent, #60a5fa); margin-bottom: 18px; }
.article-title { font-size: 44px; line-height: 1.08; font-weight: 560; letter-spacing: -0.012em; color: #f5f8fc; max-width: 1100px; }
.article-lead { font-family: var(--font-display); font-style: italic; font-size: 21px; line-height: 1.5; color: #b6c2d2; margin-top: 18px; font-weight: 400; max-width: 860px; }
.article-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-top: 20px; font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; color: #5b6675; }
.article-meta .meta-sep { color: #2d3748; }
.article-meta .badge { letter-spacing: 0.06em; }
.article-tags { margin-top: 14px; display: flex; flex-wrap: wrap; gap: 6px; }

/* wide reading column with A's numbered section dividers */
.article-reading { min-width: 0; max-width: 1060px; font-size: 16.5px; counter-reset: sec; }
.article-reading h2 { counter-increment: sec; border-top: 1px solid #1c2433; border-bottom: none; padding: 36px 0 0; margin: 44px 0 16px; }
.article-reading h2::before { content: counter(sec, decimal-leading-zero); display: block; font-family: var(--font-mono); font-size: 12px; font-weight: 500; letter-spacing: 0.1em; color: var(--accent, #60a5fa); margin-bottom: 12px; }
.article-reading > h2:first-of-type { border-top: none; padding-top: 0; margin-top: 8px; }
.article-reading h2, .article-reading h3 { scroll-margin-top: 24px; }

/* contents (left) + evidence (right) rails */
.article-contents { position: sticky; top: 8px; max-height: calc(100vh - 70px); overflow-y: auto; }
.article-evidence { position: sticky; top: 8px; max-height: calc(100vh - 70px); overflow-y: auto; min-width: 0; }
.toc-title { font-size: 10px; text-transform: uppercase; letter-spacing: 0.16em; color: #475569; font-weight: 600; margin-bottom: 12px; }
.toc-link { display: block; font-size: 11.5px; line-height: 1.4; color: #66748a; padding: 5px 0 5px 14px; border-left: 1px solid #1f2937; text-decoration: none; transition: color 0.12s ease, border-color 0.12s ease; }
.toc-link:hover { color: #cbd5e1; text-decoration: none; border-left-color: #3a4658; }
.toc-link.toc-h3 { padding-left: 27px; font-size: 11px; color: #56627a; }
.toc-link.active { color: var(--accent, #93c5fd); border-left-color: var(--accent, #93c5fd); border-left-width: 2px; padding-left: 13px; }
.toc-link.toc-h3.active { padding-left: 26px; }
.rail-block { margin-bottom: 22px; }
.rail-block-title { font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; color: #475569; font-weight: 600; margin-bottom: 9px; }
.conn-count { color: #334155; }
.rail-list { list-style: none; padding: 0; margin: 0; }
.rail-list li { margin-bottom: 9px; line-height: 1.4; }
.rail-list a { font-size: 12px; }
.rail-qs li { font-size: 12px; color: #93a0b2; line-height: 1.45; }
.rail-source { font-size: 11px; color: #66748a; word-break: break-word; font-family: var(--font-mono); }

/* ── Graph view ───────────────────────────────────────────────────────── */
#view-graph.active { display: block; position: relative; height: calc(100vh - 48px); padding: 0; margin: -24px -32px; }
#view-graph #graph-svg { width: 100%; height: 100%; cursor: grab; display: block; background: #0a0d14; }
#view-graph #graph-svg:active { cursor: grabbing; }
#view-graph #graph-info { position: absolute; top: 14px; left: 14px; font-size: 12px; color: #475569; pointer-events: none; z-index: 5; }
#view-graph #graph-tooltip { position: absolute; bottom: 20px; left: 50%; transform: translateX(-50%); background: #1e2130; padding: 6px 14px; border-radius: 6px; font-size: 13px; pointer-events: none; opacity: 0; transition: opacity 0.15s; white-space: nowrap; z-index: 20; }
#view-graph #graph-panel-toggle { position: absolute; top: 10px; right: 10px; z-index: 30; background: #1e2130; border: 1px solid #334155; color: #94a3b8; width: 30px; height: 30px; border-radius: 6px; cursor: pointer; font-size: 15px; display: flex; align-items: center; justify-content: center; }
#view-graph #graph-panel-toggle:hover { color: #e2e8f0; border-color: #64748b; }
#view-graph #graph-panel { position: absolute; right: 0; top: 0; height: 100%; width: 240px; background: #0a0d14; border-left: 1px solid #1a2030; overflow-y: auto; padding: 52px 14px 20px; transform: translateX(0); transition: transform 0.2s ease; z-index: 25; }
#view-graph #graph-panel.hidden { transform: translateX(100%); }
#view-graph .gp-section { margin-bottom: 18px; }
#view-graph .gp-section-title { font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; color: #475569; margin-bottom: 8px; font-weight: 600; }
#view-graph .gp-chips { display: flex; flex-wrap: wrap; gap: 4px; }
#view-graph .gp-chip { background: #1e2130; border: 1px solid #334155; border-radius: 10px; padding: 3px 9px; font-size: 11px; cursor: pointer; color: #64748b; transition: all 0.15s; user-select: none; }
#view-graph .gp-chip:hover { color: #94a3b8; border-color: #475569; }
#view-graph .gp-chip.on { background: #172033; border-color: #3b82f6; color: #93c5fd; }
#view-graph .gp-toggle-row { display: flex; align-items: center; justify-content: space-between; font-size: 12px; color: #94a3b8; margin: 8px 0; }
#view-graph .gp-toggle { position: relative; width: 34px; height: 20px; flex-shrink: 0; }
#view-graph .gp-toggle input { opacity: 0; width: 0; height: 0; position: absolute; }
#view-graph .gp-toggle-track { position: absolute; inset: 0; background: #334155; border-radius: 10px; cursor: pointer; transition: background 0.2s; }
#view-graph .gp-toggle input:checked + .gp-toggle-track { background: #3b82f6; }
#view-graph .gp-toggle-track::before { content: ""; position: absolute; height: 14px; width: 14px; left: 3px; top: 3px; background: white; border-radius: 50%; transition: transform 0.2s; }
#view-graph .gp-toggle input:checked + .gp-toggle-track::before { transform: translateX(14px); }
#view-graph .gp-slider-row { display: flex; justify-content: space-between; font-size: 11px; color: #475569; margin-top: 10px; margin-bottom: 2px; }
#view-graph .gp-slider-val { color: #64748b; }
#view-graph #gp-depth-section { background: #0d1520; border: 1px solid #1d3357; border-radius: 6px; padding: 10px; margin-bottom: 18px; display: none; }
#view-graph input[type="range"] { width: 100%; accent-color: #60a5fa; cursor: pointer; margin: 3px 0; }
#view-graph .gp-divider { border: none; border-top: 1px solid #1a2030; margin: 14px 0; }
#view-graph #gp-search { width: 100%; background: #1e2130; border: 1px solid #2d3748; color: #e2e8f0; padding: 5px 8px; border-radius: 4px; font-size: 12px; outline: none; }
#view-graph #gp-search:focus { border-color: #60a5fa; }
#view-graph #gp-selected { display: none; background: #0d1520; border: 1px solid #1d3357; border-radius: 6px; padding: 10px; margin-bottom: 18px; }
#view-graph #gp-selected a { font-size: 12px; color: #93c5fd; }
"""


HTML_NAV_BUTTONS = [
    ("home", "Home"),
    ("positions", "Positions"),
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
  <div class="sb-brand"><span class="sb-mark"></span><span class="sb-brand-text">Wiki</span></div>
  <nav>
{buttons}
  </nav>
  <hr class="sb-divider">
  <div class="sb-section-title">Pages</div>
  <div id="sidebar-pages"></div>
</nav>"""


HTML_SCRIPT_UTIL = """
function escHtml(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
// Lightweight inline-markdown renderer for snippet fields (risk text, open
// questions, log details) that arrive as raw markdown. Block markdown is not
// handled — these are single-line fragments.
function inlineMd(s) {
  s = escHtml(s);
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\\*\\*\\*([^*]+)\\*\\*\\*/g, '<strong><em>$1</em></strong>');
  s = s.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*])\\*([^*\\n]+)\\*/g, '$1<em>$2</em>');
  s = s.replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return s;
}
function levelCell(v) {
  const k = (v || '').trim().toUpperCase();
  const cls = k === 'H' ? 'lvl-h' : k === 'M' ? 'lvl-m' : k === 'L' ? 'lvl-l' : '';
  return cls ? '<span class="lvl ' + cls + '">' + k + '</span>' : escHtml(v);
}

// Shared type-colour language — carried from the graph into every view so
// articles / positions / entities read as distinct kinds at a glance.
const TYPE_META = {
  article:  { color: '#fbbf24', label: 'Article' },
  position: { color: '#fb7185', label: 'Position' },
  entity:   { color: '#a78bfa', label: 'Entity' },
  concept:  { color: '#a78bfa', label: 'Concept' },
  meta:     { color: '#34d399', label: 'Meta' },
  primary:  { color: '#60a5fa', label: 'Page' },
};
function typeMeta(t) { return TYPE_META[t] || TYPE_META.primary; }
function typeDot(t) { return '<span class="tdot" style="background:' + typeMeta(t).color + '"></span>'; }
function typeTag(t) {
  const m = typeMeta(t);
  return '<span class="ttag" style="color:' + m.color + ';border-color:' + m.color + '55">' + m.label + '</span>';
}

// Consistent header for every view — title + count + one-line description.
function viewHeader(title, subtitle, count) {
  return '<header class="view-header">' +
    '<h2 class="view-title">' + escHtml(title) +
      (count != null ? ' <span class="view-count">' + count + '</span>' : '') + '</h2>' +
    (subtitle ? '<p class="view-sub">' + escHtml(subtitle) + '</p>' : '') +
  '</header>';
}
"""


HTML_SCRIPT_HOME = """
function daysAgo(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d)) return '';
  const days = Math.floor((Date.now() - d.getTime()) / (1000 * 60 * 60 * 24));
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return days + ' days ago';
  if (days < 365) return Math.floor(days / 30) + ' months ago';
  return Math.floor(days / 365) + ' years ago';
}

function isArticle(p) { const t = p.type || ''; return t !== 'entity' && t !== 'concept' && t !== 'position'; }
function statBox(n, label) { return '<div class="stat"><span class="n">' + n + '</span><span class="l">' + label + '</span></div>'; }
function provBadge(p) {
  const c = p.source ? 'prov-source' : 'prov-synth';
  return '<span class="badge ' + c + '">' + (p.source ? 'source' : 'synthesized') + '</span>';
}
function agoSpan(p) { const a = daysAgo(p.last_reviewed); return a ? '<span>updated ' + a + '</span>' : ''; }

function renderHome() {
  const root = document.getElementById('view-home');
  const pages = Object.values(WIKI_DATA.pages);
  const positions = pages.filter(p => p.type === 'position');
  const articles  = pages.filter(isArticle);
  const entities  = pages.filter(p => p.type === 'entity' || p.type === 'concept');
  const nQ = (WIKI_DATA.open_qs || []).length;
  const nR = (WIKI_DATA.risks || []).length;

  const html = [];
  html.push(
    '<div class="masthead">' +
      '<h1>Wiki</h1>' +
      '<div class="stats">' +
        statBox(articles.length, 'Pages') +
        statBox(positions.length, 'Positions') +
        statBox(entities.length, 'Entities') +
        statBox(nQ, 'Open questions') +
        statBox(nR, 'Open risks') +
      '</div>' +
    '</div>'
  );

  if (positions.length) {
    html.push('<div class="home-section-title">Positions</div>');
    positions.forEach(p => {
      html.push(
        '<div class="position-hero" data-page="' + p.path + '">' +
          '<div class="ph-title">' + escHtml(p.title) + '</div>' +
          (p.summary ? '<div class="ph-summary">' + escHtml(p.summary) + '</div>' : '') +
          '<div class="ph-meta">' + typeTag(p.type) +
            (p.status ? '<span class="badge">' + escHtml(p.status) + '</span>' : '') +
            provBadge(p) + agoSpan(p) +
          '</div>' +
        '</div>'
      );
    });
  }

  html.push('<div class="home-section-title">Pages</div>');
  const groups = {};
  articles.forEach(p => { const c = p.category || 'Uncategorized'; (groups[c] = groups[c] || []).push(p); });
  Object.keys(groups).sort().forEach(cat => {
    html.push('<div class="cat-label">' + escHtml(cat) + '</div>');
    html.push('<div class="card-grid">');
    groups[cat].sort((a, b) => a.title.localeCompare(b.title)).forEach(p => {
      html.push(
        '<div class="tcard" data-page="' + p.path + '">' +
          '<div class="tcard-title">' + typeDot(p.type) + '<span>' + escHtml(p.title) + '</span></div>' +
          (p.summary ? '<div class="tcard-summary">' + escHtml(p.summary) + '</div>' : '') +
          '<div class="tcard-meta">' + provBadge(p) + agoSpan(p) + '</div>' +
        '</div>'
      );
    });
    html.push('</div>');
  });

  root.innerHTML = html.join('');
  root.querySelectorAll('[data-page]').forEach(el => {
    el.addEventListener('click', () => window.openPage(el.dataset.page));
  });
}
"""


HTML_SCRIPT_SIDEBAR_PAGES = """
function buildSidebarPages() {
  const root = document.getElementById('sidebar-pages');
  if (!root) return;
  const pages = Object.values(WIKI_DATA.pages);
  if (pages.length === 0) {
    root.innerHTML = '<div class="muted" style="font-size:11px;padding:6px 8px">No pages yet.</div>';
    return;
  }
  const groups = {};
  pages.forEach(p => {
    const cat = p.category || 'Uncategorized';
    (groups[cat] = groups[cat] || []).push(p);
  });
  Object.values(groups).forEach(arr => arr.sort((a, b) => a.title.localeCompare(b.title)));
  const cats = Object.keys(groups).sort();
  let html = '';
  cats.forEach(cat => {
    html += '<div class="sb-cat">';
    html += '  <div class="sb-cat-header"><span class="sb-chev">▾</span> ' + cat + '</div>';
    html += '  <div class="sb-cat-body">';
    groups[cat].forEach(p => {
      html += '<button class="sb-page" data-page="' + p.path + '" title="' + p.title + '">' + p.title + '</button>';
    });
    html += '  </div>';
    html += '</div>';
  });
  root.innerHTML = html;

  root.querySelectorAll('.sb-cat-header').forEach(h => {
    h.addEventListener('click', () => {
      const cat = h.parentElement;
      cat.classList.toggle('collapsed');
      const chev = h.querySelector('.sb-chev');
      chev.textContent = cat.classList.contains('collapsed') ? '▸' : '▾';
    });
  });

  root.querySelectorAll('.sb-page').forEach(b => {
    b.addEventListener('click', () => window.openPage(b.dataset.page));
  });
}

function setSidebarActivePage(path) {
  const root = document.getElementById('sidebar-pages');
  if (!root) return;
  root.querySelectorAll('.sb-page').forEach(b => {
    const isActive = b.dataset.page === path;
    b.classList.toggle('active', isActive);
    if (isActive) {
      const cat = b.closest('.sb-cat');
      if (cat && cat.classList.contains('collapsed')) {
        cat.classList.remove('collapsed');
        const chev = cat.querySelector('.sb-chev');
        if (chev) chev.textContent = '▾';
      }
      b.scrollIntoView({ block: 'nearest' });
    }
  });
}

function clearSidebarActivePage() {
  const root = document.getElementById('sidebar-pages');
  if (!root) return;
  root.querySelectorAll('.sb-page.active').forEach(b => b.classList.remove('active'));
}
"""


HTML_SCRIPT_PAGE = """
function edgesFor(path) {
  const out = [], inc = [];
  WIKI_DATA.edges.forEach(([s, t]) => {
    if (s === path) out.push(t);
    if (t === path) inc.push(s);
  });
  return { out, inc };
}

function slugify(s) {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'section';
}

function renderPage(path) {
  const page = WIKI_DATA.pages[path];
  const root = document.getElementById('view-page');
  if (!page) { root.innerHTML = '<p class="muted">Page not found.</p>'; return; }
  const { out, inc } = edgesFor(path);
  const tm = typeMeta(page.type);

  const words = (page.rendered_html || '').replace(/<[^>]+>/g, ' ').split(/\\s+/).filter(Boolean).length;
  const readMin = Math.max(1, Math.round(words / 200));
  const meta = [];
  if (page.owner)         meta.push('owner ' + escHtml(page.owner));
  if (page.last_reviewed) meta.push('reviewed ' + escHtml(page.last_reviewed));
  meta.push(readMin + ' min read');
  const metaHtml = meta.join('<span class="meta-sep">·</span>');
  const provClass = page.source ? 'prov-source' : 'prov-synth';
  const provLabel = page.source ? 'source' : 'synthesized';
  const tags = (page.tags || []).map(t => '<span class="badge">#' + escHtml(t) + '</span>').join(' ');

  const railLinks = paths => paths.length
    ? '<ul class="rail-list">' + paths.map(p => '<li><a href="#" data-page="' + p + '">' + escHtml(WIKI_DATA.pages[p] ? WIKI_DATA.pages[p].title : p) + '</a></li>').join('') + '</ul>'
    : '';
  const railBlock = (label, inner, count) => inner
    ? '<div class="rail-block"><div class="rail-block-title">' + label + (count != null ? ' <span class="conn-count">' + count + '</span>' : '') + '</div>' + inner + '</div>'
    : '';
  const pageQs = (WIKI_DATA.open_qs || []).filter(q => q.page === path);
  const qsHtml = pageQs.length ? '<ul class="rail-list rail-qs">' + pageQs.map(q => '<li>' + inlineMd(q.question) + '</li>').join('') + '</ul>' : '';
  const evidence =
    railBlock('Source', page.source ? '<div class="rail-source">' + escHtml(page.source) + '</div>' : '') +
    railBlock('Open questions', qsHtml, pageQs.length) +
    railBlock('Grounded in', railLinks(out), out.length) +
    railBlock('Referenced by', railLinks(inc), inc.length);

  root.innerHTML =
    '<div class="article-shell" style="--accent:' + tm.color + '">' +
      '<header class="article-header">' +
        '<div class="kicker"><span class="tdot" style="background:' + tm.color + '"></span>' + tm.label + (page.status ? ' · ' + escHtml(page.status) : '') + '</div>' +
        '<h1 class="article-title">' + escHtml(page.title) + '</h1>' +
        (page.summary ? '<p class="article-lead">' + escHtml(page.summary) + '</p>' : '') +
        '<div class="article-meta">' + metaHtml + '<span class="meta-sep">·</span><span class="badge ' + provClass + '">' + provLabel + '</span></div>' +
        (tags ? '<div class="article-tags">' + tags + '</div>' : '') +
      '</header>' +
      '<div class="article-cols">' +
        '<nav class="toc article-contents" id="toc" aria-label="On this page"></nav>' +
        '<div class="markdown-body article-reading" id="article-body">' + (page.rendered_html || '') + '</div>' +
        '<aside class="article-evidence">' + evidence + '</aside>' +
      '</div>' +
    '</div>';

  root.querySelectorAll('a[data-page]').forEach(a => {
    a.addEventListener('click', e => { e.preventDefault(); window.openPage(a.dataset.page); });
  });
  buildToc();
}

function buildToc() {
  const body = document.getElementById('article-body');
  const toc  = document.getElementById('toc');
  const main = document.getElementById('main');
  if (!body || !toc || !main) return;

  const seen = {};
  const items = [...body.querySelectorAll('h2, h3')].map(h => {
    let id = slugify(h.textContent);
    if (seen[id] != null) { seen[id] += 1; id = id + '-' + seen[id]; } else { seen[id] = 0; }
    h.id = id;
    return { id, text: h.textContent, level: h.tagName === 'H3' ? 3 : 2 };
  });

  if (items.length < 2) {
    toc.style.display = 'none';
  } else {
    toc.style.display = '';
    toc.innerHTML = '<div class="toc-title">On this page</div>' +
      items.map(it => '<a href="#' + it.id + '" class="toc-link' + (it.level === 3 ? ' toc-h3' : '') +
        '" data-id="' + it.id + '">' + escHtml(it.text) + '</a>').join('');
  }

  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const links = [...toc.querySelectorAll('a')];
  links.forEach(a => a.addEventListener('click', e => {
    e.preventDefault();
    const el = document.getElementById(a.dataset.id);
    if (el) el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
  }));

  function onScroll() {
    if (!items.length) return;
    const mt = main.getBoundingClientRect().top;
    let activeId = items[0].id;
    for (const it of items) {
      const el = document.getElementById(it.id);
      if (el && el.getBoundingClientRect().top - mt < 120) activeId = it.id; else break;
    }
    links.forEach(l => {
      const on = l.dataset.id === activeId;
      l.classList.toggle('active', on);
      if (on) l.setAttribute('aria-current', 'true'); else l.removeAttribute('aria-current');
    });
  }
  main.scrollTop = 0;
  main.onscroll = onScroll;
  requestAnimationFrame(onScroll);  // wait until the view is visible so heading rects are real
}

window.openPage = function(path) {
  renderPage(path);
  showView('page');
  if (window.setSidebarActivePage) window.setSidebarActivePage(path);
};
"""


HTML_SCRIPT_SEARCH = """
let _searchIndex = null;
function ensureSearchIndex() {
  if (_searchIndex) return _searchIndex;
  _searchIndex = new MiniSearch({
    fields: ['title', 'body', 'tags', 'category'],
    storeFields: ['title', 'category'],
    searchOptions: { boost: { title: 2 }, fuzzy: 0.2, prefix: true }
  });
  _searchIndex.addAll(WIKI_DATA.search);
  return _searchIndex;
}

function renderSearch() {
  const root = document.getElementById('view-search');
  if (root.dataset.built) return;
  root.dataset.built = '1';
  root.innerHTML =
    viewHeader('Search', 'Full-text across titles, bodies, tags and categories.') +
    '<input id="search-input" type="text" placeholder="Search title, body, tags, category">' +
    '<div id="search-results" style="margin-top:14px"></div>';
  const input = document.getElementById('search-input');
  const results = document.getElementById('search-results');
  input.addEventListener('input', () => {
    const q = input.value.trim();
    if (!q) { results.innerHTML = ''; return; }
    const idx = ensureSearchIndex();
    const hits = idx.search(q).slice(0, 30);
    if (hits.length === 0) { results.innerHTML = '<p class="muted">No matches.</p>'; return; }
    results.innerHTML = hits.map(h => {
      const page = WIKI_DATA.pages[h.id];
      const provClass = page && page.source ? 'prov-source' : 'prov-synth';
      const provLabel = page && page.source ? 'source' : 'synthesized';
      return '<div class="card" style="cursor:pointer" data-page="' + h.id + '">' +
        '<div class="tcard-title">' + typeDot(page ? page.type : '') +
          '<span>' + escHtml(page?.title || h.title) + '</span> ' +
          '<span class="badge ' + provClass + '">' + provLabel + '</span></div>' +
        (page && page.summary ? '<div class="search-result-summary">' + escHtml(page.summary) + '</div>' : '') +
        (h.category ? '<div class="muted" style="margin-top:4px">' + escHtml(h.category) + '</div>' : '') +
      '</div>';
    }).join('');
    results.querySelectorAll('.card[data-page]').forEach(c => {
      c.addEventListener('click', () => window.openPage(c.dataset.page));
    });
  });
}
"""


HTML_SCRIPT_GRAPH = """
let _graphBuilt = false;
function renderGraph() {
  const root = document.getElementById('view-graph');
  if (!_graphBuilt) {
    const pageCount = Object.keys(WIKI_DATA.pages).length;
    const edgeCount = WIKI_DATA.edges.length;
    const inboundInit = {};
    Object.keys(WIKI_DATA.pages).forEach(p => { inboundInit[p] = 0; });
    WIKI_DATA.edges.forEach(([s, t]) => { inboundInit[t] = (inboundInit[t] || 0) + 1; });
    const orphanCount = Object.keys(WIKI_DATA.pages).filter(p => inboundInit[p] === 0).length;
    root.innerHTML =
      '<div id="graph-info">' + pageCount + ' pages · ' + edgeCount + ' links · ' + orphanCount + ' orphan' + (orphanCount === 1 ? '' : 's') + '</div>' +
      '<div id="graph-tooltip"></div>' +
      '<button id="graph-panel-toggle" title="Toggle panel">⚙</button>' +
      '<div id="graph-panel">' +
        '<div class="gp-section"><div class="gp-section-title">Search</div><input type="text" id="gp-search" placeholder="Filter nodes…"></div>' +
        '<div class="gp-section"><div class="gp-section-title">Types</div><div class="gp-chips" id="gp-type-chips"></div></div>' +
        '<div class="gp-section" id="gp-tag-section" style="display:none"><div class="gp-section-title">Tags</div><div class="gp-chips" id="gp-tag-chips"></div></div>' +
        '<hr class="gp-divider">' +
        '<div id="gp-selected"><div class="gp-section-title">Selected</div><div id="gp-selected-body"></div></div>' +
        '<div id="gp-depth-section"><div class="gp-section-title">Selection depth</div><div class="gp-slider-row"><span>Hops</span><span class="gp-slider-val" id="gp-depth-val">1</span></div><input type="range" id="gp-depth-slider" min="1" max="4" value="1"></div>' +
        '<div class="gp-section"><div class="gp-section-title">Display</div>' +
          '<div class="gp-toggle-row">All labels <label class="gp-toggle"><input type="checkbox" id="gp-toggle-labels"><span class="gp-toggle-track"></span></label></div>' +
          '<div class="gp-toggle-row">Arrows <label class="gp-toggle"><input type="checkbox" id="gp-toggle-arrows" checked><span class="gp-toggle-track"></span></label></div>' +
        '</div>' +
        '<div class="gp-section"><div class="gp-section-title">Forces</div>' +
          '<div class="gp-slider-row"><span>Link distance</span><span class="gp-slider-val" id="gp-dist-val">120</span></div>' +
          '<input type="range" id="gp-link-dist" min="50" max="300" value="120">' +
          '<div class="gp-slider-row"><span>Repulsion</span><span class="gp-slider-val" id="gp-charge-val">450</span></div>' +
          '<input type="range" id="gp-charge-str" min="50" max="800" value="450">' +
        '</div>' +
        '<div class="gp-section"><div class="gp-section-title">Legend</div>' +
          '<div style="display:flex;flex-direction:column;gap:7px">' +
            '<div style="display:flex;align-items:center;gap:8px;font-size:11px;color:#64748b"><span style="width:11px;height:11px;border-radius:50%;background:#fbbf24;flex-shrink:0;display:inline-block"></span> Article</div>' +
            '<div style="display:flex;align-items:center;gap:8px;font-size:11px;color:#64748b"><span style="width:11px;height:11px;border-radius:50%;background:#fb7185;flex-shrink:0;display:inline-block"></span> Position</div>' +
            '<div style="display:flex;align-items:center;gap:8px;font-size:11px;color:#64748b"><span style="width:10px;height:10px;border-radius:50%;background:#a78bfa;flex-shrink:0;display:inline-block"></span> Entity / concept</div>' +
            '<div style="display:flex;align-items:center;gap:8px;font-size:11px;color:#64748b"><span style="width:10px;height:10px;border-radius:50%;background:#34d399;flex-shrink:0;display:inline-block"></span> Meta</div>' +
            '<div style="display:flex;align-items:center;gap:8px;font-size:11px;color:#64748b"><span style="width:10px;height:10px;border-radius:50%;background:transparent;border:1.5px dashed #f87171;flex-shrink:0;display:inline-block"></span> Orphan</div>' +
          '</div>' +
        '</div>' +
      '</div>' +
      '<svg id="graph-svg">' +
        '<defs>' +
          '<marker id="arrow" viewBox="0 -4 8 8" refX="8" refY="0" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,-4L8,0L0,4" fill="#3d4f66"/></marker>' +
          '<marker id="arrow-hi" viewBox="0 -4 8 8" refX="8" refY="0" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,-4L8,0L0,4" fill="#94a3b8"/></marker>' +
        '</defs>' +
      '</svg>';
    _graphBuilt = true;
    initGraph();
  }
}

function initGraph() {
  const pageList = Object.keys(WIKI_DATA.pages).sort();
  if (pageList.length === 0) return;
  const idMap = Object.fromEntries(pageList.map((p, i) => [p, i]));
  const inbound = Object.fromEntries(pageList.map(p => [p, 0]));
  WIKI_DATA.edges.forEach(([s, t]) => { inbound[t] = (inbound[t] || 0) + 1; });
  const nodes = pageList.map(p => ({
    id: idMap[p],
    label: WIKI_DATA.pages[p].title,
    type: WIKI_DATA.pages[p].type || 'primary',
    file: p,
    tags: WIKI_DATA.pages[p].tags || [],
    orphan: inbound[p] === 0,
  }));
  const links = WIKI_DATA.edges.map(([s, t]) => ({ source: idMap[s], target: idMap[t] }));

  const deg = new Array(nodes.length).fill(0);
  links.forEach(l => { deg[l.source]++; deg[l.target]++; });
  // calm default: label only the ~15 highest-degree hubs until "All labels" is on
  const _sortedDeg = [...deg].sort((a, b) => b - a);
  const LABEL_DEG = _sortedDeg[Math.min(14, _sortedDeg.length - 1)] || 0;
  const autoLabel = d => deg[d.id] >= LABEL_DEG;

  function nodeRadius(d) {
    const base = d.type === 'primary' ? 12 : 9;
    return base + Math.sqrt(deg[d.id]) * 2.5;
  }

  // Multi-primary-type wikis can declare custom `type:` values (e.g. article,
  // policy, control). A small palette covers the common cases; anything else
  // falls through to the neutral grey via `colour[d.type] || '#94a3b8'`.
  const colour = { 'primary': '#60a5fa', entity: '#a78bfa', concept: '#a78bfa', meta: '#34d399', article: '#fbbf24', position: '#fb7185', policy: '#f472b6', control: '#22d3ee' };

  const svgEl = d3.select('#graph-svg');
  const canvas = svgEl.append('g');

  const zoom = d3.zoom().scaleExtent([0.05, 5]).on('zoom', e => canvas.attr('transform', e.transform));
  svgEl.call(zoom);

  const PANEL_W = 240;
  const bbox = svgEl.node().getBoundingClientRect();
  const cx = (bbox.width - PANEL_W) / 2;
  const cy = bbox.height / 2;

  const sim = d3.forceSimulation(nodes)
    .force('link',      d3.forceLink(links).id(d => d.id).distance(120))
    .force('charge',    d3.forceManyBody().strength(-450))
    .force('center',    d3.forceCenter(cx, cy).strength(0.3))
    .force('x',         d3.forceX(cx).strength(0.06))
    .force('y',         d3.forceY(cy).strength(0.06))
    .force('collision', d3.forceCollide().radius(d => nodeRadius(d) + 10));

  const linkEl = canvas.append('g').selectAll('line').data(links).join('line')
    .attr('stroke', '#334155').attr('stroke-width', 1.5).attr('stroke-opacity', 0.6)
    .attr('marker-end', 'url(#arrow)');

  const nodeEl = canvas.append('g').selectAll('circle').data(nodes).join('circle')
    .attr('r', nodeRadius)
    .attr('fill', d => d.orphan ? 'transparent' : colour[d.type] || '#94a3b8')
    .attr('stroke', d => d.orphan ? '#f87171' : colour[d.type] || '#94a3b8')
    .attr('stroke-width', d => d.orphan ? 2 : 0)
    .attr('stroke-dasharray', d => d.orphan ? '4,2' : null)
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end',   (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

  const labelEl = canvas.append('g').selectAll('text').data(nodes).join('text')
    .text(d => d.label.length > 35 ? d.label.slice(0, 33) + '…' : d.label)
    .attr('font-size', 11).attr('fill', '#cbd5e1').attr('text-anchor', 'middle')
    .attr('dy', d => nodeRadius(d) + 14)
    .style('pointer-events', 'none');

  let selected    = null;
  let depth       = 1;
  let showLabels  = false;  // false = auto (hubs only); toggle shows every label
  let showArrows  = true;
  let activeTypes = new Set(nodes.map(n => n.type));
  let activeTags  = new Set();
  let searchQuery = '';

  function isVisible(n) {
    if (!activeTypes.has(n.type)) return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      if (!n.label.toLowerCase().includes(q) && !n.file.toLowerCase().includes(q)) return false;
    }
    if (activeTags.size > 0 && !n.tags.some(t => activeTags.has(t))) return false;
    return true;
  }

  function getNeighbours(startId, hops) {
    const visited = new Set([startId]);
    let frontier = [startId];
    for (let i = 0; i < hops; i++) {
      const next = [];
      frontier.forEach(id => {
        links.forEach(l => {
          const s = l.source.id, t = l.target.id;
          if (s === id && !visited.has(t)) { visited.add(t); next.push(t); }
          if (t === id && !visited.has(s)) { visited.add(s); next.push(s); }
        });
      });
      frontier = next;
    }
    return visited;
  }

  function applyFilters() {
    nodeEl .attr('opacity',       n => isVisible(n) ? 1 : 0)
           .style('pointer-events', n => isVisible(n) ? null : 'none');
    labelEl.attr('opacity',       n => isVisible(n) && (showLabels || autoLabel(n)) ? 1 : 0);
    linkEl .attr('stroke-opacity', l => isVisible(l.source) && isVisible(l.target) ? 0.6 : 0)
           .attr('stroke', '#334155').attr('stroke-width', 1.5)
           .attr('marker-end', showArrows ? 'url(#arrow)' : null);
  }

  function highlight(d) {
    selected = d;
    const neighbours = getNeighbours(d.id, depth);

    linkEl
      .attr('stroke', l => {
        if (l.source.id === d.id || l.target.id === d.id) return '#e2e8f0';
        if (neighbours.has(l.source.id) && neighbours.has(l.target.id)) return '#64748b';
        return '#1a2030';
      })
      .attr('stroke-width', l => (l.source.id === d.id || l.target.id === d.id) ? 2.5 : 1)
      .attr('stroke-opacity', l => {
        if (!isVisible(l.source) || !isVisible(l.target)) return 0;
        if (l.source.id === d.id || l.target.id === d.id) return 0.9;
        if (neighbours.has(l.source.id) && neighbours.has(l.target.id)) return 0.4;
        return 0.04;
      })
      .attr('marker-end', l => {
        if (!showArrows) return null;
        return (l.source.id === d.id || l.target.id === d.id) ? 'url(#arrow-hi)' : 'url(#arrow)';
      });

    nodeEl .attr('opacity', n => { if (!isVisible(n)) return 0; return neighbours.has(n.id) ? 1 : 0.1; });
    labelEl.attr('opacity', n => {
      if (!isVisible(n)) return 0;
      if (neighbours.has(n.id)) return 1;            // always label the selected neighbourhood
      return (showLabels || autoLabel(n)) ? 0.06 : 0;
    });

    document.getElementById('gp-depth-section').style.display = 'block';
    const sel = document.getElementById('gp-selected');
    sel.style.display = 'block';
    document.getElementById('gp-selected-body').innerHTML =
      '<div style="font-size:12px;color:#cbd5e1;margin-bottom:6px">' + d.label + '</div>' +
      '<a href="#" id="gp-open-page">Open page →</a>';
    document.getElementById('gp-open-page').addEventListener('click', e => {
      e.preventDefault();
      if (window.openPage) window.openPage(d.file);
    });
  }

  function reset() {
    selected = null;
    document.getElementById('gp-depth-section').style.display = 'none';
    document.getElementById('gp-selected').style.display = 'none';
    applyFilters();
  }

  nodeEl.on('click', (e, d) => {
    e.stopPropagation();
    selected && selected.id === d.id ? reset() : highlight(d);
  });

  let wasPanning = false;
  zoom.on('start.track', () => { wasPanning = false; })
      .on('zoom.track',  () => { wasPanning = true;  });
  svgEl.on('click', () => { if (!wasPanning) reset(); });

  const tip = document.getElementById('graph-tooltip');
  nodeEl.on('mouseover', (e, d) => { tip.textContent = d.file; tip.style.opacity = 1; })
        .on('mouseout',  ()     => { tip.style.opacity = 0; });

  sim.on('tick', () => {
    linkEl
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => {
        const r = nodeRadius(d.target) + (showArrows ? 7 : 0);
        const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        return d.target.x - (dx / dist) * r;
      })
      .attr('y2', d => {
        const r = nodeRadius(d.target) + (showArrows ? 7 : 0);
        const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        return d.target.y - (dy / dist) * r;
      });
    nodeEl .attr('cx', d => d.x).attr('cy', d => d.y);
    labelEl.attr('x',  d => d.x).attr('y',  d => d.y);
  });

  const allTypes = [...new Set(nodes.map(n => n.type))];
  const typeChipsCt = document.getElementById('gp-type-chips');
  allTypes.forEach(t => {
    const chip = document.createElement('div');
    chip.className = 'gp-chip on';
    chip.textContent = t;
    chip.style.borderColor = colour[t] || '#334155';
    chip.addEventListener('click', () => {
      chip.classList.toggle('on');
      activeTypes[chip.classList.contains('on') ? 'add' : 'delete'](t);
      selected ? highlight(selected) : applyFilters();
    });
    typeChipsCt.appendChild(chip);
  });

  const allTags = [...new Set(nodes.flatMap(n => n.tags))].sort();
  if (allTags.length) {
    document.getElementById('gp-tag-section').style.display = 'block';
    const tagChipsCt = document.getElementById('gp-tag-chips');
    allTags.forEach(t => {
      const chip = document.createElement('div');
      chip.className = 'gp-chip';
      chip.textContent = t;
      chip.addEventListener('click', () => {
        chip.classList.toggle('on');
        activeTags[chip.classList.contains('on') ? 'add' : 'delete'](t);
        selected ? highlight(selected) : applyFilters();
      });
      tagChipsCt.appendChild(chip);
    });
  }

  document.getElementById('gp-search').addEventListener('input', e => {
    searchQuery = e.target.value;
    selected ? highlight(selected) : applyFilters();
  });

  document.getElementById('gp-toggle-labels').addEventListener('change', e => {
    showLabels = e.target.checked;
    selected ? highlight(selected) : applyFilters();
  });

  document.getElementById('gp-toggle-arrows').addEventListener('change', e => {
    showArrows = e.target.checked;
    linkEl.attr('marker-end', showArrows ? 'url(#arrow)' : null);
    sim.on('tick')();
  });

  document.getElementById('gp-depth-slider').addEventListener('input', e => {
    depth = +e.target.value;
    document.getElementById('gp-depth-val').textContent = depth;
    if (selected) highlight(selected);
  });

  document.getElementById('gp-link-dist').addEventListener('input', e => {
    document.getElementById('gp-dist-val').textContent = e.target.value;
    sim.force('link').distance(+e.target.value);
    sim.alpha(0.3).restart();
  });

  document.getElementById('gp-charge-str').addEventListener('input', e => {
    document.getElementById('gp-charge-val').textContent = e.target.value;
    sim.force('charge').strength(-e.target.value);
    sim.alpha(0.3).restart();
  });

  document.getElementById('graph-panel-toggle').addEventListener('click', () => {
    document.getElementById('graph-panel').classList.toggle('hidden');
  });

  applyFilters();  // apply the calm-label default (hubs only) on first paint
}
"""


HTML_SCRIPT_RISKS = """
function renderRisks() {
  const root = document.getElementById('view-risks');
  const risks = WIKI_DATA.risks || [];
  const rows = risks.map(r =>
    '<tr>' +
      '<td class="cell-page">' + typeDot((WIKI_DATA.pages[r.page] || {}).type) + '<a href="#" data-page="' + r.page + '">' + escHtml(r.page_title) + '</a></td>' +
      '<td>' + inlineMd(r.risk) + '</td>' +
      '<td>' + levelCell(r.likelihood) + '</td>' +
      '<td>' + levelCell(r.impact) + '</td>' +
      '<td style="white-space:nowrap">' + escHtml(r.status) + '</td>' +
    '</tr>'
  ).join('');
  root.innerHTML =
    viewHeader('Open risks', 'Open risk-register rows across every page, with likelihood and impact.', risks.length) +
    (risks.length === 0
      ? '<p class="muted">No open risks.</p>'
      : '<table><thead><tr><th>Page</th><th>Risk</th><th>Likelihood</th><th>Impact</th><th>Status</th></tr></thead><tbody>' + rows + '</tbody></table>');
  root.querySelectorAll('a[data-page]').forEach(a => {
    a.addEventListener('click', e => { e.preventDefault(); window.openPage(a.dataset.page); });
  });
}
"""


HTML_SCRIPT_RECENT = """
function renderRecent() {
  const root = document.getElementById('view-recent');
  const log = WIKI_DATA.log || [];
  const rows = log.map(e =>
    '<tr>' +
      '<td class="muted" style="white-space:nowrap;vertical-align:top">' + e.date + '</td>' +
      '<td style="vertical-align:top"><span class="badge">' + e.action + '</span></td>' +
      '<td><div class="recent-detail">' + inlineMd(e.detail) + '</div></td>' +
    '</tr>'
  ).join('');
  root.innerHTML =
    viewHeader('Recent changes', 'Every ingest and edit, newest first.') +
    (log.length === 0
      ? '<p class="muted">No log entries.</p>'
      : '<table><tbody>' + rows + '</tbody></table>');
  root.querySelectorAll('.recent-detail').forEach(d => {
    if (d.scrollHeight <= d.clientHeight + 4) return;  // fits in the clamp, no toggle needed
    d.classList.add('expandable');
    const more = document.createElement('div');
    more.className = 'recent-detail-more';
    more.textContent = 'Show more ▾';
    const toggle = () => {
      const open = d.classList.toggle('expanded');
      more.textContent = open ? 'Show less ▴' : 'Show more ▾';
    };
    d.addEventListener('click', toggle);
    more.addEventListener('click', toggle);
    d.parentNode.appendChild(more);
  });
}
"""


HTML_SCRIPT_OPEN_QS = """
function renderOpenQs() {
  const root = document.getElementById('view-open-qs');
  const qs = WIKI_DATA.open_qs || [];
  const rows = qs.map(q =>
    '<div class="tcard oq-card" data-page="' + q.page + '">' +
      '<div class="oq-q">' + inlineMd(q.question) + '</div>' +
      '<div class="oq-src">' + typeDot((WIKI_DATA.pages[q.page] || {}).type) + '<span>' + escHtml(q.page_title) + '</span></div>' +
    '</div>'
  ).join('');
  root.innerHTML =
    viewHeader('Open questions', 'Unresolved threads flagged across the wiki.', qs.length) +
    (qs.length === 0 ? '<p class="muted">No open questions.</p>' : '<div class="oq-list">' + rows + '</div>');
  root.querySelectorAll('[data-page]').forEach(el => {
    el.addEventListener('click', () => window.openPage(el.dataset.page));
  });
}
"""


HTML_SCRIPT_ENTITIES = """
function renderEntities() {
  const root = document.getElementById('view-entities');
  const entities = Object.values(WIKI_DATA.pages).filter(p => p.type === 'entity' || p.type === 'concept');
  const inbound = {};
  WIKI_DATA.edges.forEach(([s, t]) => { inbound[t] = (inbound[t] || 0) + 1; });
  entities.sort((a, b) => (inbound[b.path] || 0) - (inbound[a.path] || 0));
  const rows = entities.map(e =>
    '<tr>' +
      '<td><a href="#" data-page="' + e.path + '">' + escHtml(e.title) + '</a>' +
        (e.summary ? '<div class="entity-gloss">' + escHtml(e.summary) + '</div>' : '') + '</td>' +
      '<td style="white-space:nowrap"><span class="tdot" style="background:' + typeMeta(e.type).color + ';margin-right:6px"></span>' + (e.type || 'entity') + '</td>' +
      '<td class="muted" style="white-space:nowrap">' + (inbound[e.path] || 0) + ' mentions</td>' +
    '</tr>'
  ).join('');
  root.innerHTML =
    viewHeader('Entities', 'Tools, vendors, roles and concepts — ranked by how often they are referenced.', entities.length) +
    (entities.length === 0
      ? '<p class="muted">No entity pages yet.</p>'
      : '<table><tbody>' + rows + '</tbody></table>');
  root.querySelectorAll('a[data-page]').forEach(a => {
    a.addEventListener('click', e => { e.preventDefault(); window.openPage(a.dataset.page); });
  });
}
"""


HTML_SCRIPT_POSITIONS = """
function renderPositions() {
  const root = document.getElementById('view-positions');
  const positions = Object.values(WIKI_DATA.pages).filter(p => p.type === 'position');
  if (positions.length === 0) {
    root.innerHTML = viewHeader('Positions', 'Stances or decisions taken, each grounded in the underlying pages.') + '<p class="muted">No positions yet — add a page with <code>type: position</code> to record a stance.</p>';
    return;
  }
  const cards = positions.map(p => {
    const out = [];
    WIKI_DATA.edges.forEach(([s, t]) => { if (s === p.path && WIKI_DATA.pages[t]) out.push(t); });
    const cites = out.length
      ? '<div class="cites-row"><span class="cites-label">Grounded in:</span> ' +
        out.map(t => '<a href="#" data-page="' + t + '">' + escHtml(WIKI_DATA.pages[t].title) + '</a>')
           .join('<span class="cites-label"> · </span>') + '</div>'
      : '';
    return '<div class="position-hero" data-page="' + p.path + '" style="margin-bottom:16px">' +
        '<div class="ph-title">' + escHtml(p.title) + '</div>' +
        (p.summary ? '<div class="ph-summary">' + escHtml(p.summary) + '</div>' : '') +
        '<div class="ph-meta">' + typeTag(p.type) +
          (p.status ? '<span class="badge">' + escHtml(p.status) + '</span>' : '') +
          provBadge(p) + agoSpan(p) + '</div>' +
        cites +
      '</div>';
  }).join('');
  root.innerHTML = viewHeader('Positions', 'Stances or decisions taken, each grounded in the underlying pages.', positions.length) + cards;
  root.querySelectorAll('a[data-page]').forEach(a => {
    a.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); window.openPage(a.dataset.page); });
  });
  root.querySelectorAll('.position-hero[data-page]').forEach(c => {
    c.addEventListener('click', () => window.openPage(c.dataset.page));
  });
}
"""


HTML_SCRIPT_VIEW_SWITCH = """
const buttons = document.querySelectorAll('#sidebar > nav > button');
const views = document.querySelectorAll('.view');
function showView(name) {
  views.forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
  buttons.forEach(b => b.classList.toggle('active', b.dataset.view === name));
  if (name !== 'page' && window.clearSidebarActivePage) window.clearSidebarActivePage();
  if (name === 'positions' && window.renderPositions) window.renderPositions();
  if (name === 'search' && window.renderSearch) window.renderSearch();
  if (name === 'graph'  && window.renderGraph)  window.renderGraph();
  if (name === 'risks'  && window.renderRisks)  window.renderRisks();
  if (name === 'recent' && window.renderRecent) window.renderRecent();
  if (name === 'open-qs' && window.renderOpenQs) window.renderOpenQs();
  if (name === 'entities' && window.renderEntities) window.renderEntities();
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
                "summary": p["fm"].get("description") or "",
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

    view_ids = ["home", "positions", "page", "search", "graph", "risks", "recent", "open-qs", "entities"]
    view_divs = "\n".join(f'    <section class="view" id="view-{vid}"></section>' for vid in view_ids)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wiki</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;1,6..72,400;1,6..72,500&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/minisearch@6/dist/umd/index.min.js"></script>
<script src="https://d3js.org/d3.v7.min.js"></script>
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
{HTML_SCRIPT_UTIL}
{HTML_SCRIPT_HOME}
{HTML_SCRIPT_PAGE}
{HTML_SCRIPT_SEARCH}
{HTML_SCRIPT_GRAPH}
{HTML_SCRIPT_RISKS}
{HTML_SCRIPT_RECENT}
{HTML_SCRIPT_OPEN_QS}
{HTML_SCRIPT_ENTITIES}
{HTML_SCRIPT_POSITIONS}
{HTML_SCRIPT_SIDEBAR_PAGES}
{HTML_SCRIPT_VIEW_SWITCH}
buildSidebarPages();
renderHome();
</script>
</body>
</html>
"""


def run(wiki_root: Path, output_path: Path) -> None:
    pages = collect_pages(wiki_root)
    edges = collect_edges(pages)
    log = collect_log(wiki_root)
    risks = extract_risks(pages)
    open_qs = extract_open_qs(pages)
    search_docs = build_search_index(pages)
    html = render_html(pages, edges, log, risks, open_qs, search_docs)
    output_path.write_text(html, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(WIKI_ROOT / "wiki.html"))
    args = parser.parse_args()
    run(WIKI_ROOT, Path(args.output))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
