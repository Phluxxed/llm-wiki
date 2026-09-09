from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.compiler import compile_context
from llm_wiki_core.contracts import CompileRequest, ContractError, Diagnostic, EvidenceRecord
from llm_wiki_core.providers.base import CandidateEvidence
from llm_wiki_core.selection import (
    DeliveryCostContext,
    delivery_rendered_bytes,
    select_candidates,
)
from llm_wiki_mcp.output_models import WikiCompileContextOutput


class _EvidenceProvider:
    name = "delivery-test"

    def __init__(self, candidate: CandidateEvidence):
        self.candidate = candidate

    def collect(self, _context):
        return [self.candidate]


def _candidate(
    *,
    content: str,
    page: str | None = "docs/delivery.md",
    source: str | None = None,
    evidence_id: str = "delivery:test",
    roles: tuple[str, ...] = ("answer", "authority"),
    atomic: bool = False,
) -> CandidateEvidence:
    return CandidateEvidence(
        id=evidence_id,
        provider="delivery-test",
        route="delivery_fixture",
        page=page,
        source=source,
        locator={"section": "Delivery"},
        content=content,
        roles=roles,
        selection_signals=("delivery_fixture",),
        authored_state="current",
        derived_flags=(),
        authority_signals=("fixture",),
        atomic=atomic,
    )


def _request(budget: dict, *, contract_version: str = "1") -> CompileRequest:
    return CompileRequest.from_mapping(
        {
            "contract_version": contract_version,
            "alias": "delivery-test",
            "question": "What is the delivery answer?",
            "budget": budget,
        }
    )


def _record(item: dict) -> EvidenceRecord:
    return EvidenceRecord(
        id=item["id"],
        provider=item["provider"],
        route=item["route"],
        page=item["page"],
        source=item["source"],
        locator=item["locator"],
        content=item["content"],
        roles=tuple(item["roles"]),
        authored_state=item["authored_state"],
        derived_flags=tuple(item["derived_flags"]),
        authority_signals=tuple(item["authority_signals"]),
        selection_reasons=tuple(item["selection_reasons"]),
        byte_cost=item["byte_cost"],
        truncated=item["truncated"],
        atomic=item["atomic"],
    )


