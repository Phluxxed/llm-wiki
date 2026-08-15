from __future__ import annotations

import inspect
import json
import sys
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.temporal import (  # noqa: E402
    TemporalContractError,
    build_temporal_claim_key,
    build_temporal_fact_candidate,
)
from llm_wiki_core.temporal_persistence import (  # noqa: E402
    TemporalClaimRevision,
    TemporalRevisionFold,
    build_temporal_claim_revision,
    fold_temporal_claim_revisions,
    parse_temporal_claim_revision,
    parse_temporal_claim_revisions,
)


FIXTURE = Path(__file__).parent / "fixtures" / "temporal" / "persistence.json"
OBS_A = "temporal-observation:sha256:" + "a" * 64
OBS_B = "temporal-observation:sha256:" + "b" * 64
CAND_A = "temporal-candidate:sha256:" + "a" * 64
CAND_B = "temporal-candidate:sha256:" + "b" * 64


def _subject(page: str = "people/alex.md") -> dict[str, str]:
    return {"kind": "resolved_page", "page": page}


def _base_values(**changes):
    values = {
        "subject": _subject(),
        "predicate": "status:has_state",
        "object_ref": {"kind": "literal", "datatype": "type:text", "value": "ready"},
        "world_validity": {
            "from": {"kind": "known", "value": "2026-01-01"},
            "to": {"kind": "open"},
        },
        "recorded_at": "2026-08-10T00:00:00Z",
        "candidate_ids": [CAND_B, CAND_A, CAND_A],
        "observation_ids": [OBS_B, OBS_A, OBS_A],
        "steward_evidence_refs": ["sources/status.md", "people/alex.md", "sources/status.md"],
        "decision": "accept",
    }
    values.update(changes)
    return values


def _accept(**changes) -> TemporalClaimRevision:
    return build_temporal_claim_revision(**_base_values(**changes))


class TemporalClaimRevisionContractTest(unittest.TestCase):
    def test_public_claim_key_is_the_frozen_wp_t1_rule(self):
        candidate = build_temporal_fact_candidate(
            subject=_subject(),
            predicate="status:has_state",
            object_ref={"kind": "literal", "datatype": "type:text", "value": "ready"},
            proposed_world_validity={"from": {"kind": "known", "value": "2026-01-01"}, "to": {"kind": "open"}},
            observed_at="2026-08-10T00:00:00Z",
            proposed_at="2026-08-10T00:00:01Z",
            supporting_observation_ids=[OBS_A],
        )
        self.assertEqual(build_temporal_claim_key(_subject(), "status:has_state"), candidate.claim_key)
        self.assertEqual(
            build_temporal_claim_key(_subject(), "status:has_state"),
            build_temporal_claim_key(_subject(), "status:has_state", "default"),
        )
        self.assertNotEqual(build_temporal_claim_key(_subject(), "status:other"), candidate.claim_key)

    def test_builder_normalizes_exact_fields_and_round_trips(self):
        revision = _accept()
        payload = revision.to_dict()
        self.assertEqual(
            set(payload),
            {
                "contract_version", "revision_id", "claim_key", "claim_scope", "decision", "subject",
                "predicate", "object", "world_validity", "recorded_at", "candidate_ids", "observation_ids",
                "retires_revision_ids", "supersedes_revision_ids", "contradicts_revision_ids",
                "qualification_of_revision_ids", "steward_evidence_refs", "authority",
            },
        )
        self.assertEqual(payload["contract_version"], "temporal-claim-revision/1")
        self.assertEqual(payload["authority"], "target_wiki_steward")
        self.assertEqual(payload["candidate_ids"], sorted({CAND_A, CAND_B}))
        self.assertEqual(payload["observation_ids"], sorted({OBS_A, OBS_B}))
        self.assertEqual(payload["steward_evidence_refs"], ["people/alex.md", "sources/status.md"])
        self.assertEqual(parse_temporal_claim_revision(payload).to_dict(), payload)
        self.assertTrue(payload["revision_id"].startswith("temporal-revision:sha256:"))

    def test_revision_identity_includes_recorded_at_but_not_caller_id(self):
        first = _accept(recorded_at="2026-08-10T00:00:00Z")
        second = _accept(recorded_at="2026-08-10T00:00:01Z")
        self.assertNotEqual(first.revision_id, second.revision_id)
        with self.assertRaises(TemporalContractError):
            parse_temporal_claim_revision({**first.to_dict(), "revision_id": "temporal-revision:sha256:" + "0" * 64})

    def test_decision_rules_require_only_the_matching_relation(self):
        accepted = _accept()
        valid = {
            "retire": {"retires_revision_ids": [accepted.revision_id]},
            "supersede": {"supersedes_revision_ids": [accepted.revision_id]},
            "contradict": {"contradicts_revision_ids": [accepted.revision_id]},
            "qualify": {"qualification_of_revision_ids": [accepted.revision_id]},
        }
        for decision, relation in valid.items():
            with self.subTest(decision=decision):
                revision = _accept(decision=decision, **relation)
                self.assertEqual(revision.decision, decision)
        with self.assertRaises(TemporalContractError):
            _accept(decision="accept", retires_revision_ids=[accepted.revision_id])
        with self.assertRaises(TemporalContractError):
            _accept(decision="retire")
        with self.assertRaises(TemporalContractError):
            _accept(subject={"kind": "literal", "datatype": "type:text", "value": "Alex"})

    def test_limits_authority_evidence_and_ids_are_strict(self):
        with self.assertRaises(TemporalContractError):
            _accept(candidate_ids=["temporal-candidate:sha256:" + f"{index:064x}" for index in range(65)])
        with self.assertRaises(TemporalContractError):
            _accept(observation_ids=["temporal-observation:sha256:" + f"{index:064x}" for index in range(65)])
        with self.assertRaises(TemporalContractError):
            _accept(steward_evidence_refs=["https://example.test/raw-payload"])
        with self.assertRaises(TemporalContractError):
            _accept(authority="llm_wiki")
        with self.assertRaises(TemporalContractError):
            parse_temporal_claim_revision({**_accept().to_dict(), "candidate_ids": []})


