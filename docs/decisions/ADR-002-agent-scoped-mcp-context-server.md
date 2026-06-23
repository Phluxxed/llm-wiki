# ADR-002: Serve llm-wiki Context Over Agent-Scoped MCP

## Status

Accepted

## Date

2026-06-23

## Context

`llm-wiki` wikis are plain markdown folders that agents already mutate reliably
by following `wiki-agent.md`. The missing production surface is not another
authoring API; it is a way for an agent to find and load the right wiki context
when it is not already inside that wiki directory.

Codex and Claude may represent different trust domains. In particular, a
personal Codex runtime must not see work wiki registrations created for Claude.

## Decision

Add a local stdio MCP server named `llm-wiki` with tools for registry,
navigation, graph health, page reads, source excerpts, and context packs.

The server is read/context only for wiki content. It may write the current
agent's registry file under `LLM_WIKI_HOME`, but it does not write wiki pages,
patch content, ingest sources, render artifacts, or append log entries.

Registries are explicitly agent-scoped. The server requires `LLM_WIKI_HOME` and
fails closed when it is absent.

## Consequences

- Existing wiki folders need no format migration to be served.
- `/wikime` remains the setup and migration workflow.
- Agents can attach aliases and retrieve context from anywhere.
- Work/personal separation depends on MCP client config using separate homes,
  such as `~/.codex/llm-wiki` and `~/.claude/llm-wiki`.
- Mutation stays in ordinary file edits governed by `wiki-agent.md`, avoiding a
  second write path that could drift from the wiki operating manual.
