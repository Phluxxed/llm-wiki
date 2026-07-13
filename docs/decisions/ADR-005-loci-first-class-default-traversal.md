# ADR-005: Make loci First-Class Default Traversal

## Status

Accepted

## Date

2026-07-13

## Context

Context Compiler v2 originally shipped loci as an opt-in provider loaded from the same Python environment as `llm-wiki`. That posture contradicted the product goal: managed wikis should use the strongest available traversal path automatically, and loci exists specifically to find and hydrate the right indexed section without broad file reads.

The opt-in implementation also failed operationally. The `llm-wiki`, Brain, and `ai_graph_ideas` virtual environments did not contain the loci Python package even though `loci-mcp` was installed and was the supported production surface. The configs therefore disabled the intended primary traversal route by default.

## Decision

Make loci a core, default-on Context Compiler provider for fresh scaffolds, legacy defaults, and managed wiki migrations.

Invoke loci through the installed local `loci-mcp` stdio service. A bounded search and batched exact-symbol hydration occur in one MCP session, returned paths are contained within the wiki before hydration, hydrated locators must match the validated search locators, and timeouts or tool failures become structured provider diagnostics.

Keep degradation deterministic. If `loci-mcp` is absent, the repo is unindexed, or the provider fails, seed, frontmatter, text, graph, and source providers continue. Removing `loci` from the provider list is an explicit opt-out.

loci ranking remains a retrieval signal. Knowledge state, authority, provenance, evidence roles, selection, and stop semantics remain owned by the Context Compiler.

This supersedes only ADR-004's statement that loci is optional. The rest of ADR-004 remains accepted.

## Alternatives Considered

### Keep loci Opt-In

Rejected because it makes the intended primary traversal capability depend on every wiki or agent remembering an operational toggle.

### Add loci as a Python Package Dependency

Rejected because loci's parser stack and index-store lifecycle do not need to be duplicated into every wiki virtual environment. It also conflicts with loci's MCP-first production boundary.

### Make loci Failure Fatal

Rejected because an unavailable index should reduce retrieval quality visibly, not make plain-file wiki access impossible.

## Consequences

- Managed wikis use loci automatically when the local MCP service and index are available.
- `wiki_doctor` reports MCP readiness, explicit opt-out, or degraded availability.
- Previous generated configs are upgraded transactionally while preserving unrelated TOML content.
- A local MCP subprocess is opened for each compiler invocation; persistent session reuse is a future performance optimization, not required for correctness.
- Deterministic local providers remain required and tested as the degradation path.
