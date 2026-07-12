# ADR-003: Centralize Shared Behavior in a Versioned Runtime Contract

## Status

Accepted

## Date

2026-07-12

## Context

`llm-wiki` currently scaffolds graph and query scripts into each wiki. The MCP runtime then dynamically imports the registered wiki's local copies. This preserves local executability, but it also makes every wiki an independent runtime fork: a canonical fix does not reach an existing wiki until its scripts are copied again, and wiki-specific patches are indistinguishable from stale shared code.

This has become a product-level constraint. The desired improvement is durable traversal and context compilation across all wikis, especially Brain and `ai_graph_ideas`, rather than another isolated experiment or hand-synchronized script update.

ADR-001 requires the graph layer to remain an adapter over portable Markdown. ADR-002 requires MCP to remain agent-scoped and read-only for wiki content. The runtime ownership decision must preserve both constraints.

## Decision

Make the installed `llm-wiki` package the single authored implementation of shared parsing, graph traversal, context compilation, budgeting, state interpretation, public response schemas, and migration compatibility.

Each wiki declares a versioned `.llm-wiki.toml` contract containing policy and supported local variation. Wiki-local query/graph scripts may remain as compatibility entrypoints, but they delegate to the canonical runtime and contain no fallback implementation of shared business logic.

Plain Markdown remains the canonical knowledge artifact. A runtime dependency is permitted for agent tooling; it is not required to read, edit, version, or migrate the underlying content.

Existing wikis migrate only through an explicit inspect, dry-run, apply, verify, and rollback workflow. MCP access alone never mutates a wiki.

## Alternatives Considered

### Continue Copying Full Scripts

This preserves today's local execution model but leaves propagation manual and keeps code drift as a permanent operational burden.

Rejected. It cannot guarantee that Brain and other existing wikis receive a shared capability or security fix.

### Generate and Vendor Canonical Runtime Snapshots

Generated snapshots could make drift detectable and preserve offline execution, but the snapshot is still a second executable copy. Compatibility defects and unreviewed local edits would remain possible between releases.

Rejected for shared business logic. Generated thin adapters and declared version metadata are acceptable.

### Move Wiki Content Into a Central Service or Database

A central service could own both behavior and content, eliminating local version skew at the cost of portability, inspectability, and the current repository workflow.

Rejected. It conflicts with ADR-001 and is unnecessary to solve runtime propagation.

### Keep MCP Canonical but Leave CLI Scripts Independent

This would improve remote agent reads while retaining two traversal implementations with different behavior.

Rejected. MCP and CLI must share the same core contract even if their transport and presentation differ.

## Consequences

- A canonical runtime upgrade improves every compatible registered wiki without copying business logic into each repository.
- Wiki-specific behavior becomes explicit, reviewable configuration.
- Local CLI tooling now has a declared package/runtime dependency and must fail loudly when it is absent or incompatible.
- The runtime needs strict backward compatibility, version negotiation, and migration tests.
- Existing script customizations require inventory and translation before replacement; unsupported changes block automatic migration.
- `wiki_doctor` becomes the authority for schema/runtime compatibility and drift status.
- Brain and `ai_graph_ideas` remain separate knowledge stores with separate governance even though they share implementation.
- MCP remains read-only for wiki content, preserving ADR-002.

## Follow-Up

- Approve the [Context Compiler v2 spec](../superpowers/specs/2026-07-12-context-compiler-v2-design.md).
- After approval, create a phased implementation and migration plan.
- Record a separate decision before adding a remote runtime, hosted content store, or automatic wiki mutation path.
