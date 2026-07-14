# llm-wiki

A skill that scaffolds a persistent, LLM-maintained wiki — inspired by Karpathy's personal wiki pattern.

Drop it into your agent's skills directory, then run `/wikime` in any project to get a fully operational wiki scaffold in seconds.

## What it sets up

```
your-wiki/
├── .llm-wiki.toml       ← versioned shared-runtime and compiler policy
├── wiki-agent.md          ← agent operating manual (all instructions live here)
├── CLAUDE.md              ← @wiki-agent.md (or natural-language pointer for other agents)
├── CONVENTIONS.md         ← human-readable naming and structure reference
├── README.md              ← quick start and scripts reference
├── index.md               ← one-liner catalog of all wiki pages
├── log.md                 ← append-only change history
├── {page-type}/           ← primary wiki pages (e.g. papers/, use-cases/, experiments/)
│   └── *.md
├── entities/              ← entity and concept pages
│   └── *.md
├── _templates/
│   ├── {page-type}.md     ← purpose-aware template for that page type
│   └── entity.md          ← template for entity/concept pages
├── sources/               ← immutable raw inputs (never edited after saving)
└── scripts/
    ├── wiki_graph.py     ← thin adapter to the canonical graph runtime
    ├── render.py          ← generates wiki.html — single-file reader with nine views
    ├── lint.py            ← structural health check
    ├── query.py           ← thin legacy-CLI adapter to the canonical runtime
    └── eval.py            ← risk-triggered LLM-as-judge quality audit
```

## Installation

```bash
# Clone the repo
git clone https://github.com/Phluxxed/llm-wiki ~/llm-wiki

# Symlink into your agent's skills directory (recommended — keeps it version-controlled)
# Claude Code example:
ln -s ~/llm-wiki ~/.claude/skills/wikime
```

Each agent has its own skills directory — your agent will know where to look.

To expose registered wikis over MCP, install the local package and configure an
agent-scoped registry home:

```bash
cd ~/llm-wiki
uv venv
uv pip install -e .

# Codex personal registry
codex mcp add --env LLM_WIKI_HOME="$HOME/.codex/llm-wiki" llm-wiki -- llm-wiki-mcp

# Claude work registry; do not share this home with Codex
claude mcp add llm-wiki -s local -e LLM_WIKI_HOME="$HOME/.claude/llm-wiki" -- llm-wiki-mcp
```

The MCP server fails closed when `LLM_WIKI_HOME` is absent. Codex and Claude
should use separate registry homes so personal and work wikis do not cross over.

Generated wikis should install the canonical package into a project-local `.venv` (resolve the path to the current release checkout; do not write a user-specific path into wiki files):

```bash
uv venv
uv pip install /path/to/llm-wiki
```

Run wiki scripts with `.venv/bin/python3`. `scripts/eval.py`'s **claude** judge additionally needs `claude-agent-sdk` (`uv pip install claude-agent-sdk`); skip it if you run the eval with `--judge codex` or `--judge none`.

## Usage

In any directory (empty or existing project), run:

```
/wikime
```

The skill asks two questions — what the wiki is for, and what the primary page type or types are — then scaffolds everything in one pass.

**Supported page types:** use cases, papers, experiments, runbooks, ADRs, agent Brain pages, or anything else you name. The agent generates templates from the wiki purpose; the template h2 sections become the lint-enforced shape for pages of that type.

**Safe on existing projects:** if a `CLAUDE.md`, `AGENTS.md`, or `GEMINI.md` already exists, the skill appends a single pointer line rather than overwriting it. If `wiki-agent.md` already exists, the skill stops — you already have a wiki.

## Scripts

| Script | Command | Output |
| --- | --- | --- |
| `scripts/lint.py` | `.venv/bin/python3 scripts/lint.py` | Structural health check — template-required sections, broken refs, open risks, index consistency |
| `scripts/query.py` | `.venv/bin/python3 scripts/query.py --help` | Frontmatter queries plus agent graph commands: `--agent-overview`, `--links`, `--backlinks`, `--around`, `--graph-health`, `--context-pack`; add `--json` for machine-readable output |
| `scripts/render.py` | `.venv/bin/python3 scripts/render.py` | Generates `wiki.html` — single-file reader (Home, Page, Search, Graph, Risks, Recent changes, Open questions, Entities, Sources). Open in a browser or view as a Claude artifact |
| `scripts/eval.py` | `.venv/bin/python3 scripts/eval.py --gate` | Risk-triggered LLM-as-judge quality audit — grounding against source evidence, typed cross-page contradictions, redundancy, and boolean near-duplicate disambiguation; per-metric thresholds + regression gating (exit code). Auto-detects your agent CLI (`claude`/`codex`) as a keyless judge; run records in `.eval/` |

