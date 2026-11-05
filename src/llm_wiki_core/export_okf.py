"""Produce an OKF exchange projection without modifying the working wiki."""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote, unquote, urlsplit

import yaml

from .config import ContentConfig, inspect_wiki_config
from .documents import collect_pages


class ExportError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)^---[ \t]*\r?\n?", re.M | re.S)
_RESERVED = {"index.md", "log.md"}


def _frontmatter(text: str, path: str) -> tuple[dict[str, Any], re.Match[str]]:
    match = _FRONTMATTER.match(text)
    try:
        value = yaml.safe_load(match[1]) if match else None
    except yaml.YAMLError as exc:
        raise ExportError("INVALID_CONCEPT", "Invalid concept YAML", {"path": path}) from exc
    if not isinstance(value, dict) or not isinstance(value.get("type"), str) or not value["type"].strip():
        raise ExportError("INVALID_CONCEPT", "A concept needs YAML with a non-empty string type", {"path": path})
    return value, match


def _replace_fields(text: str, match: re.Match[str], fields: dict[str, Any]) -> str:
    """Patch top-level YAML spans, leaving temporal history text untouched."""
    original = match[1]
    node = yaml.compose(original)
    edits = []
    found = set()
    for index, (key, _) in enumerate(node.value):
        if key.value not in fields:
            continue
        end = node.value[index + 1][0].start_mark.index if index + 1 < len(node.value) else len(original)
        edits.append((key.start_mark.index, end, yaml.safe_dump(
            {key.value: fields[key.value]}, allow_unicode=True, sort_keys=False,
        )))
        found.add(key.value)
    for start, end, replacement in reversed(edits):
        original = original[:start] + replacement + original[end:]
    additions = {key: value for key, value in fields.items() if key not in found}
    if additions:
        original = original.rstrip("\r\n") + "\n" + yaml.safe_dump(additions, allow_unicode=True, sort_keys=False)
    expected = yaml.safe_load(match[1])
    expected.update(fields)
    try:
        actual = yaml.safe_load(original)
    except yaml.YAMLError as exc:
        raise ExportError("YAML_PATCH_UNSAFE", "Cannot preserve this YAML while remapping references") from exc
    if actual != expected:
        raise ExportError("YAML_PATCH_UNSAFE", "YAML aliases or duplicate keys prevent a lossless export")
    return text[:match.start(1)] + original + text[match.end(1):]


def _link_spans(body: str) -> list[tuple[int, int]]:
    """Markdown destinations, excluding fenced, indented and inline code."""
    masked = list(body)
    offset = 0
    fence = None
    for line in body.splitlines(keepends=True):
        marker = re.match(r" {0,3}(`{3,}|~{3,})", line)
        is_code = fence is not None or marker is not None or line.startswith(("    ", "\t"))
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end():].strip():
                fence = None
        if is_code:
            masked[offset:offset + len(line)] = ["\n" if c == "\n" else " " for c in line]
        offset += len(line)
    code_free = "".join(masked)
    for match in re.finditer(r"(`+)(?!`)([\s\S]*?)(?<!`)\1(?!`)", code_free):
        masked[match.start():match.end()] = " " * (match.end() - match.start())
    visible = "".join(masked)
    spans: set[tuple[int, int]] = set()
    # Reference definitions (footnote prose is not a link destination).
    for match in re.finditer(r"^ {0,3}\[(?!\^)[^\]\n]+\]:[ \t]*(?:<([^>\n]+)>|([^\s]+))", visible, re.M):
        spans.add(match.span(1 if match[1] is not None else 2))
    for match in re.finditer(r"(?<!\\)\]\([ \t\n]*", visible):
        start = match.end()
        if start >= len(visible):
            continue
        if visible[start] == "<":
            end = visible.find(">", start + 1)
            if end != -1:
                spans.add((start + 1, end))
            continue
        end, depth = start, 0
        while end < len(visible):
            char = visible[end]
            if char == "\\":
                end += 2
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            elif char.isspace() and depth == 0:
                break
            end += 1
        if end > start:
            spans.add((start, end))
    for match in re.finditer(r"<(file://[^>]+)>", visible):
        spans.add(match.span(1))
    # Inline HTML is legal Markdown, including image attachments.
    for match in re.finditer(r"\b(?:href|src)\s*=\s*(['\"])(.*?)\1", visible, re.I):
        spans.add(match.span(2))
    return sorted(spans)


