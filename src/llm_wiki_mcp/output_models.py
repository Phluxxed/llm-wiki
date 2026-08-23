from __future__ import annotations

from typing import Annotated, ClassVar, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import TypeAliasType


JsonValue = TypeAliasType(
    "JsonValue",
    Union[str, int, float, bool, None, list["JsonValue"], dict[str, "JsonValue"]],
)


class StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WikiError(StrictOutputModel):
    code: str
    message: str
    details: dict[str, JsonValue]


class WikiErrorEnvelope(StrictOutputModel):
    error: WikiError


class SuccessOrErrorOutput(StrictOutputModel):
    """Object-root envelope for an exact success payload or the shared error."""

    error: WikiError | None = None
    success_fields: ClassVar[frozenset[str]] = frozenset()
    success_field_variants: ClassVar[tuple[frozenset[str], ...]] = ()

    @model_validator(mode="after")
    def require_success_or_error(self) -> "SuccessOrErrorOutput":
        fields = self.model_fields_set
        if "error" in fields:
            if self.error is None or fields != {"error"}:
                raise ValueError("error payload must contain only error")
        elif fields not in (self.success_field_variants or (self.success_fields,)):
            raise ValueError("success payload must contain every success field")
        return self


class WikiRecord(StrictOutputModel):
    alias: str
    path: str
    created_by: str
    registered_at: str


class WikiListOutput(SuccessOrErrorOutput):
    registry_home: str | None = None
    wikis: list[WikiRecord] | None = None
    success_fields = frozenset({"registry_home", "wikis"})


class WikiRegisterOutput(SuccessOrErrorOutput):
    alias: str | None = None
    path: str | None = None
    created_by: str | None = None
    registered_at: str | None = None
    warnings: list[str] | None = None
    success_fields = frozenset({"alias", "path", "created_by", "registered_at", "warnings"})


class WikiUnregisterOutput(SuccessOrErrorOutput):
    alias: str | None = None
    path: str | None = None
    created_by: str | None = None
    registered_at: str | None = None
    success_fields = frozenset({"alias", "path", "created_by", "registered_at"})


class RequiredWikiFiles(StrictOutputModel):
    wiki_agent_md: bool
    index_md: bool
    log_md: bool

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )

    wiki_agent_md: bool = Field(alias="wiki-agent.md")
    index_md: bool = Field(alias="index.md")
    log_md: bool = Field(alias="log.md")


class ContextTooling(StrictOutputModel):
    query: bool
    wiki_graph: bool

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )

    query: bool = Field(alias="scripts/query.py")
    wiki_graph: bool = Field(alias="scripts/wiki_graph.py")


class DoctorCompatibility(StrictOutputModel):
    status: Literal["compatible", "blocked", "migration_available", "incompatible"]
    blockers: list[str]


class DoctorConfig(StrictOutputModel):
    status: Literal[
        "legacy_missing",
        "compatible",
        "invalid",
        "unsupported_schema",
        "runtime_incompatible",
    ]
    schema_version: str | None = None
    runtime_contract: str | None = None
    profile: str | None = None
    graph_backend: Literal["loci", "legacy"] | None = None
    error: WikiError | None = None


class DoctorRuntime(StrictOutputModel):
    version: str
    schema_version: str
    contract: str


class DoctorScript(StrictOutputModel):
    status: Literal[
        "missing",
        "compatible_adapter",
        "canonical_legacy_copy",
        "supported_customization",
        "modified_unknown",
    ]
    sha256: str | None
    customizations: list[str] | None = None
    claimed_adapter_contract: str | None = None


class DoctorScripts(StrictOutputModel):
    query: DoctorScript
    wiki_graph: DoctorScript

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )

    query: DoctorScript = Field(alias="scripts/query.py")
    wiki_graph: DoctorScript = Field(alias="scripts/wiki_graph.py")


class DoctorAdapterRuntime(StrictOutputModel):
    status: Literal[
        "not_applicable", "external_runtime_required", "missing_runtime", "ready"
    ]
    failure: str | None = None
    exit_code: int | None = None
    detail: str | None = None
    python: str | None = None


class ProviderReady(StrictOutputModel):
    status: Literal["ready"]


