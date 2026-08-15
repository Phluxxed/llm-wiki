# {WIKI_NAME} — Conventions

This wiki declares its shared runtime contract in `.llm-wiki.toml`. `scripts/query.py` and `scripts/wiki_graph.py` are thin compatibility adapters; traversal and context compilation live in the installed canonical `llm-wiki` package.

## Primary Page Types

This wiki has the following primary page types. Each gets its own directory, its own template, and its own section in `index.md`.

{PRIMARY_TYPES_TABLE}

> Example row format:
> | Slug | Type name | What it captures |
> | --- | --- | --- |
> | `policies/` | Policy | Formal policy documents with statement, scope, and enforcement |
> | `controls/` | Control | Operational controls implementing one or more policies |
> | `articles/` | Article | Explanatory or background content; less formal than policies/controls |

## File Naming

| Page kind | Location | Filename pattern | Example |
| --- | --- | --- | --- |
| Primary page | `{slug}/` (one of the primary directories above) | `{kebab-case-title}.md` or `{ID}-{kebab-case-title}.md` | `policies/data-retention.md` |
| Entity / concept | `entities/` | `{kebab-case-title}.md` | `entities/openai.md` |

Links between pages use wiki-root-relative paths: `[title](./policies/data-retention.md)` or `[title](./entities/openai.md)`. Use this format in both inline links and `mentioned_in` frontmatter values.

## Folder Structure

```
{REPO_NAME}/
├── CONVENTIONS.md             ← this file
├── index.md                   ← one-liner index of all wiki pages, grouped by primary type
├── log.md                     ← append-only change history
├── {slug-1}/                  ← primary type #1 (e.g. policies/)
│   └── {pages}.md
├── {slug-2}/                  ← primary type #2 (e.g. controls/)
│   └── {pages}.md
├── {slug-N}/                  ← additional primary types as needed
│   └── {pages}.md
├── entities/                  ← entity and concept pages
│   └── {entity-files}.md
├── _templates/
│   ├── {slug-1}.md            ← template for new primary type #1
│   ├── {slug-2}.md            ← template for new primary type #2
│   ├── …                      ← one template per primary type
│   └── entity.md              ← template for entity/concept pages
├── sources/                   ← immutable raw inputs (never edited after saving)
│   └── {source-files}         ← raw inputs: docs, threads, notes, exports, etc.
└── scripts/
```

> For a single-primary-type wiki, this collapses to one `{slug}/` directory and one `_templates/{slug}.md`.

## Temporal claim revisions

Pages may carry an append-only `temporal_claim_revisions` frontmatter list when
a target-wiki Steward has accepted a durable temporal decision. The list is
validated by `llm-wiki` and is never written by `llm-wiki` itself. Every entry
has exactly these fields:

```yaml
temporal_claim_revisions:
  - contract_version: temporal-claim-revision/1
    revision_id: temporal-revision:sha256:<64 lowercase hex>
    claim_key: temporal-claim:sha256:<64 lowercase hex>
    claim_scope: default
    decision: accept # accept | retire | supersede | contradict | qualify
    subject: {kind: resolved_page, page: entities/example.md}
    predicate: status:has_state
    object: {kind: literal, datatype: type:text, value: ready}
    world_validity:
      from: {kind: known, value: 2026-01-01}
      to: {kind: open}
    recorded_at: 2026-08-10T00:00:00Z
    candidate_ids: [temporal-candidate:sha256:<64 lowercase hex>]
    observation_ids: [temporal-observation:sha256:<64 lowercase hex>]
    retires_revision_ids: []
    supersedes_revision_ids: []
    contradicts_revision_ids: []
    qualification_of_revision_ids: []
    steward_evidence_refs: [sources/example.md]
    authority: target_wiki_steward
```

The Steward appends entries in non-decreasing `recorded_at` order and never
edits an earlier entry. IDs and evidence references are sorted and
deduplicated inside an entry; relation targets must already occur in the same
page history. `accept` has no targets. `retire`, `supersede`, `contradict`, and
`qualify` use only their matching relation array. Close and contradiction
targets share the claim key; qualification targets may differ. A literal
subject, raw evidence payload, non-Steward authority, malformed ID, or
oversized entry/history is rejected.

For an accepted revision, snapshot mutable external evidence under `sources/`
before appending the revision. Keep page prose consistent with the folded
state, then update `index.md` and append `log.md` as usual. Run the target
wiki's lint and render commands. `llm-wiki` only parses and folds this authored
state; it does not edit pages, sources, indexes, logs, or model/API state.

## Sources Layer

`sources/` holds raw, unmodified inputs and immutable grounding-evidence packs. These are never edited.

Every source file must start with this header block:

```
> **Source type:** {describe what this is — ticket, doc, thread, meeting notes, etc.}
> **URL:** (if applicable)
> **Fetched:** YYYY-MM-DD
> ⚠️ Do not edit this file. Edit the derived wiki page instead.
```

Not every wiki page needs a source file — authored pages are fine without one. But anything derived from an external artifact must have one.

When adding a new source:

1. Save it to `sources/` with a descriptive filename
2. Decide which primary type the derived page belongs to and use that template
3. Set `source: sources/filename.md` in the wiki page frontmatter

If `source:` is only an identity/URL manifest, also set `source_mode: manifest`. Manifest and binary sources require judge-readable evidence: persist extracted text or a claim-complete evidence pack in `sources/` and set `evidence:` to one path or a YAML list of paths. Evidence packs must identify the inspected revision/sections and contain enough source material to support every factual claim in the derived page; URLs or an agent-authored summary alone are not evidence. Keep each page's combined grounding material within the evaluator's 48,000-character budget; curate larger extractions into bounded, claim-complete packs because eval fails oversized bundles instead of silently truncating them.

