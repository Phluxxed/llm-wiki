"""Compatibility CLI for thin wiki-local query adapters."""

from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
from typing import Any

from .config import ContentConfig, inspect_wiki_config
from .legacy import LegacyRuntime


def main(argv: list[str] | None = None, *, wiki_root: str | Path) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    root = Path(wiki_root).expanduser().resolve()
    inspection = inspect_wiki_config(root)
    if inspection.config is not None:
        content = inspection.config.content
    elif inspection.status == "legacy_missing":
        content = ContentConfig()
    else:
        assert inspection.error is not None
        parser.exit(2, f"{inspection.error.code}: {inspection.error.message}\n")
    runtime = LegacyRuntime(root, content=content)

    if args.agent_overview:
        payload = runtime.overview()
    elif args.graph_health:
        payload = runtime.health()
    elif args.links:
        payload = runtime.links(_resolve_page(args.links, runtime, parser))
    elif args.backlinks:
        payload = runtime.backlinks(_resolve_page(args.backlinks, runtime, parser))
    elif args.around:
        payload = runtime.around(
            _resolve_page(args.around, runtime, parser),
            depth=max(1, args.depth),
        )
    elif args.context_pack:
        payload = runtime.context_pack(
            _resolve_page(args.context_pack, runtime, parser),
            tokens=args.tokens,
        )
    else:
        payload = runtime.query(
            status=args.status,
            category=args.category,
            page_type=args.type,
            tag=args.tag,
            stale=args.stale,
            risks=args.risks,
        )

    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_markdown(payload), end="")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query wiki YAML frontmatter")
    parser.add_argument("--status", help="Filter by status")
    parser.add_argument("--category", help="Filter by category")
    parser.add_argument("--type", help="Filter by page type")
    parser.add_argument("--tag", help="Filter by tag")
    parser.add_argument("--stale", type=int, metavar="DAYS", help="Pages not reviewed in N+ days")
    parser.add_argument("--risks", action="store_true", help="Aggregate open risks")
    parser.add_argument("--agent-overview", action="store_true", help="Show agent-oriented wiki overview")
    parser.add_argument("--links", metavar="PAGE", help="List outgoing graph links")
    parser.add_argument("--backlinks", metavar="PAGE", help="List incoming graph links")
    parser.add_argument("--around", metavar="PAGE", help="List graph neighborhood")
    parser.add_argument("--depth", type=int, default=1, help="Traversal depth for --around")
    parser.add_argument("--graph-health", action="store_true", help="Report graph health")
    parser.add_argument("--context-pack", metavar="PAGE", help="Build deterministic agent context")
    parser.add_argument("--tokens", type=int, default=12_000, help="Approximate context token budget")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit machine-readable JSON")
    return parser


def _resolve_page(raw: str, runtime: LegacyRuntime, parser: argparse.ArgumentParser) -> str:
    normalized = raw.replace("\\", "/")
    normalized = normalized[2:] if normalized.startswith("./") else normalized
    if normalized in runtime.pages:
        return normalized
    matches = difflib.get_close_matches(normalized, sorted(runtime.pages), n=5)
    suggestion = f" Did you mean: {', '.join(matches)}?" if matches else ""
    parser.error(f"unknown page: {raw}.{suggestion}")
    raise AssertionError("argparse.error did not exit")


