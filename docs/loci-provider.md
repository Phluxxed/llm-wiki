# Core loci traversal and graph providers

loci supplies two independent Context Compiler routes:

- the `loci` provider searches indexed symbols, hydrates matched section ranges, and returns ordinary candidates with exact locators;
- the default `graph` backend calls `loci_graph_retrieve` for bounded, evidence-backed relationship paths. `compiler.graph_backend = "legacy"` is the explicit rollback to the old local graph provider.

The runtime calls the installed local `loci-mcp` stdio service. One compile performs a bounded `loci_search` followed by one batched `loci_get`, with a 15-second gateway timeout. This keeps the integration on loci's production MCP surface and avoids coupling the `llm-wiki` virtual environment to loci's parser dependencies. Fresh scaffolds and migrations include `"loci"` in `[compiler].providers`; removing it is an explicit opt-out. `LLM_WIKI_LOCI_MCP_COMMAND` may name an alternative executable, but command arguments and machine paths do not belong in wiki config.

Graph reads generate their Loci profile and contributions in an external cache mirror, never in the source wiki. The mirror contains canonical wiki pages and exact authored link evidence, is refreshed under a lock, and is committed only after Loci accepts the contribution. `LLM_WIKI_GRAPH_CACHE_DIR` overrides the default cache location.

The graph provider validates every returned collection, inferred anchor, path node, edge contract, source line, and page hash. Complete paths are atomic under the compiler budget. For an inferred relationship, a path only carries `bridge` when it crosses the distinct subject clusters exposed by Loci's anchor reasons. Paths confined to one cluster remain inspectable `support`; they do not establish the relationship. Explicitly seeded endpoints retain direct bridge semantics.

Unindexed, stale, absent, incompatible, or invalid results produce structured diagnostics. A failed graph call returns no graph candidate and never silently switches to `legacy`; other enabled providers continue. Rejected paths from an otherwise successful graph request remain visible as `LOCI_GRAPH_PATH_REJECTED`, but that normal negative evidence does not mark the provider degraded. loci ranking is a retrieval signal, not authority, source support, knowledge state, or final answerability.
