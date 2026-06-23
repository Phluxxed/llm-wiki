# llm-wiki MCP Context Plan

## Problem Statement

How might we make one or more `llm-wiki` folders available to an agent as usable brain/context from anywhere, without changing the wiki format, replacing `/wikime`, or weakening personal/work separation between agent runtimes?

The goal is not to make the MCP server own wiki authoring. `llm-wiki` already works because agents read `wiki-agent.md` and mutate plain files directly. The missing piece is reliable access to the right wiki context when the agent is not already sitting inside that wiki.

## Target User

The primary user is an agent working in an arbitrary repo or directory that needs context from a registered `llm-wiki`.

The human user benefits by getting a stable local "brain" surface without having to manually point each session at the right wiki, re-run query commands, or risk exposing work wikis to a personal agent runtime.

## Success Criteria

- An agent can list the wikis attached to its own runtime and select one by alias.
- An agent can retrieve overview, graph, page, source, and context-pack data from a registered wiki without being in that wiki's directory.
- `/wikime` remains the scaffold and migration workflow.
- A newly scaffolded wiki can be auto-registered for the current agent runtime only.
- Codex and Claude registrations are isolated by default.
- The MCP server does not mutate wiki content.
- Existing wiki folders require no format migration to be served.
- The implementation is local stdio MCP only: no HTTP server, daemon, remote service, auth layer, or shared hosted state.

## Architecture Decisions

### Keep `/wikime` As The Setup Workflow

`/wikime` has judgment baked in: it asks the setup questions, stops on existing wikis, offers migrations, and preserves user confirmation around content-affecting changes. MCP should not replace this with a rigid scaffold API.

The MCP layer can provide deterministic helper behavior underneath or beside `/wikime`, but `/wikime` remains the user-facing creation and migration surface.

### MCP Is Context And Navigation, Not Mutation

The MCP server should expose the information an agent needs before it writes:

- which wikis are available
- what pages exist
- how pages connect
- which source excerpts matter
- what risks and open questions are active
- what context pack should be loaded for a task
- whether the wiki tooling is healthy

It should not expose page creation, ingest, arbitrary file writes, or patch tools in v1. Agents already mutate wikis through normal file edits while following `wiki-agent.md`; duplicating that workflow in MCP would create a second source of behavior and drift.

### Use Agent-Scoped Registries

There must be no neutral global registry shared by Codex and Claude.

Codex is personal. Claude may be work. A shared registry would risk exposing work wiki paths or contents to the wrong runtime. Each agent gets its own registry:

```text
~/.codex/llm-wiki/registry.json
~/.claude/llm-wiki/registry.json
```

The MCP client config should pass the registry home explicitly:

```bash
codex mcp add --env LLM_WIKI_HOME="$HOME/.codex/llm-wiki" llm-wiki -- llm-wiki-mcp
claude mcp add llm-wiki -s local -e LLM_WIKI_HOME="$HOME/.claude/llm-wiki" -- llm-wiki-mcp
```

If `LLM_WIKI_HOME` is absent, the server should fail closed unless it can confidently infer the current agent runtime without crossing agent homes.

### No Automatic Cross-Attach

When `/wikime` creates a wiki under Codex, it may register that wiki in Codex's registry. It must not also attach it to Claude.

When `/wikime` creates a wiki under Claude, it may register that wiki in Claude's registry. It must not also attach it to Codex.

Explicit cross-attachment can be done later by running the setup from the other agent runtime. It is not the default path.

### Preserve Plain Markdown As The Canonical Artifact

The MCP server reads existing wiki files and generated script outputs. It does not introduce a database, canonical graph file, generated index cache, or hidden state inside the wiki folder.

Registry state lives outside the wiki, under the agent-specific MCP home.

### Build On The Existing Graph Layer

The current graph/context layer already provides the core primitives:

- `--agent-overview`
- `--links`
- `--backlinks`
- `--around`
- `--graph-health`
- `--context-pack`
- `--json`

The MCP implementation should reuse these behaviors directly through importable Python functions where practical. Shelling out to `query.py --json` can be a short-term bridge, but the production contract should not depend on parsing CLI output if the same data can be returned from a shared internal module.

## Proposed MCP Tools

Tool names use a `wiki_` prefix so they are readable in mixed MCP tool lists.

### Registry And Health

| Tool | Purpose |
| --- | --- |
| `wiki_list` | List wikis registered for the current agent runtime. |
| `wiki_register` | Register an existing wiki path under an alias in the current agent registry. |
| `wiki_unregister` | Remove an alias from the current agent registry. |
| `wiki_doctor` | Validate that a registered wiki exists, looks like an `llm-wiki`, and has usable graph/query tooling. |

`wiki_register` writes only to the current agent's registry. It does not edit the wiki folder.

