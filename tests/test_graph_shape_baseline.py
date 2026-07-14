import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def load_baseline_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "graph_shape_baseline.py"
    spec = importlib.util.spec_from_file_location("llm_wiki_graph_shape_baseline_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FixtureContractTests(unittest.TestCase):
    def setUp(self):
        self.baseline = load_baseline_module()
        self.contract_path = Path(__file__).parent / "fixtures" / "graph_shape_traversal_stage3.json"

    def test_contract_has_mirrored_question_shapes(self):
        contract = self.baseline.load_contract(self.contract_path)

        by_corpus = {
            corpus: {fixture["shape"] for fixture in contract["fixtures"] if fixture["corpus"] == corpus}
            for corpus in contract["corpora"]
        }
        expected = {
            "direct_relation",
            "meaningful_bridge",
            "false_hub_shortcut",
            "exact_attribute",
            "cannot_answer",
        }
        self.assertEqual(len(contract["fixtures"]), 10)
        self.assertEqual(by_corpus["ai_graph_ideas"], expected)
        self.assertEqual(by_corpus["brain"], expected)

    def test_contract_rejects_duplicate_fixture_ids(self):
        contract = self.baseline.load_contract(self.contract_path)
        contract["fixtures"][1]["id"] = contract["fixtures"][0]["id"]

        with self.assertRaisesRegex(ValueError, "duplicate fixture id"):
            self.baseline.validate_contract(contract)

    def test_corpus_digest_is_content_and_path_stable(self):
        first = {
            "b.md": SimpleNamespace(text="beta"),
            "a.md": SimpleNamespace(text="alpha"),
        }
        reordered = {
            "a.md": SimpleNamespace(text="alpha"),
            "b.md": SimpleNamespace(text="beta"),
        }

        self.assertEqual(
            self.baseline._corpus_digest(first),
            self.baseline._corpus_digest(reordered),
        )
        reordered["b.md"] = SimpleNamespace(text="changed")
        self.assertNotEqual(
            self.baseline._corpus_digest(first),
            self.baseline._corpus_digest(reordered),
        )

    def test_benchmark_rejects_corpus_drift_during_a_run(self):
        corpus = {
            "root": Path("/wiki"),
            "config": SimpleNamespace(content=object()),
            "input_digest": self.baseline._corpus_digest(
                {"page.md": SimpleNamespace(text="before")}
            ),
        }

        with patch.object(
            self.baseline,
            "collect_pages",
            return_value={"page.md": SimpleNamespace(text="after")},
        ):
            with self.assertRaisesRegex(RuntimeError, "corpus changed during benchmark"):
                self.baseline._assert_corpora_unchanged({"brain": corpus})


class TraceScoringTests(unittest.TestCase):
    def setUp(self):
        self.baseline = load_baseline_module()

    def test_scores_endpoint_path_bridge_and_required_literals_separately(self):
        fixture = {
            "expected_pages": ["paper.md", "idea.md"],
            "bridge_paths_any": [["paper.md", "idea.md"]],
            "bridge_literals_any": ["local experiment"],
            "forbidden_paths": [],
            "required_literals": ["0.583"],
            "answerable": True,
        }
        trace = {
            "pages": ["paper.md", "idea.md"],
            "paths": [["paper.md", "idea.md"]],
            "content": "The local experiment reported 0.583.",
            "sufficient": True,
        }

        score = self.baseline.score_trace(fixture, trace)

        self.assertEqual(score["endpoint_recall"], 1.0)
        self.assertTrue(score["path_complete"])
        self.assertTrue(score["bridge_evidence_complete"])
        self.assertEqual(score["required_literal_recall"], 1.0)

    def test_flags_a_forbidden_hub_path(self):
        fixture = {
            "expected_pages": ["left.md", "right.md"],
            "bridge_paths_any": [],
            "bridge_literals_any": [],
            "forbidden_paths": [["left.md", "hub.md", "right.md"]],
            "required_literals": [],
            "answerable": False,
        }
        trace = {
            "pages": ["left.md", "hub.md", "right.md"],
            "paths": [["left.md", "hub.md", "right.md"]],
            "content": "",
            "sufficient": True,
        }

        score = self.baseline.score_trace(fixture, trace)

        self.assertTrue(score["unsupported_shortcut"])
        self.assertFalse(score["refusal_ready"])

    def test_false_hub_fixture_rejects_an_alternative_unapproved_path(self):
        fixture = {
            "shape": "false_hub_shortcut",
            "expected_pages": ["left.md", "right.md"],
            "bridge_paths_any": [],
            "bridge_literals_any": [],
            "forbidden_paths": [["left.md", "known-hub.md", "right.md"]],
            "required_literals": [],
            "answerable": False,
        }
        trace = {
            "pages": ["left.md", "other-hub.md", "right.md"],
            "paths": [["left.md", "other-hub.md", "right.md"]],
            "content": "",
            "sufficient": None,
        }

        score = self.baseline.score_trace(fixture, trace)

        self.assertTrue(score["unsupported_shortcut"])

    def test_cannot_answer_needs_an_explicit_insufficient_stop(self):
        fixture = {
            "expected_pages": [],
            "bridge_paths_any": [],
            "bridge_literals_any": [],
            "forbidden_paths": [],
            "required_literals": [],
            "answerable": False,
        }

        score = self.baseline.score_trace(
            fixture,
            {"pages": [], "paths": [], "content": "", "sufficient": False},
        )

        self.assertTrue(score["refusal_ready"])

    def test_record_trace_reconstructs_selected_graph_path(self):
        fixture = {
            "expected_pages": ["left.md", "right.md"],
            "bridge_paths_any": [],
            "bridge_literals_any": [],
            "forbidden_paths": [["left.md", "hub.md", "right.md"]],
            "required_literals": [],
            "answerable": False,
        }
        records = [
            {
                "id": "graph:left.md->hub.md:body_link",
                "provider": "graph",
                "route": "connecting_path",
                "page": "left.md",
                "source": None,
                "locator": {"start_line": 1, "end_line": 1},
                "content": "[hub](hub.md)",
            },
            {
                "id": "graph:hub.md->right.md:body_link",
                "provider": "graph",
                "route": "connecting_path",
                "page": "hub.md",
                "source": None,
                "locator": {"start_line": 2, "end_line": 2},
                "content": "[right](right.md)",
            },
        ]

        trace = self.baseline.record_trace(
            records,
            fixture=fixture,
            generic_hubs={"hub.md"},
            sufficient=True,
            tool_calls=0,
            latency_ms=1.5,
            classified_shapes=["relationship"],
            diagnostics=[],
        )

        self.assertEqual(trace["pages"], ["hub.md", "left.md", "right.md"])
        self.assertIn(["left.md", "hub.md", "right.md"], trace["paths"])
        self.assertEqual(trace["evidence_bytes"], 30)
        self.assertEqual(trace["estimated_tokens"], 8)
        self.assertEqual(trace["generic_hub_path_rate"], 1.0)

    def test_record_trace_reconstructs_loci_evidence_backed_path(self):
        fixture = {
            "expected_pages": ["left.md", "right.md"],
            "bridge_paths_any": [["left.md", "hub.md", "right.md"]],
            "bridge_literals_any": ["authored bridge"],
            "forbidden_paths": [],
            "required_literals": [],
            "answerable": True,
        }
        records = [
            {
                "id": "graph:loci:opaque-hash",
                "provider": "graph",
                "route": "evidence_backed_path",
                "page": "hub.md",
                "source": None,
                "locator": {
                    "nodes": [
                        {"id": "left", "file": "left.md"},
                        {"id": "hub", "file": "hub.md"},
                        {"id": "right", "file": "right.md"},
                    ]
                },
                "content": "authored bridge",
            }
        ]

        trace = self.baseline.record_trace(
            records,
            fixture=fixture,
            generic_hubs={"hub.md"},
            sufficient=True,
            tool_calls=4,
            latency_ms=2.5,
            classified_shapes=["relationship"],
            diagnostics=[],
        )

        self.assertEqual(trace["pages"], ["hub.md", "left.md", "right.md"])
        self.assertEqual(trace["paths"], [["left.md", "hub.md", "right.md"]])
        self.assertEqual(trace["tool_calls"], 4)

    def test_record_trace_does_not_stitch_separate_loci_paths(self):
        fixture = {
            "shape": "false_hub_shortcut",
            "expected_pages": ["left.md", "right.md"],
            "bridge_paths_any": [],
            "bridge_literals_any": [],
            "forbidden_paths": [["left.md", "hub.md", "right.md"]],
            "required_literals": [],
            "answerable": False,
        }

        def path_record(record_id, nodes):
            return {
                "id": record_id,
                "provider": "graph",
                "route": "evidence_backed_path",
                "page": nodes[0],
                "locator": {
                    "nodes": [
                        {"id": f"{file_path}::root", "file": file_path}
                        for file_path in nodes
                    ]
                },
                "content": "exact edge evidence",
            }

        trace = self.baseline.record_trace(
            [
                path_record("graph:loci:first", ["left.md", "hub.md"]),
                path_record("graph:loci:second", ["hub.md", "right.md"]),
            ],
            fixture=fixture,
            generic_hubs={"hub.md"},
            sufficient=False,
            tool_calls=1,
            latency_ms=1.0,
            classified_shapes=["relationship"],
            diagnostics=[],
        )

        self.assertNotIn(["left.md", "hub.md", "right.md"], trace["paths"])
        self.assertFalse(self.baseline.score_trace(fixture, trace)["unsupported_shortcut"])

    def test_compiled_response_shapes_uses_public_query_envelope(self):
        response = {"query": {"shapes": ["lookup", "relationship"]}}

        self.assertEqual(
            self.baseline.compiled_response_shapes(response),
            ["lookup", "relationship"],
        )

    def test_summary_does_not_count_empty_endpoint_gold_as_perfect_recall(self):
        def result(fixture_id, expected_pages, endpoint_recall):
            return {
                "id": fixture_id,
                "shape": "cannot_answer",
                "expected_pages": expected_pages,
                "routes": {
                    "route": {
                        "score": {
                            "endpoint_recall": endpoint_recall,
                            "path_complete": None,
                            "bridge_evidence_complete": None,
                            "required_literal_recall": 1.0,
                            "unsupported_shortcut": False,
                            "refusal_ready": False,
                        },
                        "trace": {
                            "evidence_bytes": 0,
                            "estimated_tokens": 0,
                            "tool_calls": 0,
                            "latency_ms": 0,
                            "generic_hub_path_rate": 0,
                        },
                    }
                },
            }

        summary = self.baseline.summarize_results(
            [
                result("empty", [], 1.0),
                result("miss", ["expected.md"], 0.0),
            ]
        )

        self.assertEqual(summary["route"]["mean_endpoint_recall"], 0.0)


if __name__ == "__main__":
    unittest.main()
