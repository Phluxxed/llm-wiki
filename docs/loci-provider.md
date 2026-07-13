# Core loci traversal provider

loci is the default section-navigation provider for the Context Compiler. It searches the indexed wiki for the question, hydrates only the matched file/section ranges, and converts them into ordinary compiler candidates with exact locators.

The runtime calls the installed local `loci-mcp` stdio service. One compile performs a bounded `loci_search` followed by one batched `loci_get`, with a 15-second gateway timeout. This keeps the integration on loci's production MCP surface and avoids coupling the `llm-wiki` virtual environment to loci's parser dependencies. Fresh scaffolds and migrations include `"loci"` in `[compiler].providers`; removing it is an explicit opt-out. `LLM_WIKI_LOCI_MCP_COMMAND` may name an alternative executable, but command arguments and machine paths do not belong in wiki config.

The provider validates that every returned path stays inside the wiki before hydration. Unindexed, stale, absent, incompatible, or invalid results produce structured diagnostics; seed/frontmatter/text/graph/source retrieval continues deterministically. loci ranking is a retrieval signal, not authority or source support.
