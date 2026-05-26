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
2. **What is the primary page type — or types?** A wiki can have one primary type or several (one directory + template per type). Suggest based on domain:
   - Use cases, integrations, automations → `use cases`
   - Research, papers, literature → `papers`
   - ML training runs → `experiments`
   - Ops / SRE → `runbooks`
   - Architecture / product decisions → `ADRs`
   - Governance / compliance → often a mix: `policies` + `controls` + `articles`
   - Product → often a mix: `decisions` (ADRs) + `specs`

   Multiple primary types are first-class: each gets its own directory (`policies/`, `controls/`, `articles/`, …) and its own template, but they share `entities/`, `sources/`, `_templates/`, the index, the log, and the lint/render tooling.

Wait for answers. Do not scaffold until you have both.

## Step 2 — Schema file

Create the schema file for your own platform. You know which agent you are.

| Agent | File | Pointer content |
| --- | --- | --- |
| Claude Code | `CLAUDE.md` | `@wiki-agent.md` |
| Codex | `AGENTS.md` | `This folder contains a wiki. All agent instructions are in wiki-agent.md and must be adhered to.` |
| Gemini CLI | `GEMINI.md` | `This folder contains a wiki. All agent instructions are in wiki-agent.md and must be adhered to.` |

If the file already exists: append the pointer line. If it does not exist: create it containing only that line.

## Step 3 — Pre-flight check

Before creating any files, check whether `wiki-agent.md` already exists in the directory.

- **If it exists**: this is an existing wiki. Do not overwrite anything. **Migration check**: if `scripts/graph.py` or `graph.html` exists, this wiki predates the `render.py` change. Offer the user a one-line migration: replace `scripts/graph.py` with the current `skills/wikime/scripts/render.py`, delete `graph.html`, install `markdown` into the wiki's `.venv` (see Step 5 for the `uv` commands), then `python3 scripts/render.py`. After confirming, also update `wiki-agent.md`'s Operations section to add the `render.py` rule. Otherwise tell the user and stop.
- **If it does not exist**: proceed with scaffolding below.

## Step 4 — Create these files

| File | Notes |
| --- | --- |
| `wiki-agent.md` | Agent operating manual — see Schema sections below. This is the wiki's source of truth; all agent instructions live here. |
| `{your schema file}` | Pointer file for your platform — see Step 2. Append if exists; create if not. |
| `CONVENTIONS.md` | Copy from skill bundle (`skills/wikime/_templates/CONVENTIONS.md`); fill in `{WIKI_NAME}`, `{REPO_NAME}`, and the `{PRIMARY_TYPES}` table (one row per primary type — name, slug, one-line description) |
| `README.md` | Quick start, operations cheat sheet, directory structure, useful commands, Scripts & Tooling section |
| `index.md` | Empty catalog with a commented example showing exact format. For multi-primary-type wikis, use one section per primary type. |
| `log.md` | Seeded: `## [YYYY-MM-DD] init | Created wiki: {files listed}` |
| `_templates/{slug}.md` | Page template — **one per primary type** (e.g. `_templates/policy.md`, `_templates/control.md`, `_templates/article.md`). See Template sections below. |
| `_templates/entity.md` | Entity/concept template — see Entity Template section below |
| `{slug}/` | Empty directory for primary wiki pages — **one per primary type** (e.g. `papers/`, `policies/`, `controls/`, `articles/`). |
| `entities/` | Empty directory for entity and concept pages |
| `sources/` | Empty directory for immutable raw inputs |
| `scripts/render.py` | Copy from skill bundle (`skills/wikime/scripts/render.py`); generates `wiki.html` — single-file reader artifact with eight views (Home, Page, Search, Graph, Risks, Recent changes, Open questions, Entities) |
| `scripts/query.py` | Copy from skill bundle (`skills/wikime/scripts/query.py`); frontmatter queries — `--status`, `--category`, `--type`, `--tag`, `--stale`, `--risks` |
| `scripts/lint.py` | Copy from skill bundle (`skills/wikime/scripts/lint.py`); structural lint — missing sections, frontmatter, broken refs, open risks, index consistency |

The scripts require `pyyaml` and `markdown`. Install via `uv` into a project-local venv — see Step 5.

## wiki-agent.md — required sections

This file is the agent's operating manual. Include all of these:

1. **Directory structure** — annotated tree showing all primary directories (one per primary type, e.g. `policies/`, `controls/`, `articles/` — or just one like `papers/` for a single-type wiki), plus `entities/`, `sources/`, `_templates/`, `scripts/` and the root control files
2. **This Wiki's Page Types** — list each primary type, its slug/directory, and a one-line description; note that the choice of types is a per-wiki decision, not universal
3. **Absolute Rules** — never edit `sources/`; always update `index.md`; always append to `log.md`; every derived page needs `source` in frontmatter; primary pages go in their respective primary directory (the slug matches the type); entity/concept pages go in `entities/`
4. **Operations** — Ingest (ask user: quick or deep before extracting; then follow the completeness protocol below), Query (read index.md first; file substantive answers back as new pages), Update, Lint (structural checks + contradiction scan across all pages + source drift check for pages with fetchable source URLs)

   **Saving sources — by type:**
   - **PDFs**: already a file — move/copy to `sources/` as-is. Do not add a header block.
   - **Confluence pages**: fetch content via the Atlassian MCP tool (`getConfluencePage` with `contentFormat: "markdown"`), write to `sources/` as a `.md` file (e.g. `sources/page-title-YYYY-MM-DD.md`), and prepend the Source File Header Block with the Confluence URL.
   - **Other web pages / markdown / pastes**: write to `sources/` as a `.md` file and prepend the Source File Header Block.

   **After every ingest, run `python3 scripts/lint.py`** and report findings before declaring done.

   **After every ingest, also run `python3 scripts/render.py`** to regenerate `wiki.html`. The artifact must always reflect the current state of the wiki — this is non-optional.

   **Ingest completeness protocol (deep):**
   - **ToC first**: For any structured document (paper, standard, report, spec), extract or identify the table of contents before writing the wiki page. Use it as a checklist.
   - **Text-first, multimodal-selective for large sources**: For PDFs of ~50+ pages, default to extracting the full text with a CLI tool (e.g. `pdftotext -layout <file> /tmp/<slug>.txt`) and reading that as the spine. Only use the multimodal `Read` tool on pages that carry a load-bearing figure or table the extracted text can't represent (charts with quantitative data, structured diagrams, key tables). Multimodal-everywhere on a large source burns context window for diminishing returns — the text carries the bulk of the analysis; multimodal is for the visuals that matter. For shorter sources (< 50 pages), multimodal end-to-end is fine.
   - **Account for every section**: For each section in the ToC, either capture it with appropriate detail OR explicitly note it is excluded and why (e.g., boilerplate, reference list, glossary). Silence is not acceptable — a section that is skipped without acknowledgement is an error.
   - **Appendices are first-class**: Never treat appendices as peripheral. In technical standards and research papers, appendices frequently contain the most operationally useful content (actor task breakdowns, threat enumeration, design rationale). Read and extract them as carefully as the main body.
   - **Template structure ≠ coverage ceiling**: The page template provides format guidance, not a coverage limit. A filled-in template with thin one-liners is worse than a longer page that captures actual content. If the document's sections don't map to template sections, add new sections to the page — do not compress distinct content into an ill-fitting template bucket.
   - **Scale check**: Before declaring an ingest done, ask: does the output reflect the depth of the source? A 40-page document should produce substantially more than 100 lines of wiki content. If the ratio seems wrong, re-read and expand.
   - **Cover+chapter split for very large sources**: If a single wiki page from this ingest would exceed ~1000 lines, split into a cover note + chapter notes per §8a. Don't split trivially — short sources stay as a single page.
   - **Completeness gate**: Before writing the final log entry and declaring done, compare the document's ToC against what was captured. Any uncovered section must be either added or explicitly excluded with a reason.

   **Ingest completeness protocol (quick):**
   - Capture: title, abstract or executive summary, key claims (≤5 bullets), and threat model or attack surface if present.
   - Note explicitly in the page what was not extracted, so a future deep ingest knows what to add.

   After creating the wiki page, scan for entities/concepts and create/update entity pages automatically.
