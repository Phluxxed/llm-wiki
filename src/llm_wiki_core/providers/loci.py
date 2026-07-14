from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mcp import ClientSession

from ..contracts import Diagnostic
from ..state import normalize_knowledge_state
from .base import CandidateEvidence, ProviderContext, ProviderResult
from .loci_transport import LociGatewayError, LociMcpClient, tool_payload
from .utils import question_terms


SearchFn = Callable[..., list[dict[str, Any]]]
FileFn = Callable[..., dict[str, Any]]
MAX_RESULTS = 40
MAX_CONTENT_CHARS = 4_000


@dataclass(frozen=True)
class LociRetrieval:
    results: tuple[Mapping[str, Any], ...] = ()
    failures: tuple[LociGatewayError, ...] = ()


class LociGateway(Protocol):
    def retrieve(self, wiki_root: Path, query: str, *, limit: int) -> LociRetrieval: ...


class LociMcpGateway:
    """Synchronous facade over loci's production local stdio MCP surface."""

    def __init__(
        self,
        *,
        command: str | None = None,
        args: tuple[str, ...] = (),
        timeout_seconds: float = 15.0,
    ):
        self._client = LociMcpClient(
            command=command,
            args=args,
            timeout_seconds=timeout_seconds,
        )

    def retrieve(self, wiki_root: Path, query: str, *, limit: int) -> LociRetrieval:
        return self._client.run(
            lambda session: self._retrieve_session(
                session,
                wiki_root,
                query,
                limit=limit,
            )
        )

    async def _retrieve_session(
        self,
        session: ClientSession,
        wiki_root: Path,
        query: str,
        *,
        limit: int,
    ) -> LociRetrieval:
        results: list[Mapping[str, Any]] = []
        failures: list[LociGatewayError] = []
        search = await session.call_tool(
            "loci_search",
            arguments={
                "repo": str(wiki_root),
                "query": query,
                "limit": limit,
            },
        )
        symbols = tool_payload(search, "symbols")
        if not isinstance(symbols, list):
            raise LociGatewayError(
                "LOCI_RESULT_INVALID",
                "loci_search returned an invalid symbols payload",
            )
        valid: list[tuple[Mapping[str, Any], tuple[str, str, int, int]]] = []
        for symbol in symbols:
            validated = _validate_result(symbol, wiki_root)
            if isinstance(validated, Diagnostic):
                results.append(symbol if isinstance(symbol, Mapping) else {})
                continue
            valid.append((symbol, validated))
        if valid:
            fetched = await session.call_tool(
                "loci_get",
                arguments={
                    "repo": str(wiki_root),
                    "symbol_ids": [item[1][0] for item in valid],
                    "context": 0,
                },
            )
            hydrated_symbols = tool_payload(fetched, "symbols")
            if not isinstance(hydrated_symbols, list):
                raise LociGatewayError(
                    "LOCI_RESULT_INVALID",
                    "loci_get returned an invalid symbols payload",
                )
            by_id = {
                item.get("id"): item
                for item in hydrated_symbols
                if isinstance(item, Mapping) and isinstance(item.get("id"), str)
            }
            for symbol, validated in valid:
                symbol_id, file_path, _, _ = validated
                hydrated_symbol = by_id.get(symbol_id)
                source = hydrated_symbol.get("source") if hydrated_symbol is not None else None
                if not isinstance(source, str):
                    failures.append(
                        LociGatewayError(
                            "LOCI_RESULT_INVALID",
                            "loci_get did not return exact symbol source",
                            {"file": file_path},
                        )
                    )
                    continue
                hydrated = dict(symbol)
                hydrated["content"] = source
                hydrated["hydrated_locator"] = {
                    "file_path": hydrated_symbol.get("file_path"),
                    "line": hydrated_symbol.get("line"),
                    "end_line": hydrated_symbol.get("end_line"),
                }
                results.append(hydrated)
        return LociRetrieval(tuple(results), tuple(failures))


class _FunctionLociGateway:
    def __init__(self, search_fn: SearchFn, file_fn: FileFn):
        self._search_fn = search_fn
        self._file_fn = file_fn

    def retrieve(self, wiki_root: Path, query: str, *, limit: int) -> LociRetrieval:
        results = self._search_fn(wiki_root, query, limit=limit, ensure_fresh=True)
        hydrated: list[Mapping[str, Any]] = []
        failures: list[LociGatewayError] = []
        for result in results:
            validated = _validate_result(result, wiki_root)
            if isinstance(validated, Diagnostic):
                hydrated.append(result if isinstance(result, Mapping) else {})
                continue
            _, file_path, start_line, end_line = validated
            try:
                fetched = self._file_fn(
                    wiki_root,
                    file_path,
                    start_line=start_line,
                    end_line=end_line,
                    ensure_fresh=True,
                )
            except Exception as exc:
                failures.append(
                    LociGatewayError(
                        str(getattr(exc, "code", "LOCI_PROVIDER_FAILED")),
                        "loci could not hydrate indexed context",
                        {"file": file_path, "type": type(exc).__name__},
                    )
                )
                continue
            item = dict(result)
            if isinstance(fetched, Mapping):
                item["content"] = fetched.get("content")
                item["hydrated_locator"] = {
                    "file_path": fetched.get("file_path", fetched.get("file")),
                    "line": fetched.get("start_line", start_line),
                    "end_line": fetched.get("end_line", end_line),
                }
            hydrated.append(item)
        return LociRetrieval(tuple(hydrated), tuple(failures))


