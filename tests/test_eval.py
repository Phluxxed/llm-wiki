"""Tests for scripts/eval.py — wiki quality eval (LLM-as-judge metrics).

The judge is injected as a stub (Callable[[str], str] returning canned JSON), so
every metric and the gating layer is exercised with zero tokens/network. Adapter
tests mock subprocess.
"""
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eval as ev  # noqa: E402

ev._JUDGE_RETRY_BACKOFF = 0  # no real sleeping in tests


def write_md(path: Path, frontmatter: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    path.write_text(f"---\n{fm}\n---\n{body}\n", encoding="utf-8")


def conformant_fm(**overrides):
    """An OKF-conformant page frontmatter (all 10 required fields)."""
    fm = {"title": "X", "category": "X", "status": "Live", "owner": "x", "tags": [],
          "created": "2026-06-17", "last_reviewed": "2026-06-17",
          "type": "policy", "description": "d", "timestamp": "2026-06-17T00:00:00Z"}
    fm.update(overrides)
    return fm


PRIMARY_BODY = "## What This Is\nx\n## How It Works\nx\n## Risk Register\nx\n## Prerequisites\nx\n"


class CoreTest(unittest.TestCase):
    def test_parse_plain_object(self):
        self.assertEqual(ev.parse_json_obj('{"score": 0.5, "rationale": "x"}'),
                         {"score": 0.5, "rationale": "x"})

    def test_parse_fenced_json(self):
        text = 'Here you go:\n```json\n{"score": 1}\n```\nthanks'
        self.assertEqual(ev.parse_json_obj(text), {"score": 1})

    def test_parse_object_embedded_in_prose(self):
        self.assertEqual(ev.parse_json_obj('result: {"a": 1} done'), {"a": 1})

    def test_array_returns_empty_dict(self):
        # parse_json_obj ALWAYS returns a dict so callers can .get() safely.
        self.assertEqual(ev.parse_json_obj('[1, 2, 3]'), {})

    def test_garbage_returns_empty_dict(self):
        self.assertEqual(ev.parse_json_obj("not json at all"), {})
        self.assertEqual(ev.parse_json_obj(""), {})
        self.assertEqual(ev.parse_json_obj(None), {})

    def test_metric_result_shape(self):
        r = ev.MetricResult("grounding", 0.9, True, detail="why", insights="how")
        self.assertEqual(r.name, "grounding")
        self.assertEqual(r.score, 0.9)
        self.assertTrue(r.passed)
        self.assertEqual(r.detail, "why")
        self.assertEqual(r.insights, "how")
        self.assertEqual(r.extra, {})

    def test_metric_result_none_score_allowed(self):
        # None = metric self-skipped (e.g. no source to ground against).
        r = ev.MetricResult("grounding", None, True, detail="no source")
        self.assertIsNone(r.score)

    def test_body_strips_frontmatter(self):
        # Judges must score prose, not treat frontmatter keys as factual claims.
        text = "---\ntitle: X\nowner: vik\n---\n## Heading\nReal content.\n"
        body = ev._body(text)
        self.assertNotIn("owner: vik", body)
        self.assertIn("Real content.", body)

    def test_body_no_frontmatter_passthrough(self):
        self.assertEqual(ev._body("# Just markdown\n"), "# Just markdown\n")


class DeterministicTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_structural_clean_passes(self):
        write_md(self.wiki_root / "policies/a.md", conformant_fm(title="A"), PRIMARY_BODY)
        pages = ev.load_pages(self.wiki_root)
        r = ev.check_structural(pages, self.wiki_root)
        self.assertEqual(r.score, 1.0)
        self.assertTrue(r.passed)

    def test_structural_missing_section_fails(self):
        write_md(self.wiki_root / "policies/a.md", conformant_fm(title="A"), "no sections here")
        pages = ev.load_pages(self.wiki_root)
        r = ev.check_structural(pages, self.wiki_root)
        self.assertLess(r.score, 1.0)
        self.assertFalse(r.passed)

    def test_near_dup_same_source_is_candidate(self):
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="A", source="sources/x.pdf"), PRIMARY_BODY)
        write_md(self.wiki_root / "papers/b.md", conformant_fm(title="B", source="sources/x.pdf"), PRIMARY_BODY)
        pages = ev.load_pages(self.wiki_root)
        pairs = ev.near_duplicate_candidates(pages)
        self.assertEqual(len(pairs), 1)
        self.assertIn("source", pairs[0][2].lower())

    def test_near_dup_title_overlap_is_candidate(self):
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="Customer Data Retention Policy"), PRIMARY_BODY)
        write_md(self.wiki_root / "papers/b.md", conformant_fm(title="Customer Data Retention Rules"), PRIMARY_BODY)
        pairs = ev.near_duplicate_candidates(ev.load_pages(self.wiki_root))
        self.assertEqual(len(pairs), 1)

    def test_unrelated_pages_not_candidates(self):
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="Apples", tags=["fruit"]), PRIMARY_BODY)
        write_md(self.wiki_root / "papers/b.md", conformant_fm(title="Networking", tags=["infra"]), PRIMARY_BODY)
        self.assertEqual(ev.near_duplicate_candidates(ev.load_pages(self.wiki_root)), [])


