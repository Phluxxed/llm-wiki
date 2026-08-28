from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import re
from typing import Any, Mapping


CONTRACT_VERSION = "1"
SUPPORTED_CONTRACT_VERSIONS = {"1", "2", "3"}
TEMPORAL_VIEWS = {"current", "historical", "transition", "lineage", "conflict"}
_RFC3339_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NORMALIZED_REMOTE = re.compile(r"^[a-z0-9._-]+(?:/[a-z0-9._-]+)+$")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
STATE_VIEWS = {"current", "historical", "transition", "all"}
MAX_QUESTION_CHARS = 16_000
MAX_SEEDS = 32
MAX_WORKSPACE_REMOTES = 8
MAX_WORKSPACE_REMOTE_CHARS = 1_024
MAX_WORKSPACE_ALIAS_CHARS = 255
SERVER_MAX_BYTES = 1_000_000
SERVER_MAX_ITEMS = 512


class ContractError(ValueError):
    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True)
class CompileBudget:
    target_bytes: int = 48_000
    max_bytes: int = 192_000
    max_content_bytes: int | None = None
    target_items: int = 24
    max_items: int = 96
    max_estimated_tokens: int | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> CompileBudget:
        raw = raw or {}
        if not isinstance(raw, Mapping):
            raise _invalid("budget", "Budget must be an object")
        unknown = set(raw) - {
            "target_bytes",
            "max_bytes",
            "max_content_bytes",
            "target_items",
            "max_items",
            "max_estimated_tokens",
        }
        if unknown:
            field_name = f"budget.{sorted(unknown)[0]}"
            raise _invalid(field_name, "Unknown budget field")

        target_bytes = _positive_int(raw.get("target_bytes", 48_000), "budget.target_bytes")
        max_bytes = _positive_int(raw.get("max_bytes", 192_000), "budget.max_bytes")
        max_content_bytes = raw.get("max_content_bytes")
        if max_content_bytes is not None:
            max_content_bytes = _positive_int(max_content_bytes, "budget.max_content_bytes")
        target_items = _positive_int(raw.get("target_items", 24), "budget.target_items")
        max_items = _positive_int(raw.get("max_items", 96), "budget.max_items")
        estimated = raw.get("max_estimated_tokens")
        if estimated is not None:
            estimated = _positive_int(estimated, "budget.max_estimated_tokens")

        if target_bytes > max_bytes:
            raise _invalid("budget.target_bytes", "Target bytes cannot exceed maximum bytes")
        if target_items > max_items:
            raise _invalid("budget.target_items", "Target items cannot exceed maximum items")

        effective_max_bytes = min(max_bytes, SERVER_MAX_BYTES)
        effective_max_items = min(max_items, SERVER_MAX_ITEMS)
        return cls(
            target_bytes=min(target_bytes, effective_max_bytes),
            max_bytes=effective_max_bytes,
            max_content_bytes=max_content_bytes,
            target_items=min(target_items, effective_max_items),
            max_items=effective_max_items,
            max_estimated_tokens=estimated,
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "target_bytes": self.target_bytes,
            "max_bytes": self.max_bytes,
            "target_items": self.target_items,
            "max_items": self.max_items,
            "max_estimated_tokens": self.max_estimated_tokens,
        }
        if self.max_content_bytes is not None:
            result["max_content_bytes"] = self.max_content_bytes
        return result


@dataclass(frozen=True)
class TemporalTransition:
    from_value: str
    to_value: str

    @property
    def from_(self) -> str:
        return self.from_value

    @property
    def to(self) -> str:
        return self.to_value

    def to_dict(self) -> dict[str, str]:
        return {"from": self.from_value, "to": self.to_value}


