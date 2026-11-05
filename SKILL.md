---
name: wikime
version: 2.0.0
description: Use when user runs /wikime or asks to scaffold a new wiki, knowledge base, or LLM-maintained document store in an empty or fresh directory
---

# Wiki Scaffold

Create a purpose-aware fresh wiki, then hand ongoing work to its generated
`wiki-agent.md`. The canonical `llm-wiki` runtime owns diagnosis and migration.

## 1. Check the destination before any write

Resolve the requested wiki directory and this skill's bundle root (the real
directory containing this `SKILL.md`, including when installed by symlink).
All source paths below are relative to that bundle root, never the target wiki
or an assumed `skills/wikime/` directory.

If the destination contains `wiki-agent.md`, it is an existing wiki: leave every
file untouched, including agent pointer files. Read its manual and route the
request to the runtime operations below; stop fresh scaffolding. A partial wiki
or a collision with any proposed scaffold file also needs an explicit target
or reconciliation decision before creation. Preserve unrelated project files.

For existing-wiki diagnosis, use the installed `llm-wiki` executable (including
its project-local `.venv/bin/llm-wiki` when appropriate):

```text
llm-wiki doctor --wiki <target>
llm-wiki migrate inspect --wiki <target>
llm-wiki migrate dry-run --wiki <target>
```

