# Spec: Context Compiler v2 and Versioned Wiki Runtime

Status: **Implemented 2026-07-12.** See the [implementation plan](../plans/2026-07-12-context-compiler-v2.md) and [release evidence](../../releases/2026-07-12-context-compiler-v2.md).

Related decisions: [ADR-001](../../decisions/ADR-001-agent-graph-layer-adapter.md), [ADR-002](../../decisions/ADR-002-agent-scoped-mcp-context-server.md), [proposed ADR-003](../../decisions/ADR-003-canonical-runtime-and-versioned-wiki-contract.md), and [proposed ADR-004](../../decisions/ADR-004-question-shaped-state-aware-context-compiler.md).

## Objective

Turn `llm-wiki` from a collection of individually copied traversal scripts into a production-grade, shared context system that can answer a question from any registered wiki with bounded cost, explicit provenance, and honest knowledge state.

The capability is a **Context Compiler**: given a wiki, a natural-language question, optional seed pages, and a budget, it discovers candidate evidence, distinguishes current knowledge from historical or unresolved material, selects the smallest sufficient set, and returns a structured context artifact that explains what it included, omitted, and why it stopped.

The production outcome is not a one-off improvement to one wiki. Shared behavior is authored once in canonical `llm-wiki`, exposed through its read-only MCP server and CLI, and verified against at least:

- a newly scaffolded fixture wiki;
- `/Users/brummerv/phluxxed/ai_graph_ideas` as the high-volume evidence/incubator wiki; and
- Brain as the curated durable-knowledge wiki.

The wiki content remains plain Markdown. Subagents, embeddings, vector stores, and external retrieval services are optional future providers, not prerequisites or the definition of the capability.

## Problem Statement

The current system has four connected production gaps.

1. **Copied business logic drifts.** The MCP runtime dynamically imports each registered wiki's local `scripts/query.py` and `scripts/wiki_graph.py`. A fix in canonical `llm-wiki` therefore does not improve an existing wiki until its copied scripts are manually synchronized.
2. **Context is seed-shaped, not question-shaped.** `wiki_context_pack` expands to depth two from one page and takes the first twelve related pages. It cannot express the user's question, retrieval intent, or desired knowledge state.
3. **The token budget is descriptive rather than enforced.** The current pack reports `tokens * 4` as an approximate character budget, while page and source excerpts are assembled with independent fixed limits. It does not expose cumulative usage, omissions, or a stop reason.
4. **Evidence state is implicit.** Current, historical, superseded, contradicted, weak, inferred, scoped, and stale material can all be returned without a machine-readable distinction. Agents must reconstruct authority and currentness from prose.

The recent generated-manifest traversal milestone in `ai_graph_ideas` supports route-discovery value, but did not establish production readiness: endpoint recall improved while the authored bridge span was still missed, cumulative model work increased, and manual citation review reduced the apparent support score. This spec treats that result as design evidence for governed candidate discovery and explicit attribution, not as a production algorithm to copy wholesale.

## Approved Decisions (2026-07-12)

