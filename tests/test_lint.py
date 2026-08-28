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

    def test_venv_markdown_is_not_a_link_target(self):
        """Project-local venv docs should never count as wiki content."""
        write_md(self.wiki_root / "components/a.md", self._base_fm(title="A"), "See [Pkg](../.venv/pkg/README.md)")
        (self.wiki_root / ".venv/pkg").mkdir(parents=True)
        (self.wiki_root / ".venv/pkg/README.md").write_text("# Package\n", encoding="utf-8")
        issues = self._run()
        self.assertEqual(len(issues), 1)
        self.assertIn(".venv/pkg/README.md", issues[0]["detail"])

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


class GroundingEvidenceCheckTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)
        (self.wiki_root / "sources").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _fm(self, **overrides):
        fm = {"title": "X", "category": "X", "status": "Live", "owner": "x", "tags": [],
              "created": "2026-04-30", "last_reviewed": "2026-04-30",
              "type": "paper", "description": "d", "timestamp": "2026-04-30T00:00:00Z"}
        fm.update(overrides)
        return fm

    def _run(self, *check_names):
        lint = reload_lint_with_root(self.wiki_root)
        pages = lint.collect_pages()
        sources = lint.collect_source_files()
        all_md = lint.collect_all_md_paths()
        return [i for i in lint.run_checks(pages, sources, set(), all_md)
                if i["check"] in check_names]

    def test_manifest_source_requires_evidence(self):
        (self.wiki_root / "sources/repo.md").write_text("URL: https://example.com/repo\n")
        write_md(
            self.wiki_root / "papers/a.md",
            self._fm(source="sources/repo.md", source_mode="manifest"),
            "body",
        )
        issues = self._run("grounding_evidence_missing")
        self.assertEqual(len(issues), 1)
        self.assertIn("manifest", issues[0]["detail"])

    def test_binary_source_requires_evidence(self):
        (self.wiki_root / "sources/paper.pdf").write_bytes(b"%PDF-fake")
        write_md(self.wiki_root / "papers/a.md", self._fm(source="sources/paper.pdf"), "body")
        issues = self._run("grounding_evidence_missing")
        self.assertEqual(len(issues), 1)
        self.assertIn("binary", issues[0]["detail"])

    def test_missing_evidence_file_is_flagged(self):
        (self.wiki_root / "sources/repo.md").write_text("manifest\n")
        write_md(
            self.wiki_root / "papers/a.md",
            self._fm(
                source="sources/repo.md", source_mode="manifest",
                evidence=["sources/missing-evidence.md"],
            ),
            "body",
        )
        issues = self._run("evidence_missing")
        self.assertEqual(len(issues), 1)
        self.assertIn("missing-evidence.md", issues[0]["detail"])

    def test_evidence_file_counts_as_referenced_source(self):
        (self.wiki_root / "sources/repo.md").write_text("manifest\n")
        (self.wiki_root / "sources/repo-evidence.md").write_text("inspected source evidence\n")
        write_md(
            self.wiki_root / "papers/a.md",
            self._fm(
                source="sources/repo.md", source_mode="manifest",
                evidence="sources/repo-evidence.md",
            ),
            "body",
        )
        self.assertEqual(
            self._run("grounding_evidence_missing", "evidence_missing", "orphan_source"),
            [],
        )


