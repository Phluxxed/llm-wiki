from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.compiler import compile_context
from llm_wiki_core.contracts import CompileRequest
from llm_wiki_core.providers.base import CandidateEvidence
from llm_wiki_core.selection import select_candidates
from tests.wiki_fixture import base_fm, write_md


class ProgressiveSelectionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "sources").mkdir()
        (self.root / "sources" / "runtime.md").write_text(
            "The canonical source says llm-wiki owns traversal behavior and propagation.",
            encoding="utf-8",
        )
        write_md(
            self.root / "systems" / "llm-wiki.md",
            base_fm(
                title="llm-wiki",
                source="sources/runtime.md",
                knowledge_state="current",
            ),
            "## Ownership\n\nllm-wiki owns traversal behavior.",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def compile(self, budget: dict):
        request = CompileRequest.from_mapping(
            {
                "alias": "test",
                "question": "Who owns traversal behavior?",
                "seeds": ["systems/llm-wiki.md"],
                "budget": budget,
            }
        )
        return compile_context(self.root, request).to_dict()

    def test_low_starting_target_expands_until_coverage_is_sufficient(self):
        response = self.compile(
            {
                "target_bytes": 100,
                "max_bytes": 10_000,
                "target_items": 1,
                "max_items": 10,
            }
        )

        self.assertTrue(response["stop"]["sufficient"])
        self.assertGreater(response["budget"]["evidence_bytes"], 100)
        self.assertGreater(response["budget"]["items"], 1)
        self.assertTrue(response["budget"]["target_exceeded_for_coverage"])
        self.assertIsNone(response["continuation"])
        self.assertEqual([item["provider"] for item in response["evidence"]], ["seed", "source"])

    def test_hard_byte_ceiling_returns_incomplete_continuation(self):
        response = self.compile(
            {
                "target_bytes": 100,
                "max_bytes": 100,
                "target_items": 10,
                "max_items": 10,
            }
        )

        self.assertFalse(response["stop"]["sufficient"])
        self.assertEqual(response["stop"]["reason"], "byte_budget_exhausted")
        self.assertTrue(response["coverage"]["uncovered_roles"])
        self.assertEqual(response["continuation"]["reason"], "hard_limit_reached")
        self.assertTrue(response["continuation"]["remaining_candidate_ids"])

    def test_hard_item_ceiling_is_reported_separately(self):
        response = self.compile(
            {
                "target_bytes": 10_000,
                "max_bytes": 10_000,
                "target_items": 1,
                "max_items": 1,
            }
        )

        self.assertFalse(response["stop"]["sufficient"])
        self.assertEqual(response["stop"]["reason"], "item_budget_exhausted")
        self.assertEqual(response["budget"]["items"], 1)

    def test_reported_record_and_envelope_bytes_match_serialized_output(self):
        response = self.compile(
            {
                "target_bytes": 10_000,
                "max_bytes": 10_000,
                "target_items": 10,
                "max_items": 10,
            }
        )

        serialized_items = [
            len(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            for item in response["evidence"]
        ]
        total = len(
            json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        self.assertEqual([item["byte_cost"] for item in response["evidence"]], serialized_items)
        self.assertEqual(response["budget"]["evidence_bytes"], sum(serialized_items))
        self.assertEqual(response["budget"]["envelope_bytes"], total - sum(serialized_items))
        self.assertEqual(response["budget"]["estimated_tokens"], (total + 3) // 4)
        self.assertIsNone(response["budget"]["limits"]["max_estimated_tokens"])

    def test_loci_is_the_primary_non_seed_route_for_equivalent_coverage(self):
        request = CompileRequest.from_mapping(
            {"alias": "test", "question": "What owns traversal?", "seeds": []}
        )
        candidates = [
            CandidateEvidence(
                id="frontmatter:systems/wiki.md",
                provider="frontmatter",
                route="metadata_match",
                page="systems/wiki.md",
                source=None,
                locator={"file": "systems/wiki.md"},
                content="Broad page content.",
                roles=("answer",),
                selection_signals=("frontmatter_match",),
                authored_state="current",
                derived_flags=(),
                authority_signals=(),
            ),
            CandidateEvidence(
                id="loci:systems/wiki.md::Ownership#section",
                provider="loci",
                route="indexed_section",
                page="systems/wiki.md",
                source=None,
                locator={"file": "systems/wiki.md", "start_line": 10, "end_line": 12},
                content="Exact ownership section.",
                roles=("answer",),
                selection_signals=("indexed_symbol_match",),
                authored_state="current",
                derived_flags=(),
                authority_signals=(),
            ),
        ]

        selected, _ = select_candidates(candidates, request, ("answer",))

        self.assertEqual([item.provider for item in selected], ["loci"])

    def test_loci_retrieval_rank_beats_lexicographic_id_order(self):
        request = CompileRequest.from_mapping(
            {"alias": "test", "question": "What owns traversal?", "seeds": []}
        )
        candidates = [
            CandidateEvidence(
                id="loci:z-relevant#section",
                provider="loci",
                route="indexed_section",
                page="systems/relevant.md",
                source=None,
                locator={"file": "systems/relevant.md"},
                content="The exact traversal answer.",
                roles=("answer",),
                selection_signals=("indexed_symbol_match",),
                authored_state="current",
                derived_flags=(),
                authority_signals=(),
                retrieval_rank=0,
            ),
            CandidateEvidence(
                id="loci:a-irrelevant#section",
                provider="loci",
                route="indexed_section",
                page="systems/irrelevant.md",
                source=None,
                locator={"file": "systems/irrelevant.md"},
                content="Alphabetically earlier but less relevant.",
                roles=("answer",),
                selection_signals=("indexed_symbol_match",),
                authored_state="current",
                derived_flags=(),
                authority_signals=(),
                retrieval_rank=1,
            ),
        ]

        selected, _ = select_candidates(candidates, request, ("answer",))

        self.assertEqual([item.id for item in selected], ["loci:z-relevant#section"])


if __name__ == "__main__":
    unittest.main()
