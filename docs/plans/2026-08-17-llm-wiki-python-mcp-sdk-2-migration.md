# llm-wiki Python MCP SDK 2 Migration Plan

**Date:** 2026-08-17  
**Status:** Approved; implementation in progress  
**Primary repository:** `/Users/brummerv/llm-wiki`  
**Baseline commit:** `6681a57`  
**Loci dependency:** satisfied; the upgraded Loci stdio MCP boundary is
available for direct integration testing.  
**Related research:** [MCP Python SDK 2.0](../../../phluxxed/workbench/docs/research/mcp-python-sdk-2.0.md)  
**Preserved acceptance contract:** [Structured MCP Result and Brain Alias
Safety](../../../phluxxed/workbench/docs/plans/2026-08-17-structured-mcp-result-and-brain-alias-safety.md)

## Outcome

Migrate llm-wiki from Python MCP SDK 1.x to the stable 2.x line without
changing what its tools mean, how agent registries are scoped, or how Brain and
other wikis are navigated. The shipped `llm-wiki-mcp` stdio server must support
the modern MCP protocol implemented by SDK 2.x while remaining usable by the
configured Codex host, and llm-wiki's internal client must continue to retrieve
from the upgraded Loci stdio server with bounded timeouts and explicit
degradation.

The migration is complete only when one installed producer-consumer path proves
all of the following:

1. the actual `llm-wiki-mcp` console entrypoint starts under `mcp>=2,<3`;
2. a Python SDK 2 `Client` in automatic mode discovers and calls the server;
3. successful tools retain the visible bounded marker and complete
   `structured_content` payload;
4. expected failures retain diagnostic text, a structured error envelope, and
   `is_error=True`;
5. `wiki_compile_context` completes one real nested call through llm-wiki's
   SDK 2 client to the upgraded Loci server;
6. a restarted Codex host can list and navigate the canonical Brain registration
   without registry or Brain mutation; and
7. the negotiated protocol revision is recorded wherever the SDK or host makes
   it observable, without adding a product introspection tool solely for the
   migration.

This is an SDK and protocol-compatibility migration, not a product redesign.

> **TL;DR:** llm-wiki will run on Python MCP SDK 2, serve its existing tools over the real stdio entrypoint, call the upgraded Loci server through the new client, and preserve the exact structured success, error, registry, and Brain-navigation behavior users already rely on.

## Current Baseline And Inventory

The migration branch starts from synchronized and published `main` at
`6681a57`. Its installed environment currently uses `mcp==1.28.1`, while
`pyproject.toml` permits `mcp>=1.27,<2`.

### Shipped server surface

`src/llm_wiki_mcp/mcp_server.py` is the only MCP server:

- it imports `FastMCP` from `mcp.server.fastmcp`;
- it exposes twenty synchronous `@mcp.tool()` handlers;
- it uses the high-level server only—there is no low-level `Server`;
- it runs only over stdio through the `llm-wiki-mcp` console entrypoint;
- it has no HTTP/SSE transport, auth, lifespan, context injection, sampling,
  elicitation, roots calls, subscriptions, or server-initiated requests;
- every handler returns an explicit `CallToolResult` through one shared success
  and error adapter; and
- the current Python model construction uses the SDK 1 field spellings
  `structuredContent` and `isError`.

The v2 server port is therefore a high-level rename and type/field migration,
not a low-level handler rewrite.

### Shipped client surface

`src/llm_wiki_core/providers/loci_transport.py` is llm-wiki's only production
MCP client transport. It currently:

- launches `loci-mcp` over stdio with explicit command, environment, and store
  identity;
- creates `ClientSession`, calls `initialize()`, and runs a bounded operation;
- enforces a timeout and suppresses the child process's stderr;
- converts structured MCP failures into `LociGatewayError`;
- treats `structuredContent` as authoritative and retains JSON text fallback;
  and
- preserves provider degradation rather than silently switching traversal
  backends.

`src/llm_wiki_core/providers/loci.py` and
`src/llm_wiki_core/providers/loci_graph.py` type and call that session. The
graph provider performs real multi-call operations including index, outline,
search/retrieval, and hydration.

### Test and ancillary SDK surfaces

Real subprocess stdio tests currently live in:

- `tests/test_mcp_server.py`;
- `tests/test_mcp_compiler.py`;
- `tests/test_mcp_temporal.py`;
- `tests/test_mcp_temporal_activation.py`; and
- `tests/test_mcp_unified_maintenance.py`.

