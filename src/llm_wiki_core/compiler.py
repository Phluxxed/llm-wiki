from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterable

from .config import (
    CURRENT_RUNTIME_CONTRACT,
    CompilerConfig,
    ContentConfig,
    StateConfig,
    StewardshipConfig,
    WikiConfig,
    inspect_wiki_config,
)
from .contracts import (
    BudgetUsage,
    CompileRequest,
    CompiledContext,
    ContractError,
    Diagnostic,
    EvidenceRecord,
    StopState,
)
from .documents import collect_pages
from .project import ProjectIndex, ProjectScope
from .providers import (
    FrontmatterProvider,
    GraphProvider,
    LociGraphProvider,
    LociProvider,
    Provider,
    ProviderContext,
    ProviderResult,
    SeedProvider,
    SourceProvider,
    TextProvider,
)
from .providers.base import CandidateEvidence
from .providers.local import TemporalProvider, _candidate, _whole_body
from .providers.utils import page_matches_question, question_terms
from .query_shape import classify_question, required_roles
from .selection import coverage, finalize_response_budget, select_candidates


_NON_DEGRADING_DIAGNOSTICS = {"LOCI_GRAPH_PATH_REJECTED"}


def compile_context(
    wiki_root: str | Path,
    request: CompileRequest,
    *,
    extra_providers: Iterable[Provider] = (),
) -> CompiledContext:
    root = Path(wiki_root).expanduser().resolve()
    if not root.is_dir():
        raise ContractError(
            "WIKI_NOT_FOUND",
            "Wiki root does not exist or is not a directory",
            {"path": str(root)},
        )
    config = _effective_config(root)
    loaded_pages = collect_pages(root, content=config.content)
    resolved_seeds = _resolve_seeds(request.seeds, loaded_pages)
    shapes = classify_question(request.question)
    roles = required_roles(shapes)

    project_index = ProjectIndex.from_pages(loaded_pages) if request.contract_version == "3" else None
    project_resolution = (
        project_index.resolve(request.workspace_identity)
        if project_index is not None
        else None
    )
    project_scope = ProjectScope()
    eligible_pages = loaded_pages
    scope_seeds: tuple[str, ...] = ()
    diagnostics: list[Diagnostic] = []
    if project_index is not None:
        diagnostics.extend(project_index.diagnostics)
        # Contract v3 never treats malformed project metadata as an implicit
        # Global Page.  Exact caller seeds remain the narrow escape hatch.
        eligible_pages = {
            path: page
            for path, page in loaded_pages.items()
            if path not in project_index.invalid_pages
        }
        eligible_pages.update(
            {
                path: loaded_pages[path]
                for path in resolved_seeds
                if path in loaded_pages
            }
        )
        if project_resolution is not None:
            if project_resolution.status == "ambiguous":
                diagnostics.append(
                    Diagnostic(
                        code="PROJECT_IDENTITY_AMBIGUOUS",
                        message="Workspace Identity matched more than one project",
                        provider="project",
                        details={
                            "matched_by": project_resolution.matched_by,
                            "candidate_count": project_resolution.candidate_count,
                            "candidates": [dict(item) for item in project_resolution.candidates],
                        },
                    )
                )
            elif (
                project_resolution.status == "matched"
                and request.workspace_identity is not None
                and project_index.alias_shadowed(request.workspace_identity, project_resolution)
            ):
                diagnostics.append(
                    Diagnostic(
                        code="PROJECT_ALIAS_SHADOWED",
                        message="Workspace directory alias names a different project than its remote",
                        provider="project",
                        details={
                            "directory_alias": request.workspace_identity.directory_alias,
                            "project_id": project_resolution.project_id,
                        },
                    )
                )

            # A missing identity keeps the v3 request compatible with the
            # existing broad retrieval path.  A supplied identity only scopes
            # a uniquely matched workspace; unknown/ambiguous workspaces do
            # not guess or filter a project.
            if (
                request.workspace_identity is not None
                and project_resolution.status == "matched"
            ):
                project_scope = project_index.active_scope(
                    loaded_pages,
                    project_resolution,
                    request.question,
                    resolved_seeds,
                )
                eligible_pages = project_index.eligible_pages(
                    loaded_pages,
                    project_scope.active_project_ids,
                    resolved_seeds,
                )
                scope_seeds = tuple(
                    project_index.anchors[project_id]
                    for project_id in project_scope.active_project_ids
                    if project_id in project_index.anchors
                )

    context = ProviderContext(
        root,
        config,
        request,
        eligible_pages,
        shapes,
        roles,
        resolved_seeds,
        scope_seeds,
    )

    graph_provider: Provider = (
        LociGraphProvider()
        if config.compiler.graph_backend == "loci"
        else GraphProvider()
    )
    built_in: tuple[Provider, ...] = (
        SeedProvider(),
        FrontmatterProvider(),
        TextProvider(),
        graph_provider,
        SourceProvider(),
        LociProvider(),
    )
    providers = tuple(provider for provider in built_in if provider.name in config.compiler.providers) + tuple(
        extra_providers
    )
    if request.temporal is not None:
        providers = providers + (TemporalProvider(),)
    candidates: list[CandidateEvidence] = []
    for provider in providers:
        try:
            output = provider.collect(context)
            if isinstance(output, ProviderResult):
                candidates.extend(output.candidates)
                diagnostics.extend(output.diagnostics)
            else:
                candidates.extend(output)
        except Exception as exc:
            diagnostics.append(
                Diagnostic(
                    code="PROVIDER_FAILED",
                    message="Candidate provider failed",
                    provider=provider.name,
                    details={"type": type(exc).__name__},
                )
            )

    project_fallback: CandidateEvidence | None = None
    if project_index is not None and project_resolution is not None:
        candidates = _annotate_project_candidates(
            candidates,
            project_index,
            project_scope,
            resolved_seeds,
        )
        if (
            request.workspace_identity is not None
            and project_resolution.status == "matched"
            and project_scope.anchor_page is not None
        ):
            anchor = loaded_pages.get(project_scope.anchor_page)
            if anchor is not None and project_scope.anchor_page in eligible_pages:
                project_fallback = _candidate(
                    anchor,
                    provider="project",
                    route="project_orientation_fallback",
                    locator=_whole_body(anchor),
                    content=anchor.body,
                    shapes=shapes,
                    signals=(
                        "project_orientation_fallback",
                        f"active_project:{project_resolution.project_id}",
                    ),
                )

    selected, omissions = select_candidates(candidates, request, roles)
    if (
        project_fallback is not None
        and not _has_question_derived_project_candidate(
            selected,
            project_resolution.project_id,
            eligible_pages,
            request.question,
            resolved_seeds,
        )
    ):
        # Select once without the fallback so a genuinely useful project
        # candidate keeps its normal precedence.  If selection discarded all
        # question-derived project evidence, retry with the mandatory
        # orientation candidate; hard item/content/byte limits still govern
        # whether that candidate can be returned.
        candidates.append(project_fallback)
        selected, omissions = select_candidates(candidates, request, roles)
    coverage_state = coverage(roles, selected)
    evidence_bytes = sum(item.byte_cost for item in selected)
    sufficient = not coverage_state.uncovered_roles
    provider_degraded = any(
        item.code not in _NON_DEGRADING_DIAGNOSTICS
        for item in diagnostics
    )
    byte_limited = any(item.reason == "byte_limit" for item in omissions)
    content_limited = any(item.reason == "content_byte_limit" for item in omissions)
    item_limited = any(item.reason == "item_limit" for item in omissions)
    if sufficient:
        stop_reason = "sufficient"
        stop_detail = "All required roles covered"
    elif not candidates:
        stop_reason = "provider_degraded" if provider_degraded else "no_evidence"
        stop_detail = "No candidate evidence was available"
    elif content_limited:
        stop_reason = "content_budget_exhausted"
        stop_detail = "Evidence content byte ceiling was reached before coverage was complete"
    elif byte_limited:
        stop_reason = "byte_budget_exhausted"
        stop_detail = "Hard byte ceiling was reached before coverage was complete"
    elif item_limited:
        stop_reason = "item_budget_exhausted"
        stop_detail = "Hard item ceiling was reached before coverage was complete"
    elif provider_degraded:
        stop_reason = "provider_degraded"
        stop_detail = "Provider degradation may have left required roles uncovered"
    else:
        stop_reason = "candidate_exhausted"
        stop_detail = "Available candidates did not cover every required role"

    hard_omissions = tuple(
        item.candidate_id
        for item in omissions
        if item.reason in {"byte_limit", "content_byte_limit", "item_limit"}
    )
    continuation = None
    if not sufficient and hard_omissions:
        continuation = {
            "reason": "hard_limit_reached",
            "uncovered_roles": list(coverage_state.uncovered_roles),
            "remaining_candidate_ids": list(hard_omissions),
        }

    response = CompiledContext(
        alias=request.alias,
        schema_version=config.schema_version,
        runtime_contract=config.runtime_contract,
        question=request.question,
        shapes=shapes,
        state_view=request.state_view,
        resolved_seeds=resolved_seeds,
        evidence=selected,
        omissions=omissions,
        coverage=coverage_state,
        budget=BudgetUsage(
            target_bytes=request.budget.target_bytes,
            max_bytes=request.budget.max_bytes,
            max_content_bytes=request.budget.max_content_bytes,
            target_items=request.budget.target_items,
            max_items=request.budget.max_items,
            evidence_bytes=evidence_bytes,
            content_bytes=sum(len(item.content.encode("utf-8")) for item in selected),
            envelope_bytes=0,
            items=len(selected),
            estimated_tokens=(evidence_bytes + 3) // 4,
            target_exceeded_for_coverage=(
                evidence_bytes > request.budget.target_bytes
                or len(selected) > request.budget.target_items
            ),
            max_estimated_tokens=request.budget.max_estimated_tokens,
        ),
        stop=StopState(stop_reason, sufficient, stop_detail),
        continuation=continuation,
        diagnostics=tuple(diagnostics),
        contract_version=request.contract_version,
        temporal=request.temporal,
        project_resolution=(
            project_resolution.to_dict()
            if project_resolution is not None
            else None
        ),
        project_scope=(
            project_scope.to_dict()
            if project_scope.active_project_ids
            else None
        ),
    )
    return finalize_response_budget(response)