@dataclass(frozen=True)
class TemporalQuery:
    view: str
    request_time: str
    world_at: str | None = None
    known_at: str | None = None
    transition: TemporalTransition | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TemporalQuery":
        return _parse_temporal_query(raw)

    @property
    def transition_from(self) -> str | None:
        return self.transition.from_value if self.transition is not None else None

    @property
    def transition_to(self) -> str | None:
        return self.transition.to_value if self.transition is not None else None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "view": self.view,
            "request_time": self.request_time,
        }
        if self.world_at is not None:
            result["world_at"] = self.world_at
        if self.known_at is not None:
            result["known_at"] = self.known_at
        if self.transition is not None:
            result["transition"] = self.transition.to_dict()
        return result


@dataclass(frozen=True)
class WorkspaceIdentity:
    """Portable, path-free observations supplied by a host adapter."""

    directory_alias: str | None = None
    remotes: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "WorkspaceIdentity":
        if not isinstance(raw, Mapping):
            raise _invalid("workspace_identity", "Workspace Identity must be an object")
        unknown = set(raw) - {"directory_alias", "remotes"}
        if unknown:
            raise _invalid(
                f"workspace_identity.{sorted(unknown)[0]}",
                "Unknown Workspace Identity field",
            )

        alias = raw.get("directory_alias")
        if alias is not None:
            if not isinstance(alias, str):
                raise _invalid(
                    "workspace_identity.directory_alias",
                    "Workspace directory alias must be a string",
                )
            alias = alias.strip()
            if not alias:
                raise _invalid(
                    "workspace_identity.directory_alias",
                    "Workspace directory alias must not be empty",
                )
            if len(alias) > MAX_WORKSPACE_ALIAS_CHARS:
                raise _invalid(
                    "workspace_identity.directory_alias",
                    f"Workspace directory alias exceeds {MAX_WORKSPACE_ALIAS_CHARS} characters",
                )
            if (
                "/" in alias
                or "\\" in alias
                or _CONTROL_CHARACTER.search(alias)
                or alias in {".", ".."}
            ):
                raise _invalid(
                    "workspace_identity.directory_alias",
                    "Workspace directory alias must be one path-free basename",
                )

        raw_remotes = raw.get("remotes", [])
        if not isinstance(raw_remotes, list):
            raise _invalid(
                "workspace_identity.remotes",
                "Workspace remotes must be a list",
            )
        remotes: list[str] = []
        seen: set[str] = set()
        for index, remote in enumerate(raw_remotes):
            field = f"workspace_identity.remotes[{index}]"
            if not isinstance(remote, str):
                raise _invalid(field, "Workspace remote must be a string")
            if remote != remote.strip():
                raise _invalid(field, "Workspace remote must be normalized")
            if len(remote) > MAX_WORKSPACE_REMOTE_CHARS:
                raise _invalid(
                    field,
                    f"Workspace remote exceeds {MAX_WORKSPACE_REMOTE_CHARS} characters",
                )
            if not remote or not remote.isascii() or _CONTROL_CHARACTER.search(remote):
                raise _invalid(field, "Workspace remote must be normalized ASCII host/path")
            if not _NORMALIZED_REMOTE.fullmatch(remote) or remote != remote.lower():
                raise _invalid(field, "Workspace remote must be normalized ASCII host/path")
            segments = remote.split("/")
            host = segments[0]
            host_labels = host.split(".")
            if (
                not host
                or any(
                    not label
                    or label[0] == "-"
                    or label[-1] == "-"
                    or not re.fullmatch(r"[a-z0-9-]+", label)
                    for label in host_labels
                )
            ):
                raise _invalid(field, "Workspace remote must contain one valid hostname")
            if any(segment in {"", ".", ".."} for segment in segments):
                raise _invalid(field, "Workspace remote contains an invalid path segment")
            if remote not in seen:
                seen.add(remote)
                remotes.append(remote)
        if len(remotes) > MAX_WORKSPACE_REMOTES:
            raise _invalid(
                "workspace_identity.remotes",
                f"At most {MAX_WORKSPACE_REMOTES} unique workspace remotes are allowed",
            )
        return cls(directory_alias=alias, remotes=tuple(remotes))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.directory_alias is not None:
            result["directory_alias"] = self.directory_alias
        result["remotes"] = list(self.remotes)
        return result


