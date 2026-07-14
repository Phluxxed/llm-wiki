# Plan: Wiki Quality Eval (LLM-as-judge metrics)

Implements [`specs/2026-06-17-eval-metrics-design.md`](../specs/2026-06-17-eval-metrics-design.md). **Status: implemented 2026-06-17** — 127 tests green; live smoke verified (clean→PASS/0, contradiction→FAIL/1, no-judge→deterministic+brief).

> **Historical record:** This implementation plan predates the accepted live-judge circuit breaker. Current execution requires a zero-model preview, an explicit hard call cap, and a per-wiki lock as recorded in [ADR-006](../../decisions/ADR-006-budget-and-lock-live-judge-evals.md). The uncapped live-smoke commands below are not current operating instructions.

> **Mid-build course-correction (E1 revised):** the judge started as `claude -p` CLI; the live smoke (T8) exposed it as a *full agentic session* that intermittently invoked tools and returned empty output on large prompts. On Vik's steer it was rebuilt on the **Claude Agent SDK** (tools-off, single-turn, keyless) — now reliable (3/3). Codex stayed on its CLI. The T8 smoke also caught a real grounding bug (frontmatter judged as claims) and drove the E8 loud-failure / fail-closed semantics. The layered design held: only the T5 adapter changed.

## Grounding facts (verified)

- **`evals/evals.json` is the skill's own scenario-test harness** (does `/wikime` scaffold correctly) — NOT for wiki-quality run records. Run records go in a new **`.eval/`** dir *inside each generated wiki*; it must be added to `EXCLUDE_DIRS` in both `lint.py` and `render.py` so it isn't scanned as pages.
- **Reusable lint loaders** (import + set `lint.WIKI_ROOT`, as the tests do): `parse_frontmatter`, `extract_sections`, `collect_pages`, `collect_source_files`.
- **Judge adapters verified live** (spec E "Judge adapters"): `claude -p … --output-format json </dev/null` (~9s, verdict in `.result`); `codex exec … --output-schema --output-last-message … </dev/null` (~25s). **Both require `</dev/null`** or they hang on stdin.
- `pytest` not installed → tests are `unittest`.

## Components

| ID | Component | Where | Kind |
|---|---|---|---|
| C1 | Core: `MetricResult`, `Judge` type, `parse_json_obj`, run-record shape | `scripts/eval.py` | code |
| C2 | Judge adapters: auto-detect `claude`/`codex`, subprocess (`</dev/null`), `--judge <cmd>` override, `--judge none` | `scripts/eval.py` | code |
| C3 | Deterministic metrics: `structural_validity` (reuse lint), near-duplicate *candidate* detection | `scripts/eval.py` | code |
| C4 | Judge metrics: contradictions, grounding, redundancy, disambiguation — evidence assembly + batched/parallel judging | `scripts/eval.py` | code |
| C5 | Gating layer: thresholds, multi-run averaging, pass/fail, exit code | `scripts/eval.py` | code |
| C6 | CLI: default single-pass auto-detect; `collect`/`score` fallback; `--runs/--gate/--json/--judge` | `scripts/eval.py` | code |
| C7 | Run records + history in `.eval/`; exclude from lint/render | `scripts/eval.py`, `scripts/lint.py`, `scripts/render.py` | code |
| C8 | Tests: stub judge (metrics + gating), mocked-subprocess (adapters) | `tests/test_eval.py` | code (TDD) |
| C9 | Integration: scaffold eval.py, Operations step, Scripts tables, lint footer pointer | `SKILL.md`, `_templates/CONVENTIONS.md`, `README.md` | prose |

## Implementation order & critical path

Pure-Python layers first (testable without any judge), then the judge metrics with a **stub**, then the real adapters, then wiring. Critical path: **C1 → C3/C5 → C4(stub) → C2 → C6**.

