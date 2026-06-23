from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_mcp.errors import WikiMcpError
from llm_wiki_mcp.registry import list_wikis, register_wiki, unregister_wiki
from tests.wiki_fixture import create_wiki_root


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get("LLM_WIKI_HOME")
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("LLM_WIKI_HOME", None)
        else:
            os.environ["LLM_WIKI_HOME"] = self._old_home
        self._tmp.cleanup()

    def set_home(self, name: str) -> Path:
        home = self.tmp / name
        os.environ["LLM_WIKI_HOME"] = str(home)
        return home

    def test_registry_requires_explicit_agent_home(self):
        os.environ.pop("LLM_WIKI_HOME", None)

        with self.assertRaises(WikiMcpError) as raised:
            list_wikis()

        self.assertEqual(raised.exception.code, "CONFIG_REQUIRED")

    def test_register_list_unregister_are_scoped_to_agent_home(self):
        wiki = create_wiki_root(self.tmp / "wiki")

        codex_home = self.set_home("codex")
        created = register_wiki("brain", wiki, created_by="wikime")
        self.assertEqual(created["alias"], "brain")
        self.assertEqual(created["created_by"], "wikime")
        self.assertEqual(created["path"], str(wiki.resolve()))

        self.assertEqual([item["alias"] for item in list_wikis()["wikis"]], ["brain"])
        self.assertTrue((codex_home / "registry.json").exists())

        self.set_home("claude")
        self.assertEqual(list_wikis()["wikis"], [])

        self.set_home("codex")
        removed = unregister_wiki("brain")
        self.assertEqual(removed["alias"], "brain")
        self.assertEqual(list_wikis()["wikis"], [])

        registry = json.loads((codex_home / "registry.json").read_text(encoding="utf-8"))
        self.assertEqual(registry, {"version": 1, "wikis": {}})

    def test_register_rejects_non_wiki_directory(self):
        self.set_home("codex")
        non_wiki = self.tmp / "not-wiki"
        non_wiki.mkdir()

        with self.assertRaises(WikiMcpError) as raised:
            register_wiki("bad", non_wiki)

        self.assertEqual(raised.exception.code, "INVALID_WIKI")
        self.assertIn("wiki-agent.md", raised.exception.details["missing"])

    def test_register_warns_when_context_scripts_are_missing(self):
        self.set_home("codex")
        wiki = create_wiki_root(self.tmp / "wiki", with_scripts=False)

        created = register_wiki("brain", wiki)

        self.assertEqual(created["alias"], "brain")
        self.assertIn("scripts/query.py missing", created["warnings"])
