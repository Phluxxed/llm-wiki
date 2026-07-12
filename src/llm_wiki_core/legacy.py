from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import ContentConfig
from .documents import WikiPage, collect_pages, safe_source_path
from .graph import (
    ATTENTION_RE,
    Edge,
    collect_log,
    collect_typed_edges,
    extract_open_questions,
    extract_open_risks,
    graph_health,
    incoming_edges,
    neighborhood,
    outgoing_edges,
    related_pages,
)


OPEN_STATUSES = ("⚠️", "🔲")


class LegacyRuntime:
    def __init__(self, wiki_root: str | Path, *, content: ContentConfig | None = None):
        self.root = Path(wiki_root).expanduser().resolve()
        self.content = content or ContentConfig()
        self.pages = collect_pages(self.root, content=self.content)
        self.edges = collect_typed_edges(self.pages)

    def overview(self) -> dict[str, Any]:
        health = graph_health(self.pages, self.edges)
        return {
            "kind": "agent_overview",
            "page_count": health["page_count"],
            "edge_count": health["edge_count"],
            "type_counts": health["type_counts"],
            "suggested_entry_pages": [
                {
                    **self.page_record(hub["page"]),
                    "in": hub["in"],
                    "out": hub["out"],
                    "degree": hub["degree"],
                }
                for hub in health["hubs"][:10]
            ],
            "orphans": [self.page_record(path) for path in health["orphans"][:20]],
            "open_questions": self._open_question_records(),
            "open_risks": self._open_risk_records(),
            "recent_log": collect_log(self.root)[:5],
        }

    def query(
        self,
        *,
        status: str | None = None,
        category: str | None = None,
        page_type: str | None = None,
        tag: str | None = None,
        stale: int | None = None,
        risks: bool = False,
    ) -> dict[str, Any]:
        pages = list(self.pages.values())
        if status:
            pages = [page for page in pages if str(page.frontmatter.get("status") or "").lower() == status.lower()]
        if category:
            pages = [
                page
                for page in pages
                if category.lower() in str(page.frontmatter.get("category") or "").lower()
            ]
        if page_type:
            pages = [page for page in pages if page.type.lower() == page_type.lower()]
        if tag:
            pages = [page for page in pages if tag.lower() in {value.lower() for value in page.tags}]
        if stale is not None:
            pages = [page for page in pages if (_days_since(page.frontmatter.get("last_reviewed")) or 0) >= stale]
        if risks:
            rows = []
            for page in pages:
                rows.extend(_parse_risk_rows(page.text, page.path))
                rows.extend(_parse_attention_items(page.text, page.path))
            return {"kind": "risks", "count": len(rows), "risks": rows}
        return {
            "kind": "summary",
            "count": len(pages),
            "pages": [
                {
                    "page": page.path,
                    "title": page.title,
                    "category": str(page.frontmatter.get("category") or ""),
                    "status": str(page.frontmatter.get("status") or ""),
                    "type": page.type.lower(),
                    "tags": list(page.tags),
                    "last_reviewed": _format_date(page.frontmatter.get("last_reviewed")),
                }
                for page in pages
            ],
        }

    def links(self, page: str) -> dict[str, Any]:
        return {
            "kind": "links",
            **self.page_record(page),
            "links": [
                {
                    **self.page_record(edge.target),
                    "edge_type": edge.type,
                    "weight": edge.weight,
                }
                for edge in outgoing_edges(self.edges, page)
                if edge.target in self.pages
            ],
        }

    def backlinks(self, page: str) -> dict[str, Any]:
        return {
            "kind": "backlinks",
            **self.page_record(page),
            "backlinks": [
                {
                    **self.page_record(edge.source),
                    "edge_type": edge.type,
                    "weight": edge.weight,
                }
                for edge in incoming_edges(self.edges, page)
                if edge.source in self.pages
            ],
        }

    def around(self, page: str, *, depth: int) -> dict[str, Any]:
        return {
            "kind": "around",
            "seed": self.page_record(page),
            "depth": depth,
            "pages": [
                {
                    **self.page_record(item["page"]),
                    "distance": item["distance"],
                    "reasons": item["reasons"],
                    "score": item["score"],
                }
                for item in neighborhood(page, self.pages, self.edges, depth=depth)
            ],
        }

    def context_pack(self, page: str, *, tokens: int) -> dict[str, Any]:
        included = related_pages(page, self.pages, self.edges, depth=2)[:12]
        included_paths = [page, *(item["page"] for item in included)]
        included_pages = [
            {
                **self.page_record(item["page"]),
                "reasons": item["reasons"],
                "score": item["score"],
                "content": _page_content(self.pages[item["page"]], max_chars=2_200),
            }
            for item in included
        ]
        source_refs = [
            {"page": path, "source": str(self.pages[path].frontmatter.get("source"))}
            for path in included_paths
            if self.pages[path].frontmatter.get("source")
        ]
        source_excerpts = []
        seen_sources = set()
        for ref in source_refs:
            source = ref["source"]
            if source in seen_sources:
                continue
            seen_sources.add(source)
            excerpt = self._source_excerpt(source)
            if excerpt:
                source_excerpts.append({"source": source, "content": excerpt})
        open_questions = []
        open_risks = []
        for path in included_paths:
            open_questions.extend(
                {"page": path, "question": question}
                for question in extract_open_questions(self.pages[path])
            )
            open_risks.extend(
                {"page": path, **risk} for risk in extract_open_risks(self.pages[path])
            )
        gaps = []
        for path in included_paths:
            source = str(self.pages[path].frontmatter.get("source") or "")
            source_path = self.source_path(source)
            if source and (source_path is None or not source_path.exists()):
                gaps.append({"page": path, "gap": f"source_missing:{source}"})
        if not incoming_edges(self.edges, page) and not outgoing_edges(self.edges, page):
            gaps.append({"page": page, "gap": "seed_has_no_graph_edges"})
        return {
            "kind": "context_pack",
            "seed": {**self.page_record(page), "content": _page_content(self.pages[page])},
            "budget": {"requested_tokens": tokens, "approx_chars": max(1_000, tokens * 4)},
            "included_pages": included_pages,
            "source_refs": source_refs,
            "source_excerpts": source_excerpts,
            "open_questions": open_questions,
            "open_risks": open_risks,
            "recent_log": self._relevant_log_entries(included_paths),
            "gaps": gaps,
        }

    def health(self) -> dict[str, Any]:
        health = graph_health(self.pages, self.edges)
        return {
            "kind": "graph_health",
            "page_count": health["page_count"],
            "edge_count": health["edge_count"],
            "type_counts": health["type_counts"],
            "components": health["components"],
            "hubs": [
                {
                    **self.page_record(hub["page"]),
                    "in": hub["in"],
                    "out": hub["out"],
                    "degree": hub["degree"],
                }
                for hub in health["hubs"]
            ],
            "orphans": [self.page_record(path) for path in health["orphans"]],
            "pages_without_source": [
                self.page_record(path) for path in health["pages_without_source"]
            ],
        }

    def page_record(self, page: str) -> dict[str, Any]:
        record = self.pages[page]
        return {
            "page": page,
            "title": record.title,
            "type": record.type,
            "category": str(record.frontmatter.get("category") or ""),
            "tags": list(record.tags),
            "source": str(record.frontmatter.get("source") or ""),
            "description": str(record.frontmatter.get("description") or ""),
        }

    def page_content(self, page: str, *, max_chars: int) -> str:
        return _page_content(self.pages[page], max_chars=max_chars)

    def source_path(self, source: str) -> Path | None:
        if not source:
            return None
        return safe_source_path(
            self.root,
            source,
            source_directory=self.content.source_directory,
        )

    def _source_excerpt(self, source: str, max_chars: int = 1_600) -> str | None:
        path = self.source_path(source)
        if path is None or not path.is_file():
            return None
        try:
            text = path.read_text(encoding="utf-8").strip()
        except UnicodeDecodeError:
            return None
        if len(text) > max_chars:
            return text[:max_chars].rstrip() + "\n\n[truncated]"
        return text

    def _open_question_records(self, limit: int = 10) -> list[dict]:
        rows = []
        for path, page in self.pages.items():
            rows.extend(
                {"page": path, "title": page.title, "question": question}
                for question in extract_open_questions(page)
            )
        return rows[:limit]

    def _open_risk_records(self, limit: int = 10) -> list[dict]:
        rows = []
        for path, page in self.pages.items():
            rows.extend(
                {"page": path, "title": page.title, **risk}
                for risk in extract_open_risks(page)
            )
        return rows[:limit]

    def _relevant_log_entries(self, paths: list[str], limit: int = 8) -> list[dict]:
        terms = set()
        for path in paths:
            terms.update((path.lower(), Path(path).name.lower(), self.pages[path].title.lower()))
        matches = [
            entry
            for entry in collect_log(self.root)
            if any(term and term in entry["detail"].lower() for term in terms)
        ]
        return matches[:limit]