# A descriptive alias used by host adapters and tests.  The wire shape is the
# same object; keeping both names avoids coupling callers to one language's
# naming convention.
WorkspaceIdentityInput = WorkspaceIdentity


@dataclass(frozen=True)
class CompileRequest:
    alias: str
    question: str
    seeds: tuple[str, ...] = ()
    state_view: str = "current"
    budget: CompileBudget = field(default_factory=CompileBudget)
    contract_version: str = CONTRACT_VERSION
    temporal: TemporalQuery | None = None
    workspace_identity: WorkspaceIdentity | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> CompileRequest:
        if not isinstance(raw, Mapping):
            raise _invalid("request", "Compile request must be an object")
        version = _nonempty_string(raw.get("contract_version", CONTRACT_VERSION), "contract_version")
        if version not in SUPPORTED_CONTRACT_VERSIONS:
            raise ContractError(
                "CONTRACT_VERSION_UNSUPPORTED",
                "Compiler contract version is not supported",
                {"found": version, "supported": sorted(SUPPORTED_CONTRACT_VERSIONS)},
            )
        allowed = {"contract_version", "alias", "question", "seeds", "state_view", "budget"}
        if version in {"2", "3"}:
            allowed.add("temporal")
        if version == "3":
            allowed.add("workspace_identity")
        unknown = set(raw) - allowed
        if unknown:
            field_name = sorted(unknown)[0]
            raise _invalid(field_name, "Unknown request field")
        alias = _nonempty_string(raw.get("alias"), "alias")
        question = _nonempty_string(raw.get("question"), "question")
        if len(question) > MAX_QUESTION_CHARS:
            raise _invalid("question", f"Question exceeds {MAX_QUESTION_CHARS} characters")

        state_view = _nonempty_string(raw.get("state_view", "current"), "state_view")
        if state_view not in STATE_VIEWS:
            raise _invalid("state_view", "Unsupported state view", allowed=sorted(STATE_VIEWS))

        raw_seeds = raw.get("seeds", [])
        if not isinstance(raw_seeds, list) or any(not isinstance(seed, str) or not seed.strip() for seed in raw_seeds):
            raise _invalid("seeds", "Seeds must be a list of non-empty page references")
        seeds = tuple(dict.fromkeys(seed.strip() for seed in raw_seeds))
        if len(seeds) > MAX_SEEDS:
            raise _invalid("seeds", f"At most {MAX_SEEDS} seeds are allowed")

        temporal = (
            _parse_temporal_query(raw.get("temporal"))
            if version in {"2", "3"} and "temporal" in raw
            else None
        )
        workspace_identity = (
            WorkspaceIdentity.from_mapping(raw.get("workspace_identity"))
            if version == "3" and "workspace_identity" in raw
            else None
        )
        return cls(
            alias=alias,
            question=question,
            seeds=seeds,
            state_view=state_view,
            budget=CompileBudget.from_mapping(raw.get("budget")),
            contract_version=version,
            temporal=temporal,
            workspace_identity=workspace_identity,
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "contract_version": self.contract_version,
            "alias": self.alias,
            "question": self.question,
            "seeds": list(self.seeds),
            "state_view": self.state_view,
            "budget": self.budget.to_dict(),
        }
        if self.contract_version in {"2", "3"} and self.temporal is not None:
            result["temporal"] = self.temporal.to_dict()
        if self.contract_version == "3" and self.workspace_identity is not None:
            result["workspace_identity"] = self.workspace_identity.to_dict()
        return result


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    provider: str
    route: str
    page: str | None
    source: str | None
    locator: Mapping[str, Any]
    content: str
    roles: tuple[str, ...]
    authored_state: str
    derived_flags: tuple[str, ...]
    authority_signals: tuple[str, ...]
    selection_reasons: tuple[str, ...]
    byte_cost: int
    truncated: bool = False
    atomic: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "route": self.route,
            "page": self.page,
            "source": self.source,
            "locator": dict(self.locator),
            "content": self.content,
            "roles": list(self.roles),
            "authored_state": self.authored_state,
            "derived_flags": list(self.derived_flags),
            "authority_signals": list(self.authority_signals),
            "selection_reasons": list(self.selection_reasons),
            "byte_cost": self.byte_cost,
            "truncated": self.truncated,
            "atomic": self.atomic,
        }