def _markdown(payload: dict[str, Any]) -> str:
    kind = payload["kind"]
    if kind == "summary":
        rows = [
            [item["page"], item["title"], item["category"], item["status"], item["last_reviewed"]]
            for item in payload["pages"]
        ]
        if not rows:
            return "No pages match.\n"
        return _table(["File", "Title", "Category", "Status", "Last Reviewed"], rows) + f"\n\n{payload['count']} page(s)\n"
    if kind == "risks":
        rows = [
            [item["file"], item["risk"], item["likelihood"], item["impact"], item["status"]]
            for item in payload["risks"]
        ]
        return ("No open risks or attention items found.\n" if not rows else _table(["File", "Risk", "Likelihood", "Impact", "Status"], rows) + f"\n\n{payload['count']} open risk row(s)\n")
    if kind in {"links", "backlinks"}:
        field = kind
        rows = [
            [item["page"], item["title"], item["edge_type"], f"{item['weight']:.2f}"]
            for item in payload[field]
        ]
        title = "Links" if kind == "links" else "Backlinks"
        return f"# {title}: {payload['page']}\n" + (_table(["Page", "Title", "Type", "Weight"], rows) + "\n" if rows else f"No {kind}.\n")
    if kind == "around":
        rows = [
            [item["page"], item["title"], str(item["distance"]), ", ".join(item["reasons"]), f"{item['score']:.2f}"]
            for item in payload["pages"]
        ]
        return f"# Around: {payload['seed']['page']}\n" + (_table(["Page", "Title", "Distance", "Reasons", "Score"], rows) + "\n" if rows else "No connected pages found.\n")
    if kind == "agent_overview":
        lines = ["# Agent Overview", f"Pages: {payload['page_count']} | Edges: {payload['edge_count']}", "", "## Suggested entry pages"]
        lines.extend(f"- {item['page']} — {item['title']}" for item in payload["suggested_entry_pages"])
        lines.append("\n## Orphans")
        lines.extend(f"- {item['page']}" for item in payload["orphans"])
        lines.append("\n## Open questions")
        lines.extend(f"- {item['page']}: {item['question']}" for item in payload["open_questions"])
        lines.append("\n## Open risks")
        lines.extend(f"- {item['page']}: {item['risk']}" for item in payload["open_risks"])
        lines.append("\n## Recent log")
        lines.extend(f"- {item['date']} {item['detail']}" for item in payload["recent_log"])
        return "\n".join(lines) + "\n"
    if kind == "graph_health":
        lines = ["# Graph Health", f"Pages: {payload['page_count']} | Edges: {payload['edge_count']}", "", "## Hubs"]
        lines.extend(f"- {item['page']} (degree {item['degree']})" for item in payload["hubs"])
        lines.append("\n## Orphans")
        lines.extend(f"- {item['page']}" for item in payload["orphans"])
        lines.append("\n## Pages without source")
        lines.extend(f"- {item['page']}" for item in payload["pages_without_source"])
        return "\n".join(lines) + "\n"
    if kind == "context_pack":
        lines = [f"# Context Pack: {payload['seed']['page']}", "", payload["seed"]["content"]]
        for item in payload["included_pages"]:
            lines.extend(["", f"## {item['page']}", f"Included because: {', '.join(item['reasons'])}", item["content"]])
        if payload["source_refs"]:
            lines.extend(["", "## Source references"])
            lines.extend(f"- {item['page']}: {item['source']}" for item in payload["source_refs"])
        if payload["source_excerpts"]:
            lines.extend(["", "## Source excerpts"])
            for item in payload["source_excerpts"]:
                lines.extend([f"### {item['source']}", item["content"]])
        if payload["open_questions"]:
            lines.extend(["", "## Open questions"])
            lines.extend(f"- {item['page']}: {item['question']}" for item in payload["open_questions"])
        if payload["open_risks"]:
            lines.extend(["", "## Open risks"])
            lines.extend(f"- {item['page']}: {item['risk']}" for item in payload["open_risks"])
        if payload["recent_log"]:
            lines.extend(["", "## Recent log"])
            lines.extend(f"- {item['date']} {item['detail']}" for item in payload["recent_log"])
        if payload["gaps"]:
            lines.extend(["", "## Gaps"])
            lines.extend(f"- {item['page']}: {item['gap']}" for item in payload["gaps"])
        return "\n".join(lines) + "\n"
    raise ValueError(f"Unsupported legacy payload kind: {kind}")


def _table(headers: list[str], rows: list[list[str]]) -> str:
    normalized = [[str(value) for value in row] for row in rows]
    widths = [max(len(headers[index]), *(len(row[index]) for row in normalized)) for index in range(len(headers))]
    render = lambda row: "| " + " | ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)) + " |"
    separator = "| " + " | ".join("-" * width for width in widths) + " |"
    return "\n".join([render(headers), separator, *(render(row) for row in normalized)])
