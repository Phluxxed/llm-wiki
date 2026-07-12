# Context Compiler v2 release evidence

Date: 2026-07-12
Package: `llm-wiki 0.2.0`
Runtime contract: `2`

## Shipped capability

- canonical page/graph/runtime core and strict versioned wiki config;
- deterministic question shaping, local providers, progressive selection, exact budgeting, provenance/state/coverage/stop contracts;
- optional read-only loci provider;
- MCP and CLI compiler parity;
- frozen legacy context-pack behavior without wiki-local business-logic execution;
- doctor and receipt-backed inspect/dry-run/apply/verify/rollback;
- fresh-scaffold config and thin adapters;
- read-only maintenance candidate packets;
- exact-span cross-wiki acceptance.

## Real-wiki rollout evidence

| Wiki | Receipt | Result |
| --- | --- | --- |
| `ai_graph_ideas` | `20260712T114924Z-68ff9c234bed` | Target-runtime rehearsal and rollback passed; live doctor/verify, legacy context, lint, render, non-judge eval, and exact MOSS bridge acceptance passed. Authored-content hash stayed `44f00d663045c14227eec71e04c99a76dc7ffd877f21c755585bd5ff2bb538cc`. |
| Codex Brain | `20260712T115048Z-acdc26a3e7f6` | Target-runtime rehearsal and rollback passed; live doctor/verify, legacy context, lint, render, and current-boundary acceptance passed. Authored-content hash stayed `6268fe7c93ef61ab77bd0abbc4ab42ef63fd87606617f478db5bdedfb03c0b06`. |

The Brain case deliberately reports `current_claim` uncovered because its pages do not author `knowledge_state`; no state was guessed or bulk-written. The maintenance smoke returned `no_candidates_observed` plus explicit unknowns for semantic contradiction, semantic staleness, and live source drift.

## Final verification

- `270` local tests passed with both live cross-wiki roots enabled.
- `uv build` produced the `0.2.0` source distribution and wheel.
- Final wheel installs in both wiki venvs reported package `0.2.0`, compatibility `compatible`, and adapter runtime `ready`.
- Installed-wheel MCP exposed `wiki_compile_context` and `wiki_maintenance_candidates`; the Brain packet remained read-only.
- loci indexed `875` symbols in the final llm-wiki tree and verified `875/875`.
- Markdown link/CLI contract tests, `compileall`, and `git diff --check` passed.

No model judge was required for compiler acceptance, and no Brain content mutation occurred.
