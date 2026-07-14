# Plan: Extensible Graph Retrieval Stage 7

**Status:** implemented, verified, and published on 2026-07-14

**Date:** 2026-07-14

**Scope:** roll the approved Loci-backed llm-wiki graph provider into the live
`ai_graph_ideas` and Brain runtimes, record the observed outcome without adding
a duplicate idea, preserve explicit legacy rollback, and publish only after the
separate final review gate.

## Authorization And Boundaries

Vik approved the Stage 7 shared-default rollout after Stage 6 passed isolated
cross-wiki validation. This approval authorized the required live package or
migration changes and durable local records. It did not authorize removal of
the legacy provider, code import/dependency work, arbitrary executable graph
plugins, or Stage 7 publication without the separate final gate.

Vik separately approved final publication on 2026-07-14.

The rollout order was:

1. publish the approved Stage 6 validation commits;
2. inspect both live configurations, packages, migration plans, wiki manuals,
   and working-tree boundaries;
3. update and verify `ai_graph_ideas` as the canary;
4. update and verify Brain only after the canary passed;
5. record one outcome and bounded dispositions in `ai_graph_ideas`;
6. run complete repository and cross-wiki verification;
7. stop with Stage 7 changes uncommitted and unpushed;
8. after separate approval, publish `ai_graph_ideas` as a new private repository
   and publish this Stage 7 record on the existing llm-wiki feature branch.

## Stage 6 Publication

The approved Stage 6 result was split into two commits and pushed on
`feature/context-compiler-v2`:

- `4c7340a` — `test: harden cross-wiki validation portability`;
- `2f33caa` — `docs: record stage 6 cross-wiki validation`.

The local branch and `origin/feature/context-compiler-v2` matched exactly after
the push.

## Live Discovery

Both wikis already had:

- schema version `1` and runtime contract `2`;
- compatible canonical adapters;
- a compiler configuration whose missing `graph_backend` resolves to the
  approved `loci` default;
- existing migration receipts that verified successfully;
- legacy rollback reported as available.

Migration inspect and dry-run were identical and read-only:

| Wiki | Plan hash | Operations | Blockers |
| --- | --- | ---: | ---: |
| `ai_graph_ideas` | `93731b9f943c6fdcb404eb5fd68fb95f9614662302b626095a7755794a1b23f3` | 0 | 0 |
| Brain | `ef60a419af2a0734443f1938cedfec8237270c427b0fe2afb0942c5d9fa95b69` | 0 | 0 |

No migration apply was required.

Both virtualenvs reported distribution version `0.2.0`, but installed-file
inspection showed that the old packages did not contain `graph_adapter.py` or
`providers/loci_graph.py`. Version text alone was therefore insufficient to
prove the rollout revision.

## Immutable Package

The release wheel was rebuilt from the approved llm-wiki tree with `uv build`.
It reproduced the exact Stage 6-reviewed SHA-256:

`c8c37e87397e18cd3dc972469b6874881f7e27faade1776326bec46096860965`

The wheel was installed with `uv pip install --reinstall --no-deps`, first in
`ai_graph_ideas` and then in Brain. Installed hashes for the key configuration,
compiler, graph adapter, Loci graph provider, and selection modules matched the
reviewed source in both environments.

Package rollback is not the normal safety switch. The supported behavioral
rollback remains explicit wiki configuration:

```toml
[compiler]
graph_backend = "legacy"
```

Loci failure never invokes that backend silently.

## Canary And Brain Results

### `ai_graph_ideas`

After installation:

- installed-runtime doctor reported compatible contract `2`, graph backend
  `loci`, ready graph and Loci providers, external read-only cache, and legacy
  rollback available;
- migration verification passed;
- the canonical `scripts/query.py --agent-overview --json` adapter returned
  successfully;
- a fresh-cache compile of the frozen direct-relation question retrieved the
  Faithfulness-Before-Connectivity idea and its TACTIC-KG source path;
