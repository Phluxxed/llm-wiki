# MCP Structured Output Schemas Implementation Plan

**Date:** 2026-08-20
**Status:** Completed
**Primary repository:** `/Users/brummerv/llm-wiki`
**Baseline commit:** `991e39e457fc416a6b160bb6b941a7c5e7ae0026`
**SDK baseline:** `mcp>=2,<3`
**Implementation boundary:** declare and validate the existing structured result
contracts for all twenty llm-wiki MCP tools without changing their meanings,
payload keys, text markers, error semantics, registry behavior, or mutation
posture.

## Outcome

Every applicable tool returned by `llm-wiki-mcp` advertises a non-null MCP
`outputSchema`, validates its real `structuredContent` on the server, and is
visible to a fresh Codex host as a typed `CallToolResult`. The migration must
preserve the current structured error envelope and `isError` behavior as part
of each tool's declared contract.

The change is complete only when:

1. all twenty tools listed below advertise object-root `outputSchema` values;
2. a representative real success result is accepted by server-side output
   validation and an invalid result is rejected by that boundary;
3. expected and unexpected failures retain diagnostic text,
   `structuredContent.error`, and `isError=true` while satisfying the same
   tool schema;
4. existing payloads remain structurally and semantically unchanged;
5. all success and error models are strict Pydantic models, including their
   nested objects;
6. supported SDK 2.x handlers use
   `Annotated[CallToolResult, OutputModel]`; and
7. one fresh-host check proves Codex consumes a representative typed result
   after the installed server has been refreshed.

This is a contract-declaration migration. It authorizes no tool redesign,
registry or wiki mutation beyond temporary test fixtures, SDK major-version
change, independent review, or implementation work while executing this plan
document itself.

> **TL;DR:** Model the existing success and structured-error objects, prove the
> complete MCP path on `wiki_list`, then apply the proven annotation and tests
> to every remaining tool until `tools/list` contains no applicable tool with a
> missing `outputSchema`.

## Current Contract And Constraints

### Server surface

`src/llm_wiki_mcp/mcp_server.py:create_server` registers exactly twenty
synchronous handlers. Every handler currently returns an unannotated
`CallToolResult`, so SDK 2 advertises `output_schema=None` for all of them.
`_success` emits the bounded `SUCCESS_MARKER`, the runtime payload as
`structured_content`, and `is_error=False`.

`_handle_wiki_error` is part of the public behavior, not an implementation
detail that schemas may erase. A `WikiMcpError` becomes:

```json
{
  "error": {
    "code": "<domain code>",
    "message": "<diagnostic>",
    "details": {"<bounded domain fields>": "<values>"}
  }
}
```

with diagnostic text and `is_error=True`. Unexpected exceptions use the same
shape with `code="UNEXPECTED_ERROR"` and `details.type`. Each tool's output
schema must therefore accept exactly its success object or this structured
error object. Returning an error only as text, omitting `structured_content`,
or allowing the SDK to replace it with an unstructured exception result is a
regression.

### Schema representation

Add strict Pydantic output models rather than `TypedDict`. MCP Python SDK 2.0
currently materializes absent optional `TypedDict` fields as null in this path;
that would change llm-wiki's payloads. Use `BaseModel` classes with
`ConfigDict(extra="forbid", strict=True)` (and strict nested models), then
serialize only where needed with `exclude_none=True` and `exclude_unset=True`.
Do not use permissive `dict[str, Any]` as a substitute for modeling a known
nested contract; reserve explicitly typed JSON values only for genuinely open
domain maps such as error `details` or page frontmatter.

The MCP output schema must have an object at its root. Represent each tool's
success-and-error alternatives in an object-root Pydantic envelope whose
validator enforces exactly one branch. Confirm the generated JSON Schema has
`type: object`; do not expose a root `anyOf`/scalar schema that the MCP
specification rejects. Optional Pydantic fields used to express the two
branches are acceptable only if round-trip tests prove absent fields remain
absent rather than becoming null.

Keep `CallToolResult` as the wire result because llm-wiki intentionally
supplies marker/diagnostic content and `is_error`. On the pinned SDK 2.x API,
annotate each handler as:

```python
def wiki_list() -> Annotated[CallToolResult, WikiListOutput]:
    ...
```

