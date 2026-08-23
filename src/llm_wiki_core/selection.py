from __future__ import annotations

from dataclasses import replace
import json
from typing import Iterable

from .contracts import (
    CompileRequest,
    CompiledContext,
    ContractError,
    Coverage,
    EvidenceRecord,
    Omission,
    ResponseReporting,
    StopState,
)
from .providers.base import CandidateEvidence
from .state import state_compatibility


_PROVIDER_ORDER = {
    "seed": 0,
    "temporal": 1,
    "loci": 2,
    "frontmatter": 3,
    "graph": 4,
    "source": 5,
    "text": 6,
}
_MAX_RETURNED_OMISSIONS = 16
_MAX_RETURNED_DIAGNOSTICS = 16


def select_candidates(
    candidates: list[CandidateEvidence],
    request: CompileRequest,
    required: tuple[str, ...],
) -> tuple[tuple[EvidenceRecord, ...], tuple[Omission, ...]]:
    ordered = sorted(candidates, key=lambda item: _rank(item, request.state_view, required))
    selected: list[EvidenceRecord] = []
    omissions: list[Omission] = []
    seen: set[tuple] = set()
    used_bytes = 0
    used_content_bytes = 0

    for candidate in ordered:
        locator_identity = json.dumps(candidate.locator, sort_keys=True, separators=(",", ":"))
        identity = (candidate.page, candidate.source, locator_identity, candidate.content)
        if identity in seen:
            omissions.append(Omission(candidate.id, "duplicate", _candidate_cost(candidate)))
            continue
        seen.add(identity)
        compatibility = state_compatibility(candidate.authored_state, request.state_view)
        if compatibility == "lineage_only" and "lineage" not in required:
            omissions.append(Omission(candidate.id, "state_mismatch", _candidate_cost(candidate)))
            continue

        record = _to_record(candidate, required)
        uncovered = set(coverage(required, selected).uncovered_roles)
        within_target = (
            len(selected) < request.budget.target_items
            and record.byte_cost <= request.budget.target_bytes - used_bytes
        )
        supplementary_retrieval = _is_query_selected_graph_evidence(candidate) and within_target
        if (
            candidate.provider != "seed"
            and not (set(record.roles) & uncovered)
            and not supplementary_retrieval
        ):
            omissions.append(Omission(candidate.id, "lower_marginal_value", record.byte_cost))
            continue
        if len(selected) >= request.budget.max_items:
            omissions.append(Omission(candidate.id, "item_limit", record.byte_cost))
            continue
        if request.budget.max_content_bytes is not None:
            remaining_content_bytes = request.budget.max_content_bytes - used_content_bytes
            if _content_bytes(record.content) > remaining_content_bytes:
                if record.atomic:
                    omissions.append(Omission(candidate.id, "content_byte_limit", _content_bytes(record.content)))
                    continue
                fitted_content = _fit_record_to_content(record, remaining_content_bytes)
                if fitted_content is None:
                    omissions.append(Omission(candidate.id, "content_byte_limit", _content_bytes(record.content)))
                    continue
                record = fitted_content
        remaining_bytes = request.budget.max_bytes - used_bytes
        if record.byte_cost > remaining_bytes:
            if candidate.atomic:
                omissions.append(Omission(candidate.id, "byte_limit", record.byte_cost))
                continue
            fitted = _fit_record(record, remaining_bytes)
            if fitted is None:
                omissions.append(Omission(candidate.id, "byte_limit", record.byte_cost))
                continue
            record = fitted
        selected.append(record)
        used_bytes += record.byte_cost
        used_content_bytes += _content_bytes(record.content)

    selected_ids = {item.id for item in selected}
    omitted_ids = {item.candidate_id for item in omissions}
    for candidate in ordered:
        if candidate.id not in selected_ids and candidate.id not in omitted_ids:
            omissions.append(Omission(candidate.id, "lower_marginal_value", _candidate_cost(candidate)))
    return tuple(selected), tuple(omissions)


def _is_query_selected_graph_evidence(candidate: CandidateEvidence) -> bool:
    return (
        candidate.provider == "graph"
        and (
            (
                candidate.route == "evidence_backed_path"
                and "loci_evidence_backed_path" in candidate.selection_signals
            )
            or (
                candidate.route == "path_node_section"
                and "loci_path_node" in candidate.selection_signals
            )
        )
    )


def coverage(required: tuple[str, ...], selected: Iterable[EvidenceRecord]) -> Coverage:
    available = {role for item in selected for role in item.roles}
    covered = tuple(role for role in required if role in available)
    uncovered = tuple(role for role in required if role not in available)
    return Coverage(required, covered, uncovered)