5. **File Naming** — source files: kebab-case with ID if one exists; primary wiki pages: `{slug}/{id}-{title}.md` or `{slug}/{title}.md` where `{slug}` is the primary directory for that type (each primary type has its own); entity/concept pages: `entities/{title}.md`; all cross-page links use wiki-root-relative paths (e.g. `./policies/data-retention.md`, `./entities/openai.md`); `mentioned_in` frontmatter values also use wiki-root-relative paths
6. **Source File Header Block** — immutability header template (source type, URL, fetched date, do-not-edit warning)
7. **Risk Register Format** — table with Likelihood/Impact/Mitigation/Status; status reflects design clarity not build status
8. **Wiki Page Frontmatter** — YAML schema: title, category, status, owner, source (optional), cover (optional — chapter notes only, see §8a), type (optional — see §8b), tags, created, last_reviewed

   **§8a — Multi-chapter cover pattern.** When a single source is too large for one wiki page (50+ pages with multiple distinct chapters), split into a **cover note + chapter notes**. The cover note is a regular page in its primary directory with the standard frontmatter (no `cover:` field) — its body holds source overview, cross-cutting key findings, methodology summary, and a *Chapters* section linking to chapter notes. Each chapter note in the same primary directory carries `cover: ./{slug}/<cover-note>.md` in frontmatter and a body that deep-dives one chapter. All notes (cover + chapters) **share the same `source:` value** and belong to the same primary type/directory. Naming: `{source-slug}.md` for the cover, `{source-slug}-{chapter-slug}.md` for each chapter (keeps everything sorted together in `ls`). In `index.md`, chapters indent under the cover via 2-space markdown nesting. Only use this pattern for genuinely large sources where a single ~1000+ line page would be unreadable — for shorter sources, keep a single page. Lint enforces: chapter `cover:` target exists, cover and chapter share `source:`, no cover chains (cover can't itself have a cover).

   **§8b — Type field semantics.** The `type:` frontmatter field signals which lint rules apply:
   - `type:` **absent or empty** → pages are treated as the wiki's default primary type. Lint enforces the canonical structural sections (What This Is, How It Works, Risk Register, Prerequisites).
   - `type: entity` or `type: concept` → entity-page rules apply (mandatory: What It Is, How We Use It, Where It Appears).
   - `type: <any other value>` (e.g. `article`, `policy`, `control`, `meta`) → free-form. Lint skips section-presence enforcement; the page's template defines its structure.

   In a multi-primary-type wiki, set `type:` on every primary page so the graph view can colour and filter them distinctly. If you want strict section enforcement on a specific custom type, lift that type's sections into the template and rely on the template (not lint) for that discipline.
9. **index.md Format** — one row per page; for multi-primary-type wikis, use one top-level section per primary type (e.g. `## Policies`, `## Controls`, `## Articles`, `## Entities & Concepts`), with sub-grouping by category inside each section. Links use wiki-root-relative paths (e.g. `[title](./policies/data-retention.md)`, `[title](./entities/openai.md)`). Focus summaries on what it does, not what it is.
10. **log.md Format** — `## [YYYY-MM-DD] action | detail`; grep-able; append-only
11. **Entity and Concept Pages** — `type: entity | concept` frontmatter field; `mentioned_in: []` backlink list (filenames); mandatory sections: What It Is, How We Use It, Where It Appears; optional: Cross-Cutting Risks, Key References; created automatically during Ingest for any tool/platform/pattern central to how the page works
12. **Open Questions** — when a page contains an unresolved thread, mark it with the blockquote convention `> **Open question:** <text>`. The render script aggregates these into the Open questions view in `wiki.html`. Use one blockquote per question; one line each. Do not add `Open question:` headers — only the blockquote pattern is recognised.

### Lint checks — include all of these in the schema file's Lint section

- Pages missing any mandatory section. Enforcement depends on the `type:` field — see §8b. In short: untyped pages get strict default-primary checks; `entity`/`concept` get entity checks; any other explicit `type:` value (article, policy, control, meta, …) is free-form.
- Pages missing YAML frontmatter, or frontmatter missing required fields (`title`, `category`, `status`, `owner`, `tags`, `created`, `last_reviewed`)
- Pages with `source` pointing to a file that doesn't exist in `sources/`
- Pages with no `source` frontmatter whose body references `sources/X` (likely an ingest where the agent forgot to set the field)
- Body markdown links whose target `.md` file does not exist anywhere in the wiki tree (broken refs, typos, links to deleted pages)
- Risk Register rows with status `🔲 Not yet addressed` — flag explicitly
- Files in `sources/` with no corresponding wiki page
- Files not listed in `index.md`
- `index.md` entries pointing to files that don't exist
- Entity/concept pages missing mandatory sections (What It Is, How We Use It, Where It Appears)
- Entity/concept pages with `mentioned_in` entries pointing to files that don't exist
- Chapter notes (those with a `cover:` field) whose `cover:` target doesn't exist
- Cover chains — a cover note cannot itself have a `cover:` field (no nested chapter-of-chapter relationships)
- Cover/chapter `source:` mismatch — a cover note and its chapter notes must share the same `source:` value
- **Contradiction scan** — read all wiki pages together and flag factual contradictions: conflicting claims about the same tool, service, pattern, credential approach, or behaviour. Report as: "`page-a.md` claims X; `page-b.md` claims Y — conflict on Z." Only flag genuine contradictions, not differences in scope or context.
- **Source drift** — for any wiki page whose source file contains a fetchable URL, re-fetch it and compare to the saved content in `sources/`. Flag pages where the live source has changed substantially since last ingest. Skip sources with no URL (pasted text, local docs, meeting notes).

Report all findings as a markdown checklist. Do not auto-fix — report and let the user decide.

**Parameterise**: use the domain and primary types from Step 1 throughout. Do NOT carry over domain-specific categories from any existing wiki — replace category examples with generic placeholders like `Category A | Category B`. If the wiki has multiple primary types, every place this manual references "the primary directory" or "the primary template" expands to one-per-type (one directory, one template, one index section, one `type:` value).

## Templates — required sections

Create **one template per primary type** in `_templates/{slug}.md`. Each template starts with the same YAML frontmatter block (title, category, status, owner, source, type [for non-default types, see §8b], tags, created, last_reviewed), then defines the sections that fit that type.

For the **default primary template** (used when the wiki has one untyped primary, or for the type that doesn't set a `type:` value):

- **Mandatory**: What This Is, How It Works, Risk Register, Prerequisites
- **Optional** (commented out): add domain-appropriate sections based on the wiki's topic

For **custom-typed templates** (any template whose pages set `type:` to something other than entity/concept) — lint won't enforce sections, so the template's job is to make the right shape obvious. Suggested patterns:

- **Policy** template — Purpose, Scope, Policy Statement, Roles & Responsibilities, Enforcement, Exceptions, Related Controls
- **Control** template — What It Does, Implementation, Owner, Evidence / Audit Trail, Testing Cadence, Related Policies
- **Article** template — Summary, Body (free-form), Related Pages, References

These are starting points — adapt to the wiki's domain. The point is each primary type gets a shape that fits the kind of artefact it represents, not a one-size-fits-all skeleton.

## Entity Template — required sections

YAML frontmatter block (title, type: entity|concept, category: Entities & Concepts, status, owner, tags, mentioned_in: [], created, last_reviewed), then:

- **Mandatory**: What It Is, How We Use It, Where It Appears (table: wiki page → role)
- **Optional** (commented out): Cross-Cutting Risks, Key References

## Step 5 — After scaffolding

- Install dependencies into a project-local venv with `uv`. **Always use `uv venv` — never install into the system or user Python with `pip install --user`, `--break-system-packages`, or unmanaged global pip.** The venv stays inside the wiki directory so the install is fully scoped to this project.

  ```bash
  uv venv                                    # creates .venv/ in the wiki dir
  uv pip install pyyaml markdown             # installs into .venv automatically
  ```

  Then run the scripts via `.venv/bin/python3` (or `source .venv/bin/activate` once per shell):

  ```bash
  .venv/bin/python3 scripts/lint.py
  .venv/bin/python3 scripts/render.py
  ```

  Add `.venv/` to `.gitignore` when you run `git init`.

  If `uv` is not installed on the system, ask the user before falling back — do not silently install into system or user Python.
- Ensure `README.md` includes a `## Scripts & Tooling` section with all three commands and what each produces:
  - `python3 scripts/lint.py` → structural health check
  - `python3 scripts/query.py --help` → frontmatter query filters
  - `python3 scripts/render.py` → generates `wiki.html` (open in browser, or view as a Claude artifact)
- Offer `git init && echo '.env' >> .gitignore` if this looks like a standalone repo
- Confirm page type and categories look right before the user adds their first page