| # | Decision | Proposed choice |
|---|---|---|
| C1 | Shared behavior ownership | **Canonical package.** Parsing, graph traversal, compilation, budgeting, state interpretation, and response schemas live in an importable `llm-wiki` core. Registered wikis do not own forked copies of this business logic. |
| C2 | Wiki artifact | **Plain Markdown remains canonical.** The runtime may read configuration and generated metadata, but Markdown pages and sources remain inspectable and usable without a database or service. |
| C3 | Per-wiki variation | **Versioned configuration, not code patches.** Wiki-specific exclusions, page roles, state vocabulary mappings, provider policy, and stewardship policy live in `.llm-wiki.toml`. |
| C4 | Local scripts | **Compatibility adapters only.** Existing `scripts/query.py` and `scripts/wiki_graph.py` commands remain callable during migration but delegate to the canonical runtime. They are not independent implementations. |
| C5 | Primary query interface | Add **`wiki_compile_context`** and a matching CLI command. It requires a question and accepts optional seed pages and bounded policy. |
| C6 | Existing interface | Keep `wiki_context_pack(alias, page, tokens)` as a compatibility adapter with its current response contract for one deprecation window. Do not silently change its shape. |
| C7 | Knowledge state | Add an additive, optional state contract. Missing state is **`unspecified`**, not fabricated as `current`. |
| C8 | Budget semantics | **Progressive, not one-shot.** Search broadly, start with a target context size, and automatically expand while required evidence roles remain uncovered. Enforce only the caller/system maximum as the hard ceiling. Report estimated tokens as advisory unless a configured tokenizer is available. Always return usage, omissions, continuation guidance, and a stop reason. |
| C9 | Mutation boundary | Compilation, doctor, and migration inspection are read-only. Wiki content mutation remains governed by `wiki-agent.md`; Brain promotion remains a curated Brain Steward action. |
| C10 | Initial retrieval providers | Ship deterministic local providers only: seed/exact reference, frontmatter/text search, graph links/backlinks, source references, and optional `loci` navigation when available. Provider failures degrade explicitly; they do not erase successful evidence. |
| C11 | Upgrade mechanism | Ship an explicit `inspect -> dry-run -> apply -> verify -> rollback` migration workflow with receipts. Never auto-edit an existing wiki merely because MCP accessed it. |
| C12 | Definition of done | A shared capability is incomplete until canonical tests, legacy compatibility tests, migration tests, and cross-wiki verification for the fixture, `ai_graph_ideas`, and Brain pass. |

## System Boundaries

```text
Question + seed(s) + budget + state view
                    |
                    v
          Canonical Context Compiler
        /       |        |          \
   exact     graph     source     optional loci
        \       |        |          /
         candidate evidence records
                    |
          state + authority analysis
                    |
        deterministic selection/budgeting
                    |
                    v
   compiled context + provenance + omissions + stop state
```

The compiler owns retrieval orchestration and the output contract. Providers only discover or fetch candidate records; they do not decide final authority, truncate the final response independently, or mutate wiki content.

The initial provider set remains local and deterministic. A future semantic index, remote corpus, or bounded worker can implement the provider interface without changing the compiler request/response contract. Adding such a provider requires its own dependency and trust-boundary review.

## Canonical Runtime Contract

### Ownership

The installed `llm-wiki` package becomes the only authored implementation of:

- Markdown/frontmatter parsing used by agent-facing reads;
- path and source-boundary validation;
- graph construction and traversal primitives;
- query classification;
- provider orchestration;
- knowledge-state normalization;
- evidence ranking and selection;
- cumulative budgeting;
- compiled response and error schemas; and
- doctor/migration compatibility checks.

Render and lint may continue to have presentation- or policy-specific logic, but shared parsing and link resolution must use the same core primitives so that query, render, and lint cannot disagree about page identity.

### Local CLI compatibility

Existing wiki-local commands remain available through thin adapters during migration:

```bash
.venv/bin/python3 scripts/query.py --context-pack <page> --tokens 12000 --json
```

Adapters must declare the compatible runtime contract, import canonical behavior, and fail loudly with install/upgrade guidance when the required runtime is unavailable. They must not carry a fallback copy of the compiler, because two executable implementations would recreate the drift this change is intended to remove.

Plain-Markdown portability means the content can still be read, edited, versioned, and migrated without the runtime. It does not promise that agent-specific CLI features execute without their declared Python dependency.

## Wiki Configuration Contract

Each migrated or newly scaffolded wiki receives `.llm-wiki.toml` at its root. The file is policy/configuration, not generated knowledge.

Illustrative v1 shape:

```toml
schema_version = "1"
runtime_contract = "2"
profile = "default"

[content]
exclude_directories = [".git", ".venv", "node_modules"]
source_directory = "sources"

[compiler]
providers = ["seed", "frontmatter", "text", "graph", "source"]
default_max_bytes = 48000
default_max_items = 24

[state]
field = "knowledge_state"
default = "unspecified"

[stewardship]
mode = "manual"
```

