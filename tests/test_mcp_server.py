from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from llm_wiki_mcp.mcp_server import create_server
from tests.wiki_fixture import base_fm, create_wiki_root, write_md


REPO_ROOT = Path(__file__).resolve().parent.parent
SUCCESS_MARKER = "OK: structured result available; inspect structuredContent."
REGISTRY_NAVIGATION_TOOLS = {
    "wiki_list",
    "wiki_register",
    "wiki_unregister",
    "wiki_doctor",
    "wiki_agent_manual",
    "wiki_overview",
    "wiki_query",
    "wiki_links",
    "wiki_backlinks",
    "wiki_around",
    "wiki_get_page",
    "wiki_get_source_excerpt",
    "wiki_graph_health",
}
TOOL_OUTPUT_MODELS = {
    "wiki_list": "WikiListOutput",
    "wiki_register": "WikiRegisterOutput",
    "wiki_unregister": "WikiUnregisterOutput",
    "wiki_doctor": "WikiDoctorOutput",
    "wiki_agent_manual": "WikiAgentManualOutput",
    "wiki_overview": "WikiOverviewOutput",
    "wiki_query": "WikiQueryOutput",
    "wiki_links": "WikiLinksOutput",
    "wiki_backlinks": "WikiBacklinksOutput",
    "wiki_around": "WikiAroundOutput",
    "wiki_context_pack": "WikiContextPackOutput",
    "wiki_compile_context": "WikiCompileContextOutput",
    "wiki_maintenance_candidates": "WikiMaintenanceCandidatesOutput",
    "wiki_build_maintenance_candidate": "WikiBuildMaintenanceCandidateOutput",
    "wiki_build_temporal_candidates": "WikiBuildTemporalCandidatesOutput",
    "wiki_build_maintenance": "WikiBuildMaintenanceOutput",
    "wiki_reconcile_temporal_candidates": "WikiReconcileTemporalCandidatesOutput",
    "wiki_get_page": "WikiGetPageOutput",
    "wiki_get_source_excerpt": "WikiGetSourceExcerptOutput",
    "wiki_graph_health": "WikiGraphHealthOutput",
}