def finalize_response_budget(response: CompiledContext) -> CompiledContext:
    omissions = _prioritize_omissions(response.omissions)
    diagnostics = response.diagnostics
    reported_omissions = omissions[:_MAX_RETURNED_OMISSIONS]
    reported_diagnostics = diagnostics[:_MAX_RETURNED_DIAGNOSTICS]
    full = _with_reporting(
        response,
        reported_omissions,
        reported_diagnostics,
        len(omissions),
        len(diagnostics),
    )
    full = _with_budget_accounting(full)
    limit = _effective_response_limit(full)
    if _serialized_size(full) <= limit:
        return full

    compact = _compact_continuation(
        _with_reporting(full, (), (), len(omissions), len(diagnostics))
    )
    compact, budget_omissions = _fit_evidence(compact, limit)
    all_omissions = _prioritize_omissions((*budget_omissions, *omissions))
    compact = _with_reporting(
        compact,
        (),
        (),
        len(all_omissions),
        len(diagnostics),
    )
    compact = _with_budget_accounting(compact)
    if _serialized_size(compact) > limit:
        minimum = _serialized_size(compact)
        raise ContractError(
            "BUDGET_TOO_SMALL",
            "Budget is too small for the complete response contract",
            {
                "provided_max_bytes": response.budget.max_bytes,
                "provided_max_estimated_tokens": response.budget.max_estimated_tokens,
                "effective_max_bytes": limit,
                "minimum_response_bytes": minimum,
                "minimum_estimated_tokens": (minimum + 3) // 4,
            },
        )

    diagnostic_count = _largest_fitting_prefix(
        compact,
        reported_diagnostics,
        limit,
        field="diagnostics",
        omissions_total=len(all_omissions),
        diagnostics_total=len(diagnostics),
    )
    compact = _with_reporting(
        compact,
        (),
        reported_diagnostics[:diagnostic_count],
        len(all_omissions),
        len(diagnostics),
    )
    omission_count = _largest_fitting_prefix(
        compact,
        all_omissions[:_MAX_RETURNED_OMISSIONS],
        limit,
        field="omissions",
        omissions_total=len(all_omissions),
        diagnostics_total=len(diagnostics),
    )
    compact = _with_reporting(
        compact,
        all_omissions[:omission_count],
        compact.diagnostics,
        len(all_omissions),
        len(diagnostics),
    )
    compact = _with_budget_accounting(compact)
    if _serialized_size(compact) > limit:
        raise ContractError(
            "BUDGET_ENFORCEMENT_FAILED",
            "Complete response exceeded its enforced budget",
            {"effective_max_bytes": limit, "actual_bytes": _serialized_size(compact)},
        )
    return compact


def _fit_evidence(
    response: CompiledContext,
    limit: int,
) -> tuple[CompiledContext, tuple[Omission, ...]]:
    original = response.evidence
    if _serialized_size(_with_budget_accounting(response)) <= limit:
        return response, ()

    low = 0
    high = len(original)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = _response_with_evidence(response, original[:middle], original[middle:])
        if _serialized_size(_with_budget_accounting(candidate)) <= limit:
            low = middle
        else:
            high = middle - 1

    kept = list(original[:low])
    dropped_from = low
    if low < len(original) and not original[low].truncated:
        fitted = _fit_record_to_response(response, tuple(kept), original[low], original[low + 1 :], limit)
        if fitted is not None:
            kept.append(fitted)
            dropped_from += 1

    dropped = original[dropped_from:]
    fitted_response = _response_with_evidence(response, tuple(kept), dropped)
    budget_omissions = tuple(Omission(item.id, "byte_limit", item.byte_cost) for item in dropped)
    return fitted_response, budget_omissions


def _fit_record_to_response(
    response: CompiledContext,
    prefix: tuple[EvidenceRecord, ...],
    record: EvidenceRecord,
    later: tuple[EvidenceRecord, ...],
    limit: int,
) -> EvidenceRecord | None:
    if record.truncated or record.atomic:
        return None
    reasons = tuple(dict.fromkeys((*record.selection_reasons, "hard_limit_excerpt")))
    low = 0
    high = len(record.content)
    best: EvidenceRecord | None = None
    while low <= high:
        middle = (low + high) // 2
        content = _excerpt(record.content, middle)
        if not content:
            low = middle + 1
            continue
        candidate_record = _with_record_cost(
            replace(record, content=content, selection_reasons=reasons, truncated=True, byte_cost=0)
        )
        candidate_response = _response_with_evidence(
            response,
            (*prefix, candidate_record),
            later,
        )
        if _serialized_size(_with_budget_accounting(candidate_response)) <= limit:
            best = candidate_record
            low = middle + 1
        else:
            high = middle - 1
    return best


