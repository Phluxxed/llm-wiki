from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_mcp.errors import WikiMcpError
from llm_wiki_mcp.registry import register_wiki
from llm_wiki_mcp.wiki_runtime import (
    agent_manual,
    context_pack,
    get_page,
    get_source_excerpt,
    graph_health,
    links,
    overview,
    query_pages,
)
from tests.wiki_fixture import base_fm, create_wiki_root, write_md


class WikiRuntimeTest(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get("LLM_WIKI_HOME")
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        os.environ["LLM_WIKI_HOME"] = str(self.tmp / "home")
        self.wiki = create_wiki_root(self.tmp / "wiki")
        (self.wiki / "sources").mkdir()
        (self.wiki / "sources" / "a.md").write_text("Raw source excerpt.", encoding="utf-8")
        write_md(
            self.wiki / "notes" / "a.md",
            base_fm(title="A", tags=["agent"], source="sources/a.md"),
            "Seed body.\n\nSee [B](./b.md)\n\n> **Open question:** Does B matter?",
        )
        write_md(self.wiki / "notes" / "b.md", base_fm(title="B", tags=["agent"]), "B body.")
        write_md(self.wiki / "orphan.md", base_fm(title="Orphan"), "Orphan body.")
        register_wiki("brain", self.wiki)

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("LLM_WIKI_HOME", None)
        else:
            os.environ["LLM_WIKI_HOME"] = self._old_home
        self._tmp.cleanup()

    def test_overview_links_graph_health_and_query_use_registered_wiki(self):
        overview_data = overview("brain")
        self.assertEqual(overview_data["kind"], "agent_overview")
        self.assertEqual(overview_data["page_count"], 3)
        self.assertEqual(overview_data["edge_count"], 1)

        link_data = links("brain", "notes/a.md")
        self.assertEqual(link_data["kind"], "links")
        self.assertEqual(link_data["links"][0]["page"], "notes/b.md")

        health = graph_health("brain")
        self.assertEqual(health["kind"], "graph_health")
        self.assertIn("orphan.md", [item["page"] for item in health["orphans"]])

        query = query_pages("brain", tag="agent")
        self.assertEqual(query["kind"], "summary")
        self.assertEqual({item["page"] for item in query["pages"]}, {"notes/a.md", "notes/b.md"})

    def test_context_pack_page_and_source_excerpt_are_bounded(self):
        pack = context_pack("brain", "notes/a.md", tokens=500)
        self.assertEqual(pack["kind"], "context_pack")
        self.assertEqual(pack["seed"]["page"], "notes/a.md")
        self.assertEqual(pack["source_excerpts"][0]["content"], "Raw source excerpt.")

        page = get_page("brain", "notes/a.md", max_chars=9)
        self.assertEqual(page["page"], "notes/a.md")
        self.assertTrue(page["content"].endswith("[truncated]"))

        source = get_source_excerpt("brain", page="notes/a.md", max_chars=3)
        self.assertEqual(source["source"], "sources/a.md")
        self.assertTrue(source["content"].endswith("[truncated]"))

    def test_context_pack_matches_v1_golden_contract(self):
        expected = json.loads((FIXTURES / "context_pack_v1.json").read_text(encoding="utf-8"))

        pack = context_pack("brain", "notes/a.md", tokens=500)

        self.assertEqual(pack, expected)

    def test_source_excerpt_rejects_path_escape(self):
        with self.assertRaises(WikiMcpError) as raised:
            get_source_excerpt("brain", source="../log.md")

        self.assertEqual(raised.exception.code, "INVALID_INPUT")

    def test_context_pack_does_not_read_source_paths_outside_sources(self):
        (self.tmp / "secret.md").write_text("do not leak", encoding="utf-8")
        write_md(
            self.wiki / "notes" / "escape.md",
            base_fm(title="Escape", source="../secret.md"),
            "Escape body.",
        )

        pack = context_pack("brain", "notes/escape.md", tokens=500)

        self.assertEqual(pack["source_excerpts"], [])
        self.assertNotIn("do not leak", str(pack))
        self.assertEqual(
            pack["gaps"][0],
            {"page": "notes/escape.md", "gap": "source_missing:../secret.md"},
        )

    def test_agent_manual_returns_wiki_operating_contract(self):
        (self.wiki / "wiki-agent.md").write_text(
            "# Wiki Agent\n\nDo not edit sources/.\nAlways update index.md.\n",
            encoding="utf-8",
        )
        (self.wiki / "CONVENTIONS.md").write_text("# Conventions\n", encoding="utf-8")

        manual = agent_manual("brain")

        self.assertEqual(manual["kind"], "wiki_agent_manual")
        self.assertEqual(manual["alias"], "brain")
        self.assertEqual(manual["path"], str(self.wiki.resolve()))
        self.assertIn("Do not edit sources/", manual["operating_manual"])
        self.assertEqual(manual["operating_manual_path"], "wiki-agent.md")
        self.assertEqual(manual["conventions"], "# Conventions\n")
        self.assertIn("Read and obey operating_manual before mutating this wiki", manual["must_follow"])
        self.assertTrue(manual["doctor"]["is_wiki"])

    def test_registered_wiki_reads_do_not_execute_local_traversal_scripts(self):
        (self.wiki / "scripts" / "query.py").write_text(
            "raise RuntimeError('wiki-local query code executed')\n",
            encoding="utf-8",
        )
        (self.wiki / "scripts" / "wiki_graph.py").write_text(
            "raise RuntimeError('wiki-local graph code executed')\n",
            encoding="utf-8",
        )

        self.assertEqual(overview("brain")["page_count"], 3)
        self.assertEqual(links("brain", "notes/a.md")["links"][0]["page"], "notes/b.md")
        self.assertEqual(query_pages("brain", tag="agent")["count"], 2)
        self.assertEqual(context_pack("brain", "notes/a.md", tokens=500)["kind"], "context_pack")
        self.assertEqual(get_page("brain", "notes/a.md")["page"], "notes/a.md")
        self.assertEqual(graph_health("brain")["page_count"], 3)
