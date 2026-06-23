# Keppi Steal List

High-level notes from reviewing [`jgoldfed/keppi`](https://github.com/jgoldfed/keppi) against this repo.

Keppi is a real, published tool (PyPI package, MIT, Python 3.10+, test suite + CI), and it shares this repo's lineage: it pitches itself as the missing **graph layer** in Karpathy's LLM Wiki pattern. So it's a sibling project, not a random dependency. The interesting parts are the graph-retrieval verbs and wiki-health concepts.

## How to think about dependencies here

The previous version of this doc had a blanket "no SQLite, NetworkX, embeddings, daemon, or MCP" rule. That rule was asserted, not argued, and it conflated two different things. Replacing it with two distinctions that actually hold:

**1. Artifact vs tooling.** The constraint that matters is on the *generated wiki* — the thing we hand to someone else. That must stay plain, portable markdown, usable with nothing but a text editor and these scripts. It says nothing about the *authoring/maintenance tooling*. `query.py` / `render.py` / `lint.py` can use a library to *compute* hubs and bridges while the wiki they emit stays plain files. The workshop can have power tools even if the furniture ships flat-pack.

The litmus test still applies, but only to the artifact:

> Can a fresh generated wiki still be understood, inspected, repaired, and versioned as plain files?

**2. A dependency is judged per-capability, not as a blob.** "Dependencies" is not one decision. Reading Keppi's implementations, the capabilities split into three tiers by what they actually cost:

| Capability | Real dependency cost | Verdict |
|---|---|---|
| backlinks, blast-radius, traverse, orphans, hubs-by-degree, context-pack | **None.** Keppi's `blast_radius.py` is ~115 lines of plain BFS that only touches `out_edges` / `in_edges` / `nodes`. A dict does the same. | Cheap — build native. |
| bridges (betweenness centrality), communities (Louvain), shortest-path | A real graph library earns its place. Hand-rolling Brandes' betweenness or Louvain is error-prone and worse to maintain than the dependency. `networkx` is pure-Python, no system libs. | Take the dep when you want these. |
| semantic search | `sqlite-vec` + `httpx` + a running embedding provider (Ollama/OpenAI). Model choice, an index to maintain, an external service at build time. | Real operational weight. Optional-and-late on merit. |

So the working rule is: **the generated wiki stays plain files; the tooling takes a dependency when the dependency earns it.** `networkx` earns it the moment you want bridges/communities/centrality done correctly. It does not earn it for backlinks/blast-radius. Embeddings carry real ops weight, so they stay opt-in.

## Why not just depend on Keppi directly

Don't. It's built for Obsidian and drags in a daemon (`watchdog`), an MCP server, and an embeddings stack this repo doesn't need. **Design donor, not dependency** — take the ideas (blast radius, typed/weighted edges, orphans/hubs/bridges, context pack) and build them native over `collect_edges()`. Its graph *analysis* is parser-agnostic and worth reading as a reference; nothing else is.

The shape to preserve in what we emit:

> A generated wiki should be useful with plain markdown, `.venv/bin/python3 scripts/lint.py`, `.venv/bin/python3 scripts/query.py`, and `.venv/bin/python3 scripts/render.py`.

## What this repo already has

Grounding the plan in current code so reuse claims are real, not aspirational:

- `render.py::collect_edges(pages) -> list[tuple[str, str]]` already produces `(source, target)` edge pairs from body markdown links + `mentioned_in` frontmatter.
- `render.py::resolve_link` / `lint.py::resolve_link` already resolve root-relative and source-relative markdown links to wiki-root keys.
- `wiki.html` already ships an interactive graph view (`HTML_SCRIPT_GRAPH`).

So the substrate for graph queries exists. What's missing is the traversal verbs on top of it and a richer edge model under it.

The link convention is fixed and singular: per `CONVENTIONS.md` and `SKILL.md`, every cross-page link is a wiki-root-relative markdown path — `[title](./entities/openai.md)` — in both body and `mentioned_in` frontmatter. No `[[wikilinks]]`. `collect_edges()` already reads exactly this, so the graph layer builds on what the scaffold emits with no parsing work.

**Scale check (why this is worth building at all).** A graph layer buys nothing on a fresh scaffold of a few pages. The value appears once a generated wiki grows to hundreds of interlinked pages — past the point where an agent can hold the link structure in its head, and "what does editing this page touch?" stops being answerable by re-reading. Build it for that state, not this one.

## Ideas worth stealing

### 1. Blast radius

Keppi's strongest idea: graph-aware neighbourhood lookup. Given a page, show the nearby connected context, ranked by relevance.

For `llm-wiki`:

```bash
.venv/bin/python3 scripts/query.py --links entities/openai.md
.venv/bin/python3 scripts/query.py --backlinks entities/openai.md
.venv/bin/python3 scripts/query.py --around entities/openai.md --depth 2
```

Why it matters: an agent updating a page should know what else may be touched. Search finds shared words; graph traversal finds authored relationships.

Implementation: feed `collect_edges()` output into a BFS with relevance decay (`relevance = parent_relevance × edge_weight`, sorted descending). Keppi's `blast_radius.py` is a clean reference — and vendoring it is viable (~115 lines, MIT, parser-agnostic). No new dependency.

### 2. Typed edges

Current edges are plain `(source, target)` tuples. Keppi adds a `type` and a `weight`. This is what makes blast-radius ranking meaningful — a `related_to` link should outweigh a shared tag.

Keppi's weights, as a reference point:

| Type | Weight |
|------|--------|
| `wikilink` (here: `body_link`) | 1.0 |
| `embed` | 1.5 |
| `related_to` | 2.0 |
| `tag_overlap` | 0–0.5 × Jaccard |
| `folder_proximity` | 0.3 (see "avoid", below) |

Suggested shape:

```python
{
    "source": "papers/foo.md",
    "target": "entities/openai.md",
    "type": "mentioned_in",
    "weight": 2.0,
}
```

Keep weights simple and explainable. Avoid invisible ranking soup.

### 3. Orphan, hub, and bridge pages

Keppi treats graph structure as wiki hygiene.

- **Orphans**: pages with no incoming or outgoing links (`render.py` / `lint.py` can already see this from edges).
- **Hubs**: pages with unusually many connections — degree count, no dependency needed.
- **Bridges**: pages connecting otherwise separate clusters — **betweenness centrality**, which is where `networkx` earns its place.

Why it matters: bridges reveal cross-cutting concepts that should become entity pages; orphans reveal pages the agent wrote but failed to connect; hubs reveal overloaded concepts that may need splitting.

### 4. Context pack

Generate a compact, token-budgeted bundle of connected context for an LLM.

```bash
.venv/bin/python3 scripts/query.py --context-pack entities/openai.md --tokens 12000
```

Output: the seed page, direct neighbours, high-signal backlinks, nearby open risks/questions, recent log entries touching those pages. Keppi's `context_pack.py` does greedy budget-fill over blast-radius output with a centrality bonus — a good, dependency-light reference.

Highest-value agent primitive after backlinks and blast radius. Must be deterministic, inspectable markdown — not a hidden embedding oracle.

### 5. Link suggestion, conservative

Suggest missing links when two pages share repeated names, tags, or source references.

```bash
.venv/bin/python3 scripts/lint.py --suggest-links
```

Report only. Do not auto-edit links.

## Ideas to handle with care

### Semantic search / embeddings

The one capability with real operational weight: `sqlite-vec` + `httpx` + a running provider (Ollama or OpenAI), plus an index to build and invalidate. Genuinely useful once links/tags/frontmatter stop carrying the load — and genuinely premature before that.

Keep it behind an optional command, decided on merit (the graph stopped being enough), not on dogma:

```bash
.venv/bin/python3 scripts/search.py --semantic "credential handling risks"
```

The base scaffold must stay usable with no API keys, no local models, no vector store, no extra services.

### Obsidian wikilinks

Keppi's Obsidian-style `[[wikilink]]` handling is not a good default here. Keep root-relative markdown links:

```markdown
[OpenAI](./entities/openai.md)
```

They work across GitHub, generated HTML, plain markdown, and multiple agents — and they're what `collect_edges()` already parses.

### Folder proximity

Keppi uses folder proximity as a weak edge signal. Here, folder names encode page *type*, not semantic closeness — `papers/foo.md` and `papers/bar.md` aren't related just by both being papers. Skip unless a generated wiki develops domain folders where locality is meaningful.

## Suggested implementation order

### Phase 1: CLI graph queries (no new dependency)

Add traversal verbs to `scripts/query.py` over `collect_edges()` output.

```bash
.venv/bin/python3 scripts/query.py --links <page>
.venv/bin/python3 scripts/query.py --backlinks <page>
.venv/bin/python3 scripts/query.py --around <page> --depth 2
.venv/bin/python3 scripts/query.py --graph-health
```

Build over `collect_edges()` directly — it already returns the `(source, target)` pairs from the wiki-root-relative markdown links the scaffold emits.

### Phase 2: typed edge model (no new dependency)

Refactor `collect_edges()` from tuple pairs to edge objects with `type` + `weight` (`body_link`, `mentioned_in`, `tag_overlap`). Keep a tuple-deriving shim so the existing `wiki.html` graph view keeps working.

### Phase 3: graph health in lint/render

- `lint.py`: structural warnings for orphans and isolated clusters.
- `wiki.html`: graph health panel or page badges on the existing graph view.

### Phase 4: bridges + communities (take `networkx`)

This is the deliberate dependency. Add `networkx` to the tooling (not the artifact) for betweenness-centrality bridges and Louvain communities. Build the `DiGraph` from `collect_edges()` — *not* from Keppi's builder, which can't read this repo's links.

### Phase 5: context pack

```bash
.venv/bin/python3 scripts/query.py --context-pack <page> --tokens 12000
```

Markdown output suitable for pasting into an agent context window.

### Phase 6: optional semantic search

Only once the file/link/frontmatter graph proves insufficient. Stays outside the base path.

## Design constraint

The wiki should feel like a clever notebook, not a tiny enterprise knowledge graph cosplaying as infrastructure. The constraint is on what we *emit*, not on what the tooling is allowed to use:

> Can a fresh generated wiki still be understood, inspected, repaired, and versioned as plain files?

If a feature breaks that for the generated artifact, it belongs behind an optional command or out of this repo. If it only adds a dependency to the *tooling* and the emitted wiki stays plain files, judge it on whether the dependency earns its place.
