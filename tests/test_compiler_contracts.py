from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.contracts import (
    BudgetUsage,
    CompileRequest,
    CompiledContext,
    ContractError,
    Coverage,
    EvidenceRecord,
    StopState,
    TemporalQuery,
)
from llm_wiki_core.query_shape import classify_question, required_roles
from llm_wiki_core.state import normalize_knowledge_state, state_compatibility


class CompileRequestTest(unittest.TestCase):
    def test_valid_request_round_trips_with_effective_budget(self):
        request = CompileRequest.from_mapping(
            {
                "contract_version": "1",
                "alias": "brain",
                "question": "What currently owns wiki traversal?",
                "seeds": ["systems/llm-wiki.md"],
                "state_view": "current",
                "budget": {
                    "target_bytes": 48_000,
                    "max_bytes": 192_000,
                    "target_items": 24,
                    "max_items": 96,
                    "max_estimated_tokens": 48_000,
                },
            }
        )

        self.assertEqual(
            request.to_dict(),
            {
                "contract_version": "1",
                "alias": "brain",
                "question": "What currently owns wiki traversal?",
                "seeds": ["systems/llm-wiki.md"],
                "state_view": "current",
                "budget": {
                    "target_bytes": 48_000,
                    "max_bytes": 192_000,
                    "target_items": 24,
                    "max_items": 96,
                    "max_estimated_tokens": 48_000,
                },
            },
        )

    def test_blank_question_is_rejected_at_boundary(self):
        with self.assertRaises(ContractError) as raised:
            CompileRequest.from_mapping({"alias": "brain", "question": "  "})

        self.assertEqual(raised.exception.code, "INVALID_INPUT")
        self.assertEqual(raised.exception.details["field"], "question")

    def test_unsupported_contract_version_fails_before_execution(self):
        with self.assertRaises(ContractError) as raised:
            CompileRequest.from_mapping(
                {"contract_version": "3", "alias": "brain", "question": "What changed?"}
            )

        self.assertEqual(raised.exception.code, "CONTRACT_VERSION_UNSUPPORTED")

    def test_target_cannot_exceed_hard_maximum(self):
        with self.assertRaises(ContractError) as raised:
            CompileRequest.from_mapping(
                {
                    "alias": "brain",
                    "question": "What changed?",
                    "budget": {"target_bytes": 10_000, "max_bytes": 5_000},
                }
            )

        self.assertEqual(raised.exception.details["field"], "budget.target_bytes")

    def test_v1_rejects_temporal_fields_and_keeps_exact_serialization(self):
        raw = {"alias": "brain", "question": "What changed?", "temporal": {}}
        with self.assertRaises(ContractError) as raised:
            CompileRequest.from_mapping(raw)
        self.assertEqual(raised.exception.code, "INVALID_INPUT")
        self.assertEqual(raised.exception.details["field"], "temporal")

        request = CompileRequest.from_mapping({"alias": "brain", "question": "What changed?"})
        self.assertEqual(request.contract_version, "1")
        self.assertNotIn("temporal", request.to_dict())

    def test_v2_temporal_query_defaults_current_world_and_known_to_request_time(self):
        request = CompileRequest.from_mapping(
            {
                "contract_version": "2",
                "alias": "brain",
                "question": "What is current?",
                "temporal": {
                    "view": "current",
                    "request_time": "2026-08-10T00:00:00+00:00",
                },
            }
        )
        self.assertIsInstance(request.temporal, TemporalQuery)
        self.assertEqual(request.temporal.request_time, "2026-08-10T00:00:00Z")
        self.assertEqual(request.temporal.world_at, "2026-08-10T00:00:00Z")
        self.assertEqual(request.temporal.known_at, "2026-08-10T00:00:00Z")
        self.assertEqual(request.to_dict()["contract_version"], "2")
        self.assertEqual(request.to_dict()["temporal"]["view"], "current")

    def test_v2_without_temporal_preserves_ordinary_compilation_request(self):
        request = CompileRequest.from_mapping(
            {"contract_version": "2", "alias": "brain", "question": "What changed?"}
        )
        self.assertIsNone(request.temporal)
        self.assertNotIn("temporal", request.to_dict())

    def test_temporal_view_rules_are_strict(self):
        with self.assertRaises(ContractError):
            CompileRequest.from_mapping(
                {
                    "contract_version": "2",
                    "alias": "brain",
                    "question": "What was true?",
                    "temporal": {"view": "historical", "request_time": "2026-01-01T00:00:00Z"},
                }
            )
        with self.assertRaises(ContractError):
            CompileRequest.from_mapping(
                {
                    "contract_version": "2",
                    "alias": "brain",
                    "question": "What changed?",
                    "temporal": {
                        "view": "transition",
                        "request_time": "2026-01-01T00:00:00Z",
                        "world_at": "2026-01-01",
                        "transition": {"from": "2025-01-01", "to": "2026-01-01"},
                    },
                }
            )
        with self.assertRaises(ContractError):
            CompileRequest.from_mapping(
                {
                    "contract_version": "2",
                    "alias": "brain",
                    "question": "What changed?",
                    "temporal": {
                        "view": "lineage",
                        "request_time": "2026-01-01T00:00:00Z",
                        "unexpected": True,
                    },
                }
            )


