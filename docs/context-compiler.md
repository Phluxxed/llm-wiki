# Context Compiler v2

The Context Compiler turns a wiki, a real question, optional seeds, a knowledge-state view, and a bounded policy into deterministic evidence. It is shared package behavior: wiki-local query and graph scripts are compatibility adapters, not forks.

## Retrieval contract

`wiki_compile_context` and `llm-wiki compile-context` use the same versioned request/response path. Discovery can search more broadly than the returned packet. The accepted 2026-09-06 selection correction first preserves the existing deterministic initial set, including explicit seeds, relevant claims, admissible supporting sources, project fallback, and query-selected graph evidence. It then makes one bounded pass over distinct, question-qualified candidates previously omitted only for `lower_marginal_value` because their roles were already covered, in the existing relevance order. Same-role evidence may therefore be admitted after all required roles are covered. The existing query-relevance, duplicate, source-support, and state checks still apply; graph supplements still require the query-selected path contract. Selection stops for actual candidate exhaustion, provider degradation, or the existing item, content-byte, complete-response-byte, or estimated-token ceilings, not role coverage alone. Initial evidence remains before supplements in returned order so final fitting removes supplements first. This accepted correction adds no search, judge, threshold, or budget; implementation and downstream acceptance remain separate work.

Targets are preferred-size and accounting values, not safety ceilings. `target_bytes` and `target_items` may be exceeded while admissible evidence is retained or reconsidered. `max_bytes` limits the complete serialized response, `max_items` limits selected evidence records, and `max_estimated_tokens` is an additional complete-response ceiling when supplied. The token estimate is deterministic at one token per four serialized UTF-8 bytes, rounded up; it is a transport guard rather than a model-specific tokenizer claim. A request whose complete response envelope cannot fit fails with `BUDGET_TOO_SMALL` instead of returning an oversized result. The compatibility field `target_exceeded_for_coverage` records target excess, not proof of why expansion was necessary and not answerability.

Responses report selected evidence, exact locators, authored state, derived flags, authority signals, omission reasons, exact serialized byte accounting, estimated tokens, coverage, continuation guidance, diagnostics, and stop semantics. `coverage` reports required, covered, and uncovered evidence roles only. For compatibility, `stop.sufficient` remains the derived role-coverage boolean. Historical values remain accepted and readable. It does not establish answerability, confidence, readiness to answer, or an expansion cause, and the new producer does not stop solely because it is true. Actual budget, degradation, and exhaustion stops remain visible even when every role is covered. A hard-limit omission or finalizer drop keeps bounded continuation counts/IDs and omission facts regardless of coverage, subject to the existing deterministic primary-limit ordering and response compaction. Detailed omission and diagnostic rows are capped at 16 each and may be reduced further to protect the response ceiling; `reporting` always gives their total and returned counts. A compacted continuation retains `remaining_candidate_count` even when its duplicate candidate-id list is removed. Evidence marked `atomic` is omitted rather than excerpted.

Missing `knowledge_state` is `unspecified`, never inferred as `current`. Retrieval score is not source authority.

## Temporal compiler v2

The compiler keeps the v1 request and response path unchanged: an omitted
`contract_version` means `"1"`, v1 rejects `temporal`, and v1 request
serialization contains only its original fields. Contract v2 is opt-in. A v2
request without `temporal` performs ordinary compilation; a temporal request
must include a strict RFC 3339 `request_time` and one of `current`,
`historical`, `transition`, `lineage`, or `conflict` views.

`current` defaults `world_at` and `known_at` to `request_time`; `historical`
requires `world_at`; `transition` requires a `from`/`to` range and forbids
`world_at`; `lineage` and `conflict` may include `world_at`. Unknown world
bounds never satisfy point-in-time eligibility. `known_at` controls whether a
late Steward acceptance is visible, while world validity controls whether the
fact was true at the requested world time.

Temporal revisions are parsed and folded before ordinary candidate selection.
Only eligible target-wiki-Steward evidence reaches ranking. Selected records
use `provider: temporal`, a `temporal_<view>` route, a temporal claim locator,
and an atomic deterministic fact rendering capped at 4,000 UTF-8 bytes.
Contested evidence is labeled `contested` and is never presented as settled
authority. Pages without temporal revisions retain their ordinary providers
and are labeled `legacy_temporal_unspecified`.

The CLI exposes the opt-in mapping with `--contract-version 2`,
`--temporal-view`, `--request-time`, `--world-at`, `--known-at`, and
`--transition-from`/`--transition-to`. MCP accepts the corresponding
`temporal_view`, `request_time`, `world_at`, `known_at`, `transition_from`, and
`transition_to` arguments. Both surfaces pass the same strict core request;
there is no hidden clock, model call, network call, or canonical storage write.

## Configuration

Every current wiki declares `.llm-wiki.toml` with `schema_version = "1"` and `runtime_contract = "2"`. Content exclusions, source directory, providers, compiler targets/maximums, state field, and manual stewardship live there. Secrets, absolute registry identity, and machine-specific paths are rejected.

The default provider set is `seed`, `frontmatter`, `text`, `graph`, `source`, and `loci`. loci owns both indexed section navigation and the default graph mechanics for managed wikis through its local `loci-mcp` stdio service; it does not need to share the `llm-wiki` Python environment. Removing `"loci"` from `[compiler].providers` opts out of indexed-section retrieval. Removing `"graph"` opts out of graph retrieval.

