"""Project identity, membership, and workspace resolution for compiler v3.

The module deliberately owns the project-aware policy at the compiler seam.
Providers only receive the resulting eligible page mapping; they do not need to
understand Brain project metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping

from .contracts import (
    Diagnostic,
    MAX_WORKSPACE_ALIAS_CHARS,
    MAX_WORKSPACE_REMOTE_CHARS,
    WorkspaceIdentity,
)
from .documents import WikiPage


PROJECT_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
_NORMALIZED_REMOTE = re.compile(r"^[a-z0-9._-]+(?:/[a-z0-9._-]+)+$")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_IDENTITY_SEPARATOR = re.compile(r"[\s_-]+")
MAX_PROJECT_ALIASES = 32
MAX_PROJECT_REMOTES = 16


@dataclass(frozen=True)
class ProjectIdentity:
    project_id: str
    aliases: tuple[str, ...]
    remotes: tuple[str, ...]
    page: str
    title: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "page": self.page,
            "aliases": list(self.aliases),
            "remotes": list(self.remotes),
        }


@dataclass(frozen=True)
class ProjectResolution:
    status: str
    project_id: str | None = None
    page: str | None = None
    matched_by: str | None = None
    candidates: tuple[dict[str, str], ...] = ()
    candidate_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.status == "matched":
            return {
                "status": self.status,
                "project_id": self.project_id,
                "page": self.page,
                "matched_by": self.matched_by,
            }
        if self.status == "ambiguous":
            return {
                "status": self.status,
                "matched_by": self.matched_by,
                "candidates": [dict(item) for item in self.candidates],
                "candidate_count": self.candidate_count,
            }
        return {"status": self.status}


@dataclass(frozen=True)
class ProjectExpansion:
    project_id: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"project_id": self.project_id, "reason": self.reason}


@dataclass(frozen=True)
class ProjectScope:
    active_project_ids: tuple[str, ...] = ()
    anchor_page: str | None = None
    expansions: tuple[ProjectExpansion, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_project_ids": list(self.active_project_ids),
            "anchor_page": self.anchor_page,
            "expansions": [item.to_dict() for item in self.expansions],
        }


@dataclass(frozen=True)
class _IdentityParse:
    identity: ProjectIdentity | None
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectIndex:
    """Complete loaded-page index used for safe resolution and eligibility."""

    identities: tuple[ProjectIdentity, ...] = ()
    canonical_by_id: Mapping[str, ProjectIdentity] = field(default_factory=dict)
    anchors: Mapping[str, str] = field(default_factory=dict)
    membership: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    invalid_pages: frozenset[str] = frozenset()
    diagnostics: tuple[Diagnostic, ...] = ()

    @classmethod
    def from_pages(cls, pages: Mapping[str, WikiPage]) -> "ProjectIndex":
        identities: list[ProjectIdentity] = []
        diagnostics: list[Diagnostic] = []
        invalid_pages: set[str] = set()

        for path in sorted(pages):
            page = pages[path]
            is_project = page.type.casefold() == "project"
            has_identity = "identity" in page.frontmatter
            if not is_project and has_identity:
                invalid_pages.add(path)
                diagnostics.append(
                    _metadata_diagnostic(
                        path,
                        "identity",
                        "identity is only valid on type: project pages",
                    )
                )
                continue
            if not is_project:
                continue
            parsed = _parse_identity(page)
            if parsed.identity is None:
                invalid_pages.add(path)
                for error in parsed.errors:
                    diagnostics.append(_metadata_diagnostic(path, "identity", error))
                continue
            identities.append(parsed.identity)

        by_project_id: dict[str, list[ProjectIdentity]] = {}
        by_alias: dict[str, list[ProjectIdentity]] = {}
        by_remote: dict[str, list[ProjectIdentity]] = {}
        for identity in identities:
            by_project_id.setdefault(identity.project_id, []).append(identity)
            for alias in identity.aliases:
                by_alias.setdefault(normalize_alias(alias), []).append(identity)
            for remote in identity.remotes:
                by_remote.setdefault(remote, []).append(identity)

        colliding_pages: dict[str, list[str]] = {}
        for key, values in by_project_id.items():
            if len(values) > 1:
                for value in values:
                    colliding_pages.setdefault(value.page, []).append(f"project_id:{key}")
        for key, values in by_alias.items():
            if len(values) > 1:
                for value in values:
                    colliding_pages.setdefault(value.page, []).append(f"alias:{key}")
        for key, values in by_remote.items():
            if len(values) > 1:
                for value in values:
                    colliding_pages.setdefault(value.page, []).append(f"remote:{key}")

        for path, collisions in sorted(colliding_pages.items()):
            invalid_pages.add(path)
            diagnostics.append(
                _metadata_diagnostic(
                    path,
                    "identity",
                    "identity value is duplicated across project pages",
                    duplicates=sorted(set(collisions)),
                )
            )

        canonical_by_id = {
            project_id: values[0]
            for project_id, values in by_project_id.items()
            if len(values) == 1 and values[0].page not in colliding_pages
        }
        anchors = {project_id: identity.page for project_id, identity in canonical_by_id.items()}

        membership: dict[str, tuple[str, ...]] = {}
        for path in sorted(pages):
            page = pages[path]
            if "projects" not in page.frontmatter:
                continue
            raw = page.frontmatter.get("projects")
            values, errors = _parse_membership(raw, canonical_by_id)
            if page.type.casefold() == "project":
                errors = (*errors, "project pages implicitly belong to their identity and must not declare projects")
            if errors:
                invalid_pages.add(path)
                for error in errors:
                    diagnostics.append(_metadata_diagnostic(path, "projects", error))
                continue
            membership[path] = values

        diagnostics.sort(key=lambda item: (str(item.details.get("page", "")), item.code, item.message))
        return cls(
            identities=tuple(sorted(identities, key=lambda item: (item.project_id, item.page))),
            canonical_by_id=canonical_by_id,
            anchors=anchors,
            membership=membership,
            invalid_pages=frozenset(invalid_pages),
            diagnostics=tuple(diagnostics),
        )

    build = from_pages

    def resolve(self, workspace: WorkspaceIdentity | None) -> ProjectResolution:
        if workspace is None:
            return ProjectResolution("not_requested")
        remote_matches = self._matches_by_remote(workspace.remotes)
        if remote_matches:
            return self._resolution_for_matches(remote_matches, "remote")
        alias_matches = self._matches_by_alias(workspace.directory_alias)
        if alias_matches:
            return self._resolution_for_matches(alias_matches, "alias")
        return ProjectResolution("unknown")

    # Explicitly named alias for callers that prefer the operation over the
    # noun.  Both routes share the same deterministic implementation.
    resolve_workspace = resolve

    def alias_shadowed(self, workspace: WorkspaceIdentity, resolution: ProjectResolution) -> bool:
        if resolution.status != "matched" or resolution.matched_by != "remote":
            return False
        aliases = self._matches_by_alias(workspace.directory_alias)
        return bool(aliases and {item.project_id for item in aliases} != {resolution.project_id})

    def question_identity_matches(self, question: str) -> tuple[str, ...]:
        """Return canonical project IDs named by complete identity phrases."""

        normalized_question = normalize_identity_phrase(question)
        if not normalized_question:
            return ()
        terms: dict[str, set[str]] = {}
        for project_id, identity in self.canonical_by_id.items():
            names = (project_id, identity.title, *identity.aliases)
            for name in names:
                term = normalize_identity_phrase(name)
                if term:
                    terms.setdefault(term, set()).add(project_id)
        matched: set[str] = set()
        for term, project_ids in terms.items():
            if len(project_ids) != 1 or not _bounded_phrase_match(normalized_question, term):
                continue
            matched.update(project_ids)
        return tuple(sorted(matched))

    def active_scope(
        self,
        pages: Mapping[str, WikiPage],
        resolution: ProjectResolution,
        question: str,
        resolved_seeds: tuple[str, ...],
    ) -> ProjectScope:
        """Compute active IDs and the explicit seed paths that widen them."""

        if resolution.status != "matched" or not resolution.project_id:
            return ProjectScope()
        active: list[str] = [resolution.project_id]
        expansions: list[ProjectExpansion] = []
        for project_id in self.question_identity_matches(question):
            if project_id not in active:
                active.append(project_id)
                expansions.append(ProjectExpansion(project_id, "question_identity_match"))

        for path in resolved_seeds:
            page = pages.get(path)
            if page is None:
                continue
            seed_ids = self.membership.get(path, ())
            if page.type.casefold() == "project":
                identity = next((item for item in self.identities if item.page == path), None)
                if identity is not None and identity.project_id in self.canonical_by_id:
                    seed_ids = (identity.project_id,)
            for project_id in seed_ids:
                if project_id not in active:
                    active.append(project_id)
                    expansions.append(ProjectExpansion(project_id, "explicit_seed_membership"))

        # Keep the workspace project first, then deterministic widening order.
        ordered_active = (resolution.project_id, *sorted(item for item in active if item != resolution.project_id))
        return ProjectScope(
            active_project_ids=tuple(dict.fromkeys(ordered_active)),
            anchor_page=resolution.page,
            expansions=tuple(sorted(expansions, key=lambda item: (item.project_id, item.reason))),
        )

    def eligible_pages(
        self,
        pages: Mapping[str, WikiPage],
        active_project_ids: tuple[str, ...],
        explicit_seeds: tuple[str, ...],
    ) -> dict[str, WikiPage]:
        active = set(active_project_ids)
        explicit = set(explicit_seeds)
        eligible: dict[str, WikiPage] = {}
        for path in sorted(pages):
            page = pages[path]
            if path in explicit:
                eligible[path] = page
                continue
            if path in self.invalid_pages:
                continue
            if path in self.anchors.values() and any(self.anchors.get(project_id) == path for project_id in active):
                eligible[path] = page
                continue
            memberships = self.membership.get(path)
            if memberships is not None:
                if active.intersection(memberships):
                    eligible[path] = page
                continue
            if page.type.casefold() == "project":
                continue
            # A missing projects field is the global-page case.
            if "projects" not in page.frontmatter:
                eligible[path] = page
        return eligible

    def _matches_by_remote(self, remotes: tuple[str, ...]) -> tuple[ProjectIdentity, ...]:
        matches: dict[tuple[str, str], ProjectIdentity] = {}
        for remote in remotes:
            for identity in self._remote_map().get(remote, ()):
                matches[(identity.project_id, identity.page)] = identity
        return tuple(matches[key] for key in sorted(matches))

    def _matches_by_alias(self, alias: str | None) -> tuple[ProjectIdentity, ...]:
        if alias is None:
            return ()
        matches = self._alias_map().get(normalize_alias(alias), ())
        return tuple(sorted(matches, key=lambda item: (item.project_id, item.page)))

    def _remote_map(self) -> dict[str, tuple[ProjectIdentity, ...]]:
        result: dict[str, list[ProjectIdentity]] = {}
        for identity in self.identities:
            for remote in identity.remotes:
                result.setdefault(remote, []).append(identity)
        return {key: tuple(value) for key, value in result.items()}

    def _alias_map(self) -> dict[str, tuple[ProjectIdentity, ...]]:
        result: dict[str, list[ProjectIdentity]] = {}
        for identity in self.identities:
            for alias in identity.aliases:
                result.setdefault(normalize_alias(alias), []).append(identity)
        return {key: tuple(value) for key, value in result.items()}

    @staticmethod
    def _resolution_for_matches(
        matches: tuple[ProjectIdentity, ...], matched_by: str
    ) -> ProjectResolution:
        project_ids = {item.project_id for item in matches}
        if len(project_ids) == 1 and len(matches) == 1:
            identity = matches[0]
            return ProjectResolution(
                "matched",
                project_id=identity.project_id,
                page=identity.page,
                matched_by=matched_by,
            )
        candidates = tuple(
            {"project_id": item.project_id, "page": item.page}
            for item in sorted(matches, key=lambda item: (item.project_id, item.page))[:8]
        )
        return ProjectResolution(
            "ambiguous",
            matched_by=matched_by,
            candidates=candidates,
            candidate_count=len(matches),
        )


def normalize_alias(value: str) -> str:
    return value.strip().casefold()


def normalize_identity_phrase(value: str) -> str:
    return _IDENTITY_SEPARATOR.sub(" ", value.casefold()).strip()


def validate_project_id(value: Any) -> str:
    if not isinstance(value, str) or not PROJECT_ID_PATTERN.fullmatch(value) or len(value) > 64:
        raise ValueError("project_id must be a lowercase snake-case identifier")
    return value


def validate_project_alias(value: Any, index: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"aliases[{index}] must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"aliases[{index}] must be non-empty")
    if len(value) > MAX_WORKSPACE_ALIAS_CHARS:
        raise ValueError(f"aliases[{index}] exceeds {MAX_WORKSPACE_ALIAS_CHARS} characters")
    if "/" in value or "\\" in value or _CONTROL_CHARACTER.search(value):
        raise ValueError(f"aliases[{index}] must be path-free")
    return value


def validate_project_remote(value: Any, index: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"remotes[{index}] must be a string")
    if len(value) > MAX_WORKSPACE_REMOTE_CHARS or not value.isascii() or _CONTROL_CHARACTER.search(value):
        raise ValueError(f"remotes[{index}] must be normalized ASCII host/path")
    if not _NORMALIZED_REMOTE.fullmatch(value) or value != value.lower():
        raise ValueError(f"remotes[{index}] must be normalized ASCII host/path")
    segments = value.split("/")
    host_labels = segments[0].split(".")
    if (
        not segments[0]
        or any(
            not label
            or label[0] == "-"
            or label[-1] == "-"
            or not re.fullmatch(r"[a-z0-9-]+", label)
            for label in host_labels
        )
    ):
        raise ValueError(f"remotes[{index}] must contain one valid hostname")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError(f"remotes[{index}] contains an invalid path segment")
    return value


def _parse_identity(page: WikiPage) -> _IdentityParse:
    raw = page.frontmatter.get("identity")
    if not isinstance(raw, Mapping):
        return _IdentityParse(None, ("identity must be an object",))
    unknown = set(raw) - {"project_id", "aliases", "remotes"}
    errors: list[str] = []
    if unknown:
        errors.append(f"unknown identity field: {sorted(unknown)[0]}")
    try:
        project_id = validate_project_id(raw.get("project_id"))
    except ValueError as exc:
        errors.append(str(exc))
        project_id = ""

    aliases: list[str] = []
    if "aliases" in raw:
        value = raw["aliases"]
        if not isinstance(value, list):
            errors.append("aliases must be a list")
        else:
            if len(value) > MAX_PROJECT_ALIASES:
                errors.append(f"aliases exceeds {MAX_PROJECT_ALIASES} items")
            for index, item in enumerate(value):
                try:
                    aliases.append(validate_project_alias(item, index))
                except ValueError as exc:
                    errors.append(str(exc))
    remotes: list[str] = []
    if "remotes" in raw:
        value = raw["remotes"]
        if not isinstance(value, list):
            errors.append("remotes must be a list")
        else:
            if len(value) > MAX_PROJECT_REMOTES:
                errors.append(f"remotes exceeds {MAX_PROJECT_REMOTES} items")
            for index, item in enumerate(value):
                try:
                    remotes.append(validate_project_remote(item, index))
                except ValueError as exc:
                    errors.append(str(exc))

    alias_keys = [normalize_alias(item) for item in aliases]
    if len(alias_keys) != len(set(alias_keys)):
        errors.append("aliases must be unique after normalization")
    if len(remotes) != len(set(remotes)):
        errors.append("remotes must be unique after normalization")
    if not aliases and not remotes:
        errors.append("identity requires at least one alias or remote")
    if errors:
        return _IdentityParse(None, tuple(dict.fromkeys(errors)))
    return _IdentityParse(
        ProjectIdentity(
            project_id=project_id,
            aliases=tuple(aliases),
            remotes=tuple(remotes),
            page=page.path,
            title=page.title,
        )
    )


def _parse_membership(
    raw: Any,
    canonical_by_id: Mapping[str, ProjectIdentity],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    errors: list[str] = []
    if not isinstance(raw, list):
        return (), ("projects must be a non-empty list",)
    if not raw:
        errors.append("projects must be a non-empty list")
    if len(raw) > 16:
        errors.append("projects exceeds 16 items")
    values: list[str] = []
    for index, value in enumerate(raw):
        try:
            project_id = validate_project_id(value)
        except ValueError:
            errors.append(f"projects[{index}] is not a valid project ID")
            continue
        values.append(project_id)
        if project_id not in canonical_by_id:
            errors.append(f"projects[{index}] references an unknown project ID")
    if len(values) != len(set(values)):
        errors.append("projects must be unique")
    return tuple(values), tuple(dict.fromkeys(errors))


def _bounded_phrase_match(text: str, phrase: str) -> bool:
    if not phrase:
        return False
    start = 0
    while True:
        index = text.find(phrase, start)
        if index < 0:
            return False
        before = text[index - 1] if index else " "
        after_index = index + len(phrase)
        after = text[after_index] if after_index < len(text) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        start = index + 1


def _metadata_diagnostic(page: str, field: str, reason: str, **details: Any) -> Diagnostic:
    payload = {"page": page, "field": field, "reason": reason}
    payload.update(details)
    return Diagnostic(
        code="PROJECT_IDENTITY_INVALID",
        message="Project metadata is invalid and was excluded from normal retrieval",
        provider="project",
        details=payload,
    )
