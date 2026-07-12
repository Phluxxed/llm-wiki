from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .config import KNOWN_STATES


@dataclass(frozen=True)
class KnowledgeState:
    authored: str | None
    normalized: str
    derived_flags: tuple[str, ...] = ()


def normalize_knowledge_state(
    frontmatter: Mapping[str, Any],
    *,
    field_name: str = "knowledge_state",
) -> KnowledgeState:
    raw = frontmatter.get(field_name)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return KnowledgeState(authored=None, normalized="unspecified")
    authored = str(raw).strip()
    if authored not in KNOWN_STATES:
        return KnowledgeState(
            authored=authored,
            normalized="unspecified",
            derived_flags=("unknown_authored_state",),
        )
    return KnowledgeState(authored=authored, normalized=authored)


def state_compatibility(state: str, state_view: str) -> str:
    if state_view == "all":
        return "allowed"
    if state_view == "current":
        if state == "current":
            return "preferred"
        if state in {"historical", "superseded"}:
            return "lineage_only"
        return "allowed"
    if state_view == "historical":
        if state in {"historical", "superseded"}:
            return "preferred"
        return "allowed"
    if state_view == "transition":
        if state in {"historical", "superseded", "contradicted", "current"}:
            return "preferred"
        return "allowed"
    raise ValueError(f"Unsupported state view: {state_view}")