def stub(payload: str):
    """A judge that returns a fixed string regardless of prompt."""
    return lambda prompt: payload


class JudgeMetricsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)
        (self.wiki_root / "sources").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _derived_page(self, name="papers/a.md", src="sources/x.md", body="Claim: sky is blue."):
        (self.wiki_root / src).write_text("The sky is blue.\n", encoding="utf-8")
        write_md(self.wiki_root / name, conformant_fm(title="A", type="paper", source=src), body)

    # ── grounding ──
    def test_grounding_supported_passes(self):
        self._derived_page()
        pages = ev.load_pages(self.wiki_root)
        r = ev.check_grounding(pages, self.wiki_root, stub('{"score":1.0,"unsupported":[]}'))
        self.assertEqual(r.score, 1.0)
        self.assertTrue(r.passed)

    def test_grounding_unsupported_fails(self):
        self._derived_page()
        pages = ev.load_pages(self.wiki_root)
        r = ev.check_grounding(pages, self.wiki_root, stub('{"score":0.5,"unsupported":["sky is green"]}'))
        self.assertEqual(r.score, 0.5)
        self.assertFalse(r.passed)

    def test_grounding_skips_when_no_derived_pages(self):
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="A", type="paper"), PRIMARY_BODY)
        pages = ev.load_pages(self.wiki_root)
        r = ev.check_grounding(pages, self.wiki_root, stub('{"score":0.0}'))
        self.assertIsNone(r.score)   # nothing to ground → self-skip, not a 0
        self.assertTrue(r.passed)
        self.assertFalse(r.error)    # legitimate N/A, NOT a judge error

    # ── contradictions ──
    def test_contradictions_clean_passes(self):
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="A"), PRIMARY_BODY)
        write_md(self.wiki_root / "papers/b.md", conformant_fm(title="B"), PRIMARY_BODY)
        r = ev.check_contradictions(ev.load_pages(self.wiki_root), stub('{"score":1.0,"conflicts":[]}'))
        self.assertEqual(r.score, 1.0)
        self.assertTrue(r.passed)

    def test_contradictions_conflict_fails(self):
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="A"), PRIMARY_BODY)
        write_md(self.wiki_root / "papers/b.md", conformant_fm(title="B"), PRIMARY_BODY)
        r = ev.check_contradictions(ev.load_pages(self.wiki_root),
                                    stub('{"score":0.0,"conflicts":["a says X, b says Y"]}'))
        self.assertFalse(r.passed)
        self.assertIn("X", r.detail)

    def test_malformed_judge_is_loud_error_not_silent_pass(self):
        # A judge that was expected to produce a verdict but returns garbage is an
        # ERROR (loud, fail-closed) — never a silent skip that passes the gate.
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="A"), PRIMARY_BODY)
        r = ev.check_contradictions(ev.load_pages(self.wiki_root), stub("not json"))
        self.assertTrue(r.error)
        self.assertFalse(r.passed)
        self.assertIsNone(r.score)

    def test_judge_raising_is_error(self):
        def boom(prompt):
            raise RuntimeError("auth failed")
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="A"), PRIMARY_BODY)
        r = ev.check_contradictions(ev.load_pages(self.wiki_root), boom)
        self.assertTrue(r.error)
        self.assertFalse(r.passed)

    def test_judge_json_retries_transient_failure(self):
        # An agent sometimes returns a transient empty reply; a retry should recover.
        calls = {"n": 0}

        def flaky(prompt):
            calls["n"] += 1
            return "" if calls["n"] == 1 else '{"score": 1.0}'

        self.assertEqual(ev._judge_json(flaky, "p"), {"score": 1.0})
        self.assertEqual(calls["n"], 2)  # failed once, retried, recovered

    def test_judge_json_raises_on_persistent_failure(self):
        with self.assertRaises(ev.JudgeError):
            ev._judge_json(lambda p: "not json", "p")

    # ── redundancy ──
    def test_redundancy_scored(self):
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="A"), PRIMARY_BODY)
        r = ev.check_redundancy(ev.load_pages(self.wiki_root), stub('{"score":0.8}'))
        self.assertEqual(r.score, 0.8)
        self.assertTrue(r.passed)   # >= 0.6 default

    # ── disambiguation ──
    def test_disambiguation_no_candidates_passes(self):
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="Apples", tags=["fruit"]), PRIMARY_BODY)
        r = ev.check_disambiguation(ev.load_pages(self.wiki_root), stub('{"score":0.0}'))
        self.assertEqual(r.score, 1.0)   # nothing ambiguous
        self.assertTrue(r.passed)

    def test_disambiguation_candidate_judged_distinct_passes(self):
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="A", source="sources/x.md"), PRIMARY_BODY)
        write_md(self.wiki_root / "papers/b.md", conformant_fm(title="B", source="sources/x.md"), PRIMARY_BODY)
        r = ev.check_disambiguation(ev.load_pages(self.wiki_root), stub('{"score":1.0,"distinct":true}'))
        self.assertEqual(r.score, 1.0)
        self.assertTrue(r.passed)

    def test_disambiguation_candidate_not_distinct_fails(self):
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="A", source="sources/x.md"), PRIMARY_BODY)
        write_md(self.wiki_root / "papers/b.md", conformant_fm(title="B", source="sources/x.md"), PRIMARY_BODY)
        r = ev.check_disambiguation(ev.load_pages(self.wiki_root), stub('{"score":0.0,"distinct":false}'))
        self.assertLess(r.score, 1.0)
        self.assertFalse(r.passed)