Requirements:

- Unknown keys are preserved by migration tooling and reported by doctor.
- Unknown schema or runtime major versions fail closed with a structured compatibility error.
- Missing config is treated as a legacy wiki and remains readable through the compatibility path during the migration window.
- Machine- or agent-specific secrets, absolute paths, and registry identity never belong in this file.
- Current local code differences, such as excluding `.agents` from `ai_graph_ideas`, must become configuration rather than canonical-script patches.

## Knowledge State Contract

State is additive frontmatter that helps an agent interpret evidence. It does not replace `status`, which continues to describe the page's workflow/publication lifecycle.

### Normalized fields

```yaml
knowledge_state: current       # current|historical|superseded|contradicted|weak|inferred|unspecified
scope: llm-wiki-runtime        # optional free-form scope boundary
supersedes: []                 # optional page references
superseded_by: []              # optional page references
valid_from: 2026-07-12         # optional ISO date/datetime
valid_until: null              # optional ISO date/datetime
```

Rules:

- The fields are optional for backward compatibility.
- Missing `knowledge_state` normalizes to `unspecified`.
- `current` is an explicit authorial assertion, not something inferred from recency alone.
- `inferred` and `weak` must never be upgraded to authoritative merely because several pages repeat them.
- `contradicted` must identify the conflicting record in provenance or diagnostics when known.
- `superseded` must not be selected for a `current` view unless it is necessary to explain lineage or no current replacement exists; either case is reported.
- Validity dates constrain state but do not silently rewrite the Markdown field.
- A compiler may derive flags such as `stale` or `source_missing`; derived flags are reported separately from authored state.

### State views

The request selects one of:

- `current`: prefer current, in-scope evidence; include historical/conflicting evidence only when needed to avoid a misleading answer.
- `historical`: allow historical and superseded evidence, preserving lineage.
- `transition`: emphasize changes, contradictions, and supersession chains.
- `all`: do not filter by state, but still label every record.

The default is `current`.

## Compiler Request Contract

The public MCP tool is additive:

```python
wiki_compile_context(
    alias: str,
    question: str,
    seeds: list[str] | None = None,
    state_view: str = "current",
    target_bytes: int = 48_000,
    max_bytes: int = 192_000,
    target_items: int = 24,
    max_items: int = 96,
    max_estimated_tokens: int | None = None,
) -> CallToolResult
```

The equivalent internal request is versioned:

```json
{
  "contract_version": "1",
  "alias": "anvil-brain-codex",
  "question": "What currently owns wiki traversal and how do upgrades propagate?",
  "seeds": ["systems/llm-wiki.md"],
  "state_view": "current",
  "budget": {
    "target_bytes": 48000,
    "max_bytes": 192000,
    "target_items": 24,
    "max_items": 96,
    "max_estimated_tokens": 48000
  }
}
```

Validation requirements:

- `question` must contain non-whitespace text and is size-bounded.
- `seeds` resolve through the existing safe page resolver; unresolved seeds are structured input errors with suggestions.
- target and maximum limits are clamped to documented server maxima and the effective limits are returned;
- target limits cannot exceed their corresponding maximums;
- an unsupported contract version or state view fails before provider execution.
- request validation uses the existing `{code, message, details}` error envelope.

## Query Shaping

The compiler deterministically classifies the question into one or more query shapes. V1 shapes are:

- `lookup`: locate a named fact, page, owner, command, or definition;
- `relationship`: explain how two or more named things connect;
- `state`: establish what is currently true and identify stale or conflicting material;
- `history`: reconstruct sequence, supersession, or prior decisions;
- `synthesis`: combine evidence across several pages or sources; and
- `maintenance`: identify gaps, drift, unresolved risks, or stewardship work.