```yaml
source: sources/example-repo-manifest.md
source_mode: manifest
evidence:
  - sources/example-repo-evidence.md
```

Lint checks that evidence paths exist and treats them as referenced source files. Judge eval fails before spending model calls when a manifest or binary source lacks readable evidence.

## Templates

Copy the matching `_templates/{slug}.md` as the starting point for a new page of that type. Each primary template defines the structural sections suited to its type. Those h2 sections are lint-enforced for pages with the matching `type:`.

## Keeping the Wiki Healthy

When adding or significantly updating a page:

1. Update `index.md` — add or revise the one-liner for that page under the right primary-type section
2. Append a row to `log.md` — `## [YYYY-MM-DD] action | detail`
3. Add a `Related` link in the new page if it connects to an existing one
4. Re-run `.venv/bin/python3 scripts/render.py` to refresh `wiki.html` so the reader reflects the change

### Open Questions

When a page raises something unresolved, mark it with a blockquote:

```markdown
> **Open question:** Does the rate limit reset on a sliding window or fixed window?
```

The render script aggregates these into the Open questions tab of `wiki.html`. Use one blockquote per question.

### Attention Items

When a page carries an operational warning that should surface globally, use one of these one-line blockquotes:

```markdown
> **Risk:** This may break if the MCP registry changes.
> **Caveat:** This only applies to local stdio MCP.
> **Failure mode:** Agents skip the contract loader and edit from stale assumptions.
```

The render/query tooling surfaces these alongside open risk-register rows. Use a full Risk Register table only when likelihood, impact, mitigation, and status are worth tracking.

### Mandatory Sections by Type

The `type:` field is **required** (it's OKF's one mandatory field) and doubles as a colour/filter/grouping signal. It's auto-defaulted from the page's primary directory slug (`policies/` → `type: policy`), so it's set on every page without manual effort. Primary page section enforcement comes from the matching generated template:

| `type:` value | Mandatory sections (enforced by lint) |
| --- | --- |
| any primary value with `_templates/{type}.md` | the h2 sections in that template |
| any primary value without a matching template | legacy fallback: What This Is, How It Works, Risk Register, Prerequisites |
| `entity` / `concept` | What It Is, How We Use It, Where It Appears |
| `meta` (or `category:` containing "meta") | none — free-form; for changelogs, archive indices, and other legitimately unstructured pages |

Newly scaffolded wikis should have a matching template for every primary type. The fallback exists for older wikis and accidental missing templates.

## OKF Conformance

This wiki is a conformant **[Open Knowledge Format (OKF) v0.1](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)** bundle — a directory of markdown files with YAML frontmatter — held to a *strict superset* of the spec: every page is valid OKF, but lint enforces more than OKF requires. In practice this means any OKF-aware tool can read and write this wiki without bespoke integration.

What conformance adds to every page's frontmatter, on top of the existing fields:

| Field | Meaning | Notes |
| --- | --- | --- |
| `type` | OKF's required routing field; also our colour/filter signal | Auto-defaulted from the primary directory slug |
| `description` | One-line summary | Used by index entries, search snippets, previews |
| `timestamp` | ISO 8601 datetime of last meaningful change | Distinct from `created` (creation) and `last_reviewed` (last human review) |

Plus: the root `index.md` declares `okf_version: "0.1"`, and every non-reserved `.md` file has a parseable frontmatter block. Lint checks all of this.

## Risk Register Status Legend

| Symbol | Meaning |
| --- | --- |
| ✅ Handled | Well-understood mitigation; low design effort required |
| ⚠️ Action required | Needs deliberate design attention for this use case |
| 🔲 Not yet addressed | Open question; no clear mitigation identified yet |

## Scripts & Tooling

| Script | Command | Output |
| --- | --- | --- |
| `scripts/lint.py` | `.venv/bin/python3 scripts/lint.py` | Structural health check — template-required sections, broken refs, open risks, index consistency |
| `scripts/query.py` | `.venv/bin/python3 scripts/query.py --status Draft --json` | Frontmatter queries — filter by `--status`, `--category`, `--type`, `--tag`, `--stale`, `--risks`; add `--json` for agents |
| `scripts/query.py` | `.venv/bin/python3 scripts/query.py --agent-overview --json` | Agent graph overview — hubs, orphans, unresolved risks/questions, and recent log context |
| `scripts/query.py` | `.venv/bin/python3 scripts/query.py --context-pack <page> --json` | Deterministic agent context pack with inclusion reasons |
| `scripts/render.py` | `.venv/bin/python3 scripts/render.py` | Generates `wiki.html` — single-file reader with Home / Page / Search / Graph / Risks / Recent changes / Open questions / Entities views |
| `scripts/eval.py` | `.venv/bin/python3 scripts/eval.py --judge none` | Deterministic structural eval plus optional judging brief. Live judge runs require an explicit hard call cap; usage is recorded in `.eval/` |

Requires a project-local `.venv/` with the canonical package installed from the current skill bundle or release checkout: `uv venv && uv pip install /path/to/llm-wiki`. Do not hard-code a user-specific checkout path into the wiki. The eval's **claude** judge also needs `claude-agent-sdk` (skip if you use `--judge codex` or `--judge none`).

Routine wiki updates run lint, render, and diff review; they do not invoke a live judge. For a genuinely high-risk candidate, preview first with `.venv/bin/python3 scripts/eval.py --plan-judge-calls --changed-since HEAD` (zero model calls). Use `--metric <name> --max-judge-calls <small-N>` only for a bounded iteration question. Run one complete final gate with `.venv/bin/python3 scripts/eval.py --judge <owner> --gate --changed-since HEAD --max-judge-calls <N>`, choosing `N` from the preview plus only deliberate retry headroom. Never run an unscoped audit or repeat the full gate automatically.
