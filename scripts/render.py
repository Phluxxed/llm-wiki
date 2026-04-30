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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(WIKI_ROOT / "wiki.html"))
    args = parser.parse_args()
    Path(args.output).write_text("<!DOCTYPE html><html><body>Empty wiki.</body></html>", encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
