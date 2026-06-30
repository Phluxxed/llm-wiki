# Plan: Purpose-Aware Wikime Templates

## Status

Draft handoff created 2026-06-30. Simplified after review.

## The Ask

`/wikime` should generate templates that match what the wiki is being stood up for.

The setup is already agentic: the agent asks what the wiki is for and what page types it needs. The missing piece is that the scaffold still falls back to one generic page shape:

```md
## What This Is
## How It Works
## Risk Register
## Prerequisites
```

That is the wrong default. If the wiki is a Brain, research notebook, policy/control wiki, product spec wiki, or runbook wiki, the generated templates should look like that kind of wiki.

## Principle

The generated templates are the contract.

The user should not have to fill out a schema. Anvil should not supply a setup brief. `/wikime` should use the natural setup answers and create useful `_templates/{type}.md` files directly.

Because lint needs something deterministic to check, it should read the generated templates and enforce their required `h2` sections for pages of that type.

## Keep

- Markdown-first wiki folders.
- OKF frontmatter requirements.
- `wiki-agent.md`, `index.md`, `log.md`, `entities/`, `sources/`, `_templates/`, and scripts.
- Entity/concept page rules.
- Meta pages as free-form.
- Existing Risk Register table parsing for formal/detailed pages.

## Change

### 1. Template generation

Update `SKILL.md` so the agent creates one primary template per page type from the wiki purpose and page-type answer.

The template should include:

- required OKF frontmatter;
- page-type-specific `h2` sections;
- short prompts/placeholders under those sections;
- source/evidence guidance where useful;
- lightweight attention markers where useful.

Example: a Brain wiki might generate templates such as `project-orientation`, `work-history`, `correction`, or `pattern`, with sections that fit operational memory rather than generic policy/article language.

### 2. Lint section checks

Update `scripts/lint.py` so primary page section checks come from the matching template:

- page has `type: work-history` -> read `_templates/work-history.md`;
- extract required `h2` sections from that template;
- require pages of that type to include those sections.

If the matching template is missing, fall back to the legacy four-section profile. That fallback is only for older/config-less wikis, not the default for new scaffolds.

Entity/concept and meta routing stays as it is:

- `type: entity` / `type: concept` -> entity sections;
- `type: meta` or meta category -> no section enforcement;
- other types -> template-derived sections.

### 3. Query type filtering

Fix `query.py --type` so it honors arbitrary primary types.

Today it collapses most custom types to `use-case`. That will be wrong once generated wikis have page types like `project`, `work-history`, `policy`, `control`, `paper`, or `pattern`.

### 4. Attention items

Do not force every page to have a Risk Register table.

Keep Risk Register tables for pages where likelihood/impact/mitigation/status are useful. For ordinary operational warnings, use lightweight one-line blockquotes:

```md
> **Risk:** This may break if the MCP registry changes.
> **Caveat:** This only applies to local stdio MCP.
> **Failure mode:** Agents skip the contract loader and edit from stale assumptions.
```

Render/query should surface these attention items alongside existing risk-register rows.

### 5. Docs and verification

Update README/conventions so future agents understand:

- templates are purpose-aware;
- template `h2` sections are lint-enforced for that page type;
- legacy fallback exists only when a matching template is missing;
- attention items are the lightweight warning format.

Verification should use the project-local environment:

```bash
uv venv
uv pip install -e . pytest
.venv/bin/python3 -m pytest tests/ -v
```

Then manually scaffold two throwaway wikis from natural answers:

- one Brain-style wiki;
- one non-Brain wiki, such as research or policy/control.

Both should lint, render, and produce a useful agent overview.

## Acceptance

- `/wikime` no longer instructs agents to force every primary page into the universal four-section shape.
- Fresh generated wikis have purpose-specific templates.
- Lint validates pages against their matching template sections.
- Older wikis without matching templates still lint with the legacy fallback.
- `query.py --type <custom-type>` works.
- Attention items surface without requiring Risk Register tables.
- Brain is supported as a first-class use case without hard-coding an Anvil-specific template pack.