class TemporalHistoryAndFoldTest(unittest.TestCase):
    def test_frontmatter_preserves_append_order_and_requires_prior_targets(self):
        accepted = _accept(recorded_at="2026-08-10T00:00:00Z")
        retired = _accept(
            decision="retire",
            recorded_at="2026-08-10T00:00:01Z",
            retires_revision_ids=[accepted.revision_id],
        )
        parsed = parse_temporal_claim_revisions(
            {"temporal_claim_revisions": [accepted.to_dict(), retired.to_dict()]}
        )
        self.assertEqual(parsed, (accepted, retired))
        with self.assertRaises(TemporalContractError):
            parse_temporal_claim_revisions(
                {"temporal_claim_revisions": [retired.to_dict(), accepted.to_dict()]}
            )

    def test_history_requires_same_claim_key_for_close_relations(self):
        accepted = _accept()
        other = _accept(subject=_subject("people/other.md"), recorded_at="2026-08-10T00:00:01Z")
        supersede = _accept(
            decision="supersede",
            recorded_at="2026-08-10T00:00:02Z",
            supersedes_revision_ids=[accepted.revision_id],
        )
        self.assertEqual(parse_temporal_claim_revisions([accepted.to_dict(), supersede.to_dict()]), (accepted, supersede))
        wrong_target = _accept(
            decision="supersede",
            recorded_at="2026-08-10T00:00:02Z",
            supersedes_revision_ids=[other.revision_id],
        )
        with self.assertRaises(TemporalContractError):
            parse_temporal_claim_revisions([accepted.to_dict(), other.to_dict(), wrong_target.to_dict()])

    def test_known_time_fold_handles_late_acceptance_retirement_supersession_contest_and_qualification(self):
        accepted = _accept(recorded_at="2026-08-10T00:00:00Z")
        late = _accept(recorded_at="2026-08-11T00:00:00Z")
        retired = _accept(decision="retire", recorded_at="2026-08-10T00:00:01Z", retires_revision_ids=[accepted.revision_id])
        fold = fold_temporal_claim_revisions((accepted, retired, late), known_at="2026-08-10T00:00:02Z")
        self.assertEqual(fold.active_revision_ids, ())
        self.assertEqual(fold.retired_revision_ids, (accepted.revision_id,))
        self.assertEqual(fold.complete_lineage_revision_ids, tuple(sorted((accepted.revision_id, retired.revision_id))))
        self.assertNotIn(late.revision_id, fold.complete_lineage_revision_ids)

        old = _accept(recorded_at="2026-08-12T00:00:00Z")
        replacement = _accept(
            decision="supersede", recorded_at="2026-08-12T00:00:01Z", supersedes_revision_ids=[old.revision_id]
        )
        contested = _accept(
            decision="contradict", recorded_at="2026-08-12T00:00:02Z", contradicts_revision_ids=[replacement.revision_id]
        )
        qualified = _accept(
            decision="qualify", recorded_at="2026-08-12T00:00:03Z", qualification_of_revision_ids=[replacement.revision_id],
            predicate="condition:staffed_hours",
        )
        second = fold_temporal_claim_revisions((old, replacement, contested, qualified), known_at="2026-08-12T00:00:04Z")
        self.assertEqual(
            second.active_revision_ids,
            tuple(sorted((replacement.revision_id, contested.revision_id, qualified.revision_id))),
        )
        self.assertEqual(second.superseded_revision_ids, (old.revision_id,))
        self.assertEqual(second.contested_revision_ids, tuple(sorted((contested.revision_id, replacement.revision_id))))
        self.assertEqual(second.qualified_revision_ids, (qualified.revision_id,))

    def test_fold_round_trip_and_output_limit(self):
        accepted = _accept()
        fold = fold_temporal_claim_revisions((accepted,), known_at="2026-08-10T00:00:00Z")
        self.assertIsInstance(fold, TemporalRevisionFold)
        self.assertEqual(fold.to_dict()["known_at"], "2026-08-10T00:00:00Z")
        self.assertEqual(
            fold_temporal_claim_revisions((accepted,), known_at="2026-08-09T00:00:00Z").active_revision_ids,
            (),
        )