class DoctorGraphProvider(StrictOutputModel):
    status: Literal["ready", "degraded", "disabled"]
    backend: Literal["loci", "legacy"]
    rollback_available: Literal[True]
    cache: Literal["external_read_only_mirror"] | None = None
    code: Literal["LOCI_MCP_UNAVAILABLE", "LOCI_MCP_CONFIG_MISSING"] | None = None
    transport: Literal["mcp_stdio"] | None = None
    freshness: Literal["not_checked", "checked_on_provider_use"] | None = None
    missing: list[str] | None = None


class DoctorLociProvider(StrictOutputModel):
    status: Literal["ready", "degraded", "disabled"]
    transport: Literal["mcp_stdio"]
    opt_out: Literal[True] | None = None
    code: Literal["LOCI_MCP_UNAVAILABLE", "LOCI_MCP_CONFIG_MISSING"] | None = None
    freshness: Literal["not_checked", "checked_on_provider_use"] | None = None
    missing: list[str] | None = None


class DoctorProviders(StrictOutputModel):
    seed: ProviderReady
    frontmatter: ProviderReady
    text: ProviderReady
    graph: DoctorGraphProvider
    source: ProviderReady
    loci: DoctorLociProvider


class DoctorMigration(StrictOutputModel):
    last_receipt: str | None
    verification: str


DOCTOR_SUCCESS_FIELDS = frozenset(
    {
        "alias", "path", "exists", "is_dir", "is_wiki", "required_files",
        "context_tooling", "warnings", "compatibility", "config", "runtime",
        "scripts", "adapter_runtime", "providers", "migration",
    }
)


class WikiDoctorOutput(SuccessOrErrorOutput):
    alias: str | None = None
    path: str | None = None
    exists: bool | None = None
    is_dir: bool | None = None
    is_wiki: bool | None = None
    required_files: RequiredWikiFiles | None = None
    context_tooling: ContextTooling | None = None
    warnings: list[str] | None = None
    compatibility: DoctorCompatibility | None = None
    config: DoctorConfig | None = None
    runtime: DoctorRuntime | None = None
    scripts: DoctorScripts | None = None
    adapter_runtime: DoctorAdapterRuntime | None = None
    providers: DoctorProviders | None = None
    migration: DoctorMigration | None = None
    success_fields = DOCTOR_SUCCESS_FIELDS


class WikiAgentManualOutput(SuccessOrErrorOutput):
    kind: Literal["wiki_agent_manual"] | None = None
    alias: str | None = None
    path: str | None = None
    operating_manual_path: Literal["wiki-agent.md"] | None = None
    operating_manual: str | None = None
    operating_manual_truncated: bool | None = None
    conventions_path: Literal["CONVENTIONS.md"] | None = None
    conventions: str | None = None
    conventions_truncated: bool | None = None
    must_follow: list[str] | None = None
    doctor: WikiDoctorOutput | None = None
    success_fields = frozenset(
        {
            "kind", "alias", "path", "operating_manual_path", "operating_manual",
            "operating_manual_truncated", "conventions_path", "conventions",
            "conventions_truncated", "must_follow", "doctor",
        }
    )


class PageRecord(StrictOutputModel):
    page: str
    title: str
    type: str
    category: str
    tags: list[str]
    source: str
    description: str


class HubPage(PageRecord):
    in_: int = Field(alias="in")
    out: int
    degree: int

    model_config = ConfigDict(extra="forbid", strict=True, validate_by_alias=True)


class OpenQuestion(StrictOutputModel):
    page: str
    title: str
    question: str


class OpenRisk(StrictOutputModel):
    page: str
    title: str
    kind: str | None = None
    risk: str
    likelihood: str
    impact: str
    mitigation: str
    status: str


class LogEntry(StrictOutputModel):
    date: str
    action: str
    detail: str


class WikiOverviewOutput(SuccessOrErrorOutput):
    kind: Literal["agent_overview"] | None = None
    page_count: int | None = None
    edge_count: int | None = None
    type_counts: dict[str, int] | None = None
    suggested_entry_pages: list[HubPage] | None = None
    orphans: list[PageRecord] | None = None
    open_questions: list[OpenQuestion] | None = None
    open_risks: list[OpenRisk] | None = None
    recent_log: list[LogEntry] | None = None
    success_fields = frozenset(
        {
            "kind", "page_count", "edge_count", "type_counts", "suggested_entry_pages",
            "orphans", "open_questions", "open_risks", "recent_log",
        }
    )


class QueryPage(StrictOutputModel):
    page: str
    title: str
    category: str
    status: str
    type: str
    tags: list[str]
    last_reviewed: str


