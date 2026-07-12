from __future__ import annotations

import re

from ..documents import WikiPage


_TERM_RE = re.compile(r"[a-z0-9][a-z0-9_-]+", re.IGNORECASE)
_STOP_WORDS = {
    "about",
    "after",
    "also",
    "does",
    "from",
    "have",
    "into",
    "that",
    "their",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
}


def question_terms(question: str) -> set[str]:
    return {
        term.lower()
        for term in _TERM_RE.findall(question)
        if len(term) >= 3 and term.lower() not in _STOP_WORDS
    }


def page_matches_question(page: WikiPage, question: str, terms: set[str]) -> bool:
    lowered_question = question.lower()
    if page.title.lower() in lowered_question:
        return True
    fields = " ".join(
        (
            page.path,
            page.title,
            " ".join(page.tags),
            str(page.frontmatter.get("description") or ""),
            page.body,
        )
    ).lower()
    return any(term in fields for term in terms)