Use the SDK's supported `Annotated[CallToolResult, OutputModel]` facility; do
not replace the handlers with bare model returns. The model validates
`structured_content` while `CallToolResult` preserves the full MCP result.

## Complete Tool Inventory

Create `src/llm_wiki_mcp/output_models.py` as the single schema vocabulary.
Derive fields from the producer named below and lock representative examples
with the existing fixtures/tests. Every row is in scope.

| Tool | Success contract source | Required output model |
| --- | --- | --- |
| `wiki_list` | `registry.list_wikis` | registry home plus typed wiki records |
| `wiki_register` | `registry.register_wiki` | registered wiki record plus warnings |
| `wiki_unregister` | `registry.unregister_wiki` | removed wiki record |
| `wiki_doctor` | `registry.doctor`, `doctor.inspect_runtime` | complete doctor/runtime diagnostics |
| `wiki_agent_manual` | `wiki_runtime.agent_manual` | manual, conventions, rules, and nested doctor result |
| `wiki_overview` | `LegacyRuntime.overview` | agent overview |
| `wiki_query` | `LegacyRuntime.query` | query filters/count/results |
| `wiki_links` | `LegacyRuntime.links` | outgoing link records |
| `wiki_backlinks` | `LegacyRuntime.backlinks` | incoming link records |
| `wiki_around` | `LegacyRuntime.around` | bounded graph-neighborhood result |
| `wiki_context_pack` | `LegacyRuntime.context_pack` and `tests/fixtures/context_pack_v1.json` | complete context-pack contract |
| `wiki_compile_context` | `CompileResult.to_dict` and `tests/fixtures/compiled_context_v1.json` plus temporal tests | contract-versioned compiled context, including temporal variants |
| `wiki_maintenance_candidates` | `build_maintenance_packet` | candidate packet and read-only mutation envelope |
| `wiki_build_maintenance_candidate` | `build_candidate_proposal` | canonical maintenance candidate proposal |
| `wiki_build_temporal_candidates` | `temporal_candidate_proposal` and temporal fixtures | temporal proposal, observation, packet, and stewardship/mutation envelopes |
| `wiki_build_maintenance` | unified-maintenance composer and `tests/fixtures/unified_maintenance/v1.json` | every supported intent branch of unified maintenance v1 |
| `wiki_reconcile_temporal_candidates` | `TemporalReconciliationResult.to_dict` and temporal reconciliation fixture | complete reconciliation result |
| `wiki_get_page` | `wiki_runtime.get_page`, `LegacyRuntime.page_record` | page metadata/frontmatter/content |
| `wiki_get_source_excerpt` | `wiki_runtime.get_source_excerpt` | source path and bounded content |
| `wiki_graph_health` | `LegacyRuntime.health` | graph health, hubs, orphans, components, and source gaps |

Before writing a model, inspect the named producer and its nested `to_dict`
implementations. Reuse nested models where contracts really coincide (wiki
record, error, mutation, stewardship, page/link, observation, candidate,
coverage/budget); keep separate top-level output classes so every decorator
names the exact tool contract. Model literal discriminators and contract
versions with `Literal`, required lists as lists, nullable values only where
the producer emits null, and bounded/open maps with explicit JSON-value types.

Completion criterion: the model inventory accounts for every key and every
supported success variant emitted by all twenty producers, plus the shared
structured error branch, without adding or renaming a wire field.

## Ordered Work Packages

### 1. Freeze representative payloads and SDK behavior

Inspect and record the resolved `mcp` and Pydantic versions from the test
environment. In a short focused SDK contract test, prove the installed SDK's
exact annotation behavior: `Annotated[CallToolResult, OutputModel]` produces a
tool `outputSchema` and validates `structured_content`. Keep the production
dependency constraint `mcp>=2,<3`; pin more narrowly only if live SDK evidence
shows the required annotation API varies incompatibly inside that declared
range, and explain the constraint in `pyproject.toml`.

Capture current real success and error payloads through the existing stdio
fixture before changing annotations. Assertions, not checked-in snapshots of
machine-specific paths or timestamps, should freeze the shape.

Completion criterion: the implementation knows the exact installed SDK API,
has a red focused assertion for missing `wiki_list.outputSchema`, and has
explicit pre-change assertions for the current `wiki_list` success and
`CONFIG_REQUIRED` error envelopes.

