from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.temporal import (  # noqa: E402
    TemporalContractError,
    EntityRef,
    build_observation_ref,
    build_temporal_fact_candidate,
)
from llm_wiki_core.temporal_reconciliation import (  # noqa: E402
    ReconciliationRelation,
    TemporalReconciliationResult,
    reconcile_temporal_candidates,
)


FIXTURE = Path(__file__).parent / "fixtures" / "temporal" / "reconciliation.json"

OBS_A = "temporal-observation:sha256:" + "a" * 64
OBS_B = "temporal-observation:sha256:" + "b" * 64
MISSING_OBS = "temporal-observation:sha256:" + "f" * 64


def _observation(payload: bytes = b"evidence", source_ref: str = "sources/a.md"):
    return build_observation_ref(
        source_kind="source:manual",
        source_ref=source_ref,
        locator={"line": 1},
        input_type="input:markdown",
        observed_at="2026-08-10T00:00:00Z",
        source_event_time={"kind": "unknown", "reason": "not stated"},
        retention="immutable_source",
        payload=payload,
    )


def _candidate(value: str, start: str = "2026-01-01", observation_ids=(OBS_A,), *, relations=(), subject="pages/a.md", signal=()):
    subject_ref = subject if isinstance(subject, dict) else {"kind": "resolved_page", "page": subject}
    return build_temporal_fact_candidate(
        subject=subject_ref,
        predicate="status:has_state",
        object_ref={"kind": "literal", "datatype": "type:text", "value": value},
        proposed_world_validity={
            "from": {"kind": "unknown", "reason": "not stated"} if start == "unknown" else {"kind": "known", "value": start},
            "to": {"kind": "open"},
        },
        observed_at="2026-08-10T00:00:00Z",
        proposed_at="2026-08-10T00:00:01Z",
        supporting_observation_ids=list(observation_ids),
        proposed_relations=list(relations),
        signals=list(signal),
    )