@dataclass(frozen=True)
class Omission:
    candidate_id: str
    reason: str
    estimated_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "reason": self.reason,
            "estimated_bytes": self.estimated_bytes,
        }


@dataclass(frozen=True)
class Coverage:
    required_roles: tuple[str, ...]
    covered_roles: tuple[str, ...]
    uncovered_roles: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_roles": list(self.required_roles),
            "covered_roles": list(self.covered_roles),
            "uncovered_roles": list(self.uncovered_roles),
        }


@dataclass(frozen=True)
class BudgetUsage:
    target_bytes: int
    max_bytes: int
    target_items: int
    max_items: int
    evidence_bytes: int
    envelope_bytes: int
    items: int
    estimated_tokens: int
    target_exceeded_for_coverage: bool
    content_bytes: int = 0
    max_estimated_tokens: int | None = None
    max_content_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        limits = {
                "target_bytes": self.target_bytes,
                "max_bytes": self.max_bytes,
                "target_items": self.target_items,
                "max_items": self.max_items,
                "max_estimated_tokens": self.max_estimated_tokens,
            }
        result = {
            "limits": limits,
            "target_exceeded_for_coverage": self.target_exceeded_for_coverage,
            "evidence_bytes": self.evidence_bytes,
            "envelope_bytes": self.envelope_bytes,
            "items": self.items,
            "estimated_tokens": self.estimated_tokens,
        }
        if self.max_content_bytes is not None:
            limits["max_content_bytes"] = self.max_content_bytes
            result["content_bytes"] = self.content_bytes
        return result


@dataclass(frozen=True)
class StopState:
    reason: str
    sufficient: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "sufficient": self.sufficient, "detail": self.detail}


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    provider: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "provider": self.provider,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ResponseReporting:
    omissions_total: int
    omissions_returned: int
    diagnostics_total: int
    diagnostics_returned: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "omissions": {
                "total": self.omissions_total,
                "returned": self.omissions_returned,
            },
            "diagnostics": {
                "total": self.diagnostics_total,
                "returned": self.diagnostics_returned,
            },
        }


@dataclass(frozen=True)
class CompiledContext:
    alias: str
    schema_version: str
    runtime_contract: str
    question: str
    shapes: tuple[str, ...]
    state_view: str
    resolved_seeds: tuple[str, ...]
    evidence: tuple[EvidenceRecord, ...]
    omissions: tuple[Omission, ...]
    coverage: Coverage
    budget: BudgetUsage
    stop: StopState
    continuation: Mapping[str, Any] | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    reporting: ResponseReporting | None = None
    contract_version: str = CONTRACT_VERSION
    temporal: TemporalQuery | None = None
    project_resolution: Mapping[str, Any] | None = None
    project_scope: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        reporting = self.reporting or ResponseReporting(
            omissions_total=len(self.omissions),
            omissions_returned=len(self.omissions),
            diagnostics_total=len(self.diagnostics),
            diagnostics_returned=len(self.diagnostics),
        )
        query = {
            "question": self.question,
            "shapes": list(self.shapes),
            "state_view": self.state_view,
            "resolved_seeds": list(self.resolved_seeds),
        }
        if self.temporal is not None:
            query["temporal"] = self.temporal.to_dict()
        if self.contract_version == "3":
            query["project_resolution"] = dict(
                self.project_resolution or {"status": "not_requested"}
            )
            if self.project_scope is not None:
                query["project_scope"] = dict(self.project_scope)
        return {
            "kind": "compiled_context",
            "contract_version": self.contract_version,
            "wiki": {
                "alias": self.alias,
                "schema_version": self.schema_version,
                "runtime_contract": self.runtime_contract,
            },
            "query": query,
            "evidence": [item.to_dict() for item in self.evidence],
            "omissions": [item.to_dict() for item in self.omissions],
            "coverage": self.coverage.to_dict(),
            "budget": self.budget.to_dict(),
            "stop": self.stop.to_dict(),
            "continuation": dict(self.continuation) if self.continuation is not None else None,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "reporting": reporting.to_dict(),
        }