### 2. Build the `wiki_list` vertical slice

Modify:

- `src/llm_wiki_mcp/output_models.py` (new);
- `src/llm_wiki_mcp/mcp_server.py`; and
- `tests/test_mcp_server.py`.

Implement the shared strict `WikiError`, error envelope, JSON-value support,
wiki-record models, and object-root `WikiListOutput`. Update
`create_server.wiki_list` to return
`Annotated[CallToolResult, WikiListOutput]`; keep `_success` and
`_handle_wiki_error` behavior intact.

Extend the real stdio round trip to prove all four layers:

1. `client.list_tools()` returns a non-null object-root `output_schema` for
   `wiki_list`, containing the declared success fields and `error` branch;
2. a real registered-wiki success payload passes server-side model validation
   and returns unchanged, with the existing success marker;
3. the no-`LLM_WIKI_HOME` call still returns the exact structured
   `CONFIG_REQUIRED` payload, diagnostic text, and `is_error=True`, and that
   payload also validates against `WikiListOutput`; and
4. an intentionally invalid success payload injected at the narrow adapter
   seam is rejected by output validation, proving the test is not merely
   checking schema advertisement.

Also assert that absent alternate-branch fields are absent from
`structured_content`, never materialized as null. Keep the invalid-payload
case in-process if that is the smallest reliable way to exercise validation;
success and error acceptance must remain real stdio calls.

Run:

```bash
python -m unittest tests.test_mcp_server.McpServerTest -v
```

Completion criterion: `wiki_list` proves advertised schema, real success
validation, preserved structured error validation, invalid-output rejection,
and client-visible typed `CallToolResult` while its prior wire payload remains
unchanged.

### 3. Model and annotate registry, manual, and navigation tools

Extend `output_models.py` and annotations for:

- `wiki_register`, `wiki_unregister`, `wiki_doctor`;
- `wiki_agent_manual`, `wiki_overview`, `wiki_query`;
- `wiki_links`, `wiki_backlinks`, `wiki_around`;
- `wiki_get_page`, `wiki_get_source_excerpt`; and
- `wiki_graph_health`.

Use the current real fixture in `tests/test_mcp_server.py` to call every tool in
this package at least once and validate each returned `structured_content`
against its named model. Add focused fixture data only where needed to make a
non-empty nested collection observable. Preserve registration timestamps as
strings matching their current wire form; schema declaration must not turn
them into Python datetime serialization.

Targeted tests:

```bash
python -m unittest tests.test_mcp_server -v
```

Completion criterion: the eleven tools in this package advertise object-root
schemas, their real success payloads validate without normalization drift, and
the shared expected-error test passes for at least one navigation failure as
well as `wiki_list`.

### 4. Model and annotate context compilation tools

Add exact models and annotations for `wiki_context_pack` and
`wiki_compile_context`. Treat contract versions and temporal variants as
explicit schema branches while keeping one object-root top-level model per
tool. Derive nested fields from the core contract dataclasses and `to_dict`
methods; use the checked-in compiled/context-pack fixtures as completeness
evidence rather than inventing a parallel contract.

Targeted tests:

```bash
python -m unittest tests.test_mcp_compiler tests.test_mcp_temporal -v
```

These tests must assert non-null advertised schemas, validation of the real v1
compiled payload, validation of the supported temporal mapping, and unchanged
structured `CONTRACT_VERSION_UNSUPPORTED`, `BUDGET_TOO_SMALL`, and
`INVALID_INPUT` errors.

Completion criterion: both context tools validate every currently supported
result variant and error branch, with fixture payloads and stdio behavior
unchanged.

### 5. Model and annotate maintenance and temporal tools

Add exact models and annotations for:

- `wiki_maintenance_candidates`;
- `wiki_build_maintenance_candidate`;
- `wiki_build_temporal_candidates`;
- `wiki_build_maintenance`; and
- `wiki_reconcile_temporal_candidates`.

Model their candidate-only, mutation, stewardship, observation, unknown,
signal, reconciliation, and intent-specific branches from the producer
contracts and fixtures. Preserve literal read-only values such as
`mutation.allowed=False`; schema validation must strengthen, not loosen, the
no-mutation guarantee. Preserve the existing invalid-input conversion to a
structured error.

Targeted tests:

