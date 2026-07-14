from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.wiki_fixture import base_fm, create_wiki_root, write_md


REPO_ROOT = Path(__file__).resolve().parent.parent


class McpServerTest(unittest.TestCase):
    def test_stdio_server_exposes_registry_and_context_tools(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            wiki = create_wiki_root(tmp / "wiki")
            write_md(wiki / "a.md", base_fm(title="A"), "See [B](./b.md)")
            write_md(wiki / "b.md", base_fm(title="B"), "B body.")

            result = asyncio.run(_round_trip(tmp / "home", wiki))

        self.assertIn("wiki_register", result["tools"])
        self.assertIn("wiki_context_pack", result["tools"])
        self.assertIn("wiki_maintenance_candidates", result["tools"])
        self.assertIn("wiki_build_maintenance_candidate", result["tools"])
        self.assertEqual(result["registered"]["alias"], "brain")
        self.assertEqual(result["listed"]["wikis"][0]["alias"], "brain")
        self.assertEqual(result["overview"]["kind"], "agent_overview")
        self.assertEqual(result["manual"]["kind"], "wiki_agent_manual")
        self.assertIn("Wiki Agent", result["manual"]["operating_manual"])
        self.assertEqual(result["links"]["links"][0]["page"], "b.md")
        self.assertEqual(result["maintenance"]["kind"], "maintenance_candidate_packet")
        self.assertFalse(result["maintenance"]["mutation"]["allowed"])
        self.assertEqual(result["proposal"]["target_wiki"], "brain")
        self.assertEqual(result["proposal"]["kind"], "durable_outcome")
        self.assertFalse(result["proposal"]["mutation"]["allowed"])
        self.assertTrue(result["invalid_proposal_is_error"])
        self.assertEqual(result["invalid_proposal_error"]["code"], "INVALID_INPUT")

    def test_stdio_server_returns_structured_error_without_home(self):
        result = asyncio.run(_missing_home_error())

        self.assertTrue(result["is_error"])
        self.assertEqual(
            result["error"],
            {
                "code": "CONFIG_REQUIRED",
                "message": "LLM_WIKI_HOME must be set to the current agent's llm-wiki home",
                "details": {"env": "LLM_WIKI_HOME"},
            },
        )


async def _round_trip(home: Path, wiki: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["LLM_WIKI_HOME"] = str(home)
    env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}:{env.get('PYTHONPATH', '')}"
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "llm_wiki_mcp.mcp_server"],
        env=env,
        cwd=REPO_ROOT,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = sorted(tool.name for tool in tools.tools)

            registered = await session.call_tool(
                "wiki_register",
                arguments={"alias": "brain", "path": str(wiki), "created_by": "wikime"},
            )
            listed = await session.call_tool("wiki_list", arguments={})
            overview = await session.call_tool("wiki_overview", arguments={"alias": "brain"})
            manual = await session.call_tool("wiki_agent_manual", arguments={"alias": "brain"})
            links = await session.call_tool(
                "wiki_links",
                arguments={"alias": "brain", "page": "a.md"},
            )
            maintenance = await session.call_tool(
                "wiki_maintenance_candidates",
                arguments={"alias": "brain", "stale_after_days": 180},
            )
            proposal = await session.call_tool(
                "wiki_build_maintenance_candidate",
                arguments={
                    "alias": "brain",
                    "kind": "durable_outcome",
                    "diagnostic": "A verified outcome is not represented.",
                    "review_question": "Should this become durable Brain knowledge?",
                    "pages": ["a.md"],
                    "evidence": [{"ref": "test:result", "content_hash": "abc123"}],
                },
            )
            invalid_proposal = await session.call_tool(
                "wiki_build_maintenance_candidate",
                arguments={
                    "alias": "brain",
                    "kind": "relationship_gap",
                    "diagnostic": "A route may be missing.",
                    "review_question": "Should these pages be connected?",
                    "pages": ["a.md"],
                    "evidence": [{"ref": "test:result"}],
                },
            )

    return {
        "tools": tool_names,
        "registered": registered.structuredContent,
        "listed": listed.structuredContent,
        "overview": overview.structuredContent,
        "manual": manual.structuredContent,
        "links": links.structuredContent,
        "maintenance": maintenance.structuredContent,
        "proposal": proposal.structuredContent,
        "invalid_proposal_is_error": invalid_proposal.isError,
        "invalid_proposal_error": invalid_proposal.structuredContent["error"],
    }


async def _missing_home_error() -> dict[str, Any]:
    env = os.environ.copy()
    env.pop("LLM_WIKI_HOME", None)
    env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}:{env.get('PYTHONPATH', '')}"
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "llm_wiki_mcp.mcp_server"],
        env=env,
        cwd=REPO_ROOT,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("wiki_list", arguments={})
            return {
                "is_error": result.isError,
                "error": result.structuredContent["error"],
            }

    raise AssertionError("Expected wiki_list to return an error")