`compiler.graph_backend` accepts exactly `loci` or `legacy` and defaults to `loci`. The legacy value is an explicit rollback to the previous local shortest-path provider. A failed loci graph request reports a diagnostic and returns no graph candidates; it never silently invokes the legacy backend. Other enabled providers continue independently.

## Graph retrieval and relationship sufficiency

The loci graph backend builds a machine-local mirror containing the wiki pages plus generated graph profile and contribution files. The source wiki is never given `.loci` files or otherwise mutated by a compile. The cache defaults to `${XDG_CACHE_HOME:-~/.cache}/llm-wiki/graph` and may be relocated with `LLM_WIKI_GRAPH_CACHE_DIR`.

Every returned path, node, edge, evidence line, and content hash is checked against the original wiki snapshot. Paths are atomic compiler candidates: an over-budget path is omitted rather than truncated. Retrieval score affects ordering only; it does not create knowledge state, authority, or answerability.

For inferred relationship questions, llm-wiki uses Loci's explained anchors to distinguish a path that crosses the question's separate subject clusters from a nearby path within only one cluster. Only a cross-subject path carries the `bridge` role and can satisfy relationship coverage. Ancillary paths may still be returned as `support`, but they cannot make the compiler claim the requested relationship exists. Explicitly seeded endpoint retrieval treats a validated path between those seeds as bridge evidence. Graph supplements in the second selection pass remain limited to query-selected paths under this contract; arbitrary graph-neighbourhood material is not promoted. llm-wiki still owns final selection, coverage, sufficiency, stop semantics, and budget enforcement. See [loci provider](loci-provider.md).

`LOCI_GRAPH_PATH_REJECTED` records normal negative evidence from a successful graph request. It remains visible in diagnostics but does not by itself mean the provider degraded. If all available paths are rejected or only ancillary support remains, an uncovered relationship stops as `candidate_exhausted`; transport, contract, or freshness failures remain visible as provider degradation regardless of role coverage, unless a reached budget supplies the primary stop reason.

## Migration and rollback

Inspect and dry-run perform no writes. Apply requires the exact dry-run hash, accepts only `.llm-wiki.toml` and the two compatibility adapters as targets, backs up replaced files, writes a receipt before mutation, uses atomic replacement, verifies the result, and automatically restores target files on failure.

```bash
llm-wiki doctor --wiki .
llm-wiki migrate inspect --wiki .
llm-wiki migrate dry-run --wiki .
llm-wiki migrate apply --wiki . --plan-hash <hash>
llm-wiki migrate verify --wiki .
llm-wiki migrate rollback --wiki . --receipt-id <id>
```

If a project-local `.venv/bin/python3` exists, verification proves that interpreter can load the adapter runtime. Install the canonical wheel into that venv before apply. Rollback refuses to overwrite targets changed after migration.

Receipts live under `.llm-wiki/migrations/`. Migration does not edit pages, sources, index entries, log history, or knowledge-state fields.

## Maintenance candidates

`wiki_maintenance_candidates` reports deterministic signals for runtime drift, missing source paths, explicit contradictions, missing supersession links, and explicitly current pages beyond a review-age threshold. Every candidate contains exact evidence and a review question. The packet has `mutation.allowed = false` and no commands.

`wiki_build_maintenance_candidate` canonicalizes one task-derived observation for a registered target wiki. It normalizes page endpoints and evidence, produces stable observation and dedupe identities, and marks whether the candidate is deterministic or requires recurrence/corroboration. It is also read-only: the result is a proposal for an external operational queue and target steward, not permission to edit the wiki.

`wiki_build_maintenance` is the canonical entry point for new maintenance consumers. It composes eligible discovery and task observations with temporal evidence into one `unified-maintenance/1` proposal. `wiki_maintenance_candidates` and `wiki_build_maintenance_candidate` remain supported v1 compatibility producers; `wiki_build_temporal_candidates` and `wiki_reconcile_temporal_candidates` remain exact specialist component surfaces for provenance-preserving temporal workflows. All of these surfaces are candidate-only with `mutation.allowed = false`; only the target wiki's steward may accept a proposal and mutate the wiki.

Zero candidates means `no_candidates_observed`, not `clean`. Semantic contradiction, semantic staleness, and live external-source drift remain explicit unknowns without a semantic/source review. Brain changes still go through Brain Steward and the target `wiki-agent.md`; see [Brain Steward integration](brain-steward-integration.md).

## Legacy compatibility policy

`wiki_context_pack` and `scripts/query.py --context-pack` retain their frozen v1 shapes during migration. There is no date-based removal promise. Removal requires all of:

1. every supported scaffold and registered production wiki reports runtime-contract compatibility;
2. cross-wiki exact-span acceptance remains green for the fixture, incubator, and Brain;
3. known consumers have moved to `wiki_compile_context` or explicitly accept removal;
4. at least one release window has produced no unresolved compatibility regressions;
5. rollback evidence and a documented replacement command remain available.

Until those gates are evidenced, legacy reads stay supported and MCP never executes wiki-owned traversal business logic.