class DeliveryBudgetTest(unittest.TestCase):
    def _compile(self, request: CompileRequest, candidate: CandidateEvidence):
        with tempfile.TemporaryDirectory() as tmpdir:
            return compile_context(
                Path(tmpdir),
                request,
                extra_providers=(_EvidenceProvider(candidate),),
            ).to_dict()

    def test_delivery_contract_is_paired_and_closed(self):
        valid = _request(
            {
                "target_bytes": 20_000,
                "max_bytes": 20_000,
                "max_delivery_bytes": 1_000,
                "delivery_format": "anvil_prompt_evidence_v1",
            }
        )
        self.assertEqual(
            valid.to_dict()["budget"]["max_delivery_bytes"],
            1_000,
        )
        for budget, field in (
            ({"max_delivery_bytes": 1_000}, "budget.delivery_format"),
            ({"delivery_format": "anvil_prompt_evidence_v1"}, "budget.max_delivery_bytes"),
            ({"max_delivery_bytes": 0, "delivery_format": "anvil_prompt_evidence_v1"}, "budget.max_delivery_bytes"),
            ({"max_delivery_bytes": 1_000, "delivery_format": "other"}, "budget.delivery_format"),
        ):
            with self.subTest(budget=budget), self.assertRaises(ContractError) as raised:
                _request(budget)
            self.assertEqual(raised.exception.details["field"], field)

    def test_non_atomic_multibyte_evidence_fits_actual_delivery_profile(self):
        candidate = _candidate(content="é東京" * 600)
        wide = self._compile(
            _request(
                {
                    "target_bytes": 20_000,
                    "max_bytes": 20_000,
                    "target_items": 4,
                    "max_items": 4,
                    "max_delivery_bytes": 20_000,
                    "delivery_format": "anvil_prompt_evidence_v1",
                }
            ),
            candidate,
        )
        limit = wide["budget"]["delivery_bytes"] - 37
        response = self._compile(
            _request(
                {
                    "target_bytes": 2_000,
                    "max_bytes": 20_000,
                    "target_items": 4,
                    "max_items": 4,
                    "max_delivery_bytes": limit,
                    "delivery_format": "anvil_prompt_evidence_v1",
                }
            ),
            candidate,
        )
        self.assertLessEqual(response["budget"]["delivery_bytes"], limit)
        self.assertTrue(response["evidence"][0]["truncated"])
        self.assertIn("delivered_limit_excerpt", response["evidence"][0]["selection_reasons"])
        self.assertEqual(
            response["budget"]["delivery_bytes"],
            delivery_rendered_bytes(
                tuple(_record(item) for item in response["evidence"]),
                DeliveryCostContext("1"),
            ),
        )
        self.assertEqual(response["stop"]["reason"], "delivery_budget_exhausted")

    def test_selection_reserves_ambiguous_delivery_fallback_growth(self):
        candidate = _candidate(content="important evidence " * 100)
        project_resolution = {
            "status": "ambiguous",
            "matched_by": "alias",
            "candidates": [],
            "candidate_count": 2,
        }
        explicit_context = DeliveryCostContext(
            "3",
            project_resolution,
            (
                Diagnostic(
                    code="PROJECT_IDENTITY_AMBIGUOUS",
                    message="ambiguous",
                    provider="project",
                    details={},
                ),
            ),
        )
        fallback_context = DeliveryCostContext("3", project_resolution, ())
        whole_request = _request(
            {
                "target_bytes": 20_000,
                "max_bytes": 20_000,
                "target_items": 4,
                "max_items": 4,
                "max_delivery_bytes": 20_000,
                "delivery_format": "anvil_prompt_evidence_v1",
            },
            contract_version="3",
        )
        whole, whole_omissions = select_candidates(
            [candidate],
            whole_request,
            ("answer", "authority"),
            delivery_context=explicit_context,
        )
        self.assertEqual(whole_omissions, ())
        whole_actual = delivery_rendered_bytes(whole, explicit_context)
        whole_fallback = delivery_rendered_bytes(whole, fallback_context)
        self.assertLess(whole_actual, whole_fallback)
        max_delivery_bytes = whole_actual + (whole_fallback - whole_actual) // 2
        self.assertLessEqual(whole_actual, max_delivery_bytes)
        self.assertGreater(whole_fallback, max_delivery_bytes)

        request = _request(
            {
                "target_bytes": 20_000,
                "max_bytes": 20_000,
                "target_items": 4,
                "max_items": 4,
                "max_delivery_bytes": max_delivery_bytes,
                "delivery_format": "anvil_prompt_evidence_v1",
            },
            contract_version="3",
        )
        selected, omissions = select_candidates(
            [candidate],
            request,
            ("answer", "authority"),
            delivery_context=explicit_context,
        )
        self.assertEqual(omissions, ())
        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0].truncated)
        self.assertIn("delivered_limit_excerpt", selected[0].selection_reasons)
        self.assertTrue(selected[0].content.startswith("important evidence "))
        self.assertTrue(selected[0].content.endswith("\n\n[truncated]"))
        self.assertLessEqual(
            delivery_rendered_bytes(selected, explicit_context),
            max_delivery_bytes,
        )
        self.assertLessEqual(
            delivery_rendered_bytes(selected, fallback_context),
            max_delivery_bytes,
        )

    def test_atomic_evidence_is_omitted_when_delivery_metadata_cannot_fit(self):
        response = self._compile(
            _request(
                {
                    "target_bytes": 20_000,
                    "max_bytes": 20_000,
                    "target_items": 4,
                    "max_items": 4,
                    "max_delivery_bytes": 125,
                    "delivery_format": "anvil_prompt_evidence_v1",
                }
            ),
            _candidate(
                content="atomic evidence " * 80,
                page="projects/" + ("p" * 120) + ".md",
                evidence_id="atomic:" + ("i" * 120),
                roles=("answer", "authority", "extra-role"),
                atomic=True,
            ),
        )
        self.assertEqual(response["evidence"], [])
        self.assertIn(
            ("atomic:" + ("i" * 120), "delivery_byte_limit"),
            [(item["candidate_id"], item["reason"]) for item in response["omissions"]],
        )
        self.assertEqual(response["stop"]["reason"], "delivery_budget_exhausted")
        self.assertIn(response["continuation"]["reason"], {"hard_limit_reached"})

    def test_mcp_output_model_accepts_delivery_budget_accounting(self):
        response = self._compile(
            _request(
                {
                    "target_bytes": 20_000,
                    "max_bytes": 20_000,
                    "target_items": 4,
                    "max_items": 4,
                    "max_delivery_bytes": 125,
                    "delivery_format": "anvil_prompt_evidence_v1",
                }
            ),
            _candidate(
                content="atomic evidence " * 80,
                page="projects/" + ("p" * 120) + ".md",
                evidence_id="atomic:" + ("i" * 120),
                roles=("answer", "authority", "extra-role"),
                atomic=True,
            ),
        )
        validated = WikiCompileContextOutput.model_validate(response)
        assert validated.budget is not None
        assert validated.stop is not None
        self.assertEqual(validated.budget.limits.max_delivery_bytes, 125)
        self.assertEqual(
            validated.budget.limits.delivery_format,
            "anvil_prompt_evidence_v1",
        )
        self.assertIsNotNone(validated.budget.delivery_bytes)
        self.assertLessEqual(validated.budget.delivery_bytes, 125)
        self.assertEqual(validated.stop.reason, "delivery_budget_exhausted")

    def test_delivery_budget_catches_metadata_overflow_beyond_content_cap(self):
        candidates = tuple(
            replace(
                _candidate(
                    content=(f"candidate-{index} evidence " * 500)[:3_800],
                    page=("docs/overflow-" + ("p" * 120) + f"-{index}.md"),
                    evidence_id=("overflow:" + ("i" * 120) + f":{index}"),
                    roles=("answer", "authority", f"role-{index}"),
                ),
                provider="seed",
                route="seed",
                selection_signals=("seed", f"overflow:{index}"),
            )
            for index in range(4)
        )

        class _SeedOverflowProvider:
            name = "seed-overflow"

            def __init__(self, values):
                self.values = values
                self.calls = 0

            def collect(self, _context):
                self.calls += 1
                return list(self.values)

        wide_provider = _SeedOverflowProvider(candidates)
        with tempfile.TemporaryDirectory() as tmpdir:
            wide = compile_context(
                Path(tmpdir),
                _request(
                    {
                        "target_bytes": 20_000,
                        "max_bytes": 32_768,
                        "max_content_bytes": 15_360,
                        "target_items": 4,
                        "max_items": 4,
                    }
                ),
                extra_providers=(wide_provider,),
            ).to_dict()
        self.assertEqual(wide_provider.calls, 1)
        self.assertEqual(len(wide["evidence"]), 4)
        self.assertEqual(wide["budget"]["content_bytes"], 15_200)
        wide_delivery_bytes = delivery_rendered_bytes(
            tuple(_record(item) for item in wide["evidence"]),
            DeliveryCostContext("1"),
        )
        self.assertGreater(wide_delivery_bytes, 16_384)

        paired_provider = _SeedOverflowProvider(candidates)
        with tempfile.TemporaryDirectory() as tmpdir:
            paired = compile_context(
                Path(tmpdir),
                _request(
                    {
                        "target_bytes": 20_000,
                        "max_bytes": 32_768,
                        "max_content_bytes": 15_360,
                        "target_items": 4,
                        "max_items": 4,
                        "max_delivery_bytes": 16_384,
                        "delivery_format": "anvil_prompt_evidence_v1",
                    }
                ),
                extra_providers=(paired_provider,),
            ).to_dict()
        self.assertEqual(paired_provider.calls, 1)
        self.assertLessEqual(paired["budget"]["delivery_bytes"], 16_384)
        self.assertEqual(paired["budget"]["limits"]["max_delivery_bytes"], 16_384)
        self.assertEqual(
            paired["budget"]["limits"]["delivery_format"],
            "anvil_prompt_evidence_v1",
        )
        self.assertEqual(len(paired["evidence"]), 4)
        self.assertEqual(
            [len(item["content"]) for item in paired["evidence"][:3]],
            [3_800, 3_800, 3_800],
        )
        self.assertTrue(paired["evidence"][3]["truncated"])
        self.assertIn(
            "delivered_limit_excerpt",
            paired["evidence"][3]["selection_reasons"],
        )
        self.assertEqual(paired["omissions"], [])
        self.assertEqual(paired["stop"]["reason"], "delivery_budget_exhausted")

    def test_delivery_frame_alone_can_be_impossible(self):
        with self.assertRaises(ContractError) as raised:
            self._compile(
                _request(
                    {
                        "target_bytes": 20_000,
                        "max_bytes": 20_000,
                        "max_delivery_bytes": 1,
                        "delivery_format": "anvil_prompt_evidence_v1",
                    }
                ),
                _candidate(content="unused"),
            )
        self.assertEqual(raised.exception.code, "BUDGET_TOO_SMALL")
        self.assertIn("minimum_delivery_bytes", raised.exception.details)

    def test_delivery_accounting_survives_complete_json_finalizer(self):
        response = self._compile(
            _request(
                {
                    "target_bytes": 2_000,
                    "max_bytes": 2_200,
                    "target_items": 4,
                    "max_items": 4,
                    "max_delivery_bytes": 20_000,
                    "delivery_format": "anvil_prompt_evidence_v1",
                }
            ),
            _candidate(content="long evidence " * 1_000),
        )
        serialized = len(json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        self.assertLessEqual(serialized, 2_200)
        self.assertLessEqual(response["budget"]["delivery_bytes"], 20_000)
        self.assertEqual(
            response["budget"]["delivery_bytes"],
            delivery_rendered_bytes(
                tuple(_record(item) for item in response["evidence"]),
                DeliveryCostContext("1"),
            ),
        )

    def test_shared_golden_fixture_matches_python_profile(self):
        fixture = json.loads(
            (REPO_ROOT / "tests" / "fixtures" / "anvil-prompt-delivery-v1.json").read_text(
                encoding="utf-8"
            )
        )
        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                evidence = tuple(_record(item) for item in case["evidence"])
                diagnostics = tuple(
                    Diagnostic(
                        code=item["code"],
                        message=item["message"],
                        provider=item["provider"],
                        details=item["details"],
                    )
                    for item in case["diagnostics"]
                )
                actual = delivery_rendered_bytes(
                    evidence,
                    DeliveryCostContext(
                        case["contract_version"], case["project_resolution"], diagnostics, case.get("temporal")
                    ),
                )
                self.assertEqual(actual, case["expected"]["markdown_bytes"])


if __name__ == "__main__":
    unittest.main()
