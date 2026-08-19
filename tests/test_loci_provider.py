from __future__ import annotations

import os
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
from llm_wiki_core.graph_adapter import GraphAdapterError
from llm_wiki_core.providers.loci import LociMcpGateway, LociProvider, LociRetrieval
from llm_wiki_core.providers.loci_transport import LociGatewayError, LociMcpClient
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
            "## Upgrade propagation\n\npropagate_upgrade is implemented by the canonical page workflow.",
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

        def search(repo, query, *, limit, file_paths, ensure_fresh):
            calls.append(("search", Path(repo), query, limit, file_paths, ensure_fresh))
            self.assertEqual(file_paths, ["notes/overview.md"])
            return [
                {
                    "id": "notes/overview.md::Upgrade propagation#section",
                    "file_path": "notes/overview.md",
                    "line": 2,
                    "end_line": 4,
                }
            ]

        def get_file(repo, file_path, *, start_line, end_line, ensure_fresh):
            calls.append(("file", Path(repo), file_path, start_line, end_line, ensure_fresh))
            return {
                "file": file_path,
                "content": "## Upgrade propagation\n\npropagate_upgrade is implemented by the canonical page workflow.",
                "start_line": start_line,
                "end_line": end_line,
            }

        provider = LociProvider(search_fn=search, file_fn=get_file)

        response = compile_context(self.root, self.request(), extra_providers=(provider,)).to_dict()

        evidence = next(item for item in response["evidence"] if item["provider"] == "loci")
        self.assertEqual(evidence["locator"]["symbol_id"], "notes/overview.md::Upgrade propagation#section")
        self.assertEqual(evidence["locator"]["start_line"], 2)
        self.assertEqual(evidence["page"], "notes/overview.md")
        self.assertIsNone(evidence["source"])
        self.assertIn("propagate_upgrade", evidence["content"])
        self.assertTrue(all(call[-1] is True for call in calls))

    def test_gateway_receives_exact_sorted_loaded_page_keys(self):
        write_md(
            self.root / "alpha.md",
            base_fm(title="Alpha"),
            "Alpha page.",
        )
        received = []

        class RecordingGateway:
            def retrieve(self, wiki_root, query, *, limit, file_paths):
                received.append(file_paths)
                return LociRetrieval()

        compile_context(
            self.root,
            self.request(),
            extra_providers=(LociProvider(gateway=RecordingGateway()),),
        )

        self.assertEqual(received, [("alpha.md", "notes/overview.md")])

    def test_indexed_section_on_current_page_carries_current_claim_role(self):
        write_md(
            self.root / "notes" / "overview.md",
            base_fm(title="Overview", knowledge_state="current"),
            "Current Overview implementation status and next work.",
        )

        class CurrentPageGateway:
            def retrieve(self, wiki_root, query, *, limit, file_paths):
                return LociRetrieval(
                    results=(
                        {
                            "id": "notes/overview.md::Current status#section",
                            "file_path": "notes/overview.md",
                            "line": 1,
                            "end_line": 1,
                            "content": "Current Overview implementation status and next work.",
                            "hydrated_locator": {
                                "file_path": "notes/overview.md",
                                "line": 1,
                                "end_line": 1,
                            },
                        },
                    )
                )

        request = CompileRequest.from_mapping(
            {
                "alias": "test",
                "question": "What is the current Overview implementation status?",
                "seeds": [],
            }
        )
        response = compile_context(
            self.root,
            request,
            extra_providers=(LociProvider(gateway=CurrentPageGateway()),),
        ).to_dict()

        evidence = next(item for item in response["evidence"] if item["provider"] == "loci")
        self.assertEqual(evidence["authored_state"], "current")
        self.assertIn("current_claim", evidence["roles"])

    def test_mcp_gateway_hydrates_search_results_over_stdio(self):
        server = self.root / "fake_loci_mcp.py"
        server.write_text(
            textwrap.dedent(
                """\
                from mcp.server import MCPServer

                mcp = MCPServer("fake-loci")
                get_calls = 0

                @mcp.tool()
                def loci_search(repo: str, query: str, limit: int = 20, file_paths: list[str] | None = None):
                    if file_paths != ["notes/overview.md"]:
                        raise ValueError(f"unexpected file_paths: {file_paths!r}")
                    return {"symbols": [{
                        "id": "notes/overview.md::Upgrade propagation#section",
                        "file_path": "notes/overview.md",
                        "line": 2,
                        "end_line": 4,
                    }]}

                @mcp.tool()
                def loci_get(repo: str, symbol_ids: list[str], context: int = 0):
                    global get_calls
                    get_calls += 1
                    if get_calls != 1:
                        raise ValueError(f"unexpected loci_get count: {get_calls}")
                    return {"symbols": [{
                        "id": symbol_ids[0],
                        "file_path": "notes/overview.md",
                        "line": 2,
                        "end_line": 4,
                        "source": "## Upgrade propagation\\n\\npropagate_upgrade is implemented by the canonical page workflow.",
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
        self.assertEqual(evidence["locator"]["start_line"], 2)
        self.assertIn("canonical page workflow", evidence["content"])

    def test_missing_mcp_service_degrades_with_explicit_diagnostic(self):
        provider = LociProvider(gateway=LociMcpGateway())

        with patch("llm_wiki_core.providers.loci_transport.shutil.which", return_value=None):
            response = compile_context(
                self.root,
                self.request(),
                extra_providers=(provider,),
            ).to_dict()

        diagnostic = next(item for item in response["diagnostics"] if item["provider"] == "loci")
        self.assertEqual(diagnostic["code"], "LOCI_MCP_UNAVAILABLE")
        self.assertEqual(diagnostic["details"]["transport"], "mcp_stdio")

    def test_missing_mcp_store_identity_degrades_before_launch(self):
        provider = LociProvider(gateway=LociMcpGateway())

        with (
            patch("llm_wiki_core.providers.loci_transport.shutil.which", return_value="/fake/loci-mcp"),
            patch.dict(os.environ, {}, clear=True),
        ):
            response = compile_context(
                self.root,
                self.request(),
                extra_providers=(provider,),
            ).to_dict()

        diagnostic = next(item for item in response["diagnostics"] if item["provider"] == "loci")
        self.assertEqual(diagnostic["code"], "LOCI_MCP_CONFIG_MISSING")
        self.assertEqual(diagnostic["details"]["transport"], "mcp_stdio")
        self.assertEqual(
            diagnostic["details"]["missing"],
            ["LOCI_BASE_DIR", "LOCI_STORE_NAMESPACE"],
        )

    def test_slow_mcp_service_times_out_and_degrades(self):
        server = self.root / "slow_loci_mcp.py"
        server.write_text(
            textwrap.dedent(
                """\
                import time
                from mcp.server import MCPServer

                mcp = MCPServer("slow-loci")

                @mcp.tool()
                def loci_search(repo: str, query: str, limit: int = 20, file_paths: list[str] | None = None):
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

    def test_mcp_client_preserves_structured_operation_failure(self):
        server = self.root / "empty_loci_mcp.py"
        server.write_text(
            textwrap.dedent(
                """\
                from mcp.server import MCPServer

                mcp = MCPServer("empty-loci")

                if __name__ == "__main__":
                    mcp.run(transport="stdio")
                """
            ),
            encoding="utf-8",
        )
        client = LociMcpClient(command=sys.executable, args=(str(server),))

        async def fail_after_connect(_session):
            raise GraphAdapterError(
                "LOCI_GRAPH_ROOT_MISSING",
                "canonical root missing",
                {"missing_count": 1},
            )

        with self.assertRaises(LociGatewayError) as raised:
            client.run(fail_after_connect)

        self.assertEqual(raised.exception.code, "LOCI_GRAPH_ROOT_MISSING")
        self.assertEqual(raised.exception.details["missing_count"], 1)

    def test_mcp_hydration_must_match_the_validated_search_locator(self):
        class MismatchedGateway:
            def retrieve(self, wiki_root, query, *, limit, file_paths):
                return LociRetrieval(
                    results=(
                        {
                            "id": "notes/overview.md::Upgrade propagation#section",
                            "file_path": "notes/overview.md",
                            "line": 2,
                            "end_line": 4,
                            "content": "do not leak mismatched evidence",
                            "hydrated_locator": {
                                "file_path": "other.py",
                                "line": 2,
                                "end_line": 4,
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
            def retrieve(self, wiki_root, query, *, limit, file_paths):
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

    def test_broken_gateway_out_of_scope_results_are_diagnosed_and_rejected(self):
        out_of_scope_paths = (
            "sources/reference.md",
            "_templates/page.md",
            "scripts/helper.py",
            "unknown.md",
        )

        class BrokenGateway:
            def retrieve(self, wiki_root, query, *, limit, file_paths):
                self.file_paths = file_paths
                return LociRetrieval(
                    results=tuple(
                        {
                            "id": f"{file_path}::propagate_upgrade#section",
                            "file_path": file_path,
                            "line": 1,
                            "end_line": 1,
                            "content": "propagate_upgrade must not enter page evidence",
                            "hydrated_locator": {
                                "file_path": file_path,
                                "line": 1,
                                "end_line": 1,
                            },
                        }
                        for file_path in out_of_scope_paths
                    )
                )

        gateway = BrokenGateway()
        response = compile_context(
            self.root,
            self.request(),
            extra_providers=(LociProvider(gateway=gateway),),
        ).to_dict()

        self.assertEqual(gateway.file_paths, ("notes/overview.md",))
        self.assertFalse(any(item["provider"] == "loci" for item in response["evidence"]))
        diagnostics = [
            item
            for item in response["diagnostics"]
            if item["code"] == "LOCI_RESULT_OUT_OF_SCOPE"
        ]
        self.assertEqual(len(diagnostics), len(out_of_scope_paths))
        self.assertEqual(
            {item["details"]["file"] for item in diagnostics},
            set(out_of_scope_paths),
        )
        self.assertTrue(all(set(item["details"]) == {"file"} for item in diagnostics))

    def test_empty_loaded_pages_passes_empty_allowlist(self):
        received = []

        class EmptyGateway:
            def retrieve(self, wiki_root, query, *, limit, file_paths):
                received.append(file_paths)
                return LociRetrieval()

        with patch("llm_wiki_core.compiler.collect_pages", return_value={}):
            response = compile_context(
                self.root,
                self.request(),
                extra_providers=(LociProvider(gateway=EmptyGateway()),),
            ).to_dict()

        self.assertEqual(received, [()])
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
