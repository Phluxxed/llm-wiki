# Plan: Extensible Graph Retrieval Stage 5

Status: **implemented; technical review gate passed; ready for owner review**

Date: 2026-07-14

Depends on:

- loci Stage 4 commit `27f8104`
- llm-wiki Context Compiler v2 commit `d15b930`
- frozen llm-wiki benchmark commit `3a21de0`

Frozen benchmark:
`tests/fixtures/graph_shape_traversal_stage3.json`

Frozen benchmark SHA-256:
`c52def1bdf592ad735149d199910f74183598eccd9ccf8064335fa0cd0e84e27`

## Goal

Replace the Context Compiler's local shortest-path graph provider with loci's
bounded, evidence-backed `loci_graph_retrieve` operation. llm-wiki continues to
own wiki parsing, knowledge state, answerability, sufficiency, candidate
selection, and the final response budget.

The existing local provider remains available as an explicit rollback backend.
Stage 5 does not remove legacy graph code, change legacy context-pack tools, add
an executable plugin boundary to loci, or mutate a wiki merely because it was
read through MCP or the compiler.

## Governing Contracts

The loci design requires Stage 5 to:

- implement the llm-wiki domain adapter/profile in llm-wiki;
- replace generic local graph mechanics with loci retrieval;
- retain llm-wiki ownership of state and final answer policy;
- preserve the old provider until compatibility and rollback are approved;
- replay the frozen benchmark through focused integration and both full suites.

The existing llm-wiki runtime additionally requires:

- compilation and MCP reads remain read-only with respect to wiki content;
- provider failure is explicit and does not fabricate participation;
- machine-specific paths stay out of `.llm-wiki.toml`;
- retrieval score never becomes an authority signal;
- partial evidence must not be presented as a complete path.

## Architecture Decisions

### 1. Keep the public provider name stable

`compiler.providers` continues to use `"graph"`. A new compiler setting selects
the implementation:

```toml
[compiler]
graph_backend = "loci"   # default
# graph_backend = "legacy"  # explicit rollback
```

Accepted values are exactly `loci` and `legacy`. Missing means `loci`. Unknown
values fail config inspection. A loci failure produces a graph-provider
diagnostic and no graph candidate; it never silently runs the legacy provider.

### 2. Use a read-only source boundary and an external cache mirror

Loci Stage 2 accepts repository-local profile and contribution files only.
Writing those files into every source wiki during a compiler read would violate
the existing read-only contract. The llm-wiki adapter therefore maintains a
machine-local mirror under:

```text
${LLM_WIKI_GRAPH_CACHE_DIR:-${XDG_CACHE_HOME:-~/.cache}/llm-wiki/graph}/<root-hash>/
```

The mirror contains only canonical wiki pages plus generated `.loci/graph`
profile and contribution files. The source wiki receives no `.loci` files. A
manifest digest and an exclusive lock make refresh deterministic and prevent a
partially generated mirror from being treated as current.

### 3. Reuse llm-wiki's canonical graph semantics

The adapter calls `collect_typed_edges()` and emits the exact Stage 4 profile:

- namespace: `llm-wiki`;
- edge types: directed `body_link` and `mentioned_in`;
- resolution: `declared`;
- evidence: exact authored file, line, and SHA-256 content hash.

It indexes the mirror once to discover Loci's canonical Markdown page-root IDs,
writes bounded contribution shards, then indexes again so Loci validates the
contribution before retrieval. Missing endpoints or evidence lines fail loudly.

### 4. Treat loci output as untrusted boundary data

The provider validates every returned path, node, edge, evidence locator,
namespace, type, resolution, and budgeted collection before creating compiler
candidates. Each evidence span is checked against the original wiki snapshot,
not trusted merely because the cache returned it.

Selected paths become ordinary atomic `CandidateEvidence` records. Explicitly
seeded paths and inferred paths that cross Loci's distinct question-anchor
clusters receive role `bridge`. Inferred paths confined to one subject cluster
receive role `support`: they remain inspectable but cannot make the compiler
claim a different relationship is sufficient. Rejected paths become bounded
graph diagnostics with the original stable loci reason. Graph retrieval score
is retained as a selection signal and rank only; it does not add authority.

