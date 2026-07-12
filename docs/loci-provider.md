# Optional loci provider

loci is an optional section-navigation provider for the Context Compiler. It uses loci's supported read-only service boundary with freshness checks and converts current file/section results into ordinary compiler candidates with exact locators.

To enable it, install loci into the same Python environment as `llm-wiki` and add `"loci"` to `[compiler].providers` in `.llm-wiki.toml`. The base wheel deliberately has no loci dependency.

The provider validates that every returned path stays inside the wiki and that hydrated ranges resolve against current files. Unindexed, stale, absent, incompatible, or invalid results produce structured diagnostics; seed/frontmatter/text/graph/source retrieval continues deterministically. loci ranking is a retrieval signal, not authority or source support.
