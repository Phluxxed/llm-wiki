from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.wiki_fixture import base_fm, create_wiki_root, write_md

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_mcp.errors import WikiMcpError
from llm_wiki_core.maintenance import build_candidate_proposal
from llm_wiki_mcp.wiki_runtime import wiki_build_maintenance


def _source(payload_text: str = "The service is ready.", **changes: Any) -> dict[str, Any]:
    value = {
        "source_kind": "source:manual",
        "source_ref": "sources/status.md",
        "content_hash": hashlib.sha256(payload_text.encode("utf-8")).hexdigest(),
        "locator": {"line": 10, "section": "status"},
        "input_type": "input:markdown",
        "observed_at": "2026-08-10T00:00:00Z",
        "source_event_time": {"kind": "unknown", "reason": "not stated"},
        "retention": "immutable_source",
        "payload_text": payload_text,
    }
    value.update(changes)
    return value


def _claim(**changes: Any) -> dict[str, Any]:
    value = {
        "subject": {"kind": "resolved_page", "page": "a.md"},
        "predicate": "status:has_state",
        "object": {"kind": "literal", "datatype": "type:text", "value": "ready"},
        "claim_scope": "default",
        "proposed_world_validity": {
            "from": {"kind": "known", "value": "2026-01-01"},
            "to": {"kind": "open"},
        },
        "signals": [{"kind": "signal:direct", "detail": "explicit statement"}],
        "unknowns": [],
    }
    value.update(changes)
    return value


class UnifiedMaintenanceRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        self.wiki = create_wiki_root(self.tmp / "wiki")
        write_md(self.wiki / "a.md", base_fm(title="A"), "A body.")
        write_md(self.wiki / "b.md", base_fm(title="B"), "B body.")
        self.home = self.tmp / "home"
        os.environ["LLM_WIKI_HOME"] = str(self.home)
        from llm_wiki_mcp.registry import register_wiki

        register_wiki("brain", str(self.wiki), created_by="test")

    def tearDown(self) -> None:
        os.environ.pop("LLM_WIKI_HOME", None)
        self.tmpdir.cleanup()

    def test_deterministic_discovery_is_adapted_without_claim_inference(self):
        result = wiki_build_maintenance(
            "brain",
            source={"source_kind": "scan", "source_ref": "scan:1", "content_hash": "a" * 64},
            intent="detected_gap",
            proposed_at="2026-08-10T01:00:00Z",
        )
        self.assertEqual(result["classification"]["change_class"], "no_change")
        self.assertEqual(result["reconciliation"]["kind"], "maintenance_candidate_packet")
        self.assertEqual(result["candidates"], [])

    def test_discovery_candidates_become_exact_legacy_task_proposals(self):
        write_md(
            self.wiki / "stale.md",
            base_fm(title="Stale", knowledge_state="current", last_reviewed="2025-01-01"),
            "A claim that needs review.",
        )
        result = wiki_build_maintenance(
            "brain",
            source={"source_kind": "scan", "source_ref": "scan:stale", "content_hash": "a" * 64},
            intent="detected_gap",
            proposed_at="2026-08-10T01:00:00Z",
        )
        packet = result["reconciliation"]
        discovered = [item for item in packet["candidates"] if item["kind"] == "stale_current_claim"]
        self.assertEqual(len(discovered), 1)
        candidate = discovered[0]
        expected = build_candidate_proposal(
            alias="brain",
            kind=candidate["kind"],
            diagnostic=candidate["diagnostic"],
            review_question=candidate["review_question"],
            pages=[candidate["page"]],
            evidence=[{"ref": candidate["page"], "note": candidate["evidence"][0]["content"]}],
        )
        self.assertEqual(result["candidates"], [expected])
        self.assertEqual(result["classification"]["change_class"], "knowledge_revision")
        self.assertEqual(result["classification"]["temporal_obligation"], "required")
        self.assertEqual(result["reconciliation"], packet)
        self.assertNotEqual(result["candidates"][0]["contract_version"], "temporal-candidate/1")

    def test_temporal_only_embeds_exact_observation_candidate_and_reconciliation(self):
        result = wiki_build_maintenance(
            "brain",
            source=_source(),
            intent="durable_learning",
            claims=[_claim()],
            proposed_at="2026-08-10T01:00:00Z",
        )
        self.assertEqual(result["classification"]["change_class"], "knowledge_revision")
        self.assertEqual(result["observations"][0]["contract_version"], "temporal-observation/1")
        self.assertEqual(result["candidates"][0]["contract_version"], "temporal-candidate/1")
        self.assertEqual(result["reconciliation"]["contract_version"], "temporal-reconciliation/1")
        self.assertFalse(result["mutation"]["allowed"])

    def test_mixed_temporal_and_extra_evidence_remains_knowledge_revision(self):
        result = wiki_build_maintenance(
            "brain",
            source=_source(),
            intent="correction",
            claims=[_claim()],
            evidence=[{"ref": "trace:1", "kind": "review", "note": "corroborating"}],
            pages=["a.md"],
            proposed_at="2026-08-10T01:00:00Z",
        )
        self.assertEqual(result["classification"]["change_class"], "knowledge_revision")
        self.assertEqual({item.get("ref") for item in result["observations"] if "ref" in item}, {"trace:1"})

    def test_hygiene_and_empty_requests_use_frozen_paths(self):
        hygiene = wiki_build_maintenance(
            "brain",
            source={
                "source_kind": "wiki_doctor",
                "source_ref": "doctor:links",
                "content_hash": "a" * 64,
            },
            intent="wiki_hygiene",
            evidence=[{"ref": "doctor:links", "kind": "link_repair"}],
            proposed_at="2026-08-10T01:00:00Z",
        )
        self.assertEqual(hygiene["classification"]["change_class"], "wiki_hygiene")
        empty = wiki_build_maintenance(
            "brain",
            source={"source_kind": "scan", "source_ref": "scan:1", "content_hash": "b" * 64},
            intent="durable_learning",
            proposed_at="2026-08-10T01:00:00Z",
        )
        self.assertEqual(empty["classification"]["change_class"], "no_change")

    def test_invalid_input_is_named_for_unified_surface_and_hash_is_verified(self):
        with self.assertRaises(WikiMcpError) as error:
            wiki_build_maintenance(
                "brain",
                source=_source(content_hash="0" * 64),
                intent="durable_learning",
                claims=[_claim()],
                proposed_at="2026-08-10T01:00:00Z",
            )
        self.assertEqual(error.exception.code, "INVALID_INPUT")
        self.assertEqual(error.exception.details["surface"], "wiki_build_maintenance")


class UnifiedMaintenanceMcpTest(unittest.TestCase):
    def test_stdio_registers_and_round_trips_unified_builder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            wiki = create_wiki_root(tmp / "wiki")
            write_md(wiki / "a.md", base_fm(title="A"), "A body.")
            result = asyncio.run(_round_trip(tmp / "home", wiki))
        self.assertIn("wiki_build_maintenance", result["tools"])
        self.assertFalse(result["result"].isError)
        self.assertEqual(result["result"].structuredContent["schema_version"], "unified-maintenance/1")
        self.assertFalse(result["result"].structuredContent["mutation"]["allowed"])


async def _round_trip(home: Path, wiki: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["LLM_WIKI_HOME"] = str(home)
    env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}:{env.get('PYTHONPATH', '')}"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "llm_wiki_mcp.mcp_server"],
        env=env,
        cwd=REPO_ROOT,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            await session.call_tool("wiki_register", arguments={"alias": "brain", "path": str(wiki)})
            result = await session.call_tool(
                "wiki_build_maintenance",
                arguments={
                    "alias": "brain",
                    "source": {
                        "source_kind": "scan",
                        "source_ref": "scan:1",
                        "content_hash": "b" * 64,
                    },
                    "intent": "durable_learning",
                    "proposed_at": "2026-08-10T01:00:00Z",
                },
            )
            return {"tools": [tool.name for tool in tools.tools], "result": result}


if __name__ == "__main__":
    unittest.main()
