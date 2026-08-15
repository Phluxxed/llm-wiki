from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.maintenance import build_candidate_proposal
from llm_wiki_core.temporal import (
    EntityRef,
    build_observation_ref,
    build_temporal_fact_candidate,
    parse_observation_ref,
    parse_temporal_fact_candidate,
)
from llm_wiki_core.temporal_reconciliation import reconcile_temporal_candidates
from llm_wiki_core.unified_maintenance import (
    adapt_legacy_discovery_packet,
    adapt_legacy_task_proposal,
    build_unified_maintenance_outcome,
    build_unified_maintenance_proposal,
    compose_unified_maintenance_proposal,
    normalize_legacy_outcome,
    parse_unified_maintenance_outcome,
    parse_unified_maintenance_proposal,
)


FIXTURE = REPO_ROOT / "tests" / "fixtures" / "unified_maintenance" / "v1.json"


class UnifiedMaintenanceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_fixture_corpus_has_frozen_cases_and_classification(self):
        expected_names = {
            "ordinary_durable_outcome",
            "dated_milestone",
            "correction_unknown_start_time",
            "explicit_supersession",
            "link_repair",
            "no_change",
            "ambiguous_identity",
            "prompt_injection",
        }
        self.assertEqual({case["name"] for case in self.corpus["proposals"]}, expected_names)
        for case in self.corpus["proposals"]:
            proposal = build_unified_maintenance_proposal(**case["request"])
            self.assertEqual(proposal["schema_version"], "unified-maintenance/1")
            self.assertEqual(proposal["proposal_id"], case["expected_proposal_id"])
            self.assertEqual(proposal["classification"], case["classification"])
            self.assertEqual(parse_unified_maintenance_proposal(proposal), proposal)
            self.assertEqual(
                build_unified_maintenance_proposal(**case["request"]),
                proposal,
            )
            self.assertEqual(proposal["disposition"], "candidate_only")
            self.assertEqual(proposal["mutation"], {"allowed": False, "commands": []})
            self.assertEqual(proposal["authority"], "target_wiki_steward")

    def test_fixture_outcome_ids_and_classes_are_frozen(self):
        proposals = {
            case["name"]: build_unified_maintenance_proposal(**case["request"])
            for case in self.corpus["proposals"]
        }
        for case in self.corpus["outcomes"]:
            outcome = build_unified_maintenance_outcome(proposals[case["proposal"]], **case["args"])
            self.assertEqual(outcome["outcome_id"], case["expected_outcome_id"])
            self.assertEqual(outcome["change_class"], case["change_class"])

    def test_public_proposal_rejects_version_switches_and_unknown_fields(self):
        request = copy.deepcopy(self.corpus["proposals"][0]["request"])
        request["maintenance_version"] = "2"
        with self.assertRaises(ValueError):
            build_unified_maintenance_proposal(**request)

        proposal = build_unified_maintenance_proposal(**self.corpus["proposals"][0]["request"])
        proposal["temporal"] = True
        with self.assertRaises(ValueError):
            parse_unified_maintenance_proposal(proposal)

    def test_accepted_outcome_obligations_are_strict_and_idempotent(self):
        proposal = build_unified_maintenance_proposal(**self.corpus["proposals"][0]["request"])
        accepted = build_unified_maintenance_outcome(
            proposal,
            outcome="accepted",
            recorded_at="2026-08-11T00:01:00Z",
            provenance=["docs/spec-brain-temporal-knowledge-lifecycle.md:2616"],
            changed_refs=["projects/anvil-redux.md", "index.md", "log.md"],
            brain_commit="commit:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            brain_tree="tree:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            verification={"lint": True, "render": True},
            temporal_revision_ids=["temporal-revision:sha256:" + "1" * 64],
            summary="Recorded the approved durable outcome.",
        )
        self.assertEqual(accepted["change_class"], "knowledge_revision")
        self.assertEqual(parse_unified_maintenance_outcome(accepted), accepted)
        self.assertEqual(accepted["outcome_id"], build_unified_maintenance_outcome(
            proposal,
            outcome="accepted",
            recorded_at="2026-08-11T00:01:00Z",
            provenance=["docs/spec-brain-temporal-knowledge-lifecycle.md:2616"],
            changed_refs=["projects/anvil-redux.md", "index.md", "log.md"],
            brain_commit="commit:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            brain_tree="tree:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            verification={"lint": True, "render": True},
            temporal_revision_ids=["temporal-revision:sha256:" + "1" * 64],
            summary="Recorded the approved durable outcome.",
        )["outcome_id"])

        missing_revision = dict(accepted)
        missing_revision["temporal_revision_ids"] = []
        with self.assertRaises(ValueError):
            parse_unified_maintenance_outcome(missing_revision)

        with self.assertRaises(ValueError):
            build_unified_maintenance_outcome(
                proposal,
                outcome="accepted",
                recorded_at="2026-08-11T00:01:00Z",
                provenance=["docs/spec-brain-temporal-knowledge-lifecycle.md:2616"],
                changed_refs=["projects/anvil-redux.md"],
                brain_commit="commit:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                brain_tree="tree:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                verification={"lint": True, "render": True},
                not_applicable_reason="Incorrectly treating knowledge as hygiene.",
                summary="Invalid cross-classification.",
            )

        hygiene_proposal = build_unified_maintenance_proposal(**self.corpus["proposals"][4]["request"])
        hygiene = build_unified_maintenance_outcome(
            hygiene_proposal,
            outcome="accepted",
            recorded_at="2026-08-11T00:01:00Z",
            provenance=["doctor:links"],
            changed_refs=["patterns/maintenance.md"],
            brain_commit="commit:cccccccccccccccccccccccccccccccccccccccc",
            brain_tree="tree:dddddddddddddddddddddddddddddddddddddddd",
            verification={"lint": True, "render": True},
            not_applicable_reason="Link repair changes structure only.",
            summary="Repaired one internal link.",
        )
        self.assertEqual(hygiene["temporal_revision_ids"], [])
        bad_hygiene = dict(hygiene)
        bad_hygiene["not_applicable_reason"] = None
        with self.assertRaises(ValueError):
            parse_unified_maintenance_outcome(bad_hygiene)

    def test_parse_rejects_missing_required_top_level_fields(self):
        proposal = build_unified_maintenance_proposal(**self.corpus["proposals"][0]["request"])
        for field in ("schema_version", "proposal_id", "target_wiki", "source", "classification", "observations", "candidates", "reconciliation", "affected_pages", "unknowns", "disposition", "mutation", "authority"):
            malformed = dict(proposal)
            del malformed[field]
            with self.subTest(kind="proposal", field=field):
                with self.assertRaises(ValueError):
                    parse_unified_maintenance_proposal(malformed)

        outcome = build_unified_maintenance_outcome(
            proposal,
            outcome="accepted",
            recorded_at="2026-08-11T00:01:00Z",
            provenance=["docs/spec-brain-temporal-knowledge-lifecycle.md:2616"],
            changed_refs=["projects/anvil-redux.md"],
            brain_commit="commit:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            brain_tree="tree:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            verification={"lint": True, "render": True},
            temporal_revision_ids=["temporal-revision:sha256:" + "1" * 64],
            summary="Recorded the approved durable outcome.",
        )
        for field in ("schema_version", "outcome_id", "proposal_id", "target_wiki", "change_class", "outcome", "recorded_at", "provenance", "changed_refs", "brain_commit", "brain_tree", "verification", "temporal_revision_ids", "not_applicable_reason", "summary"):
            malformed = dict(outcome)
            del malformed[field]
            with self.subTest(kind="outcome", field=field):
                with self.assertRaises(ValueError):
                    parse_unified_maintenance_outcome(malformed)

    def test_nonaccepted_outcomes_cannot_claim_durable_refs(self):
        proposal = build_unified_maintenance_proposal(**self.corpus["proposals"][5]["request"])
        outcome = build_unified_maintenance_outcome(
            proposal,
            outcome="no_change",
            recorded_at="2026-08-11T00:01:00Z",
            summary="No durable change was identified.",
        )
        self.assertEqual(outcome["changed_refs"], [])
        self.assertEqual(outcome["temporal_revision_ids"], [])
        bad = dict(outcome)
        bad["changed_refs"] = ["notes/a.md"]
        with self.assertRaises(ValueError):
            parse_unified_maintenance_outcome(bad)

    def test_legacy_no_op_normalizes_without_changing_ids_and_v2_reads(self):
        legacy = self.corpus["legacy"]
        normalized = normalize_legacy_outcome(legacy["v1_outcome"])
        self.assertEqual(normalized["outcome"], "no_change")
        self.assertEqual(normalized["outcome_id"], legacy["v1_outcome"]["outcome_id"])
        self.assertEqual(legacy["v1_outcome"]["outcome"], "no_op")
        self.assertEqual(normalize_legacy_outcome(legacy["temporal_v2_outcome"]), legacy["temporal_v2_outcome"])

    def test_internal_adapters_preserve_legacy_payloads_and_ids(self):
        task = build_candidate_proposal(
            alias="brain",
            kind="durable_outcome",
            diagnostic="A durable result is not represented.",
            review_question="Should the result be recorded?",
            pages=["notes/result.md"],
            evidence=[{"ref": "handoff:1", "note": "verified"}],
        )
        discovery = {
            "kind": "maintenance_candidate_packet",
            "contract_version": "1",
            "wiki": {"alias": "brain"},
            "status": "candidates_present",
            "candidates": [task],
            "unknowns": [],
            "mutation": {"allowed": False, "commands": []},
            "stewardship": {"decision": "review_required"},
        }
        self.assertEqual(adapt_legacy_task_proposal(task), task)
        self.assertEqual(adapt_legacy_discovery_packet(discovery), discovery)

    def test_composition_preserves_temporal_nested_ids_and_payloads(self):
        observation = build_observation_ref(
            source_kind="source:manual",
            source_ref="sources/status.md",
            locator={"line": 1},
            input_type="input:markdown",
            observed_at="2026-08-10T00:00:00Z",
            source_event_time={"kind": "unknown", "reason": "not stated"},
            retention="immutable_source",
            payload=b"ready",
        ).to_dict()
        candidate = build_temporal_fact_candidate(
            subject=EntityRef.from_mapping({"kind": "resolved_page", "page": "a.md"}),
            predicate="status:has_state",
            object_ref=EntityRef.from_mapping({"kind": "literal", "datatype": "type:text", "value": "ready"}),
            claim_scope="default",
            proposed_world_validity={
                "from": {"kind": "known", "value": "2026-01-01"},
                "to": {"kind": "open"},
            },
            observed_at="2026-08-10T00:00:00Z",
            proposed_at="2026-08-10T01:00:00Z",
            supporting_observation_ids=[observation["observation_id"]],
            signals=[{"kind": "signal:direct", "observation_ids": [observation["observation_id"]]}],
        ).to_dict()
        reconciliation = reconcile_temporal_candidates(
            candidates=[parse_temporal_fact_candidate(candidate)],
            observations={observation["observation_id"]: parse_observation_ref(observation)},
        ).to_dict()
        proposal = compose_unified_maintenance_proposal(
            alias="brain",
            source={
                "source_kind": "source:manual",
                "source_ref": "sources/status.md",
                "content_hash": "5" * 64,
            },
            intent="durable_learning",
            proposed_at="2026-08-10T01:00:00Z",
            observations=[observation],
            candidates=[candidate],
            reconciliation=reconciliation,
        )
        self.assertEqual(proposal["classification"]["change_class"], "knowledge_revision")
        self.assertEqual(proposal["observations"][0], observation)
        self.assertEqual(proposal["candidates"][0], candidate)
        self.assertEqual(proposal["reconciliation"], reconciliation)
        self.assertEqual(parse_unified_maintenance_proposal(proposal), proposal)


if __name__ == "__main__":
    unittest.main()
