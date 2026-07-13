---
name: wikime
version: 2.0.0
description: Use when user runs /wikime or asks to scaffold a new wiki, knowledge base, or LLM-maintained document store in an empty or fresh directory
---

# Wiki Scaffold (Karpathy LLM Wiki Pattern)

## Overview

Scaffolds a persistent, LLM-maintained wiki. Two questions first, then create all files in one pass.

Wikis produced by this skill are **conformant [Open Knowledge Format (OKF) v0.1](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) bundles** — a directory of markdown files with YAML frontmatter — as a *strict superset*: every page is valid OKF, but the lint enforces more than OKF requires (mandatory sections, full frontmatter, broken-ref checks). This means a wiki this skill produces can be read and written by any OKF-aware tooling with no bespoke integration, while staying stricter and more curated than bulk-generated OKF.

This skill is **versioned with semver** (see `version:` above): major = a breaking change to the generated-wiki schema, minor = additive, patch = fixes. `1.0.0` is the first OKF-conformant release; older wikis (no `type`/`description`/`timestamp` on every page) predate it — see the migration check in Step 3.

## Step 1 — Ask before touching any files

In a single message, ask:
1. **What is this wiki for?** (one sentence — domain, topic, team)
2. **What is the primary page type — or types?** A wiki can have one primary type or several (one directory + purpose-aware template per type). Suggest based on domain:
   - Use cases, integrations, automations → `use cases`
   - Research, papers, literature → `papers`
   - ML training runs → `experiments`
   - Ops / SRE → `runbooks`
   - Architecture / product decisions → `ADRs`
   - Governance / compliance → often a mix: `policies` + `controls` + `articles`
   - Product → often a mix: `decisions` (ADRs) + `specs`

   Multiple primary types are first-class: each gets its own directory (`policies/`, `controls/`, `articles/`, …) and its own template, but they share `entities/`, `sources/`, `_templates/`, the index, the log, and the lint/render tooling.

Use the answer to design the templates. Do not force every wiki into a generic article/policy shape: an agent Brain, research wiki, policy/control wiki, runbook wiki, and product/spec wiki should get different primary templates.

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

- **If it exists**: this is an existing wiki. Do not overwrite anything. Run the migration checks below, then tell the user and stop.
  - **render.py migration**: if `scripts/graph.py` or `graph.html` exists, this wiki predates the `render.py` change. Offer the user a one-line migration: replace `scripts/graph.py` with the current `skills/wikime/scripts/render.py`, delete `graph.html`, ensure the wiki has its project-local `.venv` from Step 5, then run `.venv/bin/python3 scripts/render.py`. After confirming, also update `wiki-agent.md`'s Operations section to add the `render.py` rule.
  - **OKF conformance migration (pre-1.0.0 wikis)**: run `.venv/bin/python3 scripts/lint.py` (after copying in the current `scripts/lint.py`). If pages are flagged for missing `type`, `description`, or `timestamp`, or `index.md` lacks `okf_version`, this wiki predates the OKF-conformance release. Offer the user a backfill: set each page's `type` from its primary directory slug (`policies/` → `type: policy`; entity/concept pages keep `type: entity|concept`); add `okf_version: "0.1"` to the top of `index.md`; and for `description`/`timestamp`, propose values (description from the page's lead sentence, timestamp from `last_reviewed` or the file's git mtime) for the user to confirm rather than inventing silently. Re-run lint after backfilling. Do not edit pages without the user's go-ahead.
  - **Versioned runtime migration**: run `llm-wiki doctor --wiki .`, then `llm-wiki migrate dry-run --wiki .`. Show the exact plan and blockers. Do not apply it automatically. Only after explicit user consent, run `llm-wiki migrate apply --wiki . --plan-hash <hash-from-dry-run>`, followed by `llm-wiki migrate verify --wiki .`. The migration must not alter wiki pages, sources, index entries, or log history; its receipt provides the exact ID accepted by `llm-wiki migrate rollback`.
- **If it does not exist**: proceed with scaffolding below.

## Step 4 — Create these files

