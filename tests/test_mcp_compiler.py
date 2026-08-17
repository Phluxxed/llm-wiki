from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
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

        self.assertTrue(result["is_error"])
        self.assertEqual(result["error"]["code"], "CONTRACT_VERSION_UNSUPPORTED")
        self.assertEqual(
            result["error"]["details"],
            {"found": "3", "supported": ["1", "2"]},
        )

    def test_impossible_complete_response_budget_keeps_structured_error_envelope(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            wiki = create_wiki_root(tmp / "wiki")

            result = asyncio.run(_too_small_budget_round_trip(tmp / "home", wiki))

        self.assertTrue(result["is_error"])
        self.assertEqual(result["error"]["code"], "BUDGET_TOO_SMALL")
        self.assertGreater(result["error"]["details"]["minimum_response_bytes"], 100)


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
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            await session.call_tool("wiki_register", {"alias": "test", "path": str(wiki)})
            compiled = await session.call_tool(
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
                "compiled": compiled.structuredContent,
            }


async def _invalid_round_trip(home: Path, wiki: Path):
    params = await _session(home)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("wiki_register", {"alias": "test", "path": str(wiki)})
            result = await session.call_tool(
                "wiki_compile_context",
                {"alias": "test", "question": "What changed?", "contract_version": "3"},
            )
            return {"is_error": result.isError, "error": result.structuredContent["error"]}


async def _too_small_budget_round_trip(home: Path, wiki: Path):
    params = await _session(home)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("wiki_register", {"alias": "test", "path": str(wiki)})
            result = await session.call_tool(
                "wiki_compile_context",
                {
                    "alias": "test",
                    "question": "What changed?",
                    "target_bytes": 100,
                    "max_bytes": 100,
                },
            )
            return {"is_error": result.isError, "error": result.structuredContent["error"]}


if __name__ == "__main__":
    unittest.main()