class CoverFieldCheckTest(unittest.TestCase):
    """Chapter notes carry `cover:` pointing at their cover note.
    Lint enforces: target exists, target isn't itself a chapter, source values match."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)
        (self.wiki_root / "sources").mkdir()
        (self.wiki_root / "sources/BIG.pdf").write_bytes(b"%PDF-fake")

    def tearDown(self):
        self._tmp.cleanup()

    def _note_fm(self, **overrides):
        fm = {"title": "X", "category": "X", "status": "Live", "owner": "x", "tags": [],
              "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        fm.update(overrides)
        return fm

    def _run(self, *check_names):
        lint = reload_lint_with_root(self.wiki_root)
        pages = lint.collect_pages()
        sources = lint.collect_source_files()
        all_md = lint.collect_all_md_paths()
        return [i for i in lint.run_checks(pages, sources, set(), all_md) if i["check"] in check_names]

    def test_chapter_with_valid_cover_passes(self):
        write_md(self.wiki_root / "notes/big.md",
                 self._note_fm(title="Big", source="sources/BIG.pdf"), "")
        write_md(self.wiki_root / "notes/big-ch1.md",
                 self._note_fm(title="Ch1", source="sources/BIG.pdf", cover="./notes/big.md"), "")
        self.assertEqual(self._run("cover_missing", "cover_chain", "cover_source_mismatch"), [])

    def test_chapter_with_bare_cover_path_passes(self):
        write_md(self.wiki_root / "notes/big.md",
                 self._note_fm(title="Big", source="sources/BIG.pdf"), "")
        write_md(self.wiki_root / "notes/big-ch1.md",
                 self._note_fm(title="Ch1", source="sources/BIG.pdf", cover="notes/big.md"), "")
        self.assertEqual(self._run("cover_missing", "cover_chain", "cover_source_mismatch"), [])

    def test_missing_cover_target_flagged(self):
        write_md(self.wiki_root / "notes/big-ch1.md",
                 self._note_fm(title="Ch1", source="sources/BIG.pdf", cover="./notes/ghost.md"), "")
        issues = self._run("cover_missing")
        self.assertEqual(len(issues), 1)
        self.assertIn("./notes/ghost.md", issues[0]["detail"])

    def test_cover_chain_flagged(self):
        # A chapter cannot point at another chapter — only at a non-chapter cover note.
        write_md(self.wiki_root / "notes/big.md",
                 self._note_fm(title="Big", source="sources/BIG.pdf"), "")
        write_md(self.wiki_root / "notes/big-ch1.md",
                 self._note_fm(title="Ch1", source="sources/BIG.pdf", cover="./notes/big.md"), "")
        write_md(self.wiki_root / "notes/big-ch2.md",
                 self._note_fm(title="Ch2", source="sources/BIG.pdf", cover="./notes/big-ch1.md"), "")
        issues = self._run("cover_chain")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["file"], "notes/big-ch2.md")

    def test_cover_source_mismatch_flagged(self):
        (self.wiki_root / "sources/OTHER.pdf").write_bytes(b"%PDF-fake")
        write_md(self.wiki_root / "notes/big.md",
                 self._note_fm(title="Big", source="sources/BIG.pdf"), "")
        write_md(self.wiki_root / "notes/big-ch1.md",
                 self._note_fm(title="Ch1", source="sources/OTHER.pdf", cover="./notes/big.md"), "")
        issues = self._run("cover_source_mismatch")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["file"], "notes/big-ch1.md")
        self.assertIn("sources/OTHER.pdf", issues[0]["detail"])
        self.assertIn("sources/BIG.pdf", issues[0]["detail"])


class TypeAwareSectionChecksTest(unittest.TestCase):
    """Primary section checks come from the generated template for that type."""

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
        return [i for i in lint.run_checks(pages, set(), set(), all_md) if i["check"] == "missing_section"]

    def test_no_type_field_uses_legacy_primary_sections(self):
        # Untyped legacy page with no matching template still gets the fallback.
        write_md(self.wiki_root / "policies/foo.md", self._base_fm(title="Foo"), "Some body text.")
        issues = self._run()
        details = {i["detail"] for i in issues}
        self.assertEqual(len(issues), 4)
        self.assertTrue(any("What This Is" in d for d in details))
        self.assertTrue(any("How It Works" in d for d in details))
        self.assertTrue(any("Risk Register" in d for d in details))
        self.assertTrue(any("Prerequisites" in d for d in details))

    def test_no_type_field_with_all_sections_passes(self):
        body = "## What This Is\n## How It Works\n## Risk Register\n## Prerequisites\n"
        write_md(self.wiki_root / "policies/foo.md", self._base_fm(title="Foo"), body)
        self.assertEqual(self._run(), [])

    def test_explicit_type_without_template_uses_legacy_primary_sections(self):
        write_md(self.wiki_root / "articles/foo.md", self._base_fm(title="Foo", type="article"), "Some body.")
        issues = self._run()
        self.assertEqual(len(issues), 4)

    def test_template_sections_replace_legacy_primary_sections(self):
        (self.wiki_root / "_templates").mkdir()
        (self.wiki_root / "_templates/work-history.md").write_text(
            "---\ntype: work-history\n---\n"
            "## What Happened\n\n"
            "## Why It Mattered\n\n"
            "## Evidence\n",
            encoding="utf-8",
        )
        body = "## What Happened\nUpdated MCP setup.\n\n## Evidence\nSession log.\n"
        write_md(self.wiki_root / "work-history/foo.md", self._base_fm(title="Foo", type="work-history"), body)

        issues = self._run()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["detail"], "work-history page missing section: Why It Mattered")

    def test_template_sections_pass_without_legacy_primary_sections(self):
        (self.wiki_root / "_templates").mkdir()
        (self.wiki_root / "_templates/pattern.md").write_text(
            "---\ntype: pattern\n---\n"
            "## Trigger\n\n"
            "## Move\n\n"
            "## Failure Mode\n",
            encoding="utf-8",
        )
        body = (
            "## Trigger\nWhen the request is over-governed.\n\n"
            "## Move\nStrip it back to the primitive.\n\n"
            "## Failure Mode\nAdding process instead of behavior.\n"
        )
        write_md(self.wiki_root / "patterns/foo.md", self._base_fm(title="Foo", type="pattern"), body)
        self.assertEqual(self._run(), [])

    def test_entity_type_still_uses_entity_sections(self):
        write_md(self.wiki_root / "entities/openai.md",
                 self._base_fm(title="OpenAI", type="entity"),
                 "Body with no mandatory sections.")
        issues = self._run()
        details = {i["detail"] for i in issues}
        self.assertTrue(any("What It Is" in d for d in details))
        self.assertTrue(any("How We Use It" in d for d in details))
        self.assertTrue(any("Where It Appears" in d for d in details))

    def test_meta_type_still_skipped(self):
        write_md(self.wiki_root / "notes/changelog.md",
                 self._base_fm(title="Changelog", type="meta"),
                 "Body with no mandatory sections.")
        self.assertEqual(self._run(), [])


class RequiredFrontmatterTest(unittest.TestCase):
    """OKF conformance (strict superset): `type`, `description`, and `timestamp`
    are required frontmatter fields in addition to the existing seven.

    - `type` satisfies OKF's one required routing field (and stays our colour signal)
    - `description` is a one-line summary (OKF-recommended; required for us)
    - `timestamp` is ISO 8601 last-meaningful-change, distinct from created/last_reviewed
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _conformant_fm(self, **overrides):
        fm = {"title": "X", "category": "X", "status": "Live", "owner": "x", "tags": [],
              "created": "2026-06-17", "last_reviewed": "2026-06-17",
              "type": "policy", "description": "One-line summary.",
              "timestamp": "2026-06-17T00:00:00Z"}
        fm.update(overrides)
        return fm

    def _run(self):
        lint = reload_lint_with_root(self.wiki_root)
        pages = lint.collect_pages()
        all_md = lint.collect_all_md_paths()
        return [i for i in lint.run_checks(pages, set(), set(), all_md) if i["check"] == "frontmatter"]

    def _details(self):
        return {i["detail"] for i in self._run()}

    def test_missing_type_flagged(self):
        fm = self._conformant_fm(); del fm["type"]
        write_md(self.wiki_root / "policies/foo.md", fm, "")
        self.assertTrue(any("type" in d for d in self._details()))

    def test_missing_description_flagged(self):
        fm = self._conformant_fm(); del fm["description"]
        write_md(self.wiki_root / "policies/foo.md", fm, "")
        self.assertTrue(any("description" in d for d in self._details()))

    def test_missing_timestamp_flagged(self):
        fm = self._conformant_fm(); del fm["timestamp"]
        write_md(self.wiki_root / "policies/foo.md", fm, "")
        self.assertTrue(any("timestamp" in d for d in self._details()))

    def test_empty_type_flagged(self):
        write_md(self.wiki_root / "policies/foo.md", self._conformant_fm(type=""), "")
        self.assertTrue(any("type" in d for d in self._details()))

    def test_conformant_page_has_no_frontmatter_issues(self):
        write_md(self.wiki_root / "policies/foo.md", self._conformant_fm(), "")
        self.assertEqual(self._run(), [])