These are read-only. Show the relevant findings and plan; do not apply it as
part of scaffolding. The actual apply, verify and receipt-based rollback flow
lives in [Migration and rollback](docs/context-compiler.md#migration-and-rollback).
Apply needs explicit user consent and the exact dry-run hash; rollback needs the
receipt ID. Runtime migration changes only `.llm-wiki.toml` and the query/graph
adapters. Page metadata, source backfills, manual changes and legacy render
conversion require separately authorized work under the target manual; they
are not capabilities of that migration command. If the runtime is unavailable,
report the missing route instead of copying scripts or editing content.

## 2. Establish purpose and page types

Obtain both answers before scaffolding; use answers already given by the user.
Ask missing answers together:

1. **What is this wiki for?** Domain, topic, team or agent purpose.
2. **What are its primary page types?** Suggest types suited to that purpose,
   such as papers for research, runbooks for operations, decisions/specs for
   product work, or self-model/work-history for an agent Brain.

Multiple primary types share `entities/`, `sources/`, tooling, index and log.
Each primary type gets its own directory, template and index section. Choose
useful h2 sections from the purpose; do not copy another wiki's categories or
force every type into a generic article or policy template.

## 3. Create the agreed scaffold

Read and adapt [_templates/CONVENTIONS.md](_templates/CONVENTIONS.md). Fill its
`{WIKI_NAME}`, `{REPO_NAME}` and `{PRIMARY_TYPES_TABLE}` placeholders; replace
example categories, paths and folder-tree placeholders with the agreed types.
It supplies the detailed naming, source/evidence, structural and eval rules for
the generated wiki. Keep binary source files intact; their extracted text or
evidence packs are separate immutable files.

| Destination | Source or generation rule |
| --- | --- |
| `CONVENTIONS.md` | Purpose-adapted bundle template above. |
| `.llm-wiki.toml` | Copy [_templates/llm-wiki.toml](_templates/llm-wiki.toml): schema 1, runtime contract 3, five local providers. |
| `wiki-agent.md` | Generate the operating authority described below. |
| Agent pointer file | Append the pointer below once, preserving existing content; create it if absent. |
| `README.md` | Purpose, directory map, and `Scripts & Tooling` quick start using the installed commands. |
| `index.md` | Root YAML `okf_version: "0.2"`, then an empty catalog section per primary type plus entities; show a commented bullet-link-and-description example. |
| `log.md` | Initial `## [YYYY-MM-DD] init | Created wiki: ...` entry listing created files. |
| `_templates/{type}.md` | One purpose-specific page template per primary type. |
| `_templates/entity.md` | Entity/concept template described below. |
| Primary directories, `entities/`, `sources/` | Empty content directories. |
| `scripts/query.py`, `scripts/wiki_graph.py` | Copy the matching thin files from [_templates/adapters/](_templates/adapters/). |
| `scripts/lint.py`, `scripts/render.py`, `scripts/eval.py` | Copy the matching files from [scripts/](scripts/). |

For Codex, append to `AGENTS.md`: `This folder contains a wiki. All agent
instructions are in wiki-agent.md and must be adhered to.` Use the same pointer
in `GEMINI.md` for Gemini CLI; Claude Code uses `@wiki-agent.md` in `CLAUDE.md`.
Create only the current host's pointer. An existing pointer file is the sole
append exception to the scaffold's no-overwrite boundary.

Primary templates need `title`, `category`, `status: draft`, `owner`, `tags`,
`created`, `last_reviewed`, `type`, and `description` frontmatter. Pre-fill the
chosen `type` so it matches `_templates/{type}.md`; the template's h2 headings
are its lint-enforced sections. Derived pages declare `sources` entries such as
`{resource: /sources/actual-file.md}`. Fill date fields with actual dates.

Generation is prospective: add `generated` only with the actual `by` actor and
optional timezone-aware `at`. Add `verified` only for actual identified checks;
set `stale_after` only for an intentional timezone-aware review deadline. Omit
unsupported provenance instead of deriving it from owner, review date, Git
identity, or migration time. Preserve legacy `timestamp`, `source`, and
`evidence` extensions when updating an existing wiki. Follow the adapted
conventions for grounding and conformant export.

The entity template uses the same applicable metadata, `type: entity`,
`category: Entities & Concepts`, and `mentioned_in: []`. Its required sections
are `What It Is`, `How We Use It`, and `Where It Appears`; concept pages use
`type: concept`. Use the conventions' attention/open-question blockquotes where
useful; reserve full risk tables for page types that need them.

Generate `wiki-agent.md` with the purpose, directory/type map and required
sections, and make the local `CONVENTIONS.md` its reference for detailed rules.
State these operating boundaries once in the manual:

- Preserve immutable `sources/`; derived claims retain their source/evidence
  references. Maintain `index.md` and append `log.md` when wiki content changes.
- Read this manual before mutation. User authority and the target wiki's
  stewardship rules govern content writes; runtime diagnostics and maintenance
  proposals do not grant permission to change pages.
- Use standalone `wiki_compile_context` or `llm-wiki compile-context` for question-shaped
  retrieval over local page sections, authored links and declared sources.
  Inspect advisory trust/freshness and coverage/degradation limits. Retrieval
  rank grants no authority. Use the local templates and conventions for ingest/update work.
- Run structural lint, render and diff review after wiki writes. Lint checks
  structure and references; it does not establish semantic consistency or
  detect live source drift. Those need an explicit semantic/source review.
- Keep live judge eval opt-in and risk-triggered. Follow the local conventions'
  zero-model preview (`--plan-judge-calls`), explicit call cap
  (`--max-judge-calls`), candidate scope (`--changed-since`), and single
  deliberate final-gate rules. Routine initialization and page/index/log
  maintenance do not need a judge.

The generated manual owns ongoing operations; keep the scaffold procedure in
this skill.

## 4. Verify and attach the fresh wiki

Use `uv venv` in the new wiki and install the canonical package into that local
`.venv` with `uv pip install <resolved-bundle-root>`. Use the actual bundle path
for installation, not a hard-coded personal path in generated documentation.
If `uv` is unavailable, ask before changing the environment or install method.
Judge-specific dependencies are only needed for a separately requested judge.

Run `.venv/bin/python3 scripts/lint.py` and
`.venv/bin/python3 scripts/render.py`. Report unresolved findings; a structural
pass is not semantic validation. Put those commands and query/eval help routes
in the generated README, pointing to the local manual/conventions for detail.

If `wiki_register` is available in the current runtime with its own configured
`LLM_WIKI_HOME`, register the fresh wiki there using a directory-derived alias
(ask only if ambiguous). Preserve host-separated registry ownership. If the
route is unavailable, report that the wiki is created but not attached to MCP;
do not edit another host's home or configure a new registry implicitly.

Offer Git initialization only for a standalone wiki when not already authorized;
include `.venv/`, `.eval/` and `.env` in its ignore rules. Confirm the chosen
types/categories with the user before adding their first content page. Report
the created location, lint/render result and registration state.
