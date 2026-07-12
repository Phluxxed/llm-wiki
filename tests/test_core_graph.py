from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import wiki_graph as legacy_graph
from llm_wiki_core.documents import collect_pages
from llm_wiki_core.graph import collect_typed_edges, resolve_link
from tests.wiki_fixture import base_fm, write_md


class CoreGraphTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_typed_edges_match_legacy_graph(self):
        write_md(self.root / "notes" / "a.md", base_fm(title="A"), "See [B](./b.md)")
        write_md(self.root / "notes" / "b.md", base_fm(title="B"), "B body.")
        write_md(
            self.root / "entities" / "tool.md",
            base_fm(title="Tool", type="entity", mentioned_in=["notes/a.md"]),
            "Tool body.",
        )

        pages = collect_pages(self.root)
        actual = collect_typed_edges(pages)
        legacy_pages = {path: page.as_legacy_dict() for path, page in pages.items()}
        expected = legacy_graph.collect_typed_edges(legacy_pages)

        self.assertEqual(
            [(edge.source, edge.target, edge.type, edge.weight) for edge in actual],
            [(edge.source, edge.target, edge.type, edge.weight) for edge in expected],
        )

    def test_resolve_link_handles_root_relative_sibling_and_parent_paths(self):
        targets = {"notes/a.md", "notes/b.md", "shared/c.md"}

        self.assertEqual(resolve_link("./notes/a.md", "notes/b.md", targets), "notes/a.md")
        self.assertEqual(resolve_link("./b.md", "notes/a.md", targets), "notes/b.md")
        self.assertEqual(resolve_link("../shared/c.md", "notes/a.md", targets), "shared/c.md")
        self.assertIsNone(resolve_link("../../outside.md", "notes/a.md", targets))

    def test_edge_order_is_deterministic(self):
        write_md(self.root / "z.md", base_fm(title="Z"), "See [A](./a.md)")
        write_md(self.root / "a.md", base_fm(title="A"), "See [B](./b.md)")
        write_md(self.root / "b.md", base_fm(title="B"), "")

        edges = collect_typed_edges(collect_pages(self.root))

        self.assertEqual(edges, sorted(edges))


if __name__ == "__main__":
    unittest.main()
