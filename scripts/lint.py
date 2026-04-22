#!/usr/bin/env python3
"""
lint.py — structural health check across all wiki pages.

Usage:
    python3 scripts/lint.py          # full structural lint
    python3 scripts/lint.py --json   # machine-readable output for LLM consumption

Checks performed (structural/mechanical — no LLM required):
  - Mandatory sections present in use-case and entity pages
  - Required YAML frontmatter fields present
  - source frontmatter points to an existing file in sources/
  - Risk Register rows with status 🔲 (not yet addressed)
  - Files in sources/ with no corresponding wiki page
  - Wiki pages not listed in index.md
  - index.md entries pointing to files that don't exist
  - Entity/concept pages: mandatory sections present
  - Entity/concept pages: mentioned_in entries resolve to existing files

NOT checked here (require LLM):
  - Contradiction scan across pages
  - Source drift (re-fetching live sources)
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
EXCLUDE_FILES = {"wiki-agent.md", "CLAUDE.md", "AGENTS.md", "GEMINI.md", "CONVENTIONS.md", "README.md", "index.md", "log.md"}

REQUIRED_FRONTMATTER = {"title", "category", "status", "owner", "tags", "created", "last_reviewed"}
USE_CASE_MANDATORY_SECTIONS = {"What This Is", "How It Works", "Risk Register", "Prerequisites"}
ENTITY_MANDATORY_SECTIONS = {"What It Is", "How We Use It", "Where It Appears"}
OPEN_RISK_STATUS = "🔲"


# ── parsing ───────────────────────────────────────────────────────────────────

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


def extract_sections(text: str) -> set[str]:
    return set(re.findall(r'^#{1,3}\s+(.+)', text, re.MULTILINE))


def collect_pages() -> list[dict]:
    pages = []
    for md in sorted(WIKI_ROOT.glob("*.md")):
        if md.name in EXCLUDE_FILES:
            continue
        text = md.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        if not fm:
            continue
        pages.append({"file": md.name, "fm": fm, "text": text, "sections": extract_sections(text)})
    return pages


def collect_source_files() -> set[str]:
    sources_dir = WIKI_ROOT / "sources"
    if not sources_dir.exists():
        return set()
    return {f.name for f in sources_dir.iterdir() if f.is_file()}


def parse_index_entries() -> set[str]:
    index = WIKI_ROOT / "index.md"
    if not index.exists():
        return set()
    text = index.read_text(encoding="utf-8")
    return set(re.findall(r'\]\(\./([^)]+\.md)\)', text))


def parse_risk_open_rows(text: str) -> list[str]:
    rows = []
    in_table = header_seen = False
    for line in text.splitlines():
        s = line.strip()
        if "Risk" in s and "Likelihood" in s and "|" in s:
            in_table = True; header_seen = False; continue
        if in_table and s.startswith("|") and set(s.replace("|", "").replace("-", "").strip()) == set():
            header_seen = True; continue
        if in_table and s.startswith("|") and header_seen:
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) >= 5 and OPEN_RISK_STATUS in cells[4]:
                rows.append(cells[0][:80])
        elif in_table and not s.startswith("|"):
            in_table = header_seen = False
    return rows


# ── checks ────────────────────────────────────────────────────────────────────

def run_checks(pages: list[dict], source_files: set[str], index_entries: set[str]) -> list[dict]:
    issues = []
    wiki_files = {p["file"] for p in pages}

    for p in pages:
        f = p["file"]
        fm = p["fm"]
        sections = p["sections"]
        page_type = fm.get("type", "")
        is_entity = page_type in ("entity", "concept")

        # Frontmatter completeness
        for field in REQUIRED_FRONTMATTER:
            if field not in fm or fm[field] is None or fm[field] == "":
                issues.append({"file": f, "check": "frontmatter", "detail": f"missing field: {field}"})

        # source field resolves
        src = fm.get("source", "")
        if src:
            src_name = Path(src).name
            if src_name not in source_files:
                issues.append({"file": f, "check": "source_missing", "detail": f"source '{src}' not found in sources/"})

        # Mandatory sections (skip meta pages — free-form structure)
        is_meta = "meta" in str(fm.get("category", "")).lower() or fm.get("type") == "meta"
        if is_entity:
            for section in ENTITY_MANDATORY_SECTIONS:
                if not any(section.lower() in s.lower() for s in sections):
                    issues.append({"file": f, "check": "missing_section", "detail": f"entity page missing section: {section}"})
        elif not is_meta:
            for section in USE_CASE_MANDATORY_SECTIONS:
                if not any(section.lower() in s.lower() for s in sections):
                    issues.append({"file": f, "check": "missing_section", "detail": f"use-case page missing section: {section}"})

        # Open risk register rows
        open_risks = parse_risk_open_rows(p["text"])
        for risk in open_risks:
            issues.append({"file": f, "check": "open_risk", "detail": f"🔲 {risk}"})

        # mentioned_in entries resolve
        if is_entity:
            for ref in (fm.get("mentioned_in") or []):
                if str(ref) not in wiki_files:
                    issues.append({"file": f, "check": "mentioned_in_missing", "detail": f"mentioned_in: '{ref}' does not exist"})

        # Not in index
        if f not in index_entries:
            issues.append({"file": f, "check": "not_in_index", "detail": f"not listed in index.md"})

    # Sources with no wiki page
    wiki_sources = {Path(p["fm"].get("source", "")).name for p in pages if p["fm"].get("source")}
    for src_file in sorted(source_files):
        if src_file not in wiki_sources:
            issues.append({"file": f"sources/{src_file}", "check": "orphan_source", "detail": "no wiki page has source pointing here"})

    # index.md entries pointing to missing files (structural files like CONVENTIONS.md are excluded from
    # wiki_files but do exist on disk — only flag entries where the file genuinely doesn't exist)
    for entry in sorted(index_entries):
        if entry not in wiki_files and not (WIKI_ROOT / entry).exists():
            issues.append({"file": "index.md", "check": "index_dead_link", "detail": f"entry '{entry}' does not exist"})

    return issues


# ── output ────────────────────────────────────────────────────────────────────

CHECK_LABELS = {
    "frontmatter":        "Frontmatter",
    "source_missing":     "Broken source ref",
    "missing_section":    "Missing section",
    "open_risk":          "Open risk",
    "mentioned_in_missing": "Broken mentioned_in",
    "not_in_index":       "Not in index",
    "orphan_source":      "Orphan source",
    "index_dead_link":    "Dead index link",
}


def print_report(issues: list[dict]) -> None:
    if not issues:
        print("✅ No structural issues found.")
        return

    by_check: dict[str, list[dict]] = {}
    for issue in issues:
        by_check.setdefault(issue["check"], []).append(issue)

    for check, items in by_check.items():
        label = CHECK_LABELS.get(check, check)
        print(f"\n### {label} ({len(items)})\n")
        for item in items:
            print(f"- [ ] `{item['file']}` — {item['detail']}")

    total = len(issues)
    open_risks = sum(1 for i in issues if i["check"] == "open_risk")
    structural = total - open_risks
    print(f"\n---\n{total} issue(s): {structural} structural, {open_risks} open risk row(s)")
    print("\n⚠️  Contradiction scan and source drift require LLM — run those separately.")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Structural lint for wiki pages")
    parser.add_argument("--json", action="store_true", help="Output issues as JSON array")
    args = parser.parse_args()

    pages = collect_pages()
    source_files = collect_source_files()
    index_entries = parse_index_entries()
    issues = run_checks(pages, source_files, index_entries)

    if args.json:
        print(json.dumps(issues, indent=2, ensure_ascii=False))
    else:
        print_report(issues)


if __name__ == "__main__":
    main()
