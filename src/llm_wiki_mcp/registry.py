from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from llm_wiki_mcp.errors import WikiMcpError


REGISTRY_FILENAME = "registry.json"
ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
REQUIRED_WIKI_FILES = ("wiki-agent.md", "index.md", "log.md")
CONTEXT_TOOL_FILES = ("scripts/query.py", "scripts/wiki_graph.py")


def registry_home() -> Path:
    raw = os.environ.get("LLM_WIKI_HOME")
    if not raw:
        raise WikiMcpError(
            "CONFIG_REQUIRED",
            "LLM_WIKI_HOME must be set to the current agent's llm-wiki home",
            {"env": "LLM_WIKI_HOME"},
        )
    return Path(raw).expanduser().resolve()


def registry_path() -> Path:
    return registry_home() / REGISTRY_FILENAME


def list_wikis() -> dict[str, Any]:
    home = registry_home()
    registry = _load_registry(home)
    return {
        "registry_home": str(home),
        "wikis": [
            {"alias": alias, **record}
            for alias, record in sorted(registry["wikis"].items())
        ],
    }


def register_wiki(alias: str, path: str | Path, created_by: str = "manual") -> dict[str, Any]:
    alias = _validate_alias(alias)
    wiki_path = Path(path).expanduser().resolve()
    diagnostics = inspect_wiki_path(wiki_path)
    if not diagnostics["is_wiki"]:
        raise WikiMcpError(
            "INVALID_WIKI",
            "Path does not look like an llm-wiki",
            {
                "path": str(wiki_path),
                "missing": diagnostics["missing_required_files"],
            },
        )

    home = registry_home()
    registry = _load_registry(home)
    existing = registry["wikis"].get(alias)
    if existing and existing.get("path") != str(wiki_path):
        raise WikiMcpError(
            "ALIAS_EXISTS",
            "Alias already points at a different wiki",
            {"alias": alias, "path": existing.get("path")},
        )

    record = existing or {
        "path": str(wiki_path),
        "created_by": created_by,
        "registered_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    record["path"] = str(wiki_path)
    record.setdefault("created_by", created_by)
    registry["wikis"][alias] = record
    _save_registry(home, registry)

    return {"alias": alias, **record, "warnings": diagnostics["warnings"]}


def unregister_wiki(alias: str) -> dict[str, Any]:
    alias = _validate_alias(alias)
    home = registry_home()
    registry = _load_registry(home)
    record = registry["wikis"].pop(alias, None)
    if record is None:
        raise WikiMcpError(
            "ALIAS_NOT_FOUND",
            "No wiki registered for alias",
            {"alias": alias},
        )
    _save_registry(home, registry)
    return {"alias": alias, **record}


def get_wiki(alias: str) -> dict[str, Any]:
    alias = _validate_alias(alias)
    registry = _load_registry(registry_home())
    record = registry["wikis"].get(alias)
    if record is None:
        raise WikiMcpError(
            "ALIAS_NOT_FOUND",
            "No wiki registered for alias",
            {"alias": alias},
        )
    return {"alias": alias, **record}


def doctor(alias: str) -> dict[str, Any]:
    record = get_wiki(alias)
    path = Path(record["path"]).expanduser().resolve()
    diagnostics = inspect_wiki_path(path)
    return {
        "alias": record["alias"],
        "path": str(path),
        "exists": diagnostics["exists"],
        "is_dir": diagnostics["is_dir"],
        "is_wiki": diagnostics["is_wiki"],
        "required_files": diagnostics["required_files"],
        "context_tooling": diagnostics["context_tooling"],
        "warnings": diagnostics["warnings"],
    }


def inspect_wiki_path(path: Path) -> dict[str, Any]:
    exists = path.exists()
    is_dir = path.is_dir()
    required = {
        name: (path / name).is_file() if exists and is_dir else False
        for name in REQUIRED_WIKI_FILES
    }
    tooling = {
        name: (path / name).is_file() if exists and is_dir else False
        for name in CONTEXT_TOOL_FILES
    }
    missing_required = [name for name, present in required.items() if not present]
    warnings = [
        f"{name} missing"
        for name, present in tooling.items()
        if not present
    ]
    return {
        "path": str(path),
        "exists": exists,
        "is_dir": is_dir,
        "is_wiki": exists and is_dir and not missing_required,
        "required_files": required,
        "context_tooling": tooling,
        "missing_required_files": missing_required,
        "warnings": warnings,
    }


def _validate_alias(alias: str) -> str:
    if not isinstance(alias, str) or not ALIAS_RE.match(alias):
        raise WikiMcpError(
            "INVALID_ALIAS",
            "Alias must be 1-64 characters and contain only letters, numbers, dot, dash, or underscore",
            {"alias": alias},
        )
    return alias


def _load_registry(home: Path) -> dict[str, Any]:
    path = home / REGISTRY_FILENAME
    if not path.exists():
        return {"version": 1, "wikis": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WikiMcpError(
            "REGISTRY_INVALID",
            "Registry JSON could not be parsed",
            {"path": str(path), "error": str(exc)},
        ) from exc

    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("wikis"), dict):
        raise WikiMcpError(
            "REGISTRY_INVALID",
            "Registry must be an object with version 1 and a wikis object",
            {"path": str(path)},
        )
    return data


def _save_registry(home: Path, data: dict[str, Any]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    path = home / REGISTRY_FILENAME
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