Fake MCP servers and outbound-client behavior are exercised in:

- `tests/test_loci_provider.py`; and
- `tests/test_loci_graph_provider.py`.

`scripts/graph_shape_baseline.py` imports and patches `ClientSession.call_tool`
for call-count measurement and must remain compatible with the chosen client
surface.

The repository already has the important wire-level invariants. In particular,
`tests/test_mcp_server.py` asserts the bounded success marker, complete
structured payloads, and diagnostic structured errors through a real stdio
round trip. Those are migration gates, not tests to replace with handler-only
coverage.

### Absent high-risk surfaces

The inventory found no HTTP or auth integration, low-level server, direct
`request_context`, server-to-client back-channel, custom URI types, resource or
prompt handlers, notification stream, task runtime, or direct `httpx` use.
Those SDK 2 migration branches do not belong in this plan unless implementation
finds contrary live evidence.

> **TL;DR:** llm-wiki has one simple high-level stdio server and one meaningful outbound stdio client to Loci; the server port is mostly mechanical, while the client, result normalization, timeout behavior, and real transport tests carry the migration risk.

## Migration Decisions

### Target SDK and protocol posture

- Change the package constraint to `mcp>=2,<3` only on the migration change;
  do not publish an intermediate build that claims SDK 2 support while retaining
  SDK 1 code.
- Replace `FastMCP` with the SDK 2 high-level `MCPServer`; keep the decorator
  topology and the existing stdio console entrypoint.
- Use the SDK 2 first-class `Client` for llm-wiki's outbound Loci connection in
  `mode="auto"`. Do not make legacy mode the steady state.
- Let the SDK negotiate modern or legacy behavior for the real host connection.
  Prove modern `2026-07-28` support with the SDK 2 client acceptance path and
  prove actual Codex compatibility through the restarted host path.
- Keep imports from `mcp.types` unless the port demonstrates a concrete need
  for a direct `mcp-types` dependency. The SDK promises those re-exports.

### Preserve the result contract

- Construct and consume Python result fields with SDK 2 snake-case names,
  including `structured_content` and `is_error`.
- Preserve JSON wire spelling as SDK-owned camelCase; do not hand-serialize MCP
  messages.
- Keep `structured_content` authoritative for both success and error results.
- Keep the existing bounded text marker on success and diagnostic text on
  error. Do not copy full payloads into text.
- Retain the outbound client's JSON text fallback as a compatibility fallback,
  but never let the non-JSON success marker replace a missing structured
  payload.

### Preserve transport and execution semantics

- Keep stdio as the only shipped transport.
- Preserve explicit Loci command, arguments, environment, store identity,
  timeout, and stderr handling when moving to `Client`.
- Preserve one connected client across each multi-call provider operation.
- Confirm that SDK 2 worker-thread execution for synchronous server handlers
  does not alter registry, environment, or filesystem behavior.
- Keep existing tool names, argument schemas, payloads, registry format, error
  codes, and candidate-only mutation envelopes unchanged.

### Integrate with upgraded Loci

The upgraded installed `loci-mcp` is now the real acceptance target. The
llm-wiki client port must not be declared runnable against a fake server alone.
The nested acceptance phase uses Loci's actual stdio boundary, which provides:

- its actual stdio command;
- a stable SDK/protocol compatibility statement;
- successful list/discovery and representative retrieval calls; and
- the structured success and error envelopes llm-wiki consumes.

Normalizing Loci's legacy advisory `path` arguments to `repo` is not part of the
SDK migration. If the upgraded Loci contract requires that separate integration
change, stop and scope it explicitly rather than hiding it inside the dependency
bump.

> **TL;DR:** The migration adopts SDK 2's high-level server and automatic client while keeping stdio, tool contracts, structured results, timeouts, and registry behavior stable; Loci must expose a verified upgraded boundary before llm-wiki can complete its nested acceptance.

## Implementation Plan

### 1. Record the migration starting point

Repository: `/Users/brummerv/llm-wiki`

Before changing dependencies, record only the state needed for rollback and
reproducibility:

- record `git status`, Python version, `uv` version, installed `mcp` version,
  and the exact `loci-mcp` command/version available to the test environment;
- record the configured llm-wiki registry hash for the later installed smoke;
  and
