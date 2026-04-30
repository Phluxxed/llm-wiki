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


HTML_SCRIPT_HOME = """
function summaryFor(page) {
  const html = page.rendered_html || '';
  const m = html.match(/<p>([^<]{1,180})/);
  return m ? m[1] : '';
}

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

function renderHome() {
  const root = document.getElementById('view-home');
  const pages = Object.values(WIKI_DATA.pages);
  const groups = {};
  pages.forEach(p => {
    const cat = p.category || 'Uncategorized';
    (groups[cat] = groups[cat] || []).push(p);
  });
  const sorted = Object.keys(groups).sort();
  const html = ['<h2>Pages</h2>'];
  if (pages.length === 0) html.push('<p class="muted">No pages yet.</p>');
  sorted.forEach(cat => {
    html.push('<h3 style="margin-top:18px;font-size:13px;color:#64748b;text-transform:uppercase;letter-spacing:0.05em;">' + cat + '</h3>');
    groups[cat].forEach(p => {
      html.push('<div class="card" style="cursor:pointer" data-page="' + p.path + '">');
      html.push('  <div><strong>' + p.title + '</strong> ');
      if (p.status) html.push('<span class="badge">' + p.status + '</span>');
      html.push('</div>');
      const summary = summaryFor(p);
      if (summary) html.push('  <div class="muted" style="margin-top:4px">' + summary + '</div>');
      const ago = daysAgo(p.last_reviewed);
      if (ago) html.push('  <div class="muted" style="margin-top:4px">updated ' + ago + '</div>');
      html.push('</div>');
    });
  });
  root.innerHTML = html.join('\\n');
  root.querySelectorAll('.card[data-page]').forEach(card => {
    card.addEventListener('click', () => { window.openPage(card.dataset.page); });
  });
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

function renderPage(path) {
  const page = WIKI_DATA.pages[path];
  const root = document.getElementById('view-page');
  if (!page) { root.innerHTML = '<p class="muted">Page not found.</p>'; return; }
  const { out, inc } = edgesFor(path);
  const meta = [];
  if (page.status)        meta.push('<span class="badge">' + page.status + '</span>');
  if (page.owner)         meta.push('<span class="muted">owner: ' + page.owner + '</span>');
  if (page.last_reviewed) meta.push('<span class="muted">reviewed ' + page.last_reviewed + '</span>');
  const tags = (page.tags || []).map(t => '<span class="badge">#' + t + '</span>').join(' ');
  const linkList = (paths, title) => {
    if (!paths.length) return '';
    const items = paths.map(p => '<li><a href="#" data-page="' + p + '">' + (WIKI_DATA.pages[p] ? WIKI_DATA.pages[p].title : p) + '</a></li>').join('');
    return '<div style="margin-top:18px"><div class="muted" style="margin-bottom:6px">' + title + '</div><ul style="list-style:none;padding:0">' + items + '</ul></div>';
  };
  root.innerHTML =
    '<div style="display:grid;grid-template-columns:1fr 220px;gap:32px">' +
      '<div>' +
        '<h2>' + page.title + '</h2>' +
        '<div style="margin-bottom:12px">' + meta.join(' ') + '</div>' +
        (tags ? '<div style="margin-bottom:18px">' + tags + '</div>' : '') +
        '<div class="markdown-body">' + (page.rendered_html || '') + '</div>' +
      '</div>' +
      '<aside>' +
        linkList(out, 'Mentions') +
        linkList(inc, 'Mentioned by') +
        (page.source ? '<div style="margin-top:18px"><div class="muted" style="margin-bottom:6px">Source</div><div class="muted">' + page.source + '</div></div>' : '') +
      '</aside>' +
    '</div>';
  root.querySelectorAll('a[data-page]').forEach(a => {
    a.addEventListener('click', e => { e.preventDefault(); window.openPage(a.dataset.page); });
  });
}

window.openPage = function(path) { renderPage(path); showView('page'); };
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
    '<h2>Search</h2>' +
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
    results.innerHTML = hits.map(h =>
      '<div class="card" style="cursor:pointer" data-page="' + h.id + '">' +
        '<strong>' + (WIKI_DATA.pages[h.id]?.title || h.title) + '</strong>' +
        ' <span class="muted">' + (h.category || '') + '</span>' +
      '</div>'
    ).join('');
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
    root.innerHTML = '<h2>Graph</h2><svg id="graph-svg" width="100%" height="600" style="background:#0a0d14;border-radius:6px"></svg>';
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
    type: WIKI_DATA.pages[p].type || 'use-case',
    file: p,
    tags: WIKI_DATA.pages[p].tags || [],
    orphan: inbound[p] === 0,
  }));
  const links = WIKI_DATA.edges.map(([s, t]) => ({ source: idMap[s], target: idMap[t] }));

  const svg = d3.select('#graph-svg');
  const width = svg.node().getBoundingClientRect().width;
  const height = 600;
  const cx = width / 2, cy = height / 2;
  const colorByType = { 'use-case': '#60a5fa', entity: '#34d399', concept: '#a78bfa', meta: '#f59e0b' };

  const sim = d3.forceSimulation(nodes)
    .force('link',      d3.forceLink(links).id(d => d.id).distance(120))
    .force('charge',    d3.forceManyBody().strength(-450))
    .force('center',    d3.forceCenter(cx, cy).strength(0.3))
    .force('x',         d3.forceX(cx).strength(0.06))
    .force('y',         d3.forceY(cy).strength(0.06))
    .force('collision', d3.forceCollide().radius(20));

  const link = svg.append('g').attr('stroke', '#334155').attr('stroke-opacity', 0.6).selectAll('line')
    .data(links).join('line').attr('stroke-width', 1);

  const node = svg.append('g').selectAll('g').data(nodes).join('g')
    .style('cursor', 'pointer')
    .on('click', (e, d) => window.openPage(d.file))
    .call(d3.drag()
      .on('start', (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end',   (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

  node.append('circle').attr('r', 8).attr('fill', d => colorByType[d.type] || '#64748b');
  node.append('text').text(d => d.label).attr('x', 12).attr('y', 4).attr('fill', '#cbd5e1').attr('font-size', '11px');

  sim.on('tick', () => {
    link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    node.attr('transform', d => 'translate(' + d.x + ',' + d.y + ')');
  });
}
"""


