# Context Compiler v2

The Context Compiler turns a wiki, a real question, optional seeds, a knowledge-state view, and a bounded policy into deterministic evidence. It is shared package behavior: wiki-local query and graph scripts are compatibility adapters, not forks.

## Retrieval contract

`wiki_compile_context` and `llm-wiki compile-context` use the same versioned request/response path. Discovery can search more broadly than the returned packet. Selection begins at `target_bytes` / `target_items`, expands while required evidence roles remain uncovered, and stops at sufficiency, candidate exhaustion, provider degradation, or the caller/system hard maximum.

Targets are efficiency goals. Only `max_bytes` and `max_items` are hard output ceilings. Responses report selected evidence, exact locators, authored state, derived flags, authority signals, omission reasons, exact serialized byte accounting, estimated tokens, coverage, continuation guidance, diagnostics, and stop semantics.

Missing `knowledge_state` is `unspecified`, never inferred as `current`. Retrieval score is not source authority.

## Configuration

Every current wiki declares `.llm-wiki.toml` with `schema_version = "1"` and `runtime_contract = "2"`. Content exclusions, source directory, providers, compiler targets/maximums, state field, and manual stewardship live there. Secrets, absolute registry identity, and machine-specific paths are rejected.

The base provider set is `seed`, `frontmatter`, `text`, `graph`, and `source`. To opt into loci, install the compatible loci package in the same Python environment and add `"loci"` to `[compiler].providers`. Missing or stale loci state produces a structured diagnostic and deterministic local fallback; it is never a mandatory base dependency. See [loci provider](loci-provider.md).

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

Zero candidates means `no_candidates_observed`, not `clean`. Semantic contradiction, semantic staleness, and live external-source drift remain explicit unknowns without a semantic/source review. Brain changes still go through Brain Steward and the target `wiki-agent.md`; see [Brain Steward integration](brain-steward-integration.md).

## Legacy compatibility policy

`wiki_context_pack` and `scripts/query.py --context-pack` retain their frozen v1 shapes during migration. There is no date-based removal promise. Removal requires all of:

1. every supported scaffold and registered production wiki reports runtime-contract compatibility;
2. cross-wiki exact-span acceptance remains green for the fixture, incubator, and Brain;
3. known consumers have moved to `wiki_compile_context` or explicitly accept removal;
4. at least one release window has produced no unresolved compatibility regressions;
5. rollback evidence and a documented replacement command remain available.

Until those gates are evidenced, legacy reads stay supported and MCP never executes wiki-owned traversal business logic.