def _response_with_evidence(
    response: CompiledContext,
    evidence: tuple[EvidenceRecord, ...],
    dropped: tuple[EvidenceRecord, ...],
) -> CompiledContext:
    coverage_state = coverage(response.coverage.required_roles, evidence)
    if not dropped or not coverage_state.uncovered_roles:
        stop = response.stop
        continuation = response.continuation
    else:
        remaining_count = len(dropped) + _continuation_count(response.continuation)
        stop = StopState(
            reason="byte_budget_exhausted",
            sufficient=False,
            detail="Complete response ceiling was reached before coverage was complete",
        )
        continuation = {
            "reason": "hard_limit_reached",
            "uncovered_roles": list(coverage_state.uncovered_roles),
            "remaining_candidate_ids": [],
            "remaining_candidate_count": remaining_count,
        }
    reporting = response.reporting or ResponseReporting(
        omissions_total=len(response.omissions),
        omissions_returned=len(response.omissions),
        diagnostics_total=len(response.diagnostics),
        diagnostics_returned=len(response.diagnostics),
    )
    return replace(
        response,
        evidence=evidence,
        coverage=coverage_state,
        stop=stop,
        continuation=continuation,
        reporting=replace(
            reporting,
            omissions_total=reporting.omissions_total + len(dropped),
        ),
    )


def _compact_continuation(response: CompiledContext) -> CompiledContext:
    continuation = response.continuation
    if not isinstance(continuation, dict):
        return response
    compact = dict(continuation)
    compact["remaining_candidate_ids"] = []
    compact["remaining_candidate_count"] = _continuation_count(continuation)
    return replace(response, continuation=compact)


def _continuation_count(continuation: object) -> int:
    if not isinstance(continuation, dict):
        return 0
    existing_count = continuation.get("remaining_candidate_count")
    if isinstance(existing_count, int) and existing_count >= 0:
        return existing_count
    candidate_ids = continuation.get("remaining_candidate_ids")
    if not isinstance(candidate_ids, list):
        return 0
    return sum(isinstance(candidate_id, str) for candidate_id in candidate_ids)


def _largest_fitting_prefix(
    response: CompiledContext,
    items: tuple,
    limit: int,
    *,
    field: str,
    omissions_total: int,
    diagnostics_total: int,
) -> int:
    low = 0
    high = len(items)
    while low < high:
        middle = (low + high + 1) // 2
        omissions = items[:middle] if field == "omissions" else response.omissions
        diagnostics = items[:middle] if field == "diagnostics" else response.diagnostics
        candidate = _with_reporting(
            response,
            omissions,
            diagnostics,
            omissions_total,
            diagnostics_total,
        )
        if _serialized_size(_with_budget_accounting(candidate)) <= limit:
            low = middle
        else:
            high = middle - 1
    return low


def _with_reporting(
    response: CompiledContext,
    omissions: tuple[Omission, ...],
    diagnostics: tuple,
    omissions_total: int,
    diagnostics_total: int,
) -> CompiledContext:
    return replace(
        response,
        omissions=tuple(omissions),
        diagnostics=tuple(diagnostics),
        reporting=ResponseReporting(
            omissions_total=omissions_total,
            omissions_returned=len(omissions),
            diagnostics_total=diagnostics_total,
            diagnostics_returned=len(diagnostics),
        ),
    )


def _prioritize_omissions(omissions: tuple[Omission, ...]) -> tuple[Omission, ...]:
    priority = {
        "byte_limit": 0,
        "content_byte_limit": 0,
        "item_limit": 1,
        "state_mismatch": 2,
        "duplicate": 3,
    }
    return tuple(
        item
        for _, item in sorted(
            enumerate(omissions),
            key=lambda indexed: (priority.get(indexed[1].reason, 4), indexed[0]),
        )
    )


def _effective_response_limit(response: CompiledContext) -> int:
    token_limit = response.budget.max_estimated_tokens
    if token_limit is None:
        return response.budget.max_bytes
    return min(response.budget.max_bytes, token_limit * 4)


