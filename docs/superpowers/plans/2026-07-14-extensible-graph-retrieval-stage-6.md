# Plan: Extensible Graph Retrieval Stage 6

**Status:** approved on 2026-07-14; Stage 7 rollout authorized

**Date:** 2026-07-14

**Scope:** cross-wiki validation of the committed Stage 5 Loci graph consumer
against isolated copies of `ai_graph_ideas` and Brain. This stage does not
migrate either live wiki, change the shared runtime policy, remove the legacy
provider, or begin Loci's later code-import relationship stage.

## Review Boundary

Stage 6 validates whether the selected Stage 5 implementation is safe to
consider for shared-default rollout. It stops before Stage 7. No rollout,
release, live-wiki migration, or publication is authorized by this result.

The audit plan required cost and latency to remain inside an approved envelope
but did not state numeric limits. Stage 6 therefore made the gate explicit
before accepting results:

- endpoint, path, bridge, unsupported-shortcut, refusal, and literal metrics
  must equal the accepted Stage 5 result;
- mean complete-response estimated tokens and mean Loci tool calls must not
  increase over Stage 5;
- warmed mean latency may not exceed 125% of the Stage 5 reference
  (`2,491.143 ms` from `1,992.914 ms`);
- repeated identical corpus snapshots must produce the same timing- and
  machine-path-independent digest.

## Isolation Procedure

1. Compute full-tree SHA-256 digests for each live wiki, excluding only Git,
   virtualenv, Python cache, Loci cache, pytest cache, and ignored evaluation
   state.
2. Copy each wiki into two independent temporary roots with those same
   exclusions.
3. Prove each copy digest equals its live source before validation.
4. Index and verify both copies through Loci MCP.
5. Run every compiler, lint, query, build, installation, and degradation check
   against the temporary copies.
6. Recompute live and copied tree digests after all checks and require exact
   equality with the initial values.

The live and copied full-tree digests remained:

| Corpus | Full-tree digest |
| --- | --- |
| `ai_graph_ideas` | `e2ca218b0b1d4a989fb6b8aa48f860d853a9a03245144dccde504d37524a0a64` |
| Brain | `f1985896a5305dc935cd498d4045fc4a04fbcf948d8322c636a060ed1bfd6b2d` |

No live corpus file changed during Stage 6.

## Findings Resolved During Validation

### Atomic path locator compatibility

The existing cross-wiki MOSS case found its exact bridge text but failed its
line assertion. The acceptance helper still expected the pre-Stage-5 flat
`locator.start_line` shape, while an atomic Loci graph path correctly stores
each exact authored span under `locator.steps[].evidence`.

The acceptance harness now verifies both file and start line in either a normal
flat locator or an atomic path step. A focused regression test failed before
the helper existed and passed after it was added. Production retrieval code did
not change.

### Root-independent deterministic digest

Two byte-identical corpus copies mounted at different temporary paths initially
produced different benchmark digests despite identical non-timing traces. A
normalized artifact diff isolated the only difference to the diagnostic
`roots` field. The benchmark now retains roots in its inspectable artifact but
excludes them, like latency, from the deterministic digest.

A regression test failed under the old normalizer. After the one-line fix, two
separate mounts produced the same digest:

`b8203320e1db73cbbb57f3278dbdbe0712be6f23ede6674cdf2a534117a38027`

The historical Stage 5 digest remains valid for its original same-root runs,
but it used the root-inclusive normalizer and is not directly comparable to the
Stage 6 digest. The Brain corpus also legitimately changed between the two
stages; Stage 6 records its new content digest explicitly.

## Frozen Benchmark Result

The frozen fixture checksum remained:

`c52def1bdf592ad735149d199910f74183598eccd9ccf8064335fa0cd0e84e27`

Stage 6 corpus-content digests:

| Corpus | Markdown corpus digest |
| --- | --- |
| `ai_graph_ideas` | `f972c146d0f112ad84e72eb0b62f1e1da99ece40b7a2334f4bb52c645eeb25fc` |
| Brain | `58fccd7a3ff6ebce967908a6c9c0fd084b08a50befb7883edbd46f7fe213893c` |

