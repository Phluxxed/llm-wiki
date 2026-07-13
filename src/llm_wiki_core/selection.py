from __future__ import annotations

from dataclasses import replace
import json
from typing import Iterable

from .contracts import CompileRequest, CompiledContext, Coverage, EvidenceRecord, Omission
from .providers.base import CandidateEvidence
from .state import state_compatibility


_PROVIDER_ORDER = {"seed": 0, "loci": 1, "frontmatter": 2, "graph": 3, "source": 4, "text": 5}


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
    seed_count = sum(item.provider == "seed" for item in ordered)

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
        if candidate.provider != "seed" and not (set(record.roles) & uncovered):
            omissions.append(Omission(candidate.id, "lower_marginal_value", record.byte_cost))
            continue
        if len(selected) >= request.budget.max_items:
            omissions.append(Omission(candidate.id, "item_limit", record.byte_cost))
            continue
        remaining_bytes = request.budget.max_bytes - used_bytes
        if record.byte_cost > remaining_bytes:
            fitted = _fit_record(record, remaining_bytes)
            if fitted is None:
                omissions.append(Omission(candidate.id, "byte_limit", record.byte_cost))
                continue
            record = fitted
        selected.append(record)
        used_bytes += record.byte_cost
        if candidate.provider == "seed":
            seed_count -= 1
        if seed_count == 0 and not coverage(required, selected).uncovered_roles:
            break

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


def finalize_response_budget(response: CompiledContext) -> CompiledContext:
    current = response
    while True:
        payload = json.dumps(
            current.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        total_bytes = len(payload.encode("utf-8"))
        envelope_bytes = total_bytes - current.budget.evidence_bytes
        estimated_tokens = (total_bytes + 3) // 4
        if (
            envelope_bytes == current.budget.envelope_bytes
            and estimated_tokens == current.budget.estimated_tokens
        ):
            return current
        current = replace(
            current,
            budget=replace(
                current.budget,
                envelope_bytes=envelope_bytes,
                estimated_tokens=estimated_tokens,
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


def _excerpt(content: str, limit: int) -> str:
    if limit >= len(content):
        return content
    prefix = content[:limit].rstrip()
    if " " in prefix or "\n" in prefix:
        prefix = prefix.rsplit(maxsplit=1)[0]
    return f"{prefix}\n\n[truncated]" if prefix else ""
