"""Strict, candidate-only temporal knowledge value contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import hashlib
import json
import math
import re
import unicodedata
from typing import Any


_NAMESPACE = re.compile(r"^[a-z][a-z0-9_.-]*(?::[a-z0-9][a-z0-9_.-]*)?$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_OBSERVATION_ID = re.compile(r"^temporal-observation:sha256:[0-9a-f]{64}$")
_CANDIDATE_ID = re.compile(r"^temporal-candidate:sha256:[0-9a-f]{64}$")
_PACKET_ID = re.compile(r"^temporal-candidate-packet:sha256:[0-9a-f]{64}$")
_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$"
)


class TemporalContractError(ValueError):
    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


def _fail(code: str, message: str, field: str | None = None, **details: Any) -> TemporalContractError:
    if field is not None:
        details = {"field": field, **details}
    return TemporalContractError(code, message, details)


def _mapping(raw: Any, field: str = "value") -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise _fail("TEMPORAL_INVALID_FIELD", f"{field} must be an object", field)
    return raw


def _fields(raw: Mapping[str, Any], required: set[str], allowed: set[str], field: str) -> None:
    missing = sorted(required - set(raw))
    if missing:
        raise _fail("TEMPORAL_INVALID_FIELD", f"{field} is missing required fields", field, missing=missing)
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise _fail("TEMPORAL_UNKNOWN_FIELD", f"{field} contains unknown fields", field, unknown=unknown)


def _text(value: Any, field: str, *, max_chars: int, nonempty: bool = True) -> str:
    if not isinstance(value, str):
        raise _fail("TEMPORAL_INVALID_FIELD", f"{field} must be a string", field)
    value = unicodedata.normalize("NFC", value.strip())
    if nonempty and not value:
        raise _fail("TEMPORAL_INVALID_FIELD", f"{field} must not be empty", field)
    if len(value) > max_chars:
        raise _fail("TEMPORAL_LIMIT_EXCEEDED", f"{field} exceeds its size limit", field, limit=max_chars)
    return value


def _namespace(value: Any, field: str, max_chars: int) -> str:
    value = _text(value, field, max_chars=max_chars)
    if _NAMESPACE.fullmatch(value) is None or value.lower() != value:
        raise _fail("TEMPORAL_INVALID_FIELD", f"{field} must be a lowercase namespaced string", field)
    return value


def _path(value: Any, field: str, max_chars: int) -> str:
    value = _text(value, field, max_chars=max_chars).replace("\\", "/")
    if "\x00" in value or value.startswith("/") or re.match(r"^[A-Za-z]:/", value):
        raise _fail("TEMPORAL_INVALID_FIELD", f"{field} must be a relative path", field)
    if value.startswith("./"):
        value = value[2:]
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise _fail("TEMPORAL_INVALID_FIELD", f"{field} contains unsafe path components", field)
    return value


def _canonical(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        return {unicodedata.normalize("NFC", str(k)): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise _fail("TEMPORAL_INVALID_FIELD", "non-finite numbers are not supported")
    return value


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            _canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, TemporalContractError):
            raise
        raise _fail("TEMPORAL_INVALID_FIELD", "value is not canonically serializable") from exc


def _timestamp(value: Any, field: str) -> str:
    value = _text(value, field, max_chars=64)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise _fail("TEMPORAL_INVALID_FIELD", f"{field} must be a valid date or timestamp", field) from exc
        return value
    if not _RFC3339.fullmatch(value):
        raise _fail("TEMPORAL_INVALID_FIELD", f"{field} must be a timezone-aware whole-second instant", field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _fail("TEMPORAL_INVALID_FIELD", f"{field} must be a valid timestamp", field) from exc
    if parsed.tzinfo is None:
        raise _fail("TEMPORAL_INVALID_FIELD", f"{field} must include a timezone", field)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _instant(value: str) -> datetime:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return datetime.combine(date.fromisoformat(value), time.min, tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class TimeValue:
    kind: str
    value: str | None = None
    reason: str | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TimeValue":
        if isinstance(raw, cls):
            return raw
        raw = _mapping(raw, "time")
        _fields(raw, {"kind"}, {"kind", "value", "reason"}, "time")
        kind = raw["kind"]
        if kind == "known":
            if "value" not in raw or "reason" in raw:
                raise _fail("TEMPORAL_INVALID_FIELD", "known time requires value and forbids reason", "time")
            return cls(kind, _timestamp(raw["value"], "time.value"))
        if kind == "open":
            if "value" in raw or "reason" in raw:
                raise _fail("TEMPORAL_INVALID_FIELD", "open time forbids value and reason", "time")
            return cls(kind)
        if kind == "unknown":
            if "reason" not in raw or "value" in raw:
                raise _fail("TEMPORAL_INVALID_FIELD", "unknown time requires reason and forbids value", "time")
            return cls(kind, reason=_text(raw["reason"], "time.reason", max_chars=512))
        raise _fail("TEMPORAL_INVALID_FIELD", "time.kind is unsupported", "time.kind")

    def to_dict(self) -> dict[str, Any]:
        if self.kind == "known":
            return {"kind": "known", "value": self.value}
        if self.kind == "open":
            return {"kind": "open"}
        if self.kind == "unknown":
            return {"kind": "unknown", "reason": self.reason}
        raise _fail("TEMPORAL_INVALID_FIELD", "time.kind is unsupported", "time.kind")


@dataclass(frozen=True)
class TimeInterval:
    from_value: TimeValue
    to_value: TimeValue

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TimeInterval":
        raw = _mapping(raw, "interval")
        _fields(raw, {"from", "to"}, {"from", "to"}, "interval")
        start = TimeValue.from_mapping(raw["from"])
        end = TimeValue.from_mapping(raw["to"])
        if start.kind == "open":
            raise _fail("TEMPORAL_INVALID_FIELD", "interval.from may not be open", "interval.from")
        if start.kind == "known" and end.kind == "known" and _instant(start.value) >= _instant(end.value):
            raise _fail("TEMPORAL_INVALID_FIELD", "interval.from must precede interval.to", "interval")
        return cls(start, end)

    @property
    def from_(self) -> TimeValue:
        return self.from_value

    @property
    def to(self) -> TimeValue:
        return self.to_value

    def to_dict(self) -> dict[str, Any]:
        return {"from": self.from_value.to_dict(), "to": self.to_value.to_dict()}


@dataclass(frozen=True)
class EntityRef:
    kind: str
    page: str | None = None
    namespace: str | None = None
    value: str | None = None
    datatype: str | None = None
    surface: str | None = None
    candidates: tuple[tuple["EntityRef", tuple[str, ...]], ...] = ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "EntityRef":
        raw = _mapping(raw, "entity")
        _fields(
            raw,
            {"kind"},
            {"kind", "page", "namespace", "value", "datatype", "surface", "candidates"},
            "entity",
        )
        kind = raw["kind"]
        if kind == "resolved_page":
            _fields(raw, {"kind", "page"}, {"kind", "page"}, "entity")
            return cls(kind, page=_path(raw["page"], "entity.page", 512))
        if kind == "external_id":
            _fields(raw, {"kind", "namespace", "value"}, {"kind", "namespace", "value"}, "entity")
            return cls(
                kind,
                namespace=_namespace(raw["namespace"], "entity.namespace", 64),
                value=_text(raw["value"], "entity.value", max_chars=512),
            )
        if kind == "literal":
            _fields(raw, {"kind", "datatype", "value"}, {"kind", "datatype", "value"}, "entity")
            return cls(
                kind,
                datatype=_namespace(raw["datatype"], "entity.datatype", 128),
                value=_text(raw["value"], "entity.value", max_chars=4096, nonempty=False),
            )
        if kind == "ambiguous":
            _fields(raw, {"kind", "surface", "candidates"}, {"kind", "surface", "candidates"}, "entity")
            surface = _text(raw["surface"], "entity.surface", max_chars=512)
            candidates = raw["candidates"]
            if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
                raise _fail("TEMPORAL_INVALID_FIELD", "entity.candidates must be an array", "entity.candidates")
            if not 1 <= len(candidates) <= 16:
                raise _fail("TEMPORAL_LIMIT_EXCEEDED", "entity.candidates must contain 1-16 items", "entity.candidates")
            parsed: list[tuple[EntityRef, tuple[str, ...]]] = []
            for index, candidate in enumerate(candidates):
                candidate = _mapping(candidate, f"entity.candidates[{index}]")
                _fields(candidate, {"ref", "observation_ids"}, {"ref", "observation_ids"}, f"entity.candidates[{index}]")
                ref = cls.from_mapping(candidate["ref"])
                if ref.kind not in {"resolved_page", "external_id"}:
                    raise _fail("TEMPORAL_INVALID_FIELD", "ambiguity candidates must be resolved refs", "entity.candidates")
                ids = _ids(candidate["observation_ids"], f"entity.candidates[{index}].observation_ids")
                if not 1 <= len(ids) <= 64:
                    raise _fail("TEMPORAL_LIMIT_EXCEEDED", "candidate evidence must contain 1-64 IDs", "entity.candidates")
                parsed.append((ref, ids))
            return cls(kind, surface=surface, candidates=tuple(parsed))
        raise _fail("TEMPORAL_INVALID_FIELD", "entity.kind is unsupported", "entity.kind")

    def to_dict(self) -> dict[str, Any]:
        if self.kind == "resolved_page":
            return {"kind": self.kind, "page": self.page}
        if self.kind == "external_id":
            return {"kind": self.kind, "namespace": self.namespace, "value": self.value}
        if self.kind == "literal":
            return {"kind": self.kind, "datatype": self.datatype, "value": self.value}
        if self.kind == "ambiguous":
            return {
                "kind": self.kind,
                "surface": self.surface,
                "candidates": [
                    {"ref": ref.to_dict(), "observation_ids": list(ids)} for ref, ids in self.candidates
                ],
            }
        raise _fail("TEMPORAL_INVALID_FIELD", "entity.kind is unsupported", "entity.kind")


def _unknowns(raw: Any, field: str, *, limit: int = 32) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise _fail("TEMPORAL_INVALID_FIELD", f"{field} must be an array", field)
    if len(raw) > limit:
        raise _fail("TEMPORAL_LIMIT_EXCEEDED", f"{field} exceeds its item limit", field, limit=limit)
    seen: set[str] = set()
    values: list[tuple[str, str]] = []
    for index, item in enumerate(raw):
        item = _mapping(item, f"{field}[{index}]")
        _fields(item, {"field", "reason"}, {"field", "reason"}, f"{field}[{index}]")
        name = _namespace(item["field"], f"{field}[{index}].field", 128)
        reason = _text(item["reason"], f"{field}[{index}].reason", max_chars=512)
        if name in seen:
            raise _fail("TEMPORAL_INVALID_FIELD", f"{field} fields must be unique", field)
        seen.add(name)
        values.append((name, reason))
    return tuple(sorted(values))


@dataclass(frozen=True)
class ObservationRef:
    contract_version: str
    observation_id: str
    source_kind: str
    source_ref: str
    locator: tuple[tuple[str, str | int], ...]
    content_hash: str
    payload_bytes: int
    input_type: str
    observed_at: str
    source_event_time: TimeValue
    retention: str
    unknowns: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ObservationRef":
        raw = _mapping(raw, "observation")
        allowed = {
            "contract_version",
            "observation_id",
            "source_kind",
            "source_ref",
            "locator",
            "content_hash",
            "payload_bytes",
            "input_type",
            "observed_at",
            "source_event_time",
            "retention",
            "unknowns",
        }
        _fields(raw, allowed, allowed, "observation")
        if raw["contract_version"] != "temporal-observation/1":
            raise _fail("TEMPORAL_VERSION_UNSUPPORTED", "unsupported observation contract version", "contract_version")
        observation_id = raw["observation_id"]
        if not isinstance(observation_id, str) or _OBSERVATION_ID.fullmatch(observation_id) is None:
            raise _fail("TEMPORAL_INVALID_FIELD", "observation_id is invalid", "observation_id")
        source_kind = _namespace(raw["source_kind"], "source_kind", 128)
        source_ref = _text(raw["source_ref"], "source_ref", max_chars=2048)
        locator = _locator(raw["locator"])
        content_hash = raw["content_hash"]
        if not isinstance(content_hash, str) or _HASH.fullmatch(content_hash) is None:
            raise _fail("TEMPORAL_INVALID_FIELD", "content_hash must be lowercase SHA-256", "content_hash")
        payload_bytes = raw["payload_bytes"]
        if isinstance(payload_bytes, bool) or not isinstance(payload_bytes, int) or not 0 <= payload_bytes <= 65536:
            raise _fail("TEMPORAL_INVALID_FIELD", "payload_bytes must be between 0 and 65536", "payload_bytes")
        input_type = _namespace(raw["input_type"], "input_type", 128)
        observed_at = _timestamp(raw["observed_at"], "observed_at")
        source_event_time = TimeValue.from_mapping(raw["source_event_time"])
        if source_event_time.kind == "open":
            raise _fail("TEMPORAL_INVALID_FIELD", "source_event_time may not be open", "source_event_time")
        retention = raw["retention"]
        if retention not in {"immutable_source", "steward_snapshot_required"}:
            raise _fail("TEMPORAL_INVALID_FIELD", "retention is unsupported", "retention")
        unknowns = _unknowns(raw["unknowns"], "unknowns")
        result = cls(
            "temporal-observation/1",
            observation_id,
            source_kind,
            source_ref,
            locator,
            content_hash,
            payload_bytes,
            input_type,
            observed_at,
            source_event_time,
            retention,
            unknowns,
        )
        if result._computed_id() != observation_id:
            raise _fail("TEMPORAL_ID_MISMATCH", "observation_id does not match canonical identity", "observation_id")
        result._check_size()
        return result

    def _identity(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "locator": dict(self.locator),
            "content_hash": self.content_hash,
            "input_type": self.input_type,
        }

    def _computed_id(self) -> str:
        return _sha("temporal-observation", self._identity())

    def _check_size(self) -> None:
        if len(_json_bytes(self.to_dict())) > 16384:
            raise _fail("TEMPORAL_LIMIT_EXCEEDED", "observation reference exceeds 16384 bytes", "observation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "observation_id": self.observation_id,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "locator": dict(self.locator),
            "content_hash": self.content_hash,
            "payload_bytes": self.payload_bytes,
            "input_type": self.input_type,
            "observed_at": self.observed_at,
            "source_event_time": self.source_event_time.to_dict(),
            "retention": self.retention,
            "unknowns": [{"field": name, "reason": reason} for name, reason in self.unknowns],
        }


def _locator(raw: Any) -> tuple[tuple[str, str | int], ...]:
    raw = _mapping(raw, "locator")
    if len(raw) > 16:
        raise _fail("TEMPORAL_LIMIT_EXCEEDED", "locator exceeds 16 keys", "locator")
    values: list[tuple[str, str | int]] = []
    for key, value in raw.items():
        if not isinstance(key, str):
            raise _fail("TEMPORAL_INVALID_FIELD", "locator keys must be strings", "locator")
        key = _text(key, "locator.key", max_chars=64)
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise _fail("TEMPORAL_INVALID_FIELD", "locator values must be strings or integers", "locator")
        if isinstance(value, str):
            value = _text(value, "locator.value", max_chars=1024, nonempty=False)
        elif value < 0:
            raise _fail("TEMPORAL_INVALID_FIELD", "locator integers must be non-negative", "locator")
        values.append((key, value))
    return tuple(sorted(values))


def build_observation_ref(
    *,
    source_kind: str,
    source_ref: str,
    locator: Mapping[str, str | int],
    input_type: str,
    observed_at: str,
    source_event_time: TimeValue | Mapping[str, Any],
    retention: str,
    payload: bytes | None = None,
    content_hash: str | None = None,
    payload_bytes: int | None = None,
    unknowns: Sequence[Mapping[str, str]] = (),
) -> ObservationRef:
    payload_form = payload is not None
    precomputed_form = content_hash is not None or payload_bytes is not None
    if payload_form == precomputed_form:
        raise _fail("TEMPORAL_INVALID_FIELD", "provide exactly one payload form", "payload")
    if payload_form:
        if not isinstance(payload, bytes):
            raise _fail("TEMPORAL_INVALID_FIELD", "payload must be bytes", "payload")
        if len(payload) > 65536:
            raise _fail("TEMPORAL_LIMIT_EXCEEDED", "payload exceeds 65536 bytes", "payload")
        content_hash = hashlib.sha256(payload).hexdigest()
        payload_bytes = len(payload)
    else:
        if not isinstance(content_hash, str) or _HASH.fullmatch(content_hash) is None:
            raise _fail("TEMPORAL_INVALID_FIELD", "content_hash must be lowercase SHA-256", "content_hash")
        if isinstance(payload_bytes, bool) or not isinstance(payload_bytes, int) or not 0 <= payload_bytes <= 65536:
            raise _fail("TEMPORAL_INVALID_FIELD", "payload_bytes must be between 0 and 65536", "payload_bytes")
    source_event = TimeValue.from_mapping(source_event_time)
    if source_event.kind == "open":
        raise _fail("TEMPORAL_INVALID_FIELD", "source_event_time may not be open", "source_event_time")
    source_kind = _namespace(source_kind, "source_kind", 128)
    source_ref = _text(source_ref, "source_ref", max_chars=2048)
    input_type = _namespace(input_type, "input_type", 128)
    retention = _text(retention, "retention", max_chars=64)
    if retention not in {"immutable_source", "steward_snapshot_required"}:
        raise _fail("TEMPORAL_INVALID_FIELD", "retention is unsupported", "retention")
    values = {
        "contract_version": "temporal-observation/1",
        "observation_id": "",
        "source_kind": source_kind,
        "source_ref": source_ref,
        "locator": _locator(locator),
        "content_hash": content_hash,
        "payload_bytes": payload_bytes,
        "input_type": input_type,
        "observed_at": _timestamp(observed_at, "observed_at"),
        "source_event_time": source_event,
        "retention": retention,
        "unknowns": _unknowns(unknowns, "unknowns"),
    }
    identity = {
        "contract_version": values["contract_version"],
        "source_kind": values["source_kind"],
        "source_ref": values["source_ref"],
        "locator": dict(values["locator"]),
        "content_hash": values["content_hash"],
        "input_type": values["input_type"],
    }
    values["observation_id"] = _sha("temporal-observation", identity)
    result = ObservationRef(**values)
    result._check_size()
    return result


def parse_observation_ref(raw: Mapping[str, Any]) -> ObservationRef:
    return ObservationRef.from_mapping(raw)


def _usage(raw: Mapping[str, Any] | None, field: str = "usage") -> Mapping[str, int | float]:
    if raw is None:
        return {"payload_bytes": 0, "model_calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_ms": 0}
    raw = _mapping(raw, field)
    required = {"payload_bytes", "model_calls", "input_tokens", "output_tokens", "latency_ms"}
    _fields(raw, required, required, field)
    values: dict[str, int | float] = {}
    for name in sorted(required):
        value = raw[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise _fail("TEMPORAL_INVALID_FIELD", f"{field}.{name} must be a finite non-negative number", f"{field}.{name}")
        if name != "latency_ms" and not isinstance(value, int):
            raise _fail("TEMPORAL_INVALID_FIELD", f"{field}.{name} must be an integer", f"{field}.{name}")
        values[name] = value
    return values


def _relations(raw: Any) -> tuple[tuple[str, str | None, tuple[str, ...]], ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise _fail("TEMPORAL_INVALID_FIELD", "proposed_relations must be an array", "proposed_relations")
    if len(raw) > 64:
        raise _fail("TEMPORAL_LIMIT_EXCEEDED", "proposed_relations exceeds 64 items", "proposed_relations")
    values: dict[bytes, tuple[str, str | None, tuple[str, ...]]] = {}
    allowed = {"duplicate", "contradict", "supersede", "qualify", "unresolved"}
    for index, item in enumerate(raw):
        item = _mapping(item, f"proposed_relations[{index}]")
        _fields(item, {"kind", "observation_ids"}, {"kind", "target_id", "observation_ids"}, f"proposed_relations[{index}]")
        kind = item["kind"]
        if kind not in allowed:
            raise _fail("TEMPORAL_INVALID_FIELD", "relation kind is unsupported", f"proposed_relations[{index}].kind")
        target = item.get("target_id")
        if kind == "unresolved":
            if target is not None:
                raise _fail("TEMPORAL_INVALID_FIELD", "unresolved relations forbid target_id", f"proposed_relations[{index}]")
        else:
            if not isinstance(target, str) or _CANDIDATE_ID.fullmatch(target) is None:
                raise _fail("TEMPORAL_INVALID_FIELD", "relation target_id is invalid", f"proposed_relations[{index}].target_id")
        ids = _ids(item["observation_ids"], f"proposed_relations[{index}].observation_ids")
        if not 1 <= len(ids) <= 64:
            raise _fail("TEMPORAL_LIMIT_EXCEEDED", "relation evidence must contain 1-64 IDs", "proposed_relations")
        normalized = (kind, target, ids)
        values[_json_bytes({"kind": kind, "target_id": target, "observation_ids": list(ids)})] = normalized
    return tuple(values[key] for key in sorted(values))


def _signals(raw: Any) -> tuple[tuple[str, tuple[str, ...], str | None], ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise _fail("TEMPORAL_INVALID_FIELD", "signals must be an array", "signals")
    if len(raw) > 64:
        raise _fail("TEMPORAL_LIMIT_EXCEEDED", "signals exceeds 64 items", "signals")
    values: dict[bytes, tuple[str, tuple[str, ...], str | None]] = {}
    for index, item in enumerate(raw):
        item = _mapping(item, f"signals[{index}]")
        _fields(item, {"kind", "observation_ids"}, {"kind", "observation_ids", "detail"}, f"signals[{index}]")
        kind = _namespace(item["kind"], f"signals[{index}].kind", 128)
        ids = _ids(item["observation_ids"], f"signals[{index}].observation_ids")
        if not 1 <= len(ids) <= 64:
            raise _fail("TEMPORAL_LIMIT_EXCEEDED", "signal evidence must contain 1-64 IDs", "signals")
        detail = None if "detail" not in item else _text(item["detail"], f"signals[{index}].detail", max_chars=1000)
        normalized = (kind, ids, detail)
        values[_json_bytes({"kind": kind, "observation_ids": list(ids), "detail": detail})] = normalized
    return tuple(values[key] for key in sorted(values))


@dataclass(frozen=True)
class TemporalFactCandidate:
    contract_version: str
    candidate_id: str
    claim_key: str
    claim_scope: str
    subject: EntityRef
    predicate: str
    object_ref: EntityRef
    proposed_world_validity: TimeInterval
    observed_at: str
    proposed_at: str
    supporting_observation_ids: tuple[str, ...]
    conflicting_observation_ids: tuple[str, ...]
    proposed_relations: tuple[tuple[str, str | None, tuple[str, ...]], ...]
    signals: tuple[tuple[str, tuple[str, ...], str | None], ...]
    unknowns: tuple[tuple[str, str], ...]
    usage: Mapping[str, int | float]

    @property
    def disposition(self) -> str:
        return "candidate_only"

    @property
    def mutation(self) -> dict[str, Any]:
        return {"allowed": False, "commands": []}

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TemporalFactCandidate":
        raw = _mapping(raw, "candidate")
        allowed = {
            "contract_version", "candidate_id", "claim_key", "claim_scope", "subject", "predicate", "object",
            "proposed_world_validity", "observed_at", "proposed_at", "supporting_observation_ids",
            "conflicting_observation_ids", "proposed_relations", "signals", "unknowns", "disposition", "mutation", "usage",
        }
        _fields(raw, allowed, allowed, "candidate")
        if raw["contract_version"] != "temporal-candidate/1":
            raise _fail("TEMPORAL_VERSION_UNSUPPORTED", "unsupported candidate contract version", "contract_version")
        if raw["disposition"] != "candidate_only" or raw["mutation"] != {"allowed": False, "commands": []}:
            raise _fail("TEMPORAL_INVALID_FIELD", "candidate authority fields are immutable", "candidate")
        subject = EntityRef.from_mapping(raw["subject"])
        if subject.kind == "literal":
            raise _fail("TEMPORAL_INVALID_FIELD", "candidate subject may not be literal", "subject")
        object_ref = EntityRef.from_mapping(raw["object"])
        supporting = _ids(raw["supporting_observation_ids"], "supporting_observation_ids")
        conflicting = _ids(raw["conflicting_observation_ids"], "conflicting_observation_ids")
        if not supporting or len(supporting) > 64 or len(conflicting) > 64:
            raise _fail("TEMPORAL_LIMIT_EXCEEDED", "observation links must contain at most 64 IDs", "candidate")
        if set(supporting) & set(conflicting):
            raise _fail("TEMPORAL_INVALID_FIELD", "supporting and conflicting IDs may not overlap", "candidate")
        predicate = _namespace(raw["predicate"], "predicate", 128)
        claim_scope = raw["claim_scope"]
        if claim_scope != "default":
            claim_scope = _namespace(claim_scope, "claim_scope", 256)
        interval = TimeInterval.from_mapping(raw["proposed_world_validity"])
        observed_at = _timestamp(raw["observed_at"], "observed_at")
        proposed_at = _timestamp(raw["proposed_at"], "proposed_at")
        if _instant(proposed_at) < _instant(observed_at):
            raise _fail("TEMPORAL_INVALID_FIELD", "proposed_at must not precede observed_at", "proposed_at")
        relations = _relations(raw["proposed_relations"])
        signals = _signals(raw["signals"])
        unknowns = _unknowns(raw["unknowns"], "unknowns")
        usage = _usage(raw["usage"])
        candidate_id = raw["candidate_id"]
        if not isinstance(candidate_id, str) or _CANDIDATE_ID.fullmatch(candidate_id) is None:
            raise _fail("TEMPORAL_INVALID_FIELD", "candidate_id is invalid", "candidate_id")
        claim_key = raw["claim_key"]
        if not isinstance(claim_key, str) or not claim_key.startswith("temporal-claim:sha256:") or _HASH.fullmatch(claim_key.rsplit(":", 1)[-1]) is None:
            raise _fail("TEMPORAL_INVALID_FIELD", "claim_key is invalid", "claim_key")
        result = cls(
            "temporal-candidate/1", candidate_id, claim_key, claim_scope, subject, predicate, object_ref, interval,
            observed_at, proposed_at, supporting, conflicting, relations, signals, unknowns, usage,
        )
        if result._claim_key() != claim_key:
            raise _fail("TEMPORAL_ID_MISMATCH", "claim_key does not match canonical identity", "claim_key")
        if result._candidate_id() != candidate_id:
            raise _fail("TEMPORAL_ID_MISMATCH", "candidate_id does not match canonical identity", "candidate_id")
        result._check_size()
        return result

    def _claim_body(self) -> dict[str, Any]:
        return {"contract_version": "temporal-claim-key/1", "subject": self.subject.to_dict(), "predicate": self.predicate, "claim_scope": self.claim_scope}

    def _claim_key(self) -> str:
        return build_temporal_claim_key(self.subject, self.predicate, self.claim_scope)

    def _identity(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "claim_key": self.claim_key,
            "claim_scope": self.claim_scope,
            "subject": self.subject.to_dict(),
            "predicate": self.predicate,
            "object": self.object_ref.to_dict(),
            "proposed_world_validity": self.proposed_world_validity.to_dict(),
            "supporting_observation_ids": list(self.supporting_observation_ids),
            "conflicting_observation_ids": list(self.conflicting_observation_ids),
            "proposed_relations": self._relations_dict(),
            "signals": self._signals_dict(),
            "unknowns": [{"field": name, "reason": reason} for name, reason in self.unknowns],
        }

    def _candidate_id(self) -> str:
        return _sha("temporal-candidate", self._identity())

    def _relations_dict(self) -> list[dict[str, Any]]:
        return [{"kind": kind, **({} if target is None else {"target_id": target}), "observation_ids": list(ids)} for kind, target, ids in self.proposed_relations]

    def _signals_dict(self) -> list[dict[str, Any]]:
        return [{"kind": kind, "observation_ids": list(ids), **({} if detail is None else {"detail": detail})} for kind, ids, detail in self.signals]

    def _check_size(self) -> None:
        if len(_json_bytes(self.to_dict())) > 65536:
            raise _fail("TEMPORAL_LIMIT_EXCEEDED", "candidate exceeds 65536 bytes", "candidate")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "candidate_id": self.candidate_id,
            "claim_key": self.claim_key,
            "claim_scope": self.claim_scope,
            "subject": self.subject.to_dict(),
            "predicate": self.predicate,
            "object": self.object_ref.to_dict(),
            "proposed_world_validity": self.proposed_world_validity.to_dict(),
            "observed_at": self.observed_at,
            "proposed_at": self.proposed_at,
            "supporting_observation_ids": list(self.supporting_observation_ids),
            "conflicting_observation_ids": list(self.conflicting_observation_ids),
            "proposed_relations": self._relations_dict(),
            "signals": self._signals_dict(),
            "unknowns": [{"field": name, "reason": reason} for name, reason in self.unknowns],
            "disposition": self.disposition,
            "mutation": self.mutation,
            "usage": dict(self.usage),
        }


def build_temporal_fact_candidate(
    *,
    subject: EntityRef,
    predicate: str,
    object_ref: EntityRef,
    proposed_world_validity: TimeInterval,
    observed_at: str,
    proposed_at: str,
    supporting_observation_ids: Sequence[str],
    claim_scope: str = "default",
    conflicting_observation_ids: Sequence[str] = (),
    proposed_relations: Sequence[Mapping[str, Any]] = (),
    signals: Sequence[Mapping[str, Any]] = (),
    unknowns: Sequence[Mapping[str, str]] = (),
    usage: Mapping[str, int | float] | None = None,
) -> TemporalFactCandidate:
    if not isinstance(subject, EntityRef):
        subject = EntityRef.from_mapping(subject)
    if not isinstance(object_ref, EntityRef):
        object_ref = EntityRef.from_mapping(object_ref)
    if subject.kind == "literal":
        raise _fail("TEMPORAL_INVALID_FIELD", "candidate subject may not be literal", "subject")
    if not isinstance(proposed_world_validity, TimeInterval):
        proposed_world_validity = TimeInterval.from_mapping(proposed_world_validity)
    predicate = _namespace(predicate, "predicate", 128)
    claim_scope = "default" if claim_scope is None else claim_scope
    if claim_scope != "default":
        claim_scope = _namespace(claim_scope, "claim_scope", 256)
    observed_at = _timestamp(observed_at, "observed_at")
    proposed_at = _timestamp(proposed_at, "proposed_at")
    if _instant(proposed_at) < _instant(observed_at):
        raise _fail("TEMPORAL_INVALID_FIELD", "proposed_at must not precede observed_at", "proposed_at")
    supporting = _ids(supporting_observation_ids, "supporting_observation_ids")
    conflicting = _ids(conflicting_observation_ids, "conflicting_observation_ids")
    if not supporting or len(supporting) > 64 or len(conflicting) > 64:
        raise _fail("TEMPORAL_LIMIT_EXCEEDED", "observation links must contain at most 64 IDs", "candidate")
    if set(supporting) & set(conflicting):
        raise _fail("TEMPORAL_INVALID_FIELD", "supporting and conflicting IDs may not overlap", "candidate")
    usage_values = _usage(usage)
    claim_key = build_temporal_claim_key(subject, predicate, claim_scope)
    result = TemporalFactCandidate(
        "temporal-candidate/1", "", claim_key, claim_scope, subject, predicate, object_ref, proposed_world_validity,
        observed_at, proposed_at, supporting, conflicting, _relations(proposed_relations), _signals(signals), _unknowns(unknowns, "unknowns"), usage_values,
    )
    result = TemporalFactCandidate(result.contract_version, result._candidate_id(), result.claim_key, result.claim_scope, result.subject, result.predicate, result.object_ref, result.proposed_world_validity, result.observed_at, result.proposed_at, result.supporting_observation_ids, result.conflicting_observation_ids, result.proposed_relations, result.signals, result.unknowns, result.usage)
    result._check_size()
    return result


def parse_temporal_fact_candidate(raw: Mapping[str, Any]) -> TemporalFactCandidate:
    return TemporalFactCandidate.from_mapping(raw)


def _sum_usage(candidates: Sequence[TemporalFactCandidate]) -> dict[str, int | float]:
    result: dict[str, int | float] = {"payload_bytes": 0, "model_calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_ms": 0}
    for candidate in candidates:
        for key in result:
            result[key] += candidate.usage[key]
    return result


@dataclass(frozen=True)
class TemporalCandidatePacket:
    kind: str
    contract_version: str
    packet_id: str
    alias: str
    generated_at: str
    status: str
    candidates: tuple[TemporalFactCandidate, ...]
    unknowns: tuple[tuple[str, str], ...]
    usage: Mapping[str, int | float]

    @property
    def disposition(self) -> str:
        return "candidate_only"

    @property
    def mutation(self) -> dict[str, Any]:
        return {"allowed": False, "commands": []}

    @property
    def stewardship(self) -> dict[str, str]:
        return {
            "decision": "review_required",
            "instruction": "Review candidates through the target wiki steward; this packet grants no mutation authority.",
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TemporalCandidatePacket":
        raw = _mapping(raw, "packet")
        allowed = {
            "kind", "contract_version", "packet_id", "wiki", "generated_at", "status", "candidates", "unknowns",
            "disposition", "mutation", "stewardship", "usage",
        }
        _fields(raw, allowed, allowed, "packet")
        if raw["kind"] != "temporal_candidate_packet":
            raise _fail("TEMPORAL_INVALID_FIELD", "packet.kind is unsupported", "kind")
        if raw["contract_version"] != "temporal-candidate-packet/1":
            raise _fail("TEMPORAL_VERSION_UNSUPPORTED", "unsupported packet contract version", "contract_version")
        packet_id = raw["packet_id"]
        if not isinstance(packet_id, str) or _PACKET_ID.fullmatch(packet_id) is None:
            raise _fail("TEMPORAL_INVALID_FIELD", "packet_id is invalid", "packet_id")
        wiki = _mapping(raw["wiki"], "wiki")
        _fields(wiki, {"alias"}, {"alias"}, "wiki")
        alias = _alias(wiki["alias"])
        generated_at = _timestamp(raw["generated_at"], "generated_at")
        candidates_raw = raw["candidates"]
        if not isinstance(candidates_raw, Sequence) or isinstance(candidates_raw, (str, bytes)):
            raise _fail("TEMPORAL_INVALID_FIELD", "candidates must be an array", "candidates")
        if len(candidates_raw) > 256:
            raise _fail("TEMPORAL_LIMIT_EXCEEDED", "packet contains more than 256 candidates", "candidates")
        candidates = _dedupe_candidates([TemporalFactCandidate.from_mapping(item) for item in candidates_raw])
        status = raw["status"]
        expected_status = "candidates_present" if candidates else "no_candidates_observed"
        if status != expected_status:
            raise _fail("TEMPORAL_INVALID_FIELD", "packet status does not match candidates", "status")
        unknowns = _unknowns(raw["unknowns"], "unknowns")
        usage = _usage(raw["usage"])
        if usage != _sum_usage(candidates):
            raise _fail("TEMPORAL_INVALID_FIELD", "packet usage must equal candidate usage sum", "usage")
        if raw["disposition"] != "candidate_only" or raw["mutation"] != {"allowed": False, "commands": []}:
            raise _fail("TEMPORAL_INVALID_FIELD", "packet authority fields are immutable", "packet")
        if raw["stewardship"] != {
            "decision": "review_required",
            "instruction": "Review candidates through the target wiki steward; this packet grants no mutation authority.",
        }:
            raise _fail("TEMPORAL_INVALID_FIELD", "packet stewardship is immutable", "stewardship")
        result = cls("temporal_candidate_packet", "temporal-candidate-packet/1", packet_id, alias, generated_at, status, candidates, unknowns, usage)
        if result._packet_id() != packet_id:
            raise _fail("TEMPORAL_ID_MISMATCH", "packet_id does not match canonical identity", "packet_id")
        result._check_size()
        return result

    def _identity(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "wiki": {"alias": self.alias},
            "candidate_ids": [candidate.candidate_id for candidate in self.candidates],
            "unknowns": [{"field": name, "reason": reason} for name, reason in self.unknowns],
        }

    def _packet_id(self) -> str:
        return _sha("temporal-candidate-packet", self._identity())

    def _check_size(self) -> None:
        if len(_json_bytes(self.to_dict())) > 1_000_000:
            raise _fail("TEMPORAL_LIMIT_EXCEEDED", "packet exceeds 1000000 bytes", "packet")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "contract_version": self.contract_version,
            "packet_id": self.packet_id,
            "wiki": {"alias": self.alias},
            "generated_at": self.generated_at,
            "status": self.status,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "unknowns": [{"field": name, "reason": reason} for name, reason in self.unknowns],
            "disposition": self.disposition,
            "mutation": self.mutation,
            "stewardship": self.stewardship,
            "usage": dict(self.usage),
        }


def _alias(value: Any) -> str:
    value = _text(value, "alias", max_chars=128)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is None:
        raise _fail("TEMPORAL_INVALID_FIELD", "alias contains unsupported characters", "alias")
    return value


def _dedupe_candidates(candidates: Sequence[TemporalFactCandidate]) -> tuple[TemporalFactCandidate, ...]:
    unique = {candidate.candidate_id: candidate for candidate in candidates}
    return tuple(unique[key] for key in sorted(unique))


def _build_temporal_candidate_packet(
    *, alias: str, candidates: Sequence[TemporalFactCandidate], generated_at: str, unknowns: Sequence[Mapping[str, str]] = ()
) -> TemporalCandidatePacket:
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        raise _fail("TEMPORAL_INVALID_FIELD", "candidates must be an array", "candidates")
    if len(candidates) > 256:
        raise _fail("TEMPORAL_LIMIT_EXCEEDED", "packet contains more than 256 candidates", "candidates")
    typed = []
    for candidate in candidates:
        if not isinstance(candidate, TemporalFactCandidate):
            raise _fail("TEMPORAL_INVALID_FIELD", "candidates must contain temporal facts", "candidates")
        typed.append(candidate)
    normalized = _dedupe_candidates(typed)
    values = {
        "kind": "temporal_candidate_packet",
        "contract_version": "temporal-candidate-packet/1",
        "packet_id": "",
        "alias": _alias(alias),
        "generated_at": _timestamp(generated_at, "generated_at"),
        "status": "candidates_present" if normalized else "no_candidates_observed",
        "candidates": normalized,
        "unknowns": _unknowns(unknowns, "unknowns"),
        "usage": _sum_usage(normalized),
    }
    result = TemporalCandidatePacket(**values)
    result = TemporalCandidatePacket(result.kind, result.contract_version, result._packet_id(), result.alias, result.generated_at, result.status, result.candidates, result.unknowns, result.usage)
    result._check_size()
    return result


def parse_temporal_candidate_packet(raw: Mapping[str, Any]) -> TemporalCandidatePacket:
    return TemporalCandidatePacket.from_mapping(raw)


def _ids(raw: Any, field: str) -> tuple[str, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise _fail("TEMPORAL_INVALID_FIELD", f"{field} must be an array", field)
    values: set[str] = set()
    for value in raw:
        if not isinstance(value, str) or _OBSERVATION_ID.fullmatch(value) is None:
            raise _fail("TEMPORAL_INVALID_FIELD", f"{field} contains an invalid observation ID", field)
        values.add(value)
    return tuple(sorted(values))


def _sha(prefix: str, body: Mapping[str, Any]) -> str:
    return f"{prefix}:sha256:{hashlib.sha256(_json_bytes(body)).hexdigest()}"


def build_temporal_claim_key(
    subject: EntityRef | Mapping[str, Any],
    predicate: str,
    claim_scope: str = "default",
) -> str:
    """Return the frozen WP-T1 identity for a subject/predicate/scope claim."""
    if not isinstance(subject, EntityRef):
        subject = EntityRef.from_mapping(subject)
    if subject.kind == "literal":
        raise _fail("TEMPORAL_INVALID_FIELD", "candidate subject may not be literal", "subject")
    predicate = _namespace(predicate, "predicate", 128)
    claim_scope = claim_scope if claim_scope == "default" else _namespace(claim_scope, "claim_scope", 256)
    return _sha(
        "temporal-claim",
        {
            "contract_version": "temporal-claim-key/1",
            "subject": subject.to_dict(),
            "predicate": predicate,
            "claim_scope": claim_scope,
        },
    )