class KnowledgeStateTest(unittest.TestCase):
    def test_missing_state_is_unspecified_not_current(self):
        state = normalize_knowledge_state({})

        self.assertEqual(state.authored, None)
        self.assertEqual(state.normalized, "unspecified")
        self.assertEqual(state.derived_flags, ())

    def test_unknown_authored_state_is_preserved_as_diagnostic(self):
        state = normalize_knowledge_state({"knowledge_state": "probably-current"})

        self.assertEqual(state.authored, "probably-current")
        self.assertEqual(state.normalized, "unspecified")
        self.assertEqual(state.derived_flags, ("unknown_authored_state",))

    def test_current_view_prefers_current_but_allows_unspecified(self):
        self.assertEqual(state_compatibility("current", "current"), "preferred")
        self.assertEqual(state_compatibility("unspecified", "current"), "allowed")
        self.assertEqual(state_compatibility("superseded", "current"), "lineage_only")
        self.assertEqual(state_compatibility("historical", "all"), "allowed")


class QueryShapeTest(unittest.TestCase):
    def test_relationship_and_current_state_can_both_apply(self):
        shapes = classify_question("How does Brain currently connect to llm-wiki and propagate upgrades?")

        self.assertEqual(shapes, ("relationship", "state"))
        self.assertEqual(
            required_roles(shapes),
            ("endpoint", "bridge", "current_claim", "authority"),
        )

    def test_unclassified_question_defaults_to_synthesis(self):
        self.assertEqual(classify_question("Explain the architecture in useful terms"), ("synthesis",))

    def test_relationship_language_covers_supported_related_and_transition_phrasings(self):
        questions = (
            "What evidence supports Alpha through Beta?",
            "How are Alpha and Beta related?",
            "How can Alpha become durable Beta?",
            "What improvement did Alpha make to Beta?",
            "Does Alpha define how Beta should work?",
        )

        for question in questions:
            with self.subTest(question=question):
                self.assertIn("relationship", classify_question(question))

    def test_hyphenated_link_attribute_is_not_a_relationship(self):
        shapes = classify_question("What were the exact link-attribution results?")

        self.assertEqual(shapes, ("lookup",))


class CompiledContextTest(unittest.TestCase):
    def test_response_serialization_matches_v1_fixture(self):
        expected = json.loads((FIXTURES / "compiled_context_v1.json").read_text(encoding="utf-8"))
        response = CompiledContext(
            alias="brain",
            schema_version="1",
            runtime_contract="2",
            question="What currently owns traversal?",
            shapes=("lookup", "state"),
            state_view="current",
            resolved_seeds=("systems/llm-wiki.md",),
            evidence=(
                EvidenceRecord(
                    id="seed:systems/llm-wiki.md",
                    provider="seed",
                    route="exact_seed",
                    page="systems/llm-wiki.md",
                    source=None,
                    locator={"section": "Ownership"},
                    content="llm-wiki owns the shared runtime.",
                    roles=("answer", "current_claim", "authority"),
                    authored_state="current",
                    derived_flags=(),
                    authority_signals=("explicit_current",),
                    selection_reasons=("exact_seed", "covers:current_claim"),
                    byte_cost=35,
                ),
            ),
            omissions=(),
            coverage=Coverage(
                required_roles=("answer", "authority", "current_claim"),
                covered_roles=("answer", "authority", "current_claim"),
                uncovered_roles=(),
            ),
            budget=BudgetUsage(
                target_bytes=48_000,
                max_bytes=192_000,
                target_items=24,
                max_items=96,
                evidence_bytes=35,
                envelope_bytes=0,
                items=1,
                estimated_tokens=9,
                target_exceeded_for_coverage=False,
            ),
            stop=StopState(reason="sufficient", sufficient=True, detail="All required roles covered"),
        )

        self.assertEqual(response.to_dict(), expected)


if __name__ == "__main__":
    unittest.main()
