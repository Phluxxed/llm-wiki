from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Mapping

from .config import CONFIG_FILENAME, ContentConfig, inspect_wiki_config
from .documents import collect_pages, safe_source_path


SNAPSHOT_CONTRACT_VERSION = "1"
REQUIRED_WIKI_FILES = ("wiki-agent.md", "index.md", "log.md")
OPTIONAL_WIKI_FILES = ("CONVENTIONS.md", CONFIG_FILENAME, "brain-bootstrap.json")
_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class SnapshotError(ValueError):
    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True)
class SnapshotResolution:
    contract_version: str
    alias: str
    digest: str
    snapshot_wiki_root: Path
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "alias": self.alias,
            "digest": self.digest,
            "snapshot_wiki_root": str(self.snapshot_wiki_root),
            "status": self.status,
        }


def publish_snapshot(
    wiki_root: str | Path,
    *,
    alias: str,
    output_root: str | Path,
) -> dict[str, Any]:
    """Publish one validated, content-addressed wiki storage snapshot."""
    source_root = Path(wiki_root).expanduser().resolve()
    storage_root = Path(output_root).expanduser().resolve()
    _validate_alias(alias)
    if not source_root.is_dir():
        raise SnapshotError("WIKI_NOT_FOUND", "Wiki root does not exist or is not a directory")
    if source_root == storage_root or source_root.is_relative_to(storage_root) or storage_root.is_relative_to(source_root):
        raise SnapshotError(
            "SNAPSHOT_STORAGE_OVERLAP",
            "Snapshot storage and source wiki must not overlap",
        )

    first = _collect_source_files(source_root)
    second = _collect_source_files(source_root)
    if first != second:
        raise SnapshotError(
            "SNAPSHOT_SOURCE_CHANGED",
            "Wiki content changed while the snapshot was being collected",
        )
    files = second
    entries = _file_entries(files)
    digest = _content_digest(entries)

    alias_root, snapshots_root = _prepare_storage(storage_root, alias)
    final_root = snapshots_root / digest
    snapshot_wiki_root = final_root / "wiki"
    current_receipt = alias_root / "current.json"

    current = _read_receipt(current_receipt, required=False)
    if current is not None and current.get("digest") == digest:
        resolution = _resolve_receipt(alias_root, alias, current, status="current")
        payload = resolution.to_dict()
        payload["status"] = "already_current"
        return payload

    if not final_root.exists():
        _publish_snapshot_directory(snapshots_root, final_root, files, entries, digest)
    _verify_snapshot_directory(final_root, digest)

    if current is not None:
        try:
            _resolve_receipt(alias_root, alias, current, status="current")
        except SnapshotError:
            pass
        else:
            _atomic_write_json(alias_root / "previous.json", current)

    receipt = {
        "contract_version": SNAPSHOT_CONTRACT_VERSION,
        "alias": alias,
        "digest": digest,
        "snapshot": f"snapshots/{digest}",
    }
    _atomic_write_json(current_receipt, receipt)
    return {
        "contract_version": SNAPSHOT_CONTRACT_VERSION,
        "alias": alias,
        "digest": digest,
        "snapshot_wiki_root": str(snapshot_wiki_root),
        "status": "published",
    }


def resolve_snapshot(*, alias: str, output_root: str | Path) -> SnapshotResolution:
    """Resolve and integrity-check current, falling back to the previous receipt."""
    _validate_alias(alias)
    alias_root = Path(output_root).expanduser().resolve() / alias
    failures: list[dict[str, str]] = []
    for receipt_name, status in (("current.json", "current"), ("previous.json", "last_known_good")):
        try:
            receipt = _read_receipt(alias_root / receipt_name, required=True)
            assert receipt is not None
            return _resolve_receipt(alias_root, alias, receipt, status=status)
        except SnapshotError as exc:
            failures.append({"receipt": receipt_name, "code": exc.code})
    raise SnapshotError(
        "SNAPSHOT_UNAVAILABLE",
        "No valid current or last-known-good snapshot is available",
        {"failures": failures},
    )