### Context And Navigation

| Tool | Purpose |
| --- | --- |
| `wiki_overview` | Return the agent overview for a wiki alias. |
| `wiki_query` | Return frontmatter query results by type, tag, category, status, stale days, or risks. |
| `wiki_links` | Return outgoing graph links for a page. |
| `wiki_backlinks` | Return incoming graph links for a page. |
| `wiki_around` | Return a bounded graph neighborhood around a page. |
| `wiki_context_pack` | Return deterministic task context around a seed page with inclusion reasons. |
| `wiki_get_page` | Return page metadata and bounded page content. |
| `wiki_get_source_excerpt` | Return a bounded excerpt from a source file referenced by a page or source path. |
| `wiki_graph_health` | Return graph health, hubs, orphans, components, and source gaps. |

### Deferred Tools

These are deliberately out of v1:

- `wiki_create_page`
- `wiki_update_page`
- `wiki_ingest`
- `wiki_apply_patch`
- `wiki_append_log`
- `wiki_render`
- `wiki_lint` as a mutating workflow

Read-only lint/doctor information is useful. Mutation and authoring should stay under normal agent file work and `wiki-agent.md`.

## Registry Shape

Initial registry format:

```json
{
  "version": 1,
  "wikis": {
    "strategy-brain": {
      "path": "/Users/brummerv/phluxxed/strategy_brain",
      "created_by": "wikime",
      "registered_at": "2026-06-23T00:00:00Z"
    }
  }
}
```

Rules:

- Aliases are local to the current agent registry.
- Paths must resolve to directories.
- Registration should validate `wiki-agent.md`, `index.md`, `log.md`, and `scripts/query.py`.
- If graph/context scripts are missing, registration may succeed with a warning, but `wiki_doctor` must report the missing capability and suggested `/wikime` migration path.
- The server never scans the filesystem for wikis automatically.

## Implementation Plan

### Phase 1: Package And MCP Skeleton

**Description:** Add an installable Python package surface and local stdio MCP entrypoint without changing generated wiki folders.

**Acceptance criteria:**
- `llm-wiki-mcp` is available as a console script in the development environment.
- The MCP server starts over stdio and lists basic registry tools.
- The server reads `LLM_WIKI_HOME` and fails closed when no safe registry home is available.

**Verification:**
- `python -m pytest` for any existing tests that remain applicable.
- Focused MCP smoke test using a real stdio MCP client.

**Dependencies:** None

**Files likely touched:**
- `pyproject.toml`
- `src/llm_wiki_mcp/__init__.py`
- `src/llm_wiki_mcp/mcp_server.py`
- `tests/test_mcp_server.py`

**Estimated scope:** Medium

### Phase 2: Agent-Scoped Registry

**Description:** Implement registry load/save, alias validation, path validation, and host separation.

**Acceptance criteria:**
- `wiki_register`, `wiki_unregister`, and `wiki_list` operate only within `LLM_WIKI_HOME`.
- Registries under separate temp homes cannot see each other's wikis.
- Registration never edits wiki content.
- Invalid paths and non-wiki directories return structured errors.

**Verification:**
- Unit tests with separate temp `LLM_WIKI_HOME` values.
- MCP tool tests for register/list/unregister.

**Dependencies:** Phase 1

**Files likely touched:**
- `src/llm_wiki_mcp/registry.py`
- `src/llm_wiki_mcp/mcp_server.py`
- `tests/test_registry.py`
- `tests/test_mcp_server.py`

**Estimated scope:** Medium

### Phase 3: Wiki Runtime Adapter

**Description:** Add importable functions for loading a registered wiki and returning the same structured data currently produced by agent graph query commands.

**Acceptance criteria:**
- Overview, graph health, links, backlinks, around, and context-pack data can be returned as Python dicts.
- The implementation reuses existing graph/query logic rather than duplicating link resolution.
- Unknown aliases and unknown pages return structured errors.

**Verification:**
- Unit tests against a fixture wiki.
- Existing graph/query tests still pass.

**Dependencies:** Phase 2

**Files likely touched:**
- `src/llm_wiki_mcp/wiki_runtime.py`
- `scripts/query.py` if small extraction is needed
- `scripts/wiki_graph.py` if small extraction is needed
- `tests/test_wiki_runtime.py`

**Estimated scope:** Medium

### Phase 4: Context And Navigation MCP Tools

**Description:** Expose the core wiki context tools through MCP with stable input/output/error shapes.

**Acceptance criteria:**
- MCP exposes `wiki_overview`, `wiki_query`, `wiki_links`, `wiki_backlinks`, `wiki_around`, `wiki_context_pack`, `wiki_get_page`, `wiki_get_source_excerpt`, and `wiki_graph_health`.
- Tool outputs are object-wrapped and stable.
- Tool errors use a single structured shape with machine-readable codes.
- Tools do not mutate wiki content.

