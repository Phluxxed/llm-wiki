# ADR-004: Compile Question-Shaped Context with Explicit Knowledge State

## Status

Accepted; loci-specific provider posture superseded by ADR-005 and budget-boundary semantics amended by ADR-007

## Date

2026-07-12

## Context

The current context pack starts from one page, expands a fixed graph neighborhood, and includes a fixed number of related pages and source excerpts. This is useful orientation, but it does not accept the user's actual question or distinguish lookup, relationship, current-state, historical, synthesis, and maintenance needs.

It also reports an approximate character allowance without enforcing the cumulative output against that allowance. Agents cannot see why compilation stopped, which important candidates were omitted, or whether the returned material is current, historical, superseded, contradicted, weak, inferred, or simply unspecified.

The `ai_graph_ideas` generated-manifest milestone showed that better route discovery can recover evidence missed by raw search, while also showing why route recovery alone is not enough: an authored bridge was missed, total model work increased, and automatic citation scoring overstated support. Production retrieval must therefore join governed discovery with attribution, state, budgeting, and explicit incompleteness.

## Decision

Add a versioned Context Compiler as the primary agent-facing retrieval abstraction.

The compiler accepts a natural-language question, optional seed pages, a state view, an initial context target, and caller/system safety ceilings. It classifies the query, searches broadly through deterministic local providers, normalizes knowledge state, and progressively expands the selected evidence while required roles remain uncovered. It returns:

- evidence with exact provenance, roles, state, authority signals, selection reasons, and byte cost;
- omissions with explicit reasons;
- coverage and uncovered evidence roles;
- actual budget usage; and
- a deterministic stop reason and sufficiency flag.

The initial context target is not a recall ceiling. Selection may exceed it automatically when coverage is incomplete, up to the actual caller/system maximum. If evidence still cannot fit, the response exposes uncovered roles and continuation guidance rather than claiming completeness.

Knowledge-state frontmatter is additive. Missing state normalizes to `unspecified`, never implicitly to `current`.

`wiki_context_pack` remains a compatibility surface for one deprecation window. The new compiler is additive because a seed-neighborhood pack and a question-shaped evidence compilation are not equivalent operations.

The initial provider set is local and deterministic. The original optional posture for `loci` is superseded by [ADR-005](./ADR-005-loci-first-class-default-traversal.md). Subagents, vector search, remote retrieval, and model-assisted classification are not required and may only be added behind the provider/query-shaping interfaces after separate review.

## Alternatives Considered

### Improve the Existing Graph Score Only

A better score could change which neighboring pages are selected, but it would not introduce the question, state view, evidence roles, enforced budget, omission reporting, or stop semantics.

Rejected as insufficient.

### Make Bounded Subagents the Primary Architecture

Workers could explore several routes in parallel, but they add coordination cost and do not by themselves provide provenance, state, authority, or a stable result contract.

Rejected as the base architecture. Workers remain a possible future provider or escalation policy when direct bounded routes are insufficient.

### Use Semantic Search or a Vector Database First

Semantic retrieval can improve recall for vocabulary mismatch, but adds index lifecycle and dependency complexity while leaving the evidence-state and budgeting problems unsolved.

Rejected for the base path. It may become an additive provider later.

### Let the Calling Agent Traverse Manually

Manual tool use is flexible, but repeats route selection and cost-control logic in every session and makes behavior hard to test across Brain and other wikis.

Rejected as the production contract. Manual reads remain available for inspection and follow-up.

## Consequences

- Retrieval becomes shaped by the user's task rather than only by a seed page.
- Agents receive explicit evidence and incompleteness signals rather than an opaque context blob.
- Initial context size is an optimization target rather than a one-shot limit; caller/system maximums remain enforceable and testable.
- Token counts remain estimates unless a tokenizer is configured.
- Wiki authors may progressively adopt knowledge-state metadata without a destructive bulk migration.
- The compiler must maintain deterministic provider, selection, and tie-breaking behavior for fixture tests.
- Provider failure must be visible without discarding valid evidence from other providers.
- A sufficiency flag describes deterministic evidence-role coverage, not final-answer correctness.
- Content writes and Brain promotion remain outside MCP and under existing wiki governance.

The detailed meaning of the caller's byte and estimated-token ceilings, bounded omission reporting, and post-coverage Loci selection is amended by [ADR-007](./ADR-007-enforce-complete-response-context-budgets.md).

## Follow-Up

- Approve the [Context Compiler v2 spec](../superpowers/specs/2026-07-12-context-compiler-v2-design.md).
- Freeze cross-wiki questions and gold evidence spans before implementation.
- Write a new ADR before making a model, vector store, remote corpus, or worker runtime mandatory.
