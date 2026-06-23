from __future__ import annotations

import shutil
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent


def write_md(path: Path, frontmatter: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    path.write_text(f"---\n{fm}\n---\n{body}\n", encoding="utf-8")


def base_fm(**overrides):
    fm = {
        "title": "X",
        "category": "X",
        "status": "Live",
        "owner": "x",
        "tags": [],
        "created": "2026-06-23",
        "last_reviewed": "2026-06-23",
        "type": "article",
        "description": "A test page.",
        "timestamp": "2026-06-23T00:00:00Z",
    }
    fm.update(overrides)
    return fm


def create_wiki_root(path: Path, *, with_scripts: bool = True) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "wiki-agent.md").write_text("# Wiki Agent\n", encoding="utf-8")
    (path / "index.md").write_text("---\nokf_version: \"0.1\"\n---\n", encoding="utf-8")
    (path / "log.md").write_text("## [2026-06-23] init | Created wiki\n", encoding="utf-8")

    if with_scripts:
        scripts = path / "scripts"
        scripts.mkdir()
        shutil.copyfile(REPO_ROOT / "scripts" / "wiki_graph.py", scripts / "wiki_graph.py")
        shutil.copyfile(REPO_ROOT / "scripts" / "query.py", scripts / "query.py")

    return path