class QueryRisk(StrictOutputModel):
    file: str
    kind: str | None = None
    risk: str
    likelihood: str
    impact: str
    status: str


class WikiQueryOutput(SuccessOrErrorOutput):
    kind: Literal["summary", "risks"] | None = None
    count: int | None = None
    pages: list[QueryPage] | None = None
    risks: list[QueryRisk] | None = None
    success_field_variants = (
        frozenset({"kind", "count", "pages"}),
        frozenset({"kind", "count", "risks"}),
    )

    @model_validator(mode="after")
    def require_matching_query_kind(self) -> "WikiQueryOutput":
        if "error" not in self.model_fields_set:
            expected_collection = "risks" if self.kind == "risks" else "pages"
            if expected_collection not in self.model_fields_set:
                raise ValueError("query payload does not match its kind")
        return self


class LinkedPage(PageRecord):
    edge_type: str
    weight: float


class WikiLinksOutput(SuccessOrErrorOutput):
    kind: Literal["links"] | None = None
    page: str | None = None
    title: str | None = None
    type: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    source: str | None = None
    description: str | None = None
    links: list[LinkedPage] | None = None
    success_fields = frozenset({"kind", *PageRecord.model_fields, "links"})


class WikiBacklinksOutput(SuccessOrErrorOutput):
    kind: Literal["backlinks"] | None = None
    page: str | None = None
    title: str | None = None
    type: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    source: str | None = None
    description: str | None = None
    backlinks: list[LinkedPage] | None = None
    success_fields = frozenset({"kind", *PageRecord.model_fields, "backlinks"})


class AroundPage(PageRecord):
    distance: int
    reasons: list[str]
    score: float


class WikiAroundOutput(SuccessOrErrorOutput):
    kind: Literal["around"] | None = None
    seed: PageRecord | None = None
    depth: int | None = None
    pages: list[AroundPage] | None = None
    success_fields = frozenset({"kind", "seed", "depth", "pages"})


class WikiGetPageOutput(SuccessOrErrorOutput):
    kind: Literal["page"] | None = None
    page: str | None = None
    title: str | None = None
    type: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    source: str | None = None
    description: str | None = None
    content: str | None = None
    success_fields = frozenset({"kind", *PageRecord.model_fields, "content"})


class WikiGetSourceExcerptOutput(SuccessOrErrorOutput):
    kind: Literal["source_excerpt"] | None = None
    source: str | None = None
    content: str | None = None
    success_fields = frozenset({"kind", "source", "content"})


class WikiGraphHealthOutput(SuccessOrErrorOutput):
    kind: Literal["graph_health"] | None = None
    page_count: int | None = None
    edge_count: int | None = None
    type_counts: dict[str, int] | None = None
    components: list[list[str]] | None = None
    hubs: list[HubPage] | None = None
    orphans: list[PageRecord] | None = None
    pages_without_source: list[PageRecord] | None = None
    success_fields = frozenset(
        {
            "kind", "page_count", "edge_count", "type_counts", "components",
            "hubs", "orphans", "pages_without_source",
        }
    )


# Context-pack and compiled-context contracts.


class ContextPackSeed(PageRecord):
    content: str


class ContextPackBudget(StrictOutputModel):
    requested_tokens: int
    approx_chars: int


class ContextPackIncludedPage(PageRecord):
    reasons: list[str]
    score: float
    content: str


class ContextPackSourceRef(StrictOutputModel):
    page: str
    source: str


class ContextPackSourceExcerpt(StrictOutputModel):
    source: str
    content: str


class ContextPackOpenQuestion(StrictOutputModel):
    page: str
    question: str


class ContextPackOpenRisk(StrictOutputModel):
    page: str
    kind: str | None = None
    risk: str
    likelihood: str
    impact: str
    mitigation: str
    status: str


class ContextPackGap(StrictOutputModel):
    page: str
    gap: str


class WikiContextPackOutput(SuccessOrErrorOutput):
    kind: Literal["context_pack"] | None = None
    seed: ContextPackSeed | None = None
    budget: ContextPackBudget | None = None
    included_pages: list[ContextPackIncludedPage] | None = None
    source_refs: list[ContextPackSourceRef] | None = None
    source_excerpts: list[ContextPackSourceExcerpt] | None = None
    open_questions: list[ContextPackOpenQuestion] | None = None
    open_risks: list[ContextPackOpenRisk] | None = None
    recent_log: list[LogEntry] | None = None
    gaps: list[ContextPackGap] | None = None
    success_fields = frozenset(
        {
            "kind", "seed", "budget", "included_pages", "source_refs",
            "source_excerpts", "open_questions", "open_risks", "recent_log", "gaps",
        }
    )


