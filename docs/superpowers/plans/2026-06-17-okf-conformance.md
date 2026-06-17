# Plan: OKF v0.1 Conformance

Implements [`specs/2026-06-17-okf-conformance-design.md`](../specs/2026-06-17-okf-conformance-design.md). **Status: implemented 2026-06-17** — all tasks done, 78 tests green, end-to-end verified.

> Note: Plan and Tasks are combined into this one document for review efficiency. The gate before **Implement** (any code/instruction edit) still stands.

## Grounding facts (verified against current code)

- **Enforcement lives in `scripts/lint.py`**, copied verbatim into every wiki. `REQUIRED_FRONTMATTER` (line 42) = `{title, category, status, owner, tags, created, last_reviewed}`. Section routing keys off `type` via `run_checks` (lines 165–283); `collect_pages` (71–84) loads frontmatter.
- **No page-template files exist in this repo** — `_templates/` holds only `CONVENTIONS.md`. Page templates (`{slug}.md`, `entity.md`) are generated per-wiki by the agent following `SKILL.md`. So template changes = edits to `SKILL.md` (Templates / Entity Template sections) + `CONVENTIONS.md`.
- **No root `index.md` in this repo** — index format is an instruction in `SKILL.md §9`. OKF §6 alignment is a prose change.
- **Test fixtures are inline** — `_base_fm` / `_note_fm` / `_entity_fm` helpers across four `test_lint.py` classes. New required fields must be added to these helpers centrally, or existing tests that build "clean" pages will start failing on the new required-field check.
- **`SKILL.md` has no `version` field today** — introducing one (D4).

## Components

| ID | Component | Where | Kind |
|---|---|---|---|
| C1 | Canonical frontmatter schema (the field list + defaults) | `SKILL.md §8`, `CONVENTIONS.md` frontmatter ref | prose (source of truth) |
| C2 | Page + entity template instructions | `SKILL.md` "Templates — required sections", "Entity Template" | prose |
| C3 | `index.md` format → OKF §6 bullets + root `okf_version: "0.1"` | `SKILL.md §9`, `CONVENTIONS.md` | prose |
| C4 | Skill versioning (`version: 1.0.0`) | `SKILL.md` frontmatter + a one-line convention note | prose |
| C5 | Lint enforcement: 3 new required fields + OKF conformance checks | `scripts/lint.py` | code |
| C6 | Existing-wiki migration offer | `SKILL.md` Step 3 pre-flight | prose |
| C7 | Tests | `tests/test_lint.py` | code (TDD) |
| C8 | Docs/log hygiene | `CONVENTIONS.md` tables, `log.md`, cross-refs | prose |

## Implementation order & critical path

Critical path is **C7 → C5** (TDD on lint). Everything else is instruction/doc edits that must *agree* with the schema C1 fixes.

1. **C1** — fix the exact schema text: required = existing 7 **+ `type` + `description` + `timestamp`**; `type` auto-defaults from primary slug; `timestamp` is ISO 8601 last-meaningful-change, distinct from `created`/`last_reviewed`. No code yet.
2. **C7 (failing tests) → C5 (implement)** — TDD:
   - `RequiredFrontmatterTest`: page missing `type` / `description` / `timestamp` each flagged via `missing_frontmatter_field` (or equivalent existing check); conformant page clean.
   - `OKFConformanceTest`: non-empty-`type` check (empty string flagged); root `index.md` missing `okf_version` flagged.
   - Regression: existing `TypeAwareSectionChecksTest` still green (routing unchanged); update `_base_fm`/`_note_fm`/`_entity_fm` to include the three new fields so "clean" fixtures stay clean.
   - Implement in `lint.py`: extend `REQUIRED_FRONTMATTER`; add `check_okf_conformance` (parseable frontmatter + non-empty `type`, per OKF §9); add `okf_version` root-index check; register labels in `CHECK_LABELS`; keep the `type`-absent → primary-section default as belt-and-suspenders.
3. **C2, C3** — update `SKILL.md` template/index instructions + `CONVENTIONS.md` to emit the new fields and the §6 index shape with root `okf_version`.
4. **C4** — add `version: 1.0.0` to `SKILL.md` frontmatter + a short semver convention note.
5. **C6** — migration offer in `SKILL.md` Step 3 (mirror the `graph.py`→`render.py` pattern): detect a wiki whose pages lack `type`/`description`/`timestamp`, offer to backfill (`type` from directory slug; `description` and `timestamp` flagged for human/agent fill), then re-lint.
6. **C8** — update `CONVENTIONS.md` "Mandatory Sections by Type" + frontmatter table; append `log.md`; cross-ref `okf-steal-list.md`.
7. **Verify** — see checkpoints.

