from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any
import uuid

from .compiler import compile_context
from .config import CURRENT_RUNTIME_CONTRACT, CURRENT_SCHEMA_VERSION, inspect_wiki_config
from .contracts import CompileRequest, ContractError
from .doctor import inspect_runtime
from .script_drift import ADAPTER_MARKER, inspect_scripts


MIGRATION_PLAN_VERSION = "1"
_ALLOWED_TARGETS = frozenset({".llm-wiki.toml", "scripts/query.py", "scripts/wiki_graph.py"})
_RECEIPT_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")

QUERY_ADAPTER = f'''\
"""Thin compatibility entrypoint for the canonical llm-wiki runtime."""

{ADAPTER_MARKER}
from pathlib import Path

try:
    from llm_wiki_core.legacy_cli import main as _canonical_main
except ImportError as exc:
    raise SystemExit("install or upgrade llm-wiki to use this wiki adapter") from exc


WIKI_ROOT = Path(__file__).resolve().parent.parent


def main(argv=None):
    return _canonical_main(argv=argv, wiki_root=WIKI_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
'''

GRAPH_ADAPTER = f'''\
"""Thin compatibility imports for the canonical llm-wiki graph runtime."""

{ADAPTER_MARKER}
try:
    from llm_wiki_core.legacy_graph import *  # noqa: F403
except ImportError as exc:
    raise SystemExit("install or upgrade llm-wiki to use this wiki adapter") from exc
'''


@dataclass(frozen=True)
class MigrationOperation:
    action: str
    path: str
    content: str
    before_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "path": self.path,
            "content": self.content,
            "before_sha256": self.before_sha256,
            "after_sha256": _sha256(self.content.encode("utf-8")),
        }


@dataclass(frozen=True)
class MigrationPlan:
    wiki_root: Path
    operations: tuple[MigrationOperation, ...]
    blockers: tuple[str, ...]
    translated_customizations: tuple[str, ...]
    plan_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "wiki_migration_plan",
            "plan_version": MIGRATION_PLAN_VERSION,
            "plan_hash": self.plan_hash,
            "wiki_root": str(self.wiki_root),
            "operations": [operation.to_dict() for operation in self.operations],
            "blockers": list(self.blockers),
            "translated_customizations": list(self.translated_customizations),
        }


class MigrationError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


def inspect_migration(wiki_root: str | Path) -> MigrationPlan:
    root = Path(wiki_root).expanduser().resolve()
    _validate_wiki_paths(root)
    config = inspect_wiki_config(root)
    scripts = inspect_scripts(root)
    blockers: list[str] = []
    customizations = sorted(
        {
            item
            for result in scripts.values()
            for item in result.get("customizations", [])
        }
    )
    operations: list[MigrationOperation] = []

    if config.error is not None:
        blockers.append(f".llm-wiki.toml:{config.error.code}")
    elif config.status == "legacy_missing":
        operations.append(
            _operation(root, ".llm-wiki.toml", _render_config(customizations))
        )

    adapter_content = {
        "scripts/query.py": QUERY_ADAPTER,
        "scripts/wiki_graph.py": GRAPH_ADAPTER,
    }
    for path, result in scripts.items():
        status = result["status"]
        if status == "modified_unknown":
            blockers.append(f"{path}:modified_unknown")
        elif status != "compatible_adapter":
            operations.append(_operation(root, path, adapter_content[path]))

    operations.sort(key=lambda operation: operation.path)
    payload = {
        "plan_version": MIGRATION_PLAN_VERSION,
        "wiki_root": str(root),
        "operations": [operation.to_dict() for operation in operations],
        "blockers": blockers,
        "translated_customizations": customizations,
    }
    plan_hash = _sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return MigrationPlan(
        wiki_root=root,
        operations=tuple(operations),
        blockers=tuple(blockers),
        translated_customizations=tuple(customizations),
        plan_hash=plan_hash,
    )