- answer, authority, endpoint, and bridge roles were all covered, with
  `stop.reason = "sufficient"`.

Only after these checks passed was Brain updated.

### Brain

The package installation left Brain's pre-existing working-tree status
unchanged. After installation:

- installed-runtime doctor and migration verification passed with the same
  compatibility, provider, cache, and rollback states as the canary;
- the canonical adapter returned successfully;
- a fresh-cache compile asking how an incubated AI Graph Ideas idea becomes
  durable Brain maintenance covered endpoint and bridge roles, included the
  Brain Steward handoff path, and stopped as sufficient.

## Durable Recording

`ai_graph_ideas` now contains exactly one new outcome:

`outcomes/loci-graph-retrieval-shared-default-rollout-2026-07-14.md`

The existing graph-ideas ledger records bounded promoted slices for the seven
ideas named by the audit and keeps their broader database, vector, worker,
walker, co-occurrence/PPR, reranking, and agentic-research mechanisms at
`Experiment`. `index.md`, append-only `log.md`, and generated `wiki.html` were
updated. No duplicate idea was created.

The portable Brain Steward path was invoked through a read-only Anvil handoff.
It made no Brain content change: Brain already records the llm-wiki/Loci
ownership boundary, the implementation outcome is canonical in the incubator
and llm-wiki records, and adding a second page would duplicate the result
without changing Rowan's operating rules or project orientation.

No new ADR is required. The rollout implements ADR-005's existing durable
decision that Loci is the default precision traversal layer; it does not adopt
a broader policy.

## Verification

### Wiki-local

- `ai_graph_ideas` lint passed before and after render;
- render regenerated `wiki.html`;
- non-judge eval passed all deterministic gates across 122 pages;
- all 59 `ai_graph_ideas` local tests passed;
- no Codex-backed judge was run.

### Canonical repositories and live acceptance

- llm-wiki with both live acceptance roots: `326 passed, 14 subtests passed in
  28.60s`;
- Loci: `381 passed in 33.49s`;
- frozen benchmark SHA-256 remained
  `c52def1bdf592ad735149d199910f74183598eccd9ccf8064335fa0cd0e84e27`;
- `git diff --check` and documentation contract checks passed at the final
  publication gate.

### Fresh Loci index verification

| Repository | Verified symbols |
| --- | ---: |
| Loci | 984 / 984 |
| llm-wiki | 1,097 / 1,097 |
| `ai_graph_ideas` | 2,624 / 2,624 |
| Brain | 445 / 445 |

All four graph indexes reported healthy with no diagnostics.

## Review Findings

- **Correctness:** both live runtimes execute the reviewed graph provider and
  meet representative endpoint, bridge, sufficiency, and rollback checks.
- **Architecture:** Loci owns generic retrieval; llm-wiki owns domain semantics
  and final sufficiency; neither live wiki needed a content/config migration.
- **Operational safety:** immutable wheel identity replaced ambiguous version
  text; rollout was canary-first; Brain's existing work stayed untouched;
  legacy rollback remains explicit.
- **Durability:** the observed result is recorded once, and only proven slices
  of existing ideas were promoted.
- **Scope:** no legacy removal, code relationships, executable plugin system,
  model judge, or duplicate idea entered the rollout.

## Publication Outcome

Vik approved the separate final release and publication gate on 2026-07-14.
Publication remained bounded to the durable Stage 7 records:

- `ai_graph_ideas` was published as the private repository
  [`Phluxxed/ai-graph-ideas`](https://github.com/Phluxxed/ai-graph-ideas) from
  initial commit `e59d885` (`docs: publish AI graph ideas wiki`);
- this Stage 7 record and its audit/status updates were published as one
  documentation-only commit on `feature/context-compiler-v2`;
- Brain received no content commit or push, and Loci received no publication
  change from Stage 7;
- the legacy graph provider remains available only as an explicit rollback.
