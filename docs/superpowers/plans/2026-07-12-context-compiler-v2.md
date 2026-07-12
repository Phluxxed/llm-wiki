# Plan: Context Compiler v2 and Versioned Wiki Runtime

Implements the approved [Context Compiler v2 specification](../specs/2026-07-12-context-compiler-v2-design.md), [ADR-003](../../decisions/ADR-003-canonical-runtime-and-versioned-wiki-contract.md), and [ADR-004](../../decisions/ADR-004-question-shaped-state-aware-context-compiler.md).

Status: **Completed 2026-07-12.** Release evidence: [Context Compiler v2](../../releases/2026-07-12-context-compiler-v2.md).

## Outcome

Ship one canonical, production-grade context runtime that serves every compatible wiki; compiles evidence around a real question; searches broadly before progressively expanding selected context; reports provenance, knowledge state, omissions, coverage, cost, and stop state; preserves the existing context-pack contract during migration; and is proven against a fixture, `ai_graph_ideas`, and Brain.

This is the complete production path, delivered in reversible slices. It is not a throwaway MVP and does not declare the feature complete after only the first vertical slice.

## Approved Assumptions

1. Canonical `llm-wiki` owns shared runtime behavior; wiki-local scripts become compatibility adapters.
2. Plain Markdown remains canonical and readable without the runtime.
3. Progressive retrieval starts with target bytes/items and expands automatically while evidence roles remain uncovered, up to caller/system maximums.
4. Token counts are estimates unless a supported tokenizer is configured.
5. Missing knowledge state is `unspecified`; migration does not bulk-guess page state.
6. Existing context-pack interfaces remain compatible during a measured migration window.
7. MCP stays read-only for wiki content; Brain mutations stay under Brain Steward governance.

## Grounding Facts Verified Against Current Code

- `pyproject.toml` packages only `src/llm_wiki_mcp`; there is no canonical core package yet.
- `src/llm_wiki_mcp/wiki_runtime.py::_load_query` imports `scripts/query.py` and `scripts/wiki_graph.py` from each registered wiki, so existing wiki behavior is owned by copied scripts.
- `scripts/query.py::build_context_pack_data` expands two graph hops, takes twelve related pages, reports `tokens * 4`, and applies independent fixed excerpt limits; it does not enforce a cumulative pack limit.
- Frontmatter parsing and page collection are independently implemented in `query.py`, `wiki_graph.py`, `lint.py`, and `render.py`.
- `src/llm_wiki_mcp/registry.py::doctor` currently checks only basic wiki/tooling presence; it has no schema/runtime/drift status.
- The existing MCP error boundary already uses the stable `{code, message, details}` envelope through `WikiMcpError`.
- The current MCP tool registration and runtime tests provide stable seams for additive `wiki_compile_context` coverage.
- `loci` exposes read-only `search_symbols` and `get_cached_file` service functions with optional freshness checks. Integration can therefore remain optional and fail explicitly without making `loci` a base dependency.
- The working tree already contains unrelated user edits in `README.md`, `SKILL.md`, templates, eval/lint code, and tests. Implementation must not overwrite, stash, or absorb them accidentally.

## Dependency Graph

```text
Frozen legacy contracts
          |
          v
Versioned config + canonical page/graph core
          |
          v
Compiler contracts + state + query shaping
          |
          v
Local providers -> progressive selector -> compiled response
          |                 |
          |                 v
          |          MCP + canonical CLI
          v                 |
Optional loci provider      v
                      legacy adapter
                            |
                            v
                 doctor + migration engine
                            |
                    scaffold/adapters
                            |
                            v
            ai_graph_ideas -> Brain rollout
```

The critical path is sequential through the public contracts, canonical core, first complete compiler path, compatibility adapter, migration engine, and real-wiki rollouts. Documentation and optional-provider work can overlap only after the relevant contract is frozen.

## Workspace Safety Before Implementation

- Create a short-lived feature branch or clean worktree from the intended base commit.
- Preserve all pre-existing dirty changes; do not stash, reset, or include them without explicit approval.
- Carry the approved spec, ADRs, and plan into the implementation branch before code changes.
- Use atomic commits per checkpoint; no commit or push is implied by this plan without user authorization.

## Phase 1: Freeze Compatibility and Establish the Canonical Core

### Task 1: Freeze the existing public behavior

**Description:** Capture golden fixtures for current context-pack, MCP error, safe path, page resolution, and CLI JSON behavior before changing execution ownership.

