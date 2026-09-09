"""Discoverable input contracts for the read-only unified maintenance tool."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypeAliasType


JsonValue = TypeAliasType(
    "MaintenanceJsonValue",
    Union[
        str,
        int,
        float,
        bool,
        None,
        list["MaintenanceJsonValue"],
        dict[str, "MaintenanceJsonValue"],
    ],
)

SHA256_HEX = r"^[0-9a-f]{64}$"
RFC3339_WHOLE_SECOND = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$"
WholeSecondTimestamp = Annotated[
    str,
    Field(
        pattern=RFC3339_WHOLE_SECOND,
        description="Timezone-aware RFC3339 timestamp with whole-second precision.",
    ),
]


class StrictInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class KnownTimeInput(StrictInputModel):
    """A stated valid-time boundary: date or timezone-aware whole-second instant."""

    kind: Literal["known"]
    value: str = Field(
        description="ISO date or RFC3339 instant. Instants include a timezone and no fractional seconds."
    )


class OpenTimeInput(StrictInputModel):
    """An intentionally unbounded end of a validity interval."""

    kind: Literal["open"]


class UnknownTimeInput(StrictInputModel):
    """A time not established by the source; record why it is unknown."""

    kind: Literal["unknown"]
    reason: str


MaintenanceTime = Annotated[
    KnownTimeInput | OpenTimeInput | UnknownTimeInput,
    Field(discriminator="kind"),
]
KnownOrUnknownTime = Annotated[
    KnownTimeInput | UnknownTimeInput,
    Field(discriminator="kind"),
]


class MaintenanceInterval(StrictInputModel):
    """Claim validity interval; `from` cannot be open."""

    from_value: KnownOrUnknownTime = Field(alias="from")
    to: MaintenanceTime

    model_config = ConfigDict(extra="forbid", strict=True, validate_by_alias=True)


class ResolvedPageInput(StrictInputModel):
    kind: Literal["resolved_page"]
    page: str = Field(
        description="Relative wiki page path, for example `self-model/rules.md`."
    )


class ExternalIdInput(StrictInputModel):
    kind: Literal["external_id"]
    namespace: str
    value: str


class LiteralInput(StrictInputModel):
    kind: Literal["literal"]
    datatype: str
    value: str


ResolvedEntityInput = Annotated[
    ResolvedPageInput | ExternalIdInput,
    Field(discriminator="kind"),
]


class AmbiguousEntityCandidateInput(StrictInputModel):
    ref: ResolvedEntityInput
    observation_ids: list[str]


class AmbiguousEntityInput(StrictInputModel):
    kind: Literal["ambiguous"]
    surface: str
    candidates: list[AmbiguousEntityCandidateInput]


MaintenanceEntity = Annotated[
    ResolvedPageInput | ExternalIdInput | LiteralInput | AmbiguousEntityInput,
    Field(discriminator="kind"),
]


class MaintenanceSignalInput(StrictInputModel):
    kind: str
    detail: str | None = None


class MaintenanceUnknownInput(StrictInputModel):
    field: str
    reason: str


class MaintenanceClaimInput(StrictInputModel):
    """A candidate-only temporal claim; it never grants mutation authority."""

    subject: MaintenanceEntity
    predicate: str
    object: MaintenanceEntity
    claim_scope: str
    proposed_world_validity: MaintenanceInterval
    signals: list[MaintenanceSignalInput]
    unknowns: list[MaintenanceUnknownInput]


class MaintenanceEvidenceInput(StrictInputModel):
    ref: str
    kind: JsonValue | None = None
    content_hash: str | None = Field(
        default=None,
        pattern=SHA256_HEX,
        description="Optional SHA-256 digest as exactly 64 lowercase hexadecimal characters.",
    )
    note: JsonValue | None = None


class MaintenanceSourceInput(StrictInputModel):
    """Source identity for claim-free discovery or hygiene proposals."""

    source_kind: str
    source_ref: str
    content_hash: str = Field(
        pattern=SHA256_HEX,
        description="Required SHA-256 identity: exactly 64 lowercase hexadecimal characters.",
    )


class TemporalMaintenanceSourceInput(StrictInputModel):
    """Source observation required when submitting temporal claims."""

    source_kind: str
    source_ref: str
    content_hash: str | None = Field(
        default=None,
        pattern=SHA256_HEX,
        description=(
            "Optional SHA-256 digest of the exact UTF-8 bytes of `payload_text`. "
            "If omitted, the MCP boundary derives it from those bytes; if supplied, it is verified."
        ),
    )
    locator: dict[str, str | int] = Field(
        description="Object locating the observed source material."
    )
    input_type: str
    observed_at: WholeSecondTimestamp = Field(
        description="When this observation was recorded."
    )
    source_event_time: KnownOrUnknownTime = Field(
        description="When the source event occurred, or `{kind: 'unknown', reason: ...}`."
    )
    retention: Literal["immutable_source", "steward_snapshot_required"]
    payload_text: str = Field(
        description=(
            "Exact source payload whose UTF-8 bytes are hashed; maximum 65,536 bytes "
            "after UTF-8 encoding."
        ),
    )


UnifiedMaintenanceSourceInput = MaintenanceSourceInput | TemporalMaintenanceSourceInput
