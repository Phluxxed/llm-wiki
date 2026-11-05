from datetime import datetime, timezone
import json
import unittest

import yaml

from llm_wiki_core.okf import has_metadata, interpret_metadata, local_source_refs, validate_metadata


class OkfMetadataTest(unittest.TestCase):
    def test_yaml_dates_and_single_verifier_are_json_safe_without_mutation(self):
        frontmatter = yaml.safe_load("""
type: article
generated: {by: writer/v2, at: 2026-10-01T00:00:00Z, extension: [keep]}
verified: {by: 'human:vik', at: 2026-10-02T00:00:00Z}
sources:
  - {id: spec, resource: /sources/spec.md, last_modified: 2026-09-30T00:00:00Z}
usage_window: {from: 2026-09-01T00:00:00Z, to: 2026-10-01T00:00:00Z}
""")
        original = yaml.safe_dump(frontmatter)

        metadata = interpret_metadata(frontmatter, as_of="2026-10-03T00:00:00Z")

        self.assertEqual(validate_metadata(frontmatter), [])
        self.assertEqual(metadata["trust"], "human-reviewed")
        self.assertEqual(metadata["freshness"], "unknown")
        self.assertIsInstance(metadata["verified"], list)
        self.assertEqual(metadata["generated"]["extension"], ["keep"])
        self.assertIsInstance(metadata["sources"][0]["last_modified"], str)
        json.dumps(metadata, allow_nan=False)
        self.assertEqual(yaml.safe_dump(frontmatter), original)
        self.assertIsInstance(frontmatter["verified"], dict)

    def test_optional_families_are_not_required_or_inferred_from_legacy_dates(self):
        frontmatter = {"type": "article", "timestamp": "2026-10-01T00:00:00Z", "last_reviewed": "2026-10-02"}
        metadata = interpret_metadata(frontmatter, as_of="2026-10-03T00:00:00Z")
        self.assertEqual(validate_metadata(frontmatter), [])
        self.assertFalse(has_metadata(frontmatter))
        self.assertIsNone(metadata["generated"])
        self.assertEqual(metadata["verified"], [])
        self.assertEqual(metadata["trust"], "unverified")
        self.assertEqual(metadata["freshness"], "unknown")
        self.assertEqual(metadata["status"], "stable")

    def test_invalid_optional_shapes_remain_readable_and_never_earn_trust(self):
        for invalid in (
            {"generated": []}, {"generated": {"at": "2026-10-01T00:00:00Z"}},
            {"verified": "human:vik"}, {"verified": [{"by": "human:vik"}]},
            {"status": {}}, {"status": ["stable"]}, {"sources": "spec"},
            {"sources": [{"resource": "https://example.com", "usage_count": float("nan")}]},
            {"stale_after": "2026-10-03"}, {"stale_after": "2026-10-03T00:00:00"},
            {"usage_window": {"from": "bad", "to": "bad"}},
        ):
            with self.subTest(invalid=invalid):
                frontmatter = {"verified": {"by": "human:vik", "at": "2026-10-02T00:00:00Z"}, **invalid}
                metadata = interpret_metadata(frontmatter, as_of="2026-10-03T00:00:00Z")
                self.assertTrue(metadata["errors"])
                self.assertEqual(metadata["trust"], "unverified")
                self.assertIn("okf_metadata_invalid", metadata["flags"])
                json.dumps(metadata, allow_nan=False)

    def test_reverification_and_known_time_control_current_content_trust(self):
        frontmatter = {
            "generated": {"by": "writer/v2", "at": "2026-10-02T00:00:00Z"},
            "verified": [
                {"by": "human:vik", "at": "2026-10-01T00:00:00Z"},
                {"by": "process:check", "at": "2026-10-03T00:00:00Z"},
                {"by": "human:vik", "at": "2026-10-04T00:00:00Z"},
            ],
        }
        current = interpret_metadata(frontmatter, as_of="2026-10-05T00:00:00Z")
        historical = interpret_metadata(frontmatter, as_of="2026-10-05T00:00:00Z", known_at="2026-10-03T00:00:00Z")
        before_reverification = interpret_metadata(frontmatter, as_of="2026-10-02T12:00:00Z")
        self.assertEqual(current["trust"], "human-reviewed")
        self.assertEqual(historical["trust"], "machine-confirmed")
        self.assertEqual(before_reverification["trust"], "unverified")
        self.assertIn("verification_predates_generation", current["flags"])
        self.assertIn("verification_after_known_at", historical["flags"])

    def test_staleness_uses_absolute_instant_and_inclusive_boundary(self):
        frontmatter = {"stale_after": "2026-10-03T10:00:00+10:00"}
        self.assertEqual(interpret_metadata(frontmatter, as_of="2026-10-02T23:59:59Z")["freshness"], "fresh")
        self.assertEqual(interpret_metadata(frontmatter, as_of=datetime(2026, 10, 3, tzinfo=timezone.utc))["freshness"], "stale")

    def test_local_sources_preserve_grounding_and_do_not_follow_urls_or_scopes(self):
        frontmatter = {
            "source": "sources/original.md", "evidence": "sources/evidence.txt",
            "sources": [
                {"resource": "/sources/original.md"}, {"resource": "../sources/second.md"},
                {"resource": "https://example.com/source"}, {"resource": "all queries in project X"},
                {"resource": "../../outside.md"},
            ],
        }
        self.assertEqual(local_source_refs(frontmatter, page_path="notes/topic.md"), (
            "sources/original.md", "sources/evidence.txt", "sources/second.md",
        ))

    def test_attested_computation_requires_runtime_but_never_executes_metadata(self):
        frontmatter = {"type": "Attested Computation", "executor": {"resource": "/scripts/do-not-run.py"}}
        self.assertTrue(any("runtime" in error for error in validate_metadata(frontmatter)))
        self.assertEqual(validate_metadata({**frontmatter, "runtime": "python"}), [])


if __name__ == "__main__":
    unittest.main()
