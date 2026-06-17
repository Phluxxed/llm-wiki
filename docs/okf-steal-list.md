# OKF / Knowledge Catalog Steal List

High-level notes from reviewing Google's [`GoogleCloudPlatform/knowledge-catalog`](https://github.com/GoogleCloudPlatform/knowledge-catalog) against this repo.

Google has **formally specified the format this repo scaffolds**. Their **Open Knowledge Format (OKF) v0.1** is a directory of markdown files with YAML frontmatter — the same Karpathy-LLM-wiki pattern `llm-wiki` produces. Their spec (§10) explicitly names "LLM 'wiki' repositories that use markdown + frontmatter as agent-readable knowledge bases" as the prior art it formalises. This is convergent evolution: the strongest possible validation that this repo's design is right.

Two time horizons matter here, because **we are migrating to GCP in the coming months**:

- **Now (pre-GCP):** treat OKF as a *spec to optionally target*. The win is portability and interop — making a wiki this skill produces readable by Google's tooling and anything else that adopts OKF, with no cloud coupling.
- **After GCP migration:** the cloud half of the repo (BigQuery/Dataplex enrichment, Vertex Gemini agents, the `kcmd` metadata-as-code sync) becomes a **large** opportunity, not a liability. A wiki that is already OKF-conformant could be auto-enriched by their agents and synced into Dataplex with little adaptation. The conformance work we do now is what unlocks that later.

## Verdict

Do not integrate Google's cloud stack into the base scaffold (now). Do steal the **format spec** and a few **eval ideas**, and keep the generated wiki portable and dependency-light, exactly as with Keppi.

The shape to preserve (unchanged):

> A generated wiki should be useful with plain markdown, `python3 scripts/lint.py`, `python3 scripts/query.py`, and `python3 scripts/render.py`.

What changes versus the Keppi verdict: OKF is not just a design donor, it's a **standard we may want to be conformant with**, because conformance is the cheap on-ramp to leveraging GCP's enrichment tooling post-migration.

## What Google built (the five layers)

| Layer | What it is | Cloud-coupled? | Relevance |
|---|---|---|---|
| **`okf/`** | The spec (`SPEC.md`) + Python reference impl: `OKFDocument` (parse/serialize/validate), `Source`/`BigQuerySource`, index generation, a Cytoscape.js single-file HTML visualizer. CLI: `python -m enrichment_agent {enrich,visualize}`. | Spec + visualizer are local; `enrich` needs BQ + Gemini. | **High now** (format), **high later** (enrich). |
| **`agents/enrichment/`** | Python agent (Google ADK + Vertex Gemini) that auto-writes knowledge from BigQuery/Drive/GitHub. Three modes (`table`, `doc`, `context_overlay`). Ships a real **eval harness** (LLM-as-judge + golden + dynamic metrics). | Heavily (Vertex, BQ, Drive). | Eval ideas now; whole thing post-GCP. |
| **`agents/mdcode/` (`kcmd`)** | TypeScript "metadata as code" CLI — pull/push YAML+md sidecars to/from Dataplex, runs as an MCP server. | Entirely Dataplex. | Post-GCP only. |
| **`samples/`** | `discovery/` (an ADK search agent, ships its own `SKILL.md`) + `enrichment/` (download → enrich → diff → publish loop). | Dataplex Search API. | Post-GCP only. |
| **`toolbox/`** | Vendored mirror copies of the two agents. | — | — |

Maturity: Apache-2.0, everything at **v0.1 / 0.1.0**, "not an official Google product" disclaimer, but real test coverage (8 test files in `okf/`, full eval suite) and three checked-in production bundles (GA4, Stack Overflow, Bitcoin). Early but seriously-engineered POC with Google's name and distribution behind it.

## How OKF maps onto this repo

Near-isomorphic. We and Google independently arrived at the same shape:

| Concept | `llm-wiki` | OKF |
|---|---|---|
| Unit of knowledge | "page" | "concept" (`.md` + frontmatter) |
| Container | the wiki repo | "bundle" |
| Catalog | `index.md` (per-type sections) | `index.md` (progressive disclosure, **no frontmatter**) |
| History | `log.md`, `## [YYYY-MM-DD] action` | `log.md`, `## YYYY-MM-DD` + `**Update**`/`**Creation**` |
| Linking | wiki-root-relative `./policies/x.md` | bundle-relative `/tables/x.md` or `./x.md` |
| Provenance | `sources/` (immutable raw) + `source:` field | `# Citations` section + optional `references/` concepts |
| Graph view | `render.py` → `wiki.html` (8 views) | Cytoscape.js → `viz.html` |

Even the log format and index-as-progressive-disclosure idea match independently.

## The two real divergences (design forks, not gaps)

### 1. Strict vs. permissive — opposite philosophies

OKF requires *only* a non-empty `type` per file, and mandates that consumers **tolerate** missing fields, unknown types, and broken links ("permissive consumption," §9). Our lint is the inverse: seven required frontmatter fields, four mandatory load-bearing sections (What This Is / How It Works / Risk Register / Prerequisites), broken refs flagged, contradiction scan, source-drift check.

