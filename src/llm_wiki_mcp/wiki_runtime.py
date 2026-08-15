from __future__ import annotations

import difflib
import hashlib
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any

from llm_wiki_core.config import ContentConfig, inspect_wiki_config
from llm_wiki_core.compiler import compile_context as compile_wiki_context
from llm_wiki_core.contracts import CompileRequest, ContractError
from llm_wiki_core.legacy import LegacyRuntime
from llm_wiki_core.maintenance import (
    MAINTENANCE_CANDIDATE_KINDS,
    build_candidate_proposal,
    build_maintenance_packet,
)
from llm_wiki_core.maintenance import build_temporal_candidate_packet
from llm_wiki_core.unified_maintenance import (
    adapt_legacy_discovery_packet,
    adapt_legacy_task_proposal,
    build_unified_maintenance_proposal,
    compose_unified_maintenance_proposal,
)
from llm_wiki_core.temporal import (
    EntityRef,
    TemporalContractError,
    build_observation_ref,
    build_temporal_fact_candidate,
    parse_observation_ref,
    parse_temporal_candidate_packet,
)
from llm_wiki_core.temporal_reconciliation import reconcile_temporal_candidates
from llm_wiki_mcp.errors import WikiMcpError
from llm_wiki_mcp.registry import doctor, get_wiki


MAX_MANUAL_CHARS = 120_000
MAX_PAGE_CHARS = 40_000
MAX_SOURCE_CHARS = 40_000
MAX_CONTEXT_TOKENS = 50_000

_TEMPORAL_SOURCE_FIELDS = frozenset(
    {
        "source_kind",
        "source_ref",
        "locator",
        "input_type",
        "observed_at",
        "source_event_time",
        "retention",
        "payload_text",
    }
)
_UNIFIED_SOURCE_FIELDS = frozenset({"source_kind", "source_ref", "content_hash"})
_UNIFIED_TEMPORAL_SOURCE_FIELDS = _UNIFIED_SOURCE_FIELDS | _TEMPORAL_SOURCE_FIELDS
_TEMPORAL_CLAIM_FIELDS = frozenset(
    {
        "subject",
        "predicate",
        "object",
        "claim_scope",
        "proposed_world_validity",
        "signals",
        "unknowns",
    }
)
_TEMPORAL_PROPOSAL_FIELDS = frozenset(
    {
        "kind",
        "contract_version",
        "target_wiki",
        "observation",
        "packet",
        "disposition",
        "mutation",
        "stewardship",
    }
)


def agent_manual(alias: str, include_conventions: bool = True, max_chars: int = MAX_MANUAL_CHARS) -> dict[str, Any]:
    record = get_wiki(alias)
    wiki_root = Path(record["path"]).expanduser().resolve()
    limit = _bounded_int(max_chars, default=MAX_MANUAL_CHARS, upper=MAX_MANUAL_CHARS)
    manual = _read_control_file(wiki_root, "wiki-agent.md", max_chars=limit, required=True)
    conventions = None
    if include_conventions:
        conventions = _read_control_file(wiki_root, "CONVENTIONS.md", max_chars=limit, required=False)

    return {
        "kind": "wiki_agent_manual",
        "alias": record["alias"],
        "path": str(wiki_root),
        "operating_manual_path": "wiki-agent.md",
        "operating_manual": manual["content"],
        "operating_manual_truncated": manual["truncated"],
        "conventions_path": "CONVENTIONS.md" if conventions is not None else None,
        "conventions": conventions["content"] if conventions is not None else None,
        "conventions_truncated": conventions["truncated"] if conventions is not None else False,
        "must_follow": [
            "Read and obey operating_manual before mutating this wiki",
            "Do not edit sources/",
            "Update index.md when adding or moving pages",
            "Append log.md for wiki changes",
            "Run lint/render after ingest or structural changes",
        ],
        "doctor": doctor(alias),
    }


def overview(alias: str) -> dict[str, Any]:
    return _legacy_runtime(alias).overview()


