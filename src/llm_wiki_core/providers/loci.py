from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..contracts import Diagnostic
from ..state import normalize_knowledge_state
from .base import CandidateEvidence, ProviderContext, ProviderResult


SearchFn = Callable[..., list[dict[str, Any]]]
FileFn = Callable[..., dict[str, Any]]
MAX_RESULTS = 40
MAX_CONTENT_CHARS = 4_000


class LociProvider:
    name = "loci"

    def __init__(self, *, search_fn: SearchFn | None = None, file_fn: FileFn | None = None):
        if (search_fn is None) != (file_fn is None):
            raise ValueError("search_fn and file_fn must be provided together")
        self._search_fn = search_fn
        self._file_fn = file_fn

    def collect(self, context: ProviderContext) -> ProviderResult:
        functions = self._functions()
        if functions is None:
            return ProviderResult(
                diagnostics=(
                    Diagnostic(
                        code="LOCI_UNAVAILABLE",
                        message="Optional loci provider is not installed",
                        provider=self.name,
                    ),
                )
            )
        search_fn, file_fn = functions
        try:
            results = search_fn(
                context.wiki_root,
                context.request.question,
                limit=MAX_RESULTS,
                ensure_fresh=True,
            )
        except Exception as exc:
            return ProviderResult(diagnostics=(_loci_failure(exc),))

        candidates: list[CandidateEvidence] = []
        diagnostics: list[Diagnostic] = []
        for result in results:
            validated = _validate_result(result, context.wiki_root)
            if isinstance(validated, Diagnostic):
                diagnostics.append(validated)
                continue
            symbol_id, file_path, start_line, end_line = validated
            try:
                fetched = file_fn(
                    context.wiki_root,
                    file_path,
                    start_line=start_line,
                    end_line=end_line,
                    ensure_fresh=True,
                )
            except Exception as exc:
                diagnostics.append(_loci_failure(exc, file_path=file_path))
                continue
            if not isinstance(fetched, Mapping) or not isinstance(fetched.get("content"), str):
                diagnostics.append(_invalid_result(file_path, "Cached file result has no text content"))
                continue
            content, truncated = _bounded(str(fetched["content"]))
            page = context.pages.get(file_path)
            state = normalize_knowledge_state(page.frontmatter) if page is not None else None
            is_source = _is_source(file_path, context.config.content.source_directory)
            candidates.append(
                CandidateEvidence(
                    id=f"loci:{symbol_id}",
                    provider=self.name,
                    route="indexed_section",
                    page=file_path if page is not None else None,
                    source=file_path if is_source else None,
                    locator={
                        "symbol_id": symbol_id,
                        "file": file_path,
                        "start_line": start_line,
                        "end_line": end_line,
                    },
                    content=content,
                    roles=_roles(context.shapes, is_source),
                    selection_signals=("indexed_symbol_match",),
                    authored_state=state.normalized if state is not None else "unspecified",
                    derived_flags=state.derived_flags if state is not None else (),
                    authority_signals=("source_index_span",) if is_source else (),
                    truncated=truncated,
                )
            )
        return ProviderResult(tuple(candidates), tuple(diagnostics))

    def _functions(self) -> tuple[SearchFn, FileFn] | None:
        if self._search_fn is not None and self._file_fn is not None:
            return self._search_fn, self._file_fn
        try:
            from loci.service import get_cached_file, search_symbols
        except ImportError:
            return None
        return search_symbols, get_cached_file


def _validate_result(
    result: Any,
    root: Path,
) -> tuple[str, str, int, int] | Diagnostic:
    if not isinstance(result, Mapping):
        return _invalid_result(None, "Search result is not an object")
    symbol_id = result.get("id")
    file_path = result.get("file_path")
    start_line = result.get("line")
    end_line = result.get("end_line")
    if (
        not isinstance(symbol_id, str)
        or not symbol_id
        or not isinstance(file_path, str)
        or not file_path
        or isinstance(start_line, bool)
        or not isinstance(start_line, int)
        or isinstance(end_line, bool)
        or not isinstance(end_line, int)
        or start_line < 1
        or end_line < start_line
    ):
        return _invalid_result(file_path if isinstance(file_path, str) else None, "Search result shape is invalid")
    candidate = (root / file_path).resolve()
    if Path(file_path).is_absolute() or not candidate.is_relative_to(root):
        return _invalid_result(file_path, "Search result points outside the wiki root")
    return symbol_id, file_path.replace("\\", "/"), start_line, end_line


def _loci_failure(exc: Exception, *, file_path: str | None = None) -> Diagnostic:
    source_code = str(getattr(exc, "code", ""))
    code = "LOCI_REPO_NOT_INDEXED" if source_code == "REPO_NOT_INDEXED" else "LOCI_PROVIDER_FAILED"
    details: dict[str, Any] = {"type": type(exc).__name__}
    if file_path is not None:
        details["file"] = file_path
    return Diagnostic(
        code=code,
        message="loci could not provide indexed context",
        provider="loci",
        details=details,
    )


def _invalid_result(file_path: str | None, message: str) -> Diagnostic:
    details = {"file": file_path} if file_path is not None else {}
    return Diagnostic(
        code="LOCI_RESULT_INVALID",
        message=message,
        provider="loci",
        details=details,
    )


def _roles(shapes: tuple[str, ...], is_source: bool) -> tuple[str, ...]:
    roles: list[str] = []
    if "lookup" in shapes:
        roles.append("answer")
    if "relationship" in shapes:
        roles.append("endpoint")
    if "synthesis" in shapes or "history" in shapes:
        roles.append("support")
    if "maintenance" in shapes:
        roles.append("evidence")
    if is_source:
        roles.extend(("support", "authority"))
    return tuple(dict.fromkeys(roles))


def _is_source(file_path: str, source_directory: str) -> bool:
    parts = Path(file_path).parts
    return bool(parts) and parts[0] == source_directory


def _bounded(content: str) -> tuple[str, bool]:
    if len(content) <= MAX_CONTENT_CHARS:
        return content, False
    return content[:MAX_CONTENT_CHARS].rstrip() + "\n\n[truncated]", True