def dry_run_migration(wiki_root: str | Path) -> MigrationPlan:
    return inspect_migration(wiki_root)


def apply_migration(
    wiki_root: str | Path,
    *,
    plan_hash: str,
    _fail_after: int | None = None,
) -> dict[str, Any]:
    """Apply an inspected migration as a receipt-backed filesystem transaction."""
    plan = inspect_migration(wiki_root)
    if plan.plan_hash != plan_hash:
        raise MigrationError(
            "MIGRATION_PLAN_STALE",
            "Migration plan no longer matches the inspected wiki state",
            {"expected": plan_hash, "actual": plan.plan_hash},
        )
    if plan.blockers:
        raise MigrationError(
            "MIGRATION_BLOCKED",
            "Migration cannot replace unrecognized wiki-local behavior",
            {"blockers": list(plan.blockers)},
        )
    if not plan.operations:
        return {
            "kind": "wiki_migration_receipt",
            "status": "no_op",
            "plan_hash": plan.plan_hash,
            "wiki_root": str(plan.wiki_root),
            "operations": [],
        }

    _validate_operations(plan.operations)
    receipt_id = _new_receipt_id()
    migration_root = _migration_root(plan.wiki_root)
    receipt_root = _safe_receipt_root(migration_root, receipt_id)
    backups_root = receipt_root / "backups"
    backups_root.mkdir(parents=True, exist_ok=False)

    operation_receipts: list[dict[str, Any]] = []
    for operation in plan.operations:
        target = _safe_target(plan.wiki_root, operation.path)
        mode = target.stat().st_mode & 0o7777 if target.is_file() else None
        backup_relative: str | None = None
        if operation.before_sha256 is not None:
            backup = backups_root / operation.path
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            backup_relative = backup.relative_to(receipt_root).as_posix()
        operation_receipts.append(
            {
                **operation.to_dict(),
                "mode": mode,
                "backup": backup_relative,
            }
        )

    receipt: dict[str, Any] = {
        "kind": "wiki_migration_receipt",
        "receipt_version": "1",
        "receipt_id": receipt_id,
        "status": "applying",
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "plan_hash": plan.plan_hash,
        "wiki_root": str(plan.wiki_root),
        "operations": operation_receipts,
        "verification": "not_run",
    }
    receipt_path = receipt_root / "receipt.json"
    latest_path = migration_root / "latest.json"
    _write_receipt(receipt_path, latest_path, receipt)

    applied = 0
    try:
        for operation in plan.operations:
            target = _safe_target(plan.wiki_root, operation.path)
            _assert_precondition(target, operation.before_sha256, operation.path)
            _atomic_write_text(target, operation.content)
            applied += 1
            if _fail_after is not None and applied >= _fail_after:
                raise RuntimeError("injected migration failure")

        verification = verify_migration(plan.wiki_root)
        if verification["status"] != "passed":
            raise MigrationError(
                "MIGRATION_VERIFICATION_FAILED",
                "Applied migration did not pass runtime verification",
                {"verification": verification},
            )
    except Exception as exc:
        recovery = _restore_receipt_targets(plan.wiki_root, receipt_root, receipt)
        receipt.update(
            {
                "status": "failed",
                "updated_at": _utc_now(),
                "verification": "failed",
                "recovery": recovery,
                "failure": {"type": type(exc).__name__, "message": str(exc)},
            }
        )
        _write_receipt(receipt_path, latest_path, receipt)
        raise MigrationError(
            "MIGRATION_APPLY_FAILED",
            "Migration failed and its target files were restored",
            {
                "receipt_id": receipt_id,
                "recovery": recovery,
                "cause": exc.code if isinstance(exc, MigrationError) else type(exc).__name__,
            },
        ) from exc

    receipt.update(
        {
            "status": "applied",
            "updated_at": _utc_now(),
            "verification": "passed",
            "verification_result": verification,
        }
    )
    _write_receipt(receipt_path, latest_path, receipt)
    return receipt