### 5. Graph paths are atomic evidence

`CandidateEvidence` gains an internal `atomic` flag. If a complete path cannot
fit the remaining compiler byte budget, selection emits a normal `byte_limit`
omission instead of truncating the path into misleading partial evidence. The
flag is not added to the public compiled response.

## Exact Runtime APIs

### Configuration

```python
GRAPH_BACKENDS = {"loci", "legacy"}

@dataclass(frozen=True)
class CompilerConfig:
    providers: tuple[str, ...] = DEFAULT_PROVIDERS
    graph_backend: str = "loci"
    target_bytes: int = 48_000
    max_bytes: int = 192_000
    target_items: int = 24
    max_items: int = 96
```

### Shared loci MCP transport

```python
class LociMcpClient:
    def __init__(
        self,
        *,
        command: str | None = None,
        args: tuple[str, ...] = (),
        timeout_seconds: float = 15.0,
    ): ...

    def run(self, operation: Callable[[ClientSession], Awaitable[T]]) -> T: ...
```

Both the indexed-section provider and graph provider use this transport. The
production command remains `loci-mcp`, overridable only through
`LLM_WIKI_LOCI_MCP_COMMAND` or explicit test construction.

### Domain adapter

```python
@dataclass(frozen=True)
class PreparedGraphMirror:
    root: Path
    input_digest: str
    page_roots: Mapping[str, str] | None

@contextmanager
def open_graph_mirror(
    context: ProviderContext,
    *,
    cache_dir: str | Path | None = None,
) -> Iterator[PreparedGraphMirrorSession]: ...
```

The yielded session exposes `write_contributions(page_roots)` and
`commit(page_roots)`. The manifest is committed only after the caller confirms
the second loci index succeeded.

### Provider

```python
class LociGraphGateway(Protocol):
    def retrieve(self, context: ProviderContext) -> Mapping[str, Any]: ...

class LociGraphMcpGateway:
    def __init__(
        self,
        *,
        client: LociMcpClient | None = None,
        cache_dir: str | Path | None = None,
    ): ...

    def retrieve(self, context: ProviderContext) -> Mapping[str, Any]: ...

class LociGraphProvider:
    name = "graph"

    def __init__(self, *, gateway: LociGraphGateway | None = None): ...

    def collect(self, context: ProviderContext) -> ProviderResult: ...
```

The response adapter also validates inferred anchors and classifies each path
internally as `claim_bridge` or `ancillary_path`. This classification is a
candidate-role decision only. The compiler still computes coverage and final
sufficiency after all providers have returned.

For inferred selection, the primary subject cluster contains the top Loci
anchor plus later anchors sharing at least one of its matched question terms.
Anchors with no matched term in common form the distinct cluster. A path is a
`claim_bridge` only when its nodes include an anchor from both clusters. If the
question yields no distinct cluster, validated paths retain bridge behavior.
Explicit seeds bypass this inference because the caller supplied the endpoints.

The MCP gateway calls `loci_index`, `loci_outline`, and
`loci_graph_retrieve`. Retrieval is filtered to namespace `llm-wiki`, edge
types `body_link` and `mentioned_in`, resolution `declared`, direction `either`,
and hard limits derived from the caller's compiler budget but never wider than
the approved Stage 4 defaults.

## Files

### llm-wiki implementation

- `src/llm_wiki_core/config.py`
  - add and validate `compiler.graph_backend`;
- `src/llm_wiki_core/compiler.py`
  - choose the loci or legacy graph implementation explicitly;
- `src/llm_wiki_core/providers/base.py`
  - add the internal atomic-candidate flag;
- `src/llm_wiki_core/providers/loci_transport.py`
  - centralize the existing stdio client lifecycle and error mapping;
- `src/llm_wiki_core/providers/loci.py`
  - reuse the shared transport without changing section retrieval semantics;
