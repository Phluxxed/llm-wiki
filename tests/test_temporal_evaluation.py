from __future__ import annotations

import hashlib
import json
import unittest
from datetime import date
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "temporal" / "scenarios.json"


def _date(value: str) -> date | None:
    return None if value in {"open"} or value.startswith("unknown:") else date.fromisoformat(value)


def _contains(interval: list[str], point: str) -> bool:
    start, end = interval
    if start.startswith("unknown:"):
        return False
    at = date.fromisoformat(point)
    lower = _date(start)
    upper = _date(end)
    return lower <= at and (upper is None or at < upper)


def _eligible(scenario: dict, query: dict) -> list[str]:
    if query["view"] == "transition":
        return []
    if query["view"] == "lineage":
        return [revision["id"] for revision in scenario["revisions"]]
    world_at = query.get("world_at", query.get("at"))
    known_at = query.get("known_at", query.get("at"))
    result = []
    for revision in scenario["revisions"]:
        accepted = revision["status"] == "accepted" or (
            query["view"] == "historical" and revision["status"] == "retired"
        )
        if query["view"] == "conflict":
            accepted = revision["status"] in {"accepted", "retired"}
        if revision.get("identity") == "ambiguous" or not accepted:
            continue
        if _contains(revision["world"], world_at) and _contains(revision["known"], known_at):
            result.append(revision["id"])
    return result


class TemporalEvaluationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.scenarios = {item["name"]: item for item in cls.payload["scenarios"]}

    def test_fixture_is_versioned_and_covers_every_wp_t0_scenario(self):
        expected = {
            "new_fact", "correction", "supersession", "retirement_without_replacement",
            "contradiction", "qualification", "duplicate_observation", "late_arrival",
            "backdated_validity", "ambiguous_identity", "unknown_time_is_not_open",
        }
        self.assertEqual(set(self.scenarios), expected)
        self.assertEqual(self.payload["contract_version"], "temporal-evaluation-0")

    def test_golden_eligibility_and_lineage_are_explicit(self):
        for scenario in self.scenarios.values():
            with self.subTest(scenario=scenario["name"]):
                self.assertTrue(scenario["observations"])
                self.assertTrue(scenario["revisions"])
                for query in scenario["queries"]:
                    self.assertIn("expected_eligible", query)
                    self.assertEqual(_eligible(scenario, query), query["expected_eligible"])
                    if "expected_lineage" in query:
                        lineage = {revision["id"] for revision in scenario["revisions"]}
                        lineage.update(observation["id"] for observation in scenario["observations"])
                        self.assertTrue(set(query["expected_lineage"]).issubset(lineage))

    def test_current_historical_transition_views_and_bitemporal_pair(self):
        self.assertEqual(_eligible(self.scenarios["retirement_without_replacement"], {"view": "current", "at": "2025-06-01"}), [])
        self.assertEqual(_eligible(self.scenarios["retirement_without_replacement"], {"view": "historical", "world_at": "2025-01-01", "known_at": "2025-02-01"}), ["rev:retire:1"])
        transition = self.scenarios["supersession"]["queries"][0]
        self.assertEqual(transition["expected_before"], ["rev:supersession:a"])
        self.assertEqual(transition["expected_after"], ["rev:supersession:b"])
        late = self.scenarios["late_arrival"]["queries"]
        self.assertEqual(late[0]["expected_eligible"], [])
        self.assertEqual(late[1]["expected_eligible"], ["rev:late:1"])

    def test_exact_provenance_recovery_and_qualification_context(self):
        for scenario in self.scenarios.values():
            observations = {item["id"]: item for item in scenario["observations"]}
            for revision in scenario["revisions"]:
                for observation_id in revision["observations"]:
                    self.assertIn(observation_id, observations)
        qualification = self.scenarios["qualification"]["queries"][0]
        self.assertEqual(qualification["expected_qualification"], {"rev:qualification:claim": ["only during staffed hours"]})
        provenance = self.scenarios["new_fact"]["observations"][0]
        self.assertEqual(provenance["source"], "sources/status.md")
        self.assertEqual(provenance["locator"], "L10")
        self.assertEqual(provenance["content_hash"], "hash-new-1")

    def test_duplicate_observation_identity_is_deterministic(self):
        scenario = self.scenarios["duplicate_observation"]
        observations = scenario["observations"]
        fingerprints = {
            hashlib.sha256(json.dumps({"source": item["source"], "locator": item["locator"], "content_hash": item["content_hash"]}, sort_keys=True).encode()).hexdigest()
            for item in observations
        }
        self.assertEqual(len(fingerprints), 1)
        self.assertEqual(scenario["dedupe"]["expected_unique_id"], "obs:duplicate:canonical")


if __name__ == "__main__":
    unittest.main()