```bash
python -m unittest \
  tests.test_mcp_server \
  tests.test_mcp_temporal \
  tests.test_mcp_temporal_activation \
  tests.test_mcp_unified_maintenance -v
```

Completion criterion: all five tools advertise object-root schemas; the
legacy candidate, temporal proposal/reconciliation, and every supported
unified-maintenance intent validate; invalid proposal inputs retain their
exact structured error contract.

### 6. Prove complete declaration and prevent regression

Add one exhaustive assertion in `tests/test_mcp_server.py` against the actual
`tools/list` response. Define the expected twenty-name set explicitly and
assert:

- the discovered names equal that set;
- every discovered tool has `output_schema is not None`;
- every schema root is an object;
- every schema exposes the success/error alternatives required by its named
  model; and
- no tool silently falls back to an inferred untyped dictionary schema.

Do not count decorators with source-text inspection as acceptance; query the
running stdio server. Keep a model-to-tool table in the test or production
module so a newly added tool cannot evade this gate.

Run the smallest MCP-focused suite covering all affected contracts:

```bash
python -m unittest \
  tests.test_mcp_server \
  tests.test_mcp_compiler \
  tests.test_mcp_temporal \
  tests.test_mcp_temporal_activation \
  tests.test_mcp_unified_maintenance -v
```

Completion criterion: the real server advertises exactly twenty tools and the
test's final assertion reports zero applicable tools lacking `outputSchema`.

### 7. Fresh-host typed-result acceptance

This checkpoint is required because unit and SDK clients cannot prove what the
Codex host cached or displays. After all targeted tests pass, refresh the
installed editable package/server using the repository's established install
workflow, restart one fresh Codex host session so it performs a new
`tools/list`, and make one read-only `wiki_list` call against the configured
registry. Do not register, unregister, or mutate a wiki for this check.

Record evidence that the fresh host:

- sees `wiki_list.outputSchema` rather than the pre-change null value;
- exposes/consumes the call as a typed `CallToolResult` with the declared wiki
  list fields;
- receives the normal success marker and unchanged structured payload; and
- can still surface a structured tool error through a temporary isolated
  missing-home server check if the host has no safe way to provoke an error
  against its configured registry.

This is the only host restart required. Do not repeat it as reassurance once
the evidence is captured.

Completion criterion: a newly initialized Codex host, not a pre-existing MCP
connection, observes the advertised schema and typed read-only result.

## Final Completion Checklist

- `src/llm_wiki_mcp/output_models.py` contains strict, reusable nested models
  and one named object-root output model for each of the twenty tools.
- Every handler in `src/llm_wiki_mcp/mcp_server.py:create_server` uses the
  supported SDK 2.x `Annotated[CallToolResult, OutputModel]` return contract.
- `_success`, `_handle_wiki_error`, `SUCCESS_MARKER`, diagnostic text,
  `structured_content`, and `is_error` retain their current wire behavior.
- Real success and error payloads validate server-side; a malformed payload is
  demonstrably rejected.
- Optional fields remain absent when absent; no TypedDict/null-materialization
  regression reaches the wire.
- Context, temporal, and unified-maintenance variants are modeled from their
  authoritative producers and fixtures.
- The focused MCP suite passes.
- The exhaustive live `tools/list` assertion proves **no applicable llm-wiki
  MCP tool lacks `outputSchema`**.
- One fresh Codex host proves the representative host-visible typed result.
- No independent review is initiated by this plan; implementation stops after
  the targeted acceptance checks pass.

## Completion Evidence

Implemented all twenty strict object-root output contracts without changing
the existing MCP handler bodies or wire-result helpers. The MCP-focused suite
passed 24 tests, including real stdio schema advertisement, success and error
validation, malformed-output rejection, temporal variants, and unified
maintenance variants. A fresh Codex host then exposed `wiki_list` as a typed
`CallToolResult` and completed a real read-only call against the configured
registry.

The installed server is the repository's editable environment at
`/Users/brummerv/llm-wiki/.venv/bin/llm-wiki-mcp`, so the host proof exercised
this checkout rather than a separately copied runtime.

> **TL;DR:** All twenty existing llm-wiki MCP tools now advertise and enforce
strict typed output schemas, and the result is proven through both the real
stdio server and a fresh Codex host.
