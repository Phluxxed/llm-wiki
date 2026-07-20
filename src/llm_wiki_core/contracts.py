from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


CONTRACT_VERSION = "1"
STATE_VIEWS = {"current", "historical", "transition", "all"}
MAX_QUESTION_CHARS = 16_000
MAX_SEEDS = 32
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
            "target_items",
            "max_items",
            "max_estimated_tokens",
        }
        if unknown:
            field_name = f"budget.{sorted(unknown)[0]}"
            raise _invalid(field_name, "Unknown budget field")

        target_bytes = _positive_int(raw.get("target_bytes", 48_000), "budget.target_bytes")
        max_bytes = _positive_int(raw.get("max_bytes", 192_000), "budget.max_bytes")
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
            target_items=min(target_items, effective_max_items),
            max_items=effective_max_items,
            max_estimated_tokens=estimated,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_bytes": self.target_bytes,
            "max_bytes": self.max_bytes,
            "target_items": self.target_items,
            "max_items": self.max_items,
            "max_estimated_tokens": self.max_estimated_tokens,
        }


@dataclass(frozen=True)
class CompileRequest:
    alias: str
    question: str
    seeds: tuple[str, ...] = ()
    state_view: str = "current"
    budget: CompileBudget = field(default_factory=CompileBudget)
    contract_version: str = CONTRACT_VERSION

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> CompileRequest:
        if not isinstance(raw, Mapping):
            raise _invalid("request", "Compile request must be an object")
        unknown = set(raw) - {"contract_version", "alias", "question", "seeds", "state_view", "budget"}
        if unknown:
            field_name = sorted(unknown)[0]
            raise _invalid(field_name, "Unknown request field")

        version = _nonempty_string(raw.get("contract_version", CONTRACT_VERSION), "contract_version")
        if version != CONTRACT_VERSION:
            raise ContractError(
                "CONTRACT_VERSION_UNSUPPORTED",
                "Compiler contract version is not supported",
                {"found": version, "supported": CONTRACT_VERSION},
            )
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

        return cls(
            alias=alias,
            question=question,
            seeds=seeds,
            state_view=state_view,
            budget=CompileBudget.from_mapping(raw.get("budget")),
            contract_version=version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "alias": self.alias,
            "question": self.question,
            "seeds": list(self.seeds),
            "state_view": self.state_view,
            "budget": self.budget.to_dict(),
        }


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
    max_estimated_tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "limits": {
                "target_bytes": self.target_bytes,
                "max_bytes": self.max_bytes,
                "target_items": self.target_items,
                "max_items": self.max_items,
                "max_estimated_tokens": self.max_estimated_tokens,
            },
            "target_exceeded_for_coverage": self.target_exceeded_for_coverage,
            "evidence_bytes": self.evidence_bytes,
            "envelope_bytes": self.envelope_bytes,
            "items": self.items,
            "estimated_tokens": self.estimated_tokens,
        }


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

    def to_dict(self) -> dict[str, Any]:
        reporting = self.reporting or ResponseReporting(
            omissions_total=len(self.omissions),
            omissions_returned=len(self.omissions),
            diagnostics_total=len(self.diagnostics),
            diagnostics_returned=len(self.diagnostics),
        )
        return {
            "kind": "compiled_context",
            "contract_version": self.contract_version,
            "wiki": {
                "alias": self.alias,
                "schema_version": self.schema_version,
                "runtime_contract": self.runtime_contract,
            },
            "query": {
                "question": self.question,
                "shapes": list(self.shapes),
                "state_view": self.state_view,
                "resolved_seeds": list(self.resolved_seeds),
            },
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


def _invalid(field_name: str, message: str, **details: Any) -> ContractError:
    return ContractError("INVALID_INPUT", message, {"field": field_name, **details})