| File | Notes |
| --- | --- |
| `wiki-agent.md` | Agent operating manual — see Schema sections below. This is the wiki's source of truth; all agent instructions live here. |
| `{your schema file}` | Pointer file for your platform — see Step 2. Append if exists; create if not. |
| `CONVENTIONS.md` | Copy from skill bundle (`skills/wikime/_templates/CONVENTIONS.md`); fill in `{WIKI_NAME}`, `{REPO_NAME}`, and the `{PRIMARY_TYPES}` table (one row per primary type — name, slug, one-line description) |
| `README.md` | Quick start, operations cheat sheet, directory structure, useful commands, Scripts & Tooling section |
| `.llm-wiki.toml` | Copy from `skills/wikime/_templates/llm-wiki.toml`; declares the supported schema/runtime contract and canonical compiler defaults. |
| `index.md` | Catalog. Starts with a root `okf_version: "0.1"` frontmatter block (see §9), then an empty catalog with a commented example showing exact format. For multi-primary-type wikis, use one section per primary type. |
| `log.md` | Seeded: `## [YYYY-MM-DD] init | Created wiki: {files listed}` |
| `_templates/{slug}.md` | Page template — **one per primary type** (e.g. `_templates/policy.md`, `_templates/control.md`, `_templates/article.md`). See Template sections below. |
| `_templates/entity.md` | Entity/concept template — see Entity Template section below |
| `{slug}/` | Empty directory for primary wiki pages — **one per primary type** (e.g. `papers/`, `policies/`, `controls/`, `articles/`). |
| `entities/` | Empty directory for entity and concept pages |
| `sources/` | Empty directory for immutable raw inputs |
| `scripts/wiki_graph.py` | Copy from `skills/wikime/_templates/adapters/wiki_graph.py`; thin compatibility import backed by the canonical `llm-wiki` runtime. |
| `scripts/render.py` | Copy from skill bundle (`skills/wikime/scripts/render.py`); generates `wiki.html` — single-file reader artifact with nine views (Home, Page, Search, Graph, Risks, Recent changes, Open questions, Entities, Sources) |
| `scripts/query.py` | Copy from `skills/wikime/_templates/adapters/query.py`; thin compatibility entrypoint preserving the existing query/context CLI through the canonical runtime. |
| `scripts/lint.py` | Copy from skill bundle (`skills/wikime/scripts/lint.py`); structural lint — missing sections, frontmatter, broken refs, open risks, index consistency |
| `scripts/eval.py` | Copy from skill bundle (`skills/wikime/scripts/eval.py`); risk-triggered LLM-as-judge quality eval — grounding, cross-page contradictions, redundancy, near-duplicate disambiguation; per-metric thresholds + regression gating. Auto-detects the agent CLI (`claude`/`codex`) as a keyless judge; writes run records to `.eval/` |

The scripts require the canonical `llm-wiki` package (which includes `pyyaml` and `markdown`); `eval.py`'s claude judge also needs `claude-agent-sdk`. Install via `uv` into a project-local venv — see Step 5.

## wiki-agent.md — required sections

This file is the agent's operating manual. Include all of these:

1. **Directory structure** — annotated tree showing all primary directories (one per primary type, e.g. `policies/`, `controls/`, `articles/` — or just one like `papers/` for a single-type wiki), plus `entities/`, `sources/`, `_templates/`, `scripts/` and the root control files
2. **This Wiki's Page Types** — list each primary type, its slug/directory, one-line description, and required h2 sections from that type's template; note that the choice of types is a per-wiki decision, not universal
3. **Absolute Rules** — never edit `sources/`; always update `index.md`; always append to `log.md`; every derived page needs `source` in frontmatter; primary pages go in their respective primary directory (the slug matches the type); entity/concept pages go in `entities/`
4. **Operations** — Ingest (ask user: quick or deep before extracting; then follow the completeness protocol below), Query (read index.md first; file substantive answers back as new pages), Update, Lint (structural checks — missing sections, frontmatter, broken refs, grounding-evidence refs, OKF conformance, index consistency), Eval (risk-triggered LLM-as-judge quality audit via `scripts/eval.py`: grounding against source evidence, typed cross-page contradictions, redundancy, boolean near-duplicate disambiguation — with thresholds + regression gating). Lint/render are routine after wiki writes; Eval is reserved for high-risk changes, not every update.

   **Core traversal — `loci` is default-on through the Context Compiler:** Question-shaped reads use the local `loci-mcp` stdio service to find and hydrate the most relevant heading sections without loading whole pages. Frontmatter, graph, source, and lexical providers still contribute routing, state, relationships, provenance, and deterministic fallback.
   - **Use the compiler first**: ask the real question through `wiki_compile_context` or `llm-wiki compile-context`; use direct `loci_*` MCP tools for codebase inspection and retrieval follow-up, not as a second wiki business-logic implementation.
   - **Freshness is automatic on MCP reads**: loci read tools check the indexed file hashes and incrementally refresh changed files before returning results. Explicit indexing is only required for a wiki that has never been indexed.
   - **Degradation is explicit**: if `loci-mcp` is unavailable, the repo is unindexed, or a loci call fails, the compiler returns a structured diagnostic and continues through seed/frontmatter/text/graph/source retrieval. Do not silently claim loci participated.
   - **Authority stays separate**: loci ranking is a traversal signal. It never makes a page current, authoritative, or source-grounded by itself.

   **Saving sources — by type:**
   - **PDFs**: already a file — move/copy to `sources/` as-is. Do not add a header block. Also persist judge-readable extracted text or a claim-complete evidence pack in `sources/` and reference it with `evidence:`; eval never treats decoded PDF bytes as evidence.
   - **Repository or URL manifests**: keep the identity/URL manifest in `sources/`, set `source_mode: manifest`, and persist a claim-complete evidence pack containing the pinned revision plus the inspected files, sections, excerpts, or snapshots that support the derived page. Reference the pack with `evidence:`.
   - **Confluence pages**: fetch content via the Atlassian MCP tool (`getConfluencePage` with `contentFormat: "markdown"`), write to `sources/` as a `.md` file (e.g. `sources/page-title-YYYY-MM-DD.md`), and prepend the Source File Header Block with the Confluence URL.
   - **Other web pages / markdown / pastes**: write to `sources/` as a `.md` file and prepend the Source File Header Block.

   Evidence packs are immutable source artifacts, not wiki pages. They must be sufficiently complete to support every factual claim in the derived page; a list of URLs or a prose summary written from memory is not evidence. `evidence:` accepts one path or a YAML list of paths under `sources/`. Keep each page's combined grounding material within the evaluator's 48,000-character budget; if a full extraction is larger, create a bounded, claim-complete pack rather than relying on silent truncation (eval fails oversized bundles before judging).

   **After every ingest, run `.venv/bin/python3 scripts/lint.py`** and report findings before declaring done.

   **After every ingest, also run `.venv/bin/python3 scripts/render.py`** to regenerate `wiki.html`. The artifact must always reflect the current state of the wiki — this is non-optional.

   **Run `.venv/bin/python3 scripts/eval.py --gate` only for risk-triggered audits**: self-model or operating-rule changes, ownership-boundary changes, major source ingests, rebuilds, suspected contradictions, page merge/split decisions, weak grounding concerns, near-duplicate concept cleanup, or eval tooling changes. Do not run judge eval for routine page/log/index maintenance.

   **Ingest completeness protocol (deep):**
   - **ToC first**: For any structured document (paper, standard, report, spec), extract or identify the table of contents before writing the wiki page. Use it as a checklist.
   - **Text-first, multimodal-selective for large sources**: For PDFs of ~50+ pages, default to extracting the full text with a CLI tool (e.g. `pdftotext -layout <file> /tmp/<slug>.txt`) and reading that as the spine. Only use the multimodal `Read` tool on pages that carry a load-bearing figure or table the extracted text can't represent (charts with quantitative data, structured diagrams, key tables). Multimodal-everywhere on a large source burns context window for diminishing returns — the text carries the bulk of the analysis; multimodal is for the visuals that matter. For shorter sources (< 50 pages), multimodal end-to-end is fine.
   - **Account for every section**: For each section in the ToC, either capture it with appropriate detail OR explicitly note it is excluded and why (e.g., boilerplate, reference list, glossary). Silence is not acceptable — a section that is skipped without acknowledgement is an error.
   - **Appendices are first-class**: Never treat appendices as peripheral. In technical standards and research papers, appendices frequently contain the most operationally useful content (actor task breakdowns, threat enumeration, design rationale). Read and extract them as carefully as the main body.
   - **Template structure ≠ coverage ceiling**: The page template provides format guidance, not a coverage limit. A filled-in template with thin one-liners is worse than a longer page that captures actual content. If the document's sections don't map to template sections, add new sections to the page — do not compress distinct content into an ill-fitting template bucket.
   - **Scale check**: Before declaring an ingest done, ask: does the output reflect the depth of the source? A 40-page document should produce substantially more than 100 lines of wiki content. If the ratio seems wrong, re-read and expand.
   - **Cover+chapter split for very large sources**: If a single wiki page from this ingest would exceed ~1000 lines, split into a cover note + chapter notes per §8a. Don't split trivially — short sources stay as a single page.
   - **Completeness gate**: Before writing the final log entry and declaring done, compare the document's ToC against what was captured. Any uncovered section must be either added or explicitly excluded with a reason.
   - **Grounding-evidence gate**: Before finishing a deep ingest from a binary source or a source manifest, save the judge-readable extraction/evidence pack and set `evidence:` on every derived page. Use `source_mode: manifest` when `source:` contains identity/linkage rather than the inspected artifact itself.

   **Ingest completeness protocol (quick):**
   - Capture: title, abstract or executive summary, key claims (≤5 bullets), and threat model or attack surface if present.
   - Note explicitly in the page what was not extracted, so a future deep ingest knows what to add.

   After creating the wiki page, scan for entities/concepts and create/update entity pages automatically.
