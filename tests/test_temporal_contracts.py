from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.temporal import (
    EntityRef,
    TemporalContractError,
    TemporalCandidatePacket,
    TimeInterval,
    TimeValue,
    build_observation_ref,
    build_temporal_fact_candidate,
    parse_observation_ref,
    parse_temporal_candidate_packet,
    parse_temporal_fact_candidate,
)
from llm_wiki_core.maintenance import build_temporal_candidate_packet


class TimeAndEntityContractTest(unittest.TestCase):
    def test_time_values_round_trip_and_normalize(self):
        self.assertEqual(
            TimeValue.from_mapping({"kind": "known", "value": "2026-08-10"}).to_dict(),
            {"kind": "known", "value": "2026-08-10"},
        )
        self.assertEqual(
            TimeValue.from_mapping(
                {"kind": "known", "value": "2026-08-10T04:30:00+10:00"}
            ).to_dict(),
            {"kind": "known", "value": "2026-08-09T18:30:00Z"},
        )
        self.assertEqual(TimeValue.from_mapping({"kind": "open"}).to_dict(), {"kind": "open"})
        self.assertEqual(
            TimeValue.from_mapping(
                {"kind": "unknown", "reason": "source_did_not_state_time"}
            ).to_dict(),
            {"kind": "unknown", "reason": "source_did_not_state_time"},
        )

    def test_intervals_preserve_unknown_and_open_distinctions(self):
        interval = TimeInterval.from_mapping(
            {
                "from": {"kind": "unknown", "reason": "not stated"},
                "to": {"kind": "open"},
            }
        )
        self.assertEqual(
            interval.to_dict(),
            {
                "from": {"kind": "unknown", "reason": "not stated"},
                "to": {"kind": "open"},
            },
        )

    def test_rejects_invalid_time_shapes_and_order(self):
        invalid = [
            {"kind": "known", "value": "2026-08-10T04:30:00"},
            {"kind": "known", "value": "2026-08-10T04:30:00.123Z"},
            {"kind": "open", "reason": "no"},
            {"kind": "unknown", "value": "2026-08-10", "reason": "no"},
            {"kind": "unknown", "reason": ""},
            {"kind": "known", "value": "2026-08-10", "extra": True},
        ]
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(TemporalContractError):
                    TimeValue.from_mapping(raw)
        with self.assertRaises(TemporalContractError):
            TimeInterval.from_mapping(
                {
                    "from": {"kind": "open"},
                    "to": {"kind": "known", "value": "2026-08-11"},
                }
            )
        with self.assertRaises(TemporalContractError):
            TimeInterval.from_mapping(
                {
                    "from": {"kind": "known", "value": "2026-08-11"},
                    "to": {"kind": "known", "value": "2026-08-10"},
                }
            )

    def test_entity_refs_round_trip_and_retain_ranked_ambiguity(self):
        resolved = EntityRef.from_mapping({"kind": "resolved_page", "page": "./notes\\a.md"})
        self.assertEqual(resolved.to_dict(), {"kind": "resolved_page", "page": "notes/a.md"})
        ambiguous = EntityRef.from_mapping(
            {
                "kind": "ambiguous",
                "surface": "Alex",
                "candidates": [
                    {
                        "ref": {"kind": "external_id", "namespace": "person", "value": "2"},
                        "observation_ids": ["temporal-observation:sha256:" + "b" * 64],
                    },
                    {
                        "ref": {"kind": "resolved_page", "page": "people/alex.md"},
                        "observation_ids": ["temporal-observation:sha256:" + "a" * 64],
                    },
                ],
            }
        )
        self.assertEqual(ambiguous.to_dict()["candidates"][0]["ref"]["kind"], "external_id")
        self.assertEqual(ambiguous.to_dict()["candidates"][1]["ref"]["page"], "people/alex.md")

    def test_rejects_unsafe_pages_and_nested_or_literal_ambiguity(self):
        for page in ("/absolute.md", "../outside.md", "a/../b.md", "a//b.md", "a/./b.md", ""):
            with self.subTest(page=page):
                with self.assertRaises(TemporalContractError):
                    EntityRef.from_mapping({"kind": "resolved_page", "page": page})
        base = {"kind": "ambiguous", "surface": "x", "candidates": []}
        for candidate in (
            {"ref": {"kind": "ambiguous", "surface": "y", "candidates": []}, "observation_ids": ["x"]},
            {"ref": {"kind": "literal", "datatype": "type:text", "value": "x"}, "observation_ids": ["x"]},
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(TemporalContractError):
                    EntityRef.from_mapping({**base, "candidates": [candidate]})


class ObservationRefContractTest(unittest.TestCase):
    def _build(self, **changes):
        values = {
            "source_kind": "source:manual",
            "source_ref": "sources/status.md",
            "locator": {"line": 10, "section": "status"},
            "input_type": "input:markdown",
            "observed_at": "2026-08-10T04:30:00+10:00",
            "source_event_time": {"kind": "unknown", "reason": "not stated"},
            "retention": "immutable_source",
            "payload": b"hello",
            "unknowns": [{"field": "time:source_event", "reason": "not stated"}],
        }
        values.update(changes)
        return build_observation_ref(**values)

    def test_payload_and_precomputed_forms_round_trip_to_same_id(self):
        payload_ref = self._build()
        precomputed_ref = self._build(
            payload=None,
            content_hash=payload_ref.content_hash,
            payload_bytes=payload_ref.payload_bytes,
            observed_at="2026-08-11T04:30:00+10:00",
        )
        self.assertEqual(payload_ref.observation_id, precomputed_ref.observation_id)
        self.assertEqual(payload_ref.to_dict(), parse_observation_ref(payload_ref.to_dict()).to_dict())
        self.assertEqual(payload_ref.to_dict()["payload_bytes"], 5)
        self.assertEqual(payload_ref.to_dict()["observed_at"], "2026-08-09T18:30:00Z")

    def test_locator_or_content_changes_change_the_id(self):
        first = self._build()
        self.assertNotEqual(first.observation_id, self._build(locator={"line": 11}).observation_id)
        self.assertNotEqual(first.observation_id, self._build(payload=b"other").observation_id)

    def test_rejects_dual_or_missing_payload_forms_and_invalid_records(self):
        valid = self._build()
        invalid = [
            {"payload": None},
            {"payload": b"x", "content_hash": valid.content_hash, "payload_bytes": 1},
            {"payload": None, "content_hash": valid.content_hash},
            {"payload": None, "content_hash": "f" * 63, "payload_bytes": 1},
            {"source_event_time": {"kind": "open"}},
        ]
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(TemporalContractError):
                    self._build(**changes)
        with self.assertRaises(TemporalContractError):
            parse_observation_ref({**valid.to_dict(), "observation_id": "temporal-observation:sha256:" + "0" * 64})
        with self.assertRaises(TemporalContractError):
            self._build(payload=b"x" * 65537)


class TemporalFactCandidateContractTest(unittest.TestCase):
    OBS_A = "temporal-observation:sha256:" + "a" * 64
    OBS_B = "temporal-observation:sha256:" + "b" * 64
    TARGET = "temporal-candidate:sha256:" + "c" * 64

    def _build(self, **changes):
        values = {
            "subject": {"kind": "resolved_page", "page": "people/alex.md"},
            "predicate": "status:has_state",
            "object_ref": {"kind": "literal", "datatype": "type:text", "value": "ready"},
            "proposed_world_validity": {
                "from": {"kind": "known", "value": "2026-01-01"},
                "to": {"kind": "open"},
            },
            "observed_at": "2026-08-10T00:00:00Z",
            "proposed_at": "2026-08-10T00:00:01Z",
            "supporting_observation_ids": [self.OBS_B, self.OBS_A, self.OBS_A],
            "conflicting_observation_ids": [],
            "proposed_relations": [
                {"kind": "duplicate", "target_id": self.TARGET, "observation_ids": [self.OBS_B, self.OBS_A]}
            ],
            "signals": [{"kind": "signal:deterministic", "observation_ids": [self.OBS_A]}],
            "unknowns": [],
        }
        values.update(changes)
        return build_temporal_fact_candidate(**values)

    def test_set_like_input_and_timestamp_or_usage_changes_preserve_identity(self):
        first = self._build()
        second = self._build(
            supporting_observation_ids=[self.OBS_A, self.OBS_B],
            proposed_relations=[
                {"kind": "duplicate", "target_id": self.TARGET, "observation_ids": [self.OBS_A, self.OBS_B]}
            ],
            observed_at="2026-08-10T00:00:02+00:00",
            proposed_at="2026-08-10T00:00:03+00:00",
            usage={"payload_bytes": 10, "model_calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_ms": 2.5},
        )
        self.assertEqual(first.candidate_id, second.candidate_id)
        self.assertEqual(first.claim_key, self._build(object_ref={"kind": "literal", "datatype": "type:text", "value": "other"}).claim_key)
        self.assertEqual(first.disposition, "candidate_only")
        self.assertEqual(first.mutation, {"allowed": False, "commands": []})

    def test_claim_or_interval_evidence_relation_signal_or_unknown_changes_identity(self):
        first = self._build()
        changes = [
            {"predicate": "status:other"},
            {"proposed_world_validity": {"from": {"kind": "known", "value": "2027-01-01"}, "to": {"kind": "open"}}},
            {"supporting_observation_ids": [self.OBS_A]},
            {"proposed_relations": []},
            {"signals": []},
            {"unknowns": [{"field": "fact:time", "reason": "not stated"}]},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.assertNotEqual(first.candidate_id, self._build(**change).candidate_id)

    def test_rejects_ambiguous_overlap_invalid_time_confidence_and_mutation(self):
        ambiguous_subject = {
            "kind": "ambiguous",
            "surface": "Alex",
            "candidates": [
                {"ref": {"kind": "resolved_page", "page": "people/alex.md"}, "observation_ids": [self.OBS_A]}
            ],
        }
        with self.assertRaises(TemporalContractError):
            self._build(subject={"kind": "literal", "datatype": "type:text", "value": "Alex"})
        self.assertEqual(self._build(subject=ambiguous_subject).subject.kind, "ambiguous")
        for change in (
            {"conflicting_observation_ids": [self.OBS_A]},
            {"proposed_world_validity": {"from": {"kind": "known", "value": "2026-01-02"}, "to": {"kind": "known", "value": "2026-01-01"}}},
            {"signals": [{"kind": "signal:x", "observation_ids": [self.OBS_A], "confidence": 0.9}]},
            {"usage": {"payload_bytes": 0, "model_calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_ms": 0}, "mutation": {"allowed": True, "commands": ["write"]}},
        ):
            with self.subTest(change=change):
                with self.assertRaises((TemporalContractError, TypeError)):
                    self._build(**change)

    def test_parser_round_trip_and_mismatched_or_oversized_id_fail(self):
        candidate = self._build()
        self.assertEqual(candidate.to_dict(), parse_temporal_fact_candidate(candidate.to_dict()).to_dict())
        with self.assertRaises(TemporalContractError):
            parse_temporal_fact_candidate({**candidate.to_dict(), "candidate_id": "temporal-candidate:sha256:" + "0" * 64})
        with self.assertRaises(TemporalContractError):
            self._build(unknowns=[{"field": "fact:large", "reason": "x" * 512} for _ in range(32)])


class TemporalCandidatePacketContractTest(unittest.TestCase):
    def _candidate(self, predicate="status:has_state"):
        return build_temporal_fact_candidate(
            subject={"kind": "resolved_page", "page": "notes/a.md"},
            predicate=predicate,
            object_ref={"kind": "literal", "datatype": "type:text", "value": "ready"},
            proposed_world_validity={"from": {"kind": "known", "value": "2026-01-01"}, "to": {"kind": "open"}},
            observed_at="2026-08-10T00:00:00Z",
            proposed_at="2026-08-10T00:00:01Z",
            supporting_observation_ids=["temporal-observation:sha256:" + "a" * 64],
            usage={"payload_bytes": 10, "model_calls": 0, "input_tokens": 2, "output_tokens": 3, "latency_ms": 1.5},
        )

    def test_order_and_duplicate_invariance_and_usage_sum(self):
        first_candidate = self._candidate("status:first")
        second_candidate = self._candidate("status:second")
        first = build_temporal_candidate_packet(
            alias="brain-test",
            candidates=[second_candidate, first_candidate, first_candidate],
            generated_at="2026-08-10T00:01:00Z",
        )
        second = build_temporal_candidate_packet(
            alias="brain-test",
            candidates=[first_candidate, second_candidate],
            generated_at="2026-08-11T00:01:00Z",
        )
        self.assertEqual(first["packet_id"], second["packet_id"])
        self.assertEqual(first["candidates"], second["candidates"])
        self.assertEqual(first["status"], "candidates_present")
        self.assertEqual(first["usage"], {"payload_bytes": 20, "model_calls": 0, "input_tokens": 4, "output_tokens": 6, "latency_ms": 3.0})
        self.assertEqual(first["mutation"], {"allowed": False, "commands": []})
        self.assertEqual(first["disposition"], "candidate_only")
        self.assertEqual(parse_temporal_candidate_packet(first).to_dict(), first)

    def test_empty_output_is_explicitly_not_clean(self):
        packet = build_temporal_candidate_packet(alias="brain-test", candidates=[], generated_at="2026-08-10T00:01:00Z")
        self.assertEqual(packet["status"], "no_candidates_observed")
        self.assertNotEqual(packet["status"], "clean")
        self.assertEqual(packet["candidates"], [])
        self.assertEqual(packet["usage"], {"payload_bytes": 0, "model_calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_ms": 0})

    def test_rejects_bad_packet_version_id_and_alias(self):
        candidate = self._candidate()
        packet = build_temporal_candidate_packet(alias="brain-test", candidates=[candidate], generated_at="2026-08-10T00:01:00Z")
        for raw in (
            {**packet, "contract_version": "temporal-candidate-packet/2"},
            {**packet, "packet_id": "temporal-candidate-packet:sha256:" + "0" * 64},
            {**packet, "mutation": {"allowed": True, "commands": ["write"]}},
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(TemporalContractError):
                    parse_temporal_candidate_packet(raw)
        with self.assertRaises(TemporalContractError):
            build_temporal_candidate_packet(alias="../unsafe", candidates=[candidate], generated_at="2026-08-10T00:01:00Z")
        with self.assertRaises(TemporalContractError):
            build_temporal_candidate_packet(alias="brain-test", candidates=[candidate] * 257, generated_at="2026-08-10T00:01:00Z")


if __name__ == "__main__":
    unittest.main()