class AdapterTest(unittest.TestCase):
    # claude_judge uses the Claude Agent SDK (verified live, tools-off/single-turn);
    # it's not unit-mocked here. codex stays on the CLI and is tested below.

    def test_codex_judge_reads_output_file_and_uses_devnull(self):
        import unittest.mock as mock

        def side_effect(cmd, **kwargs):
            # codex writes its final message to the --output-last-message path
            i = cmd.index("--output-last-message")
            Path(cmd[i + 1]).write_text('{"score":0.4}', encoding="utf-8")
            return mock.Mock(stdout="", returncode=0)

        with mock.patch.object(ev.subprocess, "run", side_effect=side_effect) as run:
            out = ev.codex_judge("p")
        self.assertEqual(run.call_args.kwargs.get("stdin"), ev.subprocess.DEVNULL)
        self.assertEqual(ev.parse_json_obj(out), {"score": 0.4})

    def test_detect_judge_none(self):
        judge, label = ev.detect_judge("none")
        self.assertIsNone(judge)
        self.assertEqual(label, "none")

    def test_detect_judge_explicit_claude(self):
        judge, label = ev.detect_judge("claude")
        self.assertTrue(callable(judge))
        self.assertEqual(label, "claude")

    def test_detect_judge_autodetect_prefers_present_cli(self):
        import unittest.mock as mock
        with mock.patch.object(ev.shutil, "which", side_effect=lambda n: "/bin/codex" if n == "codex" else None):
            judge, label = ev.detect_judge(None)
        self.assertEqual(label, "codex")
        self.assertTrue(callable(judge))

    def test_detect_judge_autodetect_none_available(self):
        import unittest.mock as mock
        with mock.patch.object(ev.shutil, "which", return_value=None):
            judge, label = ev.detect_judge(None)
        self.assertIsNone(judge)
        self.assertEqual(label, "none")

    def test_custom_judge_command(self):
        import unittest.mock as mock
        judge, label = ev.detect_judge("mycli --flag")
        self.assertTrue(callable(judge))
        fake = mock.Mock(stdout='{"score":0.9}', returncode=0)
        with mock.patch.object(ev.subprocess, "run", return_value=fake) as run:
            out = judge("prompt text")
        self.assertEqual(run.call_args.kwargs.get("stdin"), ev.subprocess.DEVNULL)
        self.assertEqual(ev.parse_json_obj(out), {"score": 0.9})


class GatingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_default_thresholds(self):
        th = ev.load_thresholds(self.wiki_root)
        self.assertEqual(th["absence_of_contradictions"], 1.0)
        self.assertEqual(th["grounding"], 0.95)

    def test_thresholds_override_from_file(self):
        (self.wiki_root / ".eval").mkdir()
        (self.wiki_root / ".eval/thresholds.json").write_text('{"grounding": 0.8}')
        th = ev.load_thresholds(self.wiki_root)
        self.assertEqual(th["grounding"], 0.8)          # overridden
        self.assertEqual(th["redundancy_index"], 0.6)   # default retained

    def test_metric_above_threshold_passes(self):
        runs = [[ev.MetricResult("grounding", 0.97, True)]]
        agg = ev.aggregate(runs, ev.load_thresholds(self.wiki_root))
        row = next(r for r in agg if r["name"] == "grounding")
        self.assertTrue(row["passed"])

    def test_metric_below_threshold_fails(self):
        runs = [[ev.MetricResult("grounding", 0.5, False)]]
        agg = ev.aggregate(runs, ev.load_thresholds(self.wiki_root))
        self.assertFalse(next(r for r in agg if r["name"] == "grounding")["passed"])

    def test_none_score_does_not_fail_gate(self):
        runs = [[ev.MetricResult("grounding", None, True, "no source to ground")]]
        agg = ev.aggregate(runs, ev.load_thresholds(self.wiki_root))
        self.assertTrue(next(r for r in agg if r["name"] == "grounding")["passed"])

    def test_multi_run_averaging(self):
        runs = [[ev.MetricResult("grounding", 0.90, False)],
                [ev.MetricResult("grounding", 1.00, True)]]
        agg = ev.aggregate(runs, ev.load_thresholds(self.wiki_root))
        row = next(r for r in agg if r["name"] == "grounding")
        self.assertAlmostEqual(row["score"], 0.95)
        self.assertTrue(row["passed"])  # mean 0.95 >= 0.95

    def test_overall_passed_false_if_any_metric_fails(self):
        agg = [{"name": "a", "passed": True}, {"name": "b", "passed": False}]
        self.assertFalse(ev.overall_passed(agg))
        self.assertTrue(ev.overall_passed([{"name": "a", "passed": True}]))

    def test_judge_error_fails_gate_closed(self):
        # A judge error must fail the gate even with no score (fail-closed).
        runs = [[ev.MetricResult("absence_of_contradictions", None, False,
                                 "JUDGE ERROR — boom", error=True)]]
        agg = ev.aggregate(runs, ev.load_thresholds(self.wiki_root))
        row = next(r for r in agg if r["name"] == "absence_of_contradictions")
        self.assertTrue(row["error"])
        self.assertFalse(row["passed"])
        self.assertFalse(ev.overall_passed(agg))

    def test_legit_skip_passes_gate(self):
        # None score with no error = legitimate N/A → passes.
        runs = [[ev.MetricResult("grounding", None, True, "no sourced pages")]]
        agg = ev.aggregate(runs, ev.load_thresholds(self.wiki_root))
        self.assertTrue(next(r for r in agg if r["name"] == "grounding")["passed"])


class OrchestrationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)
        (self.wiki_root / "sources").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _wiki_with_derived(self):
        (self.wiki_root / "sources/x.md").write_text("The sky is blue.\n", encoding="utf-8")
        write_md(self.wiki_root / "papers/a.md",
                 conformant_fm(title="A", type="paper", source="sources/x.md"), PRIMARY_BODY)

    def test_run_once_with_stub_runs_all_metrics(self):
        self._wiki_with_derived()
        names = {r.name for r in ev.run_once(self.wiki_root, stub('{"score":1.0}'))}
        self.assertEqual(names, {"structural_validity", "grounding",
                                 "absence_of_contradictions", "redundancy_index",
                                 "disambiguation"})

    def test_run_once_no_judge_skips_judge_metrics(self):
        self._wiki_with_derived()
        results = {r.name: r for r in ev.run_once(self.wiki_root, None)}
        self.assertEqual(results["structural_validity"].score, 1.0)   # deterministic still runs
        self.assertIsNone(results["grounding"].score)                 # judge metric skipped
        self.assertIsNone(results["absence_of_contradictions"].score)

    def test_write_run_record_creates_eval_dir(self):
        self._wiki_with_derived()
        runs = [ev.run_once(self.wiki_root, stub('{"score":1.0}'))]
        agg = ev.aggregate(runs, ev.load_thresholds(self.wiki_root))
        path = ev.write_run_record(self.wiki_root, agg, "stub")
        self.assertTrue(path.exists())
        self.assertTrue((self.wiki_root / ".eval" / "history.json").exists())

    def test_eval_dir_excluded_from_lint_and_render(self):
        import lint
        import render
        self.assertIn(".eval", lint.EXCLUDE_DIRS)
        self.assertIn(".eval", render.EXCLUDE_DIRS)

    def test_build_report_has_verdict_and_names(self):
        agg = [{"name": "grounding", "score": 0.5, "threshold": 0.95, "passed": False,
                "runs": 1, "runs_passed": 0, "rationale": "x", "insights": ""}]
        report = ev.build_report(agg, "codex")
        self.assertIn("grounding", report)
        self.assertIn("FAIL", report)

    def test_build_report_shows_error_loudly(self):
        agg = [{"name": "absence_of_contradictions", "score": None, "threshold": 1.0,
                "passed": False, "error": True, "runs": 1, "runs_passed": 0,
                "rationale": "JUDGE ERROR — boom", "insights": ""}]
        self.assertIn("ERROR", ev.build_report(agg, "claude"))

    def test_gate_exit_code(self):
        self.assertEqual(ev.gate_exit_code([{"passed": True}]), 0)
        self.assertEqual(ev.gate_exit_code([{"passed": True}, {"passed": False}]), 1)


if __name__ == "__main__":
    unittest.main()