def _effective_config(root: Path) -> WikiConfig:
    inspection = inspect_wiki_config(root)
    if inspection.config is not None:
        return inspection.config
    if inspection.status == "legacy_missing":
        return WikiConfig(
            schema_version="legacy",
            runtime_contract=CURRENT_RUNTIME_CONTRACT,
            profile="legacy",
            content=ContentConfig(),
            compiler=CompilerConfig(),
            state=StateConfig(),
            stewardship=StewardshipConfig(),
            raw={},
        )
    assert inspection.error is not None
    raise ContractError(inspection.error.code, inspection.error.message, inspection.error.details)


def _resolve_seeds(seeds: tuple[str, ...], pages: dict) -> tuple[str, ...]:
    resolved: list[str] = []
    by_title = {page.title.lower(): path for path, page in pages.items()}
    for seed in seeds:
        normalized = seed.replace("\\", "/")
        candidates = (normalized, normalized[2:] if normalized.startswith("./") else normalized)
        match = next((candidate for candidate in candidates if candidate in pages), None)
        if match is None:
            match = by_title.get(seed.lower())
        if match is None:
            suggestions = sorted(
                path for path, page in pages.items() if seed.lower() in path.lower() or seed.lower() in page.title.lower()
            )[:5]
            raise ContractError(
                "PAGE_NOT_FOUND",
                "Seed page was not found",
                {"seed": seed, "suggestions": suggestions},
            )
        if match not in resolved:
            resolved.append(match)
    return tuple(resolved)


