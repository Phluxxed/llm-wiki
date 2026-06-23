# ADR-001: Build Agent Graph Layer As An Adapter

## Status

Accepted

## Date

2026-06-19

## Context

`llm-wiki` already works well as a lightweight markdown wiki for humans. The missing value is agent use: an agent dropped into an existing wiki needs a deterministic way to recover important pages, relationships, source context, risks, open questions, and gaps.

The reviewed donor projects point in the same direction:

- Keppi is a strong donor for graph traversal verbs, graph health, and context packs.
- MemoryOS is a useful donor for labeled context and source hierarchy.
- Other LLM-wiki style repos reinforce bounded graph expansion, provenance, and health reports.

The core constraint is that the generated wiki must remain plain markdown and portable files. Tooling can become smarter, but the artifact should stay inspectable, repairable, and versionable without a database, daemon, vector store, or hosted service.

See [Agent Graph Layer Plan](../agent-graph-layer-plan.md) and [Keppi Steal List](../keppi-steal-list.md) for implementation details and prior-art notes.

## Decision

Build the agent graph/context capability as an adapter layer over the existing wiki files.

The v1 implementation should use current wiki signals:

- markdown body links
- `mentioned_in`
- backlinks and outlinks
- tags
- page type/category
- source references
- risks, open questions, and log context where available

The v1 value milestone is a deterministic context pack:

```bash
.venv/bin/python3 scripts/query.py --context-pack <page> --tokens 12000 --json
```

Traversal commands such as `--agent-overview`, `--links`, `--backlinks`, and `--around` are necessary foundation, but they are not enough by themselves. They give the agent a map; the context pack turns that map into usable working context. Agent-facing query commands should also support `--json`, because structured output is part of making the wiki useful to agents rather than only pasteable for humans.

## Alternatives Considered

### Depend Directly On Keppi

Keppi already explores the right space, but it is not shaped for this repo. It brings Obsidian-oriented assumptions, daemon/watch behavior, MCP concerns, and optional semantic search machinery that are not required for the base path.

Rejected for v1. Keppi remains a design donor, not a dependency.

### Add A Database Or Vector Store

A database or vector index could support stronger retrieval later, especially for semantic search. It also adds index lifecycle, invalidation, dependency, and operational complexity.

Rejected for v1. The base agent layer should work with repo files and standard scripts.

### Change The Wiki Format

New link syntax, generated graph files as canonical state, or mandatory metadata changes could make agent retrieval easier. They would also weaken the main strength of `llm-wiki`: boring, portable markdown that humans and agents can inspect directly.

Rejected for v1. Backward-compatible metadata additions can be considered later if a concrete capability requires them.

### Build A Separate MCP Server First

An MCP server could eventually be a good interface for agents. Starting there would make the first implementation depend on serving, tool schemas, runtime setup, and integration behavior before proving the underlying graph primitives.

Rejected for v1. CLI commands are the base contract. MCP can wrap proven commands later.

## Consequences

- Existing wiki repos can benefit without migration.
- The graph layer must share parsing and link-resolution logic with `render.py` and `lint.py` to avoid drift.
- Agent-facing commands need stable, documented output because they become part of the practical agent contract.
- Context output must include inclusion reasons so agents do not treat retrieved pages as magic or equal-weight evidence.
- Markdown output remains useful for humans and pasteable context; JSON output is the structured agent interface.
- Dependency decisions remain capability-specific. `networkx` can be justified later for real bridge/community metrics, but not for basic backlinks, traversal, or context packs.

## Follow-Up

- Implement the MVP from [Agent Graph Layer Plan](../agent-graph-layer-plan.md): Phases 1-4.
- Keep link suggestions report-only until evidence quality is proven.
- Write a new ADR before adding semantic search, an MCP server, or a graph-analysis dependency.