class LociProvider:
    name = "loci"

    def __init__(
        self,
        *,
        gateway: LociGateway | None = None,
        search_fn: SearchFn | None = None,
        file_fn: FileFn | None = None,
    ):
        if (search_fn is None) != (file_fn is None):
            raise ValueError("search_fn and file_fn must be provided together")
        if gateway is not None and search_fn is not None:
            raise ValueError("gateway cannot be combined with search_fn and file_fn")
        self._gateway = gateway or (
            _FunctionLociGateway(search_fn, file_fn)
            if search_fn is not None and file_fn is not None
            else LociMcpGateway()
        )

    def collect(self, context: ProviderContext) -> ProviderResult:
        try:
            retrieval = self._gateway.retrieve(
                context.wiki_root,
                context.request.question,
                limit=MAX_RESULTS,
            )
        except Exception as exc:
            return ProviderResult(diagnostics=(_loci_failure(exc),))

        candidates: list[CandidateEvidence] = []
        diagnostics = [_loci_failure(exc) for exc in retrieval.failures]
        for retrieval_rank, result in enumerate(retrieval.results):
            validated = _validate_result(result, context.wiki_root)
            if isinstance(validated, Diagnostic):
                diagnostics.append(validated)
                continue
            symbol_id, file_path, start_line, end_line = validated
            if not _hydration_matches(result.get("hydrated_locator"), validated):
                diagnostics.append(_invalid_result(file_path, "Hydrated result locator does not match search"))
                continue
            if not isinstance(result.get("content"), str):
                diagnostics.append(_invalid_result(file_path, "Cached file result has no text content"))
                continue
            content, truncated = _bounded(str(result["content"]))
            if not _has_meaningful_query_match(result, content, context.request.question):
                continue
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
                        "search_rank": retrieval_rank,
                    },
                    content=content,
                    roles=_roles(context.shapes, is_source),
                    selection_signals=("indexed_symbol_match", f"search_rank:{retrieval_rank}"),
                    authored_state=state.normalized if state is not None else "unspecified",
                    derived_flags=state.derived_flags if state is not None else (),
                    authority_signals=("source_index_span",) if is_source else (),
                    retrieval_rank=retrieval_rank,
                    truncated=truncated,
                )
            )
        return ProviderResult(tuple(candidates), tuple(diagnostics))


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


def _hydration_matches(locator: Any, expected: tuple[str, str, int, int]) -> bool:
    if not isinstance(locator, Mapping):
        return False
    _, file_path, start_line, end_line = expected
    hydrated_path = locator.get("file_path")
    return (
        (hydrated_path is None or (
            isinstance(hydrated_path, str)
            and hydrated_path.replace("\\", "/") == file_path
        ))
        and locator.get("line") == start_line
        and locator.get("end_line") == end_line
    )


def _has_meaningful_query_match(result: Mapping[str, Any], content: str, question: str) -> bool:
    terms = question_terms(question)
    if not terms:
        return False
    searchable = " ".join((*_text_values(result), content)).lower()
    return any(term in searchable for term in terms)


def _text_values(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for nested in value.values():
            yield from _text_values(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _text_values(nested)


def _loci_failure(exc: Exception, *, file_path: str | None = None) -> Diagnostic:
    source_code = str(getattr(exc, "code", ""))
    code = {
        "REPO_NOT_INDEXED": "LOCI_REPO_NOT_INDEXED",
        "LOCI_MCP_UNAVAILABLE": "LOCI_MCP_UNAVAILABLE",
        "LOCI_MCP_FAILED": "LOCI_MCP_FAILED",
        "LOCI_MCP_TIMEOUT": "LOCI_MCP_TIMEOUT",
        "LOCI_RESULT_INVALID": "LOCI_RESULT_INVALID",
    }.get(source_code, "LOCI_PROVIDER_FAILED")
    details: dict[str, Any] = {"type": type(exc).__name__}
    source_details = getattr(exc, "details", None)
    if isinstance(source_details, Mapping):
        details.update(source_details)
    if file_path is not None:
        details["file"] = file_path
    return Diagnostic(
        code=code,
        message="Core loci traversal could not provide indexed context",
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
