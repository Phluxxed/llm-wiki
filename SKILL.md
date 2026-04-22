---
name: wikime
description: Use when user runs /wikime or asks to scaffold a new wiki, knowledge base, or LLM-maintained document store in an empty or fresh directory
---

# Wiki Scaffold (Karpathy LLM Wiki Pattern)

## Overview

Scaffolds a persistent, LLM-maintained wiki. Two questions first, then create all files in one pass.

## Step 1 — Ask before touching any files

In a single message, ask:
1. **What is this wiki for?** (one sentence — domain, topic, team)
2. **What is the primary page type?** Suggest based on domain:
   - Use cases, integrations, automations → `use cases`
   - Research, papers, literature → `papers`
   - ML training runs → `experiments`
   - Ops / SRE → `runbooks`
   - Architecture / product decisions → `ADRs`

Wait for answers. Do not scaffold until you have both.

## Step 2 — Detect agent → pick schema filename

| Running as | Schema file |
| --- | --- |
| Claude Code | `CLAUDE.md` |
| Codex | `AGENTS.md` |
| Gemini CLI | `GEMINI.md` |
| Unknown | `AGENTS.md` |

## Step 3 — Pre-flight check

Before creating any files, check whether `wiki-agent.md` already exists in the directory.

- **If it exists**: this is an existing wiki. Do not overwrite anything. Tell the user and stop.
- **If it does not exist**: proceed with scaffolding below.

## Step 4 — Create these files

| File | Notes |
| --- | --- |
| `wiki-agent.md` | Agent operating manual — see Schema sections below. This is the wiki's source of truth; all agent instructions live here. |
| `{SCHEMA_FILE}` | **If the file already exists**: append the pointer line (do not overwrite). **If it does not exist**: create it containing only the pointer line. Pointer format depends on platform — see below. |
| `CONVENTIONS.md` | Copy from skill bundle (`skills/wikime/_templates/CONVENTIONS.md`); fill in `{WIKI_NAME}`, `{REPO_NAME}`, `{PAGE_TYPE}`, `{PAGE_TYPE_SINGULAR}`, `{PAGE_TYPE_SLUG}` placeholders |
| `README.md` | Quick start, operations cheat sheet, directory structure, useful commands, Scripts & Tooling section |
| `index.md` | Empty catalog with a commented example showing exact format |
| `log.md` | Seeded: `## [YYYY-MM-DD] init | Created wiki: {files listed}` |
| `_templates/{page-type}.md` | Page template — see Template sections below |
| `_templates/entity.md` | Entity/concept template — see Entity Template section below |
| `sources/` | Empty directory for immutable raw inputs |
| `scripts/graph.py` | Copy from skill bundle (`skills/wikime/scripts/graph.py`); generates `graph.html` — D3.js force-directed graph, orphan detection, filter panel |
| `scripts/query.py` | Copy from skill bundle (`skills/wikime/scripts/query.py`); frontmatter queries — `--status`, `--category`, `--type`, `--tag`, `--stale`, `--risks` |
| `scripts/lint.py` | Copy from skill bundle (`skills/wikime/scripts/lint.py`); structural lint — missing sections, frontmatter, broken refs, open risks, index consistency |

Both scripts require `pyyaml`: `pip3 install pyyaml`

### Schema file pointer format

**Claude Code (`CLAUDE.md`)** — use the native include directive:
```
@wiki-agent.md
```

**All other platforms (`AGENTS.md`, `GEMINI.md`)** — use a natural-language pointer:
```
This folder contains a wiki. All agent instructions are in wiki-agent.md and must be adhered to.
```

## wiki-agent.md — required sections

This file is the agent's operating manual. Include all of these:

1. **Directory structure** — annotated tree
2. **This Wiki's Page Type** — name the chosen type; note it's a wiki-level choice, not universal
3. **Absolute Rules** — never edit `sources/`; always update `index.md`; always append to `log.md`; every derived page needs `source` in frontmatter
4. **Operations** — Ingest (ask user: quick or deep before extracting; after creating the wiki page, scan for entities/concepts and create/update entity pages automatically), Query (read index.md first; file substantive answers back as new pages), Update, Lint (structural checks + contradiction scan across all pages + source drift check for pages with fetchable source URLs)
5. **File Naming** — source files: kebab-case with ID if one exists; wiki pages: `{id}-{title}.md` or `{title}.md`
6. **Source File Header Block** — immutability header template (source type, URL, fetched date, do-not-edit warning)
7. **Risk Register Format** — table with Likelihood/Impact/Mitigation/Status; status reflects design clarity not build status
8. **Wiki Page Frontmatter** — YAML schema: title, category, status, owner, source, tags, created, last_reviewed
9. **index.md Format** — one row per page grouped by category; focus summaries on what it does not what it is
10. **log.md Format** — `## [YYYY-MM-DD] action | detail`; grep-able; append-only
11. **Entity and Concept Pages** — `type: entity | concept` frontmatter field; `mentioned_in: []` backlink list (filenames); mandatory sections: What It Is, How We Use It, Where It Appears; optional: Cross-Cutting Risks, Key References; created automatically during Ingest for any tool/platform/pattern central to how the use case works

### Lint checks — include all of these in the schema file's Lint section

- Pages missing any mandatory section
- Pages missing YAML frontmatter, or frontmatter missing required fields (`title`, `category`, `status`, `owner`, `tags`, `created`, `last_reviewed`)
- Pages with `source` pointing to a file that doesn't exist in `sources/`
- Risk Register rows with status `🔲 Not yet addressed` — flag explicitly
- Files in `sources/` with no corresponding wiki page
- Files not listed in `index.md`
- `index.md` entries pointing to files that don't exist
- Entity/concept pages missing mandatory sections (What It Is, How We Use It, Where It Appears)
- Entity/concept pages with `mentioned_in` entries pointing to files that don't exist
- **Contradiction scan** — read all wiki pages together and flag factual contradictions: conflicting claims about the same tool, service, pattern, credential approach, or behaviour. Report as: "`page-a.md` claims X; `page-b.md` claims Y — conflict on Z." Only flag genuine contradictions, not differences in scope or context.
- **Source drift** — for any wiki page whose source file contains a fetchable URL (Jira, Confluence, web), re-fetch it and compare to the saved content in `sources/`. Flag pages where the live source has changed substantially since last ingest. Skip sources with no URL (pasted text, local docs, meeting notes).

Report all findings as a markdown checklist. Do not auto-fix — report and let the user decide.

**Parameterise**: use the domain and page type from Step 1 throughout. Do NOT carry over domain-specific categories from any existing wiki — replace category examples with generic placeholders like `Category A | Category B`.

## Template — required sections

YAML frontmatter block (title, category, status, owner, source, tags, created, last_reviewed), then:

- **Mandatory**: What This Is, How It Works, Risk Register (seed with credential row), Prerequisites
- **Optional** (commented out): Architecture, Authentication, MCP Servers, CI/CD Setup, Output, Setup, Usage, Notes & Lessons Learned

## Entity Template — required sections

YAML frontmatter block (title, type: entity|concept, category: Entities & Concepts, status, owner, tags, mentioned_in: [], created, last_reviewed), then:

- **Mandatory**: What It Is, How We Use It, Where It Appears (table: wiki page → role)
- **Optional** (commented out): Cross-Cutting Risks, Key References

## Step 5 — After scaffolding

- Run `pip3 install pyyaml` (required by all three scripts)
- Ensure `README.md` includes a `## Scripts & Tooling` section with all three commands and what each produces:
  - `python3 scripts/lint.py` → structural health check
  - `python3 scripts/query.py --help` → frontmatter query filters
  - `python3 scripts/graph.py` → generates `graph.html` (open in browser)
- Offer `git init && echo '.env' >> .gitignore` if this looks like a standalone repo
- Confirm page type and categories look right before the user adds their first page