Classification may be rule-based initially. It is observable in the response and covered by fixtures. A model-assisted classifier may be added later behind the same interface, but cannot become a hidden network dependency in the default path.

Query shape controls provider order and evidence roles; it does not suppress provenance or override the request budget.

## Provider Interface

Every provider returns bounded candidate records in a shared shape:

```python
class CandidateEvidence:
    id: str
    provider: str
    route: str
    page: str | None
    source: str | None
    locator: dict
    content: str
    roles: list[str]
    selection_signals: list[str]
    authored_state: str
    derived_flags: list[str]
```

Provider requirements:

- deterministic for the same on-disk state and request;
- read-only;
- individually bounded;
- exact path/source containment checks;
- stable evidence identifiers within one compile;
- no final-response truncation decisions;
- failures returned as provider diagnostics, not swallowed exceptions; and
- no authority claim based solely on retrieval score.

Initial providers:

| Provider | Purpose | Required |
|---|---|---|
| `seed` | exact seed page resolution and content | yes |
| `frontmatter` | exact title/tag/type/status/state/scope matches | yes |
| `text` | bounded lexical matches with page/section locators | yes |
| `graph` | links, backlinks, and bounded connecting paths | yes |
| `source` | source references and bounded source excerpts | yes |
| `loci` | indexed section/symbol navigation for Markdown and code references | optional; explicit diagnostic if unavailable/stale |

## Selection and Budgeting

The compiler performs selection after candidate discovery.

Selection order is deterministic and uses lexicographic tie-breakers. The initial policy must consider:

1. evidence role required by the query shape;
2. state-view compatibility;
3. explicit source/decision authority;
4. seed and exact-name match;
5. bridge value between already selected evidence;
6. duplication with selected evidence; and
7. byte cost.

No single opaque score may be presented as authority. If an implementation uses scores internally, the response exposes the contributing reasons separately.

Progressive selection:

- candidate discovery is not restricted to the initial output target;
- `target_bytes` and `target_items` describe the preferred first envelope, not recall ceilings;
- if required evidence roles remain uncovered, the compiler expands selection beyond the target automatically;
- expansion stops only when coverage is sufficient, candidates are exhausted, or a caller/system maximum is reached; and
- when evidence still cannot fit, the response identifies uncovered roles and provides continuation guidance rather than implying completion.

Hard limits:

- `max_bytes` is the caller/system safety ceiling and applies to the serialized evidence content plus required per-item metadata, using UTF-8 bytes.
- `max_items` applies to selected evidence records.
- required response envelope metadata may exceed `max_bytes`; its actual size is reported separately so the caller can account for total transport cost.
- an individual record that cannot fit is omitted or excerpted at a valid text boundary; the action is recorded.
- the compiler never return an unmarked partial source quotation.

Advisory limit:

- `max_estimated_tokens` uses a documented deterministic estimate unless a supported tokenizer is configured.
- because an estimate is not exact across models, it cannot be the only hard safety control.

The compiler stops with exactly one primary reason:

- `sufficient`: required query roles are covered and additional candidates have no material marginal value;
- `byte_budget_exhausted`;
- `item_budget_exhausted`;
- `candidate_exhausted`;
- `no_evidence`;
- `provider_degraded`; or
- `invalidated`: on-disk state changed during compilation and the result cannot be made internally consistent.

## Compiler Response Contract

The structured response has a stable versioned envelope:

```json
{
  "kind": "compiled_context",
  "contract_version": "1",
  "wiki": {
    "alias": "anvil-brain-codex",
    "schema_version": "1",
    "runtime_contract": "2"
  },
  "query": {
    "question": "...",
    "shapes": ["relationship", "state"],
    "state_view": "current",
    "resolved_seeds": ["systems/llm-wiki.md"]
  },
  "evidence": [],
  "omissions": [],
  "coverage": {
    "required_roles": ["definition", "ownership", "propagation"],
    "covered_roles": [],
    "uncovered_roles": []
  },
  "budget": {
    "limits": {},
    "target_exceeded_for_coverage": false,
    "evidence_bytes": 0,
    "envelope_bytes": 0,
    "items": 0,
    "estimated_tokens": 0
  },
  "stop": {
    "reason": "candidate_exhausted",
    "sufficient": false,
    "detail": "..."
  },
  "continuation": null,
  "diagnostics": []
}
```

