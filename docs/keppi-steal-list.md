# Keppi Steal List

High-level notes from reviewing [`jgoldfed/keppi`](https://github.com/jgoldfed/keppi) against this repo.

Keppi is useful as a design donor, not as a dependency. `llm-wiki` should stay a lightweight markdown scaffold with boring scripts. Keppi's interesting parts are the graph retrieval verbs and wiki-health concepts, not the heavier local indexing stack.

## Verdict

Do not integrate Keppi directly.

Steal the graph vocabulary and a few traversal ideas. Keep the implementation native to this repo's existing scripts:

- markdown files remain the source of truth
- generated wikis stay portable
- no SQLite, NetworkX, embeddings, daemon, or MCP requirement in the base scaffold
- any advanced search layer should be optional and late

The shape to preserve:

> A generated wiki should be useful with plain markdown, `python3 scripts/lint.py`, `python3 scripts/query.py`, and `python3 scripts/render.py`.

## Ideas worth stealing

### 1. Blast radius

Keppi's strongest idea is graph-aware neighbourhood lookup: given a page or entity, show the nearby connected context.

For `llm-wiki`, this could become:

```bash
python3 scripts/query.py --links entities/openai.md
python3 scripts/query.py --backlinks entities/openai.md
python3 scripts/query.py --around entities/openai.md --depth 2
```

Why it matters: an agent updating a page should know what else may be touched. Search finds shared words. Graph traversal finds authored relationships.

Initial implementation can reuse `render.py`'s existing page collection and edge resolution logic.

### 2. Typed edges

Current graph edges are plain `(source, target)` pairs. Keppi's graph model suggests adding edge metadata.

Useful edge types:

- `body_link`: explicit markdown link in page body
- `mentioned_in`: entity/concept backlink
- `same_source`: two derived pages cite the same source file
- `same_tag`: pages share one or more tags
- `same_category`: pages share category, low confidence

Suggested shape:

```python
{
    "source": "papers/foo.md",
    "target": "entities/openai.md",
    "type": "mentioned_in",
    "weight": 2.0,
}
```

Keep the weights simple and explainable. Avoid invisible ranking soup.

### 3. Orphan, hub, and bridge pages

Keppi treats graph structure as wiki hygiene. That maps well to this repo.

Potential checks or views:

- **Orphans**: pages with no incoming or outgoing page links
- **Hubs**: pages/entities with unusually many connections
- **Bridges**: pages that connect otherwise separate clusters

Why it matters: bridges often reveal cross-cutting concepts that should become entity pages. Orphans reveal pages the agent wrote but failed to connect. Hubs reveal overloaded concepts that may need splitting.

This belongs in `query.py`, `lint.py`, or the rendered `wiki.html` graph view. Start with CLI output before adding UI.

### 4. Context pack

Keppi has the right instinct: generate a compact bundle of relevant connected context for an LLM.

Possible future command:

```bash
python3 scripts/context_pack.py entities/openai.md --tokens 12000
```

Possible output:

- the seed page
- direct neighbours
- high-signal backlinks
- open risks nearby
- open questions nearby
- recent log entries touching those pages

This is probably the highest-value agent primitive after backlinks and blast radius.

It should be deterministic, inspectable markdown. Do not make it a hidden embedding retrieval oracle.

### 5. Link suggestion, but conservative

Keppi can suggest links. This repo could eventually suggest missing entity/page links when two pages share repeated names, tags, or source references.

Safer framing:

```bash
python3 scripts/query.py --suggest-links
```

Output should be a report only. Do not auto-edit links.

## Ideas to avoid for now

### Embeddings

Embeddings are useful once a wiki is large enough that links, tags, and frontmatter stop carrying the load.

Do not put embeddings in the base scaffold. They add setup friction, model/provider choices, cache invalidation, and failure modes.

If added later, make semantic search optional:

```bash
python3 scripts/search.py --semantic "credential handling risks"
```

### SQLite and NetworkX

The generated wiki should not need a database or graph library to work.

A Python dict plus simple traversal is enough until the repo proves otherwise. If graph queries become materially more complex, reconsider then.

### Obsidian wikilinks

Keppi's Obsidian-style link handling is not a good default here.

`llm-wiki` should keep root-relative markdown links:

```markdown
[OpenAI](./entities/openai.md)
```

Those work better across GitHub, generated HTML, plain markdown, and multiple agents.

### Folder proximity

Keppi uses folder proximity as a weak signal. For this repo, folder names mostly encode page type, not semantic closeness.

`papers/foo.md` and `papers/bar.md` are not meaningfully related just because both are papers. Avoid this unless a generated wiki develops domain-specific folders where folder locality is meaningful.

## Suggested implementation order

### Phase 1: CLI graph queries

Add graph-aware queries to `scripts/query.py` using existing page and edge collection logic.

Commands:

```bash
python3 scripts/query.py --links <page>
python3 scripts/query.py --backlinks <page>
python3 scripts/query.py --around <page> --depth 2
python3 scripts/query.py --orphans
python3 scripts/query.py --hubs
```

No new dependencies.

### Phase 2: typed edge model

Refactor `render.py` edge collection from tuple pairs to edge objects.

Keep backwards compatibility where practical. The rendered graph can still consume source/target pairs derived from richer edge objects.

### Phase 3: graph health in lint/render

Expose graph hygiene in two places:

- `lint.py`: structural warnings for isolated pages and broken graph assumptions
- `wiki.html`: optional graph health panel or page badges

### Phase 4: context pack

Add a new script only after the graph query commands feel useful.

Candidate:

```bash
python3 scripts/context_pack.py <page> --depth 1 --tokens 12000
```

Output should be markdown suitable for pasting into an agent context window.

### Phase 5: optional semantic search

Only after the file/link/frontmatter graph feels insufficient.

Keep semantic search outside the base path. The base scaffold should remain usable without API keys, local models, vector stores, or extra services.

## Design constraint

The wiki should feel like a clever notebook, not a tiny enterprise knowledge graph cosplaying as infrastructure.

Every addition should pass this test:

> Can a fresh generated wiki still be understood, inspected, repaired, and versioned as plain files?

If not, the idea belongs behind an optional command or not in this repo at all.