- `src/llm_wiki_core/graph_adapter.py`
  - build and refresh the read-only graph mirror, profile, evidence, and shards;
- `src/llm_wiki_core/providers/loci_graph.py`
  - call loci graph retrieval and validate/adapt its response;
- `src/llm_wiki_core/providers/__init__.py`
  - export the new provider;
- `src/llm_wiki_core/selection.py`
  - enforce atomic graph candidates under the final budget;
- `src/llm_wiki_core/doctor.py`
  - report configured graph backend and loci readiness;
- `src/llm_wiki_core/migration.py`
  - render `graph_backend = "loci"` in fresh/migrated config;
- `docs/context-compiler.md`, `docs/loci-provider.md`, `README.md`, `SKILL.md`
  - document provider ownership, degradation, cache, and rollback.

### loci documentation

- `docs/plans/2026-07-13-extensible-graph-retrieval-stage-4.md`
  - record the Stage 5 review result after all gates pass;
- no loci production code is planned for Stage 5.

## Tests

### Focused unit and integration tests

- `tests/test_core_config.py`
  - default `loci`, explicit `legacy`, unknown backend rejection;
- `tests/test_graph_provider.py`
  - retain the legacy provider's frozen behavior under rollback config;
- `tests/test_loci_graph_adapter.py`
  - profile shape, page-root mapping, exact evidence, bounded shards, cache
    refresh, stale page removal, and no source-wiki mutation;
- `tests/test_loci_graph_provider.py`
  - selected path adaptation, rejected-path diagnostics, state ownership,
    invalid payload rejection, inferred claim-bridge versus ancillary-path
    classification, no silent fallback, atomic byte omission, and a fresh-process
    fake-MCP integration covering index/outline/retrieve;
- existing `tests/test_loci_provider.py`
  - section retrieval and stdio degradation remain unchanged;
- existing compiler/MCP/CLI golden and parity tests
  - public response and transport compatibility remain bounded.

### Frozen benchmark

Replay all ten fixtures before and after the provider switch. Preserve:

- checksum and gold isolation;
- endpoint recall;
- selected/rejected path outcomes;
- forbidden-shortcut rejection;
- exact evidence completeness;
- selected evidence bytes, complete response bytes/tokens, tool calls, and
  latency;
- deterministic digest with timing excluded.

The benchmark must show that the compiler consumes Loci's selected paths and
retains rejection evidence without claiming graph-level answerability.
The runner records deterministic page-content digests for both corpora and
fails if either corpus changes during a run, preventing mixed-snapshot traces.

## Implementation Order

1. Freeze current checksum and focused-test baseline.
2. Add failing config, adapter, provider, rollback, and atomic-budget tests.
3. Extract the shared loci transport and re-run existing loci tests.
4. Implement the mirror/profile/contribution adapter and prove no wiki writes.
5. Implement path validation/adaptation and focused provider tests.
6. Switch the compiler default and prove explicit legacy rollback.
7. Replay the frozen benchmark and inspect every selected/rejected graph trace.
8. Run full llm-wiki and loci suites, build/compile checks, and Loci verification.
9. Update docs and mark this plan complete only if the review gate passes.

## Review Gate

Stage 5 is ready for owner review only when all of these are observable:

- the frozen checksum remains unchanged;
- source wikis receive no generated profile, contribution, cache, or content
  writes during compilation;
- the default graph backend calls `loci_graph_retrieve` with approved filters
  and bounded budgets;
- `graph_backend = "legacy"` reproduces the old provider behavior;
- loci failure is explicit and never invokes the legacy provider silently;
- every selected compiler bridge contains a complete validated Loci path and
  exact authored evidence;
- rejected semantic bridges/hub shortcuts remain inspectable;
- no selected path is partially truncated to fit the final compiler budget;
- Loci scores do not create authority, state, answerability, or sufficiency;
- llm-wiki alone makes the final coverage, stop, and budget decisions;
- focused tests, both complete suites, compiler CLI/MCP parity, Python
  compilation, package build, Loci indexing/verification, and `git diff
  --check` pass;