def _annotate_project_candidates(
    candidates: list[CandidateEvidence],
    project_index: ProjectIndex,
    project_scope: ProjectScope,
    resolved_seeds: tuple[str, ...],
) -> list[CandidateEvidence]:
    """Attach deterministic project provenance after providers have collected."""

    active = set(project_scope.active_project_ids)
    workspace_project = project_scope.active_project_ids[0] if project_scope.active_project_ids else None
    anchor_ids = {
        page: project_id for project_id, page in project_index.anchors.items()
    }
    result: list[CandidateEvidence] = []
    for candidate in candidates:
        signals = list(candidate.selection_signals)
        project_ids: set[str] = set()
        if candidate.page is not None:
            project_ids.update(
                project_id
                for project_id in project_index.membership.get(candidate.page, ())
                if project_id in active
            )
            anchor_project = anchor_ids.get(candidate.page)
            if anchor_project in active:
                project_ids.add(anchor_project)
        for project_id in sorted(project_ids):
            signal = f"active_project:{project_id}"
            if signal not in signals:
                signals.append(signal)
        if (
            candidate.page in resolved_seeds
            and workspace_project is not None
            and any(project_id != workspace_project for project_id in project_ids)
            and "explicit_seed_cross_project" not in signals
        ):
            signals.append("explicit_seed_cross_project")
        result.append(replace(candidate, selection_signals=tuple(signals)))
    return result


def _has_question_derived_project_candidate(
    candidates: Iterable[CandidateEvidence | EvidenceRecord],
    workspace_project_id: str,
    eligible_pages: dict,
    question: str,
    resolved_seeds: tuple[str, ...],
) -> bool:
    """Return whether normal retrieval found query evidence for the workspace.

    Project scope seeds are provider discovery inputs, not query evidence.  In
    particular, legacy graph traversal can return a path merely because the
    compiler supplied the project anchor.  Providers mark that provenance so
    it cannot accidentally suppress the required orientation fallback.
    """

    active_signal = f"active_project:{workspace_project_id}"
    terms = question_terms(question)
    explicit = set(resolved_seeds)
    for candidate in candidates:
        if candidate.provider in {"seed", "project", "temporal"}:
            continue
        signals = getattr(candidate, "selection_signals", None)
        if signals is None:
            signals = candidate.selection_reasons
        if active_signal not in signals:
            continue
        if {
            "scope_seed_discovery",
            "explicit_seed_discovery",
        }.intersection(signals):
            continue

        # Source and other normal providers intentionally admit an exact seed
        # even when it has no lexical relation to the question.  Such output
        # retains explicit-seed semantics and is not query-derived evidence.
        if candidate.page in explicit:
            page = eligible_pages.get(candidate.page)
            if page is None or not page_matches_question(page, question, terms):
                continue

        # Legacy graph candidates without seed-discovery provenance are
        # question-derived by the provider's anchor/path contract.  This also
        # preserves multi-hop paths whose intermediate page does not itself
        # contain a query term.
        return True
    return False
