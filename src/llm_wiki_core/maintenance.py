"""Read-only maintenance candidates derived from explicit wiki/runtime evidence."""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping

from .config import ContentConfig, inspect_wiki_config
from .doctor import inspect_runtime
from .documents import WikiPage, collect_pages, safe_source_path
from .state import normalize_knowledge_state


MAINTENANCE_CONTRACT_VERSION = "1"
CANDIDATE_PROPOSAL_CONTRACT_VERSION = "1"

MAINTENANCE_CANDIDATE_KINDS = (
    "durable_outcome",
    "explicit_contradiction",
    "source_gap",
    "stale_current_claim",
    "supersession_gap",
    "relationship_gap",
    "relationship_revision",
    "promotion_candidate",
    "repeated_retrieval_gap",
)
SPECULATIVE_CANDIDATE_KINDS = frozenset(
    {
        "relationship_gap",
        "relationship_revision",
        "promotion_candidate",
        "repeated_retrieval_gap",
    }
)
ENDPOINT_DEDUPE_CANDIDATE_KINDS = frozenset(
    {
        "relationship_gap",
        "relationship_revision",
        "promotion_candidate",
    }
)

_ALIAS_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def build_candidate_proposal(
    *,
    alias: str,
    kind: str,
    diagnostic: str,
    review_question: str,
    pages: list[str] | tuple[str, ...],
    evidence: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    """Build one canonical, read-only maintenance proposal for later steward review."""

    target_wiki = _bounded_text(alias, "alias", max_chars=128)
    if _ALIAS_PATTERN.fullmatch(target_wiki) is None:
        raise ValueError("alias must contain only letters, numbers, dots, underscores, or hyphens")
    if kind not in MAINTENANCE_CANDIDATE_KINDS:
        raise ValueError(f"kind must be one of {', '.join(MAINTENANCE_CANDIDATE_KINDS)}")

    normalized_pages = sorted({_normalize_page(page) for page in pages})
    if kind in {"relationship_gap", "relationship_revision"} and len(normalized_pages) < 2:
        raise ValueError(f"{kind} requires at least two distinct page endpoints")
    if kind == "promotion_candidate" and not normalized_pages:
        raise ValueError("promotion_candidate requires at least one page")

    normalized_evidence = _normalize_proposal_evidence(evidence)
    if not normalized_evidence:
        raise ValueError("evidence must contain at least one item")

    evidence_identities = sorted(
        {str(item.get("content_hash") or item["ref"]) for item in normalized_evidence}
    )
    dedupe_identity: dict[str, Any] = {
        "target_wiki": target_wiki,
        "kind": kind,
        "pages": normalized_pages,
    }
    if kind not in ENDPOINT_DEDUPE_CANDIDATE_KINDS:
        dedupe_identity["evidence"] = evidence_identities
    dedupe_key = f"maintenance-question:{_short_hash(dedupe_identity)}"
    proposal_id = f"maintenance-observation:{_short_hash({'dedupe_key': dedupe_key, 'evidence': normalized_evidence})}"
    signal = "speculative" if kind in SPECULATIVE_CANDIDATE_KINDS else "deterministic"

    return {
        "contract_version": CANDIDATE_PROPOSAL_CONTRACT_VERSION,
        "id": proposal_id,
        "dedupe_key": dedupe_key,
        "kind": kind,
        "target_wiki": target_wiki,
        "diagnostic": _bounded_text(diagnostic, "diagnostic", max_chars=1_000),
        "review_question": _bounded_text(review_question, "review_question", max_chars=1_000),
        "pages": normalized_pages,
        "evidence": normalized_evidence,
        "signal": signal,
        "eligibility": {
            "mode": "recurrence_or_corroboration" if signal == "speculative" else "first_observation",
            "independent_evidence_count": len(evidence_identities),
        },
        "disposition": "candidate_only",
        "mutation": {"allowed": False, "commands": []},
    }


def build_maintenance_packet(
    wiki_root: str | Path,
    *,
    alias: str,
    stale_after_days: int = 180,
    as_of: date | None = None,
) -> dict[str, Any]:
    root = Path(wiki_root).expanduser().resolve()
    if stale_after_days < 1:
        raise ValueError("stale_after_days must be positive")
    as_of = as_of or date.today()
    config_inspection = inspect_wiki_config(root)
    content = config_inspection.config.content if config_inspection.config is not None else ContentConfig()
    pages = collect_pages(root, content=content)
    candidates: list[dict[str, Any]] = []

    runtime = inspect_runtime(root)
    compatibility = runtime["compatibility"]
    if compatibility["status"] != "compatible":
        candidates.append(
            _candidate(
                kind="runtime_drift",
                page=None,
                diagnostic="Wiki runtime/configuration requires review",
                review_question="Should the runtime/configuration be migrated, repaired, or explicitly deferred?",
                evidence=[
                    {
                        "page": None,
                        "source": None,
                        "locator": {"surface": "doctor.compatibility"},
                        "content": json.dumps(compatibility, sort_keys=True, separators=(",", ":")),
                        "authored_state": "not_applicable",
                        "derived_flags": ["runtime_drift"],
                    }
                ],
            )
        )

    for path, page in pages.items():
        state = normalize_knowledge_state(page.frontmatter)
        if state.normalized == "current":
            reviewed = _as_date(page.frontmatter.get("last_reviewed"))
            if reviewed is not None:
                age = (as_of - reviewed).days
                if age > stale_after_days:
                    candidates.append(
                        _candidate(
                            kind="stale_current_claim",
                            page=path,
                            diagnostic="Explicitly current page exceeds the review-age threshold",
                            review_question="Does this page still describe current knowledge?",
                            evidence=[
                                _field_evidence(
                                    page,
                                    "last_reviewed",
                                    derived_flags=[f"review_age_days:{age}"],
                                )
                            ],
                        )
                    )
        if state.normalized == "superseded" and not _references(page.frontmatter.get("superseded_by")):
            candidates.append(
                _candidate(
                    kind="supersession_gap",
                    page=path,
                    diagnostic="Page is marked superseded without a replacement reference",
                    review_question="Which page, if any, supersedes this record?",
                    evidence=[_field_evidence(page, "knowledge_state")],
                )
            )
        if state.normalized == "contradicted":
            candidates.append(
                _candidate(
                    kind="explicit_contradiction",
                    page=path,
                    diagnostic="Page is explicitly marked contradicted",
                    review_question="Is the conflicting evidence linked and is the current position clear?",
                    evidence=[_field_evidence(page, "knowledge_state")],
                )
            )
        candidates.extend(_source_gap_candidates(root, page, content))

    candidates.sort(key=lambda item: (item["kind"], item["page"] or "", item["id"]))
    return {
        "kind": "maintenance_candidate_packet",
        "contract_version": MAINTENANCE_CONTRACT_VERSION,
        "wiki": {"alias": alias},
        "as_of": as_of.isoformat(),
        "stale_after_days": stale_after_days,
        "status": "candidates_present" if candidates else "no_candidates_observed",
        "candidates": candidates,
        "unknowns": [
            {
                "kind": "semantic_contradictions",
                "status": "unsupported_without_semantic_review",
                "detail": "Only explicit authored contradiction state is reported deterministically.",
            },
            {
                "kind": "semantic_staleness",
                "status": "unsupported_without_semantic_review",
                "detail": "Review age is a candidate signal, not proof that a claim is stale.",
            },
            {
                "kind": "live_source_drift",
                "status": "unsupported_without_source_refresh",
                "detail": "Local source presence does not establish that an external source is unchanged.",
            },
        ],
        "mutation": {"allowed": False, "commands": []},
        "stewardship": {
            "decision": "review_required",
            "instruction": "Apply any accepted change through the target wiki steward and local manual.",
        },
    }


def _source_gap_candidates(root: Path, page: WikiPage, content: ContentConfig) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    fields: list[tuple[str, str]] = []
    source = page.frontmatter.get("source")
    if source:
        fields.append(("source", str(source)))
    evidence = page.frontmatter.get("evidence")
    if isinstance(evidence, list):
        fields.extend(("evidence", str(value)) for value in evidence if value)
    elif evidence:
        fields.append(("evidence", str(evidence)))

    for field, reference in fields:
        resolved = safe_source_path(root, reference, source_directory=content.source_directory)
        if resolved is not None and resolved.is_file():
            continue
        candidates.append(
            _candidate(
                kind="source_gap",
                page=page.path,
                diagnostic="Referenced source evidence is missing or outside the configured source directory",
                review_question="Should the source reference be restored, corrected, or explicitly retired?",
                evidence=[
                    _field_evidence(
                        page,
                        field,
                        content=f"{field}: {reference}",
                        derived_flags=["unsafe_source_path" if resolved is None else "source_missing"],
                    )
                ],
            )
        )
    return candidates


def _bounded_text(value: Any, field: str, *, max_chars: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field} must be a non-empty string")
    if "\x00" in text:
        raise ValueError(f"{field} must not contain null bytes")
    if len(text) > max_chars:
        raise ValueError(f"{field} must be at most {max_chars} characters")
    return text


def _normalize_page(value: Any) -> str:
    text = _bounded_text(value, "pages[]", max_chars=500).replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or (path.parts and ":" in path.parts[0]):
        raise ValueError("pages[] must be a relative path inside the target wiki")
    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise ValueError("pages[] must identify a wiki page")
    return normalized


def _normalize_proposal_evidence(
    evidence: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> list[dict[str, str]]:
    if not isinstance(evidence, (list, tuple)):
        raise ValueError("evidence must be a list")
    normalized: dict[str, dict[str, str]] = {}
    for index, item in enumerate(evidence):
        if not isinstance(item, Mapping):
            raise ValueError(f"evidence[{index}] must be an object")
        unknown = set(item) - {"ref", "note", "content_hash"}
        if unknown:
            raise ValueError(f"evidence[{index}] contains unsupported fields: {', '.join(sorted(unknown))}")
        record = {"ref": _bounded_text(item.get("ref"), f"evidence[{index}].ref", max_chars=2_000)}
        if item.get("note") is not None:
            record["note"] = _bounded_text(item["note"], f"evidence[{index}].note", max_chars=1_000)
        if item.get("content_hash") is not None:
            record["content_hash"] = _bounded_text(
                item["content_hash"],
                f"evidence[{index}].content_hash",
                max_chars=128,
            )
        normalized[_canonical_json(record)] = record
    return sorted(
        normalized.values(),
        key=lambda item: (item["ref"], item.get("content_hash", ""), item.get("note", "")),
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _short_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()[:16]


def _candidate(
    *,
    kind: str,
    page: str | None,
    diagnostic: str,
    review_question: str,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    identity = json.dumps(
        {"kind": kind, "page": page, "evidence": evidence},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "id": f"maintenance:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}",
        "kind": kind,
        "page": page,
        "diagnostic": diagnostic,
        "review_question": review_question,
        "evidence": evidence,
        "disposition": "candidate_only",
    }


def _field_evidence(
    page: WikiPage,
    field: str,
    *,
    content: str | None = None,
    derived_flags: list[str] | None = None,
) -> dict[str, Any]:
    value = page.frontmatter.get(field)
    return {
        "page": page.path,
        "source": str(page.frontmatter.get("source") or "") or None,
        "locator": {"field": field, "line": _frontmatter_line(page.text, field)},
        "content": content if content is not None else f"{field}: {_render_value(value)}",
        "authored_state": normalize_knowledge_state(page.frontmatter).normalized,
        "derived_flags": list(derived_flags or []),
    }


def _frontmatter_line(text: str, field: str) -> int | None:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None
    prefix = f"{field}:"
    for index, line in enumerate(lines[1:], start=2):
        if line == "---":
            break
        if line.startswith(prefix):
            return index
    return None


def _render_value(value: Any) -> str:
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value or "")


def _references(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if value is None:
        return ()
    rendered = str(value).strip()
    return (rendered,) if rendered else ()


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
