from __future__ import annotations

import json
from dataclasses import replace
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.compiler import compile_context
from llm_wiki_core.contracts import CompileRequest, ContractError
from llm_wiki_core.providers.base import CandidateEvidence
from llm_wiki_core.selection import select_candidates
from tests.wiki_fixture import base_fm, write_md


class _NoisyProvider:
    name = "noise"

    def __init__(self, count: int):
        self.count = count

    def collect(self, _context):
        return [
            CandidateEvidence(
                id=f"noise:{index:04d}:" + ("irrelevant-" * 4),
                provider="noise",
                route="broad_match",
                page=f"noise/page-{index:04d}.md",
                source=None,
                locator={"section": "Irrelevant detail", "rank": index},
                content="A lower-value result that does not add a missing evidence role.",
                roles=("answer",),
                selection_signals=("broad_match",),
                authored_state="current",
                derived_flags=(),
                authority_signals=(),
            )
            for index in range(self.count)
        ]


class _AtomicProvider:
    name = "noise"

    def collect(self, _context):
        return [
            CandidateEvidence(
                id="graph:atomic",
                provider="graph",
                route="connecting_path",
                page="concepts/bridge.md",
                source=None,
                locator={"path": ["A", "B"]},
                content="complete authored path evidence " * 40,
                roles=("answer", "authority"),
                selection_signals=("connecting_path",),
                authored_state="current",
                derived_flags=(),
                authority_signals=("accepted_adr",),
                atomic=True,
            )
        ]