class CompiledWiki(StrictOutputModel):
    alias: str
    schema_version: str
    runtime_contract: str


class CompiledTemporalTransition(StrictOutputModel):
    from_value: str = Field(alias="from")
    to: str

    model_config = ConfigDict(extra="forbid", strict=True, validate_by_alias=True)


class CurrentTemporalQuery(StrictOutputModel):
    view: Literal["current"]
    request_time: str
    world_at: str
    known_at: str


class HistoricalTemporalQuery(StrictOutputModel):
    view: Literal["historical"]
    request_time: str
    world_at: str
    known_at: str


class TransitionTemporalQuery(StrictOutputModel):
    view: Literal["transition"]
    request_time: str
    known_at: str
    transition: CompiledTemporalTransition


class LineageTemporalQuery(StrictOutputModel):
    view: Literal["lineage"]
    request_time: str
    known_at: str
    world_at: str | None = None


class ConflictTemporalQuery(StrictOutputModel):
    view: Literal["conflict"]
    request_time: str
    known_at: str
    world_at: str | None = None


CompiledTemporalQuery = Annotated[
    CurrentTemporalQuery
    | HistoricalTemporalQuery
    | TransitionTemporalQuery
    | LineageTemporalQuery
    | ConflictTemporalQuery,
    Field(discriminator="view"),
]


class CompiledQuery(StrictOutputModel):
    question: str
    shapes: list[str]
    state_view: Literal["current", "historical", "transition", "all"]
    resolved_seeds: list[str]
    temporal: CompiledTemporalQuery | None = None


class CompiledEvidence(StrictOutputModel):
    id: str
    provider: str
    route: str
    page: str | None
    source: str | None
    locator: dict[str, JsonValue]
    content: str
    roles: list[str]
    authored_state: str
    derived_flags: list[str]
    authority_signals: list[str]
    selection_reasons: list[str]
    byte_cost: int
    truncated: bool
    atomic: bool


class CompiledOmission(StrictOutputModel):
    candidate_id: str
    reason: str
    estimated_bytes: int | None


class CompiledCoverage(StrictOutputModel):
    required_roles: list[str]
    covered_roles: list[str]
    uncovered_roles: list[str]


class CompiledBudgetLimits(StrictOutputModel):
    target_bytes: int
    max_bytes: int
    target_items: int
    max_items: int
    max_estimated_tokens: int | None


class CompiledBudgetUsage(StrictOutputModel):
    limits: CompiledBudgetLimits
    target_exceeded_for_coverage: bool
    evidence_bytes: int
    envelope_bytes: int
    items: int
    estimated_tokens: int


class CompiledStop(StrictOutputModel):
    reason: Literal[
        "sufficient",
        "byte_budget_exhausted",
        "item_budget_exhausted",
        "provider_degraded",
        "candidate_exhausted",
    ]
    sufficient: bool
    detail: str


class CompiledContinuation(StrictOutputModel):
    reason: Literal["hard_limit_reached"]
    uncovered_roles: list[str]
    remaining_candidate_ids: list[str]
    remaining_candidate_count: int | None = None


class CompiledDiagnostic(StrictOutputModel):
    code: str
    message: str
    provider: str | None
    details: dict[str, JsonValue]


class CompiledReportCount(StrictOutputModel):
    total: int
    returned: int


class CompiledReporting(StrictOutputModel):
    omissions: CompiledReportCount
    diagnostics: CompiledReportCount


