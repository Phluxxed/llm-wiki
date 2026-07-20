# ADR-007: Enforce Complete-Response Context Budgets

## Status

Accepted

## Date

2026-07-20

## Context

The compiler enforced `max_bytes` only against serialized evidence records. It then appended the query envelope, coverage, stop state, continuation, every omission, and every diagnostic without enforcing the caller's limit against that complete response. A real 16,384-byte Brain recall request returned more than twice its ceiling even though its useful evidence occupied less than 4,500 bytes. The oversized compiler result then forced the Anvil prompt adapter to truncate context that should have fitted safely.

Selection also treated the top three Loci results as supplementary evidence even after every required evidence role was covered. That rule admitted unrelated but lexically overlapping pages, expanded the omission audit trail, and made retrieval rank behave like a relevance entitlement rather than an ordering signal.

The public documentation was internally inconsistent: the operating guide called `max_bytes` a hard output ceiling, while the original design spec allowed required envelope metadata to exceed it. A production safety boundary cannot have two meanings.

## Decision

`max_bytes` applies to the complete serialized `compiled_context` response. When supplied, `max_estimated_tokens` is an additional complete-response ceiling using the existing deterministic estimate of one token per four serialized UTF-8 bytes, rounded up. `max_items` continues to limit selected evidence records.

The compiler preserves useful evidence first, keeps atomic evidence indivisible, and bounds variable reporting metadata. Detailed omission and diagnostic rows are capped at 16 each and may be reduced further when the complete response requires it. The additive `reporting` object states total and returned counts so detail compaction remains visible. Compacted continuation guidance retains the number of remaining candidates while detailed candidate identities remain available through any returned omission rows. Evidence records expose their `atomic` status. If the irreducible response contract cannot fit, compilation fails with the structured `BUDGET_TOO_SMALL` error rather than returning an oversized response.

Loci retrieval rank remains an ordering signal. Indexed-section results are selected after initial coverage only when they add a still-uncovered evidence role. The existing bounded exception for query-selected graph paths remains because distinct authored relationship paths can materially support a relationship answer even when they share the same role.

These safety and relevance corrections apply to compiler contract version 1. They add observability fields but do not remove or rename existing fields. Oversized responses were not a safe compatibility guarantee, and retaining them under a new version would leave default and existing callers exposed to the known failure.

This ADR amends only ADR-004's budget-boundary semantics and the compiler's Loci supplementary-selection rule. ADR-004 and ADR-005 otherwise remain accepted.

## Alternatives Considered

### Cap only the omission list

Rejected because large evidence, diagnostics, continuation metadata, or a long query could still violate the declared response ceiling.

### Leave estimated tokens advisory

Rejected because callers use the field as a maximum and the deterministic estimate is already part of the response contract. It remains an estimate, but the compiler can and must enforce that estimate consistently.

### Preserve the fixed top-three Loci minimum

Rejected because a retrieval position orders possible evidence; it does not prove that the evidence adds anything after coverage is complete.

### Return an empty oversized envelope for very small budgets

Rejected because it would still violate the caller's ceiling and disguise an impossible request as a valid compiled context.

## Consequences

- Every successful compiled-context response fits both complete-response ceilings supplied by the caller.
- Callers can distinguish complete detail from compacted reporting through explicit counts.
- Very small budgets now fail deterministically with the minimum required response size.
- Atomic graph evidence is never converted into a partial quotation during final response fitting.
- Loci can still retrieve broadly, but redundant top-ranked hits no longer enter the selected answer automatically.
- Existing clients must tolerate the additive `reporting` field and `evidence[].atomic` field, as required for versioned JSON object evolution.