Run `lint.py` and `render.py` for routine wiki maintenance. Reserve `eval.py --gate` for high-risk changes such as self-model or operating-rule changes, ownership-boundary changes, major source ingests, rebuilds, suspected contradictions, page merge/split decisions, weak grounding concerns, near-duplicate concept cleanup, or eval tooling changes.

Grounding requires judge-readable evidence. A full text/markdown source works directly. A page whose `source:` is a URL/identity manifest must set `source_mode: manifest` and point `evidence:` at one or more immutable evidence packs in `sources/`; binary sources such as PDFs also require text evidence. Eval fails before spending judge calls when required evidence is absent, the primary source is missing, or the combined evidence exceeds the 48,000-character budget; oversized extractions must be curated into bounded, claim-complete packs instead of being silently truncated. Documented source drift remains visible in contradiction results without failing the gate; unresolved contradictions still fail. Disambiguation treats the judge's `distinct` boolean as authoritative.

For agents landing cold in a wiki, start with:

```bash
.venv/bin/python3 scripts/query.py --agent-overview --json
.venv/bin/python3 scripts/query.py --context-pack <page> --tokens 12000 --json
```

For question-shaped retrieval with progressive context expansion:

```bash
llm-wiki compile-context --wiki . --alias my-wiki \
  --question "What currently owns traversal and why?" \
  --seed entities/llm-wiki.md
```

The target size is an efficiency goal, not a recall ceiling. The compiler expands selected evidence while required roles remain uncovered, up to the explicit hard maximum, and reports provenance, authored state, omissions, exact byte use, uncovered roles, and its stop reason. See [Context Compiler and migration](docs/context-compiler.md).

Managed wikis use the local `loci-mcp` stdio service for default section navigation and bounded graph retrieval. Graph profile and contribution data live in an external cache mirror; compiling never writes `.loci` data into the wiki. Inferred relationship paths count as bridge evidence only when they cross the question's distinct subject anchors, so a nearby path cannot manufacture sufficiency. If loci is unavailable or unindexed, the response reports that degradation and never silently switches graph retrieval to the legacy backend. Set `compiler.graph_backend = "legacy"` only for explicit rollback. See [loci providers](docs/loci-provider.md).

## MCP tools

The MCP server is a context adapter over existing wiki folders. It does not
author pages, mutate sources, or replace `/wikime`.

| Tool | Purpose |
| --- | --- |
| `wiki_list` | List wikis registered in the current agent's registry. |
| `wiki_register` / `wiki_unregister` | Attach or detach an alias in the current agent registry only. |
| `wiki_doctor` | Report schema/runtime compatibility, adapter drift, provider readiness, and migration receipt state. |
| `wiki_agent_manual` | Load `wiki-agent.md` and the selected wiki's operating contract before file mutation. |
| `wiki_overview` / `wiki_graph_health` | Return first-pass structure, hubs, orphans, risks, questions, and graph health. |
| `wiki_query` | Query page frontmatter by status, category, type, tag, stale days, or open risks. |
| `wiki_links` / `wiki_backlinks` / `wiki_around` | Navigate the deterministic wiki graph. |
| `wiki_context_pack` | Return bounded task context around a seed page. |
| `wiki_compile_context` | Compile question-shaped evidence with progressive targets, hard limits, provenance, state, coverage, and stop semantics. |
| `wiki_maintenance_candidates` | Return evidence-backed review candidates and explicit unknowns; never mutate wiki content. |
| `wiki_build_maintenance_candidate` | Canonicalize one evidence-backed task observation for later steward review; never mutate wiki content. |
| `wiki_get_page` / `wiki_get_source_excerpt` | Read bounded page content and source excerpts. |

Existing wikis migrate only through the explicit receipt-backed flow:

```bash
llm-wiki doctor --wiki .
llm-wiki migrate dry-run --wiki .
llm-wiki migrate apply --wiki . --plan-hash <exact-dry-run-hash>
llm-wiki migrate verify --wiki .
llm-wiki migrate rollback --wiki . --receipt-id <receipt-id>
```

`wiki_context_pack` and the local query CLI remain compatibility surfaces. They have no calendar removal date; removal requires the evidence gates documented in [the compatibility policy](docs/context-compiler.md#legacy-compatibility-policy).

## Agent compatibility

Works with any agent that supports skills. The running agent creates its own schema file — a one-line pointer to `wiki-agent.md` where all instructions live.

| Agent | Schema file | Pointer format |
| --- | --- | --- |
| Claude Code | `CLAUDE.md` | `@wiki-agent.md` (native include) |
| Codex | `AGENTS.md` | Natural-language pointer |
| Gemini CLI | `GEMINI.md` | Natural-language pointer |
