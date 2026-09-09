from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server import MCPServer

from tests.wiki_fixture import base_fm, create_wiki_root, write_md

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_mcp.errors import WikiMcpError
from llm_wiki_mcp.mcp_server import create_server
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

    def test_claim_source_reports_original_contract_failures(self):
        cases = (
            (
                {"source_kind": "source:manual", "source_ref": "sources/status.md", "content_hash": "a" * 64},
                "source has invalid fields (missing",
            ),
            (
                _source(content_hash="sha256:" + "a" * 64),
                "source.content_hash must be lowercase SHA-256",
            ),
            (
                _source(content_hash="0" * 64),
                "source.content_hash does not match source.payload_text",
            ),
            (_source(source_event_time="2026-08-10T00:00:00Z"), "time must be an object"),
        )
        for source, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(WikiMcpError) as error:
                wiki_build_maintenance(
                    "brain",
                    source=source,
                    intent="durable_learning",
                    claims=[_claim()],
                    proposed_at="2026-08-10T01:00:00Z",
                )
            self.assertEqual(error.exception.code, "INVALID_INPUT")
            self.assertIn(expected, error.exception.message)


class UnifiedMaintenanceMcpTest(unittest.TestCase):
    def test_published_schema_only_changes_unified_maintenance(self):
        server = create_server()
        raw_tools = asyncio.run(MCPServer.list_tools(server))
        published_tools = asyncio.run(server.list_tools())
        raw_schemas = {tool.name: tool.input_schema for tool in raw_tools}
        published_schemas = {tool.name: tool.input_schema for tool in published_tools}

        for name, schema in raw_schemas.items():
            if name != "wiki_build_maintenance":
                with self.subTest(name=name):
                    self.assertEqual(published_schemas[name], schema)

    def test_stdio_registers_and_round_trips_unified_builder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            wiki = create_wiki_root(tmp / "wiki")
            write_md(wiki / "a.md", base_fm(title="A"), "A body.")
            result = asyncio.run(_round_trip(tmp / "home", wiki))
        self.assertIn("wiki_build_maintenance", result["tools"])
        schema = result["schema"]
        input_schema = result["input_schema"]
        self.assertIsNotNone(schema)
        assert schema is not None
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["title"], "WikiBuildMaintenanceOutput")
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("error", schema["properties"])
        self.assertEqual(input_schema["type"], "object")
        self.assertEqual(
            input_schema["properties"]["proposed_at"]["pattern"],
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$",
        )
        source_schema = input_schema["properties"]["source"]
        self.assertEqual(len(source_schema["anyOf"]), 2)
        definitions = input_schema["$defs"]
        for source_variant in source_schema["anyOf"]:
            self.assertNotIn("$ref", source_variant)
            self.assertEqual(source_variant["type"], "object")
            self.assertIn("source_kind", source_variant["properties"])
        temporal_source_schema = source_schema["anyOf"][1]
        source_event_time = temporal_source_schema["properties"]["source_event_time"]
        for variant in source_event_time["oneOf"]:
            self.assertNotIn("$ref", variant)
            self.assertEqual(variant["type"], "object")
            self.assertIn("kind", variant["properties"])
        claim_schema = input_schema["properties"]["claims"]["anyOf"][0]["items"]
        self.assertNotIn("$ref", claim_schema)
        self.assertEqual(claim_schema["type"], "object")
        self.assertIn("subject", claim_schema["properties"])
        self.assertEqual(
            claim_schema["properties"]["proposed_world_validity"]["type"], "object"
        )
        for entity_variant in claim_schema["properties"]["subject"]["oneOf"]:
            self.assertNotIn("$ref", entity_variant)
            self.assertEqual(entity_variant["type"], "object")
            self.assertIn("kind", entity_variant["properties"])
        evidence_schema = input_schema["properties"]["evidence"]["anyOf"][0]["items"]
        self.assertNotIn("$ref", evidence_schema)
        self.assertEqual(evidence_schema["type"], "object")
        self.assertIn("ref", evidence_schema["properties"])
        temporal_source = definitions["TemporalMaintenanceSourceInput"]
        self.assertEqual(
            temporal_source["properties"]["content_hash"]["description"],
            "Optional SHA-256 digest of the exact UTF-8 bytes of `payload_text`. "
            "If omitted, the MCP boundary derives it from those bytes; if supplied, it is verified.",
        )
        self.assertIn("source_event_time", temporal_source["required"])
        claim_definition = definitions["MaintenanceClaimInput"]
        self.assertEqual(
            set(claim_definition["required"]),
            {
                "subject",
                "predicate",
                "object",
                "claim_scope",
                "proposed_world_validity",
                "signals",
                "unknowns",
            },
        )
        for field in {
            "schema_version",
            "proposal_id",
            "target_wiki",
            "source",
            "classification",
            "observations",
            "candidates",
            "reconciliation",
            "affected_pages",
            "unknowns",
            "disposition",
            "mutation",
            "authority",
        }:
            with self.subTest(field=field):
                self.assertIn(field, schema["properties"])
        self.assertFalse(result["result"].is_error)
        self.assertEqual(
            result["result"].structured_content["schema_version"], "unified-maintenance/1"
        )
        self.assertEqual(
            result["result"].structured_content["classification"]["change_class"],
            "knowledge_revision",
        )
        self.assertFalse(result["result"].structured_content["mutation"]["allowed"])
        self.assertFalse(result["derived_hash"].is_error)
        self.assertEqual(
            result["derived_hash"].structured_content["source"]["content_hash"],
            hashlib.sha256(b"The service is ready.").hexdigest(),
        )
        self.assertEqual(result["result"].content[0].text, "OK: structured result available; inspect structuredContent.")
        for name in ("correction", "work_history", "detected_gap", "wiki_hygiene"):
            with self.subTest(intent=name):
                branch = result[name]
                self.assertFalse(branch.is_error)
                self.assertEqual(branch.structured_content["schema_version"], "unified-maintenance/1")
                self.assertFalse(branch.structured_content["mutation"]["allowed"])
        self.assertEqual(result["correction"].structured_content["classification"]["change_class"], "knowledge_revision")
        self.assertEqual(result["work_history"].structured_content["classification"]["change_class"], "no_change")
        self.assertEqual(result["detected_gap"].structured_content["classification"]["change_class"], "no_change")
        self.assertEqual(result["wiki_hygiene"].structured_content["classification"]["change_class"], "wiki_hygiene")
        self.assertTrue(result["invalid"].is_error)
        self.assertTrue(result["invalid_input"].is_error)
        expected_error = {
            "code": "INVALID_INPUT",
            "message": "unified maintenance: intent is unsupported",
            "details": {"surface": "wiki_build_maintenance"},
        }
        self.assertEqual(result["invalid"].structured_content, {"error": expected_error})
        self.assertEqual(result["invalid"].content[0].text, f"INVALID_INPUT: {expected_error['message']}")


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
    async with Client(stdio_client(params), mode="auto") as client:
        tools = await client.list_tools()
        schema = next(
            tool.output_schema
            for tool in tools.tools
            if tool.name == "wiki_build_maintenance"
        )
        input_schema = next(
            tool.input_schema
            for tool in tools.tools
            if tool.name == "wiki_build_maintenance"
        )
        await client.call_tool("wiki_register", arguments={"alias": "brain", "path": str(wiki)})
        result = await client.call_tool(
            "wiki_build_maintenance",
            arguments={
                "alias": "brain",
                "source": _source(),
                "intent": "durable_learning",
                "claims": [_claim()],
                "proposed_at": "2026-08-10T01:00:00Z",
            },
        )
        # Exercise the published example, not a separately maintained facsimile:
        # reference/depth-limited hosts still expose the tool description.
        description = next(tool.description for tool in tools.tools if tool.name == "wiki_build_maintenance")
        assert description is not None
        example = json.loads(description.split("```json", 1)[1].split("```", 1)[0])
        assert example["source"] == {key: value for key, value in _source().items() if key != "content_hash"}
        assert example["claims"] == [_claim()]
        for field in ("proposed_world_validity", "source_event_time", "signals", "unknowns", "content_hash"):
            assert field in description
        derived_hash = await client.call_tool("wiki_build_maintenance", arguments=example)
        correction = await client.call_tool(
            "wiki_build_maintenance",
            arguments={
                "alias": "brain",
                "source": _source(payload_text="A correction is ready."),
                "intent": "correction",
                "claims": [_claim()],
                "proposed_at": "2026-08-10T01:00:00Z",
            },
        )
        scan_source = {
            "source_kind": "scan",
            "source_ref": "scan:1",
            "content_hash": "b" * 64,
        }
        work_history = await client.call_tool(
            "wiki_build_maintenance",
            arguments={
                "alias": "brain",
                "source": scan_source,
                "intent": "work_history",
                "proposed_at": "2026-08-10T01:00:00Z",
            },
        )
        detected_gap = await client.call_tool(
            "wiki_build_maintenance",
            arguments={
                "alias": "brain",
                "source": scan_source,
                "intent": "detected_gap",
                "proposed_at": "2026-08-10T01:00:00Z",
            },
        )
        wiki_hygiene = await client.call_tool(
            "wiki_build_maintenance",
            arguments={
                "alias": "brain",
                "source": scan_source,
                "intent": "wiki_hygiene",
                "evidence": [{"ref": "doctor:links", "kind": "link_repair"}],
                "proposed_at": "2026-08-10T01:00:00Z",
            },
        )
        invalid = await client.call_tool(
            "wiki_build_maintenance",
            arguments={
                "alias": "brain",
                "source": scan_source,
                "intent": "unsupported_intent",
                "proposed_at": "2026-08-10T01:00:00Z",
            },
        )
        invalid_input = await client.call_tool(
            "wiki_build_maintenance",
            arguments={
                "alias": "brain",
                "source": {**_source(), "unexpected": True},
                "intent": "durable_learning",
                "claims": [_claim()],
                "proposed_at": "2026-08-10T01:00:00Z",
            },
        )
        return {
            "tools": [tool.name for tool in tools.tools],
            "schema": schema,
            "input_schema": input_schema,
            "result": result,
            "derived_hash": derived_hash,
            "correction": correction,
            "work_history": work_history,
            "detected_gap": detected_gap,
            "wiki_hygiene": wiki_hygiene,
            "invalid": invalid,
            "invalid_input": invalid_input,
        }


if __name__ == "__main__":
    unittest.main()