def _serialized_size(response: CompiledContext) -> int:
    payload = json.dumps(
        response.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return len(payload.encode("utf-8"))


def _with_budget_accounting(response: CompiledContext) -> CompiledContext:
    current = response
    while True:
        evidence_bytes = sum(item.byte_cost for item in current.evidence)
        content_bytes = sum(len(item.content.encode("utf-8")) for item in current.evidence)
        total_bytes = _serialized_size(current)
        envelope_bytes = total_bytes - evidence_bytes
        estimated_tokens = (total_bytes + 3) // 4
        core_response = _with_reporting(
            current,
            (),
            (),
            current.reporting.omissions_total if current.reporting is not None else len(current.omissions),
            current.reporting.diagnostics_total if current.reporting is not None else len(current.diagnostics),
        )
        core_bytes = _serialized_size(core_response)
        target_exceeded = (
            core_bytes > current.budget.target_bytes
            or len(current.evidence) > current.budget.target_items
        )
        if (
            evidence_bytes == current.budget.evidence_bytes
            and content_bytes == current.budget.content_bytes
            and envelope_bytes == current.budget.envelope_bytes
            and estimated_tokens == current.budget.estimated_tokens
            and len(current.evidence) == current.budget.items
            and target_exceeded == current.budget.target_exceeded_for_coverage
        ):
            return current
        current = replace(
            current,
            budget=replace(
                current.budget,
                evidence_bytes=evidence_bytes,
                content_bytes=content_bytes,
                envelope_bytes=envelope_bytes,
                items=len(current.evidence),
                estimated_tokens=estimated_tokens,
                target_exceeded_for_coverage=target_exceeded,
            ),
        )


def _rank(candidate: CandidateEvidence, state_view: str, required: tuple[str, ...]) -> tuple:
    state_rank = {"preferred": 0, "allowed": 1, "lineage_only": 2}[
        state_compatibility(candidate.authored_state, state_view)
    ]
    authority_rank = 0 if candidate.authority_signals else 1
    if "seed_authored_multi_bridge" in candidate.selection_signals:
        route_rank = 0
    elif "explicit_seed_bridge" in candidate.selection_signals:
        route_rank = 1
    elif "multi_seed_bridge" in candidate.selection_signals:
        route_rank = 2
    else:
        route_rank = 3
    coverage_rank = -len(set(candidate.roles) & set(required))
    retrieval_rank = candidate.retrieval_rank if candidate.retrieval_rank is not None else 2**31
    return (
        _PROVIDER_ORDER.get(candidate.provider, 50),
        route_rank,
        state_rank,
        authority_rank,
        coverage_rank,
        retrieval_rank,
        candidate.id,
    )


def _to_record(candidate: CandidateEvidence, required: tuple[str, ...]) -> EvidenceRecord:
    reasons = tuple(
        dict.fromkeys(
            (*candidate.selection_signals, *(f"covers:{role}" for role in required if role in candidate.roles))
        )
    )
    return _with_record_cost(
        EvidenceRecord(
            id=candidate.id,
            provider=candidate.provider,
            route=candidate.route,
            page=candidate.page,
            source=candidate.source,
            locator=candidate.locator,
            content=candidate.content,
            roles=candidate.roles,
            authored_state=candidate.authored_state,
            derived_flags=candidate.derived_flags,
            authority_signals=candidate.authority_signals,
            selection_reasons=reasons,
            byte_cost=0,
            truncated=candidate.truncated,
            atomic=candidate.atomic,
        )
    )


def _with_record_cost(record: EvidenceRecord) -> EvidenceRecord:
    cost = _serialized_bytes(record)
    while True:
        adjusted = replace(record, byte_cost=cost)
        actual = _serialized_bytes(adjusted)
        if actual == cost:
            return adjusted
        cost = actual


def _candidate_cost(candidate: CandidateEvidence) -> int:
    return _to_record(candidate, ()).byte_cost


def _serialized_bytes(record: EvidenceRecord) -> int:
    payload = json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return len(payload.encode("utf-8"))


def _fit_record(record: EvidenceRecord, max_bytes: int) -> EvidenceRecord | None:
    reasons = tuple(dict.fromkeys((*record.selection_reasons, "hard_limit_excerpt")))
    empty = _with_record_cost(
        replace(record, content="", selection_reasons=reasons, truncated=True, byte_cost=0)
    )
    if empty.byte_cost > max_bytes:
        return None

    low = 0
    high = len(record.content)
    best = empty
    while low <= high:
        middle = (low + high) // 2
        content = _excerpt(record.content, middle)
        candidate = _with_record_cost(
            replace(record, content=content, selection_reasons=reasons, truncated=True, byte_cost=0)
        )
        if candidate.byte_cost <= max_bytes:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best if best.content else None


def _fit_record_to_content(record: EvidenceRecord, max_content_bytes: int) -> EvidenceRecord | None:
    reasons = tuple(dict.fromkeys((*record.selection_reasons, "content_limit_excerpt")))
    source = record.content.removesuffix("\n\n[truncated]") if record.truncated else record.content
    low = 0
    high = len(source)
    best: EvidenceRecord | None = None
    while low <= high:
        middle = (low + high) // 2
        content = _excerpt(source, middle)
        if content and _content_bytes(content) <= max_content_bytes:
            best = _with_record_cost(
                replace(record, content=content, selection_reasons=reasons, truncated=True, byte_cost=0)
            )
            low = middle + 1
        else:
            high = middle - 1
    return best


def _content_bytes(content: str) -> int:
    return len(content.encode("utf-8"))


def _excerpt(content: str, limit: int) -> str:
    if limit >= len(content):
        return content
    prefix = content[:limit].rstrip()
    if " " in prefix or "\n" in prefix:
        prefix = prefix.rsplit(maxsplit=1)[0]
    return f"{prefix}\n\n[truncated]" if prefix else ""
