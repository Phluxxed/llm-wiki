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

from tests.wiki_fixture import base_fm, create_wiki_root, write_md

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.temporal import parse_observation_ref, parse_temporal_fact_candidate
from llm_wiki_core.temporal_reconciliation import reconcile_temporal_candidates
from llm_wiki_mcp.errors import WikiMcpError
from llm_wiki_mcp.wiki_runtime import (
    temporal_candidate_proposal,
    wiki_reconcile_temporal_candidates,
)


def _source(index: int = 0) -> dict[str, Any]:
    return {
        "source_kind": "source:manual",
        "source_ref": f"sources/status-{index}.md",
        "locator": {"line": 10, "section": "status"},
        "input_type": "input:markdown",
        "observed_at": f"2026-08-10T00:00:{index:02d}Z",
        "source_event_time": {"kind": "unknown", "reason": "not stated"},
        "retention": "immutable_source",
        "payload_text": "The service is ready.",
    }


def _claim(index: int = 0) -> dict[str, Any]:
    return {
        "subject": {"kind": "resolved_page", "page": "a.md"},
        "predicate": f"status:has_state_{index}",
        "object": {"kind": "literal", "datatype": "type:text", "value": "ready"},
        "claim_scope": "default",
        "proposed_world_validity": {
            "from": {"kind": "known", "value": "2026-01-01"},
            "to": {"kind": "open"},
        },
        "signals": [{"kind": "signal:direct", "detail": "explicit statement"}],
        "unknowns": [],
    }


class TemporalActivationRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        self.wiki = create_wiki_root(self.tmp / "wiki")
        write_md(self.wiki / "a.md", base_fm(title="A"), "A body.")
        self.home = self.tmp / "home"
        os.environ["LLM_WIKI_HOME"] = str(self.home)
        from llm_wiki_mcp.registry import register_wiki

        register_wiki("brain", str(self.wiki), created_by="test")

    def tearDown(self) -> None:
        os.environ.pop("LLM_WIKI_HOME", None)
        self.tmpdir.cleanup()

    def _proposal(self, index: int = 0, claims: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return temporal_candidate_proposal(
            "brain",
            source=_source(index),
            claims=claims if claims is not None else [_claim(index)],
            proposed_at="2026-08-10T01:00:00Z",
        )

    def test_returns_unchanged_public_reconciliation_result_without_writes(self):
        proposal = self._proposal()
        before = {path: path.read_bytes() for path in self.wiki.rglob("*") if path.is_file()}
        expected = reconcile_temporal_candidates(
            candidates=[parse_temporal_fact_candidate(raw) for raw in proposal["packet"]["candidates"]],
            observations={
                proposal["observation"]["observation_id"]: parse_observation_ref(proposal["observation"])
            },
        ).to_dict()

        actual = wiki_reconcile_temporal_candidates("brain", [proposal])

        self.assertEqual(actual, expected)
        self.assertEqual(before, {path: path.read_bytes() for path in self.wiki.rglob("*") if path.is_file()})

    def test_rejects_wrong_alias_count_and_malformed_provenance(self):
        proposal = self._proposal()
        wrong_alias = dict(proposal, target_wiki="other")
        with self.assertRaises(WikiMcpError) as alias_error:
            wiki_reconcile_temporal_candidates("brain", [wrong_alias])
        self.assertEqual(alias_error.exception.code, "INVALID_INPUT")

        with self.assertRaises(WikiMcpError) as count_error:
            wiki_reconcile_temporal_candidates("brain", [])
        self.assertEqual(count_error.exception.code, "INVALID_INPUT")

        malformed = dict(proposal, packet=dict(proposal["packet"], candidates=[{"bad": True}]))
        with self.assertRaises(WikiMcpError):
            wiki_reconcile_temporal_candidates("brain", [malformed])

    def test_enforces_100_unique_candidate_bound(self):
        proposals = [
            self._proposal(index=0, claims=[_claim(index) for index in range(51)]),
            self._proposal(index=1, claims=[_claim(index + 51) for index in range(51)]),
        ]
        with self.assertRaises(WikiMcpError) as error:
            wiki_reconcile_temporal_candidates("brain", proposals)
        self.assertEqual(error.exception.code, "TEMPORAL_LIMIT_EXCEEDED")


class TemporalActivationMcpTest(unittest.TestCase):
    def test_registers_read_only_reconciliation_tool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            wiki = create_wiki_root(tmp / "wiki")
            write_md(wiki / "a.md", base_fm(title="A"), "A body.")
            result = asyncio.run(_round_trip(tmp / "home", wiki))
        self.assertIn("wiki_reconcile_temporal_candidates", result["tools"])
        self.assertFalse(result["result"].is_error)
        self.assertEqual(result["result"].structured_content["kind"], "temporal_reconciliation_result")


async def _round_trip(home: Path, wiki: Path):
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
        await client.call_tool("wiki_register", arguments={"alias": "brain", "path": str(wiki)})
        proposal_result = await client.call_tool(
            "wiki_build_temporal_candidates",
            arguments={
                "alias": "brain",
                "source": _source(),
                "claims": [_claim()],
                "proposed_at": "2026-08-10T01:00:00Z",
            },
        )
        result = await client.call_tool(
            "wiki_reconcile_temporal_candidates",
            arguments={"alias": "brain", "proposals": [proposal_result.structured_content]},
        )
        return {"tools": [tool.name for tool in tools.tools], "result": result}


if __name__ == "__main__":
    unittest.main()