Each evidence record includes:

- exact page/source path and section or line locator when available;
- bounded content or excerpt;
- provider and route;
- evidence roles;
- authored state and derived flags;
- authority signals (for example `source_excerpt`, `accepted_adr`, `explicit_current`);
- selection reasons;
- byte cost; and
- any truncation marker.

Each omission includes the candidate identity, reason, and estimated cost when known. Omission reasons include state mismatch, duplicate, lower marginal value, byte limit, item limit, unsafe path, unavailable content, and provider failure.

`stop.sufficient` is a deterministic coverage assertion, not a claim that the final answer is correct. The compiler supplies evidence; the calling agent remains responsible for reasoning and citation.

## Legacy Compatibility and Deprecation

`wiki_context_pack` and the current CLI `--context-pack` remain supported for one documented deprecation window.

Compatibility requirements:

- keep current input arguments and `kind: context_pack` response shape;
- keep safe page resolution and current maximum-token clamping;
- route execution through canonical core rather than wiki-local business logic;
- add deprecation metadata only where it does not break existing consumers;
- maintain golden compatibility tests for field presence, ordering where observable, and bounded excerpts; and
- publish removal criteria and telemetry-free migration guidance before changing status from supported to deprecated.

The new compiler is additive. It does not pretend that a page-seeded context pack and a question-shaped compiled context are semantically identical.

## Migration Contract

Migration is explicit and receipt-backed.

### Phases

1. `inspect`: report wiki schema, runtime contract, local script digests, unsupported customization, and blockers without writing.
2. `dry-run`: produce the exact proposed file operations and config translation.
3. `apply`: after explicit approval, write `.llm-wiki.toml`, replace eligible local business-logic scripts with compatibility adapters, and save a migration receipt plus backups required for rollback.
4. `verify`: run doctor, core compatibility checks, wiki lint/render, and compiler smoke queries.
5. `rollback`: restore the pre-migration files from the receipt and verify the restored state.

### Safety rules

- Migration never edits `sources/`.
- Migration does not rewrite content pages merely to add knowledge state; state adoption is a separate governed content change.
- Unsupported local script changes block automatic replacement and are shown as a semantic diff category, not silently discarded.
- Apply is idempotent for the same target runtime contract.
- Partial apply fails loudly and leaves a recoverable receipt.
- Rollback scope and expiry are stated before apply.
- A wiki remains registered throughout migration unless its path becomes invalid.

### Doctor output

`wiki_doctor` becomes the compatibility truth surface and reports:

- config/schema/runtime versions;
- canonical runtime version;
- legacy adapter status;
- local script drift;
- missing/unknown configuration;
- provider readiness, including `loci` availability/freshness where configured;
- last migration receipt and verification state; and
- one of `compatible`, `legacy_supported`, `migration_available`, `blocked`, or `incompatible`.

## Brain and Incubator Propagation

The same runtime serves every registered compatible wiki immediately after the canonical package is upgraded. Content and policy changes remain separate.

### `ai_graph_ideas`

- receives `.llm-wiki.toml` with its local exclusions and evidence/incubator profile;
- is a cross-wiki acceptance target for large graph, source-heavy, and bridge-retrieval cases;
- keeps raw and unverified ideas quarantined in its own knowledge lifecycle; and
- does not automatically promote experiment outcomes into Brain.

### Brain

- receives `.llm-wiki.toml` with its durable-knowledge and stewardship policy;
- uses `wiki_compile_context` through the same canonical runtime;
- may receive **maintenance candidate packets** from compiler diagnostics (stale current pages, contradictions, supersession gaps, missing source paths);
- remains read-only over MCP; and
- applies accepted maintenance through the Brain Steward following Brain's `wiki-agent.md`, with normal lint/render verification.

