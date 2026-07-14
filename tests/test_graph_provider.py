from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.compiler import compile_context
from llm_wiki_core.contracts import CompileRequest
from tests.wiki_fixture import base_fm, write_md


class GraphProviderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".llm-wiki.toml").write_text(
            'schema_version = "1"\n'
            'runtime_contract = "2"\n'
            '[compiler]\n'
            'providers = ["seed", "frontmatter", "text", "graph", "source"]\n'
            'graph_backend = "legacy"\n',
            encoding="utf-8",
        )
        write_md(
            self.root / "projects" / "brain.md",
            base_fm(title="Brain", knowledge_state="current"),
            "## Runtime\n\nBrain uses the [shared runtime](../concepts/runtime.md).",
        )
        write_md(
            self.root / "concepts" / "runtime.md",
            base_fm(title="Shared Runtime", knowledge_state="current"),
            "## Ownership\n\nThe runtime is owned by [llm-wiki](../systems/llm-wiki.md).",
        )
        write_md(
            self.root / "systems" / "llm-wiki.md",
            base_fm(title="llm-wiki", knowledge_state="current"),
            "## Role\n\nCanonical traversal implementation.",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_relationship_compile_includes_authored_bridge_spans(self):
        request = CompileRequest.from_mapping(
            {
                "alias": "test",
                "question": "How does Brain connect to llm-wiki?",
                "seeds": [],
            }
        )

        response = compile_context(self.root, request).to_dict()

        bridges = [item for item in response["evidence"] if item["provider"] == "graph"]
        self.assertEqual(
            [(item["page"], item["locator"]["start_line"]) for item in bridges],
            [("concepts/runtime.md", 16)],
        )
        self.assertIn("[llm-wiki](../systems/llm-wiki.md)", bridges[0]["content"])
        self.assertIn("bridge", bridges[0]["roles"])
        self.assertTrue(response["stop"]["sufficient"])

    def test_seed_authored_multi_endpoint_bridge_outranks_reciprocal_links(self):
        write_md(
            self.root / "ideas" / "left.md",
            base_fm(title="Left"),
            "Left cites [MOSS](../papers/moss.md).",
        )
        write_md(
            self.root / "ideas" / "right.md",
            base_fm(title="Right"),
            "Right cites [MOSS](../papers/moss.md).",
        )
        write_md(
            self.root / "papers" / "moss.md",
            base_fm(title="MOSS"),
            "## Summary\n\nMOSS sharpens both [Left](../ideas/left.md) and [Right](../ideas/right.md).",
        )
        request = CompileRequest.from_mapping(
            {
                "alias": "test",
                "question": "How does MOSS connect Left and Right?",
                "seeds": ["papers/moss.md", "ideas/left.md", "ideas/right.md"],
                "budget": {
                    "target_bytes": 500,
                    "max_bytes": 12_000,
                    "target_items": 1,
                    "max_items": 8,
                },
            }
        )

        response = compile_context(self.root, request).to_dict()

        bridge = next(item for item in response["evidence"] if "bridge" in item["roles"])
        self.assertEqual(bridge["page"], "papers/moss.md")
        self.assertEqual(bridge["content"], "MOSS sharpens both [Left](../ideas/left.md) and [Right](../ideas/right.md).")
        self.assertIn("seed_authored_multi_bridge", bridge["selection_reasons"])


if __name__ == "__main__":
    unittest.main()