class _ContentProvider:
    name = "content-test"

    def __init__(self, content: str, *, atomic: bool = False):
        self.content = content
        self.atomic = atomic

    def collect(self, _context):
        return [
            CandidateEvidence(
                id="content:test",
                provider=self.name,
                route="content_budget_fixture",
                page="fixture.md",
                source=None,
                locator={"section": "Exact content boundary"},
                content=self.content,
                roles=("answer", "authority"),
                selection_signals=("content_budget_fixture",),
                authored_state="current",
                derived_flags=(),
                authority_signals=("test_fixture",),
                atomic=self.atomic,
            )
        ]


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

    def test_advisory_metadata_breaks_ties_without_overriding_relevance_or_seeds(self):
        request = CompileRequest.from_mapping({"alias": "test", "question": "runtime ownership"})
        base = CandidateEvidence(
            id="a:unverified", provider="frontmatter", route="exact_title", page="a.md", source=None,
            locator={"start_line": 1, "end_line": 1}, content="runtime ownership",
            roles=("answer",), selection_signals=("exact_title",), authored_state="current",
            derived_flags=(), authority_signals=(), relevance_terms=("runtime", "ownership"),
        )
        verified = replace(base, id="z:verified", page="z.md", metadata={
            "trust": "human-reviewed", "freshness": "unknown", "status": "stable",
        })
        selected, _ = select_candidates([base, verified], request, ("answer",), include_supplements=False)
        self.assertEqual(selected[0].id, verified.id)
        stale = replace(verified, metadata={**verified.metadata, "freshness": "stale"})
        selected, _ = select_candidates([base, stale], request, ("answer",), include_supplements=False)
        self.assertEqual(selected[0].id, base.id)
        better_match = replace(base, relevance_terms=("runtime", "ownership", "detail"))
        selected, _ = select_candidates([better_match, verified], request, ("answer",), include_supplements=False)
        self.assertEqual(selected[0].id, base.id)
        explicit = replace(base, provider="seed", selection_signals=("exact_seed",))
        selected, _ = select_candidates([explicit, verified], request, ("answer",), include_supplements=False)
        self.assertEqual(selected[0].id, base.id)

    def test_native_metadata_is_counted_in_serialized_evidence_budget(self):
        write_md(self.root / "systems" / "llm-wiki.md", base_fm(
            title="llm-wiki", status="stable", source="sources/runtime.md", knowledge_state="current",
            generated={"by": "writer/v2", "at": "2026-01-01T00:00:00Z", "note": "metadata " * 80},
            verified={"by": "process:check", "at": "2026-01-02T00:00:00Z"},
        ), "llm-wiki owns traversal behavior.")
        response = self.compile({"target_bytes": 6500, "max_bytes": 6500})
        self.assertLessEqual(len(json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()), 6500)
        record = next(item for item in response["evidence"] if "metadata" in item)
        self.assertEqual(record["metadata"]["trust"], "machine-confirmed")
        self.assertEqual(record["authored_state"], "current")
        self.assertNotIn("machine-confirmed", record["authority_signals"])
        self.assertEqual(record["byte_cost"], len(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()))

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
                "max_bytes": 1_500,
                "target_items": 10,
                "max_items": 10,
            }
        )

        self.assertFalse(response["stop"]["sufficient"])
        self.assertEqual(response["stop"]["reason"], "byte_budget_exhausted")
        self.assertTrue(response["coverage"]["uncovered_roles"])
        self.assertEqual(response["continuation"]["reason"], "hard_limit_reached")
        self.assertGreater(response["continuation"]["remaining_candidate_count"], 0)

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

    def test_hard_byte_ceiling_applies_to_the_complete_serialized_response(self):
        request = CompileRequest.from_mapping(
            {
                "alias": "test",
                "question": "Who owns traversal behavior?",
                "seeds": ["systems/llm-wiki.md"],
                "budget": {
                    "target_bytes": 2_000,
                    "max_bytes": 4_096,
                    "target_items": 4,
                    "max_items": 16,
                },
            }
        )
        response = compile_context(
            self.root,
            request,
            extra_providers=(_NoisyProvider(200),),
        ).to_dict()
        serialized = json.dumps(
            response,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        self.assertLessEqual(len(serialized), 4_096)
        self.assertEqual(
            response["budget"]["evidence_bytes"] + response["budget"]["envelope_bytes"],
            len(serialized),
        )
        self.assertGreater(
            response["reporting"]["omissions"]["total"],
            response["reporting"]["omissions"]["returned"],
        )
        self.assertEqual(
            response["reporting"]["omissions"]["returned"],
            len(response["omissions"]),
        )
        self.assertLessEqual(response["reporting"]["omissions"]["returned"], 16)
        self.assertTrue(all(not item["truncated"] for item in response["evidence"]))

    def test_estimated_token_ceiling_applies_to_the_complete_serialized_response(self):
        request = CompileRequest.from_mapping(
            {
                "alias": "test",
                "question": "Who owns traversal behavior?",
                "seeds": ["systems/llm-wiki.md"],
                "budget": {
                    "target_bytes": 2_000,
                    "max_bytes": 20_000,
                    "target_items": 4,
                    "max_items": 16,
                    "max_estimated_tokens": 1_024,
                },
            }
        )
        response = compile_context(
            self.root,
            request,
            extra_providers=(_NoisyProvider(200),),
        ).to_dict()

        self.assertLessEqual(response["budget"]["estimated_tokens"], 1_024)
        self.assertLessEqual(
            len(
                json.dumps(
                    response,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ),
            4_096,
        )

    def test_budget_too_small_for_the_contract_is_rejected(self):
        request = CompileRequest.from_mapping(
            {
                "alias": "test",
                "question": "Who owns traversal behavior?",
                "seeds": ["systems/llm-wiki.md"],
                "budget": {
                    "target_bytes": 100,
                    "max_bytes": 100,
                    "target_items": 1,
                    "max_items": 1,
                },
            }
        )

        with self.assertRaisesRegex(ContractError, "complete response") as raised:
            compile_context(self.root, request)

        self.assertEqual(raised.exception.code, "BUDGET_TOO_SMALL")

    def test_complete_response_compaction_never_excerpts_atomic_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            request = CompileRequest.from_mapping(
                {
                    "alias": "test",
                    "question": "What owns the bridge?",
                    "budget": {
                        "target_bytes": 2_000,
                        "max_bytes": 2_000,
                        "target_items": 4,
                        "max_items": 4,
                    },
                }
            )
            response = compile_context(
                Path(tmpdir),
                request,
                extra_providers=(_AtomicProvider(),),
            ).to_dict()

        self.assertEqual(response["evidence"], [])
        self.assertIn(
            ("graph:atomic", "byte_limit"),
            [(item["candidate_id"], item["reason"]) for item in response["omissions"]],
        )
        self.assertLessEqual(
            response["budget"]["evidence_bytes"] + response["budget"]["envelope_bytes"],
            2_000,
        )


    def test_native_sections_do_not_force_unrelated_results_after_coverage(self):
        request = CompileRequest.from_mapping(
            {"alias": "test", "question": "What owns traversal?", "seeds": []}
        )
        candidates = [
            CandidateEvidence(
                id="text:z-relevant#section",
                provider="text",
                route="lexical_section",
                page="systems/relevant.md",
                source=None,
                locator={"file": "systems/relevant.md"},
                content="The exact traversal answer.",
                roles=("answer",),
                selection_signals=("term:traversal",),
                authored_state="current",
                derived_flags=(),
                authority_signals=(),
            ),
            CandidateEvidence(
                id="text:a-irrelevant#section",
                provider="text",
                route="lexical_section",
                page="systems/irrelevant.md",
                source=None,
                locator={"file": "systems/irrelevant.md"},
                content="Alphabetically earlier but less relevant.",
                roles=("answer",),
                selection_signals=("term:traversal",),
                authored_state="current",
                derived_flags=(),
                authority_signals=(),
            ),
            CandidateEvidence(
                id="text:m-third#section",
                provider="text",
                route="lexical_section",
                page="systems/third.md",
                source=None,
                locator={"file": "systems/third.md"},
                content="The third ranked exact section.",
                roles=("answer",),
                selection_signals=("term:traversal",),
                authored_state="current",
                derived_flags=(),
                authority_signals=(),
            ),
            CandidateEvidence(
                id="text:b-fourth#section",
                provider="text",
                route="lexical_section",
                page="systems/fourth.md",
                source=None,
                locator={"file": "systems/fourth.md"},
                content="The fourth ranked section stays outside the minimum set.",
                roles=("answer",),
                selection_signals=("term:traversal",),
                authored_state="current",
                derived_flags=(),
                authority_signals=(),
            ),
        ]

        selected, _ = select_candidates(candidates, request, ("answer",))

        self.assertEqual(
            [item.id for item in selected],
            ["text:z-relevant#section"],
        )

    def test_atomic_graph_path_is_omitted_instead_of_partially_truncated(self):
        request = CompileRequest.from_mapping(
            {
                "alias": "test",
                "question": "How does A connect to B?",
                "seeds": [],
                "budget": {
                    "target_bytes": 100,
                    "max_bytes": 500,
                    "target_items": 4,
                    "max_items": 4,
                },
            }
        )
        candidate = CandidateEvidence(
            id="graph:path-a-b",
            provider="graph",
            route="connecting_path",
            page="concepts/bridge.md",
            source=None,
            locator={"support_kind": "semantic_bridge"},
            content="complete authored path evidence " * 80,
            roles=("bridge",),
            selection_signals=("connecting_path",),
            authored_state="current",
            derived_flags=(),
            authority_signals=(),
            atomic=True,
        )

        selected, omissions = select_candidates([candidate], request, ("bridge",))

        self.assertEqual(selected, ())
        self.assertEqual(
            [(item.candidate_id, item.reason) for item in omissions],
            [("graph:path-a-b", "byte_limit")],
        )

    def test_content_budget_accepts_exact_utf8_boundary_without_truncation(self):
        isolated_root = self.root / "content-budget"
        isolated_root.mkdir()
        content = "é" * 2048
        request = CompileRequest.from_mapping(
            {
                "alias": "test",
                "question": "Who owns traversal?",
                "budget": {
                    "target_bytes": 20_000,
                    "max_bytes": 20_000,
                    "target_items": 4,
                    "max_items": 4,
                    "max_content_bytes": 4096,
                },
            }
        )

        response = compile_context(
            isolated_root,
            request,
            extra_providers=(_ContentProvider(content),),
        ).to_dict()

        evidence = next(item for item in response["evidence"] if item["id"] == "content:test")
        self.assertEqual(evidence["content"], content)
        self.assertFalse(evidence["truncated"])
        self.assertEqual(response["budget"]["limits"]["max_content_bytes"], 4096)
        self.assertEqual(response["budget"]["content_bytes"], 4096)

    def test_content_budget_deterministically_truncates_non_atomic_evidence(self):
        isolated_root = self.root / "content-truncation"
        isolated_root.mkdir()
        request = CompileRequest.from_mapping(
            {
                "alias": "test",
                "question": "Who owns traversal?",
                "budget": {
                    "target_bytes": 20_000,
                    "max_bytes": 20_000,
                    "target_items": 4,
                    "max_items": 4,
                    "max_content_bytes": 4096,
                },
            }
        )

        response = compile_context(
            isolated_root,
            request,
            extra_providers=(_ContentProvider("é" * 3000),),
        ).to_dict()

        evidence = next(item for item in response["evidence"] if item["id"] == "content:test")
        delivered_bytes = len(evidence["content"].encode("utf-8"))
        self.assertTrue(evidence["truncated"])
        self.assertTrue(evidence["content"].endswith("\n\n[truncated]"))
        self.assertIn("content_limit_excerpt", evidence["selection_reasons"])
        self.assertLessEqual(delivered_bytes, 4096)
        self.assertEqual(response["budget"]["content_bytes"], delivered_bytes)
        self.assertLessEqual(
            len(json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")),
            response["budget"]["limits"]["max_bytes"],
        )

    def test_content_budget_omits_atomic_evidence_that_cannot_fit(self):
        isolated_root = self.root / "atomic-content-budget"
        isolated_root.mkdir()
        request = CompileRequest.from_mapping(
            {
                "alias": "test",
                "question": "Who owns traversal?",
                "budget": {
                    "target_bytes": 20_000,
                    "max_bytes": 20_000,
                    "target_items": 4,
                    "max_items": 4,
                    "max_content_bytes": 4096,
                },
            }
        )

        response = compile_context(
            isolated_root,
            request,
            extra_providers=(_ContentProvider("a" * 4097, atomic=True),),
        ).to_dict()

        self.assertNotIn("content:test", {item["id"] for item in response["evidence"]})
        omission = next(item for item in response["omissions"] if item["candidate_id"] == "content:test")
        self.assertEqual(omission["reason"], "content_byte_limit")
        self.assertEqual(omission["estimated_bytes"], 4097)
        self.assertEqual(response["budget"]["content_bytes"], 0)
        self.assertEqual(response["stop"]["reason"], "content_budget_exhausted")
        self.assertIn("content:test", response["continuation"]["remaining_candidate_ids"])




if __name__ == "__main__":
    unittest.main()
