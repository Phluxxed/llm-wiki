from __future__ import annotations

from dataclasses import dataclass

from ..documents import WikiPage
from ..state import normalize_knowledge_state
from .base import CandidateEvidence, ProviderContext
from .utils import question_terms


MAX_CANDIDATE_CHARS = 4_000
MAX_TEXT_CANDIDATES = 128


@dataclass(frozen=True)
class _Section:
    name: str | None
    start_line: int
    end_line: int
    content: str


class SeedProvider:
    name = "seed"

    def collect(self, context: ProviderContext) -> list[CandidateEvidence]:
        return [
            _candidate(
                context.pages[path],
                provider=self.name,
                route="exact_seed",
                locator=_whole_body(context.pages[path]),
                content=context.pages[path].body,
                shapes=context.shapes,
                signals=("exact_seed",),
            )
            for path in context.resolved_seeds
        ]


class FrontmatterProvider:
    name = "frontmatter"

    def collect(self, context: ProviderContext) -> list[CandidateEvidence]:
        question = context.request.question.lower()
        terms = question_terms(context.request.question)
        candidates: list[CandidateEvidence] = []
        for path, page in context.pages.items():
            title = page.title.lower()
            tags = {tag.lower() for tag in page.tags}
            route = None
            signals: tuple[str, ...] = ()
            if title and title in question:
                route = "exact_title"
                signals = ("exact_title",)
            else:
                matched_tags = sorted(tags & terms)
                if matched_tags:
                    route = "tag_match"
                    signals = tuple(f"tag:{tag}" for tag in matched_tags)
            if route is None:
                continue
            locator = _whole_body(page)
            candidates.append(
                _candidate(
                    page,
                    provider=self.name,
                    route=route,
                    locator=locator,
                    content=page.body,
                    shapes=context.shapes,
                    signals=signals,
                )
            )
        return candidates


class TextProvider:
    name = "text"

    def collect(self, context: ProviderContext) -> list[CandidateEvidence]:
        terms = question_terms(context.request.question)
        ranked: list[tuple[int, str, int, CandidateEvidence]] = []
        for path, page in context.pages.items():
            for section in page_sections(page):
                lowered = section.content.lower()
                matched = sorted(term for term in terms if term in lowered)
                if not matched:
                    continue
                candidate = _candidate(
                    page,
                    provider=self.name,
                    route="lexical_section",
                    locator={
                        "section": section.name,
                        "start_line": section.start_line,
                        "end_line": section.end_line,
                    },
                    content=section.content,
                    shapes=context.shapes,
                    signals=tuple(f"term:{term}" for term in matched),
                )
                ranked.append((-len(matched), path, section.start_line, candidate))
        ranked.sort(key=lambda item: item[:3])
        return [item[3] for item in ranked[:MAX_TEXT_CANDIDATES]]


def _candidate(
    page: WikiPage,
    *,
    provider: str,
    route: str,
    locator: dict,
    content: str,
    shapes: tuple[str, ...],
    signals: tuple[str, ...],
) -> CandidateEvidence:
    state = normalize_knowledge_state(page.frontmatter)
    authority = _authority_signals(page, state.normalized)
    bounded, truncated = bounded_content(content)
    section = locator.get("section") or "body"
    return CandidateEvidence(
        id=f"{provider}:{page.path}#{section}",
        provider=provider,
        route=route,
        page=page.path,
        source=str(page.frontmatter.get("source") or "") or None,
        locator=locator,
        content=bounded,
        roles=_roles(shapes, state.normalized, authority),
        selection_signals=signals,
        authored_state=state.normalized,
        derived_flags=state.derived_flags,
        authority_signals=authority,
        truncated=truncated,
    )


def _roles(shapes: tuple[str, ...], state: str, authority: tuple[str, ...]) -> tuple[str, ...]:
    roles: list[str] = []
    for shape in shapes:
        if shape == "lookup":
            roles.append("answer")
        elif shape == "relationship":
            roles.append("endpoint")
        elif shape == "state" and state == "current":
            roles.append("current_claim")
        elif shape == "history" and state in {"historical", "superseded"}:
            roles.append("lineage")
        elif shape == "synthesis":
            roles.extend(("claim", "support"))
        elif shape == "maintenance":
            roles.append("evidence")
    if "curated_type" in authority:
        roles.append("authority")
    return tuple(dict.fromkeys(roles))


def _authority_signals(page: WikiPage, state: str) -> tuple[str, ...]:
    signals: list[str] = []
    if page.frontmatter.get("source"):
        signals.append("source_reference")
    if page.type.lower() in {"adr", "decision", "policy"}:
        signals.append("curated_type")
    if state == "current":
        signals.append("explicit_current")
    return tuple(signals)


def _whole_body(page: WikiPage) -> dict:
    start = _body_start_line(page.text)
    return {
        "section": None,
        "start_line": start,
        "end_line": max(start, len(page.text.splitlines())),
    }


def page_sections(page: WikiPage) -> list[_Section]:
    lines = page.text.splitlines()
    body_start = _body_start_line(page.text)
    starts = [index for index in range(body_start - 1, len(lines)) if lines[index].startswith("#")]
    if not starts:
        content = "\n".join(lines[body_start - 1 :]).strip()
        return [_Section(None, body_start, len(lines), content)] if content else []

    sections: list[_Section] = []
    if starts[0] > body_start - 1:
        preamble = "\n".join(lines[body_start - 1 : starts[0]]).strip()
        if preamble:
            sections.append(_Section(None, body_start, starts[0], preamble))
    for position, start_index in enumerate(starts):
        end_index = starts[position + 1] if position + 1 < len(starts) else len(lines)
        heading = lines[start_index].lstrip("#").strip() or None
        content = "\n".join(lines[start_index:end_index]).strip()
        if content:
            sections.append(_Section(heading, start_index + 1, end_index, content))
    return sections


def _body_start_line(text: str) -> int:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return 1
    for index, line in enumerate(lines[1:], start=1):
        if line == "---":
            return index + 2
    return 1


def bounded_content(content: str) -> tuple[str, bool]:
    if len(content) <= MAX_CANDIDATE_CHARS:
        return content.strip(), False
    return content[:MAX_CANDIDATE_CHARS].rstrip() + "\n\n[truncated]", True