class WikiCompileContextOutput(SuccessOrErrorOutput):
    kind: Literal["compiled_context"] | None = None
    contract_version: Literal["1", "2"] | None = None
    wiki: CompiledWiki | None = None
    query: CompiledQuery | None = None
    evidence: list[CompiledEvidence] | None = None
    omissions: list[CompiledOmission] | None = None
    coverage: CompiledCoverage | None = None
    budget: CompiledBudgetUsage | None = None
    stop: CompiledStop | None = None
    continuation: CompiledContinuation | None = None
    diagnostics: list[CompiledDiagnostic] | None = None
    reporting: CompiledReporting | None = None
    success_fields = frozenset(
        {
            "kind", "contract_version", "wiki", "query", "evidence", "omissions",
            "coverage", "budget", "stop", "continuation", "diagnostics", "reporting",
        }
    )

    @model_validator(mode="after")
    def require_temporal_contract_match(self) -> "WikiCompileContextOutput":
        if "error" not in self.model_fields_set and self.query is not None:
            has_temporal = "temporal" in self.query.model_fields_set
            if self.contract_version == "1" and has_temporal:
                raise ValueError("compiled context v1 forbids query.temporal")
        return self


# Read-only maintenance and temporal contract vocabulary.


class ReadOnlyMutation(StrictOutputModel):
    allowed: Literal[False]
    commands: Literal[[]]


class MaintenancePacketStewardship(StrictOutputModel):
    decision: Literal["review_required"]
    instruction: Literal[
        "Apply any accepted change through the target wiki steward and local manual."
    ]


class TemporalPacketStewardship(StrictOutputModel):
    decision: Literal["review_required"]
    instruction: Literal[
        "Review candidates through the target wiki steward; this packet grants no mutation authority."
    ]


class ReconciliationStewardship(StrictOutputModel):
    decision: Literal["review_required"]
    instruction: Literal[
        "Review reconciliation proposals through the target wiki steward; this result grants no mutation authority."
    ]


class TemporalProposalStewardship(StrictOutputModel):
    required: Literal[True]
    authority: Literal["target_wiki_steward"]


class TemporalUnknown(StrictOutputModel):
    field: str
    reason: str


class KnownTimeValue(StrictOutputModel):
    kind: Literal["known"]
    value: str


class OpenTimeValue(StrictOutputModel):
    kind: Literal["open"]


class UnknownTimeValue(StrictOutputModel):
    kind: Literal["unknown"]
    reason: str


TemporalTimeValue = Annotated[
    KnownTimeValue | OpenTimeValue | UnknownTimeValue,
    Field(discriminator="kind"),
]


class TemporalInterval(StrictOutputModel):
    from_value: KnownTimeValue | UnknownTimeValue = Field(alias="from")
    to: TemporalTimeValue

    model_config = ConfigDict(extra="forbid", strict=True, validate_by_alias=True)


class ResolvedPageEntity(StrictOutputModel):
    kind: Literal["resolved_page"]
    page: str


class ExternalIdEntity(StrictOutputModel):
    kind: Literal["external_id"]
    namespace: str
    value: str


class LiteralEntity(StrictOutputModel):
    kind: Literal["literal"]
    datatype: str
    value: str


ResolvedEntity = Annotated[
    ResolvedPageEntity | ExternalIdEntity,
    Field(discriminator="kind"),
]


class AmbiguousEntityCandidate(StrictOutputModel):
    ref: ResolvedEntity
    observation_ids: list[str]


class AmbiguousEntity(StrictOutputModel):
    kind: Literal["ambiguous"]
    surface: str
    candidates: list[AmbiguousEntityCandidate]


TemporalEntity = Annotated[
    ResolvedPageEntity | ExternalIdEntity | LiteralEntity | AmbiguousEntity,
    Field(discriminator="kind"),
]


class TemporalObservation(StrictOutputModel):
    contract_version: Literal["temporal-observation/1"]
    observation_id: str
    source_kind: str
    source_ref: str
    locator: dict[str, str | int]
    content_hash: str
    payload_bytes: int
    input_type: str
    observed_at: str
    source_event_time: KnownTimeValue | UnknownTimeValue
    retention: Literal["immutable_source", "steward_snapshot_required"]
    unknowns: list[TemporalUnknown]


class TemporalProposedRelationResolved(StrictOutputModel):
    kind: Literal["duplicate", "contradict", "supersede", "qualify"]
    target_id: str
    observation_ids: list[str]


class TemporalProposedRelationUnresolved(StrictOutputModel):
    kind: Literal["unresolved"]
    observation_ids: list[str]


TemporalProposedRelation = Annotated[
    TemporalProposedRelationResolved | TemporalProposedRelationUnresolved,
    Field(discriminator="kind"),
]


class TemporalSignal(StrictOutputModel):
    kind: str
    observation_ids: list[str]
    detail: str | None = None