**Acceptance criteria:**

- [x] Golden fixtures cover the current `context_pack` field set and representative bounded content.
- [x] MCP errors preserve `{code, message, details}` and `isError` behavior.
- [x] Tests fail if a legacy field disappears or unsafe source access becomes possible.

**Verification:**

- [x] `.venv/bin/python3 -m unittest discover -s tests -v` passes before production code changes.
- [x] Deliberately removing one golden field makes the focused compatibility test fail.

**Dependencies:** None.

**Files likely touched:** `tests/test_query_agent_graph.py`, `tests/test_wiki_runtime.py`, `tests/test_mcp_server.py`, `tests/fixtures/context_pack_v1.json`.

**Estimated scope:** Medium (4 files).

### Task 2: Add versioned wiki configuration

**Description:** Introduce strict `.llm-wiki.toml` loading and typed schema/runtime compatibility without changing current wiki execution.

**Acceptance criteria:**

- [x] Config distinguishes compatible, legacy-missing, unsupported-major, and invalid states.
- [x] Unknown keys are preserved for migration round trips; secrets and absolute registry identity are rejected.
- [x] Python 3.10 TOML support is explicit and tested without silently raising the project minimum.

**Verification:**

- [x] Focused config tests cover valid, missing, unknown-key, malformed, and incompatible-version fixtures.
- [x] Full test suite remains green.

**Dependencies:** Task 1.

**Files likely touched:** `pyproject.toml`, `src/llm_wiki_core/__init__.py`, `src/llm_wiki_core/config.py`, `tests/test_core_config.py`.

**Estimated scope:** Medium (4 files).

### Task 3: Centralize page parsing and graph construction

**Description:** Add canonical page, frontmatter, safe-path, source-reference, link-resolution, and graph primitives while leaving existing scripts operational.

**Acceptance criteria:**

- [x] Core parses the existing fixture corpus identically to current query/graph behavior.
- [x] Excluded directories come from config, including the `ai_graph_ideas` `.agents` case.
- [x] Path/source containment and deterministic ordering are covered at the core boundary.

**Verification:**

- [x] Differential tests compare canonical-core results with frozen current behavior on representative fixtures.
- [x] Full test suite and `loci_verify` pass.

**Dependencies:** Task 2.

**Files likely touched:** `src/llm_wiki_core/documents.py`, `src/llm_wiki_core/graph.py`, `tests/test_core_documents.py`, `tests/test_core_graph.py`.

**Estimated scope:** Medium (4 files).

### Checkpoint A: Canonical foundation

- [x] Existing public behavior is frozen.
- [x] Config and core parsing/graph tests are green.
- [x] No MCP or wiki-local behavior has changed yet.
- [x] Review diff before beginning the new public compiler contract.

## Phase 2: Deliver the Complete Question-Shaped Compiler

### Task 4: Define compiler request, response, state, and query-shape contracts

**Description:** Implement validated, versioned data types for requests, evidence, omissions, diagnostics, coverage, budgets, continuation, and normalized knowledge state.

**Acceptance criteria:**

- [x] Contract fixtures round-trip deterministically and reject unsupported versions/state views.
- [x] Missing authored state normalizes to `unspecified`; derived flags remain separate.
- [x] Query-shape fixtures cover lookup, relationship, state, history, synthesis, and maintenance.

**Verification:**

- [x] Contract and classification tests pass without network or model calls.
- [x] Serialized response fixture matches the approved spec.

**Dependencies:** Task 3.

**Files likely touched:** `src/llm_wiki_core/contracts.py`, `src/llm_wiki_core/state.py`, `src/llm_wiki_core/query_shape.py`, `tests/test_compiler_contracts.py`.

**Estimated scope:** Medium (4 files).

### Task 5: Ship the first end-to-end compiler path

**Description:** Compile a question through seed, exact/frontmatter, and bounded lexical providers into selected evidence, coverage, omissions, usage, and a stop reason.

**Acceptance criteria:**

- [x] A question can compile with or without a seed and returns exact page/section provenance.
- [x] Provider ordering, evidence IDs, tie-breaks, and omission reasons are deterministic.
- [x] Provider failures are visible diagnostics and do not erase valid evidence.

**Verification:**

- [x] End-to-end fixture tests assert required spans rather than only page names.
- [x] Repeated identical compiles produce byte-identical structured output after excluding timing diagnostics.

**Dependencies:** Task 4.

