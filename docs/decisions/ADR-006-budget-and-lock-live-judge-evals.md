# ADR-006: Budget and lock live judge evals

## Status

Accepted

## Date

2026-07-14

## Context

One command can fan out into substantially more model work than its visible wiki diff suggests. Grounding invokes the judge once per derived page, contradictions and redundancy add corpus-level calls, disambiguation invokes once per candidate pair, multiple runs repeat that work, and transient failures may retry. A routine one-page change can therefore consume several judge calls. Separate agent threads can also start overlapping evals against the same wiki.

Documentation alone did not make that cost boundary reliable. The evaluator auto-detected an available agent CLI and began live judging without requiring the caller to acknowledge or cap the fan-out.

## Decision

Live judge execution is now explicit, previewable, capped, and serialized per wiki:

- `--plan-judge-calls` follows the real metric paths with a local planning judge and reports the exact first-attempt call fan-out without invoking a model.
- Every live judge run requires `--max-judge-calls N`. The evaluator refuses a missing or invalid cap and refuses a plan whose known first attempts already exceed it.
- `BudgetedJudge` counts every actual attempt, including retries, and refuses the call that would exceed the cap.
- `--metric <name>` permits bounded iteration on selected semantic metrics. It cannot be combined with `--gate`, because a final gate must cover the complete judge set.
- A per-wiki non-blocking file lock rejects overlapping live judge processes.
- Run records and history retain planned, actual, and maximum calls, selected metrics, and run count.
- Routine wiki maintenance uses deterministic lint, render, and diff review. High-risk candidates are previewed first and receive at most one complete final gate unless a human deliberately authorizes another.

The existing judge adapters and retry behavior remain available inside the caller's explicit cap. Candidate-diff scoping remains the normal live-eval boundary; unscoped whole-wiki audits are deliberate exceptions.

## Alternatives Considered

### Rely on operating instructions only

Rejected. Multiple instruction surfaces had already drifted, and an accidental unscoped command still had no runtime stop.

### Remove retries

Rejected. Retries recover transient empty or malformed responses. Counting them against a hard cap preserves that resilience without allowing open-ended spend.

### Cache or batch judge results immediately

Deferred. Both can reduce cost, but they introduce invalidation and prompt-contract complexity. The hard safety boundary does not depend on either optimization.

### Track provider account quotas inside llm-wiki

Rejected for this slice. Weekly quotas are provider- and account-specific external state. The evaluator can reliably control its own per-run calls without pretending to know the remaining account allowance.

## Consequences

- Old live commands without `--max-judge-calls` fail before any model invocation.
- Callers see first-attempt fan-out before choosing a cap and can leave zero or narrowly bounded retry headroom.
- Targeted metric runs are useful diagnostics but cannot masquerade as a complete quality gate.
- Two threads cannot concurrently spend judge calls on the same wiki through this evaluator.
- Caching, batching, and cross-wiki/provider quota telemetry remain possible follow-up optimizations rather than prerequisites for safe use.
