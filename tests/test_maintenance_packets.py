from __future__ import annotations

from datetime import date
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.maintenance import build_candidate_proposal, build_maintenance_packet
from llm_wiki_core.migration import inspect_migration
from tests.wiki_fixture import base_fm, create_wiki_root, write_md


class MaintenancePacketTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = create_wiki_root(Path(self._tmp.name) / "wiki")
        write_md(
            self.root / "notes" / "stale.md",
            base_fm(
                title="Stale Current",
                knowledge_state="current",
                last_reviewed="2025-01-01",
            ),
            "A claim that needs review.",
        )
        write_md(
            self.root / "notes" / "superseded.md",
            base_fm(title="Superseded", knowledge_state="superseded"),
            "Old behavior.",
        )
        write_md(
            self.root / "notes" / "contradicted.md",
            base_fm(title="Contradicted", knowledge_state="contradicted"),
            "A claim explicitly marked contradicted.",
        )
        write_md(
            self.root / "notes" / "missing-source.md",
            base_fm(title="Missing Source", source="sources/missing.md"),
            "Derived claim.",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_packet_reports_supported_findings_with_exact_evidence(self):
        packet = build_maintenance_packet(
            self.root,
            alias="test",
            stale_after_days=180,
            as_of=date(2026, 7, 12),
        )

        kinds = {candidate["kind"] for candidate in packet["candidates"]}
        self.assertEqual(
            kinds,
            {
                "stale_current_claim",
                "explicit_contradiction",
                "supersession_gap",
                "source_gap",
                "runtime_drift",
            },
        )
        source_gap = next(item for item in packet["candidates"] if item["kind"] == "source_gap")
        self.assertEqual(source_gap["evidence"][0]["page"], "notes/missing-source.md")
        self.assertEqual(source_gap["evidence"][0]["locator"]["field"], "source")
        self.assertEqual(source_gap["evidence"][0]["content"], "source: sources/missing.md")
        self.assertEqual(packet["mutation"], {"allowed": False, "commands": []})
        self.assertIn("semantic_contradictions", {item["kind"] for item in packet["unknowns"]})

    def test_packet_is_deterministic_and_does_not_claim_empty_means_clean(self):
        root = create_wiki_root(Path(self._tmp.name) / "clean", with_scripts=False)
        write_md(root / "notes" / "a.md", base_fm(title="A"), "A body.")
        for operation in inspect_migration(root).operations:
            path = root / operation.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(operation.content, encoding="utf-8")

        first = build_maintenance_packet(root, alias="clean", as_of=date(2026, 7, 12))
        second = build_maintenance_packet(root, alias="clean", as_of=date(2026, 7, 12))

        self.assertEqual(first, second)
        self.assertEqual(first["status"], "no_candidates_observed")
        self.assertEqual(first["candidates"], [])
        self.assertTrue(first["unknowns"])
        self.assertNotEqual(first["status"], "clean")


class MaintenanceCandidateProposalTest(unittest.TestCase):
    def test_builds_strong_read_only_proposal_for_durable_outcome(self):
        proposal = build_candidate_proposal(
            alias="anvil-brain-codex",
            kind="durable_outcome",
            diagnostic="The approved implementation result is not represented in Brain.",
            review_question="Should this verified outcome become durable Brain knowledge?",
            pages=["projects/anvil-redux.md"],
            evidence=[
                {
                    "ref": "docs/plans/implementation.md:42",
                    "note": "approved result",
                    "content_hash": "abc123",
                }
            ],
        )

        self.assertEqual(proposal["contract_version"], "1")
        self.assertEqual(proposal["kind"], "durable_outcome")
        self.assertEqual(proposal["target_wiki"], "anvil-brain-codex")
        self.assertEqual(proposal["signal"], "deterministic")
        self.assertEqual(proposal["eligibility"]["mode"], "first_observation")
        self.assertEqual(proposal["eligibility"]["independent_evidence_count"], 1)
        self.assertEqual(proposal["disposition"], "candidate_only")
        self.assertEqual(proposal["mutation"], {"allowed": False, "commands": []})
        self.assertRegex(proposal["id"], r"^maintenance-observation:[0-9a-f]{16}$")
        self.assertRegex(proposal["dedupe_key"], r"^maintenance-question:[0-9a-f]{16}$")

    def test_relationship_proposal_is_order_invariant_and_requires_review_threshold(self):
        common = {
            "alias": "anvil-brain-codex",
            "kind": "relationship_gap",
            "diagnostic": "Repeated retrieval evidence suggests a missing route.",
            "review_question": "Should these Brain pages be connected?",
        }
        first = build_candidate_proposal(
            **common,
            pages=["patterns/graph.md", "projects/anvil-redux.md"],
            evidence=[
                {"ref": "trace:b", "content_hash": "hash-b"},
                {"ref": "trace:a", "content_hash": "hash-a"},
            ],
        )
        second = build_candidate_proposal(
            **common,
            pages=["./projects/anvil-redux.md", "patterns\\graph.md"],
            evidence=[
                {"ref": "trace:a", "content_hash": "hash-a"},
                {"ref": "trace:b", "content_hash": "hash-b"},
            ],
        )

        self.assertEqual(first, second)
        self.assertEqual(first["signal"], "speculative")
        self.assertEqual(first["eligibility"]["mode"], "recurrence_or_corroboration")
        self.assertEqual(first["eligibility"]["independent_evidence_count"], 2)
        self.assertEqual(
            first["pages"],
            ["patterns/graph.md", "projects/anvil-redux.md"],
        )

    def test_rejects_invalid_or_unsupported_proposals(self):
        valid = {
            "alias": "anvil-brain-codex",
            "kind": "relationship_gap",
            "diagnostic": "A route may be missing.",
            "review_question": "Should these pages be connected?",
            "pages": ["a.md", "b.md"],
            "evidence": [{"ref": "trace:1"}],
        }
        invalid_cases = [
            {**valid, "alias": ""},
            {**valid, "kind": "invented_kind"},
            {**valid, "review_question": ""},
            {**valid, "pages": ["../outside.md", "b.md"]},
            {**valid, "pages": ["a.md"]},
            {**valid, "evidence": []},
            {**valid, "evidence": [{"ref": ""}]},
        ]

        for case in invalid_cases:
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    build_candidate_proposal(**case)


if __name__ == "__main__":
    unittest.main()