A maintenance packet is evidence for a possible edit, not an edit instruction and not a truth claim.

## Failure Semantics

Public errors retain the existing stable envelope:

```json
{
  "error": {
    "code": "RUNTIME_CONTRACT_INCOMPATIBLE",
    "message": "Wiki requires a newer llm-wiki runtime contract",
    "details": {}
  }
}
```

Expected v1 codes include:

- `INVALID_INPUT`
- `PAGE_NOT_FOUND`
- `WIKI_NOT_FOUND`
- `WIKI_CONFIG_INVALID`
- `SCHEMA_VERSION_UNSUPPORTED`
- `RUNTIME_CONTRACT_INCOMPATIBLE`
- `PROVIDER_UNAVAILABLE`
- `PROVIDER_FAILED`
- `WIKI_CHANGED_DURING_COMPILE`
- existing path/source containment errors

A non-critical provider failure is normally represented in `diagnostics` with a `provider_degraded` stop reason when the compiler can still return internally consistent evidence. Validation, containment, incompatible-version, and consistency failures remain tool errors.

## Observability and Privacy

The compiler emits structured per-run diagnostics locally:

- provider duration and candidate count;
- selected/omitted counts;
- byte and estimated-token usage;
- stop reason and coverage state;
- runtime/schema versions; and
- provider errors without source content.

Default operation does not persist questions or evidence content outside the tool response. Any future persisted trace or telemetry requires an explicit retention, privacy, and redaction decision.

## Tech Stack

- Python `>=3.10`, matching `pyproject.toml`.
- Existing `pyyaml`, `markdown`, and MCP dependencies.
- Standard-library TOML read support on Python 3.11+; the Plan must choose and test the Python 3.10 compatibility path without silently raising the minimum supported version.
- Existing `WikiMcpError` structured error boundary.
- Optional `loci` integration through a provider boundary; no hard dependency in the base install.

No vector database, hosted service, model API, or new agent framework is required for v1.

## Expected Project Surfaces

This is an architectural inventory, not an implementation task plan.

```text
src/llm_wiki_core/              canonical parsing, graph, config, state, providers, compiler, migration
src/llm_wiki_mcp/               MCP transport adapters and registry boundary
scripts/                        canonical CLI entry/adapters and existing lint/render/eval surfaces
tests/                          unit, contract, migration, compatibility, and integration tests
docs/decisions/                 accepted architecture records
docs/superpowers/plans/         Phase 2 implementation plan after spec approval
```

The Plan phase must validate the final module split against the existing package and avoid naming a second public package unless it provides a real compatibility boundary.

## Testing Strategy

Tests must be deterministic and network-free by default.

### Unit tests

- config parsing, unknown-key preservation, and version compatibility;
- state normalization and state-view policy;
- query-shape classification fixtures;
- provider boundaries, containment, failure diagnostics, and deterministic order;
- duplicate detection and selection tie-breakers;
- exact UTF-8 byte accounting, item limits, excerpt boundaries, and envelope accounting;
- every stop reason and sufficiency state; and
- structured public errors.

### Contract tests

- golden `compiled_context` request/response fixtures;
- MCP tool schema and structured content;
- `WikiMcpError` envelope compatibility;
- legacy `wiki_context_pack` response compatibility; and
- local CLI adapter behavior when canonical runtime is present, absent, older, or newer.

### Migration tests

- inspect and dry-run make no writes;
- known canonical scripts migrate cleanly;
- local customizations translate to config where supported;
- unsupported modifications block rather than overwrite;
- apply is idempotent;
- injected partial failure rolls back or leaves a valid recovery receipt; and
- verify/rollback establish the advertised state.

### Cross-wiki acceptance

Run the same fixed questions and budgets against:

1. a small generated fixture;
2. `ai_graph_ideas`, including a relationship question that requires an authored bridge; and
3. Brain, including a current-state question with deliberately historical or stale distractors.

Acceptance checks assert contract, provenance, state labels, budgets, stop reasons, and required evidence spans. They do not rely solely on an LLM judge. Judge-backed answer quality may supplement but cannot replace deterministic retrieval assertions.

## Delivery and Rollout Constraints

- Design the whole production contract before implementation, then deliver it in reversible production-ready slices.
- Land canonical runtime and compatibility tests before migrating real wikis.
- Migrate `ai_graph_ideas` before Brain; it is the safer high-volume proving ground.
- Brain migration requires a clean dry-run, rollback proof, and read-only compiler smoke test before stewardship configuration is enabled.
- Preserve the old MCP tool until compatibility and adoption criteria are met.
- Do not claim success from generated-manifest or context-answer quality alone; verify citation spans and total retrieval cost.

## Non-Goals for v1

- automatic wiki content mutation over MCP;
- automatic promotion from `ai_graph_ideas` into Brain;
- autonomous Brain cleanup;
- subagent or temporary-worker orchestration;
- embeddings, a vector database, PageRank/PPR, or a daemon;
- remote retrieval or hosted telemetry;
- model-generated query classification in the default path;
- replacement of `wiki-agent.md`, lint, render, or eval governance; or
- a promise that compiled evidence is itself a correct final answer.

## Success Criteria

1. One canonical runtime implementation serves all compatible registered wikis; the MCP no longer executes independently authored wiki-local traversal logic.
2. `wiki_compile_context` accepts a real question, optional seeds, state view, byte budget, and item budget, and returns the versioned contract described here.
3. Every returned evidence record has provenance, state, selection reasons, and byte cost; every excluded high-ranking candidate has an omission reason.
4. Hard byte/item limits are proven by boundary tests and actual usage is reported.
5. The compiler returns an explicit stop reason and uncovered roles; it never implies completeness by omission.
6. Missing state remains `unspecified`; current/historical/superseded/contradicted material is handled according to the selected state view.
7. Existing `wiki_context_pack` consumers pass compatibility tests throughout the deprecation window.
8. A migration can inspect, dry-run, apply, verify, and roll back both a standard wiki and a locally customized wiki without losing content or unsupported customization.
9. The fixed acceptance suite passes on a fixture, `ai_graph_ideas`, and Brain with exact required evidence spans and bounded cost.
10. Brain receives shared compiler behavior through the canonical runtime while all Brain content mutation remains under Brain Steward governance.

## Approved Choices

The user approved these choices on 2026-07-12:

1. **Runtime dependency:** wiki-local query commands may require the installed canonical `llm-wiki` package; we will not preserve a second vendored fallback implementation.
2. **Budget truth:** broad discovery is followed by progressive context expansion. The initial size is a target, not a recall ceiling; only the caller/system maximum is hard. Token count is reported as an estimate unless a supported tokenizer is configured.
3. **Knowledge-state adoption:** the schema is available immediately, but existing page content is not bulk-labeled by migration; curators add or verify state through normal wiki governance.
4. **Legacy window:** `wiki_context_pack` stays supported while the new compiler is adopted, with exact removal timing decided from compatibility evidence in the Plan rather than precommitted here.

## Plan-Phase Checks (after approval)

1. Inventory every current local-script delta across canonical `llm-wiki`, `ai_graph_ideas`, and Brain and classify it as config, obsolete drift, or unsupported customization.
2. Verify the Python 3.10 TOML strategy and package installation path for generated wikis.
3. Freeze current `wiki_context_pack` golden fixtures before changing its execution path.
4. Define the exact CLI verbs and migration receipt schema without expanding the MCP write boundary.
5. Freeze cross-wiki acceptance questions and gold evidence spans before implementation to avoid grading against the implementation's output.
6. Measure current response byte accounting and select safe server maxima from observed wiki sizes rather than the illustrative defaults alone.
