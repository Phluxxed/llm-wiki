"""Tests for scripts/render.py."""
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))


class RenderModuleTest(unittest.TestCase):
    def test_module_imports(self):
        import render  # noqa: F401


class CollectPagesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_page(self, rel: str, frontmatter: dict, body: str) -> None:
        import yaml
        path = self.wiki_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        fm = yaml.safe_dump(frontmatter, sort_keys=False).strip()
        path.write_text(f"---\n{fm}\n---\n{body}\n", encoding="utf-8")

    def test_returns_one_entry_per_md_file(self):
        import render
        self.write_page(
            "use-cases/foo.md",
            {"title": "Foo", "category": "Demo", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"},
            "## What This Is\n\nA test page.",
        )
        pages = render.collect_pages(self.wiki_root)
        self.assertEqual(len(pages), 1)
        self.assertIn("use-cases/foo.md", pages)
        page = pages["use-cases/foo.md"]
        self.assertEqual(page["title"], "Foo")
        self.assertIn("test page", page["rendered_html"])

    def test_skips_excluded_files_and_dirs(self):
        import render
        self.write_page("index.md", {"title": "Index"}, "skip me")
        self.write_page("sources/raw.md", {"title": "Raw"}, "skip me too")
        self.write_page(
            "papers/keep.md",
            {"title": "Keep", "category": "X", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"},
            "kept",
        )
        pages = render.collect_pages(self.wiki_root)
        self.assertEqual(set(pages.keys()), {"papers/keep.md"})


class CollectEdgesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_page(self, rel: str, frontmatter: dict, body: str) -> None:
        import yaml
        path = self.wiki_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        fm = yaml.safe_dump(frontmatter, sort_keys=False).strip()
        path.write_text(f"---\n{fm}\n---\n{body}\n", encoding="utf-8")

    def test_explicit_link_creates_edge(self):
        import render
        base = {"category": "x", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        self.write_page("a.md", {**base, "title": "A"}, "Links to [B](./b.md)")
        self.write_page("b.md", {**base, "title": "B"}, "")
        pages = render.collect_pages(self.wiki_root)
        edges = render.collect_edges(pages)
        self.assertIn(("a.md", "b.md"), edges)

    def test_mentioned_in_creates_reverse_edge(self):
        import render
        base = {"category": "x", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        self.write_page("paper.md", {**base, "title": "Paper"}, "")
        self.write_page(
            "entities/openai.md",
            {**base, "title": "OpenAI", "type": "entity", "mentioned_in": ["paper.md"]},
            "",
        )
        pages = render.collect_pages(self.wiki_root)
        edges = render.collect_edges(pages)
        self.assertIn(("paper.md", "entities/openai.md"), edges)


if __name__ == "__main__":
    unittest.main()
