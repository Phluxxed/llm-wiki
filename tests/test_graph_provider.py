from __future__ import annotations

import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.compiler import compile_context
from llm_wiki_core.contracts import CompileRequest
from tests.wiki_fixture import base_fm, write_md


class GraphProviderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".llm-wiki.toml").write_text(
            'schema_version = "1"\n'
            'runtime_contract = "3"\n'
            '[compiler]\n'
            'providers = ["seed", "frontmatter", "text", "graph", "source"]\n',
            encoding="utf-8",
        )
        write_md(
            self.root / "projects" / "brain.md",
            base_fm(title="Brain", knowledge_state="current"),
            "## Runtime\n\nBrain uses the [shared runtime](../concepts/runtime.md).",
        )
        write_md(
            self.root / "concepts" / "runtime.md",
            base_fm(title="Shared Runtime", knowledge_state="current"),
            "## Ownership\n\nThe runtime is owned by [llm-wiki](../systems/llm-wiki.md).",
        )
        write_md(
            self.root / "systems" / "llm-wiki.md",
            base_fm(title="llm-wiki", knowledge_state="current"),
            "## Role\n\nCanonical traversal implementation.",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_relationship_compile_includes_authored_bridge_spans(self):
        request = CompileRequest.from_mapping(
            {
                "alias": "test",
                "question": "How does Brain connect to llm-wiki?",
                "seeds": [],
            }
        )

        response = compile_context(self.root, request).to_dict()

        bridges = [item for item in response["evidence"] if item["provider"] == "graph"]
        self.assertEqual(len(bridges), 1)
        bridge = bridges[0]
        self.assertEqual(
            [(span["page"], span["start_line"]) for span in bridge["locator"]["spans"]],
            [("projects/brain.md", 16), ("concepts/runtime.md", 16)],
        )
        self.assertIn("[shared runtime](../concepts/runtime.md)", bridge["content"])
        self.assertIn("[llm-wiki](../systems/llm-wiki.md)", bridge["content"])
        self.assertIn("bridge", bridge["roles"])
        self.assertTrue(bridge["atomic"])
        self.assertTrue(response["stop"]["sufficient"])

    def test_native_lookup_sections_relationships_and_sources_need_no_process(self):
        write_md(
            self.root / "projects" / "brain.md",
            base_fm(title="Brain", knowledge_state="current", source="sources/brain.md"),
            "## Runtime\n\nBrain uses the [shared runtime](../concepts/runtime.md).",
        )
        (self.root / "sources").mkdir()
        (self.root / "sources" / "brain.md").write_text(
            "Brain uses the shared runtime according to the original design.", encoding="utf-8",
        )
        # Use default providers, an empty executable search path, and a guard
        # against subprocess creation: local retrieval must be self-contained.
        (self.root / ".llm-wiki.toml").write_text(
            'schema_version = "1"\nruntime_contract = "3"\n', encoding="utf-8",
        )
        with patch.dict("os.environ", {"PATH": ""}), patch(
            "subprocess.Popen", side_effect=AssertionError("retrieval started a subprocess"),
        ) as process:
            lookup = compile_context(self.root, CompileRequest.from_mapping({
                "alias": "test", "question": "What is Brain?",
            })).to_dict()
            section = compile_context(self.root, CompileRequest.from_mapping({
                "alias": "test", "question": "Who owns runtime?",
            })).to_dict()
            relationship = compile_context(self.root, CompileRequest.from_mapping({
                "alias": "test", "question": "How does Brain connect to llm-wiki?",
            })).to_dict()
        process.assert_not_called()
        self.assertTrue(any(item["provider"] == "frontmatter" and item["page"] == "projects/brain.md"
                            for item in lookup["evidence"]))
        self.assertTrue(any(item["provider"] == "source" and item["source"] == "sources/brain.md"
                            for item in lookup["evidence"]))
        self.assertTrue(any(item["provider"] == "text" and item["locator"]["section"] == "Ownership"
                            for item in section["evidence"]))
        self.assertTrue(any(item["provider"] == "graph" and "bridge" in item["roles"]
                            for item in relationship["evidence"]))
        self.assertEqual(lookup["diagnostics"], [])

    def test_missing_named_endpoint_cannot_be_replaced_by_a_nearby_link(self):
        response = compile_context(self.root, CompileRequest.from_mapping({
            "alias": "test", "question": "How does Brain connect to Unwritten?",
        })).to_dict()
        self.assertIn("bridge", response["coverage"]["uncovered_roles"])
        self.assertFalse(response["stop"]["sufficient"])
        self.assertFalse(any("bridge" in item["roles"] for item in response["evidence"]))

    def test_hard_content_limit_keeps_a_multi_hop_bridge_all_or_none(self):
        response = compile_context(self.root, CompileRequest.from_mapping({
            "alias": "test", "question": "How does Brain connect to llm-wiki?",
            "budget": {"max_content_bytes": 100},
        })).to_dict()
        self.assertIn("bridge", response["coverage"]["uncovered_roles"])
        self.assertTrue(any(item["candidate_id"].startswith("graph:path:")
                            and item["reason"] == "content_byte_limit" for item in response["omissions"]))
        self.assertFalse(any(item["provider"] == "graph" for item in response["evidence"]))
        response = compile_context(self.root, CompileRequest.from_mapping({
            "alias": "test", "question": "How does Brain connect to llm-wiki?",
            "budget": {"target_bytes": 100, "max_bytes": 1700},
        })).to_dict()
        self.assertIn("bridge", response["coverage"]["uncovered_roles"])
        self.assertFalse(any(item["provider"] == "graph" for item in response["evidence"]))

    def test_multi_hop_path_preserves_each_pages_advisory_trust_and_freshness(self):
        write_md(
            self.root / "projects" / "brain.md",
            base_fm(title="Brain", knowledge_state="current", status="stable",
                    verified={"by": "human:vik", "at": "2026-01-01T00:00:00Z"}),
            "Brain uses the [shared runtime](../concepts/runtime.md).",
        )
        write_md(
            self.root / "concepts" / "runtime.md",
            base_fm(title="Shared Runtime", knowledge_state="current", status="draft",
                    stale_after="2026-01-01T00:00:00Z"),
            "The runtime is owned by [llm-wiki](../systems/llm-wiki.md).",
        )
        response = compile_context(self.root, CompileRequest.from_mapping({
            "alias": "test", "question": "How does Brain connect to llm-wiki?",
        })).to_dict()
        bridge = next(item for item in response["evidence"] if item["provider"] == "graph")
        self.assertEqual(bridge["metadata"]["trust"], "unverified")
        self.assertEqual(bridge["metadata"]["freshness"], "stale")
        self.assertEqual(bridge["metadata"]["status"], "draft")
        self.assertEqual(bridge["metadata"]["pages"]["projects/brain.md"]["trust"], "human-reviewed")
        self.assertIn("okf_stale", bridge["derived_flags"])
        self.assertNotIn("human-reviewed", bridge["authority_signals"])

    def test_mixed_path_states_do_not_inherit_first_pages_current_state(self):
        for state in ("historical", "contradicted", "weak", "inferred", "unspecified"):
            with self.subTest(state=state):
                write_md(
                    self.root / "concepts" / "runtime.md",
                    base_fm(title="Shared Runtime", knowledge_state=state),
                    "The runtime is owned by [llm-wiki](../systems/llm-wiki.md).",
                )
                response = compile_context(self.root, CompileRequest.from_mapping({
                    "contract_version": "3", "alias": "test",
                    "question": "How does Brain connect to llm-wiki?",
                    "temporal": {"view": "current", "request_time": "2026-11-05T00:00:00Z"},
                })).to_dict()
                bridge = next(item for item in response["evidence"] if item["provider"] == "graph")
                self.assertEqual(bridge["authored_state"], state)
                self.assertNotIn("page-authored current", bridge["content"])
                self.assertIn(state + " background", bridge["content"])
                self.assertNotIn("explicit_current", bridge["authority_signals"])
                self.assertEqual([span["authored_state"] for span in bridge["locator"]["spans"]], ["current", state])

    def test_contradicted_hop_cannot_bypass_historical_hop_exclusion(self):
        write_md(
            self.root / "projects" / "brain.md",
            base_fm(title="Brain", knowledge_state="historical"),
            "Brain uses the [shared runtime](../concepts/runtime.md).",
        )
        write_md(
            self.root / "concepts" / "runtime.md",
            base_fm(title="Shared Runtime", knowledge_state="contradicted"),
            "The runtime is owned by [llm-wiki](../systems/llm-wiki.md).",
        )
        response = compile_context(self.root, CompileRequest.from_mapping({
            "alias": "test", "question": "How does Brain connect to llm-wiki?",
        })).to_dict()
        self.assertIn("bridge", response["coverage"]["uncovered_roles"])
        self.assertTrue(any(item["reason"] == "state_mismatch" and item["candidate_id"].startswith("graph:path:")
                            for item in response["omissions"]))

    def test_multi_hop_path_does_not_bypass_invalid_intermediate_history(self):
        write_md(
            self.root / "concepts" / "runtime.md",
            base_fm(title="Shared Runtime", knowledge_state="current", temporal_claim_revisions=[{}]),
            "## Ownership\n\nThe runtime is owned by [llm-wiki](../systems/llm-wiki.md).",
        )
        response = compile_context(self.root, CompileRequest.from_mapping({
            "contract_version": "3", "alias": "test",
            "question": "How does Brain connect to llm-wiki?",
            "temporal": {"view": "current", "request_time": "2026-11-05T00:00:00Z"},
        })).to_dict()
        self.assertIn("TEMPORAL_HISTORY_INVALID", [item["code"] for item in response["diagnostics"]])
        self.assertIn("bridge", response["coverage"]["uncovered_roles"])
        self.assertFalse(any(item["provider"] == "graph" for item in response["evidence"]))

    def test_seed_authored_multi_endpoint_bridge_outranks_reciprocal_links(self):
        write_md(
            self.root / "ideas" / "left.md",
            base_fm(title="Left"),
            "Left cites [MOSS](../papers/moss.md).",
        )
        write_md(
            self.root / "ideas" / "right.md",
            base_fm(title="Right"),
            "Right cites [MOSS](../papers/moss.md).",
        )
        write_md(
            self.root / "papers" / "moss.md",
            base_fm(title="MOSS"),
            "## Summary\n\nMOSS sharpens both [Left](../ideas/left.md) and [Right](../ideas/right.md).",
        )
        request = CompileRequest.from_mapping(
            {
                "alias": "test",
                "question": "How does MOSS connect Left and Right?",
                "seeds": ["papers/moss.md", "ideas/left.md", "ideas/right.md"],
                "budget": {
                    "target_bytes": 500,
                    "max_bytes": 12_000,
                    "target_items": 1,
                    "max_items": 8,
                },
            }
        )

        response = compile_context(self.root, request).to_dict()

        bridge = next(item for item in response["evidence"] if "bridge" in item["roles"])
        self.assertEqual(bridge["page"], "papers/moss.md")
        self.assertEqual(bridge["content"], "MOSS sharpens both [Left](../ideas/left.md) and [Right](../ideas/right.md).")
        self.assertIn("seed_authored_multi_bridge", bridge["selection_reasons"])


if __name__ == "__main__":
    unittest.main()