**Files likely touched:** `src/llm_wiki_core/providers/base.py`, `src/llm_wiki_core/providers/local.py`, `src/llm_wiki_core/compiler.py`, `tests/test_compiler.py`.

**Estimated scope:** Medium (4 files).

### Task 6: Add graph-bridge and source-evidence providers

**Description:** Add links, backlinks, bounded connecting paths, source references, and safe source excerpts as evidence routes with distinct authority signals.

**Acceptance criteria:**

- [x] Relationship fixtures recover required bridge spans, not only endpoint pages.
- [x] Source excerpts retain exact source provenance and never escape `sources/`.
- [x] Retrieval score is never represented as source authority.

**Verification:**

- [x] Bridge, missing-source, duplicate-source, unsafe-path, and source-truncation tests pass.
- [x] The prior generated-manifest bridge failure exists as a regression fixture.

**Dependencies:** Task 5.

**Files likely touched:** `src/llm_wiki_core/providers/graph.py`, `src/llm_wiki_core/providers/source.py`, `tests/test_graph_provider.py`, `tests/test_source_provider.py`.

**Estimated scope:** Medium (4 files).

### Task 7: Implement progressive selection and continuation

**Description:** Make context size an adaptive efficiency target: start at target bytes/items, expand while required roles are uncovered, and stop only at sufficiency, candidate exhaustion, or caller/system maximums.

**Acceptance criteria:**

- [x] Candidate discovery is independent from the initial output target.
- [x] Incomplete coverage triggers deterministic expansion beyond the target up to the hard maximum.
- [x] Maximum exhaustion returns uncovered roles and continuation guidance without claiming sufficiency.

**Verification:**

- [x] Boundary tests cover UTF-8 byte accounting, item metadata, exact-target, target expansion, maximum exhaustion, and continuation stability.
- [x] A low initial target still recovers required gold evidence when it fits beneath the configured maximum.

**Dependencies:** Tasks 5 and 6.

**Files likely touched:** `src/llm_wiki_core/selection.py`, `src/llm_wiki_core/compiler.py`, `tests/test_compiler_selection.py`, `tests/test_compiler.py`.

**Estimated scope:** Medium (4 files).

### Task 8: Add optional loci navigation

**Description:** Implement `loci` as an optional read-only provider using its supported service boundary and freshness checks, while keeping the base install functional without it.

**Acceptance criteria:**

- [x] When available and configured, loci results become section-level candidate evidence with exact file locators.
- [x] Missing, stale, unindexed, or incompatible loci produces structured diagnostics and deterministic fallback.
- [x] The base `llm-wiki` install has no mandatory loci dependency.

**Verification:**

- [x] Stubbed provider tests cover success and every degradation state.
- [x] A live read-only smoke check against indexed `ai_graph_ideas` returns locators that resolve to current files.

**Dependencies:** Tasks 4 and 5; may proceed alongside Tasks 6-7 after contracts freeze.

**Files likely touched:** `pyproject.toml`, `src/llm_wiki_core/providers/loci.py`, `tests/test_loci_provider.py`, `docs/loci-provider.md`.

**Estimated scope:** Medium (4 files).

### Task 9: Expose the compiler through MCP and canonical CLI

**Description:** Add the public `wiki_compile_context` MCP tool and a canonical CLI command using the exact same request/response path.

**Acceptance criteria:**

- [x] MCP and CLI accept question, seeds, state view, target limits, and hard maximums.
- [x] Both surfaces return the same structured contract and stable error envelope.
- [x] MCP remains read-only for wiki content and registry separation is unchanged.

**Verification:**

- [x] In-process MCP tests enumerate and call the new tool successfully.
- [x] CLI/MCP parity fixture passes; invalid versions and unsafe seeds fail consistently.

**Dependencies:** Tasks 7 and 8.

**Files likely touched:** `src/llm_wiki_mcp/mcp_server.py`, `src/llm_wiki_mcp/wiki_runtime.py`, `src/llm_wiki_core/cli.py`, `tests/test_mcp_compiler.py`, `tests/test_compiler_cli.py`.

**Estimated scope:** Medium (5 files).

### Checkpoint B: Production compiler

- [x] Complete question-shaped compiler works through MCP and CLI.
- [x] Progressive expansion is proven not to turn the initial target into a recall ceiling.
- [x] Every selected span has provenance/state/reasons/cost; omissions and uncovered roles are explicit.
- [x] Default suite is deterministic and network-free.
- [x] Live loci smoke check is read-only and reproducible.

