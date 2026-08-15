"""Deterministic, candidate-only reconciliation for temporal fact candidates."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import re
import unicodedata
from typing import Any

from .temporal import ObservationRef, TemporalContractError, TemporalFactCandidate

__all__ = [
    "ReconciliationRelation",
    "TemporalReconciliationResult",
    "reconcile_temporal_candidates",
]

_RELATION_VERSION = "temporal-reconciliation-relation/1"
_RESULT_VERSION = "temporal-reconciliation/1"
_RELATION_ID = re.compile(r"^temporal-reconciliation-relation:sha256:[0-9a-f]{64}$")
_RESULT_ID = re.compile(r"^temporal-reconciliation:sha256:[0-9a-f]{64}$")
_CANDIDATE_ID = re.compile(r"^temporal-candidate:sha256:[0-9a-f]{64}$")
_OBSERVATION_ID = re.compile(r"^temporal-observation:sha256:[0-9a-f]{64}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_KINDS = {"duplicate", "supersede", "contradict", "qualify", "unresolved"}
_BASES = {
    "duplicate": {"exact_fact_and_evidence"},
    "supersede": {"same_claim_later_world_start"},
    "contradict": {"same_claim_same_world_start"},
    "qualify": {"declared_qualification"},
    "unresolved": {
        "ambiguous_identity",
        "incomplete_provenance",
        "unknown_world_start",
        "same_fact_different_interval",
        "declared_relation_unconfirmed",
        "declared_unresolved",
        "missing_target",
    },
}
_STEWARDSHIP = {
    "decision": "review_required",
    "instruction": "Review reconciliation proposals through the target wiki steward; this result grants no mutation authority.",
}


def _error(code: str, message: str, field: str | None = None, **details: Any) -> TemporalContractError:
    if field is not None:
        details = {"field": field, **details}
    return TemporalContractError(code, message, details)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _error("TEMPORAL_INVALID_FIELD", f"{field} must be an object", field)
    return value


def _fields(raw: Mapping[str, Any], required: set[str], allowed: set[str], field: str) -> None:
    missing = sorted(required - set(raw))
    if missing:
        raise _error("TEMPORAL_INVALID_FIELD", f"{field} is missing required fields", field, missing=missing)
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise _error("TEMPORAL_UNKNOWN_FIELD", f"{field} contains unknown fields", field, unknown=unknown)


def _canonical(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        return {unicodedata.normalize("NFC", str(k)): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise _error("TEMPORAL_INVALID_FIELD", "non-finite numbers are not supported")
    return value


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _error("TEMPORAL_INVALID_FIELD", "value is not canonically serializable") from exc


def _sha(prefix: str, body: Mapping[str, Any]) -> str:
    return f"{prefix}:sha256:{hashlib.sha256(_json_bytes(body)).hexdigest()}"


def _ids(raw: Any, field: str, *, limit: int = 256) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise _error("TEMPORAL_INVALID_FIELD", f"{field} must be an array", field)
    if len(raw) > limit:
        raise _error("TEMPORAL_LIMIT_EXCEEDED", f"{field} exceeds its item limit", field)
    values: set[str] = set()
    for value in raw:
        if not isinstance(value, str) or _OBSERVATION_ID.fullmatch(value) is None:
            raise _error("TEMPORAL_INVALID_FIELD", f"{field} contains an invalid observation ID", field)
        values.add(value)
    return tuple(sorted(values))


def _unknowns(raw: Any, field: str, *, limit: int = 256) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise _error("TEMPORAL_INVALID_FIELD", f"{field} must be an array", field)
    if len(raw) > limit:
        raise _error("TEMPORAL_LIMIT_EXCEEDED", f"{field} exceeds its item limit", field)
    values: set[tuple[str, str]] = set()
    for index, item in enumerate(raw):
        item = _mapping(item, f"{field}[{index}]")
        _fields(item, {"field", "reason"}, {"field", "reason"}, f"{field}[{index}]")
        name, reason = item["field"], item["reason"]
        if not isinstance(name, str) or not name or not isinstance(reason, str) or not reason:
            raise _error("TEMPORAL_INVALID_FIELD", f"{field} entries must contain text", field)
        if len(name) > 128 or len(reason) > 512:
            raise _error("TEMPORAL_LIMIT_EXCEEDED", f"{field} entry is too large", field)
        values.add((name, reason))
    return tuple(sorted(values))


def _candidate_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or _CANDIDATE_ID.fullmatch(value) is None:
        raise _error("TEMPORAL_INVALID_FIELD", f"{field} is invalid", field)
    return value


def _relation_sort_key(relation: "ReconciliationRelation") -> tuple[str, str, str, str, str]:
    return (relation.kind, relation.source_candidate_id, relation.target_candidate_id or "", relation.basis, relation.relation_id)


@dataclass(frozen=True)
class ReconciliationRelation:
    contract_version: str
    relation_id: str
    kind: str
    source_candidate_id: str
    target_candidate_id: str | None
    basis: str
    observation_ids: tuple[str, ...]
    unknowns: tuple[tuple[str, str], ...] = ()

    @property
    def disposition(self) -> str:
        return "candidate_only"

    @property
    def mutation(self) -> dict[str, Any]:
        return {"allowed": False, "commands": []}

    def _identity(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "kind": self.kind,
            "source_candidate_id": self.source_candidate_id,
            "target_candidate_id": self.target_candidate_id,
            "basis": self.basis,
            "observation_ids": list(self.observation_ids),
            "unknowns": [{"field": field, "reason": reason} for field, reason in self.unknowns],
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ReconciliationRelation":
        raw = _mapping(raw, "relation")
        allowed = {
            "contract_version", "relation_id", "kind", "source_candidate_id", "target_candidate_id", "basis",
            "observation_ids", "unknowns", "disposition", "mutation",
        }
        _fields(raw, allowed, allowed, "relation")
        if raw["contract_version"] != _RELATION_VERSION:
            raise _error("TEMPORAL_VERSION_UNSUPPORTED", "unsupported reconciliation relation contract version", "contract_version")
        if raw["disposition"] != "candidate_only" or raw["mutation"] != {"allowed": False, "commands": []}:
            raise _error("TEMPORAL_INVALID_FIELD", "relation authority fields are immutable", "relation")
        kind, basis = raw["kind"], raw["basis"]
        if kind not in _KINDS:
            raise _error("TEMPORAL_INVALID_FIELD", "relation kind is unsupported", "kind")
        if basis not in _BASES[kind]:
            raise _error("TEMPORAL_INVALID_FIELD", "relation basis is unsupported for kind", "basis")
        source = _candidate_id(raw["source_candidate_id"], "source_candidate_id")
        target = raw["target_candidate_id"]
        if target is not None:
            target = _candidate_id(target, "target_candidate_id")
        if kind != "unresolved" and target is None:
            raise _error("TEMPORAL_INVALID_FIELD", "resolved relations require a target", "target_candidate_id")
        ids = _ids(raw["observation_ids"], "observation_ids")
        if len(ids) > 256:
            raise _error("TEMPORAL_LIMIT_EXCEEDED", "observation_ids exceeds 256 items", "observation_ids")
        unknowns = _unknowns(raw["unknowns"], "unknowns", limit=32)
        relation = cls(_RELATION_VERSION, raw["relation_id"], kind, source, target, basis, ids, unknowns)
        if not isinstance(raw["relation_id"], str) or _RELATION_ID.fullmatch(raw["relation_id"]) is None:
            raise _error("TEMPORAL_INVALID_FIELD", "relation_id is invalid", "relation_id")
        if _sha("temporal-reconciliation-relation", relation._identity()) != relation.relation_id:
            raise _error("TEMPORAL_ID_MISMATCH", "relation_id does not match canonical identity", "relation_id")
        if len(_json_bytes(relation.to_dict())) > 65536:
            raise _error("TEMPORAL_LIMIT_EXCEEDED", "relation exceeds 65536 bytes", "relation")
        return relation

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "relation_id": self.relation_id,
            "kind": self.kind,
            "source_candidate_id": self.source_candidate_id,
            "target_candidate_id": self.target_candidate_id,
            "basis": self.basis,
            "observation_ids": list(self.observation_ids),
            "unknowns": [{"field": field, "reason": reason} for field, reason in self.unknowns],
            "disposition": self.disposition,
            "mutation": self.mutation,
        }


@dataclass(frozen=True)
class TemporalReconciliationResult:
    kind: str
    contract_version: str
    reconciliation_id: str
    status: str
    candidate_ids: tuple[str, ...]
    relations: tuple[ReconciliationRelation, ...]
    unknowns: tuple[tuple[str, str], ...]
    usage: Mapping[str, int]

    @property
    def disposition(self) -> str:
        return "candidate_only"

    @property
    def mutation(self) -> dict[str, Any]:
        return {"allowed": False, "commands": []}

    @property
    def stewardship(self) -> dict[str, str]:
        return dict(_STEWARDSHIP)

    def _identity(self) -> dict[str, Any]:
        return {
            "contract_version": _RESULT_VERSION,
            "candidate_ids": list(self.candidate_ids),
            "relation_ids": [relation.relation_id for relation in self.relations],
            "unknowns": [{"field": field, "reason": reason} for field, reason in self.unknowns],
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TemporalReconciliationResult":
        raw = _mapping(raw, "reconciliation")
        allowed = {
            "kind", "contract_version", "reconciliation_id", "status", "candidate_ids", "relations", "unknowns",
            "usage", "disposition", "mutation", "stewardship",
        }
        _fields(raw, allowed, allowed, "reconciliation")
        if raw["kind"] != "temporal_reconciliation_result" or raw["contract_version"] != _RESULT_VERSION:
            raise _error("TEMPORAL_VERSION_UNSUPPORTED", "unsupported reconciliation result contract version", "contract_version")
        if raw["disposition"] != "candidate_only" or raw["mutation"] != {"allowed": False, "commands": []} or raw["stewardship"] != _STEWARDSHIP:
            raise _error("TEMPORAL_INVALID_FIELD", "reconciliation authority fields are immutable", "reconciliation")
        status = raw["status"]
        if status not in {"relations_proposed", "unresolved_present", "no_relations_observed"}:
            raise _error("TEMPORAL_INVALID_FIELD", "reconciliation status is unsupported", "status")
        candidates_raw = raw["candidate_ids"]
        if not isinstance(candidates_raw, Sequence) or isinstance(candidates_raw, (str, bytes)):
            raise _error("TEMPORAL_INVALID_FIELD", "candidate_ids must be an array", "candidate_ids")
        candidates = tuple(sorted(_candidate_id(value, "candidate_ids") for value in candidates_raw))
        if len(candidates) != len(set(candidates)):
            raise _error("TEMPORAL_INVALID_FIELD", "candidate_ids must be unique", "candidate_ids")
        if len(candidates) > 100:
            raise _error("TEMPORAL_LIMIT_EXCEEDED", "candidate_ids exceeds 100 items", "candidate_ids")
        relations_raw = raw["relations"]
        if not isinstance(relations_raw, Sequence) or isinstance(relations_raw, (str, bytes)):
            raise _error("TEMPORAL_INVALID_FIELD", "relations must be an array", "relations")
        if len(relations_raw) > 1000:
            raise _error("TEMPORAL_LIMIT_EXCEEDED", "relations exceeds 1000 items", "relations")
        relations = tuple(sorted((ReconciliationRelation.from_mapping(item) for item in relations_raw), key=_relation_sort_key))
        unknowns = _unknowns(raw["unknowns"], "unknowns", limit=256)
        usage = _usage(raw["usage"])
        expected_status = "unresolved_present" if any(relation.kind == "unresolved" for relation in relations) else ("relations_proposed" if relations else "no_relations_observed")
        if status != expected_status:
            raise _error("TEMPORAL_INVALID_FIELD", "reconciliation status does not match relations", "status")
        relation = cls("temporal_reconciliation_result", _RESULT_VERSION, raw["reconciliation_id"], status, candidates, relations, unknowns, usage)
        if not isinstance(relation.reconciliation_id, str) or _RESULT_ID.fullmatch(relation.reconciliation_id) is None:
            raise _error("TEMPORAL_INVALID_FIELD", "reconciliation_id is invalid", "reconciliation_id")
        if _sha("temporal-reconciliation", relation._identity()) != relation.reconciliation_id:
            raise _error("TEMPORAL_ID_MISMATCH", "reconciliation_id does not match canonical identity", "reconciliation_id")
        if len(_json_bytes(relation.to_dict())) > 1_000_000:
            raise _error("TEMPORAL_LIMIT_EXCEEDED", "reconciliation exceeds 1000000 bytes", "reconciliation")
        return relation

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "contract_version": self.contract_version,
            "reconciliation_id": self.reconciliation_id,
            "status": self.status,
            "candidate_ids": list(self.candidate_ids),
            "relations": [relation.to_dict() for relation in self.relations],
            "unknowns": [{"field": field, "reason": reason} for field, reason in self.unknowns],
            "usage": dict(self.usage),
            "disposition": self.disposition,
            "mutation": self.mutation,
            "stewardship": self.stewardship,
        }


def _usage(raw: Any) -> dict[str, int]:
    raw = _mapping(raw, "usage")
    required = {"candidate_count", "observation_count", "claim_group_count", "comparisons", "relation_count"}
    _fields(raw, required, required, "usage")
    result: dict[str, int] = {}
    for field in sorted(required):
        value = raw[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise _error("TEMPORAL_INVALID_FIELD", "usage values must be non-negative integers", f"usage.{field}")
        result[field] = value
    return {field: result[field] for field in ("candidate_count", "observation_count", "claim_group_count", "comparisons", "relation_count")}


def _make_relation(kind: str, source: str, target: str | None, basis: str, observation_ids: set[str] | Sequence[str] = (), unknowns: set[tuple[str, str]] | Sequence[tuple[str, str]] = ()) -> ReconciliationRelation:
    relation = ReconciliationRelation(
        _RELATION_VERSION,
        "",
        kind,
        source,
        target,
        basis,
        tuple(sorted(set(observation_ids))),
        tuple(sorted(set(unknowns))),
    )
    return ReconciliationRelation(
        relation.contract_version,
        _sha("temporal-reconciliation-relation", relation._identity()),
        relation.kind,
        relation.source_candidate_id,
        relation.target_candidate_id,
        relation.basis,
        relation.observation_ids,
        relation.unknowns,
    )


def _world_start(candidate: TemporalFactCandidate) -> datetime | None:
    value = candidate.proposed_world_validity.from_.value
    if candidate.proposed_world_validity.from_.kind != "known" or value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _ambiguous(candidate: TemporalFactCandidate) -> bool:
    return candidate.subject.kind == "ambiguous" or candidate.object_ref.kind == "ambiguous"


def _evidence(candidate: TemporalFactCandidate) -> set[str]:
    return set(candidate.supporting_observation_ids) | set(candidate.conflicting_observation_ids)


def _fact_key(candidate: TemporalFactCandidate) -> bytes:
    return _json_bytes({
        "claim_key": candidate.claim_key,
        "subject": candidate.subject.to_dict(),
        "predicate": candidate.predicate,
        "object": candidate.object_ref.to_dict(),
        "proposed_world_validity": candidate.proposed_world_validity.to_dict(),
    })


def _decl_key(kind: str, source: str, target: str | None) -> tuple[str, str, str | None]:
    if kind in {"duplicate", "contradict"} and target is not None:
        source, target = sorted((source, target))
    return kind, source, target


def reconcile_temporal_candidates(
    *,
    candidates: Sequence[TemporalFactCandidate],
    observations: Mapping[str, ObservationRef],
) -> TemporalReconciliationResult:
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        raise _error("TEMPORAL_INVALID_FIELD", "candidates must be a sequence", "candidates")
    if not isinstance(observations, Mapping):
        raise _error("TEMPORAL_INVALID_FIELD", "observations must be a mapping", "observations")
    if len(observations) > 6400:
        raise _error("TEMPORAL_LIMIT_EXCEEDED", "observations exceeds 6400 mappings", "observations")
    for key, observation in observations.items():
        if not isinstance(key, str) or not isinstance(observation, ObservationRef):
            raise _error("TEMPORAL_INVALID_FIELD", "observations must map IDs to ObservationRef values", "observations")
        if key != observation.observation_id:
            raise _error("TEMPORAL_ID_MISMATCH", "observation mapping key does not match observation_id", "observations")
    by_id: dict[str, TemporalFactCandidate] = {}
    for candidate in candidates:
        if not isinstance(candidate, TemporalFactCandidate):
            raise _error("TEMPORAL_INVALID_FIELD", "candidates must contain TemporalFactCandidate values", "candidates")
        current = by_id.get(candidate.candidate_id)
        if current is None or _json_bytes(candidate.to_dict()) < _json_bytes(current.to_dict()):
            by_id[candidate.candidate_id] = candidate
    if len(by_id) > 100:
        raise _error("TEMPORAL_LIMIT_EXCEEDED", "candidates exceeds 100 unique IDs", "candidates")
    candidate_ids = tuple(sorted(by_id))
    declaration_ids = set()
    for candidate in by_id.values():
        declaration_ids.update(ids for _, _, ids in candidate.proposed_relations for ids in ids)
    referenced = set()
    for candidate in by_id.values():
        referenced.update(_evidence(candidate))
    referenced.update(declaration_ids)
    observation_count = len(referenced & set(observations))

    relations: list[ReconciliationRelation] = []
    canonical_map: dict[str, str] = {}
    eligible: dict[str, TemporalFactCandidate] = {}
    candidate_evidence: dict[str, set[str]] = {}
    excluded: set[str] = set()

    # Preflight ambiguity and missing provenance before any pairwise inference.
    complete: dict[str, bool] = {}
    for candidate_id in candidate_ids:
        candidate = by_id[candidate_id]
        evidence = _evidence(candidate)
        missing = evidence - set(observations)
        complete[candidate_id] = not missing
        if _ambiguous(candidate):
            unknowns = []
            if candidate.subject.kind == "ambiguous":
                unknowns.append(("subject", "ambiguous_identity"))
            if candidate.object_ref.kind == "ambiguous":
                unknowns.append(("object", "ambiguous_identity"))
            relations.append(_make_relation("unresolved", candidate_id, None, "ambiguous_identity", evidence, unknowns))
            excluded.add(candidate_id)
        if missing:
            relations.append(_make_relation("unresolved", candidate_id, None, "incomplete_provenance", evidence, (("observation_ids", "incomplete_provenance"),)))
            excluded.add(candidate_id)
        if candidate_id not in excluded:
            eligible[candidate_id] = candidate
            candidate_evidence[candidate_id] = evidence

    # Exact fact/evidence duplicates collapse to the lexicographically smallest ID.
    duplicate_groups: dict[tuple[bytes, tuple[str, ...]], list[str]] = defaultdict(list)
    for candidate_id, candidate in eligible.items():
        duplicate_groups[(_fact_key(candidate), tuple(sorted(candidate_evidence[candidate_id])))].append(candidate_id)
    for members in duplicate_groups.values():
        members.sort()
        canonical = members[0]
        for member in members:
            canonical_map[member] = canonical
        for duplicate in members[1:]:
            relations.append(_make_relation("duplicate", duplicate, canonical, "exact_fact_and_evidence", candidate_evidence[canonical]))
            eligible.pop(duplicate, None)
            candidate_evidence.pop(duplicate, None)
    for candidate_id in list(eligible):
        canonical_map.setdefault(candidate_id, candidate_id)

    # Declarations are validated before claim-chain classification.
    qualifications: list[tuple[str, str, set[str]]] = []
    declarations: list[tuple[str, str, str | None, tuple[str, ...], bool]] = []
    for original_source in candidate_ids:
        candidate = by_id[original_source]
        source = canonical_map.get(original_source, original_source)
        for kind, target_raw, evidence_ids in candidate.proposed_relations:
            target = canonical_map.get(target_raw, target_raw) if target_raw is not None else None
            resolved_evidence = set(evidence_ids) & set(observations)
            target_present = target_raw is not None and target_raw in by_id
            if target_raw is not None and target_raw in canonical_map:
                target_present = True
            source_valid = source in eligible
            target_valid = target in eligible if target is not None else False
            provenance_valid = not (set(evidence_ids) - set(observations))
            if kind == "qualify" and target_present and source_valid and target_valid and provenance_valid:
                qualifications.append((source, target, resolved_evidence))
                continue
            declarations.append((source, kind, target, evidence_ids, target_present))

    suppressed_pairs = {frozenset((source, target)) for source, target, _ in qualifications}
    for source, target, evidence in qualifications:
        combined = set(candidate_evidence.get(source, ())) | set(candidate_evidence.get(target or "", ())) | evidence
        relations.append(_make_relation("qualify", source, target, "declared_qualification", combined))

    # Build exact claim chains and inspect adjacent members only.
    chains: dict[str, list[TemporalFactCandidate]] = defaultdict(list)
    for candidate in eligible.values():
        chains[candidate.claim_key].append(candidate)
    comparisons = 0
    for chain in chains.values():
        chain.sort(key=lambda item: (0, _world_start(item), item.candidate_id) if _world_start(item) is not None else (1, datetime.max, item.candidate_id))
        for left, right in zip(chain, chain[1:]):
            comparisons += 1
            left_id, right_id = left.candidate_id, right.candidate_id
            pair_evidence = candidate_evidence[left_id] | candidate_evidence[right_id]
            same_object = left.object_ref.to_dict() == right.object_ref.to_dict()
            same_interval = left.proposed_world_validity.to_dict() == right.proposed_world_validity.to_dict()
            if same_object and same_interval:
                continue
            if same_object:
                relations.append(_make_relation("unresolved", *sorted((left_id, right_id)), "same_fact_different_interval", pair_evidence, (("proposed_world_validity", "same_fact_different_interval"),)))
                continue
            left_start, right_start = _world_start(left), _world_start(right)
            if left_start is None or right_start is None:
                relations.append(_make_relation("unresolved", *sorted((left_id, right_id)), "unknown_world_start", pair_evidence, (("proposed_world_validity.from", "unknown_world_start"),)))
            elif left_start == right_start:
                source, target = sorted((left_id, right_id))
                if frozenset((source, target)) not in suppressed_pairs:
                    relations.append(_make_relation("contradict", source, target, "same_claim_same_world_start", pair_evidence))
            else:
                later, earlier = (left, right) if left_start > right_start else (right, left)
                if (later.candidate_id, earlier.candidate_id) not in {(source, target) for source, target, _ in qualifications}:
                    relations.append(_make_relation("supersede", later.candidate_id, earlier.candidate_id, "same_claim_later_world_start", pair_evidence))

    # Merge declarations into derived relations, or retain them as explicit unresolved proposals.
    for source, kind, target, evidence_ids, target_present in declarations:
        if kind == "unresolved":
            relations.append(_make_relation("unresolved", source, None, "declared_unresolved", set(evidence_ids), (("proposed_relations", "declared_unresolved"),)))
            continue
        if not target_present or target is None:
            relations.append(_make_relation("unresolved", source, None, "missing_target", set(evidence_ids), (("proposed_relations", "missing_target"),)))
            continue
        match = []
        for index, relation in enumerate(relations):
            if relation.kind != kind or relation.target_candidate_id is None:
                continue
            if _decl_key(kind, relation.source_candidate_id, relation.target_candidate_id) == _decl_key(kind, source, target):
                match.append(index)
        if match:
            resolved = set(evidence_ids) & set(observations)
            for index in match:
                relation = relations[index]
                relations[index] = _make_relation(relation.kind, relation.source_candidate_id, relation.target_candidate_id, relation.basis, set(relation.observation_ids) | resolved, relation.unknowns)
        else:
            relations.append(_make_relation("unresolved", source, target, "declared_relation_unconfirmed", set(evidence_ids), (("proposed_relations", "declared_relation_unconfirmed"),)))

    # Merge records by semantic tuple; provenance and unknowns are unions.
    merged: dict[tuple[str, str, str | None, str], ReconciliationRelation] = {}
    for relation in relations:
        key = (relation.kind, relation.source_candidate_id, relation.target_candidate_id, relation.basis)
        previous = merged.get(key)
        if previous is None:
            merged[key] = relation
        else:
            merged[key] = _make_relation(
                relation.kind,
                relation.source_candidate_id,
                relation.target_candidate_id,
                relation.basis,
                set(previous.observation_ids) | set(relation.observation_ids),
                set(previous.unknowns) | set(relation.unknowns),
            )
    ordered = tuple(sorted(merged.values(), key=_relation_sort_key))
    if len(ordered) > 1000:
        raise _error("TEMPORAL_LIMIT_EXCEEDED", "reconciliation relations exceeds 1000 items", "relations")
    result_unknowns = tuple(sorted({unknown for candidate in by_id.values() for unknown in candidate.unknowns}))
    if len(result_unknowns) > 256:
        raise _error("TEMPORAL_LIMIT_EXCEEDED", "reconciliation unknowns exceeds 256 items", "unknowns")
    status = "unresolved_present" if any(relation.kind == "unresolved" for relation in ordered) else ("relations_proposed" if ordered else "no_relations_observed")
    usage = {
        "candidate_count": len(candidate_ids),
        "observation_count": observation_count,
        "claim_group_count": len(chains),
        "comparisons": comparisons,
        "relation_count": len(ordered),
    }
    result = TemporalReconciliationResult("temporal_reconciliation_result", _RESULT_VERSION, "", status, candidate_ids, ordered, result_unknowns, usage)
    result = TemporalReconciliationResult(result.kind, result.contract_version, _sha("temporal-reconciliation", result._identity()), result.status, result.candidate_ids, result.relations, result.unknowns, result.usage)
    if len(_json_bytes(result.to_dict())) > 1_000_000:
        raise _error("TEMPORAL_LIMIT_EXCEEDED", "reconciliation exceeds 1000000 bytes", "reconciliation")
    return result
