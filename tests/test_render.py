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

    def test_collects_sources_separately_from_pages(self):
        import render
        self.write_page("sources/raw.md", {"title": "Raw"}, "# Raw\n\nEvidence.")
        self.write_page(
            "papers/keep.md",
            {"title": "Keep", "category": "X", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"},
            "kept",
        )

        pages = render.collect_pages(self.wiki_root)
        sources = render.collect_sources(self.wiki_root)

        self.assertEqual(set(pages.keys()), {"papers/keep.md"})
        self.assertIn("sources/raw.md", sources)
        self.assertEqual(sources["sources/raw.md"]["title"], "Raw")
        self.assertIn("Evidence", sources["sources/raw.md"]["rendered_html"])


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

    def test_sibling_link_from_subdir_creates_edge(self):
        """A page in components/ linking to [B](./b.md) should create an edge
        to components/b.md — the most natural markdown form for sibling links."""
        import render
        base = {"category": "x", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        self.write_page("components/a.md", {**base, "title": "A"}, "Links to [B](./b.md)")
        self.write_page("components/b.md", {**base, "title": "B"}, "")
        pages = render.collect_pages(self.wiki_root)
        edges = render.collect_edges(pages)
        self.assertIn(("components/a.md", "components/b.md"), edges)

    def test_dotdot_link_creates_edge(self):
        """A page in entities/ linking to [B](../components/b.md) should
        resolve correctly via source-relative path resolution."""
        import render
        base = {"category": "x", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        self.write_page(
            "entities/a.md",
            {**base, "title": "A", "type": "entity"},
            "Links to [B](../components/b.md)",
        )
        self.write_page("components/b.md", {**base, "title": "B"}, "")
        pages = render.collect_pages(self.wiki_root)
        edges = render.collect_edges(pages)
        self.assertIn(("entities/a.md", "components/b.md"), edges)

    def test_wiki_root_relative_link_from_subdir_creates_edge(self):
        """A page in components/ linking to [B](./components/b.md) — the
        wiki-root-relative form documented in CONVENTIONS.md — should also work."""
        import render
        base = {"category": "x", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        self.write_page("components/a.md", {**base, "title": "A"}, "Links to [B](./components/b.md)")
        self.write_page("components/b.md", {**base, "title": "B"}, "")
        pages = render.collect_pages(self.wiki_root)
        edges = render.collect_edges(pages)
        self.assertIn(("components/a.md", "components/b.md"), edges)

    def test_broken_link_does_not_create_edge(self):
        import render
        base = {"category": "x", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        self.write_page("components/a.md", {**base, "title": "A"}, "Links to [Ghost](./ghost.md)")
        pages = render.collect_pages(self.wiki_root)
        edges = render.collect_edges(pages)
        self.assertEqual(edges, [])

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


class RewriteInternalLinksTest(unittest.TestCase):
    """rewrite_internal_links should inject data-page attrs on intra-wiki .md links
    so the SPA click handler in renderPage picks them up. External/non-page links
    are left untouched."""

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

    def test_sibling_link_gets_data_page(self):
        import render
        base = {"category": "x", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        self.write_page("components/a.md", {**base, "title": "A"}, "See [B](./b.md)")
        self.write_page("components/b.md", {**base, "title": "B"}, "")
        pages = render.collect_pages(self.wiki_root)
        html = pages["components/a.md"]["rendered_html"]
        self.assertIn('data-page="components/b.md"', html)

    def test_dotdot_link_gets_data_page(self):
        import render
        base = {"category": "x", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        self.write_page("entities/a.md", {**base, "title": "A", "type": "entity"}, "See [B](../components/b.md)")
        self.write_page("components/b.md", {**base, "title": "B"}, "")
        pages = render.collect_pages(self.wiki_root)
        html = pages["entities/a.md"]["rendered_html"]
        self.assertIn('data-page="components/b.md"', html)

    def test_wiki_root_relative_link_gets_data_page(self):
        import render
        base = {"category": "x", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        self.write_page("components/a.md", {**base, "title": "A"}, "See [B](./components/b.md)")
        self.write_page("components/b.md", {**base, "title": "B"}, "")
        pages = render.collect_pages(self.wiki_root)
        html = pages["components/a.md"]["rendered_html"]
        self.assertIn('data-page="components/b.md"', html)

    def test_unresolved_link_left_untouched(self):
        """Links that don't resolve to a wiki page or source file should keep
        their original href and have no in-wiki routing attribute injected."""
        import render
        base = {"category": "x", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        self.write_page("components/a.md", {**base, "title": "A"}, "See [Ghost](./ghost.md)")
        pages = render.collect_pages(self.wiki_root)
        html = pages["components/a.md"]["rendered_html"]
        self.assertIn('href="./ghost.md"', html)
        self.assertNotIn('data-page', html)
        self.assertNotIn('data-source', html)

    def test_source_link_gets_data_source(self):
        import render
        base = {"category": "x", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        self.write_page("components/a.md", {**base, "title": "A"}, "See [Spec](../sources/SPEC.md)")
        self.write_page("sources/SPEC.md", {"title": "Spec"}, "# Spec\n\nRaw evidence.")
        pages = render.collect_pages(self.wiki_root)
        sources = render.collect_sources(self.wiki_root)

        html = pages["components/a.md"]["rendered_html"]
        self.assertIn('data-source="sources/SPEC.md"', html)
        self.assertNotIn(("components/a.md", "sources/SPEC.md"), render.collect_edges(pages))
        self.assertIn("sources/SPEC.md", sources)

    def test_external_links_left_untouched(self):
        import render
        base = {"category": "x", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        self.write_page("components/a.md", {**base, "title": "A"},
                        "See [Web](https://example.com) and [Anchor](#section).")
        pages = render.collect_pages(self.wiki_root)
        html = pages["components/a.md"]["rendered_html"]
        self.assertNotIn('data-page', html)
        self.assertIn('href="https://example.com"', html)


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

    def test_collects_attention_item_blockquotes(self):
        import render
        base = {"category": "x", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"}
        self.write_page("p.md", {**base, "title": "P"}, (
            "> **Risk:** Registry state may be stale.\n"
            "> **Caveat:** Applies only to local stdio MCP.\n"
            "> **Failure mode:** Agent skips the contract loader.\n"
        ))

        pages = render.collect_pages(self.wiki_root)
        risks = render.extract_risks(pages)

        self.assertEqual(len(risks), 3)
        self.assertEqual({r["kind"] for r in risks}, {"risk", "caveat", "failure mode"})
        self.assertTrue(all(r["status_symbol"] == "⚠️" for r in risks))


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


class BuildSearchIndexTest(unittest.TestCase):
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

    def test_returns_one_doc_per_page_with_search_fields(self):
        import render
        self.write_page(
            "p.md",
            {"title": "Foo", "category": "Demo", "status": "Live", "owner": "x", "tags": ["alpha", "beta"], "created": "2026-04-30", "last_reviewed": "2026-04-30"},
            "Body text here.",
        )
        pages = render.collect_pages(self.wiki_root)
        docs = render.build_search_index(pages)
        self.assertEqual(len(docs), 1)
        d = docs[0]
        self.assertEqual(d["id"], "p.md")
        self.assertEqual(d["title"], "Foo")
        self.assertEqual(d["category"], "Demo")
        self.assertEqual(d["tags"], ["alpha", "beta"])
        self.assertIn("Body text here", d["body"])


class PageSummaryTest(unittest.TestCase):
    """The card/lead summary comes from the OKF `description` frontmatter field."""

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

    def _data(self):
        import json
        import render
        pages = render.collect_pages(self.wiki_root)
        sources = render.collect_sources(self.wiki_root)
        html = render.render_html(pages, [], [], [], [], [], sources)
        marker = "window.WIKI_DATA = "
        start = html.find(marker)
        end = html.find("</script>", start)
        return json.loads(html[start + len(marker):end].rstrip("; \n"))

    def test_summary_uses_description_field(self):
        self.write_page(
            "p.md",
            {"title": "Foo", "category": "Demo", "status": "Live", "owner": "x",
             "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30",
             "type": "article", "description": "A one-line summary of Foo.",
             "timestamp": "2026-04-30T00:00:00Z"},
            "## What This Is\n\nLong body prose that should not become the summary.",
        )
        data = self._data()
        self.assertEqual(data["pages"]["p.md"]["summary"], "A one-line summary of Foo.")

    def test_sources_are_in_data_block_but_not_pages(self):
        self.write_page("sources/SPEC.md", {"title": "Spec"}, "# Spec\n\nRaw evidence.")
        data = self._data()
        self.assertNotIn("sources/SPEC.md", data["pages"])
        self.assertIn("sources/SPEC.md", data["sources"])
        self.assertEqual(data["sources"]["sources/SPEC.md"]["title"], "Spec")

    def test_summary_empty_when_no_description(self):
        self.write_page(
            "p.md",
            {"title": "Foo", "category": "Demo", "status": "Live", "owner": "x",
             "tags": [], "created": "2026-04-30", "last_reviewed": "2026-04-30"},
            "## What This Is\n\nBody prose.",
        )
        data = self._data()
        self.assertEqual(data["pages"]["p.md"]["summary"], "")


class RenderHtmlShellTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_html_contains_required_sections(self):
        import render
        html = render.render_html({}, [], [], [], [], [])
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn('<nav id="sidebar">', html)
        self.assertIn('id="view-home"', html)
        self.assertIn('id="view-positions"', html)
        self.assertIn('id="view-page"', html)
        self.assertIn('id="view-search"', html)
        self.assertIn('id="view-graph"', html)
        self.assertIn('id="view-risks"', html)
        self.assertIn('id="view-recent"', html)
        self.assertIn('id="view-open-qs"', html)
        self.assertIn('id="view-entities"', html)
        self.assertIn("window.WIKI_DATA", html)

    def test_data_block_is_valid_json(self):
        import json
        import render
        html = render.render_html({}, [], [], [], [], [], {})
        marker = "window.WIKI_DATA = "
        start = html.find(marker)
        self.assertGreater(start, -1)
        end = html.find("</script>", start)
        block = html[start + len(marker):end].rstrip("; \n")
        data = json.loads(block)
        self.assertEqual(set(data.keys()), {"pages", "sources", "edges", "log", "risks", "open_qs", "search"})


class SourcesViewTest(unittest.TestCase):
    def test_sources_view_and_open_source_script_present(self):
        import render
        html = render.render_html({}, [], [], [], [], [], {})
        self.assertIn('id="view-sources"', html)
        self.assertIn("function renderSources", html)
        self.assertIn("window.openSource", html)

    def test_home_and_sidebar_expose_sources_without_pages(self):
        import json
        import render
        sources = {
            "sources/raw.md": {
                "path": "sources/raw.md",
                "title": "Raw Source",
                "kind": "md",
                "rendered_html": "<p>Raw evidence.</p>",
                "text": "Raw evidence.",
                "meta": [],
            }
        }
        html = render.render_html({}, [], [], [], [], [], sources)
        self.assertIn("Source Evidence", html)
        self.assertIn("buildSidebarSources", html)
        self.assertIn("sb-source", html)
        self.assertIn("data-source", html)
        marker = "window.WIKI_DATA = "
        start = html.find(marker)
        end = html.find("</script>", start)
        data = json.loads(html[start + len(marker):end].rstrip("; \n"))
        self.assertEqual(data["sources"]["sources/raw.md"]["meta"], [])

    def test_source_rendering_moves_leading_metadata_out_of_body(self):
        import render
        with tempfile.TemporaryDirectory() as tmp:
            wiki_root = Path(tmp)
            source_path = wiki_root / "sources" / "raw.md"
            source_path.parent.mkdir()
            source_path.write_text(
                "> **Source type:** External note.\n"
                "> **URL:** https://example.test/source\n"
                "> **Fetched:** 2026-06-30\n"
                "\n"
                "# Raw Source\n\n"
                "Useful evidence.\n",
                encoding="utf-8",
            )
            sources = render.collect_sources(wiki_root)

        source = sources["sources/raw.md"]
        self.assertEqual(source["title"], "Raw Source")
        self.assertEqual(
            source["meta"],
            [
                {"label": "Source type", "value": "External note."},
                {"label": "URL", "value": "https://example.test/source"},
                {"label": "Fetched", "value": "2026-06-30"},
            ],
        )
        self.assertNotIn("Source type", source["rendered_html"])
        self.assertNotIn("<h1", source["rendered_html"])
        self.assertIn("Useful evidence.", source["rendered_html"])


class ProvenanceBadgeTest(unittest.TestCase):
    def test_provenance_badge_styles_and_labels_present(self):
        import render
        html = render.render_html({}, [], [], [], [], [])
        self.assertIn(".badge.prov-source", html)
        self.assertIn(".badge.prov-synth", html)
        # Labels emitted by Home, Page, and Search renderers:
        self.assertIn("'source'", html)
        self.assertIn("'synthesized'", html)


class SidebarPagesTest(unittest.TestCase):
    def test_sidebar_has_pages_section_and_builder(self):
        import render
        html = render.render_html({}, [], [], [], [], [])
        self.assertIn('id="sidebar-pages"', html)
        self.assertIn("function buildSidebarPages", html)
        self.assertIn("function setSidebarActivePage", html)
        self.assertIn("buildSidebarPages();", html)


class HomeViewTest(unittest.TestCase):
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

    def test_home_view_includes_render_function_and_data_groups(self):
        import render
        self.write_page(
            "p.md",
            {"title": "Foo", "category": "Alpha", "status": "Live", "owner": "x", "tags": [], "created": "2026-04-01", "last_reviewed": "2026-04-30"},
            "## What This Is\nFoo description.",
        )
        pages = render.collect_pages(self.wiki_root)
        html = render.render_html(pages, [], [], [], [], [])
        self.assertIn("renderHome", html)
        self.assertIn('"Alpha"', html)
        self.assertIn('"Foo"', html)


class PageViewTest(unittest.TestCase):
    def test_page_view_script_renders_markdown_and_edges(self):
        import render
        html = render.render_html({}, [], [], [], [], [])
        self.assertIn("function renderPage", html)
        self.assertIn("Grounded in", html)
        self.assertIn("Referenced by", html)

    def test_page_view_expands_reading_column_when_toc_is_hidden(self):
        import render
        html = render.render_html({}, [], [], [], [], [])
        self.assertIn(".article-cols.no-toc", html)
        self.assertIn("cols.classList.toggle('no-toc', hideToc)", html)
        self.assertIn("const hideToc = items.length < 2", html)


class SearchViewTest(unittest.TestCase):
    def test_search_view_includes_minisearch_and_handler(self):
        import render
        html = render.render_html({}, [], [], [], [], [])
        self.assertIn("minisearch", html.lower())
        self.assertIn("function renderSearch", html)


class GraphViewTest(unittest.TestCase):
    def test_graph_view_includes_d3_and_render_function(self):
        import render
        html = render.render_html({}, [], [], [], [], [])
        self.assertIn("d3js.org", html)
        self.assertIn("function renderGraph", html)
        self.assertIn('id="graph-svg"', html)

    def test_graph_view_has_full_filter_features(self):
        import render
        html = render.render_html({}, [], [], [], [], [])
        # Filter UI features that were on graph.html and must remain.
        self.assertIn('id="gp-search"', html)
        self.assertIn('id="gp-type-chips"', html)
        self.assertIn('id="gp-tag-chips"', html)
        self.assertIn('id="gp-depth-slider"', html)
        self.assertIn('id="gp-toggle-labels"', html)
        self.assertIn('id="gp-toggle-arrows"', html)
        self.assertIn('id="gp-link-dist"', html)
        self.assertIn('id="gp-charge-str"', html)
        self.assertIn('id="graph-panel-toggle"', html)
        self.assertIn('id="graph-tooltip"', html)
        # Graph rendering features.
        self.assertIn('marker id="arrow"', html)
        self.assertIn('function nodeRadius', html)
        self.assertIn('function getNeighbours', html)
        self.assertIn('function highlight', html)
        self.assertIn('d3.zoom', html)


class RisksViewTest(unittest.TestCase):
    def test_risks_view_renders_table(self):
        import render
        html = render.render_html({}, [], [], [], [], [])
        self.assertIn("function renderRisks", html)
        self.assertIn("Likelihood", html)
        self.assertIn("Impact", html)


class RecentViewTest(unittest.TestCase):
    def test_recent_view_renders_log(self):
        import render
        html = render.render_html({}, [], [], [], [], [])
        self.assertIn("function renderRecent", html)


class OpenQsViewTest(unittest.TestCase):
    def test_open_qs_view_renders_list(self):
        import render
        html = render.render_html({}, [], [], [], [], [])
        self.assertIn("function renderOpenQs", html)


class EntitiesViewTest(unittest.TestCase):
    def test_entities_view_renders_list(self):
        import render
        html = render.render_html({}, [], [], [], [], [])
        self.assertIn("function renderEntities", html)


class EndToEndRenderTest(unittest.TestCase):
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

    def test_render_pipeline_writes_wiki_html(self):
        import render
        self.write_page(
            "use-cases/foo.md",
            {"title": "Foo", "category": "Demo", "status": "Live", "owner": "x", "tags": ["alpha"], "created": "2026-04-30", "last_reviewed": "2026-04-30", "source": "sources/raw.md"},
            "## What This Is\nFoo body.\n\nSee [Raw](./sources/raw.md).\n\n## Risk Register\n| Risk | Likelihood | Impact | Mitigation | Status |\n| --- | --- | --- | --- | --- |\n| R1 | Low | Med | M1 | ⚠️ Action required |\n",
        )
        self.write_page("sources/raw.md", {"title": "Raw"}, "# Raw\n\nEvidence.")
        (self.wiki_root / "log.md").write_text("## [2026-04-30] init | Created wiki\n", encoding="utf-8")
        out = self.wiki_root / "wiki.html"
        render.run(self.wiki_root, out)
        html = out.read_text(encoding="utf-8")
        self.assertIn("Foo", html)
        self.assertIn('"alpha"', html)
        self.assertIn("Created wiki", html)
        self.assertIn('"⚠️"', html)
        self.assertIn('"sources"', html)
        self.assertIn('data-source=\\"sources/raw.md\\"', html)


class PageTypeTest(unittest.TestCase):
    """page_type() honours explicit `type:` values for any string, not just
    entity/concept. This is what makes multi-primary-type wikis possible —
    a wiki with policies/, controls/, articles/ can give each a distinct
    `type:` and they all flow through to the graph view as separate node
    classes. Pages without a `type:` field default to "primary"."""

    def test_passes_through_explicit_article_type(self):
        import render
        self.assertEqual(render.page_type({"type": "article"}), "article")

    def test_passes_through_explicit_policy_type(self):
        import render
        self.assertEqual(render.page_type({"type": "policy"}), "policy")

    def test_entity_type_preserved(self):
        import render
        self.assertEqual(render.page_type({"type": "entity"}), "entity")
        self.assertEqual(render.page_type({"type": "concept"}), "concept")

    def test_meta_type_preserved(self):
        import render
        self.assertEqual(render.page_type({"type": "meta"}), "meta")

    def test_meta_category_returns_meta(self):
        # Existing behaviour: category containing "meta" → meta type label.
        import render
        self.assertEqual(render.page_type({"category": "Meta — Wiki"}), "meta")

    def test_no_type_field_returns_primary(self):
        import render
        self.assertEqual(render.page_type({}), "primary")
        self.assertEqual(render.page_type({"title": "X"}), "primary")

    def test_empty_string_type_returns_primary(self):
        import render
        self.assertEqual(render.page_type({"type": ""}), "primary")


class GraphTypeRenderingTest(unittest.TestCase):
    """The graph JS must default to 'primary' (not 'use-case') for nodes
    without an explicit type, and the colour map must include 'primary'."""

    def test_graph_js_default_type_is_primary(self):
        import render
        html = render.render_html({}, [], [], [], [], [])
        # The fallback node type in the graph initialiser.
        self.assertIn("'primary'", html)
        self.assertNotIn("'use-case'", html)

    def test_colour_map_includes_primary(self):
        import render
        html = render.render_html({}, [], [], [], [], [])
        # Colour map should map 'primary' to a colour value.
        self.assertRegex(html, r"'primary':\s*'#[0-9a-fA-F]{3,6}'")


if __name__ == "__main__":
    unittest.main()
