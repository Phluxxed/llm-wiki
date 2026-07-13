# Plan: Graph Shape and Traversal Audit

Status: **Stage 3 baseline completed 2026-07-13; Stage 4 paused by architecture pivot.**
The next implementation owner is loci. Resume this plan only after the loci
graph-retrieval layer passes the frozen Stage 3 benchmark and receives explicit
human approval.

## Outcome

Determine whether the canonical `llm-wiki` graph provider uses page links, path
depth, and high-degree hubs in a way that improves agent evidence retrieval.
Produce an evidence-backed production recommendation without changing traversal
behavior before the current behavior and its failure modes are visible.

This plan extends the Context Compiler v2 work governed by
[ADR-004](../../decisions/ADR-004-question-shaped-state-aware-context-compiler.md)
and [ADR-005](../../decisions/ADR-005-loci-first-class-default-traversal.md).
It does not reopen the decision that loci is the default precision traversal
layer.

## Scope

- Runtime owner: the `llm-wiki` repository and canonical Context Compiler.
- Wiki corpora: `ai_graph_ideas` and Brain only.
- Excluded: every other registered or scaffolded wiki.
- `llm-wiki` is an implementation and test surface, not a third evaluation
  corpus unless a later stage explicitly approves a repository-local fixture.

## Operating Constraints

1. Stop after every stage and present its deliverable for human review.
2. Do not start the next stage without explicit approval.
3. Stages 1-4 do not change traversal behavior.
4. Do not use subagents or a model judge for this audit unless separately
   approved.
5. Do not migrate live wikis, commit, or push without separate approval.
6. Preserve exact paths, edge reasons, omissions, and cost measurements so a
   shorter path cannot be mistaken for a better-supported path.
7. Treat connectivity as a navigation property, not evidence of semantic
   quality.

## Existing Idea Coverage

This audit evaluates existing `ai_graph_ideas` candidates rather than creating
a new idea:

- In-Database Query-Aware Traversal
- Graph-Derived Eval Fixtures
- Recall-Efficient Wiki Traversal
- Faithfulness-Before-Connectivity Graph Construction
- Governed Hybrid Retrieval Pipeline
- Cheap Deterministic Pre-Graph
- Compiled Context Fit Gate

Workflow state belongs in Continuity. The ledger and an outcome page should be
updated only after observed results exist.

## Stage 1: Read-Only Topology Snapshot

**Description:** Measure the current graph shape in `ai_graph_ideas` and Brain
without changing either corpus or the canonical runtime.

**Acceptance criteria:**

- [x] Report page count, typed edge count, connected components, and orphans for
  each corpus.
- [x] Report the largest hubs and one-, two-, and three-hop reach distributions.
- [x] Quantify how often two- or three-hop reach depends on the largest generic
  hubs.
- [x] Show concrete examples of useful paths and semantically weak hub
  shortcuts.
- [x] Record the exact commands and runtime revision used.

**Verification:**

- [x] Repeated analysis produces identical structural counts.
- [x] Existing wiki lint/query behavior remains unchanged.
- [x] `git diff` shows no Stage 1 corpus or runtime behavior changes.

**Review gate:** Decide whether a graph-shape problem is demonstrated and
whether Stage 2 is warranted.

### Stage 1 Evidence

The snapshot used canonical graph parsing from the current `llm-wiki` working
tree on base revision `9585d0b`. That working tree already contained the
uncommitted first-class loci correction. The audit added this plan only and did
not change runtime or corpus behavior.

| Corpus | Pages | Typed edges | Unique directed pairs | Unique undirected pairs | Components | Orphans |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `ai_graph_ideas` | 121 | 2,000 | 1,632 | 1,006 | 1 | 0 |
| Brain | 25 | 131 | 110 | 71 | 1 | 0 |

The canonical health command reports unique directed page pairs as
`edge_count`; the audit also counted edge types and collapsed them to unique
undirected neighbors because traversal can follow links in either direction.

| Corpus | Median one-hop reach | Median two-hop reach | Median three-hop reach |
| --- | ---: | ---: | ---: |
| `ai_graph_ideas` | 12 of 120 | 99 of 120 | 103 of 120 |
| Brain | 4 of 24 | 20 of 24 | 24 of 24 |

Every Brain page reaches the entire Brain graph within three hops. In
`ai_graph_ideas`, a typical page reaches 82.5% of all other pages within two
hops, so a fixed two-hop neighborhood is already close to broad corpus
expansion.