- confirm the canonical Brain alias/root through `anvil_session_bootstrap` and
  `wiki_list` without mutating the registry.

Do not run the existing SDK 1 suite as a ceremonial baseline. The live service
already establishes that the starting system runs; verification belongs to the
changed SDK 2 paths and the final installed boundary.

Completion criterion: the starting commit, resolved versions, registry hash,
Brain alias/root, and installed Loci target are recorded without mutation.

### 2. Port the high-level server and shared result adapter

Modify:

- `pyproject.toml`;
- `src/llm_wiki_mcp/mcp_server.py`; and
- `tests/test_mcp_server.py`.

Work in an isolated temporary v2 environment first so the installed Codex MCP
process remains usable during the port:

```bash
MIGRATION_VENV="$(mktemp -d)/venv"
uv venv "$MIGRATION_VENV"
uv pip install --python "$MIGRATION_VENV/bin/python3" -e .
```

Then:

- constrain the package to `mcp>=2,<3`;
- replace `FastMCP` with `MCPServer` using the pinned SDK's documented import;
- retain all existing decorators, sync handlers, tool descriptions, and stdio
  run boundary;
- change Python model construction and access to SDK 2 snake-case fields;
- migrate the focused test client to the SDK 2 client API in automatic mode;
- retain a real subprocess stdio round trip rather than converting this seam to
  an in-process handler test; and
- assert success marker, complete structured payload, diagnostic error text,
  structured error envelope, and `is_error` exactly as before.

Run:

```bash
"$MIGRATION_VENV/bin/python3" -m unittest tests.test_mcp_server -v
```

Completion criterion: the real llm-wiki stdio server starts and its shared
success/error contract passes under SDK 2 before any Loci client code changes.

### 3. Port the outbound Loci client as one vertical slice

Modify:

- `src/llm_wiki_core/providers/loci_transport.py`;
- `src/llm_wiki_core/providers/loci.py`;
- `src/llm_wiki_core/providers/loci_graph.py`;
- `tests/test_loci_provider.py`; and
- `tests/test_loci_graph_provider.py`.

Change the client boundary once:

- replace the explicit transport + `ClientSession` + `initialize()` stack with
  SDK 2 `Client(..., mode="auto")` using the documented stdio transport input;
- update provider callback types to the new client surface;
- update `tool_mapping()` to read `structured_content` and `is_error` first;
- retain the JSON text fallback and explicit invalid-result failure;
- preserve timeout translation, exception-tree inspection, stderr suppression,
  command resolution, store-identity refusal, and one-session multi-call
  operations; and
- migrate the embedded fake servers from `FastMCP` to `MCPServer` without
  changing their behaviors.

Add or sharpen tests that prove:

- successful structured search and hydration over stdio;
- a non-JSON success marker cannot masquerade as an object payload;
- a structured operation error preserves its code, message, and details;
- a missing command and missing store identity degrade explicitly;
- timeout behavior remains bounded under SDK 2's dispatcher/exception shape;
  and
- one real call reaches the upgraded installed Loci server.

Run:

```bash
"$MIGRATION_VENV/bin/python3" -m unittest \
  tests.test_loci_provider tests.test_loci_graph_provider -v
```

Completion criterion: llm-wiki's real outbound client can complete a
representative operation against upgraded Loci and all failure/degradation
paths remain explicit.

### 4. Port remaining MCP tests and ancillary instrumentation

Modify only as required:

- `tests/test_mcp_compiler.py`;
- `tests/test_mcp_temporal.py`;
- `tests/test_mcp_temporal_activation.py`;
- `tests/test_mcp_unified_maintenance.py`;
- `tests/test_graph_shape_baseline.py`; and
- `scripts/graph_shape_baseline.py`.

For each test surface:

- use SDK 2 client/result field names and the chosen client construction;
- preserve at least one real stdio round trip for compiler, temporal, and
  maintenance behavior;
- preserve exact structured error assertions and payload budgets;
- keep benchmark call counting attached to the actual SDK 2 call surface; and
- avoid converting transport tests into in-process tests merely to simplify
  the migration.

Run:

```bash
"$MIGRATION_VENV/bin/python3" -m unittest \
  tests.test_mcp_compiler \
  tests.test_mcp_temporal \
  tests.test_mcp_temporal_activation \
  tests.test_mcp_unified_maintenance \
  tests.test_graph_shape_baseline -v
```

