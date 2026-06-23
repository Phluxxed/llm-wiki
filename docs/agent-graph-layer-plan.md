# Agent Graph Layer Plan

## Problem Statement

How might we let an agent dropped into any `llm-wiki` repo recover the wiki's important pages, relationships, source context, risks, and gaps without changing the wiki into a heavyweight knowledge-base system?

The goal is not to make `llm-wiki` more impressive for humans. It is already a useful markdown wiki for humans. The missing value is agent use: an agent should be able to point at a repo and ask "what do I need to know before working on this?" without manually rereading hundreds of markdown files or guessing which links matter.

## Target User

The primary user is an agent operating inside an existing `llm-wiki` repo.

The human user benefits indirectly: they get better outcomes because the agent has a deterministic way to pull connected context, inspect graph health, and understand why a page was included in context.

## Success Criteria

- An agent can run one or two documented commands and understand the wiki's structure before editing or answering.
- Given a target page, an agent can retrieve backlinks, outlinks, nearby pages, source references, risks, open questions, and recent log context.
- Every included context item explains why it was included.
- The generated wiki remains plain markdown and portable files.
- The v1 graph layer requires no new runtime service, vector DB, daemon, MCP server, or API key.
- Any dependency added later has a clear capability it earns.

## Recommended Direction

Build a boring agent-facing adapter layer over the current wiki files.

The layer should use the metadata and links already emitted by `llm-wiki`: markdown links, `mentioned_in`, page type/category, tags, sources, risks, open questions, and logs. It should expose traversal and context commands through the existing script surface, likely `scripts/query.py` plus a small shared graph helper.

The core primitive is:

> Search or user intent finds seed pages. Graph traversal expands related context. Context packs explain inclusion reasons.

This keeps `llm-wiki` itself simple while giving agents the missing retrieval substrate.

## Architecture Decisions

### Keep The Wiki Format Boring

Generated output stays plain markdown. No hidden database is required to understand or repair the wiki. Tooling may compute indexes, but the canonical artifact remains files.

### Add An Adapter, Not A New System

The graph layer should sit beside current scripts. It should not rewrite page structure, require a new authoring workflow, or make existing generated wikis incompatible.

### Prefer Deterministic Graph Signals First

Use explainable signals before embeddings:

- direct body links
- `mentioned_in`
- backlinks
- shared source references
- shared tags
- common neighbors
- compatible page type/category

Semantic/vector search can be a later optional feature if the graph layer proves insufficient.

### Make Agent Context Inspectable

Context packs should be deterministic markdown or JSON with visible inclusion reasons. Avoid invisible ranking soup.

## Proposed Agent Commands

These commands are the practical contract an agent can learn:

```bash
.venv/bin/python3 scripts/query.py --agent-overview
.venv/bin/python3 scripts/query.py --links <page> --json
.venv/bin/python3 scripts/query.py --backlinks <page> --json
.venv/bin/python3 scripts/query.py --around <page> --depth 2 --json
.venv/bin/python3 scripts/query.py --context-pack <page> --tokens 12000 --json
.venv/bin/python3 scripts/query.py --graph-health
```

`--agent-overview` is important. It gives an agent a first move when it has no seed page yet: wiki stats, key hubs, orphan areas, recent log entries, unresolved risks, and suggested starting points. All agent graph commands support `--json` for structured agent consumption while keeping markdown as the pasteable default.

Future optional command:

```bash
.venv/bin/python3 scripts/lint.py --suggest-links
```

## Context Pack Shape

A context pack should be optimized for agent use, not for human reading:

```markdown
# Context Pack: entities/openai.md

## How To Use This Pack
- Treat source-linked pages and raw source excerpts as stronger evidence than summaries.
- Treat risks and open questions as unresolved, not factual.
- Use inclusion reasons to decide what to inspect next.

## Seed
- entities/openai.md

## Included Pages
| Page | Reason | Score |
|---|---:|---:|
| projects/example.md | backlink | 1.00 |
| sources/example-transcript.md | source | 0.95 |
| concepts/agentic-wikis.md | shared_tag:agents | 0.55 |

## Seed Page
[full content or bounded excerpt]

## Nearby Pages
[bounded excerpts grouped by reason]

## Risks And Open Questions
[items from included pages]

## Recent Log Context
[recent relevant log entries]

## Gaps
[dangling links, orphan related pages, missing source references]
```

The agent should never have to infer why a page is in the pack.

## Implementation Plan

The MVP is Phases 1-4. Phases 1-3 give an agent a map of the wiki; Phase 4 turns that map into usable working context. Phase 5 and later are optional improvements.

### Phase 1: Shared Graph Substrate

**Description:** Create a small internal graph helper that loads pages, resolves links, and exposes typed edges without changing the wiki format.

**Acceptance criteria:**
- Existing body links and `mentioned_in` links are represented as edges.
- Existing render behavior can still derive simple `(source, target)` pairs.
- Page metadata is available for scoring: type/category, tags, sources, status, risks, open questions.

