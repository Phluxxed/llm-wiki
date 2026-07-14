# Spec: Wiki Quality Eval (LLM-as-judge metrics)

Status: **Phase 1 — Specify (awaiting review).** Do not advance to Plan/Tasks/Implement until approved.

> **Historical record:** This original design predates the accepted live-judge circuit breaker. Current execution requires a zero-model preview, an explicit hard call cap, and a per-wiki lock as recorded in [ADR-006](../../decisions/ADR-006-budget-and-lock-live-judge-evals.md). Do not copy the uncapped example commands below into current automation.

Related intel: [`docs/okf-steal-list.md`](../../okf-steal-list.md) idea #2. Source patterns: Google `knowledge-catalog` `agents/enrichment/eval/`.

## Objective

Give llm-wiki a **scored, repeatable quality eval** (`scripts/eval.py`) that hardens the checks `lint.py` currently defers to prose ("contradiction scan and source drift require LLM — run those separately"). Today those are unrepeatable hand-waves; this turns them into named metrics with rationales, scores, thresholds, and regression gating.

Adapted from Google's enrichment eval, stripped of all cloud coupling. The metrics and the *gating machinery* are provider-agnostic; the **judge is the host agent** (Claude Code, etc.), not an API call.

**Success looks like:** an agent (or CI, with a plugged-in judge) can run `eval.py`, get per-metric scores 0..1 with specific rationales, and gate on regressions — with zero API keys or network in the default path.

## Decisions locked (2026-06-17)

| # | Decision | Choice |
|---|---|---|
| E1 | Judge | **Pluggable `Judge`, default = platform auto-detect.** The **claude** judge uses the **Claude Agent SDK** configured as a single-shot completion (tools disabled, single turn, system prompt) — NOT a raw `claude -p` agentic session, which intermittently invoked tools and returned empty output. The **codex** judge stays on `codex exec` (reliable in testing). Both inherit the user's existing login → **no new API key**. Fallback to deterministic-only + emitted brief when no judge is available. *(Revised 2026-06-17 after `claude -p` proved unreliable as a judge — see E8.)* |
| E2 | Metrics (v1) | **All four:** contradictions (cross-page), grounding/hallucination (page-vs-source), redundancy/novelty, disambiguation (near-duplicate). |
| E3 | Scope | **Regression harness** — pass/fail thresholds, gating (exit code), and multi-run averaging for judge variance. |
| E4 | Thresholds | contradictions = 1.0, grounding ≥ 0.95, redundancy ≥ 0.6, disambiguation = 1.0, structural = 1.0 (configurable; overridable in `evals/`). |
| E5 | Run count N | Default N=1; configurable up for variance-sensitive gating. |
| E6 | Adapters (v1) | `claude` + `codex`. `gemini` later (additive). Plus a `--judge <cmd>` escape hatch (E7). |
| E7 | `--judge` override | Yes — point at any CLI that takes a prompt and returns text; covers CI runners and unsupported agents with no code change. |
| E8 | Judge failure must be LOUD | Distinguish a **legitimate skip** (metric genuinely N/A — grounding with no sourced pages, disambiguation with no candidates → score `None`, quietly passes) from a **judge error** (a verdict was expected but the judge returned garbage/empty/raised after retries). A judge error is surfaced loudly (stderr + an `ERROR` row in the report, not a benign "n/a") **and fails the gate (fail-closed)** — a flaky judge must never silently let a gated metric pass. |
| E9 | claude judge dependency | The Claude Agent SDK is a new pip dep, **lazily imported** only when the claude judge runs — deterministic checks, gating, and the codex judge work without it. If the claude judge is requested and the SDK is missing, fail loudly (per E8). |

### Judge adapters — verified live 2026-06-17