- before/after benchmark traces and deterministic digests are recorded here.

Do not remove the legacy graph provider in this stage. Removal requires a later
owner decision after real-wiki rollout evidence and a rollback window.

## Verification Evidence

Technical review completed on 2026-07-14.

### Frozen contract and benchmark

- frozen fixture checksum remained
  `c52def1bdf592ad735149d199910f74183598eccd9ccf8064335fa0cd0e84e27`;
- historical pre-Stage-5 timing-excluded digest:
  `f9cf6b39b81c111cb166161b6f2e6ed7046d07304769a5b036f40abbbf9ea893`;
- final Stage-5 timing-excluded digest:
  `a8fe96152358f2d4cb3a5e163a5c9402f9581bc776148523defea85ce047d2e2`;
- an immediate unchanged-input repeat produced the same final digest;
- final `ai_graph_ideas` corpus digest:
  `f972c146d0f112ad84e72eb0b62f1e1da99ece40b7a2334f4bb52c645eeb25fc`;
- final Brain corpus digest:
  `f0af6db7658cbc751f141b97e9cdf89470cf92ec259677e13586db87ea8dd6d1`.

The historical and final digests cover the same frozen fixture contract, but
the live corpora received legitimate edits between stages. They are therefore
not byte-for-byte corpus snapshots. Stage 5 now records corpus digests and
rejects mid-run corpus drift; one review run correctly failed while Brain was
being edited concurrently, and only a stable repeat was accepted as evidence.

| Current compiler metric | Pre-Stage-5 audit | Stage 5 |
| --- | ---: | ---: |
| Mean endpoint recall | 0.056 | 0.944 |
| Required path complete | 0.0 | 1.0 |
| Bridge evidence complete | 0.0 | 1.0 |
| Unsupported shortcut rate | 0.0 | 0.0 |
| Refusal ready | 0.0 | 1.0 |
| Exact required-literal recall | 0.0 | 1.0 |
| Mean complete-response estimated tokens | 25,611 | 15,797.3 |
| Mean MCP tool calls | 2.0 | 3.4 |
| Mean latency | about 1.9 s | 1,992.914 ms |

All four positive relationship fixtures were sufficient with complete expected
paths, bridge evidence, and 1.0 endpoint recall. Both false-hub fixtures and
both cannot-answer fixtures stopped insufficient with `bridge` uncovered and
the semantic `candidate_exhausted` reason. Rejected ancillary graph paths
remained in diagnostics as normal negative evidence; they did not misreport the
provider as degraded. The final mean generic-hub path rate was 0.025 and mean
selected evidence size was 26,314.5 bytes.

### Verification commands

- focused provider, adapter, selection, query-shape, benchmark, CLI, and MCP
  tests passed;
- complete llm-wiki suite: `322 passed, 2 skipped, 14 subtests passed`;
- complete loci suite: `381 passed`;
- explicit compiler CLI/MCP parity slice: `4 passed`;
- Python compilation passed for `src`, `scripts`, and `tests`;
- `uv build --offline` produced both the source distribution and wheel;
- `python -m build` was unavailable because this venv does not install the
  optional `build` frontend; no dependency was added merely for review;
- Loci incremental index and sequential verification passed with no drift;
- `git diff --check` passed.

### Review findings resolved

- ancillary accepted paths no longer satisfy a different relationship claim;
- malformed or anchorless inferred results fail at the provider boundary;
- cached page-root identities must belong to their declared page;
- altered or missing contribution shards invalidate and rebuild the external
  mirror instead of being trusted through a stale manifest;
- exact edge text is validated and retained once in evidence content, while the
  public locator keeps only file, line range, and hash to avoid duplication;
- the benchmark preserves atomic Loci path boundaries and refuses corpus drift;
- normal rejected-path diagnostics no longer turn an exhausted relationship
  search into the misleading `provider_degraded` stop reason.

The technical review verdict is **ready for owner review**. The legacy graph
provider remains available only through explicit `graph_backend = "legacy"`.
No removal or automatic fallback is authorized by this stage.