class McpServerTest(unittest.TestCase):
    def test_stdio_server_exposes_registry_and_context_tools(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            wiki = create_wiki_root(tmp / "wiki")
            sources = wiki / "sources"
            sources.mkdir()
            (sources / "a.txt").write_text("Source A.", encoding="utf-8")
            write_md(
                wiki / "a.md",
                base_fm(title="A", source="a.txt"),
                "See [B](./b.md)",
            )
            write_md(wiki / "b.md", base_fm(title="B"), "B body.")

            result = asyncio.run(_round_trip(tmp / "home", wiki))

        self.assertIn("wiki_register", result["tools"])
        self.assertIn("wiki_context_pack", result["tools"])
        self.assertIn("wiki_maintenance_candidates", result["tools"])
        self.assertIn("wiki_build_maintenance_candidate", result["tools"])
        self.assertEqual(set(result["tools"]), set(TOOL_OUTPUT_MODELS))
        self.assertIsNotNone(result["wiki_list_output_schema"])
        self.assertEqual(result["wiki_list_output_schema"]["type"], "object")
        self.assertIn("wikis", result["wiki_list_output_schema"]["properties"])
        self.assertIn("error", result["wiki_list_output_schema"]["properties"])
        self.assertEqual(
            set(result["schemas"]) & REGISTRY_NAVIGATION_TOOLS,
            REGISTRY_NAVIGATION_TOOLS,
        )
        for name in REGISTRY_NAVIGATION_TOOLS:
            with self.subTest(tool=name):
                self.assertIsNotNone(result["schemas"][name])
                self.assertEqual(result["schemas"][name]["type"], "object")
                self.assertIn("error", result["schemas"][name]["properties"])
        for name, output_model in TOOL_OUTPUT_MODELS.items():
            with self.subTest(exhaustive_schema=name):
                schema = result["schemas"][name]
                self.assertIsNotNone(schema)
                self.assertEqual(schema["type"], "object")
                self.assertEqual(schema["title"], output_model)
                self.assertFalse(schema["additionalProperties"])
                self.assertIn("error", schema["properties"])
                self.assertTrue(set(schema["properties"]) - {"error"})
        self.assertEqual(result["registered"]["alias"], "brain")
        self.assertEqual(result["listed"]["wikis"][0]["alias"], "brain")
        self.assertNotIn("error", result["listed"])
        self.assertEqual(result["overview"]["kind"], "agent_overview")
        self.assertEqual(result["manual"]["kind"], "wiki_agent_manual")
        self.assertIn("Wiki Agent", result["manual"]["operating_manual"])
        self.assertIn("B body.", result["page"]["content"])
        self.assertTrue(result["context"])
        self.assertEqual(
            result["success_text"],
            {
                "listed": [SUCCESS_MARKER],
                "manual": [SUCCESS_MARKER],
                "page": [SUCCESS_MARKER],
                "context": [SUCCESS_MARKER],
            },
        )
        self.assertLessEqual(len(SUCCESS_MARKER), 80)
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
        self.assertIn("CONFIG_REQUIRED", result["error_text"])
        self.assertIn("LLM_WIKI_HOME must be set", result["error_text"])
        self.assertNotIn("registry_home", result["structured_content"])
        self.assertNotIn("wikis", result["structured_content"])

    def test_wiki_list_rejects_invalid_structured_output(self):
        server = create_server()
        tool = server._tool_manager.get_tool("wiki_list")

        with self.assertRaises(ValidationError):
            tool.fn_metadata.convert_result(
                CallToolResult(
                    content=[TextContent(type="text", text=SUCCESS_MARKER)],
                    structured_content={"registry_home": "/tmp/missing-wikis"},
                )
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

    async with Client(stdio_client(server_params), mode="auto") as client:
        tools = await client.list_tools()
        tool_names = sorted(tool.name for tool in tools.tools)
        schemas = {tool.name: tool.output_schema for tool in tools.tools}
        wiki_list_output_schema = next(
            tool.output_schema for tool in tools.tools if tool.name == "wiki_list"
        )

        registered = await client.call_tool(
            "wiki_register",
            arguments={"alias": "brain", "path": str(wiki), "created_by": "wikime"},
        )
        listed = await client.call_tool("wiki_list", arguments={})
        overview = await client.call_tool("wiki_overview", arguments={"alias": "brain"})
        manual = await client.call_tool("wiki_agent_manual", arguments={"alias": "brain"})
        page = await client.call_tool(
            "wiki_get_page",
            arguments={"alias": "brain", "page": "b.md"},
        )
        context = await client.call_tool(
            "wiki_context_pack",
            arguments={"alias": "brain", "page": "a.md", "tokens": 1_000},
        )
        links = await client.call_tool(
            "wiki_links",
            arguments={"alias": "brain", "page": "a.md"},
        )
        doctor = await client.call_tool("wiki_doctor", arguments={"alias": "brain"})
        query = await client.call_tool("wiki_query", arguments={"alias": "brain"})
        risks = await client.call_tool(
            "wiki_query", arguments={"alias": "brain", "risks": True}
        )
        backlinks = await client.call_tool(
            "wiki_backlinks", arguments={"alias": "brain", "page": "b.md"}
        )
        around = await client.call_tool(
            "wiki_around", arguments={"alias": "brain", "page": "a.md"}
        )
        source_excerpt = await client.call_tool(
            "wiki_get_source_excerpt",
            arguments={"alias": "brain", "page": "a.md"},
        )
        graph_health = await client.call_tool(
            "wiki_graph_health", arguments={"alias": "brain"}
        )
        maintenance = await client.call_tool(
            "wiki_maintenance_candidates",
            arguments={"alias": "brain", "stale_after_days": 180},
        )
        proposal = await client.call_tool(
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
        invalid_proposal = await client.call_tool(
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
        unregistered = await client.call_tool(
            "wiki_unregister", arguments={"alias": "brain"}
        )

    return {
        "tools": tool_names,
        "schemas": schemas,
        "wiki_list_output_schema": wiki_list_output_schema,
        "registered": registered.structured_content,
        "listed": listed.structured_content,
        "overview": overview.structured_content,
        "manual": manual.structured_content,
        "page": page.structured_content,
        "context": context.structured_content,
        "success_text": {
            "listed": [block.text for block in listed.content],
            "manual": [block.text for block in manual.content],
            "page": [block.text for block in page.content],
            "context": [block.text for block in context.content],
        },
        "links": links.structured_content,
        "registry_navigation_results": [
            registered.structured_content,
            doctor.structured_content,
            query.structured_content,
            risks.structured_content,
            backlinks.structured_content,
            around.structured_content,
            source_excerpt.structured_content,
            graph_health.structured_content,
            unregistered.structured_content,
        ],
        "maintenance": maintenance.structured_content,
        "proposal": proposal.structured_content,
        "invalid_proposal_is_error": invalid_proposal.is_error,
        "invalid_proposal_error": invalid_proposal.structured_content["error"],
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

    async with Client(stdio_client(server_params), mode="auto") as client:
        tools = await client.list_tools()
        wiki_list_output_schema = next(
            tool.output_schema for tool in tools.tools if tool.name == "wiki_list"
        )
        result = await client.call_tool("wiki_list", arguments={})
        return {
            "is_error": result.is_error,
            "error": result.structured_content["error"],
            "error_text": "\n".join(block.text for block in result.content),
            "structured_content": result.structured_content,
            "output_schema": wiki_list_output_schema,
        }

    raise AssertionError("Expected wiki_list to return an error")
