from __future__ import annotations

import re
from collections.abc import Iterable


_SHAPE_PATTERNS = (
    ("lookup", re.compile(r"\b(who|what|where|which|owner|command|definition|define)\b", re.IGNORECASE)),
    (
        "relationship",
        re.compile(r"\b(how does|connect|relationship|between|depend|propagat|interact|link)\w*\b", re.IGNORECASE),
    ),
    ("state", re.compile(r"\b(current|currently|now|latest|status|still true)\b", re.IGNORECASE)),
    ("history", re.compile(r"\b(history|historical|previous|formerly|used to|changed|timeline)\b", re.IGNORECASE)),
    ("maintenance", re.compile(r"\b(stale|drift|gap|missing|maintain|cleanup|contradiction)\w*\b", re.IGNORECASE)),
)

_ROLE_MAP = {
    "lookup": ("answer", "authority"),
    "relationship": ("endpoint", "bridge"),
    "state": ("current_claim", "authority"),
    "history": ("lineage", "authority"),
    "synthesis": ("claim", "support", "authority"),
    "maintenance": ("finding", "evidence", "ownership"),
}


def classify_question(question: str) -> tuple[str, ...]:
    shapes = tuple(name for name, pattern in _SHAPE_PATTERNS if pattern.search(question))
    if shapes:
        return shapes
    return ("synthesis",)


def required_roles(shapes: Iterable[str]) -> tuple[str, ...]:
    roles: list[str] = []
    for shape in shapes:
        for role in _ROLE_MAP[shape]:
            if role not in roles:
                roles.append(role)
    return tuple(roles)
