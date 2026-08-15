"""Frozen, candidate-only contract for unified Brain maintenance.

This module deliberately contains no runtime or MCP integration.  It builds and
validates the JSON envelope shared by llm-wiki and Anvil while preserving the
legacy readers needed during migration.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "unified-maintenance/1"
FIXTURE_SCHEMA_VERSION = "unified-maintenance-fixtures/1"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_PROPOSAL_ID = re.compile(r"^maintenance-proposal:sha256:[0-9a-f]{64}$")
_OUTCOME_ID = re.compile(r"^maintenance-outcome:sha256:[0-9a-f]{64}$")
_REVISION_ID = re.compile(r"^temporal-(?:claim-revision|revision):sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"^(?:commit|tree):[0-9a-f]{40}$")
_INTENTS = {"durable_learning", "correction", "work_history", "detected_gap", "wiki_hygiene"}
_OUTCOMES = {"accepted", "no_change", "deferred", "rejected", "failed"}
_PROPOSAL_FIELDS = {
    "schema_version", "proposal_id", "target_wiki", "source", "classification", "observations",
    "candidates", "reconciliation", "affected_pages", "unknowns", "disposition", "mutation", "authority",
}
_OUTCOME_FIELDS = {
    "schema_version", "outcome_id", "proposal_id", "target_wiki", "change_class", "outcome", "recorded_at", "provenance",
    "changed_refs", "brain_commit", "brain_tree", "verification", "temporal_revision_ids",
    "not_applicable_reason", "summary",
}
_SOURCE_FIELDS = {"source_kind", "source_ref", "content_hash"}
_CLAIM_FIELDS = {
    "subject", "predicate", "object", "world_validity", "operation", "supersedes", "contradicts",
    "qualifies", "retire", "identity", "effective_at", "status", "relation", "text",
}
_EVIDENCE_FIELDS = {"ref", "kind", "content_hash", "note"}


def _fail(message: str) -> ValueError:
    return ValueError(f"unified maintenance: {message}")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(prefix: str, value: Any) -> str:
    return f"{prefix}:sha256:{hashlib.sha256(_canonical(value)).hexdigest()}"


def _mapping(raw: Any, name: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise _fail(f"{name} must be an object")
    return dict(raw)


def _fields(raw: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise _fail(f"{name} contains unknown fields: {', '.join(unknown)}")


def _text(raw: Any, name: str, *, max_chars: int = 2048) -> str:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > max_chars:
        raise _fail(f"{name} must be a non-empty string of at most {max_chars} characters")
    return raw.strip()


def _timestamp(raw: Any, name: str) -> str:
    value = _text(raw, name, max_chars=64)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _fail(f"{name} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise _fail(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(raw: Any, name: str) -> str:
    if not isinstance(raw, str) or _HASH.fullmatch(raw) is None:
        raise _fail(f"{name} must be lowercase SHA-256")
    return raw


def _page(raw: Any) -> str:
    value = _text(raw, "page", max_chars=512).replace("\\", "/")
    if value.startswith("/") or any(part in {"", ".", ".."} for part in value.split("/")):
        raise _fail("page must be a relative wiki path")
    return value


def _json_value(raw: Any, name: str, max_bytes: int = 8192) -> Any:
    try:
        value = json.loads(json.dumps(raw, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise _fail(f"{name} must be JSON data") from exc
    if len(_canonical(value)) > max_bytes:
        raise _fail(f"{name} exceeds {max_bytes} bytes")
    return value


def _source(raw: Mapping[str, Any]) -> dict[str, Any]:
    value = _mapping(raw, "source")
    _fields(value, _SOURCE_FIELDS, "source")
    return {
        "source_kind": _text(value.get("source_kind"), "source.source_kind", max_chars=128),
        "source_ref": _text(value.get("source_ref"), "source.source_ref", max_chars=2048),
        "content_hash": _hash(value.get("content_hash"), "source.content_hash"),
    }


def _claim(raw: Mapping[str, Any]) -> dict[str, Any]:
    value = _mapping(raw, "claim")
    _fields(value, _CLAIM_FIELDS, "claim")
    if "subject" not in value or "predicate" not in value or "object" not in value:
        raise _fail("claim requires subject, predicate, and object")
    result: dict[str, Any] = {
        "subject": _json_value(value["subject"], "claim.subject", 2048),
        "predicate": _text(value["predicate"], "claim.predicate", max_chars=128),
        "object": _json_value(value["object"], "claim.object", 4096),
    }
    for key in sorted(set(value) - {"subject", "predicate", "object"}):
        result[key] = _json_value(value[key], f"claim.{key}")
    return result


def _evidence(raw: Mapping[str, Any]) -> dict[str, Any]:
    value = _mapping(raw, "evidence")
    _fields(value, _EVIDENCE_FIELDS, "evidence")
    result = {"ref": _text(value.get("ref"), "evidence.ref", max_chars=2048)}
    for key in sorted(set(value) - {"ref"}):
        if key == "content_hash":
            result[key] = _hash(value[key], "evidence.content_hash")
        else:
            result[key] = _json_value(value[key], f"evidence.{key}", 2048)
    return result


def _normalize_proposal_request(
    *,
    alias: str,
    source: Mapping[str, Any],
    intent: str,
    claims: Sequence[Mapping[str, Any]] = (),
    pages: Sequence[str] = (),
    evidence: Sequence[Mapping[str, Any]] = (),
    proposed_at: str,
) -> dict[str, Any]:
    if not isinstance(alias, str) or not alias or len(alias) > 128 or not re.fullmatch(r"[A-Za-z0-9_.-]+", alias):
        raise _fail("alias is invalid")
    if intent not in _INTENTS:
        raise _fail("intent is unsupported")
    if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes)) or len(claims) > 64:
        raise _fail("claims must contain at most 64 items")
    if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)) or len(pages) > 64:
        raise _fail("pages must contain at most 64 items")
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)) or len(evidence) > 64:
        raise _fail("evidence must contain at most 64 items")
    normalized_claims = sorted((_claim(item) for item in claims), key=lambda item: _canonical(item))
    normalized_pages = sorted({_page(item) for item in pages})
    normalized_evidence = sorted((_evidence(item) for item in evidence), key=lambda item: _canonical(item))
    return {
        "alias": alias,
        "source": _source(source),
        "intent": intent,
        "claims": normalized_claims,
        "pages": normalized_pages,
        "evidence": normalized_evidence,
        "proposed_at": _timestamp(proposed_at, "proposed_at"),
    }


def build_unified_maintenance_proposal(
    *,
    alias: str,
    source: Mapping[str, Any],
    intent: str,
    claims: Sequence[Mapping[str, Any]] = (),
    pages: Sequence[str] = (),
    evidence: Sequence[Mapping[str, Any]] = (),
    proposed_at: str,
    **unexpected: Any,
) -> dict[str, Any]:
    """Build one deterministic, candidate-only maintenance proposal."""

    if unexpected:
        raise _fail(f"caller version/temporal switches are unsupported: {', '.join(sorted(unexpected))}")

    request = _normalize_proposal_request(
        alias=alias, source=source, intent=intent, claims=claims, pages=pages,
        evidence=evidence, proposed_at=proposed_at,
    )
    if request["claims"]:
        change_class, temporal_obligation = "knowledge_revision", "required"
        reasons = ["claim_present"]
    elif request["intent"] == "wiki_hygiene" and request["evidence"]:
        change_class, temporal_obligation = "wiki_hygiene", "not_applicable"
        reasons = ["non_semantic_maintenance"]
    else:
        change_class, temporal_obligation = "no_change", "not_applicable"
        reasons = ["no_applicable_change"]

    unknowns: list[dict[str, str]] = []
    for claim in request["claims"]:
        validity = claim.get("world_validity")
        if isinstance(validity, Mapping) and validity.get("kind") == "unknown":
            unknowns.append({"kind": "world_time", "status": "unknown", "detail": str(validity.get("reason", "unspecified"))})
        identity = claim.get("identity")
        if isinstance(identity, Mapping) and identity.get("status") == "ambiguous":
            unknowns.append({"kind": "identity", "status": "ambiguous", "detail": "claim identity requires Steward review"})
    result = {
        "schema_version": SCHEMA_VERSION,
        "proposal_id": "",
        "target_wiki": request["alias"],
        "source": request["source"],
        "classification": {"change_class": change_class, "temporal_obligation": temporal_obligation, "reasons": reasons},
        "observations": request["evidence"],
        "candidates": request["claims"],
        "reconciliation": None,
        "affected_pages": request["pages"],
        "unknowns": unknowns,
        "disposition": "candidate_only",
        "mutation": {"allowed": False, "commands": []},
        "authority": "target_wiki_steward",
    }
    proposal_identity = dict(result)
    proposal_identity.pop("proposal_id")
    result["proposal_id"] = _sha("maintenance-proposal", proposal_identity)
    parse_unified_maintenance_proposal(result)
    return result


def parse_unified_maintenance_proposal(raw: Mapping[str, Any]) -> dict[str, Any]:
    value = _mapping(raw, "proposal")
    _fields(value, _PROPOSAL_FIELDS, "proposal")
    missing = sorted(_PROPOSAL_FIELDS - set(value))
    if missing:
        raise _fail(f"proposal is missing required fields: {', '.join(missing)}")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise _fail("proposal schema_version is unsupported")
    proposal_id = value.get("proposal_id")
    if not isinstance(proposal_id, str) or _PROPOSAL_ID.fullmatch(proposal_id) is None:
        raise _fail("proposal_id is invalid")
    proposal_identity = dict(value)
    proposal_identity.pop("proposal_id")
    if _sha("maintenance-proposal", proposal_identity) != proposal_id:
        raise _fail("proposal_id does not match canonical identity")
    target = value.get("target_wiki")
    if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", target):
        raise _fail("target_wiki is invalid")
    _source(value.get("source"))
    classification = _mapping(value.get("classification"), "classification")
    _fields(classification, {"change_class", "temporal_obligation", "reasons"}, "classification")
    if classification.get("change_class") not in {"knowledge_revision", "wiki_hygiene", "no_change"}:
        raise _fail("change_class is unsupported")
    expected_obligation = "required" if classification["change_class"] == "knowledge_revision" else "not_applicable"
    if classification.get("temporal_obligation") != expected_obligation:
        raise _fail("temporal_obligation does not match change_class")
    reasons = classification.get("reasons")
    if not isinstance(reasons, list) or not reasons or any(not isinstance(item, str) or not item for item in reasons):
        raise _fail("classification reasons are invalid")
    if value.get("disposition") != "candidate_only" or value.get("mutation") != {"allowed": False, "commands": []}:
        raise _fail("proposal authority fields are immutable")
    if value.get("authority") != "target_wiki_steward":
        raise _fail("proposal authority is invalid")
    for key in ("observations", "candidates", "affected_pages", "unknowns"):
        if not isinstance(value.get(key), list):
            raise _fail(f"{key} must be a list")
    if value["observations"] != sorted(value["observations"], key=_canonical):
        raise _fail("observations are not in canonical order")
    if value["candidates"] != sorted(value["candidates"], key=_canonical):
        raise _fail("candidates are not in canonical order")
    # Temporal internals remain versioned inputs.  When present, use their
    # existing strict validators rather than creating a second interpretation.
    from .temporal import parse_observation_ref, parse_temporal_fact_candidate
    for observation in value["observations"]:
        if isinstance(observation, Mapping) and observation.get("contract_version") == "temporal-observation/1":
            parse_observation_ref(observation)
    for candidate in value["candidates"]:
        if isinstance(candidate, Mapping) and candidate.get("contract_version") == "temporal-candidate/1":
            parse_temporal_fact_candidate(candidate)
    if value["affected_pages"] != sorted(set(value["affected_pages"])) or len(value["affected_pages"]) != len(set(value["affected_pages"])):
        raise _fail("affected_pages are not canonical")
    expected_class = "knowledge_revision" if value["candidates"] else ("wiki_hygiene" if value["observations"] else "no_change")
    if classification["change_class"] != expected_class:
        raise _fail("classification does not match deterministic proposal contents")
    if value.get("reconciliation") is not None and not isinstance(value["reconciliation"], Mapping):
        raise _fail("reconciliation must be an object or null")
    return dict(value)


def adapt_legacy_task_proposal(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Carry one existing v1 task proposal into a unified result unchanged."""

    return _json_value(_mapping(raw, "legacy task proposal"), "legacy task proposal", max_bytes=1_000_000)