HTML_SCRIPT_RISKS = """
function renderRisks() {
  const root = document.getElementById('view-risks');
  const risks = WIKI_DATA.risks || [];
  const rows = risks.map(r =>
    '<tr>' +
      '<td><a href="#" data-page="' + r.page + '">' + r.page_title + '</a></td>' +
      '<td>' + r.risk + '</td>' +
      '<td>' + r.likelihood + '</td>' +
      '<td>' + r.impact + '</td>' +
      '<td>' + r.status + '</td>' +
    '</tr>'
  ).join('');
  root.innerHTML =
    '<h2>Open risks (' + risks.length + ')</h2>' +
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
      '<td class="muted" style="white-space:nowrap">' + e.date + '</td>' +
      '<td><span class="badge">' + e.action + '</span></td>' +
      '<td>' + e.detail + '</td>' +
    '</tr>'
  ).join('');
  root.innerHTML =
    '<h2>Recent changes</h2>' +
    (log.length === 0
      ? '<p class="muted">No log entries.</p>'
      : '<table><tbody>' + rows + '</tbody></table>');
}
"""


HTML_SCRIPT_OPEN_QS = """
function renderOpenQs() {
  const root = document.getElementById('view-open-qs');
  const qs = WIKI_DATA.open_qs || [];
  const rows = qs.map(q =>
    '<div class="card">' +
      '<div>' + q.question + '</div>' +
      '<div class="muted" style="margin-top:6px">' +
        '<a href="#" data-page="' + q.page + '">' + q.page_title + '</a>' +
      '</div>' +
    '</div>'
  ).join('');
  root.innerHTML =
    '<h2>Open questions (' + qs.length + ')</h2>' +
    (qs.length === 0 ? '<p class="muted">No open questions.</p>' : rows);
  root.querySelectorAll('a[data-page]').forEach(a => {
    a.addEventListener('click', e => { e.preventDefault(); window.openPage(a.dataset.page); });
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
      '<td><a href="#" data-page="' + e.path + '">' + e.title + '</a></td>' +
      '<td><span class="badge">' + (e.type || 'entity') + '</span></td>' +
      '<td class="muted">' + (inbound[e.path] || 0) + ' mentions</td>' +
    '</tr>'
  ).join('');
  root.innerHTML =
    '<h2>Entities (' + entities.length + ')</h2>' +
    (entities.length === 0
      ? '<p class="muted">No entity pages yet.</p>'
      : '<table><tbody>' + rows + '</tbody></table>');
  root.querySelectorAll('a[data-page]').forEach(a => {
    a.addEventListener('click', e => { e.preventDefault(); window.openPage(a.dataset.page); });
  });
}
"""


HTML_SCRIPT_VIEW_SWITCH = """
const buttons = document.querySelectorAll('#sidebar nav button');
const views = document.querySelectorAll('.view');
function showView(name) {
  views.forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
  buttons.forEach(b => b.classList.toggle('active', b.dataset.view === name));
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
{HTML_SCRIPT_HOME}
{HTML_SCRIPT_PAGE}
{HTML_SCRIPT_SEARCH}
{HTML_SCRIPT_GRAPH}
{HTML_SCRIPT_RISKS}
{HTML_SCRIPT_RECENT}
{HTML_SCRIPT_OPEN_QS}
{HTML_SCRIPT_ENTITIES}
{HTML_SCRIPT_VIEW_SWITCH}
renderHome();
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
