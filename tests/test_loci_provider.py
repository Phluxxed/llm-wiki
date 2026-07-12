from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.compiler import compile_context
from llm_wiki_core.contracts import CompileRequest
from llm_wiki_core.providers.loci import LociProvider
from tests.wiki_fixture import base_fm, write_md


class FakeLociError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class LociProviderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write_md(
            self.root / "notes" / "overview.md",
            base_fm(title="Overview"),
            "General overview without implementation detail.",
        )
        (self.root / "implementation").mkdir()
        (self.root / "implementation" / "runtime.py").write_text(
            "def propagate_upgrade():\n    return 'canonical runtime'\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def request(self) -> CompileRequest:
        return CompileRequest.from_mapping(
            {
                "alias": "test",
                "question": "Where is propagate_upgrade implemented?",
                "seeds": [],
            }
        )

    def test_loci_result_becomes_exact_section_candidate(self):
        calls = []

        def search(repo, query, *, limit, ensure_fresh):
            calls.append(("search", Path(repo), query, limit, ensure_fresh))
            return [
                {
                    "id": "implementation/runtime.py::propagate_upgrade#function",
                    "file_path": "implementation/runtime.py",
                    "line": 1,
                    "end_line": 2,
                }
            ]

        def get_file(repo, file_path, *, start_line, end_line, ensure_fresh):
            calls.append(("file", Path(repo), file_path, start_line, end_line, ensure_fresh))
            return {
                "file": file_path,
                "content": "def propagate_upgrade():\n    return 'canonical runtime'",
                "start_line": start_line,
                "end_line": end_line,
            }

        provider = LociProvider(search_fn=search, file_fn=get_file)

        response = compile_context(self.root, self.request(), extra_providers=(provider,)).to_dict()

        evidence = next(item for item in response["evidence"] if item["provider"] == "loci")
        self.assertEqual(evidence["locator"]["symbol_id"], "implementation/runtime.py::propagate_upgrade#function")
        self.assertEqual(evidence["locator"]["start_line"], 1)
        self.assertIn("propagate_upgrade", evidence["content"])
        self.assertTrue(all(call[-1] is True for call in calls))

    def test_unindexed_repo_degrades_with_structured_diagnostic(self):
        def search(*args, **kwargs):
            raise FakeLociError("REPO_NOT_INDEXED")

        provider = LociProvider(search_fn=search, file_fn=lambda *args, **kwargs: {})

        response = compile_context(self.root, self.request(), extra_providers=(provider,)).to_dict()

        diagnostic = next(item for item in response["diagnostics"] if item["provider"] == "loci")
        self.assertEqual(diagnostic["code"], "LOCI_REPO_NOT_INDEXED")
        self.assertNotIn("REPO_NOT_INDEXED", diagnostic["message"])

    def test_loci_result_cannot_read_outside_wiki_root(self):
        def search(*args, **kwargs):
            return [{"id": "escape", "file_path": "../secret.md", "line": 1, "end_line": 1}]

        provider = LociProvider(
            search_fn=search,
            file_fn=lambda *args, **kwargs: {"content": "do not leak"},
        )

        response = compile_context(self.root, self.request(), extra_providers=(provider,)).to_dict()

        self.assertNotIn("do not leak", str(response))
        diagnostic = next(item for item in response["diagnostics"] if item["code"] == "LOCI_RESULT_INVALID")
        self.assertEqual(diagnostic["details"]["file"], "../secret.md")


if __name__ == "__main__":
    unittest.main()