def _page_content(page: WikiPage, max_chars: int = 4_000) -> str:
    body = page.body.strip()
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "\n\n[truncated]"
    return body or "[empty body]"


def _format_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return str(value)[:10]
    return str(value)


def _days_since(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        try:
            parsed = date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    return (date.today() - parsed).days


def _parse_risk_rows(text: str, filename: str) -> list[dict]:
    rows = []
    in_table = False
    header_seen = False
    for line in text.splitlines():
        stripped = line.strip()
        if "Risk" in stripped and "Likelihood" in stripped and "|" in stripped:
            in_table = True
            header_seen = False
            continue
        if in_table and stripped.startswith("|") and not stripped.replace("|", "").replace("-", "").strip():
            header_seen = True
            continue
        if in_table and stripped.startswith("|") and header_seen:
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) >= 5 and any(marker in cells[4] for marker in OPEN_STATUSES):
                rows.append(
                    {
                        "file": filename,
                        "risk": cells[0],
                        "likelihood": cells[1],
                        "impact": cells[2],
                        "status": cells[4],
                    }
                )
        elif in_table and not stripped.startswith("|"):
            in_table = False
            header_seen = False
    return rows


def _parse_attention_items(text: str, filename: str) -> list[dict]:
    return [
        {
            "file": filename,
            "kind": match.group(1).lower(),
            "risk": match.group(2).strip(),
            "likelihood": "",
            "impact": "",
            "status": "⚠️ Attention",
        }
        for match in ATTENTION_RE.finditer(text)
    ]
