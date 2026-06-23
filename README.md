# llm-wiki

A skill that scaffolds a persistent, LLM-maintained wiki — inspired by Karpathy's personal wiki pattern.

Drop it into your agent's skills directory, then run `/wikime` in any project to get a fully operational wiki scaffold in seconds.

## What it sets up

```
your-wiki/
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
│   ├── {page-type}.md     ← template for new pages
│   └── entity.md          ← template for entity/concept pages
├── sources/               ← immutable raw inputs (never edited after saving)
└── scripts/
    ├── wiki_graph.py     ← shared graph substrate for traversal, health, and context packs
    ├── render.py          ← generates wiki.html — single-file reader with eight views
    ├── lint.py            ← structural health check
    ├── query.py           ← frontmatter queries + agent graph/context commands
    └── eval.py            ← LLM-as-judge quality eval (grounding, contradictions, redundancy) with regression gating
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

Generated wikis should install script dependencies into a project-local `.venv`:

```bash
uv venv
uv pip install pyyaml markdown
```

Run wiki scripts with `.venv/bin/python3`. `scripts/eval.py`'s **claude** judge additionally needs `claude-agent-sdk` (`uv pip install claude-agent-sdk`); skip it if you run the eval with `--judge codex` or `--judge none`.

## Usage

In any directory (empty or existing project), run:

```
/wikime
```

The skill asks two questions — what the wiki is for, and what the primary page type is — then scaffolds everything in one pass.

**Supported page types:** use cases, papers, experiments, runbooks, ADRs, or anything else you name.

**Safe on existing projects:** if a `CLAUDE.md`, `AGENTS.md`, or `GEMINI.md` already exists, the skill appends a single pointer line rather than overwriting it. If `wiki-agent.md` already exists, the skill stops — you already have a wiki.

## Scripts

| Script | Command | Output |
| --- | --- | --- |
| `scripts/lint.py` | `.venv/bin/python3 scripts/lint.py` | Structural health check — missing sections, broken refs, open risks, index consistency |
| `scripts/query.py` | `.venv/bin/python3 scripts/query.py --help` | Frontmatter queries plus agent graph commands: `--agent-overview`, `--links`, `--backlinks`, `--around`, `--graph-health`, `--context-pack`; add `--json` for machine-readable output |
| `scripts/render.py` | `.venv/bin/python3 scripts/render.py` | Generates `wiki.html` — single-file reader (Home, Page, Search, Graph, Risks, Recent changes, Open questions, Entities). Open in a browser or view as a Claude artifact |
| `scripts/eval.py` | `.venv/bin/python3 scripts/eval.py --gate` | LLM-as-judge quality eval — grounding, cross-page contradictions, redundancy, near-duplicate disambiguation; per-metric thresholds + regression gating (exit code). Auto-detects your agent CLI (`claude`/`codex`) as a keyless judge; run records in `.eval/` |

For agents landing cold in a wiki, start with:

```bash
.venv/bin/python3 scripts/query.py --agent-overview --json
.venv/bin/python3 scripts/query.py --context-pack <page> --tokens 12000 --json
```

## Agent compatibility

Works with any agent that supports skills. The running agent creates its own schema file — a one-line pointer to `wiki-agent.md` where all instructions live.

| Agent | Schema file | Pointer format |
| --- | --- | --- |
| Claude Code | `CLAUDE.md` | `@wiki-agent.md` (native include) |
| Codex | `AGENTS.md` | Natural-language pointer |
| Gemini CLI | `GEMINI.md` | Natural-language pointer |