def verify_migration(wiki_root: str | Path) -> dict[str, Any]:
    root = Path(wiki_root).expanduser().resolve()
    runtime = inspect_runtime(root)
    plan = inspect_migration(root)
    compiler_smoke = "failed"
    compiler_error: dict[str, Any] | None = None
    try:
        compiled = compile_context(
            root,
            CompileRequest.from_mapping(
                {
                    "alias": "verification",
                    "question": "What is in this wiki?",
                    "seeds": [],
                }
            ),
        )
        if compiled.runtime_contract == CURRENT_RUNTIME_CONTRACT:
            compiler_smoke = "passed"
    except ContractError as exc:
        compiler_error = exc.to_dict()
    except Exception as exc:  # verification must report unexpected failures
        compiler_error = {
            "code": "COMPILER_SMOKE_FAILED",
            "message": "Compiler smoke check failed",
            "details": {"type": type(exc).__name__},
        }

    compatibility = runtime["compatibility"]["status"]
    passed = (
        compatibility == "compatible"
        and compiler_smoke == "passed"
        and not plan.blockers
        and not plan.operations
    )
    result: dict[str, Any] = {
        "kind": "wiki_migration_verification",
        "status": "passed" if passed else "failed",
        "compatibility": compatibility,
        "compiler_smoke": compiler_smoke,
        "pending_operations": [operation.path for operation in plan.operations],
        "blockers": list(plan.blockers),
    }
    if compiler_error is not None:
        result["compiler_error"] = compiler_error
    return result


def rollback_migration(wiki_root: str | Path, *, receipt_id: str) -> dict[str, Any]:
    root = Path(wiki_root).expanduser().resolve()
    if not _RECEIPT_ID.fullmatch(receipt_id):
        raise MigrationError(
            "MIGRATION_RECEIPT_INVALID",
            "Migration receipt identifier is invalid",
            {"receipt_id": receipt_id},
        )
    migration_root = _migration_root(root)
    receipt_root = _safe_receipt_root(migration_root, receipt_id)
    receipt_path = receipt_root / "receipt.json"
    receipt = _read_receipt(receipt_path)
    if receipt.get("wiki_root") != str(root):
        raise MigrationError(
            "MIGRATION_RECEIPT_MISMATCH",
            "Migration receipt belongs to a different wiki root",
            {"receipt_id": receipt_id},
        )
    if receipt.get("status") == "rolled_back":
        return receipt
    if receipt.get("status") != "applied":
        raise MigrationError(
            "MIGRATION_ROLLBACK_UNAVAILABLE",
            "Only an applied migration can be rolled back explicitly",
            {"receipt_id": receipt_id, "status": receipt.get("status")},
        )

    operations = receipt.get("operations")
    if not isinstance(operations, list):
        raise MigrationError(
            "MIGRATION_RECEIPT_INVALID",
            "Migration receipt has no valid operation list",
            {"receipt_id": receipt_id},
        )
    _validate_receipt_targets(operations)
    _validate_receipt_backups(receipt_root, operations)
    for operation in operations:
        target = _safe_target(root, operation["path"])
        current = _sha256(target.read_bytes()) if target.is_file() else None
        if current != operation.get("after_sha256"):
            raise MigrationError(
                "MIGRATION_ROLLBACK_TARGET_CHANGED",
                "Migration target changed after apply; refusing to overwrite it",
                {
                    "path": operation["path"],
                    "expected": operation.get("after_sha256"),
                    "actual": current,
                },
            )

    recovery = _restore_receipt_targets(root, receipt_root, receipt)
    receipt.update(
        {
            "status": "rolled_back",
            "updated_at": _utc_now(),
            "verification": "rolled_back",
            "recovery": recovery,
        }
    )
    _write_receipt(receipt_path, migration_root / "latest.json", receipt)
    return receipt


