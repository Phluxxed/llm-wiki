from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .config import CURRENT_RUNTIME_CONTRACT, CURRENT_SCHEMA_VERSION, inspect_wiki_config
from .script_drift import inspect_scripts


def inspect_runtime(wiki_root: str | Path) -> dict[str, Any]:
    root = Path(wiki_root).expanduser().resolve()
    config = inspect_wiki_config(root)
    scripts = inspect_scripts(root)
    blockers = [path for path, result in scripts.items() if result["status"] == "modified_unknown"]
    adapter_runtime = _adapter_runtime(root, scripts)
    loci = _loci_status(config)

    if config.error is not None:
        compatibility_status = "incompatible"
    elif blockers:
        compatibility_status = "blocked"
    elif config.status == "legacy_missing":
        compatibility_status = "migration_available"
    elif adapter_runtime["status"] == "missing_runtime":
        compatibility_status = "incompatible"
        blockers.append("adapter_runtime")
    else:
        compatibility_status = "compatible"

    config_payload: dict[str, Any] = {"status": config.status}
    if config.config is not None:
        config_payload.update(
            {
                "schema_version": config.config.schema_version,
                "runtime_contract": config.config.runtime_contract,
                "profile": config.config.profile,
            }
        )
    if config.error is not None:
        config_payload["error"] = {
            "code": config.error.code,
            "message": config.error.message,
            "details": config.error.details,
        }

    return {
        "compatibility": {
            "status": compatibility_status,
            "blockers": blockers,
        },
        "config": config_payload,
        "runtime": {
            "version": _runtime_version(),
            "schema_version": CURRENT_SCHEMA_VERSION,
            "contract": CURRENT_RUNTIME_CONTRACT,
        },
        "scripts": scripts,
        "adapter_runtime": adapter_runtime,
        "providers": {
            "seed": {"status": "ready"},
            "frontmatter": {"status": "ready"},
            "text": {"status": "ready"},
            "graph": {"status": "ready"},
            "source": {"status": "ready"},
            "loci": loci,
        },
        "migration": _migration_status(root),
    }


def _migration_status(root: Path) -> dict[str, Any]:
    latest = root / ".llm-wiki" / "migrations" / "latest.json"
    if not latest.is_file():
        return {"last_receipt": None, "verification": "not_run"}
    try:
        receipt = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"last_receipt": str(latest.relative_to(root)), "verification": "invalid_receipt"}
    return {
        "last_receipt": str(latest.relative_to(root)),
        "verification": str(receipt.get("verification") or "unknown"),
    }


def _runtime_version() -> str:
    try:
        return version("llm-wiki")
    except PackageNotFoundError:
        return "development"


def _adapter_runtime(root: Path, scripts: dict[str, dict]) -> dict[str, Any]:
    if any(result["status"] != "compatible_adapter" for result in scripts.values()):
        return {"status": "not_applicable"}
    python = root / ".venv" / "bin" / "python3"
    if not python.is_file():
        return {"status": "external_runtime_required"}
    query = root / "scripts" / "query.py"
    try:
        completed = subprocess.run(
            [str(python), str(query), "--help"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "missing_runtime", "failure": type(exc).__name__}
    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout).strip()
        return {
            "status": "missing_runtime",
            "exit_code": completed.returncode,
            "detail": output[-500:],
        }
    return {"status": "ready", "python": str(python.relative_to(root))}


def _loci_status(config) -> dict[str, Any]:
    if config.config is not None and "loci" not in config.config.compiler.providers:
        return {"status": "disabled", "opt_out": True, "transport": "mcp_stdio"}
    command = os.environ.get("LLM_WIKI_LOCI_MCP_COMMAND", "loci-mcp")
    if shutil.which(command) is None:
        return {
            "status": "degraded",
            "code": "LOCI_MCP_UNAVAILABLE",
            "transport": "mcp_stdio",
            "freshness": "not_checked",
        }
    return {
        "status": "ready",
        "transport": "mcp_stdio",
        "freshness": "checked_on_provider_use",
    }