## Phase 3: Preserve Existing Users and Build Safe Migration

### Task 10: Route legacy context packs through canonical core

**Description:** Replace the MCP's dynamic execution of wiki-local query business logic with canonical core while preserving the frozen legacy response and CLI behavior.

**Acceptance criteria:**

- [x] Existing `wiki_context_pack` output passes all Task 1 golden tests.
- [x] Registered wiki reads no longer execute independently authored query/graph business logic.
- [x] Legacy missing-config wikis remain supported during the migration window.

**Verification:**

- [x] Golden, MCP, CLI, and safe-path compatibility suites pass.
- [x] A test-only poisoned local query script cannot alter MCP context-pack behavior.

**Dependencies:** Task 9.

**Files likely touched:** `src/llm_wiki_mcp/wiki_runtime.py`, `src/llm_wiki_core/legacy.py`, `tests/test_wiki_runtime.py`, `tests/test_legacy_context_pack.py`.

**Estimated scope:** Medium (4 files).

### Task 11: Expand doctor into the compatibility truth surface

**Description:** Report config/schema/runtime compatibility, script drift, provider readiness, and migration state without mutating wiki content.

**Acceptance criteria:**

- [x] Doctor returns exactly one compatibility status and actionable structured details.
- [x] Script drift distinguishes canonical legacy copies, supported customization, unknown modification, and missing adapters.
- [x] Provider readiness includes loci availability/freshness only when configured.

**Verification:**

- [x] Doctor matrix tests cover every advertised compatibility state.
- [x] Existing registry isolation and missing-path behavior remain green.

**Dependencies:** Tasks 2, 8, and 10.

**Files likely touched:** `src/llm_wiki_mcp/registry.py`, `src/llm_wiki_core/doctor.py`, `tests/test_registry.py`, `tests/test_doctor.py`.

**Estimated scope:** Medium (4 files).

### Task 12: Implement migration inspect and dry-run

**Description:** Inventory a wiki, classify local deltas, translate supported customization into config, and produce an exact no-write migration proposal.

**Acceptance criteria:**

- [x] Inspect/dry-run perform no writes and report semantic operations plus blockers.
- [x] Known `.agents` exclusion and other supported local deltas translate into config.
- [x] Unknown script modifications block replacement rather than being discarded.

**Verification:**

- [x] Before/after filesystem hashes prove inspect/dry-run are read-only.
- [x] Standard, customized, unknown-drift, and already-current fixtures pass.

**Dependencies:** Task 11.

**Files likely touched:** `src/llm_wiki_core/migration.py`, `src/llm_wiki_core/script_drift.py`, `tests/test_migration_inspect.py`, `tests/fixtures/migrations/`.

**Estimated scope:** Medium (4 surfaces).

### Task 13: Implement migration apply, verify, and rollback

**Description:** Apply approved operations transactionally, install compatibility adapters/config, write a receipt, verify the result, and restore the prior state on demand.

**Acceptance criteria:**

- [x] Apply is idempotent, receipt-backed, and never edits `sources/` or bulk-labels page state.
- [x] Injected partial failure leaves either the original state or a complete recoverable receipt.
- [x] Rollback restores pre-migration hashes and verify reports the restored compatibility state.

**Verification:**

- [x] Failure-injection, idempotence, verify, and rollback tests pass in temporary repositories.
- [x] Lint/render/compiler smoke checks run as explicit verify steps.

**Dependencies:** Task 12.

**Files likely touched:** `src/llm_wiki_core/migration.py`, `src/llm_wiki_core/receipts.py`, `tests/test_migration_apply.py`, `tests/test_migration_rollback.py`.

**Estimated scope:** Medium (4 files).

### Task 14: Update scaffold and wiki-local adapters

**Description:** Make newly scaffolded wikis emit `.llm-wiki.toml` and thin query/graph adapters, and expose the explicit migration workflow for existing wikis.

**Acceptance criteria:**

- [x] A fresh wiki declares compatible config and contains no copied compiler business logic.
- [x] Adapter absence/incompatibility fails with actionable install/upgrade guidance.
- [x] The scaffold instructions never auto-migrate an existing wiki without consent.

**Verification:**

- [x] Fresh-scaffold smoke wiki passes doctor, lint, render, compiler, and legacy context-pack checks.
- [x] Missing-runtime and incompatible-runtime adapter tests fail loudly.

**Dependencies:** Task 13.