def _rewrite_links(body: str, rewrite: Callable[[str], str]) -> str:
    for start, end in reversed(_link_spans(body)):
        body = body[:start] + rewrite(body[start:end]) + body[end:]
    return body


def _publish(stage: Path, output: Path) -> None:
    """Atomic rename with no replacement, including a raced empty directory."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        args = (os.fsencode(stage), os.fsencode(output), 4)  # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        args = (-100, os.fsencode(stage), -100, os.fsencode(output), 1)  # RENAME_NOREPLACE
    else:
        raise ExportError("ATOMIC_PUBLISH_UNSUPPORTED", "Atomic no-replace directory publication is unavailable")
    rename.restype = ctypes.c_int
    if rename(*args) != 0:
        error = ctypes.get_errno()
        if error in (errno.EEXIST, errno.ENOTEMPTY):
            raise ExportError("DESTINATION_EXISTS", "Export destination already exists", {"output": str(output)})
        raise OSError(error, os.strerror(error), str(output))


def _export_okf(wiki_root: str | Path, output: str | Path) -> dict[str, Any]:
    """Export a new bundle; unresolved local references are visible diagnostics.

    Raw attachments are opaque evidence. Their embedded historical links are not
    rewritten or traversed. ``self_contained`` covers emitted concept references,
    not external URLs or links embedded inside immutable attachments.
    """
    root = Path(wiki_root).expanduser().resolve()
    requested = Path(output).expanduser().absolute()
    if requested.exists() or requested.is_symlink():
        raise ExportError("DESTINATION_EXISTS", "Export destination already exists", {"output": str(requested)})
    destination = requested.resolve()
    if not root.is_dir():
        raise ExportError("WIKI_NOT_FOUND", "Wiki root is not a directory", {"wiki_root": str(root)})
    if destination.is_relative_to(root) or root.is_relative_to(destination):
        raise ExportError("OVERLAPPING_PATHS", "Export destination must not overlap the working wiki")
    if not destination.parent.is_dir():
        raise ExportError("DESTINATION_PARENT_MISSING", "Export destination parent must already exist")
    config_path = root / ".llm-wiki.toml"
    if config_path.is_symlink() and not config_path.resolve().is_relative_to(root):
        raise ExportError("UNSAFE_CONFIG", "Wiki configuration resolves outside the wiki")
    inspection = inspect_wiki_config(root)
    if inspection.error:
        raise ExportError(inspection.error.code, inspection.error.message, inspection.error.details)
    content = inspection.config.content if inspection.config else ContentConfig()
    diagnostics: list[dict[str, str]] = []
    diagnostic_keys: set[tuple[str, str, str]] = set()

    def diagnostic(page: str, reference: str, reason: str) -> None:
        key = (page, reference, reason)
        if key not in diagnostic_keys:
            diagnostic_keys.add(key)
            diagnostics.append({"page": page, "reference": reference, "reason": reason})

    pages = collect_pages(root, content=content)
    artifacts: dict[str, bytes] = {}
    payloads: dict[str, str] = {}
    emitted_links: set[str] = set()
    exports: dict[str, bytes] = {}
    source_root = root / content.source_directory

    def artifact(relative: str) -> str | None:
        path = root / relative
        if not path.resolve().is_relative_to(root):
            diagnostic(relative, relative, "outside_wiki")
            return None
        if not path.is_file():
            diagnostic(relative, relative, "missing_file")
            return None
        if relative not in artifacts:
            artifacts[relative] = path.read_bytes()
            payloads[relative] = "references/raw/" + relative + ".txt"
        return payloads[relative]

    if source_root.exists():
        if not source_root.resolve().is_relative_to(root):
            diagnostic(content.source_directory, content.source_directory, "outside_wiki")
        else:
            for directory, children, filenames in os.walk(source_root, followlinks=False):
                for child in children[:]:
                    child_path = Path(directory) / child
                    if child_path.is_symlink():
                        diagnostic(child_path.relative_to(root).as_posix(), child, "symlink_directory_omitted")
                        children.remove(child)
                for filename in sorted(filenames):
                    artifact((Path(directory) / filename).relative_to(root).as_posix())

    def resolve(reference: str, page: str, *, root_first: bool = False) -> tuple[str, str] | None:
        value = re.sub(r"\\([()\[\] ])", r"\1", reference)
        try:
            parts = urlsplit(value)
        except ValueError:
            diagnostic(page, reference, "invalid_reference")
            return None
        if parts.scheme and parts.scheme != "file":
            return None
        if parts.netloc and parts.scheme != "file":
            return None
        if not parts.path:
            return None
        path = unquote(parts.path)
        suffix = ("?" + parts.query if parts.query else "") + ("#" + parts.fragment if parts.fragment else "")
        if parts.scheme == "file":
            if parts.netloc not in ("", "localhost"):
                diagnostic(page, reference, "external_local_file")
                return None
            candidates = [Path(path)]
        elif path.startswith("/"):
            # Absolute host paths within this wiki can be made portable.
            host_path = Path(path).resolve()
            candidates = [host_path] if host_path.is_relative_to(root) else [root / path.lstrip("/")]
        elif path.startswith("../"):
            candidates = [root / Path(page).parent / path]
        else:
            # The working wiki uses ./projects/... as a root-relative form.
            candidates = [root / path, root / Path(page).parent / path]
            if not root_first and not path.startswith("./"):
                candidates.reverse()
        safe = [candidate for candidate in candidates if candidate.resolve().is_relative_to(root)]
        existing = [candidate for candidate in safe if candidate.is_file()]
        if not existing:
            diagnostic(page, reference, "missing_local_reference" if safe else "outside_wiki")
            return None
        candidate = existing[0]
        # Keep logical paths (including safe symlink aliases), normalized lexically.
        relative = Path(os.path.normpath(candidate)).relative_to(root).as_posix()
        return relative, suffix

    def reference(reference: str, page: str, *, raw: bool = False, root_first: bool = False, scope: bool = False) -> str:
        if scope and not any(c in reference for c in "/\\") and not Path(reference).suffix:
            return reference  # A population/scope descriptor is not a local file.
        resolved = resolve(reference, page, root_first=root_first)
        if resolved is None:
            return reference
        relative, suffix = resolved
        if relative in _RESERVED:
            # The root reserved files are regenerated. Preserve legacy provenance separately.
            target = artifact(relative) if raw else relative
        elif relative in pages and not raw:
            target = relative
        elif relative in pages and raw:
            target = relative  # A concept is already a first-class provenance resource.
        else:
            payload = artifact(relative)
            target = payload if raw or Path(relative).suffix.lower() != ".md" else relative
        if target is None:
            return reference
        emitted_links.add(target)
        return "/" + quote(target, safe="/-._~") + suffix

    def legacy_reference(value: str, page: str) -> str:
        mapped = reference(value, page, raw=True, root_first=True)
        return mapped.lstrip("/") if mapped != value and mapped.startswith("/") else mapped

    # Serialize concepts only after all source payloads are registered. Link processing
    # also registers support attachments; their contents stay opaque.
    for relative, page in sorted(pages.items()):
        fm, match = _frontmatter(page.text, relative)
        changes: dict[str, Any] = {}
        for field in ("source", "evidence"):
            value = fm.get(field)
            if isinstance(value, str):
                changes[field] = legacy_reference(value, relative)
            elif isinstance(value, list):
                changes[field] = [legacy_reference(item, relative) if isinstance(item, str) else item for item in value]
        if isinstance(fm.get("sources"), list):
            changes["sources"] = [
                {**item, "resource": reference(item["resource"], relative, raw=True, root_first=True, scope=True)}
                if isinstance(item, dict) and isinstance(item.get("resource"), str) else item
                for item in fm["sources"]
            ]
        for field in ("resource", "computation"):
            if isinstance(fm.get(field), str):
                changes[field] = reference(fm[field], relative)
        for field in ("executor", "attester"):
            if isinstance(fm.get(field), dict) and isinstance(fm[field].get("resource"), str):
                changes[field] = {**fm[field], "resource": reference(fm[field]["resource"], relative)}
        status = fm.get("status")
        if status is not None and status not in ("draft", "stable", "deprecated"):
            if "workflow_status" in fm and fm["workflow_status"] != status:
                raise ExportError("STATUS_CONFLICT", "Cannot preserve legacy status without replacing workflow_status", {"path": relative})
            changes.update(workflow_status=status, status="draft" if str(status).lower() == "draft" else "stable")
        changes = {key: value for key, value in changes.items() if value != fm.get(key)}
        revised = _replace_fields(page.text, match, changes) if changes else page.text
        _, revised_match = _frontmatter(revised, relative)
        body = _rewrite_links(revised[revised_match.end():], lambda value: reference(value, relative))
        exports[relative] = (revised[:revised_match.end()] + body).encode("utf-8")

    # Temporal records remain untouched; ensure their explicitly stored evidence
    # references are accounted for even when absent from source/evidence fields.
    for relative, page in sorted(pages.items()):
        revisions = page.frontmatter.get("temporal_claim_revisions", [])
        if isinstance(revisions, list):
            for revision in revisions:
                if isinstance(revision, dict):
                    for value in revision.get("steward_evidence_refs", []) or []:
                        if isinstance(value, str):
                            reference(value, relative, root_first=True)

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    manifest_files = []
    for relative, data in sorted(artifacts.items()):
        payload = payloads[relative]
        if payload in pages or payload in artifacts:
            raise ExportError("PATH_COLLISION", "Payload path collides with corpus content", {"path": payload})
        exports[payload] = data
        digest = hashlib.sha256(data).hexdigest()
        manifest_files.append({"original_path": relative, "payload_path": payload, "sha256": digest, "bytes": len(data)})
        if Path(relative).suffix.lower() == ".md" and Path(relative).name in _RESERVED and relative not in _RESERVED:
            link = "* [Original document](</" + quote(payload, safe="/-._~") + ">)\n"
            heading = "# Original document\n\n"
            if Path(relative).name == "log.md":
                heading += "## " + now[:10] + "\n\n"
            exports[relative] = (heading + link).encode("utf-8")
            emitted_links.add(payload)
        elif Path(relative).suffix.lower() == ".md" and Path(relative).name not in _RESERVED:
            metadata = {
                "type": "Reference", "title": Path(relative).name,
                "resource": "/" + quote(payload, safe="/-._~"),
                "sources": [{"resource": "/" + quote(payload, safe="/-._~")}],
                "generated": {"by": "process:llm-wiki-okf-export", "at": now},
                "original_path": relative, "sha256": digest,
            }
            exports[relative] = ("---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) +
                "---\n# Original artifact\n\n[Read the unchanged original](</" + quote(payload, safe="/-._~") +
                ">). Its embedded links are historical text and have not been rewritten.\n").encode("utf-8")
            emitted_links.add(payload)
    index = '---\nokf_version: "0.2"\n---\n# Concepts\n\n'
    for path in sorted(exports):
        if path.endswith(".md") and Path(path).name not in _RESERVED:
            index += "* [" + path.replace("[", "\\[").replace("]", "\\]") + "](</" + quote(path, safe="/-._~") + ">)\n"
    exports["index.md"] = index.encode("utf-8")
    exports["log.md"] = ("# Export Log\n\n## " + now[:10] + "\n\n* Exported an OKF 0.2 exchange projection; original content dates are retained.\n").encode("utf-8")
    for path, data in exports.items():
        if path.endswith(".md") and Path(path).name not in _RESERVED:
            _frontmatter(data.decode("utf-8"), path)
    missing = sorted(emitted_links - exports.keys())
    if missing:
        raise ExportError("INVALID_EXPORT", "Generated references do not resolve", {"paths": missing})
    report: dict[str, Any] = {
        "okf_version": "0.2", "output": str(destination), "conformant": True,
        "self_contained": not diagnostics,
        "counts": {"concepts": len(pages), "attachments": len(artifacts), "markdown_files": sum(path.endswith(".md") for path in exports)},
        "diagnostics": sorted(diagnostics, key=lambda entry: (entry["page"], entry["reference"], entry["reason"])),
        "manifest": "export-manifest.json",
    }
    manifest = {**report, "exported_at": now, "files": manifest_files,
        "reference_scope": "Emitted concept links and resources; raw attachment contents are opaque and external URLs are not mirrored.",
        "omissions": ["Unlinked support files and runtime configuration are not exported."]}
    exports["export-manifest.json"] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    stage = Path(tempfile.mkdtemp(prefix="." + destination.name + ".export-", dir=destination.parent))
    try:
        for path, data in sorted(exports.items()):
            target = stage / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for entry in manifest_files:
            if hashlib.sha256((stage / entry["payload_path"]).read_bytes()).hexdigest() != entry["sha256"]:
                raise ExportError("PAYLOAD_MISMATCH", "Exported attachment hash differs from original")
        _publish(stage, destination)
    except OSError as exc:
        raise ExportError("EXPORT_IO_ERROR", "Could not write or publish the export", {"reason": str(exc)}) from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return report


def export_okf(wiki_root: str | Path, output: str | Path) -> dict[str, Any]:
    """Create a new exchange bundle, or raise a structured :class:`ExportError`."""
    try:
        return _export_okf(wiki_root, output)
    except (OSError, UnicodeError) as exc:
        raise ExportError("EXPORT_IO_ERROR", "Could not read or publish the export", {"reason": str(exc)}) from exc
