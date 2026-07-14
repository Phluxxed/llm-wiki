from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.cli import build_parser


class DocumentationContractTest(unittest.TestCase):
    def test_new_local_markdown_links_resolve(self):
        files = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "docs" / "context-compiler.md",
            REPO_ROOT / "docs" / "brain-steward-integration.md",
            REPO_ROOT / "docs" / "loci-provider.md",
            REPO_ROOT / "docs" / "releases" / "2026-07-12-context-compiler-v2.md",
        ]
        for path in files:
            text = path.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
                if "://" in target or target.startswith("#"):
                    continue
                file_target = target.split("#", 1)[0]
                with self.subTest(path=path.name, target=target):
                    self.assertTrue((path.parent / file_target).resolve().is_file())

    def test_documented_cli_commands_exist(self):
        parser = build_parser()
        commands = next(
            action.choices
            for action in parser._actions
            if getattr(action, "choices", None)
        )

        self.assertIn("compile-context", commands)
        self.assertIn("doctor", commands)
        self.assertIn("migrate", commands)

    def test_judge_workflow_docs_require_preview_and_hard_cap(self):
        files = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "SKILL.md",
            REPO_ROOT / "_templates" / "CONVENTIONS.md",
        ]

        for path in files:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIn("--plan-judge-calls", text)
                self.assertIn("--max-judge-calls", text)
                self.assertNotIn("scripts/eval.py --gate`", text)


if __name__ == "__main__":
    unittest.main()
