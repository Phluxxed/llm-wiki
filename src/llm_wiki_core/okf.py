"""Read and validate OKF 0.2 metadata without granting content authority.

The source document remains authoritative. These projections never rewrite its
frontmatter, infer verification, execute resources, or replace temporal history.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timezone
import math
import posixpath
import re
from typing import Any


_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
_ACTOR = re.compile(r"^(?:(?:human|process):[^\s:]+|[^\s/]+/[^\s/]+)$")
_FAMILIES = ("generated", "verified", "sources", "usage_window", "stale_after")
_STATUSES = {"draft", "stable", "deprecated"}


def has_metadata(frontmatter: Mapping[str, Any]) -> bool:
    """Leave legacy page responses unchanged until native metadata is present."""
    status = frontmatter.get("status")
    return (
        any(key in frontmatter for key in _FAMILIES)
        or (isinstance(status, str) and status in _STATUSES)
        or ("status" in frontmatter and not isinstance(status, str))
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _instant(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None and value.utcoffset() is not None else None
    if not isinstance(value, str) or _INSTANT.fullmatch(value) is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clock(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    result = _instant(value)
    if result is None:
        raise ValueError("metadata evaluation time must include a UTC offset")
    return result


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _event_errors(value: Any, field: str, *, require_at: bool) -> list[str]:
    if not isinstance(value, Mapping):
        return [f"{field} must be a mapping"]
    errors = []
    if not isinstance(value.get("by"), str) or _ACTOR.fullmatch(value["by"]) is None:
        errors.append(f"{field}.by must identify human:<id>, process:<id>, or <producer>/<version>")
    if (require_at or "at" in value) and _instant(value.get("at")) is None:
        errors.append(f"{field}.at must be an ISO 8601 datetime with a UTC offset")
    return errors


def _window_errors(value: Any, field: str) -> list[str]:
    if not isinstance(value, Mapping):
        return [f"{field} must be a mapping with from and to datetimes"]
    errors = []
    for key in ("from", "to"):
        if _instant(value.get(key)) is None:
            errors.append(f"{field}.{key} must be an ISO 8601 datetime with a UTC offset")
    start, end = _instant(value.get("from")), _instant(value.get("to"))
    if start is not None and end is not None and start > end:
        errors.append(f"{field}.from must not follow to")
    return errors


def validate_metadata(frontmatter: Mapping[str, Any]) -> list[str]:
    """Validate present standard fields; unknown extension keys stay permitted."""
    errors: list[str] = []
    if "generated" in frontmatter:
        errors.extend(_event_errors(frontmatter["generated"], "generated", require_at=False))
    if "verified" in frontmatter:
        verified = frontmatter["verified"]
        entries = [verified] if isinstance(verified, Mapping) else verified
        if not isinstance(entries, list):
            errors.append("verified must be a list or one verification mapping")
        else:
            for index, event in enumerate(entries):
                errors.extend(_event_errors(event, f"verified[{index}]", require_at=True))
    if "status" in frontmatter and (not isinstance(frontmatter["status"], str) or frontmatter["status"] not in _STATUSES):
        errors.append("status must be draft, stable, or deprecated")
    if "stale_after" in frontmatter and _instant(frontmatter["stale_after"]) is None:
        errors.append("stale_after must be an ISO 8601 datetime with a UTC offset")
    if "usage_window" in frontmatter:
        errors.extend(_window_errors(frontmatter["usage_window"], "usage_window"))
    if "sources" in frontmatter:
        sources = frontmatter["sources"]
        if not isinstance(sources, list):
            errors.append("sources must be a list of mappings")
        else:
            ids: set[str] = set()
            for index, source in enumerate(sources):
                field = f"sources[{index}]"
                if not isinstance(source, Mapping):
                    errors.append(f"{field} must be a mapping")
                    continue
                if not _nonempty(source.get("resource")):
                    errors.append(f"{field}.resource must be a nonempty string")
                for key in ("id", "title", "author"):
                    if key in source and not _nonempty(source[key]):
                        errors.append(f"{field}.{key} must be a nonempty string")
                source_id = source.get("id")
                if isinstance(source_id, str):
                    if source_id in ids:
                        errors.append(f"{field}.id must be unique within sources")
                    ids.add(source_id)
                if "usage_count" in source:
                    count = source["usage_count"]
                    if isinstance(count, bool) or not isinstance(count, (int, float)) or count < 0 or (isinstance(count, float) and not math.isfinite(count)):
                        errors.append(f"{field}.usage_count must be a finite nonnegative number")
                if "last_modified" in source and _instant(source["last_modified"]) is None:
                    errors.append(f"{field}.last_modified must be an ISO 8601 datetime with a UTC offset")
                if "usage_window" in source:
                    errors.extend(_window_errors(source["usage_window"], f"{field}.usage_window"))
    if frontmatter.get("type") == "Attested Computation" and not _nonempty(frontmatter.get("runtime")):
        errors.append("runtime must be a nonempty string for Attested Computation")
    return errors


def interpret_metadata(
    frontmatter: Mapping[str, Any],
    *,
    as_of: date | datetime | str | None = None,
    known_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Return JSON-safe provenance and conservative, advisory derived signals."""
    now = _clock(as_of)
    cutoff = min(now, _clock(known_at)) if known_at is not None else now
    errors = validate_metadata(frontmatter)
    # Legacy status labels remain readable, without turning them into OKF tiers.
    status = frontmatter.get("status", "stable")
    generated = frontmatter.get("generated")
    raw_verified = frontmatter.get("verified", [])
    verified = [raw_verified] if isinstance(raw_verified, Mapping) else raw_verified
    sources = frontmatter.get("sources", [])
    flags = []
    if errors:
        flags.append("okf_metadata_invalid")
    generated_at = _instant(generated.get("at")) if isinstance(generated, Mapping) else None
    if generated_at is not None and generated_at > cutoff:
        flags.append("generation_after_known_at" if known_at is not None else "generation_in_future")
    eligible = []
    for event in verified if isinstance(verified, list) else []:
        if _event_errors(event, "verified", require_at=True):
            continue
        verified_at = _instant(event["at"])
        if verified_at > cutoff:
            flags.append("verification_after_known_at" if known_at is not None else "verification_in_future")
        elif generated_at is not None and verified_at < generated_at:
            flags.append("verification_predates_generation")
        else:
            eligible.append(event)
    trust = "unverified"
    if eligible and not errors and not (generated_at is not None and generated_at > cutoff):
        trust = "human-reviewed" if any(event["by"].startswith("human:") for event in eligible) else "machine-confirmed"
    if trust == "unverified":
        flags.append("verification_missing")
    stale_after = _instant(frontmatter.get("stale_after"))
    freshness = "unknown" if stale_after is None else "stale" if now >= stale_after else "fresh"
    if freshness == "stale":
        flags.append("okf_stale")
    if isinstance(status, str) and status in {"draft", "deprecated"}:
        flags.append(f"okf_status_{status}")
    result = {
        "generated": _json_safe(generated) if isinstance(generated, Mapping) else None,
        "verified": _json_safe(verified) if isinstance(verified, list) else [],
        "sources": _json_safe(sources) if isinstance(sources, list) else [],
        "status": _json_safe(status),
        "stale_after": _json_safe(frontmatter.get("stale_after")),
        "trust": trust,
        "freshness": freshness,
        "evaluated_at": now.isoformat(),
        "flags": list(dict.fromkeys(flags)),
        "errors": errors,
    }
    if "usage_window" in frontmatter:
        result["usage_window"] = _json_safe(frontmatter["usage_window"])
    return result