def adapt_legacy_discovery_packet(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Carry one existing v1 discovery packet into a unified result unchanged."""

    return _json_value(_mapping(raw, "legacy discovery packet"), "legacy discovery packet", max_bytes=1_000_000)


def compose_unified_maintenance_proposal(
    *,
    alias: str,
    source: Mapping[str, Any],
    intent: str,
    claims: Sequence[Mapping[str, Any]] = (),
    pages: Sequence[str] = (),
    evidence: Sequence[Mapping[str, Any]] = (),
    proposed_at: str,
    observations: Sequence[Mapping[str, Any]] = (),
    candidates: Sequence[Mapping[str, Any]] = (),
    reconciliation: Mapping[str, Any] | None = None,
    unknowns: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Compose one unified envelope from exact existing producer payloads.

    The frozen WP-TU0 builder remains the compatibility path for its historical
    generic claim shape.  This composition helper is the internal seam used by
    the new runtime path, where temporal producers have already validated and
    materialized their exact observation/candidate/reconciliation dictionaries.
    """

    if claims:
        raise _fail("compose_unified_maintenance_proposal accepts materialized candidates, not raw claims")
    request = _normalize_proposal_request(
        alias=alias,
        source=source,
        intent=intent,
        claims=(),
        pages=pages,
        evidence=(),
        proposed_at=proposed_at,
    )
    normalized_observations = sorted(
        (_json_value(item, "observation", max_bytes=1_000_000) for item in observations),
        key=_canonical,
    )
    normalized_candidates = sorted(
        (_json_value(item, "candidate", max_bytes=1_000_000) for item in candidates),
        key=_canonical,
    )
    normalized_evidence = sorted(
        (_evidence(item) for item in evidence),
        key=_canonical,
    )
    normalized_observations = sorted(
        [*normalized_observations, *normalized_evidence],
        key=_canonical,
    )
    normalized_unknowns = [
        _json_value(item, "unknown", max_bytes=8_192) for item in unknowns
    ]
    if normalized_candidates:
        change_class, temporal_obligation, reasons = "knowledge_revision", "required", ["claim_present"]
    elif normalized_observations:
        change_class, temporal_obligation, reasons = "wiki_hygiene", "not_applicable", ["non_semantic_maintenance"]
    else:
        change_class, temporal_obligation, reasons = "no_change", "not_applicable", ["no_applicable_change"]
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "proposal_id": "",
        "target_wiki": request["alias"],
        "source": request["source"],
        "classification": {
            "change_class": change_class,
            "temporal_obligation": temporal_obligation,
            "reasons": reasons,
        },
        "observations": normalized_observations,
        "candidates": normalized_candidates,
        "reconciliation": (
            _json_value(reconciliation, "reconciliation", max_bytes=1_000_000)
            if reconciliation is not None else None
        ),
        "affected_pages": request["pages"],
        "unknowns": normalized_unknowns,
        "disposition": "candidate_only",
        "mutation": {"allowed": False, "commands": []},
        "authority": "target_wiki_steward",
    }
    proposal_identity = dict(result)
    proposal_identity.pop("proposal_id")
    result["proposal_id"] = _sha("maintenance-proposal", proposal_identity)
    if len(_canonical(result)) > 1_000_000:
        raise _fail("proposal exceeds 1000000 canonical UTF-8 bytes")
    parse_unified_maintenance_proposal(result)
    return result