def _nonempty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(field_name, "Field must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _invalid(field_name, "Field must be a positive integer")
    return value


def _parse_instant(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(field_name, "Field must be an RFC 3339 instant")
    candidate = value.strip()
    if _RFC3339_INSTANT.fullmatch(candidate) is None:
        raise _invalid(field_name, "Field must be an RFC 3339 instant")
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise _invalid(field_name, "Field must be an RFC 3339 instant") from exc
    if parsed.tzinfo is None:
        raise _invalid(field_name, "Field must include a timezone")
    normalized = parsed.astimezone(timezone.utc).replace(microsecond=parsed.microsecond)
    return normalized.isoformat(timespec="microseconds" if normalized.microsecond else "seconds").replace("+00:00", "Z")


def _parse_world_point(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(field_name, "Field must be a known date or RFC 3339 instant")
    candidate = value.strip()
    if _ISO_DATE.fullmatch(candidate) is None:
        return _parse_instant(candidate, field_name)
    try:
        parsed_date = date.fromisoformat(candidate)
    except ValueError:
        return _parse_instant(candidate, field_name)
    return parsed_date.isoformat()


def _point_key(value: str) -> datetime:
    if len(value) == 10:
        return datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_temporal_query(raw: Any) -> TemporalQuery:
    if not isinstance(raw, Mapping):
        raise _invalid("temporal", "Temporal query must be an object")
    allowed = {"view", "request_time", "world_at", "known_at", "transition"}
    unknown = set(raw) - allowed
    if unknown:
        raise _invalid(f"temporal.{sorted(unknown)[0]}", "Unknown temporal field")
    view = _nonempty_string(raw.get("view"), "temporal.view")
    if view not in TEMPORAL_VIEWS:
        raise _invalid("temporal.view", "Unsupported temporal view", allowed=sorted(TEMPORAL_VIEWS))
    request_time = _parse_instant(raw.get("request_time"), "temporal.request_time")
    known_at = _parse_instant(raw["known_at"], "temporal.known_at") if "known_at" in raw else request_time
    world_at = _parse_world_point(raw["world_at"], "temporal.world_at") if "world_at" in raw else None
    transition = None
    if "transition" in raw:
        transition_raw = raw["transition"]
        if not isinstance(transition_raw, Mapping):
            raise _invalid("temporal.transition", "Transition must be an object")
        unknown_transition = set(transition_raw) - {"from", "to"}
        if unknown_transition:
            raise _invalid(f"temporal.transition.{sorted(unknown_transition)[0]}", "Unknown transition field")
        if "from" not in transition_raw or "to" not in transition_raw:
            raise _invalid("temporal.transition", "Transition requires from and to")
        from_value = _parse_world_point(transition_raw["from"], "temporal.transition.from")
        to_value = _parse_world_point(transition_raw["to"], "temporal.transition.to")
        if _point_key(from_value) >= _point_key(to_value):
            raise _invalid("temporal.transition", "Transition from must precede to")
        transition = TemporalTransition(from_value, to_value)

    if view == "historical" and world_at is None:
        raise _invalid("temporal.world_at", "Historical view requires world_at")
    if view == "transition":
        if world_at is not None:
            raise _invalid("temporal.world_at", "Transition view forbids world_at")
        if transition is None:
            raise _invalid("temporal.transition", "Transition view requires a transition range")
    elif transition is not None:
        raise _invalid("temporal.transition", "Transition range is only valid for transition view")
    if view == "current" and world_at is None:
        world_at = request_time
    return TemporalQuery(view, request_time, world_at, known_at, transition)


def _invalid(field_name: str, message: str, **details: Any) -> ContractError:
    return ContractError("INVALID_INPUT", message, {"field": field_name, **details})
