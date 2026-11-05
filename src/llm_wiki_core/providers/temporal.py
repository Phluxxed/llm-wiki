from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Iterable

from ..contracts import Diagnostic
from ..state import normalize_knowledge_state
from ..temporal import EntityRef, TemporalContractError
from ..temporal_persistence import (
    TemporalClaimRevision,
    eligible_temporal_revisions,
    fold_temporal_claim_revisions,
    parse_temporal_claim_revisions,
    render_temporal_revision,
)
from .base import CandidateEvidence, ProviderContext, ProviderResult
from .utils import matched_question_terms, page_matches_question, question_terms


TEMPORAL_DIAGNOSTIC_RECOVERY = {
    "TEMPORAL_HISTORY_INVALID": "Repair the affected authored history through the wiki Steward; do not use its prose as a settled temporal answer.",
    "TEMPORAL_CONFLICT": "Use the existing conflict view and inspect the competing claims; do not choose a settled winner from this recall.",
    "TEMPORAL_STATE_UNCERTAIN": "Use explicit temporal or source follow-up before claiming current or dated applicability; do not invent missing times.",
}


def _entity_meaning(ref: EntityRef) -> str:
    """Entity identity/value without ambiguity-observation bookkeeping."""
    values = [ref.page, ref.namespace, ref.value, ref.surface]
    values.extend(_entity_meaning(candidate) for candidate, _ in ref.candidates)
    return " ".join(value for value in values if value is not None)


def _claim_matches(revision: TemporalClaimRevision, terms: set[str], view: str) -> tuple[str, ...]:
    subject = set(matched_question_terms(_entity_meaning(revision.subject), terms))
    detail = set(matched_question_terms(revision.predicate + " " + _entity_meaning(revision.object_ref), terms))
    specific = terms - subject - {view}
    if specific and not specific & detail:
        return ()
    return tuple(sorted(subject | detail))