def _operation(root: Path, relative: str, content: str) -> MigrationOperation:
    path = _safe_target(root, relative)
    before = _sha256(path.read_bytes()) if path.is_file() else None
    return MigrationOperation(
        action="replace" if before is not None else "create",
        path=relative,
        content=content,
        before_sha256=before,
    )


def _render_config(customizations: list[str]) -> str:
    excludes = [".git", ".venv", "node_modules"]
    if "exclude_directory:.agents" in customizations:
        excludes.append(".agents")
    rendered_excludes = ", ".join(json.dumps(value) for value in excludes)
    return f'''\
schema_version = "{CURRENT_SCHEMA_VERSION}"
runtime_contract = "{CURRENT_RUNTIME_CONTRACT}"
profile = "default"

[content]
exclude_directories = [{rendered_excludes}]
source_directory = "sources"

[compiler]
providers = ["seed", "frontmatter", "text", "graph", "source"]
target_bytes = 48000
max_bytes = 192000
target_items = 24
max_items = 96

[state]
field = "knowledge_state"
default = "unspecified"

[stewardship]
mode = "manual"
'''


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _validate_operations(operations: tuple[MigrationOperation, ...]) -> None:
    invalid = sorted(operation.path for operation in operations if operation.path not in _ALLOWED_TARGETS)
    if invalid:
        raise MigrationError(
            "MIGRATION_TARGET_INVALID",
            "Migration plan contains a target outside the runtime compatibility surface",
            {"paths": invalid},
        )


def _validate_receipt_targets(operations: list[Any]) -> None:
    invalid = sorted(
        str(operation.get("path"))
        for operation in operations
        if not isinstance(operation, dict) or operation.get("path") not in _ALLOWED_TARGETS
    )
    if invalid:
        raise MigrationError(
            "MIGRATION_RECEIPT_INVALID",
            "Migration receipt contains an invalid target",
            {"paths": invalid},
        )


def _validate_receipt_backups(receipt_root: Path, operations: list[dict[str, Any]]) -> None:
    invalid: list[str] = []
    resolved_root = receipt_root.resolve()
    for operation in operations:
        before = operation.get("before_sha256")
        backup_relative = operation.get("backup")
        if before is None:
            if backup_relative is not None:
                invalid.append(str(operation.get("path")))
            continue
        if not isinstance(backup_relative, str):
            invalid.append(str(operation.get("path")))
            continue
        backup = (receipt_root / backup_relative).resolve()
        if (
            resolved_root not in backup.parents
            or not backup.is_file()
            or _sha256(backup.read_bytes()) != before
        ):
            invalid.append(str(operation.get("path")))
    if invalid:
        raise MigrationError(
            "MIGRATION_RECEIPT_INVALID",
            "Migration receipt backups are missing, unsafe, or do not match recorded hashes",
            {"paths": sorted(invalid)},
        )


def _assert_precondition(target: Path, expected: str | None, relative: str) -> None:
    actual = _sha256(target.read_bytes()) if target.is_file() else None
    if actual != expected:
        raise MigrationError(
            "MIGRATION_PRECONDITION_FAILED",
            "Migration target changed after inspection",
            {"path": relative, "expected": expected, "actual": actual},
        )


def _restore_receipt_targets(root: Path, receipt_root: Path, receipt: dict[str, Any]) -> str:
    operations = receipt.get("operations", [])
    _validate_receipt_targets(operations)
    errors: list[str] = []
    for operation in reversed(operations):
        target = _safe_target(root, operation["path"])
        try:
            before = operation.get("before_sha256")
            backup_relative = operation.get("backup")
            if before is None:
                target.unlink(missing_ok=True)
                continue
            if not isinstance(backup_relative, str):
                raise OSError("missing backup reference")
            backup = (receipt_root / backup_relative).resolve()
            if receipt_root.resolve() not in backup.parents or not backup.is_file():
                raise OSError("backup is missing or outside receipt directory")
            _atomic_write_bytes(target, backup.read_bytes(), mode=operation.get("mode"))
            restored = _sha256(target.read_bytes())
            if restored != before:
                raise OSError("restored file hash does not match receipt")
        except Exception as exc:  # collect every recovery failure
            errors.append(f"{operation.get('path')}:{type(exc).__name__}")
    if errors:
        return "automatic_rollback_incomplete:" + ",".join(errors)
    return "automatic_rollback_complete"