The largest `ai_graph_ideas` hubs are the `llm-wiki`, loci, Anvil, and Brain
entity pages, followed by the governed-state synthesis, operating ledger, and
major retrieval/evaluation ideas. Removing only the first four changes little
because the next hubs provide redundant shortcuts. Removing the ten largest
hubs makes 55.5% of otherwise reachable two-hop non-hub pairs and 22.3% of
three-hop pairs unreachable within the same limit.

Brain is smaller and more explicitly hub-dependent. Its largest hub is the
Anvil Redux project page, followed by the Anvil MCP boundary model, maintained
Brain self-model, and AI Graph Ideas project page. Removing those four makes
59.2% of otherwise reachable two-hop non-hub pairs and 50.0% of three-hop pairs
unreachable within the same limit.

Useful direct links remain visible. Examples include TACTIC-KG paper ->
Faithfulness-Before-Connectivity idea in `ai_graph_ideas`, and Codex entity -> I
Am Rowan self-model in Brain. Weak two-hop shortcuts also exist, such as a
code-graph repository article -> generic `llm-wiki` entity -> traversal pilot,
or a Brain entity -> generic Anvil Redux project -> unrelated layout/project
material.

Commands:

```bash
cd /Users/brummerv/phluxxed/ai_graph_ideas
.venv/bin/python3 scripts/query.py --graph-health --json
.venv/bin/python3 scripts/query.py --graph-health --json | shasum -a 256
.venv/bin/python3 scripts/lint.py

cd /Users/brummerv/.anvil-brain/codex
.venv/bin/python3 scripts/query.py --graph-health --json
.venv/bin/python3 scripts/query.py --graph-health --json | shasum -a 256
.venv/bin/python3 scripts/lint.py
```

The repeated health-output hashes matched within each corpus:

- `ai_graph_ideas`: `96ec7e691883b8bcaaa593dd123dfb046a45c622d71c375ab696664ef9c12844`
- Brain: `e6d58f833d775ceaf07ef1bd95368db07a1ecd988235a21321409cf4961679f8`

Both corpus lint checks passed. The additional reach and hub-dependence pass
used `PYTHONPATH=/Users/brummerv/llm-wiki/src` with the same canonical
`collect_pages` and `collect_typed_edges` functions as the runtime; it performed
read-only breadth-first traversal with hubs excluded only as intermediate
nodes.

## Stage 2: Fixture Contract and Question Approval

**Description:** Draft representative relationship questions before running a
comparative evaluation.

**Acceptance criteria:**

- [x] Each question names expected endpoint evidence, required bridge evidence,
  and an unsupported shortcut condition.
- [x] The set covers direct relations, meaningful multi-hop chains,
  hub-attractive false shortcuts, graph-inappropriate attribute questions, and
  cannot-answer cases.
- [x] The human approves or edits the exact question set before it is frozen.

**Verification:** Manual review against current source and page links. Completed
for the draft below; final freeze awaits human approval.

**Review gate:** Approve the fixture contract and exact questions.

### Stage 2 Fixture Contract

The gold contract is authored once for this audit, not for future live queries.
Each frozen fixture will contain:

| Field | Meaning |
| --- | --- |
| Fixture ID | Stable corpus and question-shape identifier. |
| Question | Natural-language request given to every route. |
| Expected endpoints | Pages and sections that contain the required answer evidence. |
| Required bridge | Page/section or explicit relation needed to justify an endpoint relationship; `none` for exact lookup or refusal fixtures. |
| Unsupported shortcut | A tempting graph path that must not be treated as answer support. |
| Expected route | Whether graph traversal should help, remain unnecessary, or decline to answer. |
| Pass condition | Endpoint recall, bridge completeness, and answer/refusal behavior required for credit. |

### Stage 2 Draft Questions

The two corpora use the same five question shapes so results are not explained
only by one corpus receiving easier questions.

