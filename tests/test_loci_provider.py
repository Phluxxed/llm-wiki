from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.compiler import compile_context
from llm_wiki_core.contracts import CompileRequest
from llm_wiki_core.providers.loci import LociMcpGateway, LociProvider
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
        (self.root / ".llm-wiki.toml").write_text(
            'schema_version = "1"\n'
            'runtime_contract = "2"\n'
            '[compiler]\n'
            'providers = ["seed", "frontmatter", "text", "graph", "source"]\n',
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

    def test_mcp_gateway_hydrates_search_results_over_stdio(self):
        server = self.root / "fake_loci_mcp.py"
        server.write_text(
            textwrap.dedent(
                """\
                from mcp.server.fastmcp import FastMCP

                mcp = FastMCP("fake-loci")

                @mcp.tool()
                def loci_search(repo: str, query: str, limit: int = 20):
                    return {"symbols": [{
                        "id": "implementation/runtime.py::propagate_upgrade#function",
                        "file_path": "implementation/runtime.py",
                        "line": 1,
                        "end_line": 2,
                    }]}

                @mcp.tool()
                def loci_get(repo: str, symbol_ids: list[str], context: int = 0):
                    return {"symbols": [{
                        "id": symbol_ids[0],
                        "file_path": "implementation/runtime.py",
                        "line": 1,
                        "end_line": 2,
                        "source": "def propagate_upgrade():\\n    return 'canonical runtime'",
                    }]}

                if __name__ == "__main__":
                    mcp.run(transport="stdio")
                """
            ),
            encoding="utf-8",
        )
        provider = LociProvider(
            gateway=LociMcpGateway(command=sys.executable, args=(str(server),))
        )

        response = compile_context(self.root, self.request(), extra_providers=(provider,)).to_dict()

        evidence = next(item for item in response["evidence"] if item["provider"] == "loci")
        self.assertEqual(evidence["route"], "indexed_section")
        self.assertEqual(evidence["locator"]["start_line"], 1)
        self.assertIn("canonical runtime", evidence["content"])

    def test_missing_mcp_service_degrades_with_explicit_diagnostic(self):
        provider = LociProvider(gateway=LociMcpGateway())

        with patch("llm_wiki_core.providers.loci.shutil.which", return_value=None):
            response = compile_context(
                self.root,
                self.request(),
                extra_providers=(provider,),
            ).to_dict()

        diagnostic = next(item for item in response["diagnostics"] if item["provider"] == "loci")
        self.assertEqual(diagnostic["code"], "LOCI_MCP_UNAVAILABLE")
        self.assertEqual(diagnostic["details"]["transport"], "mcp_stdio")

    def test_slow_mcp_service_times_out_and_degrades(self):
        server = self.root / "slow_loci_mcp.py"
        server.write_text(
            textwrap.dedent(
                """\
                import time
                from mcp.server.fastmcp import FastMCP

                mcp = FastMCP("slow-loci")

                @mcp.tool()
                def loci_search(repo: str, query: str, limit: int = 20):
                    time.sleep(1)
                    return {"symbols": []}

                if __name__ == "__main__":
                    mcp.run(transport="stdio")
                """
            ),
            encoding="utf-8",
        )
        provider = LociProvider(
            gateway=LociMcpGateway(
                command=sys.executable,
                args=(str(server),),
                timeout_seconds=0.05,
            )
        )

        response = compile_context(
            self.root,
            self.request(),
            extra_providers=(provider,),
        ).to_dict()

        diagnostic = next(item for item in response["diagnostics"] if item["provider"] == "loci")
        self.assertEqual(diagnostic["code"], "LOCI_MCP_TIMEOUT", diagnostic)

    def test_mcp_hydration_must_match_the_validated_search_locator(self):
        class MismatchedGateway:
            def retrieve(self, wiki_root, query, *, limit):
                from llm_wiki_core.providers.loci import LociRetrieval

                return LociRetrieval(
                    results=(
                        {
                            "id": "implementation/runtime.py::propagate_upgrade#function",
                            "file_path": "implementation/runtime.py",
                            "line": 1,
                            "end_line": 2,
                            "content": "do not leak mismatched evidence",
                            "hydrated_locator": {
                                "file_path": "other.py",
                                "line": 1,
                                "end_line": 2,
                            },
                        },
                    )
                )

        response = compile_context(
            self.root,
            self.request(),
            extra_providers=(LociProvider(gateway=MismatchedGateway()),),
        ).to_dict()

        self.assertNotIn("do not leak mismatched evidence", str(response["evidence"]))
        diagnostic = next(item for item in response["diagnostics"] if item["provider"] == "loci")
        self.assertEqual(diagnostic["code"], "LOCI_RESULT_INVALID")

    def test_stopword_only_search_result_is_not_evidence(self):
        class StopwordOnlyGateway:
            def retrieve(self, wiki_root, query, *, limit):
                from llm_wiki_core.providers.loci import LociRetrieval

                return LociRetrieval(
                    results=(
                        {
                            "id": "notes/overview.md::What It Is#section",
                            "file_path": "notes/overview.md",
                            "line": 1,
                            "end_line": 1,
                            "content": "What it is: a general overview.",
                            "hydrated_locator": {
                                "file_path": "notes/overview.md",
                                "line": 1,
                                "end_line": 1,
                            },
                        },
                    )
                )

        request = CompileRequest.from_mapping(
            {"alias": "test", "question": "What is two plus two?", "seeds": []}
        )
        response = compile_context(
            self.root,
            request,
            extra_providers=(LociProvider(gateway=StopwordOnlyGateway()),),
        ).to_dict()

        self.assertFalse(any(item["provider"] == "loci" for item in response["evidence"]))

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