def query_pages(
    alias: str,
    status: str | None = None,
    category: str | None = None,
    type: str | None = None,
    tag: str | None = None,
    stale: int | None = None,
    risks: bool = False,
) -> dict[str, Any]:
    return _legacy_runtime(alias).query(
        status=status,
        category=category,
        page_type=type,
        tag=tag,
        stale=stale,
        risks=risks,
    )


def links(alias: str, page: str) -> dict[str, Any]:
    runtime = _legacy_runtime(alias)
    return runtime.links(_page_or_error(page, runtime))


def backlinks(alias: str, page: str) -> dict[str, Any]:
    runtime = _legacy_runtime(alias)
    return runtime.backlinks(_page_or_error(page, runtime))


def around(alias: str, page: str, depth: int = 1) -> dict[str, Any]:
    runtime = _legacy_runtime(alias)
    resolved = _page_or_error(page, runtime)
    safe_depth = max(1, min(int(depth), 5))
    return runtime.around(resolved, depth=safe_depth)


def context_pack(alias: str, page: str, tokens: int = 12_000) -> dict[str, Any]:
    runtime = _legacy_runtime(alias)
    resolved = _page_or_error(page, runtime)
    safe_tokens = max(500, min(int(tokens), MAX_CONTEXT_TOKENS))
    return runtime.context_pack(resolved, tokens=safe_tokens)


def compiled_context(
    alias: str,
    question: str,
    *,
    seeds: list[str] | None = None,
    state_view: str = "current",
    target_bytes: int = 48_000,
    max_bytes: int = 192_000,
    target_items: int = 24,
    max_items: int = 96,
    max_estimated_tokens: int | None = None,
    contract_version: str = "1",
    temporal_view: str | None = None,
    request_time: str | None = None,
    world_at: str | None = None,
    known_at: str | None = None,
    transition_from: str | None = None,
    transition_to: str | None = None,
) -> dict[str, Any]:
    record = get_wiki(alias)
    request_data: dict[str, Any] = {
        "contract_version": contract_version,
        "alias": record["alias"],
        "question": question,
        "seeds": seeds or [],
        "state_view": state_view,
        "budget": {
            "target_bytes": target_bytes,
            "max_bytes": max_bytes,
            "target_items": target_items,
            "max_items": max_items,
            "max_estimated_tokens": max_estimated_tokens,
        },
    }
    if temporal_view is not None:
        temporal: dict[str, Any] = {
            "view": temporal_view,
            "request_time": request_time,
        }
        if world_at is not None:
            temporal["world_at"] = world_at
        if known_at is not None:
            temporal["known_at"] = known_at
        if transition_from is not None or transition_to is not None:
            temporal["transition"] = {"from": transition_from, "to": transition_to}
        request_data["temporal"] = temporal
    try:
        temporal_arguments = (request_time, world_at, known_at, transition_from, transition_to)
        if temporal_view is None and any(value is not None for value in temporal_arguments):
            raise ContractError(
                "INVALID_INPUT",
                "Temporal arguments require temporal_view",
                {"field": "temporal_view"},
            )
        request = CompileRequest.from_mapping(request_data)
        return compile_wiki_context(record["path"], request).to_dict()
    except ContractError as exc:
        raise WikiMcpError(exc.code, exc.message, exc.details) from exc


def graph_health(alias: str) -> dict[str, Any]:
    return _legacy_runtime(alias).health()


def maintenance_candidates(alias: str, *, stale_after_days: int = 180) -> dict[str, Any]:
    record = get_wiki(alias)
    threshold = _bounded_int(stale_after_days, default=180, upper=3650)
    return build_maintenance_packet(
        record["path"],
        alias=record["alias"],
        stale_after_days=threshold,
    )


def maintenance_candidate_proposal(
    alias: str,
    *,
    kind: str,
    diagnostic: str,
    review_question: str,
    pages: list[str],
    evidence: list[dict[str, str]],
) -> dict[str, Any]:
    record = get_wiki(alias)
    try:
        return build_candidate_proposal(
            alias=record["alias"],
            kind=kind,
            diagnostic=diagnostic,
            review_question=review_question,
            pages=pages,
            evidence=evidence,
        )
    except ValueError as exc:
        raise WikiMcpError(
            "INVALID_INPUT",
            str(exc),
            {"surface": "wiki_build_maintenance_candidate"},
        ) from exc