class TemporalPersistenceFixtureTest(unittest.TestCase):
    def test_fixture_covers_required_histories_and_invalid_cases(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(payload["contract_version"], "temporal-claim-persistence-fixtures/1")
        names = {case["name"] for case in payload["cases"]}
        self.assertTrue({"accept", "retire", "supersede", "contradict", "qualify"} <= names)
        self.assertTrue(any(not case["valid"] for case in payload["cases"]))

    def test_fixture_histories_execute_as_valid_or_invalid(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        relation_fields = {
            "retire": "retires_revision_ids",
            "supersede": "supersedes_revision_ids",
            "contradict": "contradicts_revision_ids",
            "qualify": "qualification_of_revision_ids",
        }
        for case in payload["cases"]:
            built = {}
            raised = None
            try:
                for event in case["events"]:
                    subject = _subject("people/other.md") if event.get("subject") == "other" else _subject()
                    if event.get("subject") == "literal":
                        subject = {"kind": "literal", "datatype": "type:text", "value": "Alex"}
                    target_ids = [built[target].revision_id for target in event.get("targets", [])]
                    kwargs = {
                        "decision": event["decision"],
                        "recorded_at": event["recorded_at"],
                        "subject": subject,
                        "predicate": event.get("predicate", "status:has_state"),
                        "authority": event.get("authority", "target_wiki_steward"),
                    }
                    if event["decision"] in relation_fields:
                        kwargs[relation_fields[event["decision"]]] = target_ids
                    built[event["id"]] = _accept(**kwargs)
                parse_temporal_claim_revisions([revision.to_dict() for revision in built.values()])
            except (TemporalContractError, KeyError) as exc:
                raised = exc
            self.assertEqual(case["valid"], raised is None, msg=case["name"])

    def test_persistence_module_has_no_write_or_model_boundary(self):
        import llm_wiki_core.temporal_persistence as persistence

        source = inspect.getsource(persistence)
        self.assertNotIn("llm_wiki_mcp", source)
        self.assertNotIn("requests.", source)
        self.assertNotIn("urllib.", source)
        self.assertNotIn("model_call", source)
        self.assertNotRegex(source, r"\bopen\s*\(")

    def test_two_hundred_warm_folds_of_one_hundred_revisions_are_bounded(self):
        revisions = tuple(
            _accept(recorded_at=f"2026-08-10T00:{index // 60:02d}:{index % 60:02d}Z", predicate=f"status:value_{index:03d}")
            for index in range(100)
        )
        fold_temporal_claim_revisions(revisions, known_at="2026-08-10T01:00:00Z")
        timings = []
        for _ in range(200):
            started = time.perf_counter_ns()
            fold_temporal_claim_revisions(revisions, known_at="2026-08-10T01:00:00Z")
            timings.append((time.perf_counter_ns() - started) / 1_000_000)
        timings.sort()
        self.assertLess(timings[int(len(timings) * 0.95)], 10.0)


if __name__ == "__main__":
    unittest.main()
