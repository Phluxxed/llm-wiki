#!/usr/bin/env python3
"""
lint.py — structural health check across all wiki pages.

Usage:
    python3 scripts/lint.py          # full structural lint
    python3 scripts/lint.py --json   # machine-readable output for LLM consumption

Checks performed (structural/mechanical — no LLM required):
  - Mandatory sections present in default-primary pages (no `type:` field) and entity pages
  - Required YAML frontmatter fields present (incl. OKF: type, description, timestamp)
  - OKF v0.1: non-reserved .md files have parseable frontmatter (§9.1)
  - OKF v0.1: root index.md declares okf_version (§11)
  - source frontmatter points to an existing file in sources/
  - Pages without a source field whose body references sources/X (likely missed ingest)
  - Body markdown links whose target .md file does not exist (broken refs / typos)
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
EXCLUDE_DIRS = {"sources", "_templates", "scripts", ".git", ".obsidian", "evals", ".eval"}

REQUIRED_FRONTMATTER = {"title", "category", "status", "owner", "tags", "created", "last_reviewed",
                        "type", "description", "timestamp"}
# `type`, `description`, `timestamp` make a wiki page a conformant OKF v0.1 concept
# (strict superset): `type` is OKF's one required routing field (and our colour
# signal), `description` a one-line summary, `timestamp` the ISO 8601 last
# meaningful change — distinct from `created` (creation) and `last_reviewed`.
# Enforced on every primary page regardless of its `type:` value (the slug
# default, or article/policy/control/…). `type:` is a colour/filter signal, not
# a lint exemption — only entity/concept and meta pages route elsewhere (§8b).
PRIMARY_MANDATORY_SECTIONS = {"What This Is", "How It Works", "Risk Register", "Prerequisites"}
ENTITY_MANDATORY_SECTIONS = {"What It Is", "How We Use It", "Where It Appears"}
OPEN_RISK_STATUS = "🔲"
SOURCE_REF_RE = re.compile(r'\bsources/[\w\-./]+\.\w+')
BODY_LINK_RE = re.compile(r'\[(?:[^\]]+)\]\(([^)#\s]+\.md)\)')


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
        pages.append({"file": str(rel), "fm": fm, "text": text, "sections": extract_sections(text)})
    return pages


def collect_source_files() -> set[str]:
    sources_dir = WIKI_ROOT / "sources"
    if not sources_dir.exists():
        return set()
    return {f.name for f in sources_dir.iterdir() if f.is_file() and not f.name.startswith(".")}


def collect_all_md_paths() -> set[str]:
    """Wiki-root-relative paths of every .md file in the wiki tree.

    Used as the universe of valid link targets for the broken-body-link check —
    includes pages, source files, templates, and top-level structural files,
    so a link to any of them is considered resolvable.
    """
    paths = set()
    for md in WIKI_ROOT.rglob("*.md"):
        rel = md.relative_to(WIKI_ROOT)
        if rel.parts and rel.parts[0] == ".git":
            continue
        paths.add(str(rel).replace("\\", "/"))
    return paths


def resolve_link(raw: str, src_file: str, targets: set) -> str | None:
    """Resolve a markdown link to a wiki-root-relative path against `targets`.

    Mirrors render.py's resolver: tries wiki-root-relative interpretation first
    (`./components/X.md`, `components/X.md`), then falls back to source-relative
    resolution so sibling `./X.md` and `../other-dir/X.md` from inside a sub-dir
    also resolve. Returns the matched key, or None.
    """
    raw = raw.replace("\\", "/")
    cleaned = raw[2:] if raw.startswith("./") else raw

    if cleaned in targets:
        return cleaned

    src_dir_parts = src_file.split("/")[:-1]
    parts = list(src_dir_parts)
    for c in cleaned.split("/"):
        if c == "..":
            if parts:
                parts.pop()
        elif c and c != ".":
            parts.append(c)
    resolved = "/".join(parts)
    return resolved if resolved in targets else None


def parse_index_entries() -> set[str]:
    index = WIKI_ROOT / "index.md"
    if not index.exists():
        return set()
    text = index.read_text(encoding="utf-8")
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    return set(re.findall(r'\]\(\./([^)]+\.md)\)', text))


def collect_unparseable_md() -> list[str]:
    """Non-reserved .md files (concept docs) that lack a parseable frontmatter block.

    OKF §9.1 requires every non-reserved .md to have parseable YAML frontmatter.
    collect_pages() silently skips frontmatter-less files, so they would never be
    flagged otherwise — this scan catches them.
    """
    bad = []
    for md in sorted(WIKI_ROOT.rglob("*.md")):
        rel = md.relative_to(WIKI_ROOT)
        if rel.parts[0] in EXCLUDE_DIRS:
            continue
        if md.name in EXCLUDE_FILES:
            continue
        if not parse_frontmatter(md.read_text(encoding="utf-8")):
            bad.append(str(rel).replace("\\", "/"))
    return bad


def check_okf_conformance() -> list[dict]:
    """OKF v0.1 conformance checks that operate outside the collect_pages() set.

    §9.1 — every non-reserved .md has parseable frontmatter (with a non-empty
    `type`; the non-empty/required part is covered by REQUIRED_FRONTMATTER once
    the page is parseable and reaches run_checks).
    §11  — the root index.md should declare okf_version.
    """
    issues = []
    for f in collect_unparseable_md():
        issues.append({
            "file": f,
            "check": "okf_no_frontmatter",
            "detail": "no parseable YAML frontmatter (OKF §9.1 — concept docs need frontmatter with a non-empty `type`)",
        })
    index = WIKI_ROOT / "index.md"
    if index.exists():
        if not parse_frontmatter(index.read_text(encoding="utf-8")).get("okf_version"):
            issues.append({
                "file": "index.md",
                "check": "okf_version_missing",
                "detail": 'root index.md should declare okf_version (OKF §11), e.g. okf_version: "0.1"',
            })
    return issues


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

def run_checks(pages: list[dict], source_files: set[str], index_entries: set[str], all_md_paths: set[str]) -> list[dict]:
    issues = []
    wiki_files = {p["file"] for p in pages}

    for p in pages:
        f = p["file"]
        fm = p["fm"]
        sections = p["sections"]
        page_type = fm.get("type", "")
        is_entity = page_type in ("entity", "concept")

        # Body markdown links to .md files must resolve to existing files
        for raw in BODY_LINK_RE.findall(p["text"]):
            if resolve_link(raw, f, all_md_paths) is None:
                issues.append({
                    "file": f,
                    "check": "broken_body_link",
                    "detail": f"link to '{raw}' does not resolve to any .md file in the wiki",
                })

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

        # Page has no source field but body references a sources/X file —
        # likely an ingest where the agent forgot to set the frontmatter source.
        # Skip entity/concept pages (legitimately have no source) and meta pages.
        is_meta_pre = "meta" in str(fm.get("category", "")).lower() or fm.get("type") == "meta"
        if not src and not is_entity and not is_meta_pre:
            body_refs = SOURCE_REF_RE.findall(p["text"])
            if body_refs:
                unique = sorted(set(body_refs))
                refs_preview = ", ".join(unique[:3]) + ("…" if len(unique) > 3 else "")
                issues.append({
                    "file": f,
                    "check": "likely_missing_source",
                    "detail": f"body references {refs_preview} but no source field — should this be set?",
                })

        # Mandatory sections. Behaviour by type:
        #   entity/concept → entity sections enforced
        #   meta (or category contains "meta") → free-form, no enforcement
        #     (meta is for changelogs, archive indices, etc. — legitimately
        #     unstructured)
        #   anything else (no `type:` set, OR `type: policy/control/article/…`)
        #     → strict primary sections enforced. The `type:` field is a
        #     colour/filter/grouping signal, not a lint exemption — primary
        #     pages of any custom type should still answer the four load-bearing
        #     questions (What This Is / How It Works / Risk Register /
        #     Prerequisites), with type-specific content nested as h3 below.
        is_meta = "meta" in str(fm.get("category", "")).lower() or page_type == "meta"
        if is_entity:
            for section in ENTITY_MANDATORY_SECTIONS:
                if not any(section.lower() in s.lower() for s in sections):
                    issues.append({"file": f, "check": "missing_section", "detail": f"entity page missing section: {section}"})
        elif not is_meta:
            for section in PRIMARY_MANDATORY_SECTIONS:
                if not any(section.lower() in s.lower() for s in sections):
                    issues.append({"file": f, "check": "missing_section", "detail": f"primary page missing section: {section}"})

        # Open risk register rows
        open_risks = parse_risk_open_rows(p["text"])
        for risk in open_risks:
            issues.append({"file": f, "check": "open_risk", "detail": f"🔲 {risk}"})

        # mentioned_in entries resolve (values are wiki-root-relative paths, e.g.
        # papers/foo.md or ./papers/foo.md — per wiki-agent.md both forms are valid)
        if is_entity:
            for ref in (fm.get("mentioned_in") or []):
                ref_norm = str(ref).replace("\\", "/")
                ref_cleaned = ref_norm[2:] if ref_norm.startswith("./") else ref_norm
                if ref_cleaned not in wiki_files:
                    issues.append({"file": f, "check": "mentioned_in_missing", "detail": f"mentioned_in: '{ref_norm}' does not exist"})

        # cover: field (chapter notes from a multi-page ingest) — see wiki-agent.md §8a
        # Cover note must exist, must not itself have a cover: field, and must share the same source: value.
        cover_ref = fm.get("cover", "")
        if cover_ref:
            cover_norm = str(cover_ref).replace("\\", "/")
            cover_cleaned = cover_norm[2:] if cover_norm.startswith("./") else cover_norm
            if cover_cleaned not in wiki_files:
                issues.append({"file": f, "check": "cover_missing", "detail": f"cover: '{cover_norm}' does not exist"})
            else:
                # Find the cover page and validate its shape
                cover_page = next((q for q in pages if q["file"] == cover_cleaned), None)
                if cover_page is not None:
                    cover_fm = cover_page["fm"]
                    if cover_fm.get("cover"):
                        issues.append({"file": f, "check": "cover_chain", "detail": f"cover target '{cover_norm}' is itself a chapter (has its own cover: field) — cover chains aren't allowed"})
                    if src and cover_fm.get("source") and Path(str(cover_fm.get("source"))).name != Path(src).name:
                        issues.append({"file": f, "check": "cover_source_mismatch", "detail": f"cover '{cover_norm}' has source '{cover_fm.get('source')}' but this chapter has source '{src}' — should match"})

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
        # Normalise to forward-slash relative path for comparison
        entry_norm = entry.replace("\\", "/")
        if entry_norm not in wiki_files and not (WIKI_ROOT / entry_norm).exists():
            issues.append({"file": "index.md", "check": "index_dead_link", "detail": f"entry '{entry_norm}' does not exist"})

    return issues


# ── output ────────────────────────────────────────────────────────────────────

CHECK_LABELS = {
    "frontmatter":        "Frontmatter",
    "source_missing":     "Broken source ref",
    "likely_missing_source": "Likely missing source field",
    "broken_body_link":   "Broken body link",
    "missing_section":    "Missing section",
    "open_risk":          "Open risk",
    "mentioned_in_missing": "Broken mentioned_in",
    "cover_missing":      "Broken cover ref",
    "cover_chain":        "Cover chain",
    "cover_source_mismatch": "Cover/chapter source mismatch",
    "not_in_index":       "Not in index",
    "orphan_source":      "Orphan source",
    "index_dead_link":    "Dead index link",
    "okf_no_frontmatter": "OKF: missing frontmatter",
    "okf_version_missing": "OKF: index missing okf_version",
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
    print("\n⚠️  Contradiction scan and source drift require LLM — run `python3 scripts/eval.py` (LLM-as-judge quality eval).")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Structural lint for wiki pages")
    parser.add_argument("--json", action="store_true", help="Output issues as JSON array")
    args = parser.parse_args()

    pages = collect_pages()
    source_files = collect_source_files()
    index_entries = parse_index_entries()
    all_md_paths = collect_all_md_paths()
    issues = run_checks(pages, source_files, index_entries, all_md_paths)
    issues += check_okf_conformance()

    if args.json:
        print(json.dumps(issues, indent=2, ensure_ascii=False))
    else:
        print_report(issues)


if __name__ == "__main__":
    main()
