"""Pure validation and known-time folding for steward-authored claim revisions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any

from .contracts import TemporalQuery
from .temporal import (
    EntityRef,
    TemporalContractError,
    TimeInterval,
    _fail,
    _instant,
    _json_bytes,
    _namespace,
    _path,
    _sha,
    _text,
    _timestamp,
    build_temporal_claim_key,
)


_REVISION_VERSION = "temporal-claim-revision/1"
_FOLD_VERSION = "temporal-revision-fold/1"
_DECISIONS = {"accept", "retire", "supersede", "contradict", "qualify"}
_ESTABLISHED_DECISIONS = {"accept", "supersede", "contradict", "qualify"}
_REVISION_ID = re.compile(r"^temporal-revision:sha256:[0-9a-f]{64}$")
_CANDIDATE_ID = re.compile(r"^temporal-candidate:sha256:[0-9a-f]{64}$")
_OBSERVATION_ID = re.compile(r"^temporal-observation:sha256:[0-9a-f]{64}$")
_HASH = re.compile(r"^temporal-claim:sha256:[0-9a-f]{64}$")

_REVISION_FIELDS = {
    "contract_version",
    "revision_id",
    "claim_key",
    "claim_scope",
    "decision",
    "subject",
    "predicate",
    "object",
    "world_validity",
    "recorded_at",
    "candidate_ids",
    "observation_ids",
    "retires_revision_ids",
    "supersedes_revision_ids",
    "contradicts_revision_ids",
    "qualification_of_revision_ids",
    "steward_evidence_refs",
    "authority",
}
_RELATION_FIELDS = (
    "retires_revision_ids",
    "supersedes_revision_ids",
    "contradicts_revision_ids",
    "qualification_of_revision_ids",
)
_DECISION_RELATION = {
    "accept": None,
    "retire": "retires_revision_ids",
    "supersede": "supersedes_revision_ids",
    "contradict": "contradicts_revision_ids",
    "qualify": "qualification_of_revision_ids",
}


def _mapping(raw: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise _fail("TEMPORAL_INVALID_FIELD", f"{field} must be an object", field)
    return raw


def _fields(raw: Mapping[str, Any], field: str) -> None:
    missing = sorted(_REVISION_FIELDS - set(raw))
    if missing:
        raise _fail("TEMPORAL_INVALID_FIELD", f"{field} is missing required fields", field, missing=missing)
    unknown = sorted(set(raw) - _REVISION_FIELDS)
    if unknown:
        raise _fail("TEMPORAL_UNKNOWN_FIELD", f"{field} contains unknown fields", field, unknown=unknown)


def _recorded_at(value: Any, field: str = "recorded_at") -> str:
    normalized = _timestamp(value, field)
    if len(normalized) == 10:
        raise _fail("TEMPORAL_INVALID_FIELD", f"{field} must be an RFC 3339 instant", field)
    return normalized


def _array(raw: Any, field: str) -> Sequence[Any]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise _fail("TEMPORAL_INVALID_FIELD", f"{field} must be an array", field)
    return raw


def _ids(raw: Any, field: str, pattern: re.Pattern[str], *, limit: int = 64, required: bool = False) -> tuple[str, ...]:
    values = set()
    for value in _array(raw, field):
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise _fail("TEMPORAL_INVALID_FIELD", f"{field} contains an invalid ID", field)
        values.add(value)
    if required and not values:
        raise _fail("TEMPORAL_INVALID_FIELD", f"{field} must not be empty", field)
    if len(values) > limit:
        raise _fail("TEMPORAL_LIMIT_EXCEEDED", f"{field} exceeds {limit} items", field)
    return tuple(sorted(values))


def _evidence_refs(raw: Any) -> tuple[str, ...]:
    values = set()
    for index, value in enumerate(_array(raw, "steward_evidence_refs")):
        ref = _path(value, f"steward_evidence_refs[{index}]", 512)
        if not (ref.startswith("sources/") or ref.endswith(".md")):
            raise _fail(
                "TEMPORAL_INVALID_FIELD",
                "steward_evidence_refs must reference a wiki page or sources/ path",
                "steward_evidence_refs",
            )
        values.add(ref)
    if not values:
        raise _fail("TEMPORAL_INVALID_FIELD", "steward_evidence_refs must not be empty", "steward_evidence_refs")
    if len(values) > 64:
        raise _fail("TEMPORAL_LIMIT_EXCEEDED", "steward_evidence_refs exceeds 64 items", "steward_evidence_refs")
    return tuple(sorted(values))


def _validate_decision(decision: str, relations: Mapping[str, tuple[str, ...]]) -> None:
    target_field = _DECISION_RELATION[decision]
    if target_field is None:
        if any(relations.values()):
            raise _fail("TEMPORAL_INVALID_FIELD", "accept revisions may not contain relation targets", "decision")
        return
    if not relations[target_field]:
        raise _fail("TEMPORAL_INVALID_FIELD", f"{decision} revisions require relation targets", target_field)
    for field, values in relations.items():
        if field != target_field and values:
            raise _fail("TEMPORAL_INVALID_FIELD", f"{decision} revisions may only contain {target_field}", field)


def _validate_relation_limit(relations: Mapping[str, tuple[str, ...]]) -> None:
    if sum(len(values) for values in relations.values()) > 128:
        raise _fail("TEMPORAL_LIMIT_EXCEEDED", "revision relation targets exceed 128 items", "revision")


def _relation_targets(revision: "TemporalClaimRevision") -> tuple[str, ...]:
    return tuple(
        target
        for field in _RELATION_FIELDS
        for target in getattr(revision, field)
    )


@dataclass(frozen=True)
class TemporalClaimRevision:
    contract_version: str
    revision_id: str
    claim_key: str
    claim_scope: str
    decision: str
    subject: EntityRef
    predicate: str
    object_ref: EntityRef
    world_validity: TimeInterval
    recorded_at: str
    candidate_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    retires_revision_ids: tuple[str, ...]
    supersedes_revision_ids: tuple[str, ...]
    contradicts_revision_ids: tuple[str, ...]
    qualification_of_revision_ids: tuple[str, ...]
    steward_evidence_refs: tuple[str, ...]
    authority: str

    def _identity(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "claim_key": self.claim_key,
            "claim_scope": self.claim_scope,
            "decision": self.decision,
            "subject": self.subject.to_dict(),
            "predicate": self.predicate,
            "object": self.object_ref.to_dict(),
            "world_validity": self.world_validity.to_dict(),
            "recorded_at": self.recorded_at,
            "candidate_ids": list(self.candidate_ids),
            "observation_ids": list(self.observation_ids),
            "retires_revision_ids": list(self.retires_revision_ids),
            "supersedes_revision_ids": list(self.supersedes_revision_ids),
            "contradicts_revision_ids": list(self.contradicts_revision_ids),
            "qualification_of_revision_ids": list(self.qualification_of_revision_ids),
            "steward_evidence_refs": list(self.steward_evidence_refs),
            "authority": self.authority,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TemporalClaimRevision":
        raw = _mapping(raw, "revision")
        _fields(raw, "revision")
        if raw["contract_version"] != _REVISION_VERSION:
            raise _fail("TEMPORAL_VERSION_UNSUPPORTED", "unsupported temporal claim revision contract version", "contract_version")
        if raw["authority"] != "target_wiki_steward":
            raise _fail("TEMPORAL_INVALID_FIELD", "revision authority is immutable", "authority")
        decision = raw["decision"]
        if decision not in _DECISIONS:
            raise _fail("TEMPORAL_INVALID_FIELD", "revision decision is unsupported", "decision")
        subject = EntityRef.from_mapping(raw["subject"])
        if subject.kind == "literal":
            raise _fail("TEMPORAL_INVALID_FIELD", "revision subject may not be literal", "subject")
        object_ref = EntityRef.from_mapping(raw["object"])
        predicate = _namespace(raw["predicate"], "predicate", 128)
        claim_scope = raw["claim_scope"] if raw["claim_scope"] == "default" else _namespace(raw["claim_scope"], "claim_scope", 256)
        claim_key = raw["claim_key"]
        if not isinstance(claim_key, str) or _HASH.fullmatch(claim_key) is None:
            raise _fail("TEMPORAL_INVALID_FIELD", "claim_key is invalid", "claim_key")
        expected_claim_key = build_temporal_claim_key(subject, predicate, claim_scope)
        if claim_key != expected_claim_key:
            raise _fail("TEMPORAL_ID_MISMATCH", "claim_key does not match canonical identity", "claim_key")
        revision = cls(
            _REVISION_VERSION,
            raw["revision_id"],
            claim_key,
            claim_scope,
            decision,
            subject,
            predicate,
            object_ref,
            TimeInterval.from_mapping(raw["world_validity"]),
            _recorded_at(raw["recorded_at"]),
            _ids(raw["candidate_ids"], "candidate_ids", _CANDIDATE_ID, required=True),
            _ids(raw["observation_ids"], "observation_ids", _OBSERVATION_ID, required=True),
            _ids(raw["retires_revision_ids"], "retires_revision_ids", _REVISION_ID),
            _ids(raw["supersedes_revision_ids"], "supersedes_revision_ids", _REVISION_ID),
            _ids(raw["contradicts_revision_ids"], "contradicts_revision_ids", _REVISION_ID),
            _ids(raw["qualification_of_revision_ids"], "qualification_of_revision_ids", _REVISION_ID),
            _evidence_refs(raw["steward_evidence_refs"]),
            "target_wiki_steward",
        )
        _validate_decision(
            decision,
            {field: getattr(revision, field) for field in _RELATION_FIELDS},
        )
        _validate_relation_limit({field: getattr(revision, field) for field in _RELATION_FIELDS})
        if not isinstance(revision.revision_id, str) or _REVISION_ID.fullmatch(revision.revision_id) is None:
            raise _fail("TEMPORAL_INVALID_FIELD", "revision_id is invalid", "revision_id")
        if _sha("temporal-revision", revision._identity()) != revision.revision_id:
            raise _fail("TEMPORAL_ID_MISMATCH", "revision_id does not match canonical identity", "revision_id")
        if len(_json_bytes(revision.to_dict())) > 65_536:
            raise _fail("TEMPORAL_LIMIT_EXCEEDED", "revision exceeds 65536 bytes", "revision")
        return revision

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._identity(),
            "revision_id": self.revision_id,
        }


def build_temporal_claim_revision(
    *,
    subject: Mapping[str, Any] | EntityRef,
    predicate: str,
    object_ref: Mapping[str, Any] | EntityRef,
    world_validity: Mapping[str, Any] | TimeInterval,
    recorded_at: str,
    candidate_ids: Sequence[str],
    observation_ids: Sequence[str],
    steward_evidence_refs: Sequence[str],
    decision: str = "accept",
    claim_scope: str = "default",
    retires_revision_ids: Sequence[str] = (),
    supersedes_revision_ids: Sequence[str] = (),
    contradicts_revision_ids: Sequence[str] = (),
    qualification_of_revision_ids: Sequence[str] = (),
    authority: str = "target_wiki_steward",
) -> TemporalClaimRevision:
    subject_ref = subject if isinstance(subject, EntityRef) else EntityRef.from_mapping(subject)
    object_value = object_ref.to_dict() if isinstance(object_ref, EntityRef) else object_ref
    interval = world_validity.to_dict() if isinstance(world_validity, TimeInterval) else world_validity
    # Normalize once through the field parser, then compute the immutable ID.
    candidate_ids_n = _ids(candidate_ids, "candidate_ids", _CANDIDATE_ID, required=True)
    observation_ids_n = _ids(observation_ids, "observation_ids", _OBSERVATION_ID, required=True)
    retire_n = _ids(retires_revision_ids, "retires_revision_ids", _REVISION_ID)
    supersede_n = _ids(supersedes_revision_ids, "supersedes_revision_ids", _REVISION_ID)
    contradict_n = _ids(contradicts_revision_ids, "contradicts_revision_ids", _REVISION_ID)
    qualify_n = _ids(qualification_of_revision_ids, "qualification_of_revision_ids", _REVISION_ID)
    decision = _text(decision, "decision", max_chars=32)
    if decision not in _DECISIONS:
        raise _fail("TEMPORAL_INVALID_FIELD", "revision decision is unsupported", "decision")
    predicate = _namespace(predicate, "predicate", 128)
    claim_scope = claim_scope if claim_scope == "default" else _namespace(claim_scope, "claim_scope", 256)
    subject_ref = EntityRef.from_mapping(subject_ref.to_dict())
    if subject_ref.kind == "literal":
        raise _fail("TEMPORAL_INVALID_FIELD", "revision subject may not be literal", "subject")
    interval_ref = TimeInterval.from_mapping(interval)
    evidence_n = _evidence_refs(steward_evidence_refs)
    if authority != "target_wiki_steward":
        raise _fail("TEMPORAL_INVALID_FIELD", "revision authority is immutable", "authority")
    revision = TemporalClaimRevision(
        _REVISION_VERSION,
        "",
        build_temporal_claim_key(subject_ref, predicate, claim_scope),
        claim_scope,
        decision,
        subject_ref,
        predicate,
        EntityRef.from_mapping(object_value),
        interval_ref,
        _recorded_at(recorded_at),
        candidate_ids_n,
        observation_ids_n,
        retire_n,
        supersede_n,
        contradict_n,
        qualify_n,
        evidence_n,
        "target_wiki_steward",
    )
    relations = {field: getattr(revision, field) for field in _RELATION_FIELDS}
    _validate_decision(decision, relations)
    _validate_relation_limit(relations)
    revision_id = _sha("temporal-revision", revision._identity())
    result = TemporalClaimRevision(
        revision.contract_version,
        revision_id,
        revision.claim_key,
        revision.claim_scope,
        revision.decision,
        revision.subject,
        revision.predicate,
        revision.object_ref,
        revision.world_validity,
        revision.recorded_at,
        revision.candidate_ids,
        revision.observation_ids,
        revision.retires_revision_ids,
        revision.supersedes_revision_ids,
        revision.contradicts_revision_ids,
        revision.qualification_of_revision_ids,
        revision.steward_evidence_refs,
        revision.authority,
    )
    if len(_json_bytes(result.to_dict())) > 65_536:
        raise _fail("TEMPORAL_LIMIT_EXCEEDED", "revision exceeds 65536 bytes", "revision")
    return result


def parse_temporal_claim_revision(raw: Mapping[str, Any]) -> TemporalClaimRevision:
    return TemporalClaimRevision.from_mapping(raw)


def _validate_history(revisions: Sequence[TemporalClaimRevision]) -> tuple[TemporalClaimRevision, ...]:
    if len(revisions) > 512:
        raise _fail("TEMPORAL_LIMIT_EXCEEDED", "page history exceeds 512 revisions", "temporal_claim_revisions")
    seen: set[str] = set()
    prior: dict[str, TemporalClaimRevision] = {}
    last_recorded: datetime | None = None
    for index, revision in enumerate(revisions):
        if revision.revision_id in seen:
            raise _fail("TEMPORAL_INVALID_FIELD", "revision IDs must be unique", f"temporal_claim_revisions[{index}]")
        seen.add(revision.revision_id)
        recorded = _instant(revision.recorded_at)
        if last_recorded is not None and recorded < last_recorded:
            raise _fail("TEMPORAL_INVALID_FIELD", "revision history must be append ordered by recorded_at", "temporal_claim_revisions")
        last_recorded = recorded
        for target in _relation_targets(revision):
            if target not in prior:
                raise _fail("TEMPORAL_INVALID_FIELD", "relation target must precede its revision", "temporal_claim_revisions")
            if revision.decision in {"retire", "supersede", "contradict"} and prior[target].claim_key != revision.claim_key:
                raise _fail("TEMPORAL_INVALID_FIELD", "close and contradiction targets must share claim_key", "temporal_claim_revisions")
        prior[revision.revision_id] = revision
    canonical = _json_bytes([revision.to_dict() for revision in revisions])
    if len(canonical) > 1_000_000:
        raise _fail("TEMPORAL_LIMIT_EXCEEDED", "page history exceeds 1000000 bytes", "temporal_claim_revisions")
    return tuple(revisions)


def parse_temporal_claim_revisions(frontmatter: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> tuple[TemporalClaimRevision, ...]:
    if isinstance(frontmatter, Mapping):
        raw = frontmatter["temporal_claim_revisions"] if "temporal_claim_revisions" in frontmatter else ()
    else:
        raw = frontmatter
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise _fail("TEMPORAL_INVALID_FIELD", "temporal_claim_revisions must be an array", "temporal_claim_revisions")
    revisions = tuple(parse_temporal_claim_revision(item) for item in raw)
    return _validate_history(revisions)


@dataclass(frozen=True)
class TemporalRevisionFold:
    known_at: str
    active_revision_ids: tuple[str, ...]
    retired_revision_ids: tuple[str, ...]
    superseded_revision_ids: tuple[str, ...]
    contested_revision_ids: tuple[str, ...]
    qualified_revision_ids: tuple[str, ...]
    complete_lineage_revision_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": _FOLD_VERSION,
            "known_at": self.known_at,
            "active_revision_ids": list(self.active_revision_ids),
            "retired_revision_ids": list(self.retired_revision_ids),
            "superseded_revision_ids": list(self.superseded_revision_ids),
            "contested_revision_ids": list(self.contested_revision_ids),
            "qualified_revision_ids": list(self.qualified_revision_ids),
            "complete_lineage_revision_ids": list(self.complete_lineage_revision_ids),
        }


def fold_temporal_claim_revisions(
    revisions: Sequence[TemporalClaimRevision | Mapping[str, Any]], *, known_at: str
) -> TemporalRevisionFold:
    normalized_known_at = _timestamp(known_at, "known_at")
    parsed = tuple(
        revision if isinstance(revision, TemporalClaimRevision) else parse_temporal_claim_revision(revision)
        for revision in revisions
    )
    if len(parsed) > 512:
        raise _fail("TEMPORAL_LIMIT_EXCEEDED", "page history exceeds 512 revisions", "temporal_claim_revisions")
    history = _validate_history(parsed)
    known_instant = _instant(normalized_known_at)
    visible = tuple(revision for revision in history if _instant(revision.recorded_at) <= known_instant)
    visible_ids = {revision.revision_id for revision in visible}
    retired = {
        target
        for revision in visible
        if revision.decision == "retire"
        for target in revision.retires_revision_ids
    }
    superseded = {
        target
        for revision in visible
        if revision.decision == "supersede"
        for target in revision.supersedes_revision_ids
    }
    established = {
        revision.revision_id
        for revision in visible
        if revision.decision in _ESTABLISHED_DECISIONS
    }
    active = established - retired - superseded
    contested = {
        revision.revision_id
        for revision in visible
        if revision.decision == "contradict"
    }
    contested.update(
        target
        for revision in visible
        if revision.decision == "contradict"
        for target in revision.contradicts_revision_ids
    )
    qualified = {
        revision.revision_id
        for revision in visible
        if revision.decision == "qualify"
    }
    result = TemporalRevisionFold(
        normalized_known_at,
        tuple(sorted(active)),
        tuple(sorted(retired & visible_ids)),
        tuple(sorted(superseded & visible_ids)),
        tuple(sorted(contested & visible_ids)),
        tuple(sorted(qualified)),
        tuple(sorted(visible_ids)),
    )
    if len(_json_bytes(result.to_dict())) > 1_000_000:
        raise _fail("TEMPORAL_LIMIT_EXCEEDED", "fold exceeds 1000000 bytes", "fold")
    return result


def _world_point_known(value: Any) -> datetime | None:
    if value.kind != "known":
        return None
    return _instant(value.value)


def _world_contains(revision: TemporalClaimRevision, world_at: str) -> bool:
    start = _world_point_known(revision.world_validity.from_)
    end_value = revision.world_validity.to
    end = _world_point_known(end_value)
    if start is None or (end_value.kind != "open" and end is None):
        return False
    point = _instant(_timestamp(world_at, "world_at"))
    return start <= point and (end is None or point < end)


def _world_intersects(revision: TemporalClaimRevision, start_at: str, end_at: str) -> bool:
    start = _world_point_known(revision.world_validity.from_)
    end_value = revision.world_validity.to
    end = _world_point_known(end_value)
    if start is None or (end_value.kind != "open" and end is None):
        return False
    query_start = _instant(_timestamp(start_at, "transition.from"))
    query_end = _instant(_timestamp(end_at, "transition.to"))
    return start < query_end and (end is None or query_start < end)


def eligible_temporal_revisions(
    revisions: Sequence[TemporalClaimRevision | Mapping[str, Any]], query: TemporalQuery
) -> tuple[TemporalClaimRevision, ...]:
    """Return only Steward-authored revisions eligible for a strict temporal view."""
    if not isinstance(query, TemporalQuery):
        raise _fail("TEMPORAL_INVALID_FIELD", "query must be a TemporalQuery", "query")
    original = tuple(revisions)
    trusted_inputs = all(isinstance(item, TemporalClaimRevision) for item in original)
    parsed = tuple(
        revision if isinstance(revision, TemporalClaimRevision) else parse_temporal_claim_revision(revision)
        for revision in original
    )
    if len(parsed) > 512:
        raise _fail("TEMPORAL_LIMIT_EXCEEDED", "page history exceeds 512 revisions", "temporal_claim_revisions")
    # Provider output has already crossed the strict frontmatter parser.  Avoid
    # re-serializing trusted immutable revisions on the hot eligibility path;
    # mappings still receive the full bounded history validation above.
    history = parsed if trusted_inputs else _validate_history(parsed)
    known_at = query.known_at or query.request_time
    if trusted_inputs:
        known_instant = _instant(_timestamp(known_at, "known_at"))
        visible = tuple(revision for revision in history if _instant(revision.recorded_at) <= known_instant)
        visible_ids = {revision.revision_id for revision in visible}
        retired = {
            target for revision in visible if revision.decision == "retire" for target in revision.retires_revision_ids
        }
        superseded = {
            target
            for revision in visible
            if revision.decision == "supersede"
            for target in revision.supersedes_revision_ids
        }
        established = {
            revision.revision_id for revision in visible if revision.decision in _ESTABLISHED_DECISIONS
        }
        active = established - retired - superseded
        contested = {
            revision.revision_id for revision in visible if revision.decision == "contradict"
        }
        contested.update(
            target
            for revision in visible
            if revision.decision == "contradict"
            for target in revision.contradicts_revision_ids
        )
        fold = TemporalRevisionFold(
            _timestamp(known_at, "known_at"),
            tuple(sorted(active)),
            tuple(sorted(retired & visible_ids)),
            tuple(sorted(superseded & visible_ids)),
            tuple(sorted(contested & visible_ids)),
            tuple(sorted(revision.revision_id for revision in visible if revision.decision == "qualify")),
            tuple(sorted(visible_ids)),
        )
    else:
        fold = fold_temporal_claim_revisions(history, known_at=known_at)
    by_id = {revision.revision_id: revision for revision in history if revision.revision_id in fold.complete_lineage_revision_ids}

    if query.view == "lineage":
        return tuple(by_id[revision_id] for revision_id in fold.complete_lineage_revision_ids)

    if query.view == "conflict":
        candidate_ids = fold.contested_revision_ids
        result = []
        for revision_id in candidate_ids:
            revision = by_id.get(revision_id)
            if revision is None or revision.decision not in _ESTABLISHED_DECISIONS:
                continue
            if query.world_at is not None and not _world_contains(revision, query.world_at):
                continue
            result.append(revision)
        return tuple(result)

    if query.view == "transition":
        transition = query.transition
        if transition is None:
            raise _fail("TEMPORAL_INVALID_FIELD", "transition view requires a transition range", "query.transition")
        result = []
        for revision in history:
            if revision.revision_id not in by_id or revision.decision not in _ESTABLISHED_DECISIONS:
                continue
            if _world_intersects(revision, transition.from_value, transition.to_value):
                result.append(revision)
        return tuple(result)

    if query.view == "historical":
        result = []
        for revision in history:
            if revision.revision_id not in by_id or revision.decision not in _ESTABLISHED_DECISIONS:
                continue
            if revision.revision_id in fold.contested_revision_ids:
                continue
            if query.world_at is not None and _world_contains(revision, query.world_at):
                result.append(revision)
        return tuple(result)

    result = []
    for revision_id in fold.active_revision_ids:
        revision = by_id.get(revision_id)
        if revision is None or revision.decision not in _ESTABLISHED_DECISIONS:
            continue
        if revision_id in fold.contested_revision_ids:
            continue
        if query.world_at is not None and _world_contains(revision, query.world_at):
            result.append(revision)
    return tuple(result)


def render_temporal_revision(
    revision: TemporalClaimRevision, *, view: str = "current", fold: TemporalRevisionFold | None = None
) -> str:
    """Render one deterministic, atomic temporal fact within the evidence bound."""
    if not isinstance(revision, TemporalClaimRevision):
        revision = parse_temporal_claim_revision(revision)
    contested = view == "conflict" or (fold is not None and revision.revision_id in fold.contested_revision_ids)
    status = "contested" if contested else {
        "retire": "retired",
        "supersede": "superseded",
        "qualify": "qualified",
    }.get(revision.decision, "settled")
    payload = {
        "claim_key": revision.claim_key,
        "revision_id": revision.revision_id,
        "decision": revision.decision,
        "status": status,
        "subject": revision.subject.to_dict(),
        "predicate": revision.predicate,
        "object": revision.object_ref.to_dict(),
        "world_validity": revision.world_validity.to_dict(),
        "recorded_at": revision.recorded_at,
        "steward_evidence_refs": revision.steward_evidence_refs,
        "authority": revision.authority,
        "view": view,
    }
    rendered = _json_bytes(payload)
    if len(rendered) <= 4_000:
        return rendered.decode("utf-8")
    bounded = dict(payload)
    bounded["object"] = {"kind": revision.object_ref.kind, "value": "[bounded]"}
    rendered = _json_bytes(bounded)
    if len(rendered) <= 4_000:
        return rendered.decode("utf-8")
    return rendered[:3_997].decode("utf-8", errors="ignore") + "..."


# Explicit aliases keep the selection boundary discoverable to providers and adapters.
filter_temporal_revisions = eligible_temporal_revisions
render_temporal_evidence = render_temporal_revision
