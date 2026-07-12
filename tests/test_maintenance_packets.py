from __future__ import annotations

from datetime import date
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.maintenance import build_maintenance_packet
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


if __name__ == "__main__":
    unittest.main()
