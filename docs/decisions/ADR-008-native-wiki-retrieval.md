# ADR-008: Retrieve Wiki Evidence Natively

## Status

Accepted

## Date

2026-11-05

## Context

Wiki retrieval can read local page sections, authored links, metadata, and
source evidence directly. The external indexed-section and graph integration
added service, protocol, store identity, and cache lifecycle dependencies to
those reads. That dependency is unnecessary for the requested standalone wiki
runtime.

## Decision

Use exactly five configured providers: `seed`, `frontmatter`, `text`, `graph`,
and `source`. Retrieve local sections, native Markdown links and `mentioned_in`
edges, and declared source evidence from the wiki snapshot. Keep question
relevance, authored knowledge state, provenance, role coverage, and hard
response budgets in the compiler.

Remove the external provider, graph backend selector, gateway, graph mirror,
and external readiness diagnostics. No replacement index or service is added.
MCP doctor exposes only local provider availability. Historical reports and
source evidence remain intact.

Keep configuration schema 1 and advance the wiki runtime contract from 2 to 3.
Runtime 2 wikis require the existing dry-run, exact-plan apply, verification,
and receipt-backed rollback flow; migration removes only recognized retired
configuration while preserving local policy. Compiler request contracts 1, 2,
and 3 remain unchanged.

This supersedes ADR-005 and the external-provider portions of ADR-004 and
ADR-007. Their remaining provenance, state, selection, and budgeting decisions
continue to apply. See [the current retrieval and migration contract](../context-compiler.md).

## Consequences

Retrieval works without an external index process or machine-specific store.
Native graph evidence describes authored links, not inferred semantic truth.
Missing local evidence and unsupported relationships remain visible limitations.
Existing runtime 2 deployments must migrate before compiling with runtime 3.