OKF is built for *machine-generated knowledge at scale where quality can't be guaranteed* (an agent enriching 10,000 BigQuery tables can't be blocked by a lint failure). Ours is built for *human-curated quality where the enforced structure is the value-add*.

**Keep our strictness — it is the differentiator.** Conformance with OKF should be an *export/compat layer*, not a relaxation of our lint. We can be a strict superset: every wiki we produce is valid OKF, but not every valid OKF bundle would pass our lint.

### 2. The `type` field means opposite things

- OKF: `type` is the **one required** field, used for routing/filtering/presentation (`BigQuery Table`, `Playbook`).
- Us (commit `c29f703`): `type` is **colour-only, explicitly not a lint-exemption knob**, and **optional** on single-type wikis.

Same field name, inverted role. This is the sharpest concrete conflict and the one thing that breaks OKF-conformance for our single-type wikis (which omit `type`). Reconcilable: a meaningful `type` value serves *both* their routing and our colouring at once. Making `type` required (with a sensible default per primary directory) closes the gap without changing how we use it.

## Ideas worth stealing

### 1. OKF conformance as an export/compat target — highest value, low cost

> **Status (2026-06-17): implemented** as *native* on-disk conformance (not export) — see [`spec`](superpowers/specs/2026-06-17-okf-conformance-design.md) / [`plan`](superpowers/plans/2026-06-17-okf-conformance.md). Generated wikis are now conformant OKF v0.1 bundles; lint enforces it.

Make a wiki this skill produces conformant with OKF v0.1, so it is consumable by Google's visualizer, their enrichment agents, and anything else in the emerging ecosystem. This is the strategic move: it positions the skill as "produces the format Google specified," and it is the on-ramp to GCP enrichment post-migration.

The conformance delta against our current frontmatter is small:

- **`type` required on every page** (today optional on single-type wikis). Default it per primary directory.
- **Add `description`** — a one-line summary. OKF-recommended; we don't have it.
- **Add `timestamp`** (ISO 8601) — maps closely to our `last_reviewed`; could be derived.
- **Links:** OKF prefers bundle-absolute (`/tables/x.md`); we use `./tables/x.md`. Both are valid OKF (§5.2), so this is a no-op for conformance, but worth noting if we ever generate for their visualizer.

Everything else we already have or exceed. Likely shape: a `scripts/okf_export.py` (or `render.py --okf`) that emits an OKF-conformant copy, plus an `okf` mode in lint that checks conformance specifically. Keep our native format as the source of truth; OKF is a projection.

### 2. Steal their eval metrics for our lint's contradiction scan

Our contradiction scan and source-drift check are prose instructions today. Google's `agents/enrichment/eval/` formalises exactly the checks we want as **LLM-as-judge metrics**:

- `absence_of_contradictions` — join-key/enum/definition conflicts (≈ our contradiction scan)
- `hallucination_free` — every claim grounded in retrieved sources (≈ our `source:` provenance, but enforced)
- `redundancy_index` — novel content vs. echoing the schema/source
- `disambiguation_efficacy` — a page distinguishable from similar ones
- plus deterministic `structural_validity` (≈ our lint) and golden-based `concept_recall`/`fact_recall`

These are concrete rubrics we could lift into a `scripts/eval.py` or fold into lint — turning fuzzy "scan for contradictions" prose into scored, repeatable checks. This is the highest-value steal after conformance, and it improves the wiki regardless of GCP.

### 3. `resource:` field — separate "what this describes" from "where it came from"

OKF splits `source:` (the citation/raw input — our `sources/`) from `resource:` (the canonical URI of the *live asset the page describes*, e.g. a BigQuery table URL). We have the former, not the latter. Useful for data-catalog-style wikis where pages describe live systems — and **directly relevant post-GCP**, where `resource:` would point at the actual Dataplex/BigQuery asset. Add as optional frontmatter now so it's already in place at migration.

### 4. `references/` as first-class concepts (judgment call)

Google mirrors external material into `references/*.md` concepts rather than only stashing raw in a sources layer — making citations linkable, navigable graph nodes. This cuts against our clean raw/derived split (our `sources/` is immutable and not part of the graph), so it's not a clear win. Worth considering only for citation-heavy wikis. Flag, don't adopt by default.

## Park until GCP migration

These are not "avoid" — they're **deferred high-value**, the inverse of the Keppi avoid-list. Revisit when the GCP migration lands.

- **`agents/enrichment/` (BigQuery/Drive/GitHub → auto-enrichment via Vertex Gemini).** Post-migration, this could auto-populate or enrich a wiki from our actual GCP data estate. The conformance work in idea #1 is the prerequisite.
- **`kcmd` / mdcode (Dataplex sync).** If our wikis live alongside Dataplex metadata, two-way sync becomes interesting. Entirely GCP-coupled, so meaningless before migration.
- **The discovery agent (Dataplex Search API).** Semantic search over the catalog, GCP-native.

