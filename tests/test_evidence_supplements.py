from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.compiler import compile_context
from llm_wiki_core.contracts import CompileRequest
from tests.wiki_fixture import base_fm, create_wiki_root, write_md


class EvidenceSupplementTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = create_wiki_root(Path(self.temporary.name) / "wiki", with_scripts=False)
        (self.root / ".llm-wiki.toml").write_text(
            'schema_version = "1"\nruntime_contract = "3"\n'
            '[compiler]\nproviders = ["text", "source"]\n',
            encoding="utf-8",
        )
        write_md(
            self.root / "overview.md",
            base_fm(title="Archive overview", source="sources/overview.md"),
            "## Overview\n\nArchive export requirements are documented for archive export operators.",
        )
        (self.root / "sources").mkdir()
        (self.root / "sources" / "overview.md").write_text(
            "Archive export requirements are reviewed by release owners.", encoding="utf-8"
        )
        write_md(
            self.root / "approval.md",
            base_fm(title="Approval rule"),
            "## Approval\n\nArchive export needs written approval before deleting source files.",
        )

    def request(self, **budget):
        return CompileRequest.from_mapping({
            "alias": "supplement-test",
            "question": "What are the archive export requirements?",
            "budget": {
                "target_bytes": 8_192, "max_bytes": 16_384,
                "target_items": 4, "max_items": 8, **budget,
            },
        })

    def test_needed_rule_survives_after_other_evidence_covers_its_role(self):
        response = compile_context(self.root, self.request()).to_dict()

        content = "\n".join(item["content"] for item in response["evidence"])
        self.assertIn("written approval before deleting source files", content)
        self.assertIn("Archive export requirements are reviewed by release owners.", content)

    def test_full_role_coverage_does_not_hide_a_budget_omission(self):
        response = compile_context(self.root, self.request(target_items=2, max_items=2)).to_dict()

        self.assertEqual(response["coverage"]["uncovered_roles"], [])
        self.assertEqual(response["stop"]["reason"], "item_budget_exhausted")
        self.assertTrue(response["stop"]["sufficient"])  # Legacy role coverage only.
        self.assertIn("text:approval.md#Approval", response["continuation"]["remaining_candidate_ids"])

    def test_complete_response_fitting_reports_a_dropped_supplement(self):
        write_md(
            self.root / "approval.md", base_fm(title="Approval rule"),
            "## Approval\n\nArchive export needs written approval. " + "Extra export detail. " * 150,
        )
        response = compile_context(
            self.root, self.request(target_bytes=4_000, max_bytes=4_000)
        ).to_dict()

        self.assertEqual(response["coverage"]["uncovered_roles"], [])
        self.assertEqual(response["stop"]["reason"], "byte_budget_exhausted")
        self.assertTrue(response["stop"]["sufficient"])
        self.assertGreater(response["continuation"].get("remaining_candidate_count", 0), 0)

    def test_complete_response_fitting_excerpt_reports_a_byte_limit_without_whole_drop(self):
        write_md(
            self.root / "approval.md", base_fm(title="Approval rule"),
            "## Approval\n\nArchive export needs written approval. " + "Extra export detail. " * 10,
        )
        response = compile_context(
            self.root, self.request(target_bytes=2_600, max_bytes=2_600)
        ).to_dict()

        rule = next(item for item in response["evidence"] if item["page"] == "approval.md")
        self.assertTrue(rule["truncated"])
        self.assertEqual(response["stop"]["reason"], "byte_budget_exhausted")
        self.assertTrue(response["stop"]["sufficient"])
        self.assertEqual(response["omissions"], [])
        self.assertIsNone(response["continuation"])

    def test_content_limit_is_reported_when_a_supplement_is_excerpted(self):
        write_md(
            self.root / "approval.md", base_fm(title="Approval rule"),
            "## Approval\n\nArchive export needs written approval. " + "Extra export detail. " * 100,
        )
        response = compile_context(self.root, self.request(max_content_bytes=400)).to_dict()

        rule = next(item for item in response["evidence"] if item["page"] == "approval.md")
        self.assertTrue(rule["truncated"])
        self.assertEqual(response["stop"]["reason"], "content_budget_exhausted")
        self.assertTrue(response["stop"]["sufficient"])


if __name__ == "__main__":
    unittest.main()