def wiki_build_maintenance(
    alias: str,
    *,
    source: Mapping[str, Any],
    intent: str,
    claims: Sequence[Mapping[str, Any]] = (),
    pages: Sequence[str] = (),
    evidence: Sequence[Mapping[str, Any]] = (),
    proposed_at: str,
    **unexpected: Any,
) -> dict[str, Any]:
    """Build one read-only unified maintenance proposal."""

    record = get_wiki(alias)
    try:
        if unexpected:
            raise ValueError(
                "caller version/temporal switches are unsupported: "
                + ", ".join(sorted(unexpected))
            )
        if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes)):
            raise ValueError("claims must be an array")
        if len(claims) > 64:
            raise ValueError("claims exceeds 64 items")
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
            raise ValueError("evidence must be an array")
        if len(evidence) > 64:
            raise ValueError("evidence exceeds 64 items")
        source_value = _unified_source(source, claims_present=bool(claims))
        source_identity = {key: source_value[key] for key in _UNIFIED_SOURCE_FIELDS}

        if claims:
            temporal_source = {
                key: source_value[key] for key in _TEMPORAL_SOURCE_FIELDS
            }
            temporal = temporal_candidate_proposal(
                record["alias"],
                source=temporal_source,
                claims=claims,
                proposed_at=proposed_at,
            )
            reconciliation = wiki_reconcile_temporal_candidates(record["alias"], [temporal])
            return compose_unified_maintenance_proposal(
                alias=record["alias"],
                source=source_identity,
                intent=intent,
                pages=pages,
                evidence=evidence,
                proposed_at=proposed_at,
                observations=[temporal["observation"]],
                candidates=temporal["packet"]["candidates"],
                reconciliation=reconciliation,
                unknowns=temporal["packet"].get("unknowns", ()),
            )

        if intent == "detected_gap":
            packet = build_maintenance_packet(record["path"], alias=record["alias"])
            return compose_unified_maintenance_proposal(
                alias=record["alias"],
                source=source_identity,
                intent=intent,
                pages=pages,
                proposed_at=proposed_at,
                candidates=_discovery_task_proposals(record["alias"], packet),
                reconciliation=adapt_legacy_discovery_packet(packet),
            )

        return build_unified_maintenance_proposal(
            alias=record["alias"],
            source=source_identity,
            intent=intent,
            pages=pages,
            evidence=evidence,
            proposed_at=proposed_at,
        )
    except WikiMcpError:
        raise
    except (TypeError, ValueError, UnicodeError) as exc:
        raise WikiMcpError(
            "INVALID_INPUT",
            str(exc),
            {"surface": "wiki_build_maintenance"},
        ) from exc