Completion criterion: every existing MCP-facing test runs through supported SDK
2 APIs without weakening its transport, error, budget, or payload assertions.

### 5. Install locally and verify the complete repository

After every focused migration gate is green:

```bash
uv pip install -e .
.venv/bin/python3 -c 'from importlib.metadata import version; print(version("mcp"))'
.venv/bin/python3 -m unittest discover -s tests -p 'test_*.py'
git diff --check
```

The installed version must satisfy `mcp>=2,<3`. Update `README.md` only if the
existing `uv venv` / `uv pip install -e .` instructions or host-registration
commands actually change; do not add migration prose that simply restates the
dependency metadata.

Completion criterion: the repository's actual local environment uses SDK 2 and
the full suite passes without an SDK 1 compatibility shim.

### 6. Verify the installed producer-consumer boundary

Restart the llm-wiki MCP process after the local install. Run this sequence:

1. record the registry file hash;
2. call host-bound `anvil_session_bootstrap` and capture the exact
   `brain.wiki_alias` and `brain.brain_root`;
3. call `wiki_list` and confirm the exact pair exists in
   `structuredContent.wikis`;
4. confirm the success marker is present without a duplicated payload;
5. call `wiki_doctor`, `wiki_agent_manual`, and one bounded page/context tool;
6. call `wiki_compile_context` so llm-wiki performs a real nested retrieval
   through upgraded Loci;
7. make one deliberately invalid read-only call and confirm diagnostic text,
   structured error, and error status;
8. make no registry or Brain mutation call;
9. confirm the registry hash and Brain worktree are unchanged; and
10. run an SDK 2 `Client(mode="auto")` probe against the installed console
    entrypoint and record the negotiated protocol revision if exposed.

Completion criterion: one installed result crosses Codex host routing,
llm-wiki SDK 2 server serialization, the nested SDK 2 Loci client, structured
consumption, and read-only Brain navigation without changing durable state.

> **TL;DR:** The implementation proceeds through six gated slices—baseline, server, Loci client, remaining tests, full local install, and installed host smoke—so each SDK boundary proves its own behavior before the migration is called complete.

## Acceptance Criteria

The migration is complete only when all of the following are true:

- `pyproject.toml` requires `mcp>=2,<3`, and the installed environment reports a
  matching version.
- The server uses `MCPServer`; no `FastMCP` production import remains.
- No production code reads or writes SDK 1 Python result fields
  `structuredContent` or `isError`.
- `llm-wiki-mcp` starts through its actual console entrypoint over stdio.
- Tool discovery returns the existing tool set without renaming or schema
  drift.
- Success responses retain the bounded marker and complete structured payload.
- Error responses retain diagnostic text, structured error, and error status.
- The outbound Loci client uses SDK 2 automatic negotiation and preserves
  timeout, configuration refusal, structured errors, and invalid-result
  handling.
- One real nested Loci retrieval completes against the upgraded installed Loci
  server.
- Modern protocol support is proven with the SDK 2 client; actual Codex host
  compatibility is proven after restart.
- Focused server, client, compiler, temporal, and maintenance tests pass.
- The full llm-wiki suite and `git diff --check` pass.
- The canonical Brain read-only smoke passes with unchanged registry hash and
  unchanged Brain worktree.
- No SDK 1 compatibility adapter, legacy-mode steady state, payload duplication,
  or new product control surface is introduced.

> **TL;DR:** Acceptance requires the installed SDK 2 server, nested Loci client, structured success/error contracts, full suite, modern-protocol probe, and real read-only Brain path to work together without compatibility shims or state mutation.

## Risks, Rollback, And Stop Conditions

| Risk | Failure signal | Required response |
| --- | --- | --- |
| CamelCase Python field use survives | structured payload becomes `None`, errors look successful, or the marker is parsed as data | Stop; correct every producer and consumer to SDK 2 snake-case fields before continuing. |
| Strict v2 schema validation rejects a tool | discovery or call fails for nested `Mapping`/optional arguments or a returned payload | Fix the owning tool annotation/result shape without weakening the public payload contract. |
| Sync handlers behave differently in worker threads | registry, environment, or filesystem tests become nondeterministic | Stop and isolate the concrete thread-safety issue; do not add a generic execution layer. |
| Client port loses controls | command/env/stderr/timeout behavior changes | Keep the migration at the existing client boundary and prove equivalent controls before accepting `Client`. |
| Upgraded Loci is incompatible with the client port | fake tests pass but real nested retrieval fails | Keep llm-wiki pinned to SDK 1 in the installed environment, preserve the failing boundary evidence, and scope the concrete incompatibility rather than weakening the acceptance path. |
| Codex host negotiates only legacy MCP | SDK 2 modern probe passes but host cannot call tools | Preserve dual-era SDK support and report the host dependency; do not force legacy mode into llm-wiki's own client. |
| Scope expands | a sixth production file, new service/registry/compatibility layer, more than 500 net non-test lines, or 1,000 total changed lines is required | Stop for Vik's explicit architecture-expansion approval with the blocker, smaller alternative, and revised estimate. |

