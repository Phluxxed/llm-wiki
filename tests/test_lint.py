"""Tests for scripts/lint.py."""
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


def reload_lint_with_root(wiki_root: Path):
    """Reload lint with WIKI_ROOT pointing at a temp wiki dir."""
    if "lint" in sys.modules:
        del sys.modules["lint"]
    import lint  # noqa: E402
    lint.WIKI_ROOT = wiki_root
    return lint


class ResolveLinkTest(unittest.TestCase):
    """Mirrors render.resolve_link semantics — sibling, ../, wiki-root-relative."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_wiki_root_relative_resolves(self):
        lint = reload_lint_with_root(self.wiki_root)
        targets = {"components/b.md"}
        self.assertEqual(lint.resolve_link("./components/b.md", "components/a.md", targets), "components/b.md")
        self.assertEqual(lint.resolve_link("components/b.md", "components/a.md", targets), "components/b.md")

    def test_sibling_resolves_from_subdir(self):
        lint = reload_lint_with_root(self.wiki_root)
        targets = {"components/b.md"}
        self.assertEqual(lint.resolve_link("./b.md", "components/a.md", targets), "components/b.md")

    def test_dotdot_resolves(self):
        lint = reload_lint_with_root(self.wiki_root)
        targets = {"components/b.md"}
        self.assertEqual(lint.resolve_link("../components/b.md", "entities/a.md", targets), "components/b.md")

    def test_unresolvable_returns_none(self):
        lint = reload_lint_with_root(self.wiki_root)
        targets = {"components/b.md"}
        self.assertIsNone(lint.resolve_link("./ghost.md", "components/a.md", targets))


class BrokenBodyLinkCheckTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _base_fm(self, **overrides):
        fm = {"title": "X", "category": "X", "status": "Live", "owner": "x", "tags": [],
              "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        fm.update(overrides)
        return fm

    def _run(self):
        lint = reload_lint_with_root(self.wiki_root)
        pages = lint.collect_pages()
        all_md = lint.collect_all_md_paths()
        return [i for i in lint.run_checks(pages, set(), set(), all_md) if i["check"] == "broken_body_link"]

    def test_valid_sibling_link_passes(self):
        write_md(self.wiki_root / "components/a.md", self._base_fm(title="A"), "Linked: [B](./b.md)")
        write_md(self.wiki_root / "components/b.md", self._base_fm(title="B"), "")
        self.assertEqual(self._run(), [])

    def test_valid_wiki_root_relative_link_passes(self):
        write_md(self.wiki_root / "components/a.md", self._base_fm(title="A"), "Linked: [B](./components/b.md)")
        write_md(self.wiki_root / "components/b.md", self._base_fm(title="B"), "")
        self.assertEqual(self._run(), [])

    def test_valid_dotdot_link_passes(self):
        write_md(self.wiki_root / "entities/a.md",
                 self._base_fm(title="A", type="entity"),
                 "Linked: [B](../components/b.md)")
        write_md(self.wiki_root / "components/b.md", self._base_fm(title="B"), "")
        self.assertEqual(self._run(), [])

    def test_broken_sibling_link_flagged(self):
        write_md(self.wiki_root / "components/a.md", self._base_fm(title="A"), "Linked: [Ghost](./ghost.md)")
        issues = self._run()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["file"], "components/a.md")
        self.assertIn("./ghost.md", issues[0]["detail"])

    def test_link_to_source_file_passes(self):
        """Body links to source files (e.g. for citation) should resolve."""
        write_md(self.wiki_root / "components/a.md", self._base_fm(title="A"), "See [Spec](../sources/SPEC.md)")
        (self.wiki_root / "sources").mkdir()
        (self.wiki_root / "sources/SPEC.md").write_text("# Spec\n", encoding="utf-8")
        self.assertEqual(self._run(), [])

    def test_external_links_ignored(self):
        write_md(self.wiki_root / "components/a.md", self._base_fm(title="A"),
                 "See [Web](https://example.com) and [Anchor](#section).")
        self.assertEqual(self._run(), [])

    def test_typo_in_subdir_flagged(self):
        write_md(self.wiki_root / "components/a.md",
                 self._base_fm(title="A"),
                 "Linked: [B](./componnts/b.md)")  # typo: componnts
        write_md(self.wiki_root / "components/b.md", self._base_fm(title="B"), "")
        issues = self._run()
        self.assertEqual(len(issues), 1)
        self.assertIn("componnts", issues[0]["detail"])


class MentionedInCheckTest(unittest.TestCase):
    """Entity pages list `mentioned_in:` paths. Per wiki-agent.md both `./notes/x.md`
    and bare `notes/x.md` are valid wiki-root-relative forms — both must resolve."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _note_fm(self, **overrides):
        fm = {"title": "X", "category": "X", "status": "Live", "owner": "x", "tags": [],
              "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        fm.update(overrides)
        return fm

    def _entity_fm(self, mentioned_in, **overrides):
        fm = {"title": "E", "type": "entity", "category": "Entities & Concepts",
              "status": "Live", "owner": "x", "tags": [], "mentioned_in": mentioned_in,
              "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        fm.update(overrides)
        return fm

    def _run(self):
        lint = reload_lint_with_root(self.wiki_root)
        pages = lint.collect_pages()
        all_md = lint.collect_all_md_paths()
        return [i for i in lint.run_checks(pages, set(), set(), all_md) if i["check"] == "mentioned_in_missing"]

    def test_dot_slash_prefix_resolves(self):
        write_md(self.wiki_root / "notes/foo.md", self._note_fm(title="Foo"), "")
        write_md(self.wiki_root / "entities/e.md", self._entity_fm(mentioned_in=["./notes/foo.md"]), "")
        self.assertEqual(self._run(), [])

    def test_bare_path_resolves(self):
        write_md(self.wiki_root / "notes/foo.md", self._note_fm(title="Foo"), "")
        write_md(self.wiki_root / "entities/e.md", self._entity_fm(mentioned_in=["notes/foo.md"]), "")
        self.assertEqual(self._run(), [])

    def test_missing_target_flagged(self):
        write_md(self.wiki_root / "entities/e.md", self._entity_fm(mentioned_in=["./notes/ghost.md"]), "")
        issues = self._run()
        self.assertEqual(len(issues), 1)
        self.assertIn("./notes/ghost.md", issues[0]["detail"])


if __name__ == "__main__":
    unittest.main()
