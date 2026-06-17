# Spec: OKF v0.1 Conformance (native, strict-superset)

Status: **Implemented 2026-06-17.** See the [plan](../plans/2026-06-17-okf-conformance.md) for task-level detail.

Related intel: [`docs/okf-steal-list.md`](../../okf-steal-list.md).

## Objective

Make every wiki the `wikime` skill scaffolds a **conformant Open Knowledge Format (OKF) v0.1 bundle on disk**, so that anyone else on the OKF standard can read and write our wikis as the same format with zero bespoke integration — no export step, clone-and-use.

We do this as a **strict superset**: every wiki we produce is valid OKF, but our existing strict lint (mandatory sections, frontmatter completeness, broken-ref detection, contradiction scan, source-drift) stays fully in force. OKF is permissive; we are not. Conformance adds constraints, it never removes ours.

**Who benefits:** consumers/producers on the OKF standard (Google's `okf` visualizer + enrichment agents today; the wider ecosystem as it grows; ourselves post-GCP migration, when GCP enrichment agents can write into our wikis because they target this exact format).

**Success looks like:** a freshly scaffolded wiki passes OKF v0.1 conformance *and* our existing lint, and Google's own OKF visualizer renders it unmodified.

## Decisions locked (2026-06-17)

| # | Decision | Choice |
|---|---|---|
| D1 | Conformance delivery | **Native on-disk** — the wiki directory *is* an OKF bundle by default; no export script. |
| D2 | `type` field value | **Auto-defaulted from the primary directory slug** (`papers/` → `type: paper`). Stays our colour/filter signal *and* satisfies OKF's required routing field. |
| D3 | `timestamp` source | **Distinct ISO 8601 field** (last meaningful change), maintained alongside `created` and `last_reviewed`. |
| D4 | Skill versioning | Introduce **semver** in `SKILL.md` frontmatter; this change ships as **`1.0.0`** (first formally versioned release). Future: major = breaking generated-wiki schema change, minor = additive, patch = fixes. |
| D5 | Existing-wiki migration | **In scope.** Add a migration offer to `SKILL.md`'s pre-flight (Step 3), mirroring the existing `graph.py`→`render.py` migration pattern. |

Assumptions carried (correct before approval):
- `description` becomes a **required** field (we're stricter than OKF, which only recommends it).
- Links unchanged — `./policies/x.md` is valid OKF §5.2.
- Root `index.md` declares `okf_version: "0.1"` (the one place OKF permits index frontmatter).
- `entity` / `concept` / `meta` type semantics survive untouched — they are valid OKF `type` values.

## Tech Stack

- Python 3.12, `pyyaml`, `markdown` (existing scripts: `lint.py`, `render.py`, `query.py`).
- `uv venv` + `uv pip install pyyaml markdown` into project-local `.venv/` (per repo convention).
- The "product" is the `wikime` skill (`SKILL.md` + `_templates/` + `scripts/`), not an app. Changes here propagate to every wiki generated afterward.

## Commands

```bash
# Tests (TDD — required for lint/render changes)
.venv/bin/python3 -m pytest tests/ -v

# Lint a wiki (now also enforces OKF conformance fields)
.venv/bin/python3 scripts/lint.py

# Render the reader artifact
.venv/bin/python3 scripts/render.py

# Interop verification (against Google's OKF tooling, if available)
python -m enrichment_agent visualize --bundle <generated-wiki>
```

## Project Structure (files this change touches)

```
SKILL.md                          → frontmatter schema (§8), lint section, templates section, §8b type logic
_templates/CONVENTIONS.md         → frontmatter reference, "Mandatory Sections by Type" table
_templates/{slug}.md              → each primary template: add type (defaulted), description, timestamp
_templates/entity.md              → add description, timestamp (type already present)
scripts/lint.py                   → require type/description/timestamp; type-required; OKF conformance checks
scripts/render.py                 → (only if description/timestamp surface in views — see Open Questions)
tests/test_lint.py                → cover new required fields, type-required, conformance checks
tests/test_render.py              → only if render changes
docs/superpowers/plans/2026-06-17-okf-conformance.md  → Phase 2 plan (created on approval)
```

## Code Style

Match existing `lint.py` patterns — plain functions, a findings list, no auto-fix, report-as-markdown-checklist. Example of the conformance check shape to add:

```python
def check_okf_conformance(pages, wiki_root):
    """OKF v0.1 §9: every non-reserved .md has parseable frontmatter + non-empty type."""
    findings = []
    for page in pages:
        if not page.frontmatter:
            findings.append(f"{page.path}: no parseable YAML frontmatter (OKF §9.1)")
        elif not page.frontmatter.get("type", "").strip():
            findings.append(f"{page.path}: empty or missing `type` (OKF §9.2)")
    return findings
```

## Frontmatter schema — before / after

**Required today:** `title, category, status, owner, tags, created, last_reviewed`
**Required after:** `title, category, status, owner, tags, created, last_reviewed, type, description, timestamp`

- `type` — auto-set from primary directory slug in each template (D2); `entity|concept` for entity pages, `meta` for meta pages. Now a hard-required field.
- `description` — one-line summary (OKF-recommended; required for us).
- `timestamp` — ISO 8601 last-meaningful-change (D3); distinct from `created` (creation) and `last_reviewed` (last human review).

**`type` lint logic (revision to SKILL.md §8b):** `type` is now *required* — "absent" is a lint failure, not a routing branch. Routing by **value** is unchanged: `entity`/`concept` → entity checks; `meta`/`category` contains "meta" → free-form; any other value → strict primary-section checks.

## Testing Strategy

- `pytest`, tests in `tests/`. TDD per repo profile: failing test first, then implement.
- New `test_lint.py` cases: (a) page missing `type`/`description`/`timestamp` → flagged; (b) page with empty `type` → flagged; (c) conformant page → clean; (d) `entity`/`meta` routing still correct with required `type`; (e) root `index.md` missing `okf_version` → flagged.
- Interop check (manual, if Google's `okf` package is installable): generate a sample wiki, run their `visualize`, confirm it renders.

## Boundaries

- **Always:** keep the strict lint in force; TDD for `lint.py`/`render.py` changes; update `SKILL.md` + `CONVENTIONS.md` + templates together (schema lives in three places, must not drift); ship the migration offer (D5) in the same change.
- **Ask first:** any further change to the required-field set beyond the three locked here; rewriting `index.md` body to strict §6 form if the Plan-phase check finds the current form non-conformant.
- **Never:** relax or remove an existing lint check; couple the base scaffold to any cloud SDK; auto-edit a user's existing wiki without explicit migration consent.

## Success Criteria

1. A wiki freshly scaffolded by `wikime` is **OKF v0.1 conformant** (§9): every non-reserved `.md` has parseable frontmatter with non-empty `type`; `index.md`/`log.md` follow §6/§7; root `index.md` declares `okf_version: "0.1"`.
2. The same wiki **passes the existing strict lint** unchanged (no regression).
3. `scripts/lint.py` reports OKF conformance findings as part of its checklist.
4. `pytest tests/` is green, including the new conformance cases.
5. (Stretch / interop) Google's `python -m enrichment_agent visualize --bundle <wiki>` renders the generated wiki without error.

## Resolved (was Open Questions)

1. **Existing-wiki migration** → **in scope** (D5). Migration offer in `SKILL.md` pre-flight, `graph.py`→`render.py` pattern.
2. **Skill versioning** → semver, ships as `1.0.0` (D4).
3. **`description` feeding render** → **out of scope.** Frontmatter-only now; surfacing it in `wiki.html` search/index is a possible later enhancement, not part of this change.

## Plan-phase checks (not blockers — verify when planning)

1. **`index.md` body format.** OKF §6 wants `* [Title](url) - description` bullet sections and *no* frontmatter beyond root `okf_version`. Confirm our current `index.md` body is §6-shaped, or align it. If alignment is non-trivial, surface before implementing (per Boundaries → Ask first).
2. **Test fixtures.** `tests/` fixtures predate the three new required fields — they'll need updating alongside the lint changes (TDD).