class TemporalProvider:
    """Apply the existing temporal reader to claims and qualify other evidence.

    Ordinary providers still discover context. They cannot establish claim-level
    applicability by bypassing a page's authored history. No content is written
    and no dates are inferred from prose, source filenames, or file timestamps.
    """

    name = "temporal"

    def collect(self, context: ProviderContext, ordinary: Iterable[CandidateEvidence] = ()) -> ProviderResult:
        query = context.request.temporal
        if query is None:
            return ProviderResult(tuple(ordinary))
        terms = question_terms(context.request.question)
        known_at = query.known_at or query.request_time
        restricted_known_time = (
            datetime.fromisoformat(known_at.replace("Z", "+00:00"))
            < datetime.fromisoformat(query.request_time.replace("Z", "+00:00"))
        )
        invalid_pages: set[str] = set()
        late_pages: set[str] = set()
        history_pages: set[str] = set()
        notices: dict[str, set[str]] = {code: set() for code in TEMPORAL_DIAGNOSTIC_RECOVERY}
        temporal: list[CandidateEvidence] = []
        for path, page in context.pages.items():
            if "temporal_claim_revisions" not in page.frontmatter:
                continue
            page_relevant = path in context.resolved_seeds or page_matches_question(page, context.request.question, terms)
            try:
                revisions = parse_temporal_claim_revisions(page.frontmatter)
            except TemporalContractError:
                invalid_pages.add(path)
                if page_relevant:
                    notices["TEMPORAL_HISTORY_INVALID"].add(path)
                continue
            if not revisions:
                continue
            eligible = eligible_temporal_revisions(revisions, query)
            fold = fold_temporal_claim_revisions(revisions, known_at=known_at)
            history_pages.add(path)
            visible = set(fold.complete_lineage_revision_ids)
            if any(r.revision_id not in visible for r in revisions):
                late_pages.add(path)
                if page_relevant and not visible:
                    notices["TEMPORAL_STATE_UNCERTAIN"].add(path)
            matching = {
                r.revision_id: _claim_matches(r, terms, query.view)
                for r in revisions if r.revision_id in visible
            }
            matched = {r.revision_id for r in revisions if r.revision_id in visible
                       and (matching[r.revision_id] or path in context.resolved_seeds)}
            if query.view == "current":
                conflict = eligible_temporal_revisions(revisions, replace(query, view="conflict", transition=None))
                contested = {r.claim_key for r in conflict if r.revision_id in matched}
                if contested:
                    notices["TEMPORAL_CONFLICT"].add(path)
                eligible_keys = {r.claim_key for r in eligible}
                if any(r.revision_id in matched and r.claim_key not in eligible_keys | contested for r in revisions):
                    notices["TEMPORAL_STATE_UNCERTAIN"].add(path)
            for revision in eligible:
                if revision.revision_id not in matched:
                    continue
                contested = revision.revision_id in fold.contested_revision_ids or query.view == "conflict"
                current = query.view == "current" and not contested
                flags = ["temporal_accepted"]
                if revision.decision == "qualify":
                    flags.append("temporal_qualified")
                if contested:
                    flags.append("temporal_contested")
                roles = ("answer", "current_claim", "authority") if current else (
                    ("answer", "lineage") if contested else ("answer", "lineage", "authority")
                )
                temporal.append(CandidateEvidence(
                    id=f"temporal:{path}#{revision.revision_id}", provider=self.name,
                    route=f"temporal_{query.view}", page=path,
                    source=str(page.frontmatter.get("source") or "") or None,
                    locator={
                        "kind": "temporal_claim", "claim_key": revision.claim_key,
                        "revision_ids": [revision.revision_id],
                        "world_validity": revision.world_validity.to_dict(), "known_at": known_at,
                        "steward_evidence_refs": list(revision.steward_evidence_refs),
                    },
                    content=render_temporal_revision(revision, view=query.view, fold=fold),
                    roles=roles,
                    selection_signals=("temporal_accepted", "temporal_steward")
                    + (("explicit_seed_discovery",) if path in context.resolved_seeds else ()),
                    authored_state="current" if current else "contradicted" if contested else "historical",
                    derived_flags=tuple(flags),
                    authority_signals=("target_wiki_steward", "contested" if contested else "settled"),
                    atomic=True, relevance_terms=matching.get(revision.revision_id, ()),
                ))

        # Only eligible, question-matched claims can keep a source visible across
        # a known-time cut. Whether it supports a *selected* claim is checked at
        # the existing selection seam, where authority can be assigned honestly.
        eligible_sources = {ref for item in temporal for ref in item.locator["steward_evidence_refs"]}
        qualified: list[CandidateEvidence] = []
        for item in ordinary:
            matched_terms = item.relevance_terms if item.relevance_terms is not None else matched_question_terms(item.content, terms)
            relevant = bool(matched_terms) or item.page in context.resolved_seeds
            if item.provider == "source":
                paths = {signal.removeprefix("source_for:") for signal in item.selection_signals if signal.startswith("source_for:")}
                paths = paths or ({item.page} if item.page else set())
                usable = paths - invalid_pages - late_pages
                if (not usable or restricted_known_time) and item.source not in eligible_sources:
                    continue
                # An invalid first linker must not make a shared, valid source
                # appear to originate from the invalid page.
                page = item.page if item.page in usable else next(iter(sorted(usable)), item.page)
                signals = tuple(s for s in item.selection_signals if not s.startswith("source_for:"))
                signals += tuple(f"source_for:{path}" for path in sorted(usable))
                prefix = "Temporal context: source evidence, not independent proof of current or dated applicability."
                roles = tuple(r for r in item.roles if r not in {"authority", "current_claim"})
                qualified.append(replace(item, page=page, roles=roles,
                    content=prefix + "\n\n" + item.content, relevance_terms=tuple(matched_terms),
                    selection_signals=signals, authored_state="unspecified",
                    authority_signals=("source_excerpt",), derived_flags=item.derived_flags + ("temporal_background",)))
                continue
            # Native graph paths carry exact spans from every authored page.
            # No constituent may bypass the same temporal eligibility checks
            # that apply to a single-page excerpt.
            paths = {item.page} if item.page else set()
            paths.update(span["page"] for span in item.locator.get("spans", ())
                         if isinstance(span, dict) and isinstance(span.get("page"), str))
            if paths & (invalid_pages | late_pages):
                continue
            if restricted_known_time:
                if relevant and item.page:
                    notices["TEMPORAL_STATE_UNCERTAIN"].add(item.page)
                continue
            state = (
                item.authored_state if item.locator.get("spans") or item.page not in context.pages
                else normalize_knowledge_state(context.pages[item.page].frontmatter).normalized
            )
            governed = bool(paths & history_pages)
            if governed:
                prefix = "Temporal context: background prose; accepted temporal claims govern applicability."
                roles = tuple(r for r in item.roles if r not in {"answer", "authority", "current_claim"})
            elif state == "current" and query.view == "current":
                prefix = "Temporal context: page-authored current; claim-level times are unspecified."
                roles = item.roles
            else:
                prefix = f"Temporal context: {state} background; not verified for the requested time."
                roles = tuple(r for r in item.roles if r not in {"authority", "current_claim"})
                if relevant and state not in {"historical", "superseded"} and item.page:
                    notices["TEMPORAL_STATE_UNCERTAIN"].add(item.page)
            qualified.append(replace(item, roles=roles,
                content=prefix + "\n\n" + item.content, relevance_terms=tuple(matched_terms),
                authority_signals=() if "authority" not in roles else item.authority_signals,
                derived_flags=item.derived_flags + ("temporal_background",)))

        messages = {
            "TEMPORAL_HISTORY_INVALID": "Authored temporal history is invalid; its prose cannot supply a settled answer",
            "TEMPORAL_CONFLICT": "Relevant temporal claims are contested; no settled current winner was selected",
            "TEMPORAL_STATE_UNCERTAIN": "Relevant context has no verified claim for the requested time or has unspecified temporal state",
        }
        diagnostics = tuple(Diagnostic(
            code=code, provider=self.name,
            message=messages[code] + ": " + ", ".join(sorted(paths)[:3]) + (f" (+{len(paths) - 3} more)" if len(paths) > 3 else ""),
            details={"page_count": len(paths), "pages": sorted(paths)[:8]},
        ) for code, paths in notices.items() if paths)
        return ProviderResult(tuple(temporal + qualified), diagnostics)
