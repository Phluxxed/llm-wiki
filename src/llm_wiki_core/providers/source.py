from __future__ import annotations

from ..contracts import Diagnostic
from ..documents import safe_source_path
from ..okf import local_source_refs
from ..state import normalize_knowledge_state
from .base import CandidateEvidence, ProviderContext, ProviderResult
from .utils import matched_question_terms, question_terms


MAX_SOURCE_CHARS = 4_000


class SourceProvider:
    name = "source"

    def collect(self, context: ProviderContext) -> ProviderResult:
        terms = question_terms(context.request.question)
        candidates: list[CandidateEvidence] = []
        diagnostics: list[Diagnostic] = []
        source_pages: dict[str, list[str]] = {}
        for path, page in context.pages.items():
            # Declared sources may answer the question even when their page's
            # summary does not. Project eligibility is already owned by context.pages.
            for source in local_source_refs(
                page.frontmatter,
                source_directory=context.config.content.source_directory,
                page_path=path,
            ):
                if source:
                    source_pages.setdefault(source, []).append(path)

        for source, paths in source_pages.items():
            path = paths[0]
            page = context.pages[path]
            source_path = safe_source_path(
                context.wiki_root,
                source,
                source_directory=context.config.content.source_directory,
            )
            if source_path is None:
                diagnostics.append(
                    Diagnostic(
                        code="SOURCE_PATH_INVALID",
                        message="Source reference is outside the configured source directory",
                        provider=self.name,
                        details={"page": path, "source": source},
                    )
                )
                continue
            if not source_path.is_file():
                diagnostics.append(
                    Diagnostic(
                        code="SOURCE_NOT_FOUND",
                        message="Referenced source file was not found",
                        provider=self.name,
                        details={"page": path, "source": source},
                    )
                )
                continue
            try:
                raw_content = source_path.read_text(encoding="utf-8").strip()
            except UnicodeDecodeError:
                diagnostics.append(
                    Diagnostic(
                        code="SOURCE_NOT_TEXT",
                        message="Referenced source is not UTF-8 text",
                        provider=self.name,
                        details={"page": path, "source": source},
                    )
                )
                continue

            content, truncated = _bounded(raw_content)
            matched = matched_question_terms(content, terms)
            explicit_seed = bool(set(paths).intersection(context.resolved_seeds))
            if not matched and not explicit_seed:
                continue
            state = normalize_knowledge_state(page.frontmatter)
            candidates.append(
                CandidateEvidence(
                    id=f"source:{source}",
                    provider=self.name,
                    route="source_excerpt",
                    page=path,
                    source=source,
                    locator={
                        "start_line": 1,
                        "end_line": max(1, len(content.splitlines())),
                    },
                    content=content,
                    roles=_source_roles(context.shapes),
                    selection_signals=tuple(f"source_for:{linked}" for linked in paths)
                    + (("explicit_seed_discovery",) if explicit_seed else ()),
                    authored_state="unspecified",
                    derived_flags=(f"linked_page_state:{state.normalized}",),
                    authority_signals=("source_excerpt",),
                    truncated=truncated,
                    relevance_terms=tuple(sorted(set(matched) | set(matched_question_terms(source, terms)))),
                )
            )
        return ProviderResult(tuple(candidates), tuple(diagnostics))


def _source_roles(shapes: tuple[str, ...]) -> tuple[str, ...]:
    roles = ["authority", "support"]
    if "lookup" in shapes:
        roles.append("answer")
    if "relationship" in shapes:
        roles.append("endpoint")
    if "maintenance" in shapes:
        roles.append("evidence")
    return tuple(dict.fromkeys(roles))


def _bounded(content: str) -> tuple[str, bool]:
    if len(content) <= MAX_SOURCE_CHARS:
        return content, False
    return content[:MAX_SOURCE_CHARS].rstrip() + "\n\n[truncated]", True