| Current compiler metric | Stage 5 | Stage 6 |
| --- | ---: | ---: |
| Mean endpoint recall | 0.944 | 0.944 |
| Required path complete | 1.0 | 1.0 |
| Bridge evidence complete | 1.0 | 1.0 |
| Unsupported shortcut rate | 0.0 | 0.0 |
| Refusal ready | 1.0 | 1.0 |
| Exact required-literal recall | 1.0 | 1.0 |
| Mean evidence bytes | 26,314.5 | 26,314.5 |
| Mean complete-response estimated tokens | 15,797.3 | 15,796.7 |
| Mean MCP tool calls | 3.4 | 3.4 |
| Warmed mean latency | 1,992.914 ms | 1,888.974 / 1,883.250 ms |
| Mean generic-hub path rate | 0.025 | 0.025 |

The first cold isolated run measured `2,171.271 ms`, still below the
`2,491.143 ms` ceiling. All four positive relationship fixtures remained
sufficient with exact paths and bridge evidence. Both false-hub and both
cannot-answer fixtures remained insufficient, refusal-ready, and stopped with
`candidate_exhausted`.

## Degradation and Rollback Evidence

A clean installed wheel was invoked twice with
`LLM_WIKI_LOCI_MCP_COMMAND=definitely-missing-loci-mcp` against the temporary
`ai_graph_ideas` copy. The complete JSON responses were byte-identical with
SHA-256:

`683c7271811862d16ddee9e3e76f7239510afa4e31b3bb4b145f05ed9276db75`

Both responses:

- retained deterministic seed evidence and covered `endpoint`;
- left `bridge` uncovered and reported `sufficient: false`;
- stopped with `provider_degraded`;
- reported `LOCI_GRAPH_MCP_UNAVAILABLE` and `LOCI_MCP_UNAVAILABLE`;
- did not silently invoke the legacy graph provider.

Both installed-wheel doctor checks reported runtime contract `2`, compatible
config, `graph_backend = "loci"`, ready graph and Loci providers, and explicit
legacy rollback availability.

## Verification Evidence

- live cross-wiki acceptance: `4 tests passed` after the atomic locator harness
  correction;
- complete llm-wiki suite with both private acceptance roots enabled:
  `326 passed, 14 subtests passed` with no cross-wiki skips;
- package build: source distribution and wheel succeeded offline;
- clean install: wheel plus 31 dependencies installed into a new isolated
  virtualenv from local cache;
- wheel SHA-256:
  `c8c37e87397e18cd3dc972469b6874881f7e27faade1776326bec46096860965`;
- installed compiler smokes passed for the positive MOSS bridge and Brain's
  deliberately uncovered `current_claim` boundary;
- both wiki-local lint checks passed;
- both legacy `query.py --context-pack` smoke checks returned non-empty,
  correctly seeded packets;
- Loci verification passed `2615/2615` for each `ai_graph_ideas` copy and
  `434/434` for each Brain copy;
- frozen fixture checksum, full-tree isolation digests, compiler result
  assertions, `git diff --check`, Python compilation, and documentation checks
  are part of the final local gate.

The detailed `.eval` artifacts remain intentionally ignored because they
contain corpus excerpts and machine-local diagnostic roots.

## Five-Axis Technical Review

- **Correctness:** frozen graph metrics, exact spans, refusal behavior,
  installed-wheel behavior, and deterministic degradation all passed.
- **Readability:** Stage 6 changed only the acceptance locator helper and the
  benchmark digest normalizer; no new runtime abstraction was introduced.
- **Architecture:** llm-wiki still owns sufficiency and budgets, Loci still owns
  retrieval mechanics, and live wiki content remains outside generated graph
  state.
- **Security and boundaries:** external results remain validated by the Stage 5
  provider; all Stage 6 writes were confined to temporary roots or external
  caches; no secrets or live content writes were introduced.
- **Performance:** deterministic cost did not regress, tool calls were stable,
  and cold and warmed latency remained below the recorded ceiling.

## Review Gate

The technical verdict was **approve Stage 6 and present Stage 7 shared-default
rollout for owner approval**. Vik approved the Stage 7 rollout on 2026-07-14.

Stage 7 is recorded separately in
[`2026-07-14-extensible-graph-retrieval-stage-7.md`](./2026-07-14-extensible-graph-retrieval-stage-7.md).