def local_source_refs(
    frontmatter: Mapping[str, Any],
    *,
    source_directory: str = "sources",
    page_path: str | None = None,
) -> tuple[str, ...]:
    """Enumerate existing grounding plus plural resources inside the source layer.

    URL/scope resources are never fetched. Existing singular/evidence values
    retain their invalid paths so callers can report their established errors.
    """
    refs = []
    source = frontmatter.get("source")
    if isinstance(source, str) and source.strip():
        refs.append(source)
    evidence = frontmatter.get("evidence")
    for ref in evidence if isinstance(evidence, (list, tuple)) else [evidence]:
        if isinstance(ref, str) and ref.strip():
            refs.append(ref)
    prefix = source_directory.rstrip("/") + "/"
    for item in frontmatter.get("sources", []) if isinstance(frontmatter.get("sources", []), list) else []:
        resource = item.get("resource") if isinstance(item, Mapping) else None
        if not isinstance(resource, str) or not resource.strip() or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", resource) or resource.startswith("//"):
            continue
        if resource.startswith("/"):
            reference = posixpath.normpath(resource[1:])
        elif resource.startswith(("./", "../")) and page_path is not None:
            reference = posixpath.normpath(posixpath.join(posixpath.dirname(page_path), resource))
        else:
            reference = posixpath.normpath(resource)
        if reference.startswith(prefix):
            refs.append(reference)
    return tuple(dict.fromkeys(refs))