5. **File Naming** — source files: kebab-case with ID if one exists; primary wiki pages: `{slug}/{id}-{title}.md` or `{slug}/{title}.md` where `{slug}` is the primary directory for that type (each primary type has its own); entity/concept pages: `entities/{title}.md`; all cross-page links use wiki-root-relative paths (e.g. `./policies/data-retention.md`, `./entities/openai.md`); `mentioned_in` frontmatter values also use wiki-root-relative paths
6. **Source File Header Block** — immutability header template (source type, URL, fetched date, do-not-edit warning)
7. **Attention Items and Risk Register Format** — for lightweight warnings, use one-line blockquotes: `> **Risk:** <text>`, `> **Caveat:** <text>`, or `> **Failure mode:** <text>`. For formal/detailed pages, a Risk Register table with Likelihood/Impact/Mitigation/Status is still supported; status reflects design clarity not build status.
8. **Wiki Page Frontmatter** — YAML schema. **Required (lint-enforced):** title, category, status, owner, tags, created, last_reviewed, **type** (see §8b), **description** (one-line summary), **timestamp** (ISO 8601 datetime of last meaningful change — distinct from `created` and `last_reviewed`). **Optional:** source (set when the page derives from a `sources/` file), `source_mode: manifest` (when that source is only an identity/URL manifest), evidence (one source path or a list of immutable judge-readable evidence packs; required for manifest and binary sources), cover (chapter notes only, see §8a).

   The last three are the **OKF v0.1 conformance fields** (strict superset): `type` is OKF's one required routing field, `description` its recommended summary, `timestamp` its last-modified marker. `type` is **auto-defaulted from the page's primary directory slug** (a page in `policies/` gets `type: policy`, `papers/` → `type: paper`); entity/concept pages use `type: entity|concept`, meta pages `type: meta`. Because it's always set from the directory, authors rarely write it by hand — but it is required and lint flags its absence.

   **§8a — Multi-chapter cover pattern.** When a single source is too large for one wiki page (50+ pages with multiple distinct chapters), split into a **cover note + chapter notes**. The cover note is a regular page in its primary directory with the standard frontmatter (no `cover:` field) — its body holds source overview, cross-cutting key findings, methodology summary, and a *Chapters* section linking to chapter notes. Each chapter note in the same primary directory carries `cover: ./{slug}/<cover-note>.md` in frontmatter and a body that deep-dives one chapter. All notes (cover + chapters) **share the same `source:` value** and belong to the same primary type/directory. Naming: `{source-slug}.md` for the cover, `{source-slug}-{chapter-slug}.md` for each chapter (keeps everything sorted together in `ls`). In `index.md`, chapters indent under the cover via 2-space markdown nesting. Only use this pattern for genuinely large sources where a single ~1000+ line page would be unreadable — for shorter sources, keep a single page. Lint enforces: chapter `cover:` target exists, cover and chapter share `source:`, no cover chains (cover can't itself have a cover).

   **§8b — Type field semantics.** The `type:` frontmatter field is **required** (it's OKF's one mandatory field) and doubles as a colour/filter/grouping signal for the graph and sidebar. Primary page section checks come from the matching generated template: `type: policy` uses `_templates/policy.md`, `type: work-history` uses `_templates/work-history.md`, and so on. The template's h2 headings are the required sections for that page type. If an older wiki has no matching template, lint falls back to the legacy primary sections (What This Is / How It Works / Risk Register / Prerequisites). `type` is auto-defaulted from the primary directory slug (see §8), so it's set on every page without manual effort.

   Lint behaviour: `type` absent or empty is a **frontmatter error** (it's required). Section enforcement then routes by the field's *value*:
   - `type: entity` or `type: concept` → entity-page rules apply (mandatory: What It Is, How We Use It, Where It Appears).
   - `type: meta` (or `category:` containing "meta") → free-form, no section enforcement. Meta is for changelogs, archive indices, and other legitimately unstructured pages.
   - `type: <anything else>` (`policy`, `control`, `article`, the slug-derived default, …) → section checks from `_templates/{type}.md`; if that template is missing, legacy primary section checks apply.

   In a multi-primary-type wiki, set `type:` on every primary page so the graph view colours and filters them distinctly. Lint then enforces the matching template shape for each type.
9. **index.md Format** — the bundle's progressive-disclosure catalog, conformant with OKF §6. Structure:
   - A **root frontmatter block declaring the OKF version** — `---` / `okf_version: "0.1"` / `---` — at the very top. This is the *only* place OKF permits frontmatter in an index file; lint flags its absence.
   - Then one top-level section per primary type (e.g. `## Policies`, `## Controls`, `## Articles`, `## Entities & Concepts`), with sub-grouping by category inside each section.
   - Each entry is a **bullet link with a trailing description**: `* [title](./policies/data-retention.md) - what it does` (OKF §6 form). Links use wiki-root-relative paths. Focus summaries on what it does, not what it is.
10. **log.md Format** — `## [YYYY-MM-DD] action | detail`; grep-able; append-only
11. **Entity and Concept Pages** — `type: entity | concept` frontmatter field; `mentioned_in: []` backlink list (filenames); mandatory sections: What It Is, How We Use It, Where It Appears; optional: Cross-Cutting Risks, Key References; created automatically during Ingest for any tool/platform/pattern central to how the page works
12. **Open Questions** — when a page contains an unresolved thread, mark it with the blockquote convention `> **Open question:** <text>`. The render script aggregates these into the Open questions view in `wiki.html`. Use one blockquote per question; one line each. Do not add `Open question:` headers — only the blockquote pattern is recognised.
13. **Attention Items** — when a page contains an operational warning that should surface globally, mark it with `> **Risk:** <text>`, `> **Caveat:** <text>`, or `> **Failure mode:** <text>`. Use one blockquote per item; one line each. These are the lightweight default for Brain/agent notes. Use a full Risk Register table only when likelihood/impact/mitigation/status are worth tracking.

### Lint checks — include all of these in the schema file's Lint section

- Pages missing any mandatory section. Enforcement depends on the `type:` field — see §8b. In short: primary pages use required h2 sections from `_templates/{type}.md`; older wikis without a matching template use the legacy four-section fallback; `entity`/`concept` get entity checks; only `type: meta` is free-form.
- Pages missing YAML frontmatter, or frontmatter missing required fields (`title`, `category`, `status`, `owner`, `tags`, `created`, `last_reviewed`, `type`, `description`, `timestamp`)
- **OKF conformance** (§9.1): any non-reserved `.md` file with no parseable frontmatter (these are silently skipped by the page collector, so they get a dedicated scan)
- **OKF conformance** (§11): root `index.md` missing the `okf_version` declaration
- Pages with `source` pointing to a file that doesn't exist in `sources/`
- Pages with `evidence` pointing to a file that doesn't exist in `sources/`
- Pages whose source is binary, or whose `source_mode` is `manifest`, without judge-readable `evidence`
- Pages with no `source` frontmatter whose body references `sources/X` (likely an ingest where the agent forgot to set the field)
- Body markdown links whose target `.md` file does not exist anywhere in the wiki tree (broken refs, typos, links to deleted pages)
- Risk Register rows with status `🔲 Not yet addressed` — flag explicitly. Render/query also surface attention-item blockquotes (`Risk`, `Caveat`, `Failure mode`) alongside open risk rows.
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

Create **one template per primary type** in `_templates/{slug}.md`. Each template starts with the same YAML frontmatter block (title, category, status, owner, source, **type** [defaulted to the directory slug — see §8b], **description**, **timestamp**, tags, created, last_reviewed). Pre-fill `type:` in each template with that type's slug value (e.g. `type: policy` in `_templates/policy.md`) so new pages are OKF-conformant by default. `source_mode` and `evidence` stay optional and should only be added when the source contract requires them; do not pre-fill empty evidence fields.

After the frontmatter, generate h2 sections that fit the wiki's actual purpose and that page type. These h2 headings are lint-enforced for pages with the matching `type:`.

Examples, not fixed packs:

- **Research / papers**: Summary, Key Claims, Evidence, Method Notes, Open Questions.
- **Policy**: Policy Statement, Scope, Responsibilities, Exceptions, Attention Items.
- **Control**: Control Objective, Implementation, Evidence / Audit Trail, Testing Cadence, Failure Modes.
- **Runbook**: When To Use This, Preconditions, Procedure, Rollback, Known Failure Modes.
- **Agent Brain**: Current Orientation, What Changed, Durable Pattern, Corrections, Evidence.

Use the user's setup answer to pick the useful sections. Avoid empty generic headings unless they genuinely fit the page type. Add the lightweight attention blockquote pattern in templates where warnings are expected; use a full Risk Register table only for page types that benefit from it.

## Entity Template — required sections

YAML frontmatter block (title, type: entity|concept, category: Entities & Concepts, status, owner, description, timestamp, tags, mentioned_in: [], created, last_reviewed), then:

- **Mandatory**: What It Is, How We Use It, Where It Appears (table: wiki page → role)
- **Optional** (commented out): Cross-Cutting Risks, Key References

## Step 5 — After scaffolding

- Install dependencies into a project-local venv with `uv`. **Always use `uv venv` — never install into the system or user Python with `pip install --user`, `--break-system-packages`, or unmanaged global pip.** The venv stays inside the wiki directory so the install is fully scoped to this project.

  ```bash
  uv venv                                    # creates .venv/ in the wiki dir
  uv pip install /path/to/llm-wiki           # current skill bundle or release checkout; includes pyyaml + markdown
  uv pip install claude-agent-sdk            # only for eval.py's claude judge (skip if using --judge codex/none)
  ```

  Resolve `/path/to/llm-wiki` to the current skill bundle root before running the command. Do not hard-code one user's home path into the scaffold.

  Then run the scripts via `.venv/bin/python3` (or `source .venv/bin/activate` once per shell):

  ```bash
  .venv/bin/python3 scripts/lint.py
  .venv/bin/python3 scripts/render.py
  ```

  Add `.venv/` and `.eval/` (local eval run records) to `.gitignore` when you run `git init`.

  If `uv` is not installed on the system, ask the user before falling back — do not silently install into system or user Python.
- If the `llm-wiki` MCP server is configured in the current agent runtime, register the newly created wiki with the current runtime only:
  - Use the `wiki_register` MCP tool with an alias derived from the directory name (kebab-case, ask only if ambiguous).
  - Never register the wiki into another agent's home. Codex and Claude registries are intentionally separate.
  - If `wiki_register` is unavailable or `LLM_WIKI_HOME` is not configured, skip registration and say the wiki was created but not attached to MCP. Do not edit `~/.codex/` or `~/.claude/` directly unless the current runtime owns that home.
- Ensure `README.md` includes a `## Scripts & Tooling` section with the commands and what each produces:
  - `.venv/bin/python3 scripts/lint.py` → structural health check
  - `.venv/bin/python3 scripts/query.py --help` → frontmatter query filters and agent graph/context commands; add `--json` for machine-readable agent output
  - `.venv/bin/python3 scripts/query.py --agent-overview --json` → agent-oriented first pass over wiki structure, hubs, orphans, risks, questions, and recent log context
  - `.venv/bin/python3 scripts/query.py --context-pack <page> --tokens 12000 --json` → deterministic working context for an agent, with inclusion reasons
  - `.venv/bin/python3 scripts/render.py` → generates `wiki.html` (open in browser, or view as a Claude artifact)
  - `.venv/bin/python3 scripts/eval.py --gate` → risk-triggered LLM-as-judge quality audit (grounding, contradictions, redundancy, disambiguation) with regression gating; run records in `.eval/`
- Offer `git init && echo '.env' >> .gitignore` if this looks like a standalone repo
- Confirm page type and categories look right before the user adds their first page
