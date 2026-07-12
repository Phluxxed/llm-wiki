from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.compiler import compile_context
from llm_wiki_core.contracts import CompileRequest


CASES = yaml.safe_load((Path(__file__).with_name("cases.yaml")).read_text(encoding="utf-8"))


class CrossWikiAcceptanceTest(unittest.TestCase):
    def test_live_gold_spans_resolve_before_grading_compiler_output(self):
        for case in CASES:
            with self.subTest(case=case["id"]):
                root = _case_root(case)
                if root is None:
                    continue
                for expected in case["required_evidence"]:
                    line = (root / expected["page"]).read_text(encoding="utf-8").splitlines()[
                        expected["source_line"] - 1
                    ]
                    self.assertIn(expected["text"], line)

    def test_live_wikis_meet_frozen_evidence_contracts(self):
        exercised = 0
        for case in CASES:
            with self.subTest(case=case["id"]):
                root = _case_root(case)
                if root is None:
                    continue
                exercised += 1
                response = compile_context(root, CompileRequest.from_mapping(case["request"])).to_dict()
                _assert_case(self, case, response)
        if exercised == 0:
            self.skipTest("Set cross-wiki acceptance root environment variables to run live cases")

    def test_fixture_rejects_a_deliberately_incomplete_result(self):
        case = CASES[0]
        root = _case_root(case)
        if root is None:
            self.skipTest(f"Set {case['root_env']} to run the live fixture mutation check")
        response = compile_context(root, CompileRequest.from_mapping(case["request"])).to_dict()
        broken = copy.deepcopy(response)
        required = case["required_evidence"][0]
        broken["evidence"] = [
            item
            for item in broken["evidence"]
            if not (
                item["page"] == required["page"]
                and item["provider"] == required["provider"]
                and required["text"] in item["content"]
            )
        ]

        with self.assertRaises(AssertionError):
            _assert_case(self, case, broken)


def _case_root(case: dict) -> Path | None:
    raw = os.environ.get(case["root_env"])
    if not raw:
        return None
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise AssertionError(f"Acceptance root is not a directory: {root}")
    return root


def _assert_case(test: unittest.TestCase, case: dict, response: dict) -> None:
    for required in case["required_evidence"]:
        matches = [
            item
            for item in response["evidence"]
            if item["page"] == required["page"]
            and item["provider"] == required["provider"]
            and required["role"] in item["roles"]
            and required["text"] in item["content"]
        ]
        test.assertTrue(matches, f"missing required evidence for {case['id']}: {required}")
        if "locator_start_line" in required:
            test.assertTrue(
                any(
                    item["locator"].get("start_line") == required["locator_start_line"]
                    for item in matches
                ),
                f"wrong locator for {case['id']}: {required}",
            )
        if "authored_state" in required:
            test.assertTrue(
                any(item["authored_state"] == required["authored_state"] for item in matches),
                f"wrong state for {case['id']}: {required}",
            )
    combined = "\n".join(item["content"] for item in response["evidence"])
    for forbidden in case.get("forbidden_text", []):
        test.assertNotIn(forbidden, combined)
    expected = case["expected"]
    test.assertEqual(response["stop"]["sufficient"], expected["sufficient"])
    test.assertEqual(
        response["budget"]["target_exceeded_for_coverage"],
        expected["target_exceeded_for_coverage"],
    )
    test.assertEqual(response["coverage"]["uncovered_roles"], expected["uncovered_roles"])


if __name__ == "__main__":
    unittest.main()