**Verification:**
- `.venv/bin/python3 scripts/lint.py`
- `.venv/bin/python3 scripts/render.py`
- Focused tests or fixture checks for edge extraction and link resolution.

**Files likely touched:**
- `scripts/query.py`
- `scripts/render.py`
- `scripts/lint.py`
- new helper such as `scripts/wiki_graph.py`

### Phase 2: Basic Traversal Commands

**Description:** Add direct agent traversal verbs over the graph.

**Acceptance criteria:**
- `--links <page>` lists outgoing links.
- `--backlinks <page>` lists incoming links.
- `--around <page> --depth N` returns a bounded neighborhood with distance and edge reasons.
- Commands fail loudly on unknown pages and suggest close matches where practical.

**Verification:**
- CLI checks against a small fixture wiki.
- Existing query filters still work.

**Files likely touched:**
- `scripts/query.py`
- graph fixture tests if test structure exists

### Phase 3: Agent Overview And Graph Health

**Description:** Give an agent a repo-level first move and expose structural weaknesses.

**Acceptance criteria:**
- `--agent-overview` reports page counts, page types, top hubs, recent log entries, unresolved risks/questions, and suggested entry pages.
- `--graph-health` reports orphans, dangling links already known to lint, hubs, isolated clusters, and pages lacking source linkage.
- Output is useful in plain text, with JSON available if the existing query style supports it cleanly.

**Verification:**
- Run commands on this repo's own generated wiki scaffold or a fixture.
- Confirm no health warning is silently swallowed.

**Files likely touched:**
- `scripts/query.py`
- `scripts/lint.py` only if health warnings should become lint warnings

### Phase 4: Context Pack

**Description:** Build the core agent primitive: deterministic, token-budgeted context around a seed page.

**Acceptance criteria:**
- `--context-pack <page>` includes the seed page, backlinks, outlinks, high-scoring nearby pages, source references, risks, open questions, and recent log context.
- Every included item has an inclusion reason.
- Token or character budgeting is deterministic and visible.
- The output can be pasted directly into an agent context window.

**Verification:**
- Generate a pack for a known fixture page and snapshot the inclusion reasons.
- Manually inspect one real pack for usefulness and over-inclusion.

**Files likely touched:**
- `scripts/query.py`
- `scripts/wiki_graph.py`
- tests/fixtures if present or created

### Phase 5: Advisory Lint Link Suggestions

**Description:** Suggest missing links as advisory graph hygiene without editing files automatically.

**Acceptance criteria:**
- `scripts/lint.py --suggest-links` reports candidate links using shared sources, repeated names, shared tags, and common neighbors.
- Suggestions include evidence.
- The command is report-only and does not count as a hard lint failure by default.

**Verification:**
- Fixture with known missing links produces expected suggestions.
- No command mutates wiki pages.

**Files likely touched:**
- `scripts/lint.py`
- `scripts/wiki_graph.py`

### Phase 6: Optional Stronger Graph Metrics

**Description:** Add `networkx` only if bridge/community metrics become worth it.

**Acceptance criteria:**
- Dependency is isolated to tooling.
- The wiki artifact remains plain markdown.
- Bridges and communities are materially better than degree-based hubs/orphans.

**Verification:**
- Compare output against dependency-free graph health on a large wiki.
- Document why the dependency earned its place before adding it.

## Not Doing In V1

- No vector database.
- No embeddings.
- No daemon or watcher.
- No MCP server.
- No direct dependency on Keppi, MemoryOS, or other reviewed repos.
- No Obsidian-style wikilinks.
- No automatic page mutation from link suggestions.
- No changes to the generated wiki format unless a backward-compatible metadata addition is clearly required.

## Risks And Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Context packs become too noisy | High | Require inclusion reasons, scoring, and budgets from the first version. |
| Graph commands duplicate logic from render/lint | Medium | Extract shared helper once, then adapt scripts to it incrementally. |
| Link suggestions become hallucination bait | Medium | Keep them evidence-only and report-only. |
| Agent overview becomes a vague dashboard | Medium | Optimize it for "what should the agent inspect first?" not general analytics. |
| Dependency creep | Medium | No dependency in v1; later dependencies need a documented capability win. |

## Open Questions

- What evidence threshold should advisory link suggestions meet before `scripts/lint.py --suggest-links` is implemented?
- Should `networkx` be added later for bridge/community metrics on large wikis?

## Suggested First Slice

The smallest useful engineering checkpoint is:

```bash
.venv/bin/python3 scripts/query.py --agent-overview
.venv/bin/python3 scripts/query.py --links <page>
.venv/bin/python3 scripts/query.py --backlinks <page>
.venv/bin/python3 scripts/query.py --around <page> --depth 1
```

This gives agents the missing first moves without yet solving context budgeting or link suggestion. It should not be treated as "done"; the v1 agent-value milestone is reached when `--context-pack` exists and is useful on a real wiki.
