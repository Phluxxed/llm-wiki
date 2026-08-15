from __future__ import annotations

import statistics
import tempfile
import time
import unittest
from datetime import datetime, timezone
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.compiler import compile_context
from llm_wiki_core.contracts import CompileRequest, TemporalQuery, TemporalTransition
from llm_wiki_core.temporal import TemporalContractError
from llm_wiki_core.temporal_persistence import (
    build_temporal_claim_revision,
    eligible_temporal_revisions,
    render_temporal_revision,
)


OBS = "temporal-observation:sha256:" + "a" * 64
CAND = "temporal-candidate:sha256:" + "b" * 64


def _revision(*, decision="accept", recorded_at="2026-01-02T00:00:00Z", world_from="2025-01-01", world_to=None, predicate="status:has_state", **relations):
    from_value = (
        {"kind": "unknown", "reason": world_from}
        if world_from.startswith("unknown:")
        else {"kind": "known", "value": world_from}
    )
    return build_temporal_claim_revision(
        subject={"kind": "resolved_page", "page": "status.md"},
        predicate=predicate,
        object_ref={"kind": "literal", "datatype": "type:text", "value": "ready"},
        world_validity={
            "from": from_value,
            "to": {"kind": "open"} if world_to is None else {"kind": "known", "value": world_to},
        },
        recorded_at=recorded_at,
        candidate_ids=[CAND],
        observation_ids=[OBS],
        steward_evidence_refs=["status.md"],
        decision=decision,
        **relations,
    )