def temporal_candidate_proposal(
    alias: str,
    *,
    source: Mapping[str, Any],
    claims: Sequence[Mapping[str, Any]],
    proposed_at: str,
) -> dict[str, Any]:
    """Build one Codex-supplied, candidate-only temporal proposal."""

    record = get_wiki(alias)
    runtime = _legacy_runtime(alias)
    try:
        source = _exact_mapping(source, _TEMPORAL_SOURCE_FIELDS, "source")
        payload_text = source["payload_text"]
        if not isinstance(payload_text, str):
            raise ValueError("source.payload_text must be text")
        payload = payload_text.encode("utf-8")
        if len(payload) > 65_536:
            raise ValueError("source.payload_text exceeds 65536 UTF-8 bytes")

        observation = build_observation_ref(
            source_kind=source["source_kind"],
            source_ref=source["source_ref"],
            locator=source["locator"],
            input_type=source["input_type"],
            observed_at=source["observed_at"],
            source_event_time=source["source_event_time"],
            retention=source["retention"],
            payload=payload,
        )

        if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes)):
            raise ValueError("claims must be an array")
        if len(claims) > 64:
            raise ValueError("claims exceeds 64 items")
        candidates = []
        for index, raw_claim in enumerate(claims):
            claim = _exact_mapping(raw_claim, _TEMPORAL_CLAIM_FIELDS, f"claims[{index}]")
            subject = EntityRef.from_mapping(claim["subject"])
            object_ref = EntityRef.from_mapping(claim["object"])
            _validate_temporal_pages(subject, runtime, f"claims[{index}].subject")
            _validate_temporal_pages(object_ref, runtime, f"claims[{index}].object")
            signals = _temporal_signals(claim["signals"], observation.observation_id)
            candidate = build_temporal_fact_candidate(
                subject=subject,
                predicate=claim["predicate"],
                object_ref=object_ref,
                claim_scope=claim["claim_scope"],
                proposed_world_validity=claim["proposed_world_validity"],
                observed_at=observation.observed_at,
                proposed_at=proposed_at,
                supporting_observation_ids=[observation.observation_id],
                signals=signals,
                unknowns=claim["unknowns"],
            )
            candidates.append(candidate)

        packet = build_temporal_candidate_packet(
            alias=record["alias"], candidates=candidates, generated_at=proposed_at
        )
        return {
            "kind": "temporal_candidate_proposal",
            "contract_version": "temporal-candidate-proposal/1",
            "target_wiki": record["alias"],
            "observation": observation.to_dict(),
            "packet": packet,
            "disposition": "candidate_only",
            "mutation": {"allowed": False, "commands": []},
            "stewardship": {"required": True, "authority": "target_wiki_steward"},
        }
    except WikiMcpError:
        raise
    except (TemporalContractError, TypeError, ValueError, UnicodeError) as exc:
        raise WikiMcpError(
            "INVALID_INPUT",
            str(exc),
            {"surface": "wiki_build_temporal_candidates"},
        ) from exc


