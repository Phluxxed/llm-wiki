#!/usr/bin/env python3
"""
graph.py — generate graph.html: an interactive force-directed graph of wiki page connections.

Usage:
    python3 scripts/graph.py            # writes graph.html to wiki root
    python3 scripts/graph.py --output path/to/out.html
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

WIKI_ROOT = Path(__file__).parent.parent
EXCLUDE_FILES = {"CLAUDE.md", "AGENTS.md", "GEMINI.md", "CONVENTIONS.md", "README.md", "index.md", "log.md"}


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
    cat = fm.get("category", "").lower()
    if "meta" in cat:
        return "meta"
    return "use-case"


def collect_pages() -> dict:
    pages = {}
    for md in WIKI_ROOT.glob("*.md"):
        if md.name in EXCLUDE_FILES:
            continue
        text = md.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        if not fm:
            continue
        pages[md.name] = {
            "title": fm.get("title") or md.stem.replace("-", " ").title(),
            "type": page_type(fm),
            "tags": list(fm.get("tags") or []),
            "fm": fm,
            "text": text,
        }
    return pages


def collect_edges(pages: dict) -> list[tuple[str, str]]:
    edges = set()
    for src_file, page in pages.items():
        for raw in re.findall(r'\[(?:[^\]]+)\]\(\.?/?([^)#\s]+\.md)\)', page["text"]):
            tgt = Path(raw).name
            if tgt in pages and tgt != src_file:
                edges.add((src_file, tgt))
        mentioned = page["fm"].get("mentioned_in") or []
        for referrer in mentioned:
            referrer = str(referrer)
            if referrer in pages and referrer != src_file:
                edges.add((referrer, src_file))
    return list(edges)


def build_html(pages: dict, edges: list[tuple[str, str]]) -> str:
    file_list = sorted(pages.keys())
    id_map = {f: i for i, f in enumerate(file_list)}

    inbound = {f: 0 for f in pages}
    for _, tgt in edges:
        inbound[tgt] += 1

    nodes_js = ",\n".join(
        f'  {{id: {id_map[f]}, label: {repr(p["title"])}, '
        f'type: {repr(p["type"])}, file: {repr(f)}, '
        f'tags: {json.dumps(p["tags"])}, '
        f'orphan: {"true" if inbound[f] == 0 else "false"}}}'
        for f, p in pages.items()
    )

    links_js = ",\n".join(
        f'  {{source: {id_map[s]}, target: {id_map[t]}}}'
        for s, t in edges
    )

    node_count = len(pages)
    edge_count = len(edges)
    orphan_count = sum(1 for f in pages if inbound[f] == 0)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Wiki Graph</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f1117; font-family: system-ui, sans-serif; color: #e2e8f0; overflow: hidden; }}

  #info {{ position: fixed; top: 14px; left: 14px; font-size: 12px; color: #475569; pointer-events: none; z-index: 5; }}

  #tooltip {{
    position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
    background: #1e2130; padding: 6px 14px; border-radius: 6px; font-size: 13px;
    pointer-events: none; opacity: 0; transition: opacity 0.15s; white-space: nowrap; z-index: 20;
  }}

  /* ── Panel toggle button ── */
  #panel-toggle {{
    position: fixed; top: 10px; right: 10px; z-index: 30;
    background: #1e2130; border: 1px solid #334155; color: #94a3b8;
    width: 30px; height: 30px; border-radius: 6px; cursor: pointer;
    font-size: 15px; display: flex; align-items: center; justify-content: center;
  }}
  #panel-toggle:hover {{ color: #e2e8f0; border-color: #64748b; }}

  /* ── Panel ── */
  #panel {{
    position: fixed; right: 0; top: 0; height: 100vh; width: 240px;
    background: #0a0d14; border-left: 1px solid #1a2030;
    overflow-y: auto; padding: 52px 14px 20px;
    transform: translateX(0); transition: transform 0.2s ease;
    z-index: 25;
  }}
  #panel.hidden {{ transform: translateX(100%); }}

  .section {{ margin-bottom: 18px; }}
  .section-title {{
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em;
    color: #475569; margin-bottom: 8px; font-weight: 600;
  }}

  input[type="text"] {{
    width: 100%; background: #1e2130; border: 1px solid #2d3748;
    color: #e2e8f0; padding: 5px 8px; border-radius: 4px; font-size: 12px; outline: none;
  }}
  input[type="text"]:focus {{ border-color: #60a5fa; }}

  input[type="range"] {{ width: 100%; accent-color: #60a5fa; cursor: pointer; margin: 3px 0; }}

  .chips {{ display: flex; flex-wrap: wrap; gap: 4px; }}
  .chip {{
    background: #1e2130; border: 1px solid #334155; border-radius: 10px;
    padding: 3px 9px; font-size: 11px; cursor: pointer; color: #64748b;
    transition: all 0.15s; user-select: none;
  }}
  .chip:hover {{ color: #94a3b8; border-color: #475569; }}
  .chip.on {{ background: #172033; border-color: #3b82f6; color: #93c5fd; }}

  .toggle-row {{
    display: flex; align-items: center; justify-content: space-between;
    font-size: 12px; color: #94a3b8; margin: 8px 0;
  }}
  .toggle {{ position: relative; width: 34px; height: 20px; flex-shrink: 0; }}
  .toggle input {{ opacity: 0; width: 0; height: 0; position: absolute; }}
  .toggle-track {{
    position: absolute; inset: 0; background: #334155;
    border-radius: 10px; cursor: pointer; transition: background 0.2s;
  }}
  .toggle input:checked + .toggle-track {{ background: #3b82f6; }}
  .toggle-track::before {{
    content: ""; position: absolute; height: 14px; width: 14px;
    left: 3px; top: 3px; background: white; border-radius: 50%; transition: transform 0.2s;
  }}
  .toggle input:checked + .toggle-track::before {{ transform: translateX(14px); }}

  .slider-row {{
    display: flex; justify-content: space-between;
    font-size: 11px; color: #475569; margin-top: 10px; margin-bottom: 2px;
  }}
  .slider-val {{ color: #64748b; }}

  #depth-section {{
    background: #0d1520; border: 1px solid #1d3357; border-radius: 6px;
    padding: 10px; margin-bottom: 18px; display: none;
  }}

  .divider {{ border: none; border-top: 1px solid #1a2030; margin: 14px 0; }}

  svg {{ width: 100vw; height: 100vh; cursor: grab; display: block; }}
  svg:active {{ cursor: grabbing; }}
</style>
</head>
<body>

<div id="info">{node_count} pages · {edge_count} links · {orphan_count} orphan{'s' if orphan_count != 1 else ''}</div>
<div id="tooltip"></div>

<button id="panel-toggle" title="Toggle panel">⚙</button>

<div id="panel">

  <div class="section">
    <div class="section-title">Search</div>
    <input type="text" id="search" placeholder="Filter nodes…">
  </div>

  <div class="section">
    <div class="section-title">Types</div>
    <div class="chips" id="type-chips"></div>
  </div>

  <div class="section" id="tag-section" style="display:none">
    <div class="section-title">Tags</div>
    <div class="chips" id="tag-chips"></div>
  </div>

  <hr class="divider">

  <div id="depth-section">
    <div class="section-title">Selection depth</div>
    <div class="slider-row"><span>Hops</span><span class="slider-val" id="depth-val">1</span></div>
    <input type="range" id="depth-slider" min="1" max="4" value="1">
  </div>

  <div class="section">
    <div class="section-title">Display</div>
    <div class="toggle-row">Labels <label class="toggle"><input type="checkbox" id="toggle-labels" checked><span class="toggle-track"></span></label></div>
    <div class="toggle-row">Arrows <label class="toggle"><input type="checkbox" id="toggle-arrows" checked><span class="toggle-track"></span></label></div>
  </div>

  <div class="section">
    <div class="section-title">Forces</div>
    <div class="slider-row"><span>Link distance</span><span class="slider-val" id="dist-val">120</span></div>
    <input type="range" id="link-dist" min="50" max="300" value="120">
    <div class="slider-row"><span>Repulsion</span><span class="slider-val" id="charge-val">450</span></div>
    <input type="range" id="charge-str" min="50" max="800" value="450">
  </div>

  <div class="section">
    <div class="section-title">Legend</div>
    <div style="display:flex;flex-direction:column;gap:7px">
      <div style="display:flex;align-items:center;gap:8px;font-size:11px;color:#64748b">
        <span style="width:12px;height:12px;border-radius:50%;background:#60a5fa;flex-shrink:0;display:inline-block"></span> Use case
      </div>
      <div style="display:flex;align-items:center;gap:8px;font-size:11px;color:#64748b">
        <span style="width:10px;height:10px;border-radius:50%;background:#a78bfa;flex-shrink:0;display:inline-block"></span> Entity / concept
      </div>
      <div style="display:flex;align-items:center;gap:8px;font-size:11px;color:#64748b">
        <span style="width:10px;height:10px;border-radius:50%;background:#34d399;flex-shrink:0;display:inline-block"></span> Meta
      </div>
      <div style="display:flex;align-items:center;gap:8px;font-size:11px;color:#64748b">
        <span style="width:10px;height:10px;border-radius:50%;background:transparent;border:1.5px dashed #f87171;flex-shrink:0;display:inline-block"></span> Orphan
      </div>
    </div>
  </div>

</div><!-- /panel -->

<svg id="graph-svg">
  <defs>
    <marker id="arrow" viewBox="0 -4 8 8" refX="8" refY="0" markerWidth="5" markerHeight="5" orient="auto">
      <path d="M0,-4L8,0L0,4" fill="#3d4f66"/>
    </marker>
    <marker id="arrow-hi" viewBox="0 -4 8 8" refX="8" refY="0" markerWidth="5" markerHeight="5" orient="auto">
      <path d="M0,-4L8,0L0,4" fill="#94a3b8"/>
    </marker>
  </defs>
</svg>

<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
const nodes = [
{nodes_js}
];
const links = [
{links_js}
];

// ── Degree-based radius (computed before sim mutates links) ───────────────
const deg = new Array(nodes.length).fill(0);
links.forEach(l => {{ deg[l.source]++; deg[l.target]++; }});

function nodeRadius(d) {{
  const base = d.type === "use-case" ? 12 : 9;
  return base + Math.sqrt(deg[d.id]) * 2.5;
}}

// ── Colour ────────────────────────────────────────────────────────────────
const colour = {{ "use-case": "#60a5fa", "entity": "#a78bfa", "concept": "#a78bfa", "meta": "#34d399" }};

// ── SVG + zoom ────────────────────────────────────────────────────────────
const svgEl = d3.select("#graph-svg");
const canvas = svgEl.append("g");

const zoom = d3.zoom()
  .scaleExtent([0.05, 5])
  .on("zoom", e => canvas.attr("transform", e.transform));
svgEl.call(zoom);

// ── Simulation ────────────────────────────────────────────────────────────
const PANEL_W = 240;
const cx = (window.innerWidth - PANEL_W) / 2;
const cy = window.innerHeight / 2;

const sim = d3.forceSimulation(nodes)
  .force("link",      d3.forceLink(links).id(d => d.id).distance(120))
  .force("charge",    d3.forceManyBody().strength(-450))
  .force("center",    d3.forceCenter(cx, cy).strength(0.3))
  .force("x",         d3.forceX(cx).strength(0.06))
  .force("y",         d3.forceY(cy).strength(0.06))
  .force("collision", d3.forceCollide().radius(d => nodeRadius(d) + 10));

// ── Elements ──────────────────────────────────────────────────────────────
const linkEl = canvas.append("g").selectAll("line").data(links).join("line")
  .attr("stroke", "#334155").attr("stroke-width", 1.5).attr("stroke-opacity", 0.6)
  .attr("marker-end", "url(#arrow)");

const nodeEl = canvas.append("g").selectAll("circle").data(nodes).join("circle")
  .attr("r", nodeRadius)
  .attr("fill", d => d.orphan ? "transparent" : colour[d.type] || "#94a3b8")
  .attr("stroke", d => d.orphan ? "#f87171" : colour[d.type] || "#94a3b8")
  .attr("stroke-width", d => d.orphan ? 2 : 0)
  .attr("stroke-dasharray", d => d.orphan ? "4,2" : null)
  .style("cursor", "pointer")
  .call(d3.drag()
    .on("start", (e, d) => {{ if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }})
    .on("drag",  (e, d) => {{ d.fx = e.x; d.fy = e.y; }})
    .on("end",   (e, d) => {{ if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }}));

const labelEl = canvas.append("g").selectAll("text").data(nodes).join("text")
  .text(d => d.label.length > 35 ? d.label.slice(0, 33) + "…" : d.label)
  .attr("font-size", 11).attr("fill", "#cbd5e1").attr("text-anchor", "middle")
  .attr("dy", d => nodeRadius(d) + 14)
  .style("pointer-events", "none");

// ── State ─────────────────────────────────────────────────────────────────
let selected    = null;
let depth       = 1;
let showLabels  = true;
let showArrows  = true;
let activeTypes = new Set(nodes.map(n => n.type));
let activeTags  = new Set();
let searchQuery = "";

// ── Visibility ────────────────────────────────────────────────────────────
function isVisible(n) {{
  if (!activeTypes.has(n.type)) return false;
  if (searchQuery) {{
    const q = searchQuery.toLowerCase();
    if (!n.label.toLowerCase().includes(q) && !n.file.toLowerCase().includes(q)) return false;
  }}
  if (activeTags.size > 0 && !n.tags.some(t => activeTags.has(t))) return false;
  return true;
}}

// ── BFS neighbours ────────────────────────────────────────────────────────
function getNeighbours(startId, hops) {{
  const visited = new Set([startId]);
  let frontier = [startId];
  for (let i = 0; i < hops; i++) {{
    const next = [];
    frontier.forEach(id => {{
      links.forEach(l => {{
        const s = l.source.id, t = l.target.id;
        if (s === id && !visited.has(t)) {{ visited.add(t); next.push(t); }}
        if (t === id && !visited.has(s)) {{ visited.add(s); next.push(s); }}
      }});
    }});
    frontier = next;
  }}
  return visited;
}}

// ── Apply filters (no selection) ─────────────────────────────────────────
function applyFilters() {{
  nodeEl .attr("opacity",       n => isVisible(n) ? 1 : 0)
         .style("pointer-events", n => isVisible(n) ? null : "none");
  labelEl.attr("opacity",       n => isVisible(n) && showLabels ? 1 : 0);
  linkEl .attr("stroke-opacity", l => isVisible(l.source) && isVisible(l.target) ? 0.6 : 0)
         .attr("stroke", "#334155").attr("stroke-width", 1.5)
         .attr("marker-end", showArrows ? "url(#arrow)" : null);
}}

// ── Highlight (with selection) ────────────────────────────────────────────
function highlight(d) {{
  selected = d;
  const neighbours = getNeighbours(d.id, depth);

  linkEl
    .attr("stroke", l => {{
      if (l.source.id === d.id || l.target.id === d.id) return "#e2e8f0";
      if (neighbours.has(l.source.id) && neighbours.has(l.target.id)) return "#64748b";
      return "#1a2030";
    }})
    .attr("stroke-width", l => (l.source.id === d.id || l.target.id === d.id) ? 2.5 : 1)
    .attr("stroke-opacity", l => {{
      if (!isVisible(l.source) || !isVisible(l.target)) return 0;
      if (l.source.id === d.id || l.target.id === d.id) return 0.9;
      if (neighbours.has(l.source.id) && neighbours.has(l.target.id)) return 0.4;
      return 0.04;
    }})
    .attr("marker-end", l => {{
      if (!showArrows) return null;
      return (l.source.id === d.id || l.target.id === d.id) ? "url(#arrow-hi)" : "url(#arrow)";
    }});

  nodeEl .attr("opacity", n => {{ if (!isVisible(n)) return 0; return neighbours.has(n.id) ? 1 : 0.1; }});
  labelEl.attr("opacity", n => {{ if (!isVisible(n) || !showLabels) return 0; return neighbours.has(n.id) ? 1 : 0.06; }});

  document.getElementById("depth-section").style.display = "block";
}}

function reset() {{
  selected = null;
  document.getElementById("depth-section").style.display = "none";
  applyFilters();
}}

// ── Click: node ───────────────────────────────────────────────────────────
nodeEl.on("click", (e, d) => {{
  e.stopPropagation();
  selected && selected.id === d.id ? reset() : highlight(d);
}});

// ── Click: background (reset, but not when panning) ───────────────────────
let wasPanning = false;
zoom.on("start.track", () => {{ wasPanning = false; }})
    .on("zoom.track",  () => {{ wasPanning = true;  }});
svgEl.on("click", () => {{ if (!wasPanning) reset(); }});

// ── Tooltip ───────────────────────────────────────────────────────────────
const tip = document.getElementById("tooltip");
nodeEl.on("mouseover", (e, d) => {{ tip.textContent = d.file; tip.style.opacity = 1; }})
      .on("mouseout",  ()     => {{ tip.style.opacity = 0; }});

// ── Tick ──────────────────────────────────────────────────────────────────
sim.on("tick", () => {{
  linkEl
    .attr("x1", d => d.source.x)
    .attr("y1", d => d.source.y)
    .attr("x2", d => {{
      const r = nodeRadius(d.target) + (showArrows ? 7 : 0);
      const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      return d.target.x - (dx / dist) * r;
    }})
    .attr("y2", d => {{
      const r = nodeRadius(d.target) + (showArrows ? 7 : 0);
      const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      return d.target.y - (dy / dist) * r;
    }});
  nodeEl .attr("cx", d => d.x).attr("cy", d => d.y);
  labelEl.attr("x",  d => d.x).attr("y",  d => d.y);
}});

// ── Panel: type chips ─────────────────────────────────────────────────────
const allTypes = [...new Set(nodes.map(n => n.type))];
const typeChipsCt = document.getElementById("type-chips");
allTypes.forEach(t => {{
  const chip = document.createElement("div");
  chip.className = "chip on";
  chip.textContent = t;
  chip.style.borderColor = colour[t] || "#334155";
  chip.addEventListener("click", () => {{
    chip.classList.toggle("on");
    activeTypes[chip.classList.contains("on") ? "add" : "delete"](t);
    selected ? highlight(selected) : applyFilters();
  }});
  typeChipsCt.appendChild(chip);
}});

// ── Panel: tag chips ──────────────────────────────────────────────────────
const allTags = [...new Set(nodes.flatMap(n => n.tags))].sort();
if (allTags.length) {{
  document.getElementById("tag-section").style.display = "block";
  const tagChipsCt = document.getElementById("tag-chips");
  allTags.forEach(t => {{
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.textContent = t;
    chip.addEventListener("click", () => {{
      chip.classList.toggle("on");
      activeTags[chip.classList.contains("on") ? "add" : "delete"](t);
      selected ? highlight(selected) : applyFilters();
    }});
    tagChipsCt.appendChild(chip);
  }});
}}

// ── Panel: search ─────────────────────────────────────────────────────────
document.getElementById("search").addEventListener("input", e => {{
  searchQuery = e.target.value;
  selected ? highlight(selected) : applyFilters();
}});

// ── Panel: display toggles ────────────────────────────────────────────────
document.getElementById("toggle-labels").addEventListener("change", e => {{
  showLabels = e.target.checked;
  selected ? highlight(selected) : applyFilters();
}});

document.getElementById("toggle-arrows").addEventListener("change", e => {{
  showArrows = e.target.checked;
  linkEl.attr("marker-end", showArrows ? "url(#arrow)" : null);
  // Retick to fix line endpoints
  sim.on("tick")();
}});

// ── Panel: depth slider ───────────────────────────────────────────────────
document.getElementById("depth-slider").addEventListener("input", e => {{
  depth = +e.target.value;
  document.getElementById("depth-val").textContent = depth;
  if (selected) highlight(selected);
}});

// ── Panel: force sliders ──────────────────────────────────────────────────
document.getElementById("link-dist").addEventListener("input", e => {{
  document.getElementById("dist-val").textContent = e.target.value;
  sim.force("link").distance(+e.target.value);
  sim.alpha(0.3).restart();
}});

document.getElementById("charge-str").addEventListener("input", e => {{
  document.getElementById("charge-val").textContent = e.target.value;
  sim.force("charge").strength(-e.target.value);
  sim.alpha(0.3).restart();
}});

// ── Panel: toggle open/close ──────────────────────────────────────────────
document.getElementById("panel-toggle").addEventListener("click", () => {{
  document.getElementById("panel").classList.toggle("hidden");
}});

</script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate wiki graph.html")
    parser.add_argument("--output", default=str(WIKI_ROOT / "graph.html"))
    args = parser.parse_args()

    pages = collect_pages()
    edges = collect_edges(pages)

    inbound = {f: 0 for f in pages}
    for _, tgt in edges:
        inbound[tgt] += 1
    orphans = [f for f in pages if inbound[f] == 0]

    html = build_html(pages, edges)
    Path(args.output).write_text(html, encoding="utf-8")

    print(f"graph.html written — {len(pages)} nodes, {len(edges)} edges, {len(orphans)} orphan(s)")
    if orphans:
        for o in sorted(orphans):
            print(f"  orphan: {o}")


if __name__ == "__main__":
    main()