class TemporalSelectionTest(unittest.TestCase):
    def test_raw_mapping_history_is_validated_before_eligibility(self):
        accepted = _revision(recorded_at="2026-01-02T00:00:00Z")
        retired = _revision(
            decision="retire",
            recorded_at="2026-01-03T00:00:00Z",
            retires_revision_ids=[accepted.revision_id],
        )
        query = TemporalQuery("current", "2027-01-01T00:00:00Z", "2026-01-01", "2027-01-01T00:00:00Z")
        with self.assertRaises(TemporalContractError):
            eligible_temporal_revisions((retired.to_dict(), accepted.to_dict()), query)

    def test_current_and_historical_apply_known_and_world_time(self):
        revision = _revision(world_from="2025-01-01", world_to="2026-01-01")
        historical = TemporalQuery("historical", "2026-02-01T00:00:00Z", "2025-06-01", "2026-02-01T00:00:00Z")
        current = TemporalQuery("current", "2026-02-01T00:00:00Z", "2026-02-01T00:00:00Z", "2026-02-01T00:00:00Z")
        self.assertEqual(eligible_temporal_revisions((revision,), historical), (revision,))
        self.assertEqual(eligible_temporal_revisions((revision,), current), ())

    def test_late_acceptance_and_unknown_world_time_fail_closed(self):
        late = _revision(recorded_at="2027-01-01T00:00:00Z")
        current_before_known = TemporalQuery("current", "2026-06-01T00:00:00Z", "2026-06-01T00:00:00Z", "2026-06-01T00:00:00Z")
        current_after_known = TemporalQuery("current", "2027-02-01T00:00:00Z", "2026-06-01T00:00:00Z", "2027-02-01T00:00:00Z")
        self.assertEqual(eligible_temporal_revisions((late,), current_before_known), ())
        self.assertEqual(eligible_temporal_revisions((late,), current_after_known), (late,))

        unknown = _revision(world_from="unknown:missing-valid-from")
        unknown_query = TemporalQuery("current", "2027-02-01T00:00:00Z", "2027-02-01T00:00:00Z", "2027-02-01T00:00:00Z")
        lineage = TemporalQuery("lineage", "2027-02-01T00:00:00Z", None, "2027-02-01T00:00:00Z")
        self.assertEqual(eligible_temporal_revisions((unknown,), unknown_query), ())
        self.assertEqual(eligible_temporal_revisions((unknown,), lineage), (unknown,))

    def test_retirement_supersession_and_conflict_views(self):
        old = _revision(world_from="2025-01-01", recorded_at="2025-01-02T00:00:00Z")
        retired = _revision(
            decision="retire", recorded_at="2026-01-02T00:00:00Z", retires_revision_ids=[old.revision_id]
        )
        historical = TemporalQuery("historical", "2026-01-01T00:00:00Z", "2025-06-01", "2026-01-01T00:00:00Z")
        current = TemporalQuery("current", "2027-01-01T00:00:00Z", "2025-06-01", "2027-01-01T00:00:00Z")
        self.assertEqual(eligible_temporal_revisions((old, retired), historical), (old,))
        self.assertEqual(eligible_temporal_revisions((old, retired), current), ())

        conflict = _revision(
            world_from="2025-01-01", recorded_at="2026-01-03T00:00:00Z", decision="contradict",
            contradicts_revision_ids=[old.revision_id],
        )
        conflict_query = TemporalQuery("conflict", "2027-01-01T00:00:00Z", "2025-06-01", "2027-01-01T00:00:00Z")
        selected = eligible_temporal_revisions((old, conflict), conflict_query)
        self.assertEqual({item.revision_id for item in selected}, {old.revision_id, conflict.revision_id})
        rendered = render_temporal_revision(selected[0], view="conflict")
        self.assertIn("contested", rendered)
        self.assertLessEqual(len(rendered.encode("utf-8")), 4000)

    def test_lineage_rendering_preserves_steward_evidence_refs(self):
        revision = _revision()

        rendered = render_temporal_revision(revision, view="lineage")

        self.assertIn('"steward_evidence_refs":["status.md"]', rendered)

    def test_supersede_establishes_replacement_across_views_and_conflict(self):
        old = _revision(world_from="2020-01-01", world_to="2025-01-01", recorded_at="2020-01-02T00:00:00Z")
        replacement = _revision(
            decision="supersede",
            world_from="2025-01-01",
            recorded_at="2025-01-02T00:00:00Z",
            supersedes_revision_ids=[old.revision_id],
            predicate="status:replacement",
        )
        current = TemporalQuery("current", "2026-01-01T00:00:00Z", "2026-01-01", "2026-01-01T00:00:00Z")
        historical = TemporalQuery("historical", "2026-01-01T00:00:00Z", "2025-06-01", "2026-01-01T00:00:00Z")
        transition = TemporalQuery(
            "transition", "2026-01-01T00:00:00Z", None, "2026-01-01T00:00:00Z",
            TemporalTransition("2025-06-01", "2025-07-01"),
        )
        self.assertEqual(eligible_temporal_revisions((old, replacement), current), (replacement,))
        self.assertEqual(eligible_temporal_revisions((old, replacement), historical), (replacement,))
        self.assertIn(replacement, eligible_temporal_revisions((old, replacement), transition))

        contradiction = _revision(
            decision="contradict",
            world_from="2025-01-01",
            recorded_at="2025-01-03T00:00:00Z",
            contradicts_revision_ids=[replacement.revision_id],
            predicate="status:replacement",
        )
        conflict = TemporalQuery("conflict", "2026-01-01T00:00:00Z", "2026-01-01", "2026-01-01T00:00:00Z")
        selected = eligible_temporal_revisions((old, replacement, contradiction), conflict)
        self.assertIn(replacement, selected)
        self.assertIn(contradiction, selected)

    def test_transition_uses_a_bounded_world_range(self):
        revision = _revision(world_from="2025-01-01", world_to="2026-01-01")
        transition = TemporalQuery(
            "transition", "2026-02-01T00:00:00Z", None, "2026-02-01T00:00:00Z",
            TemporalTransition("2025-06-01", "2025-07-01"),
        )
        outside = TemporalQuery(
            "transition", "2026-02-01T00:00:00Z", None, "2026-02-01T00:00:00Z",
            TemporalTransition("2026-01-01", "2026-02-01"),
        )
        self.assertEqual(eligible_temporal_revisions((revision,), transition), (revision,))
        self.assertEqual(eligible_temporal_revisions((revision,), outside), ())

    def test_eligibility_p95_stays_below_fifty_ms_for_ten_thousand_revisions(self):
        query = TemporalQuery("current", "2027-01-01T00:00:00Z", "2026-06-01", "2027-01-01T00:00:00Z")
        samples = []
        fixture_groups = tuple(
            tuple(
                _revision(
                    recorded_at=f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}Z",
                    world_from="2020-01-01",
                    predicate=f"status:value_{index:03d}",
                )
                for index in range(500)
            )
            for _ in range(20)
        )
        for _ in range(7):
            started = time.perf_counter_ns()
            for revisions in fixture_groups:
                eligible_temporal_revisions(revisions, query)
            samples.append((time.perf_counter_ns() - started) / 1_000_000)
        self.assertLessEqual(statistics.quantiles(samples, n=20, method="inclusive")[18], 50.0)

    def test_temporal_provider_excludes_ineligible_revisions_before_selection(self):
        from tests.wiki_fixture import base_fm, create_wiki_root, write_md

        old = _revision(world_from="2020-01-01", world_to="2025-01-01", recorded_at="2020-01-02T00:00:00Z")
        current = _revision(world_from="2025-01-01", recorded_at="2025-01-02T00:00:00Z", predicate="status:current")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = create_wiki_root(Path(tmpdir) / "wiki")
            write_md(
                root / "status.md",
                base_fm(title="Status", temporal_claim_revisions=[old.to_dict(), current.to_dict()]),
                "The stale prose says the service was ready.",
            )
            request = CompileRequest.from_mapping(
                {
                    "contract_version": "2",
                    "alias": "test",
                    "question": "What is the current status?",
                    "temporal": {
                        "view": "current",
                        "request_time": "2026-01-01T00:00:00Z",
                        "world_at": "2026-01-01",
                        "known_at": "2026-01-01T00:00:00Z",
                    },
                }
            )
            response = compile_context(root, request).to_dict()
        temporal = [item for item in response["evidence"] if item["provider"] == "temporal"]
        self.assertEqual(len(temporal), 1)
        self.assertEqual(temporal[0]["route"], "temporal_current")
        self.assertIn("temporal_accepted", temporal[0]["derived_flags"])
        self.assertIn("target_wiki_steward", temporal[0]["authority_signals"])
        self.assertNotIn(old.revision_id, temporal[0]["content"])

    def test_legacy_page_evidence_is_explicitly_unspecified(self):
        from tests.wiki_fixture import base_fm, create_wiki_root, write_md

        with tempfile.TemporaryDirectory() as tmpdir:
            root = create_wiki_root(Path(tmpdir) / "wiki")
            write_md(root / "status.md", base_fm(title="Status"), "The status is current.")
            request = CompileRequest.from_mapping({"alias": "test", "question": "What is the status?"})
            response = compile_context(root, request).to_dict()
        self.assertTrue(any("legacy_temporal_unspecified" in item["derived_flags"] for item in response["evidence"]))


if __name__ == "__main__":
    unittest.main()
