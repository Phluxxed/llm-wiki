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


class SourceProviderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "sources").mkdir()
        (self.root / "sources" / "runtime.md").write_text(
            "The canonical runtime owns traversal behavior.",
            encoding="utf-8",
        )
        write_md(
            self.root / "systems" / "runtime.md",
            base_fm(
                title="Runtime",
                source="sources/runtime.md",
                knowledge_state="current",
            ),
            "Runtime summary.",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_source_excerpt_is_the_authority_not_the_page_reference(self):
        request = CompileRequest.from_mapping(
            {"alias": "test", "question": "What does Runtime own?", "seeds": []}
        )

        response = compile_context(self.root, request).to_dict()

        page = next(item for item in response["evidence"] if item["provider"] == "frontmatter")
        source = next(item for item in response["evidence"] if item["provider"] == "source")
        self.assertIn("source_reference", page["authority_signals"])
        self.assertNotIn("authority", page["roles"])
        self.assertEqual(source["source"], "sources/runtime.md")
        self.assertEqual(source["authority_signals"], ["source_excerpt"])
        self.assertIn("authority", source["roles"])
        self.assertTrue(response["stop"]["sufficient"])

    def test_unsafe_source_path_is_diagnostic_and_never_read(self):
        (self.root / "secret.md").write_text("do not leak", encoding="utf-8")
        write_md(
            self.root / "systems" / "escape.md",
            base_fm(title="Escape", source="../secret.md", knowledge_state="current"),
            "Escape summary.",
        )
        request = CompileRequest.from_mapping(
            {"alias": "test", "question": "What does Escape contain?", "seeds": []}
        )

        response = compile_context(self.root, request).to_dict()

        self.assertNotIn("do not leak", str(response))
        diagnostic = next(item for item in response["diagnostics"] if item["code"] == "SOURCE_PATH_INVALID")
        self.assertEqual(diagnostic["provider"], "source")
        self.assertEqual(diagnostic["details"]["source"], "../secret.md")


if __name__ == "__main__":
    unittest.main()