def _ref_list(raw: Any, name: str, *, required: bool = False) -> list[str]:
    if not isinstance(raw, list) or len(raw) > 256 or any(not isinstance(item, str) or not item.strip() for item in raw):
        raise _fail(f"{name} must be a list of bounded non-empty strings")
    result = sorted(set(item.strip() for item in raw))
    if required and not result:
        raise _fail(f"{name} is required")
    return result


def build_unified_maintenance_outcome(
    proposal: Mapping[str, Any],
    *,
    outcome: str,
    recorded_at: str,
    provenance: Sequence[str] = (),
    changed_refs: Sequence[str] = (),
    brain_commit: str | None = None,
    brain_tree: str | None = None,
    verification: Mapping[str, Any] | None = None,
    temporal_revision_ids: Sequence[str] = (),
    not_applicable_reason: str | None = None,
    summary: str = "",
) -> dict[str, Any]:
    """Build and validate one idempotent closure for a proposal."""

    proposal_value = parse_unified_maintenance_proposal(proposal)
    if outcome not in _OUTCOMES:
        raise _fail("outcome is unsupported")
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "outcome_id": "",
        "proposal_id": proposal_value["proposal_id"],
        "target_wiki": proposal_value["target_wiki"],
        "change_class": proposal_value["classification"]["change_class"],
        "outcome": outcome,
        "recorded_at": _timestamp(recorded_at, "recorded_at"),
        "provenance": _ref_list(list(provenance), "provenance"),
        "changed_refs": _ref_list(list(changed_refs), "changed_refs"),
        "brain_commit": brain_commit,
        "brain_tree": brain_tree,
        "verification": dict(verification or {}),
        "temporal_revision_ids": sorted(set(temporal_revision_ids)),
        "not_applicable_reason": not_applicable_reason,
        "summary": _text(summary, "summary", max_chars=65536),
    }
    identity = dict(result)
    identity.pop("outcome_id")
    result["outcome_id"] = _sha("maintenance-outcome", identity)
    parse_unified_maintenance_outcome(result)
    return result