**Files likely touched:** `SKILL.md`, `_templates/CONVENTIONS.md`, `scripts/query.py`, `scripts/wiki_graph.py`, `tests/test_scaffold_contract.py`.

**Estimated scope:** Medium (5 files).

### Checkpoint C: Compatibility and migration

- [x] Existing context-pack consumers remain green.
- [x] MCP no longer executes wiki-owned traversal implementations.
- [x] Doctor and every migration phase are test-covered.
- [x] Fresh scaffolds use canonical runtime plus thin adapters.
- [x] Rollback has been demonstrated before any real wiki is changed.

## Phase 4: Prove and Roll Out Across Real Wikis

### Task 15: Freeze cross-wiki acceptance fixtures

**Description:** Before migration, define fixed questions, state views, budgets, and exact gold evidence spans for the fixture wiki, `ai_graph_ideas`, and Brain.

**Acceptance criteria:**

- [x] `ai_graph_ideas` includes a relationship query requiring an authored bridge.
- [x] Brain includes a current-state query with historical/stale distractors.
- [x] Fixtures assert spans, provenance, state, expansion, and stop semantics—not an LLM judge score alone.

**Verification:**

- [x] Fixtures fail against a deliberately incomplete provider result.
- [x] Gold spans resolve against current live files before implementation output is graded.

**Dependencies:** Checkpoint B; must complete before real-wiki migration.

**Files likely touched:** `tests/cross_wiki/cases.yaml`, `tests/cross_wiki/test_acceptance.py`, `tests/cross_wiki/README.md`.

**Estimated scope:** Medium (3 files).

### Task 16: Migrate and verify `ai_graph_ideas`

**Description:** Use the production migration flow on the incubator wiki first, translating its `.agents` exclusion into config and preserving all content/evidence boundaries.

**Acceptance criteria:**

- [x] Inspect and dry-run show only approved config/adapter operations with a valid rollback receipt plan.
- [x] Apply/verify passes wiki lint, render, doctor, legacy compatibility, and fixed compiler acceptance cases.
- [x] Raw ideas remain in `ai_graph_ideas`; nothing is promoted into Brain.

**Verification:**

- [x] Pre/post content hashes prove sources and authored pages were not rewritten by tooling migration.
- [x] Rollback is rehearsed in a disposable clone or worktree before accepting the live migration.

**Dependencies:** Tasks 14 and 15, Checkpoint C.

**Files likely touched:** `/Users/brummerv/phluxxed/ai_graph_ideas/.llm-wiki.toml`, wiki-local compatibility adapters, migration receipt, acceptance run record.

**Estimated scope:** Medium (cross-repo, tightly bounded).

### Task 17: Migrate and verify Brain

**Description:** Migrate Brain only after the incubator rollout passes, preserving its durable-knowledge policy and read-only MCP mutation boundary.

**Acceptance criteria:**

- [x] Dry-run has no unknown script drift or unresolved policy translation.
- [x] Apply/verify passes Brain lint, render, doctor, legacy compatibility, and fixed current-state acceptance cases.
- [x] No Brain page is automatically state-labelled, rewritten, or promoted from the incubator.

**Verification:**

- [x] Pre/post hashes and receipt prove the exact bounded tooling changes.
- [x] Brain Steward can consume compiler diagnostics as candidates while MCP remains unable to mutate pages.

**Dependencies:** Task 16 must be fully accepted.

**Files likely touched:** Brain `.llm-wiki.toml`, wiki-local compatibility adapters, migration receipt, acceptance run record.

**Estimated scope:** Medium (cross-repo, tightly bounded).

### Task 18: Add maintenance candidate packets

**Description:** Convert compiler/doctor findings into a stable read-only packet for stale-current claims, contradictions, supersession gaps, source gaps, and runtime drift.

**Acceptance criteria:**

- [x] Packets cite exact evidence and distinguish diagnostics from proposed content changes.
- [x] Packets contain no mutation command and cannot bypass `wiki-agent.md`/Brain Steward review.
- [x] Empty/unsupported findings remain honest unknowns rather than fabricated zeroes or clean status.

**Verification:**

- [x] Fixture tests cover each finding kind and the no-findings case.
- [x] Brain read-only smoke check produces a reviewable packet without changing files.

**Dependencies:** Tasks 11 and 17.

**Files likely touched:** `src/llm_wiki_core/maintenance.py`, `src/llm_wiki_mcp/mcp_server.py`, `tests/test_maintenance_packets.py`, `docs/brain-steward-integration.md`.

**Estimated scope:** Medium (4 files).

