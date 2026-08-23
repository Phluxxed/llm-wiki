from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
ANVIL_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "anvil_compiled_evidence_v1.json"
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.cli import main
from llm_wiki_core.compiler import compile_context
from llm_wiki_core.contracts import CompileRequest
from tests.wiki_fixture import base_fm, write_md


class CompilerCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write_md(
            self.root / "runtime.md",
            base_fm(title="Runtime", knowledge_state="current"),
            "Runtime owns traversal.",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_cli_and_core_return_the_same_contract(self):
        args = [
            "compile-context",
            "--wiki",
            str(self.root),
            "--alias",
            "test",
            "--question",
            "What owns traversal?",
            "--seed",
            "runtime.md",
        ]
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            exit_code = main(args)

        expected = compile_context(
            self.root,
            CompileRequest.from_mapping(
                {
                    "alias": "test",
                    "question": "What owns traversal?",
                    "seeds": ["runtime.md"],
                }
            ),
        ).to_dict()
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), expected)

    def test_cli_errors_are_structured_and_nonzero(self):
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            exit_code = main(
                [
                    "compile-context",
                    "--wiki",
                    str(self.root),
                    "--alias",
                    "test",
                    "--question",
                    " ",
                ]
            )

        payload = json.loads(stderr.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["error"]["code"], "INVALID_INPUT")

    def test_cli_temporal_v2_matches_core_mapping(self):
        args = [
            "compile-context", "--wiki", str(self.root), "--alias", "test",
            "--question", "What is current?", "--contract-version", "2",
            "--temporal-view", "current", "--request-time", "2026-08-10T00:00:00Z",
            "--world-at", "2026-08-10", "--known-at", "2026-08-10T00:00:00Z",
        ]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(args)
        expected = compile_context(
            self.root,
            CompileRequest.from_mapping(
                {
                    "contract_version": "2", "alias": "test", "question": "What is current?",
                    "temporal": {
                        "view": "current", "request_time": "2026-08-10T00:00:00Z",
                        "world_at": "2026-08-10", "known_at": "2026-08-10T00:00:00Z",
                    },
                }
            ),
        ).to_dict()
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), expected)

    def test_cli_applies_content_ceiling_without_replacing_response_ceiling(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "compile-context",
                    "--wiki",
                    str(self.root),
                    "--alias",
                    "test",
                    "--question",
                    "What owns traversal?",
                    "--seed",
                    "runtime.md",
                    "--target-bytes",
                    "10000",
                    "--max-bytes",
                    "10000",
                    "--max-content-bytes",
                    "24",
                ]
            )

        payload = json.loads(stdout.getvalue())
        delivered_content_bytes = sum(
            len(item["content"].encode("utf-8")) for item in payload["evidence"]
        )
        serialized_bytes = len(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["budget"]["limits"]["max_content_bytes"], 24)
        self.assertEqual(payload["budget"]["limits"]["max_bytes"], 10000)
        self.assertEqual(payload["budget"]["content_bytes"], delivered_content_bytes)
        self.assertLessEqual(delivered_content_bytes, 24)
        self.assertLessEqual(serialized_bytes, 10000)

    def test_cli_emits_anvil_compiled_evidence_contract(self):
        contract = json.loads(ANVIL_FIXTURE.read_text(encoding="utf-8"))
        (self.root / ".llm-wiki.toml").write_text(
            'schema_version = "1"\n'
            'runtime_contract = "2"\n'
            '[compiler]\n'
            'providers = ["seed", "frontmatter", "text", "graph", "source"]\n'
            'graph_backend = "legacy"\n',
            encoding="utf-8",
        )
        write_md(
            self.root / "systems" / "runtime.md",
            base_fm(
                title="Runtime",
                type="policy",
                knowledge_state="current",
                source="sources/missing.md",
                tags=["runtime"],
            ),
            "## Ownership\n\n"
            "Runtime links to [Adapter](adapter.md).\n\n"
            + ("deterministic evidence " * 180),
        )
        write_md(
            self.root / "systems" / "adapter.md",
            base_fm(
                title="Adapter",
                type="system",
                knowledge_state="current",
                tags=["adapter"],
            ),
            "## Role\n\nAdapter handles traversal.",
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "compile-context",
                    "--wiki",
                    str(self.root),
                    "--alias",
                    "anvil-fixture",
                    "--question",
                    "How are Runtime and Adapter related?",
                    "--seed",
                    "systems/runtime.md",
                    "--target-bytes",
                    "1",
                    "--max-bytes",
                    "6000",
                    "--target-items",
                    "1",
                    "--max-items",
                    "1",
                ]
            )

        payload = json.loads(stdout.getvalue())
        expectations = contract["expectations"]
        self.assertEqual(exit_code, 0)
        self.assertEqual(set(payload), set(contract["top_level_fields"]))
        self.assertEqual(set(payload["wiki"]), set(contract["wiki_fields"]))
        self.assertEqual(set(payload["query"]), set(contract["query_fields"]))
        self.assertEqual(payload["kind"], expectations["kind"])
        self.assertEqual(payload["contract_version"], expectations["contract_version"])
        self.assertEqual(payload["wiki"]["alias"], expectations["alias"])
        self.assertEqual(payload["wiki"]["schema_version"], expectations["schema_version"])
        self.assertEqual(payload["wiki"]["runtime_contract"], expectations["runtime_contract"])
        self.assertEqual(payload["query"]["question"], expectations["question"])
        self.assertEqual(payload["query"]["shapes"], expectations["shapes"])
        self.assertEqual(payload["query"]["state_view"], expectations["state_view"])
        self.assertEqual(payload["query"]["resolved_seeds"], expectations["resolved_seeds"])

        self.assertEqual(len(payload["evidence"]), 1)
        evidence = payload["evidence"][0]
        self.assertEqual(set(evidence), set(contract["evidence_record_fields"]))
        self.assertEqual(evidence["provider"], expectations["selected_provider"])
        self.assertEqual(evidence["route"], expectations["selected_route"])
        self.assertEqual(evidence["page"], expectations["selected_page"])
        self.assertEqual(evidence["authored_state"], expectations["selected_authored_state"])
        self.assertEqual(evidence["truncated"], expectations["selected_truncated"])
        self.assertIn("[truncated]", evidence["content"])

        self.assertTrue(payload["omissions"])
        self.assertTrue(all(set(item) == set(contract["omission_fields"]) for item in payload["omissions"]))
        self.assertIn(expectations["omission_reason"], {item["reason"] for item in payload["omissions"]})
        self.assertEqual(set(payload["coverage"]), set(contract["coverage_fields"]))
        self.assertEqual(payload["coverage"]["required_roles"], expectations["required_roles"])
        self.assertEqual(payload["coverage"]["covered_roles"], expectations["covered_roles"])
        self.assertEqual(payload["coverage"]["uncovered_roles"], expectations["uncovered_roles"])

        self.assertEqual(set(payload["budget"]), set(contract["budget_fields"]))
        self.assertEqual(set(payload["budget"]["limits"]), set(contract["budget_limit_fields"]))
        self.assertEqual(payload["budget"]["limits"]["target_bytes"], expectations["target_bytes"])
        self.assertEqual(payload["budget"]["limits"]["max_bytes"], expectations["max_bytes"])
        self.assertEqual(payload["budget"]["limits"]["target_items"], expectations["target_items"])
        self.assertEqual(payload["budget"]["limits"]["max_items"], expectations["max_items"])
        self.assertEqual(
            payload["budget"]["target_exceeded_for_coverage"],
            expectations["target_exceeded_for_coverage"],
        )
        self.assertEqual(set(payload["stop"]), set(contract["stop_fields"]))
        self.assertEqual(payload["stop"]["reason"], expectations["stop_reason"])
        self.assertEqual(payload["stop"]["sufficient"], expectations["stop_sufficient"])

        self.assertIsNotNone(payload["continuation"])
        self.assertEqual(set(payload["continuation"]), set(contract["continuation_fields"]))
        self.assertEqual(payload["continuation"]["reason"], expectations["continuation_reason"])
        self.assertEqual(payload["continuation"]["uncovered_roles"], expectations["uncovered_roles"])
        self.assertGreater(payload["continuation"]["remaining_candidate_count"], 0)

        self.assertTrue(payload["diagnostics"])
        self.assertTrue(all(set(item) == set(contract["diagnostic_fields"]) for item in payload["diagnostics"]))
        source_diagnostic = next(
            item for item in payload["diagnostics"] if item["code"] == expectations["diagnostic_code"]
        )
        self.assertEqual(source_diagnostic["provider"], expectations["diagnostic_provider"])
        self.assertEqual(set(payload["reporting"]), set(contract["reporting_fields"]))
        self.assertTrue(
            all(
                set(counts) == set(contract["reporting_count_fields"])
                for counts in payload["reporting"].values()
            )
        )
        self.assertGreaterEqual(payload["reporting"]["omissions"]["total"], len(payload["omissions"]))
        self.assertEqual(payload["reporting"]["omissions"]["returned"], len(payload["omissions"]))
        self.assertEqual(payload["reporting"]["diagnostics"]["returned"], len(payload["diagnostics"]))


if __name__ == "__main__":
    unittest.main()
