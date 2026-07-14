from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from ..config import WikiConfig
from ..contracts import CompileRequest, Diagnostic
from ..documents import WikiPage


@dataclass(frozen=True)
class CandidateEvidence:
    id: str
    provider: str
    route: str
    page: str | None
    source: str | None
    locator: Mapping[str, Any]
    content: str
    roles: tuple[str, ...]
    selection_signals: tuple[str, ...]
    authored_state: str
    derived_flags: tuple[str, ...]
    authority_signals: tuple[str, ...]
    retrieval_rank: int | None = None
    truncated: bool = False
    atomic: bool = False


@dataclass(frozen=True)
class ProviderContext:
    wiki_root: Path
    config: WikiConfig
    request: CompileRequest
    pages: Mapping[str, WikiPage]
    shapes: tuple[str, ...]
    required_roles: tuple[str, ...]
    resolved_seeds: tuple[str, ...]


@dataclass(frozen=True)
class ProviderResult:
    candidates: tuple[CandidateEvidence, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


class Provider(Protocol):
    name: str

    def collect(self, context: ProviderContext) -> list[CandidateEvidence] | ProviderResult: ...
