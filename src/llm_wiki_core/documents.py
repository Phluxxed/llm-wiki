from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Mapping

import yaml

from .config import ContentConfig


SYSTEM_EXCLUDE_FILES = {
    "wiki-agent.md",
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    "CONVENTIONS.md",
    "README.md",
    "index.md",
    "log.md",
}
SYSTEM_EXCLUDE_DIRS = {
    "sources",
    "_templates",
    "scripts",
    ".git",
    ".obsidian",
    ".venv",
    "evals",
    ".eval",
}


@dataclass(frozen=True)
class WikiPage:
    path: str
    title: str
    type: str
    tags: tuple[str, ...]
    frontmatter: Mapping[str, Any]
    body: str
    text: str

    def as_legacy_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "type": self.type,
            "tags": list(self.tags),
            "fm": dict(self.frontmatter),
            "body": self.body,
            "text": self.text,
        }


def parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        parsed = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def split_frontmatter_and_body(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    frontmatter = parse_frontmatter(text)
    return frontmatter, text[end + 4 :].lstrip("\n")


def page_type(frontmatter: Mapping[str, Any]) -> str:
    explicit = str(frontmatter.get("type") or "").strip()
    if explicit:
        return explicit
    category = str(frontmatter.get("category") or "").lower()
    return "meta" if "meta" in category else "primary"


def collect_pages(
    wiki_root: str | Path,
    *,
    content: ContentConfig | None = None,
) -> dict[str, WikiPage]:
    root = Path(wiki_root).expanduser().resolve()
    content = content or ContentConfig()
    exclude_dirs = SYSTEM_EXCLUDE_DIRS | set(content.exclude_directories) | {content.source_directory}
    pages: dict[str, WikiPage] = {}

    for directory, child_dirs, filenames in os.walk(root, followlinks=False):
        child_dirs[:] = sorted(name for name in child_dirs if name not in exclude_dirs)
        directory_path = Path(directory)
        for filename in sorted(filenames):
            if not filename.endswith(".md") or filename in SYSTEM_EXCLUDE_FILES:
                continue
            path = directory_path / filename
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                continue
            relative = path.relative_to(root)
            text = path.read_text(encoding="utf-8")
            frontmatter, body = split_frontmatter_and_body(text)
            if not frontmatter:
                continue
            key = relative.as_posix()
            raw_tags = frontmatter.get("tags") or []
            tags = tuple(str(tag) for tag in raw_tags) if isinstance(raw_tags, list) else ()
            pages[key] = WikiPage(
                path=key,
                title=str(frontmatter.get("title") or path.stem.replace("-", " ").title()),
                type=page_type(frontmatter),
                tags=tags,
                frontmatter=frontmatter,
                body=body,
                text=text,
            )
    return pages


def safe_source_path(
    wiki_root: str | Path,
    source: str,
    *,
    source_directory: str = "sources",
) -> Path | None:
    root = Path(wiki_root).expanduser().resolve()
    source_root = (root / source_directory).resolve()
    candidate = (root / str(source)).resolve()
    if candidate == source_root or not candidate.is_relative_to(source_root):
        return None
    return candidate