**Verification:**
- MCP stdio tests covering one happy path per tool.
- Error tests for unknown alias, unknown page, missing source, and unhealthy wiki tooling.

**Dependencies:** Phase 3

**Files likely touched:**
- `src/llm_wiki_mcp/mcp_server.py`
- `src/llm_wiki_mcp/wiki_runtime.py`
- `tests/test_mcp_server.py`

**Estimated scope:** Medium

### Phase 5: `/wikime` Registration Hook

**Description:** Update `/wikime` instructions so newly scaffolded wikis are automatically registered for the current agent runtime when MCP registry configuration is available.

**Acceptance criteria:**
- `/wikime` still asks the same setup questions and remains the scaffold workflow.
- New wiki scaffolding attempts current-agent registration only.
- The instructions explicitly forbid cross-agent registration.
- Existing wiki detection does not silently register or migrate content without user intent.
- If MCP registration is unavailable, scaffolding still succeeds and reports the registry gap.

**Verification:**
- Review `SKILL.md` for consistency with the personal/work separation rule.
- Existing scaffold tests or manual dry-run notes confirm the wiki files are unchanged except for normal scaffolding output.

**Dependencies:** Phase 2

**Files likely touched:**
- `SKILL.md`
- `README.md`
- `_templates/CONVENTIONS.md` only if the generated docs need a small MCP note
- tests covering scaffold docs if present later

**Estimated scope:** Small

### Phase 6: Installation And Agent Docs

**Description:** Document installation and MCP setup for Codex and Claude without sharing registries.

**Acceptance criteria:**
- README includes Codex and Claude MCP setup commands with explicit `LLM_WIKI_HOME`.
- Docs explain that Codex and Claude registries are intentionally separate.
- Docs explain that MCP is context/navigation, while wiki mutation remains normal agent file work under `wiki-agent.md`.
- Docs include unregister/removal guidance.

**Verification:**
- `README.md` examples are command-copyable.
- Manual smoke check of `llm-wiki-mcp` under a temp registry.

**Dependencies:** Phase 4

**Files likely touched:**
- `README.md`
- `SKILL.md`
- new docs section or `docs/llm-wiki-mcp-context-plan.md` updates

**Estimated scope:** Small

## Checkpoints

### Checkpoint: Foundation

After Phases 1-2:

- MCP server launches locally over stdio.
- Registry operations work under a temp `LLM_WIKI_HOME`.
- Separate registry homes are isolated.
- No wiki content is edited.

### Checkpoint: Useful Context

After Phases 3-4:

- A registered fixture wiki can serve overview and context-pack data through MCP.
- The same graph semantics as `scripts/query.py` are preserved.
- Tool errors are structured and recoverable.

### Checkpoint: Workflow Integration

After Phases 5-6:

- `/wikime` remains the setup workflow.
- Newly scaffolded wikis register with the current agent only.
- Codex and Claude setup docs cannot accidentally point at the same registry.

## Risks And Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Cross-agent data leak between personal Codex and work Claude | High | No shared registry; require explicit `LLM_WIKI_HOME`; fail closed on ambiguous host detection. |
| MCP grows into a second wiki authoring system | High | Keep v1 read/context/navigation only; mutation remains normal agent file work under `wiki-agent.md`. |
| Runtime logic drifts from `query.py` and `wiki_graph.py` | Medium | Reuse importable functions where possible; keep CLI and MCP tests over the same fixture expectations. |
| Existing wikis lack current graph tooling | Medium | `wiki_doctor` reports migration status; `/wikime` remains the migration path. |
| Packaging complicates a currently simple skill repo | Medium | Keep package scope limited to MCP runtime; do not require generated wikis to become Python packages. |
| MCP setup friction makes the feature unused | Medium | `/wikime` auto-registers new wikis for the current agent when available; docs provide copyable setup commands. |

## Open Questions

- Should `wiki_query` cover only existing frontmatter filters, or should v1 add lightweight text search?
- Should `wiki_get_page` return full content by default, or require an explicit max character budget?
- Should `wiki_doctor` call wiki-local scripts directly, or inspect files only unless the caller asks for script execution?
- What exact environment signal should `/wikime` use to detect "current agent" for registration when both Codex and Claude tooling exist on the same machine?

## Not Doing

- No shared `~/.llm-wiki` registry.
- No cross-agent auto-attach.
- No HTTP server or remote MCP server.
- No daemon, watcher, or background indexer.
- No database or vector store.
- No MCP write proxy for page mutation.
- No requirement that generated wikis ship or run their own MCP server.
- No silent migration of existing wiki content.
