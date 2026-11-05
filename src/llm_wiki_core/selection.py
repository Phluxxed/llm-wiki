from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re
from typing import Any, Iterable, Mapping

from .contracts import (
    CompileRequest,
    CompiledContext,
    ContractError,
    Coverage,
    Diagnostic,
    EvidenceRecord,
    Omission,
    ResponseReporting,
    StopState,
)
from .providers.base import CandidateEvidence
from .providers.temporal import TEMPORAL_DIAGNOSTIC_RECOVERY
from .providers.utils import matched_question_terms, question_terms
from .state import state_compatibility


_PROVIDER_ORDER = {
    "seed": 0,
    "temporal": 1,
    "frontmatter": 2,
    "graph": 3,
    "source": 4,
    "text": 5,
    "project": 6,
}
_MAX_RETURNED_OMISSIONS = 16
_MAX_RETURNED_DIAGNOSTICS = 16
_DELIVERY_DIAGNOSTIC_CODES = frozenset(
    {
        "PROJECT_IDENTITY_AMBIGUOUS",
        "PROJECT_IDENTITY_INVALID",
        "PROJECT_ALIAS_SHADOWED",
    }
)
_DELIVERY_DIAGNOSTIC_RECOVERY = (
    "Repair the canonical Brain project identity or workspace mapping before relying on project routing."
)
_JS_WHITESPACE = re.compile(
    r"[\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+"
)
_JS_WHITESPACE_CHARS = (
    "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)


@dataclass(frozen=True)
class DeliveryCostContext:
    """Internal projection inputs for the closed Anvil Prompt Evidence profile."""

    contract_version: str
    project_resolution: Mapping[str, Any] | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    temporal: Mapping[str, Any] | None = None


def select_candidates(
    candidates: list[CandidateEvidence],
    request: CompileRequest,
    required: tuple[str, ...],
    *,
    include_supplements: bool = True,
    delivery_context: DeliveryCostContext | None = None,
) -> tuple[tuple[EvidenceRecord, ...], tuple[Omission, ...]]:
    terms = question_terms(request.question)
    relevance = {
        item.id: set(item.relevance_terms if item.relevance_terms is not None
                     else matched_question_terms(item.content, terms))
        for item in candidates
    }
    query_providers = {"text", "frontmatter", "source", "temporal"}
    strongest = max((len(relevance[item.id]) for item in candidates
                     if item.provider in query_providers), default=0)
    ordered = sorted(candidates, key=lambda item: (
        0 if item.provider == "seed" or (item.provider == "temporal" and item.page in request.seeds)
        else 2 if item.provider == "source" else 1,
        -len(relevance[item.id]),
        # Temporal authority governs applicability, not question relevance.
        # Prefer it on a relevance tie; do not crowd out a better answer merely
        # because another page has a valid, loosely related temporal claim.
        0 if request.temporal is not None and item.provider == "temporal" else 1,
        _rank(item, request.state_view, required),
    ))
    selected: list[EvidenceRecord] = []
    omissions: list[Omission] = []
    seen: set[tuple] = set()
    used_bytes = 0
    used_content_bytes = 0

    # Finish the existing compact selection before considering supplements, so
    # later claims cannot crowd out its supporting sources. Each deferred item
    # is reconsidered once, without another provider call or relevance rule.
    pending = [(candidate, False) for candidate in ordered]
    for candidate, supplement in pending:
        explicit = (candidate.provider == "seed" or candidate.page in request.seeds
                    or "explicit_seed_discovery" in candidate.selection_signals)
        if (candidate.provider in query_providers
                and not relevance[candidate.id] and not explicit):
            omissions.append(Omission(candidate.id, "question_mismatch", _candidate_cost(candidate)))
            continue
        if candidate.provider == "source":
            linked_pages = {signal.removeprefix("source_for:")
                            for signal in candidate.selection_signals if signal.startswith("source_for:")}
            claims = [item for item in selected if item.provider not in {"source", "project"}]
            supported_claims = [
                item for item in claims
                if bool(relevance[item.id] & relevance[candidate.id])
                and (
                    candidate.source in item.locator.get("steward_evidence_refs", ())
                    if request.temporal is not None and item.provider == "temporal"
                    else item.page in linked_pages
                )
            ]
            supports_selected_claim = bool(supported_claims)
            if request.temporal is not None and any("authority" in item.roles for item in supported_claims):
                candidate = replace(candidate, roles=tuple(dict.fromkeys((*candidate.roles, "authority"))),
                                    selection_signals=(*candidate.selection_signals, "supports_selected_authority"))
            # A source may answer a question about the source itself. Otherwise
            # its declared provenance must connect to a selected relevant claim;
            # unrelated provenance cannot independently fill an authority slot.
            named_source = len(matched_question_terms(candidate.source or "", terms)) >= 2
            selected_claim_terms = set().union(*(relevance[item.id] for item in claims))
            direct_source_answer = bool(terms) and (
                named_source or (len(relevance[candidate.id]) == strongest
                                 and selected_claim_terms <= relevance[candidate.id])
            )
            if not explicit and not supports_selected_claim and not direct_source_answer:
                if include_supplements and not supplement:
                    pending.append((candidate, True))
                    continue
                omissions.append(Omission(candidate.id, "unsupported_source", _candidate_cost(candidate)))
                continue
        locator = dict(candidate.locator)
        if "start_line" in locator and "end_line" in locator:
            # Seed and section providers may name the same exact span
            # differently. A heading label does not make its text distinct.
            locator.pop("section", None)
        locator_identity = json.dumps(locator, sort_keys=True, separators=(",", ":"))
        identity = (
            candidate.page, candidate.source, candidate.provider == "source",
            locator_identity, candidate.content,
        )
        if identity in seen:
            # A project fallback may intentionally share a whole-body span
            # with an internal graph discovery candidate.  Keep it when its
            # normal query-shape roles still cover an uncovered role; the
            # fallback's mandatory status must not be defeated by deduping on
            # locator/content alone.  Preserve the existing duplicate rule
            # for every other provider and for already-covered fallbacks.
            duplicate_roles = set(candidate.roles) & set(coverage(required, selected).uncovered_roles)
            is_useful_project_fallback = (
                candidate.provider == "project"
                and "project_orientation_fallback" in candidate.selection_signals
                and duplicate_roles
            )
            if not is_useful_project_fallback:
                omissions.append(Omission(candidate.id, "duplicate", _candidate_cost(candidate)))
                continue
        compatibility = state_compatibility(candidate.authored_state, request.state_view)
        if (compatibility == "lineage_only" and "lineage" not in required
                and candidate.provider != "temporal"
                and "temporal_background" not in candidate.derived_flags):
            omissions.append(Omission(candidate.id, "state_mismatch", _candidate_cost(candidate)))
            continue

        record = _to_record(candidate, required)
        uncovered = set(coverage(required, selected).uncovered_roles)
        mandatory_project_fallback = (
            candidate.provider == "project"
            and "project_orientation_fallback" in candidate.selection_signals
        )
        if (
            not supplement
            and candidate.provider != "seed"
            and not mandatory_project_fallback
            and not (set(record.roles) & uncovered)
        ):
            if include_supplements and (
                candidate.provider in query_providers and relevance[candidate.id]
            ):
                pending.append((candidate, True))
                continue
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
        if request.budget.max_delivery_bytes is not None:
            context = delivery_context or DeliveryCostContext(
                contract_version=request.contract_version,
                temporal=request.temporal.to_dict() if request.temporal is not None else None,
            )
            delivery_limit = request.budget.max_delivery_bytes
            projected_delivery_bytes = delivery_budget_cost_bytes(
                (*selected, record),
                context,
            )
            if projected_delivery_bytes > delivery_limit:
                if candidate.atomic:
                    omissions.append(
                        Omission(candidate.id, "delivery_byte_limit", record.byte_cost)
                    )
                    continue
                fitted_delivery = _fit_record_to_delivery(
                    record,
                    tuple(selected),
                    context,
                    delivery_limit,
                )
                if fitted_delivery is None:
                    omissions.append(
                        Omission(candidate.id, "delivery_byte_limit", record.byte_cost)
                    )
                    continue
                record = fitted_delivery
        selected.append(record)
        seen.add(identity)
        used_bytes += record.byte_cost
        used_content_bytes += _content_bytes(record.content)

    selected_ids = {item.id for item in selected}
    omitted_ids = {item.candidate_id for item in omissions}
    for candidate in ordered:
        if candidate.id not in selected_ids and candidate.id not in omitted_ids:
            omissions.append(Omission(candidate.id, "lower_marginal_value", _candidate_cost(candidate)))
    return tuple(selected), tuple(omissions)


def coverage(required: tuple[str, ...], selected: Iterable[EvidenceRecord]) -> Coverage:
    available = {role for item in selected for role in item.roles}
    covered = tuple(role for role in required if role in available)
    uncovered = tuple(role for role in required if role not in available)
    return Coverage(required, covered, uncovered)


def delivery_rendered_bytes(
    evidence: Iterable[EvidenceRecord],
    context: DeliveryCostContext,
) -> int:
    """Return the UTF-8 size of Anvil's untruncated Prompt Evidence render."""

    records = tuple(evidence)
    relevant = None
    if records:
        relevant = _delivery_section(
            "Prompt Evidence",
            "\n\n".join(_delivery_evidence_block(item) for item in records),
        )

    diagnostics = _delivery_diagnostics(context)
    diagnostic_section = None
    if diagnostics:
        diagnostic_section = _delivery_section(
            "Diagnostics",
            "\n".join(
                "\n".join(
                    (
                        f"- code: {code}",
                        f"- message: {_delivery_one_line(message)}",
                        f"- recovery: {_delivery_one_line(recovery)}",
                    )
                )
                for code, message, recovery in diagnostics
            ),
        )

    pages: list[str] = []
    seen_pages: set[str] = set()
    for item in records:
        if item.page and item.page not in seen_pages:
            seen_pages.add(item.page)
            pages.append(item.page)
    resolution = context.project_resolution if context.contract_version == "3" else None
    matched_project = (
        resolution.get("page")
        if isinstance(resolution, Mapping) and resolution.get("status") == "matched"
        else None
    )
    temporal_line = ()
    if context.temporal is not None:
        temporal_query = {key: context.temporal[key] for key in ("view", "request_time", "world_at", "known_at", "transition")
                          if key in context.temporal}
        temporal_line = ("- temporal_query: " + json.dumps(temporal_query, ensure_ascii=False, separators=(",", ":")),)
    route = _delivery_section(
        "Recall Route",
        "\n".join(
            (
                f"- current_project: {_delivery_code(matched_project) if matched_project else 'unknown_project'}",
                "- kernel_pages: none",
                "- evidence_pages: "
                + (
                    ", ".join(_delivery_code(page) for page in pages)
                    if pages
                    else "none"
                ),
            ) + temporal_line
        ),
    )

    prefix_parts = ["## Brain Context"]
    if relevant is not None:
        prefix_parts.extend(("", relevant))
    if diagnostic_section is not None:
        prefix_parts.extend(("", diagnostic_section))
    prefix = "\n".join(prefix_parts)
    full = f"{prefix}\n\n{route}\n"
    return len(full.encode("utf-8"))


def _delivery_section(title: str, body: str) -> str:
    return f"### {title}\n\n{body}"


def _delivery_code(value: str) -> str:
    return f"`{value}`"


def _delivery_evidence_block(record: EvidenceRecord) -> str:
    origin = record.page if record.page is not None else record.source
    if origin is None:
        origin = record.id
    roles = (
        ", ".join(_delivery_code(role) for role in record.roles)
        if record.roles
        else "none"
    )
    return "\n".join(
        (
            f"#### `{origin}`",
            "",
            _delivery_trim(record.content),
            "",
            f"- evidence_id: `{record.id}`",
            f"- roles: {roles}",
        )
    )


def _delivery_diagnostics(
    context: DeliveryCostContext,
) -> tuple[tuple[str, str, str], ...]:
    if context.contract_version != "3":
        return ()
    result: list[tuple[str, str, str]] = []
    for diagnostic in context.diagnostics:
        if diagnostic.code in _DELIVERY_DIAGNOSTIC_CODES:
            result.append(
                (
                    diagnostic.code,
                    diagnostic.message,
                    _DELIVERY_DIAGNOSTIC_RECOVERY,
                )
            )
    resolution = context.project_resolution
    if (
        isinstance(resolution, Mapping)
        and resolution.get("status") == "ambiguous"
        and not any(code == "PROJECT_IDENTITY_AMBIGUOUS" for code, _, _ in result)
    ):
        result.append(
            (
                "PROJECT_IDENTITY_AMBIGUOUS",
                "llm-wiki reported multiple projects for the observed workspace identity.",
                _DELIVERY_DIAGNOSTIC_RECOVERY,
            )
        )
    for diagnostic in context.diagnostics:
        if diagnostic.code in TEMPORAL_DIAGNOSTIC_RECOVERY:
            host_code = "BRAIN_TEMPORAL_HISTORY_INVALID" if diagnostic.code == "TEMPORAL_HISTORY_INVALID" else "BRAIN_TEMPORAL_UNCERTAIN"
            result.append((host_code, diagnostic.message, TEMPORAL_DIAGNOSTIC_RECOVERY[diagnostic.code]))
    return tuple(result)


def delivery_budget_cost_bytes(
    evidence: Iterable[EvidenceRecord],
    context: DeliveryCostContext,
) -> int:
    """Return the bounded packing cost for the closed delivery profile.

    Anvil supplies its own ``PROJECT_IDENTITY_AMBIGUOUS`` diagnostic when a
    v3 response has an ambiguous project resolution and the explicit compiler
    diagnostic is absent from the JSON envelope. Selection can therefore
    reserve against both the ordinary render and that fallback render without
    introducing a second fitting pass during response finalization.
    """

    records = tuple(evidence)
    actual = delivery_rendered_bytes(records, context)
    resolution = context.project_resolution
    if (
        context.contract_version != "3"
        or not isinstance(resolution, Mapping)
        or resolution.get("status") != "ambiguous"
    ):
        return actual
    compact_context = DeliveryCostContext(
        contract_version=context.contract_version,
        project_resolution=context.project_resolution,
        temporal=context.temporal,
        diagnostics=tuple(
            diagnostic
            for diagnostic in context.diagnostics
            if diagnostic.code != "PROJECT_IDENTITY_AMBIGUOUS"
        ),
    )
    return max(actual, delivery_rendered_bytes(records, compact_context))


def _delivery_trim(value: str) -> str:
    return value.strip(_JS_WHITESPACE_CHARS)


def _delivery_one_line(value: str) -> str:
    return _JS_WHITESPACE.sub(" ", value).strip(_JS_WHITESPACE_CHARS)


def _fit_record_to_delivery(
    record: EvidenceRecord,
    prefix: tuple[EvidenceRecord, ...],
    context: DeliveryCostContext,
    max_bytes: int,
) -> EvidenceRecord | None:
    reasons = tuple(dict.fromkeys((*record.selection_reasons, "delivered_limit_excerpt")))
    source = record.content.removesuffix("\n\n[truncated]") if record.truncated else record.content
    low = 0
    high = len(source)
    best: EvidenceRecord | None = None
    while low <= high:
        middle = (low + high) // 2
        content = _excerpt(source, middle)
        if not content:
            low = middle + 1
            continue
        candidate = _with_record_cost(
            replace(
                record,
                content=content,
                selection_reasons=reasons,
                truncated=True,
                byte_cost=0,
            )
        )
        if delivery_budget_cost_bytes((*prefix, candidate), context) <= max_bytes:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def finalize_response_budget(response: CompiledContext) -> CompiledContext:
    omissions = _prioritize_omissions(response.omissions)
    critical_diagnostics = tuple(d for d in response.diagnostics if d.code in TEMPORAL_DIAGNOSTIC_RECOVERY)
    diagnostics = critical_diagnostics + tuple(d for d in response.diagnostics if d.code not in TEMPORAL_DIAGNOSTIC_RECOVERY)
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
        return _assert_delivery_budget(full)

    compact = _compact_continuation(
        _with_reporting(full, (), critical_diagnostics, len(omissions), len(diagnostics))
    )
    compact, budget_omissions = _fit_evidence(compact, limit)
    all_omissions = _prioritize_omissions((*budget_omissions, *omissions))
    compact = _with_reporting(
        compact,
        (),
        critical_diagnostics,
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
    return _assert_delivery_budget(compact)


def _assert_delivery_budget(response: CompiledContext) -> CompiledContext:
    """Validate the actual rendered delivery after normal JSON finalization."""

    max_delivery = response.budget.max_delivery_bytes
    actual_delivery = response.budget.delivery_bytes
    if max_delivery is not None and actual_delivery is not None and actual_delivery > max_delivery:
        raise ContractError(
            "BUDGET_ENFORCEMENT_FAILED",
            "Prompt Evidence delivery exceeded its enforced byte allowance",
            {
                "max_delivery_bytes": max_delivery,
                "delivery_bytes": actual_delivery,
                "delivery_format": response.budget.delivery_format,
            },
        )
    return response


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
    original_by_id = {item.id: item for item in response.evidence}
    finalizer_excerpt = any(
        "hard_limit_excerpt" in item.selection_reasons
        and (
            (original := original_by_id.get(item.id)) is None
            or item.content != original.content
            or item.byte_cost != original.byte_cost
            or item.truncated != original.truncated
            or "hard_limit_excerpt" not in original.selection_reasons
        )
        for item in evidence
    )
    if not dropped and not finalizer_excerpt:
        stop = response.stop
        continuation = response.continuation
    elif not dropped:
        stop = StopState(
            reason="byte_budget_exhausted",
            sufficient=not coverage_state.uncovered_roles,
            detail="Complete response ceiling limited the candidate evidence returned",
        )
        continuation = response.continuation
    else:
        remaining_count = len(dropped) + _continuation_count(response.continuation)
        stop = StopState(
            reason="byte_budget_exhausted",
            sufficient=not coverage_state.uncovered_roles,
            detail="Complete response ceiling limited the candidate evidence returned",
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
        "delivery_byte_limit": 0,
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
        delivery_bytes = (
            delivery_rendered_bytes(
                current.evidence,
                DeliveryCostContext(
                    contract_version=current.contract_version,
                    project_resolution=current.project_resolution,
                    diagnostics=current.diagnostics,
                    temporal=current.temporal.to_dict() if current.temporal is not None else None,
                ),
            )
            if current.budget.max_delivery_bytes is not None
            else None
        )
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
            and delivery_bytes == current.budget.delivery_bytes
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
                delivery_bytes=delivery_bytes,
            ),
        )


def _rank(candidate: CandidateEvidence, state_view: str, required: tuple[str, ...]) -> tuple:
    state_rank = {"preferred": 0, "allowed": 1, "lineage_only": 2}[
        state_compatibility(candidate.authored_state, state_view)
    ]
    authority_rank = 0 if candidate.authority_signals else 1
    if "exact_title" in candidate.selection_signals:
        route_rank = 0
    elif "seed_authored_multi_bridge" in candidate.selection_signals:
        route_rank = 0
    elif "explicit_seed_bridge" in candidate.selection_signals:
        route_rank = 1
    elif "multi_seed_bridge" in candidate.selection_signals:
        route_rank = 2
    else:
        route_rank = 3
    coverage_rank = -len(set(candidate.roles) & set(required))
    metadata = candidate.metadata or {}
    lifecycle_rank = 1 if metadata.get("status") == "deprecated" else 0
    freshness_rank = 1 if metadata.get("freshness") == "stale" else 0
    trust_rank = {"human-reviewed": 0, "machine-confirmed": 1}.get(metadata.get("trust"), 2)
    return (
        _PROVIDER_ORDER.get(candidate.provider, 50),
        route_rank,
        state_rank,
        authority_rank,
        coverage_rank,
        lifecycle_rank,
        freshness_rank,
        trust_rank,
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
            metadata=candidate.metadata,
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
