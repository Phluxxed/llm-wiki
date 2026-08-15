from __future__ import annotations

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
    StopState,
)
from .documents import collect_pages
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
from .providers.local import TemporalProvider
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
    pages = collect_pages(root, content=config.content)
    resolved_seeds = _resolve_seeds(request.seeds, pages)
    shapes = classify_question(request.question)
    roles = required_roles(shapes)
    context = ProviderContext(root, config, request, pages, shapes, roles, resolved_seeds)

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
    diagnostics: list[Diagnostic] = []
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

    selected, omissions = select_candidates(candidates, request, roles)
    coverage_state = coverage(roles, selected)
    evidence_bytes = sum(item.byte_cost for item in selected)
    sufficient = not coverage_state.uncovered_roles
    provider_degraded = any(
        item.code not in _NON_DEGRADING_DIAGNOSTICS
        for item in diagnostics
    )
    byte_limited = any(item.reason == "byte_limit" for item in omissions)
    item_limited = any(item.reason == "item_limit" for item in omissions)
    if sufficient:
        stop_reason = "sufficient"
        stop_detail = "All required roles covered"
    elif not candidates:
        stop_reason = "provider_degraded" if provider_degraded else "no_evidence"
        stop_detail = "No candidate evidence was available"
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
        item.candidate_id for item in omissions if item.reason in {"byte_limit", "item_limit"}
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
            target_items=request.budget.target_items,
            max_items=request.budget.max_items,
            evidence_bytes=evidence_bytes,
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