def _read_receipt(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MigrationError(
            "MIGRATION_RECEIPT_NOT_FOUND",
            "Migration receipt was not found",
            {"path": str(path)},
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(
            "MIGRATION_RECEIPT_INVALID",
            "Migration receipt could not be read",
            {"path": str(path), "type": type(exc).__name__},
        ) from exc
    if not isinstance(value, dict) or value.get("kind") != "wiki_migration_receipt":
        raise MigrationError(
            "MIGRATION_RECEIPT_INVALID",
            "Migration receipt has an invalid shape",
            {"path": str(path)},
        )
    return value


def _write_receipt(receipt_path: Path, latest_path: Path, receipt: dict[str, Any]) -> None:
    content = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write_text(receipt_path, content, mode=0o600)
    _atomic_write_text(latest_path, content, mode=0o600)


def _atomic_write_text(path: Path, content: str, mode: int | None = None) -> None:
    _atomic_write_bytes(path, content.encode("utf-8"), mode=mode)


def _atomic_write_bytes(path: Path, content: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = path.stat().st_mode & 0o7777 if path.exists() else None
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode if mode is not None else existing_mode or 0o644)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _new_receipt_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:12]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_wiki_paths(root: Path) -> None:
    for relative in _ALLOWED_TARGETS:
        _safe_target(root, relative)
    _migration_root(root)


def _safe_target(root: Path, relative: str) -> Path:
    if relative not in _ALLOWED_TARGETS:
        raise MigrationError(
            "MIGRATION_TARGET_INVALID",
            "Migration target is outside the runtime compatibility surface",
            {"path": relative},
        )
    target = root / relative
    parent = target.parent
    resolved_parent = parent.resolve()
    if target.is_symlink() or resolved_parent != parent or not resolved_parent.is_relative_to(root):
        raise MigrationError(
            "MIGRATION_TARGET_INVALID",
            "Migration target uses a symlink or resolves outside the wiki root",
            {"path": relative},
        )
    return target


def _migration_root(root: Path) -> Path:
    metadata = root / ".llm-wiki"
    if metadata.is_symlink():
        raise MigrationError(
            "MIGRATION_TARGET_INVALID",
            "Migration receipt directory cannot be a symlink",
            {"path": ".llm-wiki"},
        )
    resolved = metadata.resolve()
    if not resolved.is_relative_to(root):
        raise MigrationError(
            "MIGRATION_TARGET_INVALID",
            "Migration receipt directory resolves outside the wiki root",
            {"path": ".llm-wiki"},
        )
    migrations = metadata / "migrations"
    if migrations.is_symlink() or not migrations.resolve().is_relative_to(root):
        raise MigrationError(
            "MIGRATION_TARGET_INVALID",
            "Migration receipts path uses a symlink or resolves outside the wiki root",
            {"path": ".llm-wiki/migrations"},
        )
    return migrations


def _safe_receipt_root(migration_root: Path, receipt_id: str) -> Path:
    receipt_root = migration_root / receipt_id
    if receipt_root.is_symlink() or not receipt_root.resolve().is_relative_to(migration_root.resolve()):
        raise MigrationError(
            "MIGRATION_RECEIPT_INVALID",
            "Migration receipt path uses a symlink or resolves outside the receipt directory",
            {"receipt_id": receipt_id},
        )
    return receipt_root
