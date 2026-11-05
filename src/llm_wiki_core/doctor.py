from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import subprocess
from typing import Any

from .config import CURRENT_RUNTIME_CONTRACT, CURRENT_SCHEMA_VERSION, DEFAULT_PROVIDERS, inspect_wiki_config
from .script_drift import inspect_scripts


def inspect_runtime(wiki_root: str | Path) -> dict[str, Any]:
    root = Path(wiki_root).expanduser().resolve()
    config = inspect_wiki_config(root)
    scripts = inspect_scripts(root)
    blockers = [path for path, result in scripts.items() if result["status"] == "modified_unknown"]
    adapter_runtime = _adapter_runtime(root, scripts)

    if config.error is not None and config.status != "migration_required":
        compatibility_status = "incompatible"
    elif blockers:
        compatibility_status = "blocked"
    elif config.status in {"legacy_missing", "migration_required"}:
        compatibility_status = "migration_available"
    elif any(result["status"] == "outdated_adapter" for result in scripts.values()):
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
            name: {
                "status": (
                    "unavailable" if config.config is None
                    else "ready" if name in config.config.compiler.providers
                    else "disabled"
                )
            }
            for name in DEFAULT_PROVIDERS
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
