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

   **Optional accelerator — `loci` for inspecting existing notes (never a dependency):** Several operations re-read existing wiki pages — Ingest checks whether a note already covers the incoming material (dedup/route), and Update needs to find the right page and section to change. When a `loci` symbol indexer is available, it can serve just the relevant heading sections instead of loading whole files, which matters for large/cover notes (§8a). Use it as follows, and **fall back to normal `Read` whenever it is absent or unhelpful**:
   - **Front-matter routing stays on `scripts/query.py`** (`--type`, `--tag`, `--category`, …). loci indexes heading sections only, not YAML front-matter, so query.py remains the way to find *which* notes are relevant. loci is purely for reading their bodies more cheaply once identified.
   - **Section-level reads**: if a `loci` command is on PATH and the repo is indexed (`loci index .`), use `loci outline . --file <note>` to get a note's heading tree, then `loci get <id>` to pull only the section you need — a `get` returns the full section body (heading + prose + nested subsections). This avoids reading an entire large note to inspect or update one part.
   - **Fallback is mandatory and silent**: if `loci` is not installed, the repo is not indexed, or any loci call errors or returns nothing, just read the file(s) with `Read` as normal. loci is a token-saving convenience only — the wiki must behave identically without it, and no operation may depend on it.
   - **Keep the index fresh**: loci is read-only and its index goes stale the moment you write a page. After any operation that creates or edits pages (Ingest, Update), re-index so later reads stay accurate — `loci index . --incremental` re-parses only the files that changed (cheap, the default choice), or a plain `loci index .` for a small wiki where a full pass is trivial. Skip this silently if loci isn't in use.
   - **Small notes**: for short pages, reading the whole file is simpler than outline-then-get. Reach for loci on large and cover/chapter notes, where the saving is real.

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

   **§8b — Type field semantics.** The `type:` frontmatter field is a colour/filter/grouping signal for the graph and sidebar — **not** a lint-exemption knob. Every primary page must answer the four load-bearing questions (What This Is / How It Works / Risk Register / Prerequisites), regardless of which custom type it declares. Type-specific content (a policy's Statement, a control's Implementation) sits as h3 nested under the h2 mandatory sections.

   Lint behaviour by type:
   - `type:` **absent or empty** → strict primary section checks.
   - `type: entity` or `type: concept` → entity-page rules apply (mandatory: What It Is, How We Use It, Where It Appears).
   - `type: meta` (or `category:` containing "meta") → free-form, no enforcement. Meta is for changelogs, archive indices, and other legitimately unstructured pages.
   - `type: <anything else>` (`policy`, `control`, `article`, …) → strict primary section checks, same as no-type.

   In a multi-primary-type wiki, set `type:` on every primary page so the graph view colours and filters them distinctly. Lint then enforces the same baseline shape on all of them.
9. **index.md Format** — one row per page; for multi-primary-type wikis, use one top-level section per primary type (e.g. `## Policies`, `## Controls`, `## Articles`, `## Entities & Concepts`), with sub-grouping by category inside each section. Links use wiki-root-relative paths (e.g. `[title](./policies/data-retention.md)`, `[title](./entities/openai.md)`). Focus summaries on what it does, not what it is.
10. **log.md Format** — `## [YYYY-MM-DD] action | detail`; grep-able; append-only
11. **Entity and Concept Pages** — `type: entity | concept` frontmatter field; `mentioned_in: []` backlink list (filenames); mandatory sections: What It Is, How We Use It, Where It Appears; optional: Cross-Cutting Risks, Key References; created automatically during Ingest for any tool/platform/pattern central to how the page works
12. **Open Questions** — when a page contains an unresolved thread, mark it with the blockquote convention `> **Open question:** <text>`. The render script aggregates these into the Open questions view in `wiki.html`. Use one blockquote per question; one line each. Do not add `Open question:` headers — only the blockquote pattern is recognised.

### Lint checks — include all of these in the schema file's Lint section

- Pages missing any mandatory section. Enforcement depends on the `type:` field — see §8b. In short: every primary page (no `type:` or any custom value) gets strict primary checks; `entity`/`concept` get entity checks; only `type: meta` is free-form.
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

Create **one template per primary type** in `_templates/{slug}.md`. Each template starts with the same YAML frontmatter block (title, category, status, owner, source, type [see §8b], tags, created, last_reviewed), then includes the four mandatory h2 sections (What This Is, How It Works, Risk Register, Prerequisites) with type-specific content nested as h3 underneath.

**Universal h2 structure (mandatory across all primary types, lint-enforced):**

- **What This Is** — definition, scope, who/what it applies to
- **How It Works** — the mechanism (organise with type-specific h3s underneath)
- **Risk Register** — table; for articles or non-operational pages, use a single `N/A — explanatory content` row rather than omitting the section
- **Prerequisites** — what must be true first

**Type-specific h3s suggested (template-only, not lint-enforced):**

- **Policy** template — under "How It Works": Policy Statement, Roles & Responsibilities, Enforcement, Exceptions
- **Control** template — under "How It Works": Implementation, Owner, Evidence / Audit Trail, Testing Cadence
- **Article** template — under "How It Works": free-form h3s suited to the topic (Origin, Structure, Practical Implications, etc.)

Additional h2s (Related Policies, Related Controls, References, etc.) sit alongside the four mandatory ones — add them where useful, no constraint on order.

These are starting points — adapt to the wiki's domain. The point is each primary type gets a shape that fits the kind of artefact it represents, *while still answering the four universal questions*.

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
