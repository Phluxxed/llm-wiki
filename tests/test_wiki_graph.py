"""Tests for the shared agent graph substrate."""

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))


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
        "created": "2026-06-19",
        "last_reviewed": "2026-06-19",
        "type": "article",
        "description": "A test page.",
        "timestamp": "2026-06-19T00:00:00Z",
    }
    fm.update(overrides)
    return fm


class WikiGraphTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_collect_pages_excludes_project_local_venv(self):
        import wiki_graph

        write_md(self.wiki_root / "notes/keep.md", base_fm(title="Keep"), "")
        write_md(self.wiki_root / ".venv/lib/package/readme.md", base_fm(title="Ignore"), "")

        pages = wiki_graph.collect_pages(self.wiki_root)

        self.assertIn("notes/keep.md", pages)
        self.assertNotIn(".venv/lib/package/readme.md", pages)

    def test_collect_typed_edges_preserves_body_and_mentioned_in_reasons(self):
        import wiki_graph

        write_md(self.wiki_root / "notes/a.md", base_fm(title="A"), "See [B](./b.md)")
        write_md(self.wiki_root / "notes/b.md", base_fm(title="B"), "")
        write_md(
            self.wiki_root / "entities/tool.md",
            base_fm(title="Tool", type="entity", mentioned_in=["notes/a.md"]),
            "",
        )

        pages = wiki_graph.collect_pages(self.wiki_root)
        edges = wiki_graph.collect_typed_edges(pages)

        self.assertIn(wiki_graph.Edge("notes/a.md", "notes/b.md", "body_link", 1.0), edges)
        self.assertIn(wiki_graph.Edge("notes/a.md", "entities/tool.md", "mentioned_in", 2.0), edges)
        self.assertEqual(wiki_graph.edge_pairs(edges), [("notes/a.md", "entities/tool.md"), ("notes/a.md", "notes/b.md")])

    def test_neighborhood_walks_incoming_and_outgoing_edges_with_reasons(self):
        import wiki_graph

        write_md(self.wiki_root / "a.md", base_fm(title="A"), "See [B](./b.md)")
        write_md(self.wiki_root / "b.md", base_fm(title="B"), "See [C](./c.md)")
        write_md(self.wiki_root / "c.md", base_fm(title="C"), "")

        pages = wiki_graph.collect_pages(self.wiki_root)
        edges = wiki_graph.collect_typed_edges(pages)
        around = wiki_graph.neighborhood("b.md", pages, edges, depth=1)

        by_page = {item["page"]: item for item in around}
        self.assertEqual(by_page["a.md"]["distance"], 1)
        self.assertEqual(by_page["c.md"]["distance"], 1)
        self.assertIn("backlink:body_link", by_page["a.md"]["reasons"])
        self.assertIn("outlink:body_link", by_page["c.md"]["reasons"])

    def test_graph_health_reports_orphans_and_hubs(self):
        import wiki_graph

        write_md(self.wiki_root / "hub.md", base_fm(title="Hub"), "See [A](./a.md) and [B](./b.md)")
        write_md(self.wiki_root / "a.md", base_fm(title="A"), "")
        write_md(self.wiki_root / "b.md", base_fm(title="B"), "")
        write_md(self.wiki_root / "orphan.md", base_fm(title="Orphan"), "")

        pages = wiki_graph.collect_pages(self.wiki_root)
        edges = wiki_graph.collect_typed_edges(pages)
        health = wiki_graph.graph_health(pages, edges)

        self.assertIn("orphan.md", health["orphans"])
        self.assertEqual(health["hubs"][0]["page"], "hub.md")
        self.assertEqual(health["hubs"][0]["degree"], 2)