| ID | Corpus | Shape | Question |
| --- | --- | --- | --- |
| `AI-D1` | `ai_graph_ideas` | Direct relation | What source supports the faithfulness-before-connectivity rule, and what concrete rule does it propose for llm-wiki? |
| `AI-M1` | `ai_graph_ideas` | Meaningful multi-hop/evidence bridge | What evidence supports query-aware graph traversal for llm-wiki, and what still has to be proven locally before adoption? |
| `AI-F1` | `ai_graph_ideas` | False hub shortcut | What evidence shows that Code-Graph-RAG improved Recall-Efficient Wiki Traversal? |
| `AI-A1` | `ai_graph_ideas` | Graph-inappropriate exact attribute | What were the exact recall, full-chain, total-model-input, and tool-round results of the link-attribution milestone? |
| `AI-C1` | `ai_graph_ideas` | Cannot answer locally | What measured recall improvement has query-aware traversal produced in Brain? |
| `BR-D1` | Brain | Direct relation | How are Codex and Rowan related in this Brain? |
| `BR-M1` | Brain | Meaningful multi-hop/evidence bridge | How can an idea incubated in AI Graph Ideas become durable Brain maintenance? |
| `BR-F1` | Brain | False hub shortcut | Does the Node Graph UI Layout pattern define how Rowan should work with Vik? |
| `BR-A1` | Brain | Graph-inappropriate exact attribute | When Brain runs quality evals, which judge should it use and why? |
| `BR-C1` | Brain | Cannot answer locally | What measured answer-quality improvement has three-hop traversal produced in Brain? |

### Stage 2 Draft Evidence Contract

| ID | Expected endpoint evidence | Required bridge | Unsupported shortcut or claim |
| --- | --- | --- | --- |
| `AI-D1` | TACTIC-KG paper `Graph / Agent Relevance`; Faithfulness-Before-Connectivity `Proposed Graph Move` and `Brain / Wiki Improvement` | Direct paper -> idea link plus the source-backed local-transfer statement | Paper -> generic `llm-wiki`/loci entity without retrieving the rule or supporting span |
| `AI-M1` | Query-Aware Spreading Activation paper; In-Database Query-Aware Traversal `Proposed Graph Move`, `Evidence To Gather`, and `Next Experiment` | Direct paper -> idea link; source result must remain distinct from the unproven local transfer | Paper -> generic entity -> traversal page, or presenting source-reported results as a Brain/llm-wiki local result |
| `AI-F1` | Code-Graph-RAG repository article; Recall-Efficient Wiki Traversal idea/outcomes | No authored evidence bridge currently exists; the correct answer must say that no improvement evidence was found | Code-Graph-RAG -> `llm-wiki` or loci entity -> Recall-Efficient Wiki Traversal treated as causal/support evidence |
| `AI-A1` | Link-Attribution Milestone `Observed Outcome` and `Next Action`, backed by its evidence artifact | None; exact section retrieval should own the answer | Neighboring idea summaries or graph paths that omit or alter `0.583`, `1/3`, `23.6%`, or `50.0%` |
| `AI-C1` | In-Database Query-Aware Traversal evidence gaps/next experiment and the absence of a Brain-local outcome | None; answer must distinguish source evidence from missing local measurement | Paper benchmark results or Stage 1 topology reach represented as measured Brain recall gain |
| `BR-D1` | Codex entity; I Am Rowan `Operating Rule` and scope | Direct Codex -> I Am Rowan body link, preserving runtime-label versus collaboration-identity scope | Collapsing Codex and Rowan into an unscoped universal identity claim |
| `BR-M1` | AI Graph Ideas `Current Orientation`/`Important Boundaries`; Brain Steward Handoff Path `What Changed`/`Why It Mattered` | AI Graph Ideas -> Anvil Redux or Anvil MCP -> Brain Steward handoff, plus the explicit promotion boundary | Connectivity represented as automatic promotion of every incubated idea into Brain |
| `BR-F1` | Node Graph UI Layout `Durable Pattern`; Working With Vik `Operating Rule` | No supporting relation exists; the correct answer is that UI layout does not define collaboration behavior | The real three-hop path through Anvil Redux and Vik treated as semantic support |
| `BR-A1` | Brain Eval Uses Owner Judge `Operating Rule`, `Failure Mode It Prevents`, and `Evidence` | None; exact self-model section retrieval should own the answer | Selecting whichever agent CLI is linked or appears first instead of the owner judge |
| `BR-C1` | No Brain page records an answer-quality traversal result | None; answer must remain unknown pending Stage 3 measurement | Treating three-hop reach of `24/24` pages as an answer-quality improvement |

### Stage 2 Path Verification

- TACTIC-KG has a direct authored link to Faithfulness-Before-Connectivity.
- Query-Aware Spreading Activation has a direct authored link to
  In-Database Query-Aware Traversal.
- Code-Graph-RAG reaches Recall-Efficient Wiki Traversal in two hops through the
  generic `llm-wiki` entity, but neither endpoint authors a direct relation.