class ReconciliationContractTest(unittest.TestCase):
    def test_empty_result_contract_is_deterministic_and_mutation_disabled(self):
        result = reconcile_temporal_candidates(candidates=[], observations={})
        self.assertEqual(result.status, "no_relations_observed")
        self.assertEqual(result.candidate_ids, ())
        self.assertEqual(result.relations, ())
        self.assertEqual(result.usage, {
            "candidate_count": 0,
            "observation_count": 0,
            "claim_group_count": 0,
            "comparisons": 0,
            "relation_count": 0,
        })
        self.assertEqual(result.disposition, "candidate_only")
        self.assertEqual(result.mutation, {"allowed": False, "commands": []})
        self.assertEqual(
            TemporalReconciliationResult.from_mapping(result.to_dict()).to_dict(),
            result.to_dict(),
        )

    def test_fixture_has_frozen_case_names(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(payload["contract_version"], "temporal-reconciliation-evaluation/1")
        self.assertEqual(
            {case["name"] for case in payload["cases"]},
            {
                "exact_duplicate_metadata_variant",
                "independent_corroboration_is_not_duplicate",
                "ordered_supersession",
                "same_start_contradiction",
                "explicit_qualification",
                "late_arrival_uses_world_time",
                "backdated_validity_uses_world_time",
                "ambiguous_identity_is_unresolved",
                "unknown_world_start_is_unresolved",
                "missing_observation_is_unresolved",
                "same_fact_different_interval_is_unresolved",
                "unconfirmed_declared_relation_is_unresolved",
                "missing_declared_target_is_unresolved",
                "retirement_without_replacement_is_not_invented",
                "unrelated_claims_have_no_relation",
            },
        )


class DuplicateAndProvenanceTest(unittest.TestCase):
    def test_exact_duplicates_collapse_and_independent_evidence_corroborates(self):
        observation = _observation()
        corroborating_observation = _observation(b"other", "sources/b.md")
        obs_a, obs_b = observation.observation_id, corroborating_observation.observation_id
        first = _candidate("ready", observation_ids=(obs_a,))
        variant = _candidate("ready", observation_ids=(obs_a,), signal=[{"kind": "signal:variant", "observation_ids": [obs_a]}])
        corroborating = _candidate("ready", observation_ids=(obs_b,))
        result = reconcile_temporal_candidates(
            candidates=[variant, corroborating, first],
            observations={obs_a: observation, obs_b: corroborating_observation},
        )
        self.assertEqual([relation.kind for relation in result.relations], ["duplicate"])
        self.assertEqual(result.relations[0].target_candidate_id, min(first.candidate_id, variant.candidate_id))
        self.assertNotEqual(first.candidate_id, variant.candidate_id)
        self.assertEqual(result.usage["candidate_count"], 3)

    def test_ambiguous_and_missing_provenance_are_source_only_unresolved(self):
        observation = _observation()
        obs_a = observation.observation_id
        ambiguous = _candidate(
            "ready",
            observation_ids=(obs_a,),
            subject={"kind": "ambiguous", "surface": "A", "candidates": [{"ref": {"kind": "resolved_page", "page": "pages/a.md"}, "observation_ids": [obs_a]}]},
        )
        missing = _candidate("blocked", observation_ids=(MISSING_OBS,))
        result = reconcile_temporal_candidates(candidates=[missing, ambiguous], observations={obs_a: observation})
        self.assertEqual({relation.basis for relation in result.relations}, {"ambiguous_identity", "incomplete_provenance"})
        self.assertTrue(all(relation.target_candidate_id is None for relation in result.relations))
        self.assertIn(MISSING_OBS, next(relation for relation in result.relations if relation.basis == "incomplete_provenance").observation_ids)

    def test_candidate_and_observation_order_does_not_change_result(self):
        observation = _observation()
        other = _observation(b"other", "sources/b.md")
        obs_a, obs_b = observation.observation_id, other.observation_id
        candidates = [_candidate("ready", observation_ids=(obs_a,)), _candidate("blocked", observation_ids=(obs_b,))]
        first = reconcile_temporal_candidates(candidates=candidates, observations={obs_a: observation, obs_b: other})
        second = reconcile_temporal_candidates(candidates=list(reversed(candidates)), observations={obs_b: other, obs_a: observation})
        self.assertEqual(first.to_dict(), second.to_dict())


class ClaimChainTest(unittest.TestCase):
    def test_world_start_orders_supersession_and_same_start_contradiction(self):
        observation = _observation()
        obs_a = observation.observation_id
        old = _candidate("ready", "2025-01-01", observation_ids=(obs_a,))
        new = _candidate("blocked", "2025-02-01", observation_ids=(obs_a,))
        conflict = _candidate("unknown", "2025-01-01", observation_ids=(obs_a,))
        result = reconcile_temporal_candidates(candidates=[new, conflict, old], observations={obs_a: observation})
        self.assertIn(("supersede", new.candidate_id, conflict.candidate_id), {(r.kind, r.source_candidate_id, r.target_candidate_id) for r in result.relations})
        self.assertIn("contradict", {r.kind for r in result.relations})
        self.assertEqual(result.usage["comparisons"], 2)

    def test_unknown_start_and_different_intervals_remain_unresolved(self):
        observation = _observation()
        obs_a = observation.observation_id
        unknown = _candidate("ready", "unknown", observation_ids=(obs_a,))
        known = _candidate("blocked", "2025-01-01", observation_ids=(obs_a,))
        result = reconcile_temporal_candidates(candidates=[unknown, known], observations={obs_a: observation})
        self.assertIn("unknown_world_start", {r.basis for r in result.relations})
        same_fact = _candidate("ready", "2025-02-01", observation_ids=(obs_a,))
        interval_result = reconcile_temporal_candidates(candidates=[_candidate("ready", "2025-01-01", observation_ids=(obs_a,)), same_fact], observations={obs_a: observation})
        self.assertIn("same_fact_different_interval", {r.basis for r in interval_result.relations})

    def test_unrelated_claims_and_retirement_do_not_invent_relations(self):
        observation = _observation()
        obs_a = observation.observation_id
        first = _candidate("ready", observation_ids=(obs_a,))
        unrelated = _candidate("blocked", observation_ids=(obs_a,), subject="pages/other.md")
        result = reconcile_temporal_candidates(candidates=[first, unrelated], observations={obs_a: observation})
        self.assertEqual(result.relations, ())
        self.assertEqual(result.status, "no_relations_observed")


class QualificationAndResultTest(unittest.TestCase):
    def test_qualification_suppresses_replacement_pair_and_preserves_candidates(self):
        observation = _observation()
        obs_a = observation.observation_id
        claim = _candidate("ready", "2025-01-01", observation_ids=(obs_a,))
        limiting = _candidate(
            "limited",
            "2025-02-01",
            observation_ids=(obs_a,),
            relations=({"kind": "qualify", "target_id": claim.candidate_id, "observation_ids": [obs_a]},),
        )
        result = reconcile_temporal_candidates(candidates=[claim, limiting], observations={obs_a: observation})
        self.assertEqual([relation.kind for relation in result.relations], ["qualify"])
        self.assertEqual(result.relations[0].source_candidate_id, limiting.candidate_id)
        self.assertEqual(result.relations[0].target_candidate_id, claim.candidate_id)

    def test_declarations_without_confirmation_and_missing_targets_are_unresolved(self):
        observation = _observation()
        obs_a = observation.observation_id
        target = _candidate("ready", observation_ids=(obs_a,))
        unmatched = _candidate("blocked", observation_ids=(obs_a,), relations=({"kind": "supersede", "target_id": target.candidate_id, "observation_ids": [obs_a]},))
        missing_target = _candidate("unknown", observation_ids=(obs_a,), relations=({"kind": "contradict", "target_id": "temporal-candidate:sha256:" + "c" * 64, "observation_ids": [obs_a]},))
        result = reconcile_temporal_candidates(candidates=[unmatched, target, missing_target], observations={obs_a: observation})
        self.assertIn("declared_relation_unconfirmed", {r.basis for r in result.relations})
        self.assertIn("missing_target", {r.basis for r in result.relations})

    def test_strict_contracts_reject_unknown_or_mismatched_fields(self):
        result = reconcile_temporal_candidates(candidates=[], observations={})
        with self.assertRaises(TemporalContractError):
            TemporalReconciliationResult.from_mapping({**result.to_dict(), "extra": True})
        with self.assertRaises(TemporalContractError):
            ReconciliationRelation.from_mapping({
                "contract_version": "temporal-reconciliation-relation/1",
                "relation_id": "temporal-reconciliation-relation:sha256:" + "0" * 64,
                "kind": "unresolved",
                "source_candidate_id": "temporal-candidate:sha256:" + "a" * 64,
                "target_candidate_id": None,
                "basis": "declared_unresolved",
                "observation_ids": [],
                "unknowns": [],
                "disposition": "candidate_only",
                "mutation": {"allowed": False, "commands": []},
            })


if __name__ == "__main__":
    unittest.main()