def parse_unified_maintenance_outcome(raw: Mapping[str, Any], *, proposal: Mapping[str, Any] | None = None) -> dict[str, Any]:
    value = _mapping(raw, "outcome")
    _fields(value, _OUTCOME_FIELDS, "outcome")
    missing = sorted(_OUTCOME_FIELDS - set(value))
    if missing:
        raise _fail(f"outcome is missing required fields: {', '.join(missing)}")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise _fail("outcome schema_version is unsupported")
    if not isinstance(value.get("outcome_id"), str) or _OUTCOME_ID.fullmatch(value["outcome_id"]) is None:
        raise _fail("outcome_id is invalid")
    if not isinstance(value.get("proposal_id"), str) or _PROPOSAL_ID.fullmatch(value["proposal_id"]) is None:
        raise _fail("outcome proposal_id is invalid")
    if value.get("change_class") not in {"knowledge_revision", "wiki_hygiene", "no_change"}:
        raise _fail("outcome change_class is unsupported")
    if proposal is not None:
        proposal_value = parse_unified_maintenance_proposal(proposal)
        if value["proposal_id"] != proposal_value["proposal_id"] or value["target_wiki"] != proposal_value["target_wiki"]:
            raise _fail("outcome does not close the supplied proposal")
        if value["change_class"] != proposal_value["classification"]["change_class"]:
            raise _fail("outcome change_class does not match proposal")
    if value.get("outcome") not in _OUTCOMES:
        raise _fail("outcome is unsupported")
    _timestamp(value.get("recorded_at"), "recorded_at")
    changed = _ref_list(value.get("changed_refs"), "changed_refs")
    provenance = _ref_list(value.get("provenance"), "provenance")
    if value["changed_refs"] != changed or value["provenance"] != provenance:
        raise _fail("reference lists are not canonical")
    revisions = value.get("temporal_revision_ids")
    if not isinstance(revisions, list) or any(not isinstance(item, str) or _REVISION_ID.fullmatch(item) is None for item in revisions):
        raise _fail("temporal_revision_ids are invalid")
    if len(set(revisions)) != len(revisions):
        raise _fail("temporal_revision_ids must be unique")
    if revisions != sorted(revisions):
        raise _fail("temporal_revision_ids are not canonical")
    if value.get("brain_commit") is not None and (not isinstance(value["brain_commit"], str) or not re.fullmatch(r"commit:[0-9a-f]{40}", value["brain_commit"])):
        raise _fail("brain_commit is invalid")
    if value.get("brain_tree") is not None and (not isinstance(value["brain_tree"], str) or not re.fullmatch(r"tree:[0-9a-f]{40}", value["brain_tree"])):
        raise _fail("brain_tree is invalid")
    verification = _mapping(value.get("verification"), "verification")
    _fields(verification, {"lint", "render"}, "verification")
    if value["outcome"] == "accepted":
        if value["change_class"] == "no_change":
            raise _fail("accepted no_change outcome is invalid")
        if not provenance or not changed or value.get("brain_commit") is None or value.get("brain_tree") is None:
            raise _fail("accepted outcome requires provenance, changed refs, commit, and tree")
        if verification.get("lint") is not True or verification.get("render") is not True:
            raise _fail("accepted outcome requires lint and render proof")
        if value["change_class"] == "knowledge_revision":
            if not revisions or value.get("not_applicable_reason") is not None:
                raise _fail("accepted knowledge_revision requires revisions and no not_applicable_reason")
        elif value["change_class"] == "wiki_hygiene":
            if revisions or not value.get("not_applicable_reason"):
                raise _fail("accepted wiki_hygiene requires not_applicable_reason and no revisions")
    else:
        if changed or revisions or value.get("brain_commit") is not None or value.get("brain_tree") is not None:
            raise _fail("nonaccepted outcome may not contain durable references")
    if value.get("not_applicable_reason") is not None:
        _text(value["not_applicable_reason"], "not_applicable_reason", max_chars=2048)
    _text(value.get("summary"), "summary", max_chars=65536)
    outcome_identity = dict(value)
    outcome_id = outcome_identity.pop("outcome_id")
    if _sha("maintenance-outcome", outcome_identity) != outcome_id:
        raise _fail("outcome_id does not match canonical identity")
    return dict(value)


def normalize_legacy_outcome(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize historical v1 ``no_op`` for views while retaining its IDs."""

    value = _mapping(raw, "legacy outcome")
    version = value.get("contract_version")
    if version not in {"1", "2"}:
        raise _fail("legacy outcome version is unsupported")
    result = dict(value)
    if version == "1" and result.get("outcome") == "no_op":
        result["outcome"] = "no_change"
    return result


__all__ = [
    "FIXTURE_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "adapt_legacy_discovery_packet",
    "adapt_legacy_task_proposal",
    "build_unified_maintenance_outcome",
    "build_unified_maintenance_proposal",
    "compose_unified_maintenance_proposal",
    "normalize_legacy_outcome",
    "parse_unified_maintenance_outcome",
    "parse_unified_maintenance_proposal",
]
