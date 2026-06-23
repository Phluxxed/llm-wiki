"""Tests for query.py agent graph commands."""

import contextlib
import io
import json
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


def reload_query_with_root(wiki_root: Path):
    if "query" in sys.modules:
        del sys.modules["query"]
    import query  # noqa: E402
    query.WIKI_ROOT = wiki_root
    return query


class QueryAgentGraphTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_query(self, *args: str) -> str:
        query = reload_query_with_root(self.wiki_root)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            query.main(list(args))
        return buf.getvalue()

    def test_links_and_backlinks_report_edge_reasons(self):
        write_md(self.wiki_root / "notes/a.md", base_fm(title="A"), "See [B](./b.md)")
        write_md(self.wiki_root / "notes/b.md", base_fm(title="B"), "")

        links = self.run_query("--links", "notes/a.md")
        backlinks = self.run_query("--backlinks", "notes/b.md")

        self.assertIn("notes/b.md", links)
        self.assertIn("body_link", links)
        self.assertIn("notes/a.md", backlinks)
        self.assertIn("body_link", backlinks)

    def test_links_json_has_stable_agent_shape(self):
        write_md(self.wiki_root / "notes/a.md", base_fm(title="A"), "See [B](./b.md)")
        write_md(self.wiki_root / "notes/b.md", base_fm(title="B"), "")

        data = json.loads(self.run_query("--links", "notes/a.md", "--json"))

        self.assertEqual(data["kind"], "links")
        self.assertEqual(data["page"], "notes/a.md")
        self.assertEqual(data["links"][0]["page"], "notes/b.md")
        self.assertEqual(data["links"][0]["edge_type"], "body_link")
        self.assertEqual(data["links"][0]["weight"], 1.0)

    def test_around_reports_nearby_pages_with_directional_reasons(self):
        write_md(self.wiki_root / "a.md", base_fm(title="A"), "See [B](./b.md)")
        write_md(self.wiki_root / "b.md", base_fm(title="B"), "See [C](./c.md)")
        write_md(self.wiki_root / "c.md", base_fm(title="C"), "")

        out = self.run_query("--around", "b.md", "--depth", "1")

        self.assertIn("a.md", out)
        self.assertIn("c.md", out)
        self.assertIn("backlink:body_link", out)
        self.assertIn("outlink:body_link", out)

    def test_agent_overview_reports_first_move_context(self):
        write_md(
            self.wiki_root / "hub.md",
            base_fm(title="Hub"),
            "See [A](./a.md)\n\n> **Open question:** Which owner signs off?",
        )
        write_md(self.wiki_root / "a.md", base_fm(title="A"), "")
        write_md(self.wiki_root / "orphan.md", base_fm(title="Orphan"), "")
        (self.wiki_root / "log.md").write_text("## [2026-06-19] update | Updated hub.md\n", encoding="utf-8")

        out = self.run_query("--agent-overview")

        self.assertIn("Agent Overview", out)
        self.assertIn("hub.md", out)
        self.assertIn("orphan.md", out)
        self.assertIn("Which owner signs off?", out)
        self.assertIn("Updated hub.md", out)

    def test_agent_overview_json_has_stable_agent_shape(self):
        write_md(
            self.wiki_root / "hub.md",
            base_fm(title="Hub"),
            "See [A](./a.md)\n\n> **Open question:** Which owner signs off?",
        )
        write_md(self.wiki_root / "a.md", base_fm(title="A"), "")
        write_md(self.wiki_root / "orphan.md", base_fm(title="Orphan"), "")
        (self.wiki_root / "log.md").write_text("## [2026-06-19] update | Updated hub.md\n", encoding="utf-8")

        data = json.loads(self.run_query("--agent-overview", "--json"))

        self.assertEqual(data["kind"], "agent_overview")
        self.assertEqual(data["page_count"], 3)
        self.assertEqual(data["edge_count"], 1)
        entry_pages = {item["page"] for item in data["suggested_entry_pages"]}
        self.assertIn("hub.md", entry_pages)
        self.assertEqual(data["orphans"][0]["page"], "orphan.md")
        self.assertEqual(data["open_questions"][0]["question"], "Which owner signs off?")
        self.assertEqual(data["recent_log"][0]["detail"], "Updated hub.md")

    def test_context_pack_includes_seed_neighbors_and_inclusion_reasons(self):
        risk_status = "\u26a0\ufe0f Action required"
        write_md(
            self.wiki_root / "notes/a.md",
            base_fm(title="A", tags=["agent"], source="sources/a.md"),
            (
                "Seed body.\n\n"
                "See [B](./b.md)\n\n"
                "> **Open question:** Does B change the answer?\n\n"
                "| Risk | Likelihood | Impact | Mitigation | Status |\n"
                "| --- | --- | --- | --- | --- |\n"
                f"| R1 | Low | High | M1 | {risk_status} |\n"
            ),
        )
        write_md(self.wiki_root / "notes/b.md", base_fm(title="B", tags=["agent"]), "B body.")
        write_md(
            self.wiki_root / "entities/tool.md",
            base_fm(title="Tool", type="entity", mentioned_in=["notes/a.md"]),
            "Tool body.",
        )
        (self.wiki_root / "sources").mkdir()
        (self.wiki_root / "sources/a.md").write_text("Raw source excerpt.", encoding="utf-8")
        (self.wiki_root / "log.md").write_text("## [2026-06-19] update | Updated notes/a.md\n", encoding="utf-8")

        out = self.run_query("--context-pack", "notes/a.md", "--tokens", "1200")

        self.assertIn("Context Pack: notes/a.md", out)
        self.assertIn("outlink:body_link", out)
        self.assertIn("outlink:mentioned_in", out)
        self.assertIn("Does B change the answer?", out)
        self.assertIn("R1", out)
        self.assertIn("sources/a.md", out)
        self.assertIn("Raw source excerpt.", out)
        self.assertIn("Updated notes/a.md", out)

    def test_context_pack_json_has_stable_agent_shape(self):
        write_md(
            self.wiki_root / "notes/a.md",
            base_fm(title="A", tags=["agent"], source="sources/a.md"),
            "Seed body.\n\nSee [B](./b.md)\n\n> **Open question:** Does B change the answer?",
        )
        write_md(self.wiki_root / "notes/b.md", base_fm(title="B", tags=["agent"]), "B body.")
        (self.wiki_root / "sources").mkdir()
        (self.wiki_root / "sources/a.md").write_text("Raw source excerpt.", encoding="utf-8")
        (self.wiki_root / "log.md").write_text("## [2026-06-19] update | Updated notes/a.md\n", encoding="utf-8")

        data = json.loads(self.run_query("--context-pack", "notes/a.md", "--tokens", "1200", "--json"))

        self.assertEqual(data["kind"], "context_pack")
        self.assertEqual(data["seed"]["page"], "notes/a.md")
        self.assertEqual(data["budget"]["requested_tokens"], 1200)
        self.assertEqual(data["included_pages"][0]["page"], "notes/b.md")
        self.assertIn("outlink:body_link", data["included_pages"][0]["reasons"])
        self.assertEqual(data["source_refs"][0]["source"], "sources/a.md")
        self.assertEqual(data["source_excerpts"][0]["content"], "Raw source excerpt.")
        self.assertEqual(data["open_questions"][0]["question"], "Does B change the answer?")
        self.assertEqual(data["recent_log"][0]["detail"], "Updated notes/a.md")