### Task 19: Finalize documentation, deprecation policy, and release evidence

**Description:** Document operation, configuration, migration/rollback, loci integration, Brain stewardship, compatibility status, and the evidence required before legacy removal.

**Acceptance criteria:**

- [x] README/manual/API docs match shipped commands and response contracts.
- [x] Legacy context-pack removal criteria are explicit; no premature removal date is invented.
- [x] Release record includes deterministic tests and all three cross-wiki acceptance results.

**Verification:**

- [x] Local Markdown links and documented commands are checked against the live package.
- [x] Full test suite, lint/render smoke fixtures, loci verify, and cross-wiki acceptance suite pass.

**Dependencies:** Tasks 16-18.

**Files likely touched:** `README.md`, `SKILL.md`, `_templates/CONVENTIONS.md`, `docs/context-compiler.md`, release/verification record.

**Estimated scope:** Medium (5 surfaces).

### Checkpoint D: Production complete

- [x] Canonical runtime is the only shared implementation used by MCP and compatible CLI adapters.
- [x] Compiler meets every approved response, state, progressive-retrieval, provenance, and failure contract.
- [x] Fixture, `ai_graph_ideas`, and Brain acceptance suites pass.
- [x] Existing context-pack users remain compatible.
- [x] Migration and rollback evidence exists for both real wikis.
- [x] Brain content mutation remains governed and separate from read-only retrieval.
- [x] Documentation and deprecation criteria match actual behavior.

## Test and Verification Matrix

| Layer | Required evidence |
|---|---|
| Unit | config, parsing, graph, state, query shape, providers, selection, byte accounting, continuation, errors |
| Contract | compiled response goldens, MCP/CLI parity, legacy pack goldens, error envelope |
| Security/boundary | path traversal, source containment, unsafe config, registry separation, no MCP content writes |
| Migration | no-write inspect/dry-run, drift blockers, idempotence, partial failure, receipt, verify, rollback |
| Integration | fresh scaffold, missing runtime, incompatible runtime, optional loci degradation |
| Cross-wiki | exact evidence spans and state/budget semantics for fixture, `ai_graph_ideas`, Brain |
| Regression | full current test suite plus fixed generated-manifest bridge case |

Default verification is local and network-free. Live loci and real-wiki checks are read-only until their explicitly approved migration step.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Canonicalization changes legacy output | High | Freeze goldens first; route legacy only after compiler is complete; retain compatibility adapter. |
| Initial target accidentally becomes recall ceiling | High | Separate discovery from selection; auto-expand to hard maximum; assert low-target/high-recall fixture. |
| State metadata creates false certainty | High | Missing = `unspecified`; no bulk guessing; derived flags separate from authored state. |
| Shared parser disagrees with lint/render | High | Differential fixtures first, then migrate consumers incrementally with full-suite checkpoints. |
| Local wiki customization is overwritten | High | Semantic inspect/dry-run; unknown drift blocks; receipt-backed rollback. |
| loci store is missing or stale | Medium | Optional provider, freshness check, structured degradation, deterministic local fallback. |
| Cross-wiki tests overfit implementation | High | Freeze questions and exact gold spans before grading implementation; include known bridge failure. |
| Large response still exceeds a caller's real context | Medium | Caller/system hard maximum, exact bytes, uncovered roles, continuation guidance, no false sufficiency. |
| Dirty worktree mixes unrelated changes | High | Clean feature worktree/branch; preserve user changes; inspect every staged diff before any commit. |
| Production scope balloons into workers/vector search | Medium | Keep those behind future provider ADRs; they are explicit v1 non-goals. |

## Parallelization Notes

- Tasks 1-7 and 9-17 follow the critical path and should be integrated sequentially.
- Task 8 can proceed after compiler/provider contracts freeze.
- Documentation drafts can proceed after each public contract lands, but final docs wait for verified commands.
- Cross-wiki gold fixtures can be authored after the compiler contract freezes, before real migration.
- Parallel work must not modify the same public contract or real wiki simultaneously.

## Review Gate

Before implementation, confirm:

- [x] Every task has testable acceptance criteria and verification.
- [x] No task requires automatic Brain or wiki-content mutation.
- [x] Progressive retrieval—not a fixed initial cap—is represented in contracts and tests.
- [x] Real-wiki rollouts occur only after rollback is proven.
- [x] The full production outcome, not only the first compiler slice, defines completion.
- [x] The user approves this task order and implementation boundary.