- Brain's AI Graph Ideas project reaches the Brain Steward handoff in two hops
  through Anvil Redux/Anvil MCP material.
- Node Graph UI Layout reaches Working With Vik in three hops through Anvil
  Redux and the Vik entity, while their page contents describe unrelated UI and
  collaboration rules.
- Exact attribute and cannot-answer fixtures were checked against the current
  indexed sections; no model judge or evaluation route was run.

## Stage 3: Current-Behavior Baseline

**Description:** Run the approved questions against loci/search-only, direct
links, bounded one/two/three-hop traversal, and the current compiler route.

**Acceptance criteria:**

- [x] Report endpoint recall separately from bridge/path completeness.
- [x] Report unsupported path rate, generic-hub shortcut rate, bytes, estimated
  tokens, tool calls, and latency.
- [x] Preserve the actual selected evidence and path for inspection.
- [x] Stop if graph traversal shows no actionable defect or value.

**Verification:** Re-run deterministic cells and compare structured outputs.

**Review gate:** Decide whether any runtime change is justified.

### Stage 3 Evidence

The approved fixture contract is frozen at
`tests/fixtures/graph_shape_traversal_stage3.json`. The deterministic read-only
runner is `scripts/graph_shape_baseline.py`, with scoring/contract regression
tests in `tests/test_graph_shape_baseline.py`. It does not call a model judge or
generate answers.

Two complete accepted runs produced the same timing-excluded digest:

`f9cf6b39b81c111cb166161b6f2e6ed7046d07304769a5b036f40abbbf9ea893`

The latest raw trace remains local at
`.eval/graph-shape-traversal-stage3-run5.json` (1,860,555 bytes). It preserves
selected compiler evidence, omissions, coverage/stop state, relevant graph
paths, anchor counts, route bytes, tool-call counts, and latency. `.eval/` is
intentionally untracked because these traces contain corpus excerpts and
machine-local paths; the frozen contract, deterministic runner, digest, and
aggregate results are the reviewable repository evidence.

| Route | Endpoint recall | Required path complete | Bridge evidence complete | Unsupported shortcut rate | Cannot-answer/refusal ready | Exact-literal recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| No graph: loci/frontmatter/text/source | 5.6% | 0% | 0% | 0% | 0% | 0% |
| Direct graph links, raw candidate route | 100% | 75% | 0% | 0% | n/a | 0% |
| Two-hop graph, raw candidate route | 100% | 100% | 0% | 50% | n/a | 0% |
| Three-hop graph, raw candidate route | 100% | 100% | 0% | 100% | n/a | 0% |
| Current Context Compiler | 5.6% | 0% | 0% | 0% | 0% | 0% |

The raw graph endpoint result is not useful recall. Question matching selected
nearly the whole corpus as anchors before traversal:

- `ai_graph_ideas`: 121/121 anchors on four fixtures and 115/121 on the fifth.
- Brain: 25/25 anchors on four fixtures and 24/25 on the fifth.

Consequently, direct-link expansion already generated the graph's broad edge
set. Its mean raw link-evidence footprint was 190,133 bytes (about 47,534
estimated tokens). Two and three hops added little endpoint coverage but raised
the mean proportion of paths with a generic hub as an intermediate to 55.2%
and 60.1% respectively. Two hops accepted one of the two unsupported
relationships; three hops accepted both.

The graph paths still did not provide the semantic bridge evidence needed to
answer the questions. Reaching the right pages and finding a path were therefore
insufficient on every required-bridge fixture.

The current compiler selected only one of eighteen expected endpoint-page
slots across the nine fixtures with endpoint gold (5.6%). It recovered none of
the four required bridge paths, none of the required bridge evidence, and none
of the exact literals in the two exact-answer fixtures. It marked all ten
questions `sufficient`, including both false-relationship questions and both
cannot-answer questions.

Question-shape routing was also brittle. The compiler activated the graph only
for `AI-D1` because `connectivity` matched `connect`, and `AI-A1` because
`link-attribution` matched `link`. It did not classify the explicitly
query-aware traversal or Brain relationship questions as relationship-shaped.

The compiler's selected evidence averaged only 3,613 bytes, but its complete
response averaged 25,611 estimated tokens because omissions are part of the
response envelope. On `AI-D1` and `AI-A1`, broad graph candidate generation
produced response envelopes above 372,000 bytes, or roughly 94,000 estimated
tokens, despite selecting only two evidence items.

Mean measured route work:

| Route | Evidence/candidate bytes | Estimated tokens | Tool calls | Mean latency |
| --- | ---: | ---: | ---: | ---: |
| No graph | 2,350 selected | 588 selected | 2 | 1.14 s |
| Direct graph | 190,133 raw | 47,534 raw | 0 | 27 ms |
| Two-hop graph | 190,247 raw | 47,562 raw | 0 | 29 ms |
| Three-hop graph | 190,427 raw | 47,607 raw | 0 | 33 ms |
| Current compiler | 3,613 selected | 25,611 complete response | 2 | 1.9 s |

Stage 3 demonstrates actionable defects, but it does not select a local
`llm-wiki` fix. Review on 2026-07-13 concluded that generic anchor selection,
typed-edge storage, graph traversal, path ranking, hub control, and retrieval
provenance belong in loci so every indexed repository can benefit. `llm-wiki`
should contribute wiki semantics and retain answerability, knowledge-state,
sufficiency, and final context-compilation policy.

The frozen fixtures, deterministic runner, and accepted digest remain the
cross-repository acceptance benchmark for that loci work. Do not tune the local
graph provider while the upstream layer is being designed; doing so would
create a second traversal engine and make the later integration harder.

Command:

```bash
PYTHONPATH=src .venv/bin/python3 scripts/graph_shape_baseline.py \
  --contract tests/fixtures/graph_shape_traversal_stage3.json \
  --ai-graph-root /Users/brummerv/phluxxed/ai_graph_ideas \
  --brain-root /Users/brummerv/.anvil-brain/codex \
  --output .eval/graph-shape-traversal-stage3-run5.json
```

## Stage 4: Correction Options

**Status:** Paused. This stage is not the next action. Resume only after loci
provides the approved graph-retrieval substrate and the Stage 3 fixtures have
been replayed through it.

**Description:** If Stage 3 demonstrates a defect, present no more than three
evidence-backed policy options, such as hub penalties, edge-type policy, or
query-shaped depth/fanout.

**Acceptance criteria:**

- [ ] Each option shows before/after paths on approved fixtures.
- [ ] Each option states recall, cost, compatibility, and failure tradeoffs.
- [ ] A no-change option remains available.

**Verification:** Replay the approved fixtures with a non-production prototype
or calculated scoring trace; no runtime mutation.

**Review gate:** Human selects the exact behavior or stops the work.

## Stage 5: Local Runtime Implementation

**Description:** Implement only the selected policy in `llm-wiki` with focused
regression tests.

**Acceptance criteria:**

- [ ] Tests fail against the previous behavior and pass with the selected
  policy.
- [ ] Existing compiler contracts, diagnostics, deterministic fallback, and
  loci-first behavior remain compatible.
- [ ] Diff is limited to the approved behavior, tests, and required docs.

**Verification:** Focused tests, full local suite, build, diff check, and
before/after fixture output.

**Review gate:** Review implementation before cross-wiki validation.

## Stage 6: Cross-Wiki Validation

**Description:** Validate the selected implementation against temporary or
read-only copies of `ai_graph_ideas` and Brain.

**Acceptance criteria:**

- [ ] Both corpora meet the approved retrieval and bridge criteria.
- [ ] Cost and latency regressions remain within the approved envelope.
- [ ] Loci degradation still produces explicit diagnostics and deterministic
  fallback.
- [ ] No live wiki has been migrated or mutated.

**Verification:** Cross-wiki acceptance suite, lint/query smoke checks, package
build, and clean temporary-install smoke tests.

**Review gate:** Approve or reject shared-default rollout.

## Stage 7: Rollout and Durable Recording

**Description:** After approval, release the shared behavior to the two wiki
corpora and record the observed outcome.

**Acceptance criteria:**

- [ ] Apply only required migrations or package updates.
- [ ] Update the existing idea dispositions and add one outcome page; do not add
  a duplicate idea.
- [ ] Update docs and write a new ADR only if a durable graph policy was adopted.
- [ ] Run complete repository and cross-wiki verification.
- [ ] Commit and push only after separate explicit approval.

**Review gate:** Final release and publication approval.

## Dependency Order

```text
Stage 1 topology evidence
        |
        v
Stage 2 approved fixtures
        |
        v
Stage 3 baseline evidence ----> stop if no justified change
        |
        v
Stage 4 selected policy ------> stop if no option is acceptable
        |
        v
Stage 5 local implementation
        |
        v
Stage 6 cross-wiki validation
        |
        v
Stage 7 approved rollout
```