class TemporalUsage(StrictOutputModel):
    payload_bytes: int
    model_calls: int
    input_tokens: int
    output_tokens: int
    latency_ms: int | float


class TemporalFactCandidate(StrictOutputModel):
    contract_version: Literal["temporal-candidate/1"]
    candidate_id: str
    claim_key: str
    claim_scope: str
    subject: TemporalEntity
    predicate: str
    object: TemporalEntity
    proposed_world_validity: TemporalInterval
    observed_at: str
    proposed_at: str
    supporting_observation_ids: list[str]
    conflicting_observation_ids: list[str]
    proposed_relations: list[TemporalProposedRelation]
    signals: list[TemporalSignal]
    unknowns: list[TemporalUnknown]
    disposition: Literal["candidate_only"]
    mutation: ReadOnlyMutation
    usage: TemporalUsage


class AliasOnlyWiki(StrictOutputModel):
    alias: str


class TemporalCandidatePacket(StrictOutputModel):
    kind: Literal["temporal_candidate_packet"]
    contract_version: Literal["temporal-candidate-packet/1"]
    packet_id: str
    wiki: AliasOnlyWiki
    generated_at: str
    status: Literal["candidates_present", "no_candidates_observed"]
    candidates: list[TemporalFactCandidate]
    unknowns: list[TemporalUnknown]
    disposition: Literal["candidate_only"]
    mutation: ReadOnlyMutation
    stewardship: TemporalPacketStewardship
    usage: TemporalUsage


class WikiBuildTemporalCandidatesOutput(SuccessOrErrorOutput):
    kind: Literal["temporal_candidate_proposal"] | None = None
    contract_version: Literal["temporal-candidate-proposal/1"] | None = None
    target_wiki: str | None = None
    observation: TemporalObservation | None = None
    packet: TemporalCandidatePacket | None = None
    disposition: Literal["candidate_only"] | None = None
    mutation: ReadOnlyMutation | None = None
    stewardship: TemporalProposalStewardship | None = None
    success_fields = frozenset(
        {
            "kind", "contract_version", "target_wiki", "observation", "packet",
            "disposition", "mutation", "stewardship",
        }
    )


MaintenanceCandidateKind = Literal[
    "durable_outcome",
    "explicit_contradiction",
    "source_gap",
    "stale_current_claim",
    "supersession_gap",
    "relationship_gap",
    "relationship_revision",
    "promotion_candidate",
    "repeated_retrieval_gap",
]


class MaintenanceDiscoveryEvidence(StrictOutputModel):
    page: str | None
    source: str | None
    locator: dict[str, JsonValue]
    content: str
    authored_state: str
    derived_flags: list[str]


class MaintenanceDiscoveryCandidate(StrictOutputModel):
    id: str
    kind: MaintenanceCandidateKind | Literal["runtime_drift"]
    page: str | None
    diagnostic: str
    review_question: str
    evidence: list[MaintenanceDiscoveryEvidence]
    disposition: Literal["candidate_only"]


class SemanticContradictionsUnknown(StrictOutputModel):
    kind: Literal["semantic_contradictions"]
    status: Literal["unsupported_without_semantic_review"]
    detail: str


class SemanticStalenessUnknown(StrictOutputModel):
    kind: Literal["semantic_staleness"]
    status: Literal["unsupported_without_semantic_review"]
    detail: str


class LiveSourceDriftUnknown(StrictOutputModel):
    kind: Literal["live_source_drift"]
    status: Literal["unsupported_without_source_refresh"]
    detail: str


MaintenanceUnknown = Annotated[
    SemanticContradictionsUnknown | SemanticStalenessUnknown | LiveSourceDriftUnknown,
    Field(discriminator="kind"),
]


class MaintenanceCandidatePacket(StrictOutputModel):
    kind: Literal["maintenance_candidate_packet"]
    contract_version: Literal["1"]
    wiki: AliasOnlyWiki
    as_of: str
    stale_after_days: int
    status: Literal["candidates_present", "no_candidates_observed"]
    candidates: list[MaintenanceDiscoveryCandidate]
    unknowns: list[MaintenanceUnknown]
    mutation: ReadOnlyMutation
    stewardship: MaintenancePacketStewardship


