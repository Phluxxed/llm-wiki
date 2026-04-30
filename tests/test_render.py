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


class CollectLogTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_parses_entries_in_reverse_chronological_order(self):
        import render
        (self.wiki_root / "log.md").write_text(
            "# Log\n\n"
            "## [2026-04-28] init | Created wiki\n"
            "## [2026-04-29] add | Added page foo.md\n"
            "## [2026-04-30] update | Updated page foo.md\n",
            encoding="utf-8",
        )
        entries = render.collect_log(self.wiki_root)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["date"], "2026-04-30")
        self.assertEqual(entries[0]["action"], "update")
        self.assertEqual(entries[0]["detail"], "Updated page foo.md")
        self.assertEqual(entries[-1]["date"], "2026-04-28")

    def test_returns_empty_list_when_no_log(self):
        import render
        self.assertEqual(render.collect_log(self.wiki_root), [])


class ExtractRisksTest(unittest.TestCase):
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

    def test_collects_open_risks_only(self):
        import render
        base = {"category": "x", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        self.write_page("p.md", {**base, "title": "P"}, (
            "## Risk Register\n\n"
            "| Risk | Likelihood | Impact | Mitigation | Status |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| R1 | Low | High | M1 | ⚠️ Action required |\n"
            "| R2 | High | High | M2 | ✅ Handled |\n"
            "| R3 | Med | Med | M3 | 🔲 Not yet addressed |\n"
        ))
        pages = render.collect_pages(self.wiki_root)
        risks = render.extract_risks(pages)
        self.assertEqual(len(risks), 2)
        statuses = {r["status_symbol"] for r in risks}
        self.assertEqual(statuses, {"⚠️", "🔲"})


class ExtractOpenQsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_page(self, rel, frontmatter, body):
        import yaml
        path = self.wiki_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        fm = yaml.safe_dump(frontmatter, sort_keys=False).strip()
        path.write_text(f"---\n{fm}\n---\n{body}\n", encoding="utf-8")

    def test_extracts_open_question_blockquotes(self):
        import render
        base = {"category": "x", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        self.write_page("p.md", {**base, "title": "P"}, (
            "Some prose.\n\n"
            "> **Open question:** Does X reset on a sliding window?\n\n"
            "More prose.\n\n"
            "> **Open question:** What is the rate limit ceiling?\n"
        ))
        pages = render.collect_pages(self.wiki_root)
        qs = render.extract_open_qs(pages)
        self.assertEqual(len(qs), 2)
        self.assertEqual(qs[0]["page"], "p.md")
        self.assertEqual(qs[0]["question"], "Does X reset on a sliding window?")
        self.assertEqual(qs[1]["question"], "What is the rate limit ceiling?")


if __name__ == "__main__":
    unittest.main()