def _collect_source_files(root: Path) -> dict[str, bytes]:
    inspection = inspect_wiki_config(root)
    if inspection.error is not None:
        raise SnapshotError(inspection.error.code, inspection.error.message, inspection.error.details)
    content = inspection.config.content if inspection.config is not None else ContentConfig()

    for relative in REQUIRED_WIKI_FILES:
        path = root / relative
        if not path.is_file():
            raise SnapshotError(
                "WIKI_FILE_MISSING",
                "Wiki is missing a required canonical file",
                {"file": relative},
            )

    pages = collect_pages(root, content=content)
    selected = set(pages)
    selected.update(REQUIRED_WIKI_FILES)
    selected.update(relative for relative in OPTIONAL_WIKI_FILES if (root / relative).is_file())
    for page in pages.values():
        source = page.frontmatter.get("source")
        if not isinstance(source, str) or not source.strip():
            continue
        source_path = safe_source_path(root, source, source_directory=content.source_directory)
        lexical_source = Path(source)
        candidate = root / lexical_source
        if source_path is not None and not lexical_source.is_absolute() and candidate.is_file():
            _validate_source_path(root, candidate, lexical_source.as_posix())
            selected.add(candidate.resolve().relative_to(root).as_posix())

    collected: dict[str, bytes] = {}
    for relative in sorted(selected):
        path = root / relative
        _validate_source_path(root, path, relative)
        try:
            collected[relative] = path.read_bytes()
        except OSError as exc:
            raise SnapshotError(
                "SNAPSHOT_SOURCE_UNREADABLE",
                "Canonical wiki file could not be read",
                {"file": relative, "reason": type(exc).__name__},
            ) from exc
    return collected


def _validate_source_path(root: Path, path: Path, relative: str) -> None:
    candidate = root
    for part in Path(relative).parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise SnapshotError(
                "SNAPSHOT_SYMLINK_REJECTED",
                "Snapshot input must not contain symlinks",
                {"file": relative},
            )
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise SnapshotError(
            "SNAPSHOT_PATH_INVALID",
            "Snapshot input is not a contained regular file",
            {"file": relative},
        )