Rollback is dependency-and-code atomic:

1. retain baseline commit `6681a57` and the recorded installed versions;
2. do not modify registry or Brain content during migration;
3. if an installed smoke fails, restore the pre-migration llm-wiki commit and
   reinstall it with the existing `mcp>=1.27,<2` constraint;
4. restart the MCP process; and
5. rerun the same registry-hash, `wiki_list`, doctor, manual, and nested-Loci
   smoke to prove service restoration.

Do not publish or widen into host, Loci, registry, or Brain fixes when the
failure belongs to another owner. Preserve the failing evidence and re-plan the
cross-owner dependency explicitly.

> **TL;DR:** Rollback restores the pre-migration llm-wiki commit and SDK 1 environment without touching registry or Brain data, while any sixth production file or cross-owner fix triggers a stop-and-replan boundary.

## Scope, Estimates, And Boundaries

Expected production/config files:

| Surface | Files | Estimated net change |
| --- | ---: | ---: |
| SDK constraint and high-level server | 2 | 20-45 lines |
| Outbound Loci client and provider typing | 3 | 45-100 lines |
| Total production/config | 5 | 65-145 lines |

Expected supporting surfaces:

| Surface | Files | Estimated net change |
| --- | ---: | ---: |
| Existing MCP and Loci tests | 8 | 80-200 lines |
| Benchmark instrumentation | 1 | 5-20 lines |
| README, only if commands change | 0-1 | 0-20 lines |
| Total supporting | 9-10 | 85-240 lines |

Expected total: 14-15 files and 150-385 net lines, with no new package,
service, registry, state format, public tool, or compatibility layer. The five
production/config files stay at the current architecture-expansion threshold;
crossing that file count or either line threshold requires explicit approval
before further implementation.

The following are outside this migration:

- changing llm-wiki tool names, argument schemas, payload schemas, or error
  codes;
- changing registry format, registrations, aliases, Brain content, or wiki
  content;
- implementing Loci's own SDK/protocol upgrade;
- changing Anvil's TypeScript MCP SDK or Codex host behavior;
- adding HTTP, auth, OAuth, SSE, WebSockets, subscriptions, MRTR product flows,
  Tasks, resources, prompts, or server-initiated requests;
- adopting a direct `mcp-types` dependency without demonstrated need;
- adding a generic MCP client framework or compatibility adapter;
- normalizing Loci `path`/`repo` arguments unless separately accepted;
- converting all integration tests to in-process clients; and
- publishing or pushing before the migration is implemented, verified, and
  accepted.

> **TL;DR:** The planned migration touches five production/config files and existing tests only; product behavior, registries, Brain data, Loci's own upgrade, Anvil, new protocol features, compatibility layers, and publication remain separate work.

## Implementation Handoff

The plan is accepted and upgraded Loci is available for real integration
testing. Implementation runs on `feat/python-mcp-sdk-2`, created from clean,
synchronized `main` at `6681a57`.

Use the plan as a sequence of acceptance gates, not permission to batch the
whole port before testing. Keep the temporary SDK 2 environment isolated until
the focused server and Loci client paths pass. Install into the configured
`.venv` only after those gates are green, then restart the host process for the
installed smoke.

Implementation includes only the focused checks and the final full suite named
here. Independent review, broader protocol conformance testing, Anvil migration,
publication, and release work remain opt-in follow-ups.

Once the migrated paths and installed end-to-end boundary pass, merge the
branch directly into `main` and push it. This single-maintainer workflow does
not require a pull request.

> **TL;DR:** After plan approval and Loci readiness, migrate llm-wiki in gated server/client slices, install SDK 2 only after focused acceptance, and finish with the restarted host plus nested-Loci smoke before considering review or publication.