None of this belongs in the base scaffold. The base scaffold must remain usable with zero cloud dependencies, exactly as the Keppi design constraint demands. The GCP integrations, when they come, are an *optional layer* on top — same pattern as "optional semantic search" in the Keppi list.

## Ideas to avoid (now and possibly forever)

### Adopting OKF's permissive consumption model wholesale

Do not loosen our lint to match OKF's "tolerate everything" stance. Our strictness is why our wikis are better than the bulk-generated bundles OKF was designed to tolerate. Conformance is an export projection, not a relaxation.

### Coupling the base scaffold to ADK / Vertex / any cloud SDK

Even post-GCP, the *generated wiki* should not require an SDK to be read, linted, or rendered. Cloud enrichment is a producer-side tool, not a consumer-side dependency.

## Suggested implementation order

### Phase 1: OKF conformance audit + delta (no behaviour change)

Document the exact frontmatter delta against `SKILL.md` and `_templates/CONVENTIONS.md`. Decide on defaults for `type`, `description`, `timestamp`. No code yet — agree the schema change first.

### Phase 2: conformance in lint

Add an OKF-conformance check to `lint.py` (likely `--okf` mode): every page has non-empty `type`, parseable frontmatter, reserved-file structure. Reports only; does not change native lint.

### Phase 3: OKF export

`scripts/okf_export.py` (or `render.py --okf`) emits an OKF-conformant copy of the wiki — frontmatter normalised, links rewritten to bundle-absolute if needed. Native format stays source of truth.

### Phase 4: eval metrics

`scripts/eval.py` implementing the steal-worthy metrics from `agents/enrichment/eval/` — contradiction, hallucination/grounding, redundancy — as scored checks. Improves the wiki independently of GCP.

### Phase 5 (post-GCP): enrichment + Dataplex sync

Only after migration. Evaluate `agents/enrichment/` and `kcmd` as optional producer-side layers. Conformance from phases 1–3 is the prerequisite.

## Future idea — MCP serving layer over an OKF bundle

OKF is a *file format*, not a protocol — interop is clone-and-read / point-a-tool-at-the-directory. That's all "connect" needs to mean for us: standardise to OKF enough that others on the same standard can consume our wikis (see idea #1, conformance).

A *live* connection — an LLM querying a wiki on demand rather than reading the files — would need a thin serving layer on top of the bundle, most naturally an MCP server (`list concepts → read concept → traverse links`). Google proved the pattern with `kcmd`'s MCP server, but only for Dataplex; nobody ships a generic MCP-over-an-OKF-bundle. It would be a novel contribution and is only clean to build *because* the format is conformant — but it is **not** required for format-level interop. Park as a future, optional, consumer-side piece. Not in scope for the conformance work.

## Repo watcher — track the whole of `knowledge-catalog`, not just the spec

We want to know about **any** meaningful change to Google's repo — new commits, new agents, eval changes, format changes — and have it read through one lens: *is there anything useful or breaking for us in here?* A spec-only diff is too narrow; the spec is one file, and the enrichment/eval/tooling code is where most of the action will be.

This is an LLM-judgment task, not a textual diff — "read the commit, understand what changed, flag relevance" — so the right vehicle is a **scheduled cloud agent**, not a dumb script.

Shape:

- A scheduled Claude agent (via the `schedule` skill) that wakes on a cadence (weekly is plenty pre-GCP; tighten post-migration).
- It records the last-seen commit SHA in-repo (e.g. `docs/okf/last-seen.txt`). On each run: `gh api` / `git fetch` to list commits on the default branch since that SHA, then read the diffs.
- For each commit (or batch), classify against our lens:
  - **Format change** (`okf/SPEC.md`, `okf/src/.../document.py` frontmatter rules) → highest signal; may affect our conformance layer. Flag breaking vs. additive.
  - **Eval/enrichment change** (`agents/enrichment/eval/`, `agents/enrichment/src/`) → may be worth stealing into our `scripts/eval.py`.
  - **Tooling/cloud** (`kcmd`, `samples/`, `bundles/`) → mostly post-GCP relevance; note but low priority now.
- Output: a short digest — what changed, and a one-line verdict per change (`adopt` / `watch` / `breaking — review` / `ignore`). Update the pinned SHA at the end of the run.

Keep a pinned snapshot of `SPEC.md` (`docs/okf/SPEC-0.1.md`) too, so format changes specifically get a clean reviewable diff in version control alongside the agent's prose summary.

This keeps us cheaply aware of the whole repo's evolution without manual polling. Build alongside the conformance work.

## Design constraint (unchanged from Keppi)

> Can a fresh generated wiki still be understood, inspected, repaired, and versioned as plain files?

OKF conformance *strengthens* this — it's a more portable plain-files format, not less. The cloud integrations must stay behind optional, late, producer-side commands. If an idea can't pass the plain-files test, it belongs behind an optional command or not in this repo at all.