def _file_entries(files: Mapping[str, bytes]) -> list[dict[str, Any]]:
    return [
        {"path": path, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        for path, content in sorted(files.items())
    ]


def _content_digest(entries: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        {"contract_version": SNAPSHOT_CONTRACT_VERSION, "files": entries},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _prepare_storage(storage_root: Path, alias: str) -> tuple[Path, Path]:
    storage_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    alias_root = storage_root / alias
    snapshots_root = alias_root / "snapshots"
    for path in (alias_root, snapshots_root):
        if path.is_symlink():
            raise SnapshotError("SNAPSHOT_STORAGE_INVALID", "Snapshot storage must not use symlinks")
        path.mkdir(mode=0o700, exist_ok=True)
        path.chmod(0o700)
    return alias_root, snapshots_root


def _publish_snapshot_directory(
    snapshots_root: Path,
    final_root: Path,
    files: Mapping[str, bytes],
    entries: list[dict[str, Any]],
    digest: str,
) -> None:
    temporary = Path(tempfile.mkdtemp(prefix=".publishing-", dir=snapshots_root))
    try:
        wiki = temporary / "wiki"
        wiki.mkdir(mode=0o700)
        for relative, content in sorted(files.items()):
            target = wiki / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(content)
            target.chmod(0o400)
        manifest = {
            "contract_version": SNAPSHOT_CONTRACT_VERSION,
            "digest": digest,
            "files": entries,
        }
        manifest_path = temporary / "snapshot.json"
        manifest_path.write_text(_json_text(manifest), encoding="utf-8")
        manifest_path.chmod(0o400)
        for directory in sorted((path for path in wiki.rglob("*") if path.is_dir()), reverse=True):
            directory.chmod(0o500)
        wiki.chmod(0o500)
        temporary.chmod(0o500)
        os.rename(temporary, final_root)
    except Exception:
        if temporary.exists():
            _make_removable(temporary)
            shutil.rmtree(temporary)
        raise


def _verify_snapshot_directory(snapshot_root: Path, expected_digest: str) -> None:
    try:
        _verify_snapshot_directory_checked(snapshot_root, expected_digest)
    except SnapshotError:
        raise
    except OSError as exc:
        raise SnapshotError(
            "SNAPSHOT_INTEGRITY_FAILED",
            "Snapshot content could not be verified",
            {"reason": type(exc).__name__},
        ) from exc


def _verify_snapshot_directory_checked(snapshot_root: Path, expected_digest: str) -> None:
    if snapshot_root.is_symlink() or not snapshot_root.is_dir():
        raise SnapshotError("SNAPSHOT_INTEGRITY_FAILED", "Snapshot directory is invalid")
    _require_read_only(snapshot_root)
    manifest_path = snapshot_root / "snapshot.json"
    _require_read_only(manifest_path)
    manifest = _read_json(manifest_path, "SNAPSHOT_INTEGRITY_FAILED")
    if manifest.get("contract_version") != SNAPSHOT_CONTRACT_VERSION or manifest.get("digest") != expected_digest:
        raise SnapshotError("SNAPSHOT_INTEGRITY_FAILED", "Snapshot manifest identity does not match receipt")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise SnapshotError("SNAPSHOT_INTEGRITY_FAILED", "Snapshot manifest file list is invalid")
    wiki = snapshot_root / "wiki"
    actual: dict[str, bytes] = {}
    if wiki.is_symlink() or not wiki.is_dir():
        raise SnapshotError("SNAPSHOT_INTEGRITY_FAILED", "Snapshot wiki root is invalid")
    _require_read_only(wiki)
    for path in sorted(wiki.rglob("*")):
        if path.is_symlink():
            raise SnapshotError("SNAPSHOT_INTEGRITY_FAILED", "Snapshot contains a symlink")
        _require_read_only(path)
        if path.is_file():
            actual[path.relative_to(wiki).as_posix()] = path.read_bytes()
    actual_entries = _file_entries(actual)
    if actual_entries != entries or _content_digest(actual_entries) != expected_digest:
        raise SnapshotError("SNAPSHOT_INTEGRITY_FAILED", "Snapshot content digest does not match receipt")


def _resolve_receipt(
    alias_root: Path,
    alias: str,
    receipt: Mapping[str, Any],
    *,
    status: str,
) -> SnapshotResolution:
    if receipt.get("contract_version") != SNAPSHOT_CONTRACT_VERSION or receipt.get("alias") != alias:
        raise SnapshotError("SNAPSHOT_RECEIPT_INVALID", "Snapshot receipt contract or alias is invalid")
    digest = receipt.get("digest")
    snapshot = receipt.get("snapshot")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SnapshotError("SNAPSHOT_RECEIPT_INVALID", "Snapshot receipt digest is invalid")
    expected_snapshot = f"snapshots/{digest}"
    if snapshot != expected_snapshot:
        raise SnapshotError("SNAPSHOT_RECEIPT_INVALID", "Snapshot receipt path is invalid")
    snapshot_root = alias_root / expected_snapshot
    _verify_snapshot_directory(snapshot_root, digest)
    return SnapshotResolution(
        contract_version=SNAPSHOT_CONTRACT_VERSION,
        alias=alias,
        digest=digest,
        snapshot_wiki_root=snapshot_root / "wiki",
        status=status,
    )


def _read_receipt(path: Path, *, required: bool) -> dict[str, Any] | None:
    if not path.exists():
        if required:
            raise SnapshotError("SNAPSHOT_RECEIPT_MISSING", "Snapshot receipt is missing")
        return None
    return _read_json(path, "SNAPSHOT_RECEIPT_INVALID")


def _read_json(path: Path, code: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise SnapshotError(code, "Snapshot JSON file is not a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(code, "Snapshot JSON file could not be read") from exc
    if not isinstance(value, dict):
        raise SnapshotError(code, "Snapshot JSON value must be an object")
    return value


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    descriptor, raw_temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(_json_text(payload))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_text(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _validate_alias(alias: str) -> None:
    if not isinstance(alias, str) or _ALIAS.fullmatch(alias) is None or alias in {".", ".."}:
        raise SnapshotError("SNAPSHOT_ALIAS_INVALID", "Snapshot alias is invalid")


def _require_read_only(path: Path) -> None:
    if path.stat().st_mode & 0o222:
        raise SnapshotError("SNAPSHOT_INTEGRITY_FAILED", "Snapshot content is writable")


def _make_removable(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(0o700)
        else:
            path.chmod(0o600)
    root.chmod(0o700)
