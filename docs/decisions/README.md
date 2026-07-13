# Decision Log

This directory stores Architecture Decision Records (ADRs) for `llm-wiki`.

Use an ADR when a decision changes architecture, public behavior, dependency posture, data shape, or the agent contract. Keep implementation checklists in plans or specs; keep decision rationale here.

## Status Values

- Proposed: under discussion.
- Accepted: current direction.
- Superseded: replaced by a later ADR.
- Deprecated: intentionally no longer recommended.

## Records

| ADR | Status | Date | Decision |
|---|---|---:|---|
| [ADR-001](./ADR-001-agent-graph-layer-adapter.md) | Accepted | 2026-06-19 | Build the agent graph layer as an adapter over plain markdown. |
| [ADR-002](./ADR-002-agent-scoped-mcp-context-server.md) | Accepted | 2026-06-23 | Serve wiki context over local, agent-scoped MCP. |
| [ADR-003](./ADR-003-canonical-runtime-and-versioned-wiki-contract.md) | Accepted | 2026-07-12 | Centralize shared behavior in a versioned runtime contract. |
| [ADR-004](./ADR-004-question-shaped-state-aware-context-compiler.md) | Accepted | 2026-07-12 | Compile question-shaped context with explicit knowledge state. |
| [ADR-005](./ADR-005-loci-first-class-default-traversal.md) | Accepted | 2026-07-13 | Make loci core, default-on traversal with explicit degradation. |