1. **C1** — dataclass + `Judge = Callable[[str], str]` + `parse_json_obj` (strip ```json fences/arrays) + run-record dict shape. Tests for `parse_json_obj` edge cases.
2. **C3** — deterministic metrics: `structural_validity` delegates to lint; near-dup candidate detection (title/tag/source overlap → candidate pairs, the cheap pre-filter that stops disambiguation from going O(n²) against the judge). Pure-Python tests.
3. **C5** — gating layer: load thresholds (`.eval/thresholds.json`, defaults E4), average N runs, `passed = score >= threshold`, overall pass/fail, exit code. Tested with injected `MetricResult`s — no judge.
4. **C4** — judge metrics behind a **stub judge**: evidence assembly (page+source pairing for grounding; sibling pages for contradictions; candidate pairs for disambiguation; page-vs-template for redundancy), batched rubric prompt (one call/page → dict keyed by dimension, Google-style), grounding claim-extraction + chunking, `concurrent.futures` thread pool (cap ~3) for parallel calls, retry on malformed JSON, unparseable → `None`/skip (never silent 0). Stub-judge tests assert score/pass/rationale wiring.
5. **C2** — adapters: `detect_judge()` probes PATH (`claude`, then `codex`); each adapter builds the verified command **with `</dev/null`** and extracts the verdict (claude: `.result` → `parse_json_obj`; codex: read `--output-last-message` file). `--judge <cmd>`/`--judge none`. Tested via **mocked subprocess** asserting command shape (incl. `</dev/null`) + parse path; not live in unit tests.
6. **C6** — CLI wiring: default = detect → run all layers → report (+`--gate` exit code, `--json`); `collect`/`score` fallback for no-CLI machines.
7. **C7** — write run records/history to `.eval/`; add `.eval` to `EXCLUDE_DIRS` in `lint.py` and `render.py`; update lint footer to point at eval.py.
8. **C9** — SKILL.md (scaffold eval.py + Operations eval step + Scripts table + note `.eval/` is gitignorable), CONVENTIONS, README.
9. **Verify** — see checkpoints.

**Parallelizable**: C9 prose once the CLI shape is fixed. C1→C6 is the sequential code path.

## Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | Latency/cost on large wikis (~9–25s/judge call) | Batch rubric (1 call/page); parallelize (thread pool, cap 3); grounding only on derived pages (`source:` set); N=1 default; `--judge none` for cheap deterministic runs |
| R2 | Judge returns malformed/incomplete JSON | `parse_json_obj` + retry (≤3, Google pattern) + require ≥1 expected key; unparseable → `None`/skip, never a silent 0 |
| R3 | Adapter hangs on stdin | `</dev/null` baked into every adapter; a unit test asserts the command includes it |
| R4 | Nested agent invocation / token spend on user account | Expected & verified to work; document that eval spends tokens on the user's existing login; `--judge none` avoids it |
| R5 | `.eval/` scanned as pages by lint/render, or `evals/` name collision | Add `.eval` to both `EXCLUDE_DIRS`; never write to `evals/` (skill's own test harness) |
| R6 | Disambiguation combinatorial blowup | Deterministic candidate pre-filter; judge confirms only candidate *pairs*, not all pairs |

## Verification checkpoints

- **After C3/C5:** `unittest` green for deterministic metrics + gating (injected results, no judge).
- **After C4:** stub-judge metric tests green.
- **After C2:** mocked-subprocess adapter tests green (assert `</dev/null` + parse).
- **End-to-end (live smoke):** on a temp wiki, `eval.py --judge codex --gate` and `--judge claude --gate` each produce a report + correct exit code; seed a contradiction across two pages → gating **fails** (non-zero exit); remove it → passes. Uses the real CLIs on this machine.

## Tasks

- [x] **T1 — Core (C1).** `MetricResult`, `Judge`, `parse_json_obj`, run-record shape.
  - Acceptance: `parse_json_obj` handles fenced/array/garbage; dataclass matches spec.
  - Verify: `unittest tests.test_eval.CoreTest`.
  - Files: `scripts/eval.py`, `tests/test_eval.py`.
- [x] **T2 — Deterministic metrics (C3).** structural (via lint) + near-dup candidates.
  - Acceptance: structural mirrors lint result; candidate detection finds seeded near-dup pair, ignores unrelated pages.
  - Verify: `unittest` (no judge).
  - Files: `scripts/eval.py`, `tests/test_eval.py`.
- [x] **T3 — Gating layer (C5).** thresholds + averaging + pass/fail + exit code.
  - Acceptance: injected results below threshold → fail + non-zero exit; N-run average correct; thresholds overridable from `.eval/thresholds.json`.
  - Verify: `unittest` (injected `MetricResult`s).
  - Files: `scripts/eval.py`, `tests/test_eval.py`.
- [x] **T4 — Judge metrics w/ stub (C4).** four metrics + evidence assembly + batching + parallel + retry.
  - Acceptance: stub judge → each metric yields score/pass/rationale; malformed JSON → `None`/skip not 0; grounding skips source-less pages.
  - Verify: `unittest` with stub judge.
  - Files: `scripts/eval.py`, `tests/test_eval.py`.
- [x] **T5 — Adapters (C2).** detect + `claude`/`codex` invocation + `--judge` override.
  - Acceptance: mocked subprocess → command includes `</dev/null` and correct flags; verdict extracted from `.result` (claude) / output file (codex); `--judge none` → fallback.
  - Verify: `unittest` (mocked subprocess).
  - Files: `scripts/eval.py`, `tests/test_eval.py`.
- [x] **T6 — CLI (C6).** default single-pass + `collect`/`score` fallback + flags.
  - Acceptance: `--judge none` runs deterministic + emits brief; `--gate` sets exit code; `--json` clean.
  - Verify: `unittest` + manual run on temp wiki.
  - Files: `scripts/eval.py`.
- [x] **T7 — Run records + excludes (C7).** `.eval/` writes; lint/render exclude; lint footer pointer.
  - Acceptance: run record + history written under `.eval/`; `lint.py`/`render.py` ignore `.eval/`; lint footer names eval.py.
  - Verify: `unittest` (existing lint/render suites stay green) + manual.
  - Files: `scripts/eval.py`, `scripts/lint.py`, `scripts/render.py`.
- [x] **T8 — Live smoke (verify).** real codex + claude judge on a temp wiki, gating both ways.
  - Acceptance: report produced; seeded contradiction fails gate, clean wiki passes.
  - Verify: manual live run (this machine).
  - Files: none (verification).
- [x] **T9 — Integration/docs (C9).** scaffold eval.py + Operations + Scripts tables.
  - Acceptance: `SKILL.md` scaffolds `scripts/eval.py`, adds an eval Operations step + Scripts row + `.eval/` gitignore note; CONVENTIONS + README updated.
  - Verify: manual read; field/command consistency across SKILL.md / CONVENTIONS / README.
  - Files: `SKILL.md`, `_templates/CONVENTIONS.md`, `README.md`.