| Adapter | Mechanism | Notes |
|---|---|---|
| `claude` | **Claude Agent SDK**, single-shot (tools disabled, single turn, system prompt) | Replaces `claude -p`. The raw CLI is a *full agentic session* (tools on, multi-turn) → it intermittently web-searched and returned empty output on large prompts. The SDK lets us construct a tools-off, single-turn judge properly. Keyless (inherits Claude Code auth). SDK call shape verified via claude-code-guide before building. |
| `codex` | `codex exec --skip-git-repo-check -s read-only --output-last-message <file> "<prompt>" </dev/null` | Reliable in testing (~25s/call). **MUST redirect `</dev/null`** — `codex exec` blocks forever on an open stdin pipe. Read `<file>` → `parse_json_obj`. |

Design consequence: at ~10–25s/call, a large wiki must **batch** (all rubric dims in one judge call per page) and **parallelize** judge calls (thread pool, à la Google's `max_workers=3`), or run time balloons.

### Judge architecture (E1) — three layers

A platform-auto-detected subprocess judge gives the harness an *unattended* judge, so the regression gating (E3) works in the default path — no separate key, no in-session hand-off. Three clean layers:

1. **Deterministic layer** (pure Python, always runs): structural/frontmatter validity, perf/size, near-duplicate *candidate* detection — no judge needed.
2. **Judge layer** (pluggable `Judge`): the **default** auto-detects an agent CLI on PATH (`claude -p`, `codex`, `gemini`) and drives it via subprocess, inheriting the user's machine-level auth (`~/.claude`, etc. — not a new key). Each adapter is a thin `prompt → model-text` wrapper behind the `Judge = Callable[[str], str]` seam. If **no** known CLI is found, fall back to emitting a judging brief for whoever is driving to judge inline. Verdicts are always *captured to disk* as `MetricResult` JSON, so the gating layer never cares which judge produced them.
3. **Gating layer** (pure Python, judge-agnostic): reads captured verdicts + deterministic results, applies per-metric thresholds, averages across runs, computes pass/fail, sets exit code, writes report + history.

Consequences: an agent user gets **hands-off, keyless gating** end-to-end (eval.py drives their own agent CLI as the judge). A machine with **no agent CLI** (e.g. a bare CI runner) degrades to the deterministic layer + re-scoring any previously-captured verdicts — to get judge-backed gating there, install/point at an agent CLI or set an explicit `Judge` command. Per-platform adapters are a bounded, additive maintenance surface; the exact headless invocation per CLI is verified in Plan.

## Tech Stack

- Python 3.12, stdlib + existing `pyyaml`/`markdown`. **No new pip deps** — the judge is the agent CLI driven via `subprocess`, not an imported SDK, so no provider library is added.
- Judge adapters detect `claude` / `codex` / `gemini` on PATH and invoke them headlessly (exact flags verified in Plan). Auth is inherited from the user's existing agent login; nothing new to configure for an agent user.
- Reuses `scripts/lint.py` loaders (`collect_pages`, `parse_frontmatter`, source pairing) — no duplication.
- Run record / history / config under the existing `evals/` directory (confirm `evals/evals.json` role in Plan).

## Commands (indicative — exact verbs settled in Plan)

```bash
# Default: auto-detect agent CLI, run all layers (deterministic + judge + gate), report.
# One command, hands-off, keyless for an agent user.
python3 scripts/eval.py [--runs N] [--gate] [--json]

# Pin or override the judge (skip auto-detect)
python3 scripts/eval.py --judge claude        # force a specific adapter
python3 scripts/eval.py --judge none          # deterministic-only + emit brief

# Fallback two-step (no agent CLI present): emit brief → someone judges → score
python3 scripts/eval.py collect --out evals/<run>/
python3 scripts/eval.py score evals/<run>/ [--gate] [--json]
```

## Project Structure (files this touches)

```
scripts/eval.py             → new: harness (deterministic metrics, brief emitter, gating/scoring)
scripts/lint.py             → minor: footer pointer now names eval.py; loaders imported by eval.py
tests/test_eval.py          → new: stub-judge tests for all metrics + gating layer
evals/                      → run records, thresholds config, history (reuse existing dir)
SKILL.md                    → Operations: add eval step; Scripts & Tooling; scaffold eval.py
_templates/CONVENTIONS.md   → Scripts & Tooling table + a short "Quality Eval" note
README.md                   → Scripts & Tooling row
```

## Code Style

Mirror Google's proven shapes (adapted, not copied):

```python
Judge = Callable[[str], str]  # returns model text (expected JSON); stub in tests

@dataclasses.dataclass
class MetricResult:
    name: str
    score: float | None        # 0..1; None = self-skipped (e.g. no source to ground against)
    passed: bool
    detail: str = ""           # rationale: WHY this score (must quote offending content)
    insights: str = ""         # HOW to improve
    extra: dict = dataclasses.field(default_factory=dict)

def parse_json_obj(text: str) -> dict:
    """Robust JSON extraction from a judge response (strips ```json fences, arrays)."""
```

Match `lint.py` conventions otherwise: plain functions, findings lists, markdown-checklist report, `--json` flag, report-don't-auto-fix.

## Metrics (v1)

All scored 0..1; `None` self-skips when inapplicable. Rubric metrics demand specific rationales (quote the offending lines) — Google's "generic rationales are NOT acceptable" discipline.

| Metric | Layer | Definition |
|---|---|---|
| `structural_validity` | deterministic | Reuses lint: frontmatter + section conformance. Cheap pre-gate. |
| `absence_of_contradictions` | judge | Conflicting claims across pages about the same tool/service/credential/behaviour. 1=none, 0=explicit conflict. Names both conflicting statements. |
| `grounding` (hallucination_free) | judge | Each factual claim in a *derived* page (has `source:`) is supported by its source file. `score = 1 − unsupported/total`; chunk large sources; self-skip pages with no source. |
| `redundancy_index` | judge | Does the page add synthesis beyond restating its source/template? 1=rich, 0=boilerplate. |
| `disambiguation` | judge (over deterministic candidates) | Deterministic pass finds near-duplicate candidate pairs (title/tag/source overlap); judge confirms whether each pair is genuinely distinct or should merge. |

## Testing Strategy

- `unittest` (repo standard; pytest not installed). Tests in `tests/test_eval.py`.
- **Stub judge** injected (Google's pattern) — every metric + the gating layer is tested with canned judge JSON, zero tokens/network.
- Deterministic metrics + gating (thresholds, averaging, exit code) fully unit-tested.
- Judge-dependent metrics tested via stub returning known JSON → assert score/pass/rationale wiring.

## Boundaries

- **Always:** TDD (stub-judge tests first); require **no new key** for an agent user (reuse their existing CLI auth) and degrade gracefully with no CLI; reuse lint loaders; report-don't-auto-fix.
- **Ask first:** default per-metric thresholds; default run count N for averaging; adding any pip dependency; the exact CLI verbs; which agent CLIs ship adapters in v1.
- **Never:** hardcode a single provider or require a *new* API key; import a cloud/provider SDK; couple to any cloud service; auto-edit pages based on eval findings.

## Success Criteria

1. `eval.py --judge none` runs the deterministic layer alone and produces a partial scored report + an emitted brief.
2. With a stub judge, all four judge metrics produce `MetricResult`s with scores + rationales; `tests/test_eval.py` green.
3. The gating layer applies thresholds, averages N runs, and sets a non-zero exit code on a seeded regression — all in pure Python, judge-agnostic.
4. On a machine with an agent CLI on PATH: a single `eval.py --gate` auto-detects the judge, runs all layers, and yields a pass/fail report with specific findings — **no new key configured**.
5. With no agent CLI: eval.py degrades to deterministic + emitted brief (no crash), and the two-step `collect`/`score` path still gates on captured verdicts.
6. `lint.py`'s deferred-checks footer points at `eval.py`.

## Plan-phase checks (resolved questions moved to Decisions E4–E7)

1. **`evals/` reuse.** Confirm what `evals/evals.json` currently is before writing run records there.
2. **CLI verbs.** Single auto-detect `eval.py` + the `collect`/`score` fallback. Settle exact verbs/flags in Plan.
3. **Codex `--output-schema` per metric.** The batched rubric returns a dict keyed by dimension — the schema must describe that object (or skip schema for the batched call and rely on `parse_json_obj`).
