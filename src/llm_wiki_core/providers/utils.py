from __future__ import annotations

import re

from ..documents import WikiPage


_TERM_RE = re.compile(r"[a-z0-9][a-z0-9_-]+", re.IGNORECASE)
_STOP_WORDS = {
    "and", "are", "before", "can", "could", "continue", "for", "how",
    "its", "now", "please", "should", "the", "then", "there", "these",
    "they", "was", "were", "who", "why", "will", "would", "you", "your",
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
    ignored = text_terms(" ".join(_STOP_WORDS))
    return {term for term in text_terms(question) if len(term) >= 3
            and not term.isdigit() and term not in ignored}


def text_terms(text: str) -> set[str]:
    """Whole words, retaining identifiers as well as their named components."""
    words = {term.lower() for term in _TERM_RE.findall(text)}
    words |= {part for word in words for part in re.split(r"[-_]", word)}
    # A conservative shared normalization keeps own/owns and skill/skills
    # equivalent without bringing substring matching back.
    return {word[:-1] if len(word) >= 4 and word.endswith("s")
            and not word.endswith(("ss", "us", "is")) else word for word in words}


def matched_question_terms(text: str, terms: set[str]) -> tuple[str, ...]:
    return tuple(sorted(terms & text_terms(text)))


def title_matches_question(title: str, question: str) -> bool:
    return bool(title.strip()) and bool(
        re.search(r"(?<!\w)" + re.escape(title.strip()) + r"(?!\w)", question, re.IGNORECASE)
    )


def page_matches_question(page: WikiPage, question: str, terms: set[str]) -> bool:
    if title_matches_question(page.title, question):
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
    return bool(matched_question_terms(fields, terms))