class WikiMaintenanceCandidatesOutput(SuccessOrErrorOutput):
    kind: Literal["maintenance_candidate_packet"] | None = None
    contract_version: Literal["1"] | None = None
    wiki: AliasOnlyWiki | None = None
    as_of: str | None = None
    stale_after_days: int | None = None
    status: Literal["candidates_present", "no_candidates_observed"] | None = None
    candidates: list[MaintenanceDiscoveryCandidate] | None = None
    unknowns: list[MaintenanceUnknown] | None = None
    mutation: ReadOnlyMutation | None = None
    stewardship: MaintenancePacketStewardship | None = None
    success_fields = frozenset(
        {
            "kind", "contract_version", "wiki", "as_of", "stale_after_days", "status",
            "candidates", "unknowns", "mutation", "stewardship",
        }
    )


class MaintenanceProposalEvidence(StrictOutputModel):
    ref: str
    note: str | None = None
    content_hash: str | None = None


class MaintenanceEligibility(StrictOutputModel):
    mode: Literal["first_observation", "recurrence_or_corroboration"]
    independent_evidence_count: int


class MaintenanceCandidateProposal(StrictOutputModel):
    contract_version: Literal["1"]
    id: str
    dedupe_key: str
    kind: MaintenanceCandidateKind
    target_wiki: str
    diagnostic: str
    review_question: str
    pages: list[str]
    evidence: list[MaintenanceProposalEvidence]
    signal: Literal["deterministic", "speculative"]
    eligibility: MaintenanceEligibility
    disposition: Literal["candidate_only"]
    mutation: ReadOnlyMutation


class WikiBuildMaintenanceCandidateOutput(SuccessOrErrorOutput):
    contract_version: Literal["1"] | None = None
    id: str | None = None
    dedupe_key: str | None = None
    kind: MaintenanceCandidateKind | None = None
    target_wiki: str | None = None
    diagnostic: str | None = None
    review_question: str | None = None
    pages: list[str] | None = None
    evidence: list[MaintenanceProposalEvidence] | None = None
    signal: Literal["deterministic", "speculative"] | None = None
    eligibility: MaintenanceEligibility | None = None
    disposition: Literal["candidate_only"] | None = None
    mutation: ReadOnlyMutation | None = None
    success_fields = frozenset(
        {
            "contract_version", "id", "dedupe_key", "kind", "target_wiki", "diagnostic",
            "review_question", "pages", "evidence", "signal", "eligibility",
            "disposition", "mutation",
        }
    )


class ReconciliationRelationBase(StrictOutputModel):
    contract_version: Literal["temporal-reconciliation-relation/1"]
    relation_id: str
    source_candidate_id: str
    observation_ids: list[str]
    unknowns: list[TemporalUnknown]
    disposition: Literal["candidate_only"]
    mutation: ReadOnlyMutation


class DuplicateReconciliationRelation(ReconciliationRelationBase):
    kind: Literal["duplicate"]
    target_candidate_id: str
    basis: Literal["exact_fact_and_evidence"]


class SupersedeReconciliationRelation(ReconciliationRelationBase):
    kind: Literal["supersede"]
    target_candidate_id: str
    basis: Literal["same_claim_later_world_start"]


class ContradictReconciliationRelation(ReconciliationRelationBase):
    kind: Literal["contradict"]
    target_candidate_id: str
    basis: Literal["same_claim_same_world_start"]


class QualifyReconciliationRelation(ReconciliationRelationBase):
    kind: Literal["qualify"]
    target_candidate_id: str
    basis: Literal["declared_qualification"]


class UnresolvedReconciliationRelation(ReconciliationRelationBase):
    kind: Literal["unresolved"]
    target_candidate_id: None
    basis: Literal[
        "ambiguous_identity",
        "incomplete_provenance",
        "unknown_world_start",
        "same_fact_different_interval",
        "declared_relation_unconfirmed",
        "declared_unresolved",
        "missing_target",
    ]


ReconciliationRelation = Annotated[
    DuplicateReconciliationRelation
    | SupersedeReconciliationRelation
    | ContradictReconciliationRelation
    | QualifyReconciliationRelation
    | UnresolvedReconciliationRelation,
    Field(discriminator="kind"),
]


class ReconciliationUsage(StrictOutputModel):
    candidate_count: int
    observation_count: int
    claim_group_count: int
    comparisons: int
    relation_count: int


