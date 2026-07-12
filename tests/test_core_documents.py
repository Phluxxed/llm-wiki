from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import wiki_graph as legacy_graph
from llm_wiki_core.config import ContentConfig
from llm_wiki_core.documents import collect_pages, safe_source_path, split_frontmatter_and_body
from tests.wiki_fixture import base_fm, write_md


class CoreDocumentsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_collect_pages_matches_legacy_page_shape(self):
        write_md(self.root / "notes" / "a.md", base_fm(title="A", tags=["one"]), "See [B](./b.md)")
        write_md(self.root / "notes" / "b.md", base_fm(title="B"), "B body.")
        (self.root / "index.md").write_text("# Index\n", encoding="utf-8")

        actual = collect_pages(self.root)
        legacy = legacy_graph.collect_pages(self.root)

        self.assertEqual(set(actual), set(legacy))
        for path, page in actual.items():
            self.assertEqual(page.as_legacy_dict(), legacy[path])

    def test_configured_excludes_are_added_to_system_excludes(self):
        write_md(self.root / "notes" / "kept.md", base_fm(title="Kept"), "Body.")
        write_md(self.root / ".agents" / "hidden.md", base_fm(title="Hidden"), "Body.")
        write_md(self.root / "sources" / "source.md", base_fm(title="Source"), "Body.")
        write_md(self.root / "node_modules" / "package.md", base_fm(title="Package"), "Body.")
        content = ContentConfig(
            exclude_directories=(".git", ".venv", "node_modules", ".agents"),
            source_directory="sources",
        )

        pages = collect_pages(self.root, content=content)

        self.assertEqual(list(pages), ["notes/kept.md"])

    def test_split_frontmatter_and_body_matches_legacy_behavior(self):
        text = "---\ntitle: Example\ntags: [one]\n---\nBody.\n"

        self.assertEqual(split_frontmatter_and_body(text), legacy_graph.split_frontmatter_and_body(text))
        self.assertEqual(split_frontmatter_and_body("No frontmatter"), ({}, "No frontmatter"))

    def test_safe_source_path_stays_inside_configured_source_directory(self):
        sources = self.root / "sources"
        sources.mkdir()
        valid = sources / "a.md"
        valid.write_text("source", encoding="utf-8")

        self.assertEqual(safe_source_path(self.root, "sources/a.md"), valid.resolve())
        self.assertIsNone(safe_source_path(self.root, "../secret.md"))
        self.assertIsNone(safe_source_path(self.root, "/tmp/secret.md"))


if __name__ == "__main__":
    unittest.main()