def wiki_reconcile_temporal_candidates(
    alias: str,
    proposals: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reconcile exact, read-only temporal candidate proposals for one wiki."""

    get_wiki(alias)
    try:
        if not isinstance(proposals, Sequence) or isinstance(proposals, (str, bytes)):
            raise ValueError("proposals must be an array")
        if not 1 <= len(proposals) <= 16:
            raise ValueError("proposals must contain between 1 and 16 envelopes")

        candidates = []
        observations = {}
        for index, raw_proposal in enumerate(proposals):
            proposal = _exact_mapping(raw_proposal, _TEMPORAL_PROPOSAL_FIELDS, f"proposals[{index}]")
            if proposal["kind"] != "temporal_candidate_proposal":
                raise ValueError(f"proposals[{index}].kind is unsupported")
            if proposal["contract_version"] != "temporal-candidate-proposal/1":
                raise ValueError(f"proposals[{index}].contract_version is unsupported")
            if proposal["target_wiki"] != alias:
                raise ValueError(f"proposals[{index}].target_wiki must match alias")
            if proposal["disposition"] != "candidate_only":
                raise ValueError(f"proposals[{index}].disposition is unsupported")
            if proposal["mutation"] != {"allowed": False, "commands": []}:
                raise ValueError(f"proposals[{index}].mutation is not read-only")
            if proposal["stewardship"] != {"required": True, "authority": "target_wiki_steward"}:
                raise ValueError(f"proposals[{index}].stewardship is unsupported")

            packet = parse_temporal_candidate_packet(proposal["packet"])
            if packet.alias != alias:
                raise ValueError(f"proposals[{index}].packet.wiki.alias must match alias")
            observation = parse_observation_ref(proposal["observation"])
            observations[observation.observation_id] = observation
            candidates.extend(packet.candidates)

        unique_candidates = {candidate.candidate_id for candidate in candidates}
        if len(unique_candidates) > 100:
            raise TemporalContractError(
                "TEMPORAL_LIMIT_EXCEEDED",
                "candidates exceeds 100 unique IDs",
                {"field": "candidates"},
            )
        if len(observations) > 16:
            raise TemporalContractError(
                "TEMPORAL_LIMIT_EXCEEDED",
                "observations exceeds 16 unique IDs",
                {"field": "observations"},
            )

        return reconcile_temporal_candidates(candidates=candidates, observations=observations).to_dict()
    except WikiMcpError:
        raise
    except TemporalContractError as exc:
        raise WikiMcpError(exc.code, str(exc), {"surface": "wiki_reconcile_temporal_candidates", **exc.details}) from exc
    except (TypeError, ValueError) as exc:
        raise WikiMcpError(
            "INVALID_INPUT",
            str(exc),
            {"surface": "wiki_reconcile_temporal_candidates"},
        ) from exc


def get_page(alias: str, page: str, max_chars: int = 4_000) -> dict[str, Any]:
    runtime = _legacy_runtime(alias)
    resolved = _page_or_error(page, runtime)
    limit = _bounded_int(max_chars, default=4_000, upper=MAX_PAGE_CHARS)
    return {
        "kind": "page",
        **runtime.page_record(resolved),
        "content": runtime.page_content(resolved, max_chars=limit),
    }


def get_source_excerpt(
    alias: str,
    page: str | None = None,
    source: str | None = None,
    max_chars: int = 1_600,
) -> dict[str, Any]:
    runtime = _legacy_runtime(alias)
    if bool(page) == bool(source):
        raise WikiMcpError(
            "INVALID_INPUT",
            "Provide exactly one of page or source",
            {"page": page, "source": source},
        )

    if page:
        resolved = _page_or_error(page, runtime)
        source = str(runtime.pages[resolved].frontmatter.get("source") or "")
        if not source:
            raise WikiMcpError(
                "SOURCE_NOT_FOUND",
                "Page has no source frontmatter",
                {"page": resolved},
            )

    assert source is not None
    source_path = runtime.source_path(source)
    if source_path is None:
        raise WikiMcpError(
            "INVALID_INPUT",
            "Source must be a relative path inside the configured sources directory",
            {"source": source},
        )
    if not source_path.is_file():
        raise WikiMcpError(
            "SOURCE_NOT_FOUND",
            "Source file not found",
            {"source": source},
        )
    try:
        text = source_path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as exc:
        raise WikiMcpError(
            "SOURCE_NOT_TEXT",
            "Source file is not UTF-8 text",
            {"source": source},
        ) from exc

    limit = _bounded_int(max_chars, default=1_600, upper=MAX_SOURCE_CHARS)
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n\n[truncated]"
    return {
        "kind": "source_excerpt",
        "source": str(source).replace("\\", "/"),
        "content": text,
    }


def _legacy_runtime(alias: str) -> LegacyRuntime:
    record = get_wiki(alias)
    wiki_root = Path(record["path"]).expanduser().resolve()
    inspection = inspect_wiki_config(wiki_root)
    if inspection.config is not None:
        content = inspection.config.content
    elif inspection.status == "legacy_missing":
        content = ContentConfig()
    else:
        assert inspection.error is not None
        raise WikiMcpError(
            inspection.error.code,
            inspection.error.message,
            inspection.error.details,
        )
    return LegacyRuntime(wiki_root, content=content)


def _read_control_file(wiki_root: Path, filename: str, max_chars: int, required: bool) -> dict[str, Any] | None:
    path = (wiki_root / filename).resolve()
    if path.parent != wiki_root:
        raise WikiMcpError(
            "INVALID_INPUT",
            "Control file must be at wiki root",
            {"file": filename},
        )
    if not path.is_file():
        if required:
            raise WikiMcpError(
                "CONTROL_FILE_MISSING",
                "Wiki control file is missing",
                {"file": filename, "path": str(path)},
            )
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise WikiMcpError(
            "CONTROL_FILE_NOT_TEXT",
            "Wiki control file is not UTF-8 text",
            {"file": filename},
        ) from exc

    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars].rstrip() + "\n\n[truncated]"
    return {"content": content, "truncated": truncated}


def _exact_mapping(raw: Any, allowed: frozenset[str], field: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{field} must be an object")
    keys = set(raw)
    if keys != allowed:
        missing = sorted(allowed - keys)
        extra = sorted(keys - allowed)
        detail = []
        if missing:
            detail.append(f"missing {', '.join(missing)}")
        if extra:
            detail.append(f"unsupported {', '.join(extra)}")
        raise ValueError(f"{field} has invalid fields ({'; '.join(detail)})")
    return raw


def _unified_source(raw: Any, *, claims_present: bool) -> dict[str, Any]:
    value = _exact_mapping(
        raw,
        _UNIFIED_TEMPORAL_SOURCE_FIELDS if claims_present else _UNIFIED_SOURCE_FIELDS,
        "source",
    )
    content_hash = value["content_hash"]
    if not isinstance(content_hash, str) or len(content_hash) != 64 or any(
        character not in "0123456789abcdef" for character in content_hash
    ):
        raise ValueError("source.content_hash must be lowercase SHA-256")
    if claims_present:
        payload_text = value["payload_text"]
        if not isinstance(payload_text, str):
            raise ValueError("source.payload_text must be text")
        payload = payload_text.encode("utf-8")
        if len(payload) > 65_536:
            raise ValueError("source.payload_text exceeds 65536 UTF-8 bytes")
        if hashlib.sha256(payload).hexdigest() != content_hash:
            raise ValueError("source.content_hash does not match source.payload_text")
    return dict(value)


def _discovery_task_proposals(alias: str, packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Adapt supported v1 discovery candidates into exact v1 task proposals."""

    proposals: list[dict[str, Any]] = []
    for index, raw_candidate in enumerate(packet.get("candidates", ())):
        if not isinstance(raw_candidate, Mapping):
            raise ValueError(f"discovery.candidates[{index}] must be an object")
        kind = raw_candidate.get("kind")
        if kind not in MAINTENANCE_CANDIDATE_KINDS:
            continue
        page = raw_candidate.get("page")
        pages = [page] if page else []
        task_evidence: list[dict[str, str]] = []
        for evidence_index, raw_evidence in enumerate(raw_candidate.get("evidence", ())):
            if not isinstance(raw_evidence, Mapping):
                raise ValueError(
                    f"discovery.candidates[{index}].evidence[{evidence_index}] must be an object"
                )
            ref = raw_evidence.get("source") or raw_evidence.get("page") or raw_candidate.get("id")
            note = raw_evidence.get("content") or raw_candidate.get("diagnostic")
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError(f"discovery.candidates[{index}] evidence ref is invalid")
            if not isinstance(note, str) or not note.strip():
                raise ValueError(f"discovery.candidates[{index}] evidence note is invalid")
            task_evidence.append({"ref": ref, "note": note[:1_000]})
        if not task_evidence:
            task_evidence = [{
                "ref": str(raw_candidate.get("id") or f"discovery:{index}"),
                "note": str(raw_candidate.get("diagnostic") or "discovery candidate"),
            }]
        task = build_candidate_proposal(
            alias=alias,
            kind=kind,
            diagnostic=raw_candidate["diagnostic"],
            review_question=raw_candidate["review_question"],
            pages=pages,
            evidence=task_evidence,
        )
        proposals.append(adapt_legacy_task_proposal(task))
    return proposals


def _validate_temporal_pages(entity: EntityRef, runtime: LegacyRuntime, field: str) -> None:
    refs = []
    if entity.kind == "resolved_page":
        refs.append(entity.page)
    elif entity.kind == "ambiguous":
        refs.extend(ref.page for ref, _ in entity.candidates if ref.kind == "resolved_page")
    for page in refs:
        if page not in runtime.pages:
            raise ValueError(f"{field} references unknown wiki page: {page}")


def _temporal_signals(raw: Any, observation_id: str) -> list[dict[str, Any]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("signals must be an array")
    if len(raw) > 64:
        raise ValueError("signals exceeds 64 items")
    result = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"signals[{index}] must be an object")
        allowed = frozenset({"kind", "detail"}) if "detail" in item else frozenset({"kind"})
        item = _exact_mapping(item, allowed, f"signals[{index}]")
        normalized = {"kind": item["kind"], "observation_ids": [observation_id]}
        if "detail" in item:
            normalized["detail"] = item["detail"]
        result.append(normalized)
    return result


def _page_or_error(raw: str, runtime: LegacyRuntime) -> str:
    page = str(raw).replace("\\", "/")
    page = page[2:] if page.startswith("./") else page
    if page in runtime.pages:
        return page
    matches = difflib.get_close_matches(page, sorted(runtime.pages), n=5)
    raise WikiMcpError(
        "PAGE_NOT_FOUND",
        "Unknown wiki page",
        {"page": raw, "suggestions": matches},
    )


def _bounded_int(value: int, *, default: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, upper))