class TemporalReconciliationResult(StrictOutputModel):
    kind: Literal["temporal_reconciliation_result"]
    contract_version: Literal["temporal-reconciliation/1"]
    reconciliation_id: str
    status: Literal[
        "relations_proposed", "unresolved_present", "no_relations_observed"
    ]
    candidate_ids: list[str]
    relations: list[ReconciliationRelation]
    unknowns: list[TemporalUnknown]
    usage: ReconciliationUsage
    disposition: Literal["candidate_only"]
    mutation: ReadOnlyMutation
    stewardship: ReconciliationStewardship


class WikiReconcileTemporalCandidatesOutput(SuccessOrErrorOutput):
    kind: Literal["temporal_reconciliation_result"] | None = None
    contract_version: Literal["temporal-reconciliation/1"] | None = None
    reconciliation_id: str | None = None
    status: Literal[
        "relations_proposed", "unresolved_present", "no_relations_observed"
    ] | None = None
    candidate_ids: list[str] | None = None
    relations: list[ReconciliationRelation] | None = None
    unknowns: list[TemporalUnknown] | None = None
    usage: ReconciliationUsage | None = None
    disposition: Literal["candidate_only"] | None = None
    mutation: ReadOnlyMutation | None = None
    stewardship: ReconciliationStewardship | None = None
    success_fields = frozenset(
        {
            "kind", "contract_version", "reconciliation_id", "status", "candidate_ids",
            "relations", "unknowns", "usage", "disposition", "mutation", "stewardship",
        }
    )


# Unified maintenance v1 preserves three exact producer families inside one envelope.


class UnifiedMaintenanceSource(StrictOutputModel):
    source_kind: str
    source_ref: str
    content_hash: str


class UnifiedMaintenanceEvidence(StrictOutputModel):
    ref: str
    kind: JsonValue | None = None
    content_hash: str | None = None
    note: JsonValue | None = None


class UnifiedMaintenanceClaim(StrictOutputModel):
    subject: JsonValue
    predicate: str
    object: JsonValue
    world_validity: JsonValue | None = None
    operation: JsonValue | None = None
    supersedes: JsonValue | None = None
    contradicts: JsonValue | None = None
    qualifies: JsonValue | None = None
    retire: JsonValue | None = None
    identity: JsonValue | None = None
    effective_at: JsonValue | None = None
    status: JsonValue | None = None
    relation: JsonValue | None = None
    text: JsonValue | None = None


class KnowledgeRevisionClassification(StrictOutputModel):
    change_class: Literal["knowledge_revision"]
    temporal_obligation: Literal["required"]
    reasons: Literal[["claim_present"]]


class WikiHygieneClassification(StrictOutputModel):
    change_class: Literal["wiki_hygiene"]
    temporal_obligation: Literal["not_applicable"]
    reasons: Literal[["non_semantic_maintenance"]]


class NoChangeClassification(StrictOutputModel):
    change_class: Literal["no_change"]
    temporal_obligation: Literal["not_applicable"]
    reasons: Literal[["no_applicable_change"]]


UnifiedMaintenanceClassification = Annotated[
    KnowledgeRevisionClassification | WikiHygieneClassification | NoChangeClassification,
    Field(discriminator="change_class"),
]


class UnifiedMaintenanceUnknown(StrictOutputModel):
    kind: Literal["world_time", "identity"]
    status: Literal["unknown", "ambiguous"]
    detail: str


class WikiBuildMaintenanceOutput(SuccessOrErrorOutput):
    schema_version: Literal["unified-maintenance/1"] | None = None
    proposal_id: str | None = None
    target_wiki: str | None = None
    source: UnifiedMaintenanceSource | None = None
    classification: UnifiedMaintenanceClassification | None = None
    observations: list[TemporalObservation | UnifiedMaintenanceEvidence] | None = None
    candidates: list[
        TemporalFactCandidate | MaintenanceCandidateProposal | UnifiedMaintenanceClaim
    ] | None = None
    reconciliation: TemporalReconciliationResult | MaintenanceCandidatePacket | None = None
    affected_pages: list[str] | None = None
    unknowns: list[TemporalUnknown | UnifiedMaintenanceUnknown] | None = None
    disposition: Literal["candidate_only"] | None = None
    mutation: ReadOnlyMutation | None = None
    authority: Literal["target_wiki_steward"] | None = None
    success_fields = frozenset(
        {
            "schema_version", "proposal_id", "target_wiki", "source", "classification",
            "observations", "candidates", "reconciliation", "affected_pages", "unknowns",
            "disposition", "mutation", "authority",
        }
    )