class OKFConformanceTest(unittest.TestCase):
    """OKF v0.1 §9 conformance checks that operate outside collect_pages():

    - §9.1: every non-reserved .md must have parseable frontmatter. collect_pages()
      silently skips frontmatter-less files, so they need their own scan.
    - §11: the root index.md should declare okf_version.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *check_names):
        lint = reload_lint_with_root(self.wiki_root)
        issues = lint.check_okf_conformance()
        return [i for i in issues if not check_names or i["check"] in check_names]

    def test_md_without_frontmatter_flagged(self):
        (self.wiki_root / "policies").mkdir(parents=True)
        (self.wiki_root / "policies/raw.md").write_text("# Just a heading\nNo frontmatter.\n", encoding="utf-8")
        issues = self._run("okf_no_frontmatter")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["file"], "policies/raw.md")

    def test_reserved_and_excluded_files_not_flagged(self):
        # index.md / log.md are reserved (no frontmatter expected); README is excluded.
        (self.wiki_root / "log.md").write_text("# Log\n", encoding="utf-8")
        (self.wiki_root / "README.md").write_text("# Readme\n", encoding="utf-8")
        self.assertEqual(self._run("okf_no_frontmatter"), [])

    def test_root_index_missing_okf_version_flagged(self):
        (self.wiki_root / "index.md").write_text("# Index\n\n* [Foo](./policies/foo.md) - x\n", encoding="utf-8")
        issues = self._run("okf_version_missing")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["file"], "index.md")

    def test_root_index_with_okf_version_clean(self):
        (self.wiki_root / "index.md").write_text(
            '---\nokf_version: "0.1"\n---\n# Index\n\n* [Foo](./policies/foo.md) - x\n', encoding="utf-8")
        self.assertEqual(self._run("okf_version_missing"), [])

    def test_conformant_tree_clean(self):
        (self.wiki_root / "index.md").write_text('---\nokf_version: "0.1"\n---\n# Index\n', encoding="utf-8")
        write_md(self.wiki_root / "policies/foo.md",
                 {"title": "Foo", "category": "X", "status": "Live", "owner": "x", "tags": [],
                  "created": "2026-06-17", "last_reviewed": "2026-06-17", "type": "policy",
                  "description": "d", "timestamp": "2026-06-17T00:00:00Z"}, "body")
        self.assertEqual(self._run(), [])


class ProjectMetadataCheckTest(unittest.TestCase):
    """Project Identity and Project Membership are lint-owned metadata."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _fm(self, **overrides):
        fm = {
            "title": "X", "category": "X", "status": "Live", "owner": "x", "tags": [],
            "created": "2026-08-27", "last_reviewed": "2026-08-27",
            "type": "policy", "description": "d", "timestamp": "2026-08-27T00:00:00Z",
        }
        fm.update(overrides)
        return fm

    def _run(self):
        lint = reload_lint_with_root(self.wiki_root)
        pages = lint.collect_pages()
        all_md = lint.collect_all_md_paths()
        return [
            issue for issue in lint.run_checks(pages, set(), set(), all_md)
            if issue["check"].startswith("project_")
        ]

    def test_valid_identity_and_membership_pass(self):
        write_md(
            self.wiki_root / "projects/alpha.md",
            self._fm(type="project", identity={
                "project_id": "alpha",
                "aliases": ["Alpha"],
                "remotes": ["github.com/example/alpha"],
            }),
            "",
        )
        write_md(self.wiki_root / "notes/n.md", self._fm(projects=["alpha"]), "")
        self.assertEqual(self._run(), [])

    def test_project_without_identity_is_migration_warning(self):
        write_md(self.wiki_root / "projects/alpha.md", self._fm(type="project"), "")
        issues = self._run()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["check"], "project_identity")
        self.assertEqual(issues[0]["severity"], "warning")
        self.assertIn("identity", issues[0]["detail"])

    def test_malformed_identity_is_error(self):
        write_md(self.wiki_root / "projects/alpha.md", self._fm(type="project", identity="alpha"), "")
        issues = self._run()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["check"], "project_identity")
        self.assertEqual(issues[0]["severity"], "error")
        self.assertIn("mapping", issues[0]["detail"])

    def test_identity_on_non_project_is_error(self):
        write_md(
            self.wiki_root / "notes/n.md",
            self._fm(identity={"project_id": "alpha", "aliases": ["alpha"]}),
            "",
        )
        issues = self._run()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["check"], "project_identity")
        self.assertEqual(issues[0]["severity"], "error")
        self.assertIn("type: project", issues[0]["detail"])

    def test_null_scalar_empty_duplicate_and_dangling_membership_are_errors(self):
        write_md(
            self.wiki_root / "projects/alpha.md",
            self._fm(type="project", identity={"project_id": "alpha", "aliases": ["alpha"]}),
            "",
        )
        write_md(self.wiki_root / "notes/null.md", self._fm(projects=None), "")
        write_md(self.wiki_root / "notes/scalar.md", self._fm(projects="alpha"), "")
        write_md(self.wiki_root / "notes/empty.md", self._fm(projects=[]), "")
        write_md(self.wiki_root / "notes/duplicate.md", self._fm(projects=["alpha", "alpha"]), "")
        write_md(self.wiki_root / "notes/dangling.md", self._fm(projects=["missing"]), "")
        issues = self._run()
        self.assertEqual(
            {(issue["file"], issue["check"]) for issue in issues},
            {
                ("notes/null.md", "project_membership"),
                ("notes/scalar.md", "project_membership"),
                ("notes/empty.md", "project_membership"),
                ("notes/duplicate.md", "project_membership"),
                ("notes/dangling.md", "project_dangling_membership"),
            },
        )
        self.assertTrue(all(issue["severity"] == "error" for issue in issues))

    def test_project_id_alias_and_remote_collisions_are_errors(self):
        identity_a = {
            "project_id": "alpha",
            "aliases": ["Shared"],
            "remotes": ["github.com/example/shared"],
        }
        identity_b = {
            "project_id": "alpha",
            "aliases": [" shared "],
            "remotes": ["github.com/example/shared"],
        }
        write_md(self.wiki_root / "projects/a.md", self._fm(type="project", identity=identity_a), "")
        write_md(self.wiki_root / "projects/b.md", self._fm(type="project", identity=identity_b), "")
        checks = {issue["check"] for issue in self._run()}
        self.assertIn("project_duplicate_id", checks)
        self.assertIn("project_duplicate_alias", checks)
        self.assertIn("project_duplicate_remote", checks)

    def test_membership_cannot_resolve_through_a_duplicate_project_id(self):
        identity = {"project_id": "alpha", "aliases": ["alpha"]}
        write_md(self.wiki_root / "projects/a.md", self._fm(type="project", identity=identity), "")
        write_md(
            self.wiki_root / "projects/b.md",
            self._fm(type="project", identity={"project_id": "alpha", "aliases": ["alpha-two"]}),
            "",
        )
        write_md(self.wiki_root / "notes/n.md", self._fm(projects=["alpha"]), "")

        issues = self._run()

        self.assertTrue(any(issue["check"] == "project_duplicate_id" for issue in issues))
        self.assertTrue(any(
            issue["file"] == "notes/n.md"
            and issue["check"] == "project_dangling_membership"
            for issue in issues
        ))

    def test_identity_limits_and_normalized_remote_are_errors(self):
        write_md(
            self.wiki_root / "projects/alpha.md",
            self._fm(type="project", identity={
                "project_id": "Alpha",
                "aliases": ["", "x"] * 17,
                "remotes": ["https://github.com/example/alpha", "github.com/example/alpha/../x"],
            }),
            "",
        )
        issues = self._run()
        details = "\n".join(issue["detail"] for issue in issues)
        self.assertTrue(any("project_id" in issue["detail"] for issue in issues))
        self.assertIn("aliases", details)
        self.assertIn("remotes", details)
        self.assertTrue(all(issue["severity"] == "error" for issue in issues))


if __name__ == "__main__":
    unittest.main()