**Parallelizable** once C1 is fixed: C2, C3, C4, C6, C8 are independent prose edits. C5+C7 is the sequential critical path.

## Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | `type` now required collides with §8b "absent type → primary checks" routing | Keep the absent→primary default as defense-in-depth; type-required is a *separate* check, not a routing change. Existing section tests stay green. |
| R2 | Inline fixtures lack new fields → broad test churn | Update the 3–4 fixture helpers centrally; existing tests filter by check name so they won't trip on the new field check. |
| R3 | OKF §6 index alignment may shift `parse_index_entries` expectations (`lint.py`/`render.py`) | Confirm current parser handles `* [title](path) - desc` bullets. If the format materially changes, update both parsers + their tests. **Ask-first if non-trivial** (per spec Boundaries). |
| R4 | Schema lives in 3 places (`SKILL.md`, `CONVENTIONS.md`, `lint.py`) — drift | One task updates all three together; final checklist cross-checks the field list in each. |
| R5 | `description`/`timestamp` required breaks every existing real wiki until migrated | Intended breaking change — handled by C6 migration + the `1.0.0` version bump. |

## Verification checkpoints

- **After C5:** `.venv/bin/python3 -m pytest tests/ -v` green, including new cases.
- **End-to-end:** scaffold a throwaway wiki per `SKILL.md`, add one page from a template → `lint.py` reports clean **and** OKF-conformant; `render.py` produces `wiki.html`.
- **Stretch (interop):** if Google's `okf` package installs, `python -m enrichment_agent visualize --bundle <wiki>` renders it without error.

## Tasks

- [x] **T1 — Fix canonical schema (C1).**
  - Acceptance: `SKILL.md §8` and `CONVENTIONS.md` state required = 10 fields with `type` (slug-defaulted), `description`, `timestamp` (ISO 8601, distinct) defined.
  - Verify: manual read; field list identical in both.
  - Files: `SKILL.md`, `_templates/CONVENTIONS.md`.
- [x] **T2 — Failing tests for required fields + conformance (C7).**
  - Acceptance: new `RequiredFrontmatterTest` + `OKFConformanceTest` exist and fail against current `lint.py`.
  - Verify: `pytest tests/ -k "Required or OKFConformance"` → fails as expected.
  - Files: `tests/test_lint.py`.
- [x] **T3 — Implement lint enforcement (C5).**
  - Acceptance: `REQUIRED_FRONTMATTER` extended; `check_okf_conformance` + root `okf_version` check added; `CHECK_LABELS` updated; absent-type default preserved.
  - Verify: `pytest tests/ -v` green (incl. T2 + existing).
  - Files: `scripts/lint.py`, `tests/test_lint.py` (fixture helper updates).
- [x] **T4 — Update template + entity instructions (C2).**
  - Acceptance: `SKILL.md` template sections instruct emitting `type`/`description`/`timestamp`; entity template gains `description`/`timestamp`.
  - Verify: manual read against T1 schema.
  - Files: `SKILL.md`, `_templates/CONVENTIONS.md`.
- [x] **T5 — index.md → OKF §6 + okf_version (C3).**
  - Acceptance: `SKILL.md §9` specifies §6 bullet sections and root `okf_version: "0.1"`; parser compatibility confirmed (R3).
  - Verify: scaffold check; `parse_index_entries` still resolves entries (add/extend a test if format changes).
  - Files: `SKILL.md`, `_templates/CONVENTIONS.md`, possibly `scripts/lint.py`/`scripts/render.py` + tests.
- [x] **T6 — Skill versioning (C4).**
  - Acceptance: `SKILL.md` frontmatter has `version: 1.0.0` + semver convention note.
  - Verify: manual read.
  - Files: `SKILL.md`.
- [x] **T7 — Migration offer (C6).**
  - Acceptance: `SKILL.md` Step 3 detects pre-`1.0.0` wikis (pages lacking the new fields) and offers backfill, then re-lint.
  - Verify: manual read; logic mirrors existing `graph.py`→`render.py` migration.
  - Files: `SKILL.md`.
- [x] **T8 — Docs/log + final drift check (C8).**
  - Acceptance: `CONVENTIONS.md` tables updated; `log.md` appended; field list verified identical across `SKILL.md` / `CONVENTIONS.md` / `lint.py`.
  - Verify: 3-way field-list diff matches; `pytest tests/ -v` green.
  - Files: `_templates/CONVENTIONS.md`, `log.md`.
