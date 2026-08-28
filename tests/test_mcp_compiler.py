from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.wiki_fixture import base_fm, create_wiki_root, write_md


REPO_ROOT = Path(__file__).resolve().parent.parent


class McpCompilerTest(unittest.TestCase):
    def test_stdio_server_exposes_and_runs_question_shaped_compiler(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            wiki = create_wiki_root(tmp / "wiki")
            (wiki / "sources").mkdir()
            (wiki / "sources" / "runtime.md").write_text(
                "llm-wiki owns traversal.", encoding="utf-8"
            )
            write_md(
                wiki / "systems" / "runtime.md",
                base_fm(
                    title="Runtime",
                    source="sources/runtime.md",
                    knowledge_state="current",
                ),
                "Runtime summary.",
            )

            result = asyncio.run(_compile_round_trip(tmp / "home", wiki))

        self.assertIn("wiki_compile_context", result["tools"])
        _assert_object_output_schema(
            self,
            result["schemas"]["wiki_context_pack"],
            {
                "kind",
                "seed",
                "budget",
                "included_pages",
                "source_refs",
                "source_excerpts",
                "open_questions",
                "open_risks",
                "recent_log",
                "gaps",
            },
        )
        _assert_object_output_schema(
            self,
            result["schemas"]["wiki_compile_context"],
            {
                "kind",
                "contract_version",
                "wiki",
                "query",
                "evidence",
                "omissions",
                "coverage",
                "budget",
                "stop",
                "continuation",
                "diagnostics",
                "reporting",
            },
        )
        self.assertEqual(result["context"]["kind"], "context_pack")
        self.assertEqual(result["compiled"]["kind"], "compiled_context")
        self.assertEqual(result["compiled"]["query"]["question"], "What does Runtime own?")
        self.assertTrue(result["compiled"]["stop"]["sufficient"])
        serialized = json.dumps(
            result["compiled"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertLessEqual(len(serialized), 10_000)
        self.assertIn("reporting", result["compiled"])

    def test_invalid_compiler_input_keeps_structured_error_envelope(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            wiki = create_wiki_root(tmp / "wiki")

            result = asyncio.run(_invalid_round_trip(tmp / "home", wiki))

        _assert_error_envelope(
            self,
            result,
            {
                "code": "CONTRACT_VERSION_UNSUPPORTED",
                "message": "Compiler contract version is not supported",
                "details": {"found": "4", "supported": ["1", "2", "3"]},
            },
        )

    def test_stdio_v3_workspace_identity_round_trip_is_path_free(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            wiki = create_wiki_root(tmp / "wiki")
            write_md(
                wiki / "projects" / "alpha.md",
                base_fm(
                    title="Alpha",
                    type="project",
                    identity={
                        "project_id": "alpha",
                        "aliases": ["Alpha"],
                        "remotes": ["github.com/acme/alpha"],
                    },
                ),
                "Alpha orientation.",
            )
            result = asyncio.run(_v3_round_trip(tmp / "home", wiki))

        self.assertEqual(result["contract_version"], "3")
        self.assertEqual(
            result["query"]["project_resolution"],
            {
                "status": "matched",
                "project_id": "alpha",
                "page": "projects/alpha.md",
                "matched_by": "remote",
            },
        )
        self.assertTrue(any(item["provider"] == "project" for item in result["evidence"]))
        self.assertNotIn(str(wiki), json.dumps(result))

    def test_impossible_complete_response_budget_keeps_structured_error_envelope(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            wiki = create_wiki_root(tmp / "wiki")

            result = asyncio.run(_too_small_budget_round_trip(tmp / "home", wiki))

        _assert_error_envelope(
            self,
            result,
            {
                "code": "BUDGET_TOO_SMALL",
                "message": "Budget is too small for the complete response contract",
                "details": {
                    "provided_max_bytes": 100,
                    "provided_max_estimated_tokens": None,
                    "effective_max_bytes": 100,
                    "minimum_response_bytes": 853,
                    "minimum_estimated_tokens": 214,
                },
            },
        )


async def _session(home: Path):
    env = os.environ.copy()
    env["LLM_WIKI_HOME"] = str(home)
    env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}:{env.get('PYTHONPATH', '')}"
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "llm_wiki_mcp.mcp_server"],
        env=env,
        cwd=REPO_ROOT,
    )


async def _compile_round_trip(home: Path, wiki: Path):
    params = await _session(home)
    async with Client(stdio_client(params), mode="auto") as client:
        tools = await client.list_tools()
        schemas = {tool.name: tool.output_schema for tool in tools.tools}
        await client.call_tool("wiki_register", {"alias": "test", "path": str(wiki)})
        context = await client.call_tool(
            "wiki_context_pack",
            {"alias": "test", "page": "systems/runtime.md", "tokens": 500},
        )
        compiled = await client.call_tool(
            "wiki_compile_context",
            {
                "alias": "test",
                "question": "What does Runtime own?",
                "target_bytes": 100,
                "max_bytes": 10_000,
            },
        )
        return {
            "tools": sorted(tool.name for tool in tools.tools),
            "schemas": schemas,
            "context": context.structured_content,
            "compiled": compiled.structured_content,
        }


async def _invalid_round_trip(home: Path, wiki: Path):
    params = await _session(home)
    async with Client(stdio_client(params), mode="auto") as client:
        await client.call_tool("wiki_register", {"alias": "test", "path": str(wiki)})
        result = await client.call_tool(
            "wiki_compile_context",
            {"alias": "test", "question": "What changed?", "contract_version": "4"},
        )
        return {
            "is_error": result.is_error,
            "error": result.structured_content["error"],
            "structured_content": result.structured_content,
            "error_text": result.content[0].text,
        }


async def _v3_round_trip(home: Path, wiki: Path):
    params = await _session(home)
    async with Client(stdio_client(params), mode="auto") as client:
        await client.call_tool("wiki_register", {"alias": "test", "path": str(wiki)})
        result = await client.call_tool(
            "wiki_compile_context",
            {
                "alias": "test",
                "question": "What should I work on next?",
                "contract_version": "3",
                "workspace_identity": {
                    "directory_alias": "renamed-checkout",
                    "remotes": ["github.com/acme/alpha", "github.com/acme/alpha"],
                },
            },
        )
        return result.structured_content


async def _too_small_budget_round_trip(home: Path, wiki: Path):
    params = await _session(home)
    async with Client(stdio_client(params), mode="auto") as client:
        await client.call_tool("wiki_register", {"alias": "test", "path": str(wiki)})
        result = await client.call_tool(
            "wiki_compile_context",
            {
                "alias": "test",
                "question": "What changed?",
                "target_bytes": 100,
                "max_bytes": 100,
            },
        )
        return {
            "is_error": result.is_error,
            "error": result.structured_content["error"],
            "structured_content": result.structured_content,
            "error_text": result.content[0].text,
        }


def _assert_object_output_schema(
    test: unittest.TestCase,
    schema: dict[str, Any] | None,
    success_fields: set[str],
) -> None:
    test.assertIsNotNone(schema)
    assert schema is not None
    test.assertEqual(schema["type"], "object")
    properties = schema["properties"]
    test.assertIn("error", properties)
    for field in success_fields:
        with test.subTest(field=field):
            test.assertIn(field, properties)


def _assert_error_envelope(
    test: unittest.TestCase,
    result: dict[str, Any],
    expected_error: dict[str, Any],
) -> None:
    test.assertTrue(result["is_error"])
    test.assertEqual(result["error"], expected_error)
    test.assertEqual(result["structured_content"], {"error": expected_error})
    test.assertEqual(result["error_text"], f"{expected_error['code']}: {expected_error['message']}")


if __name__ == "__main__":
    unittest.main()
