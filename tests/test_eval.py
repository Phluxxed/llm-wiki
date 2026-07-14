"""Tests for scripts/eval.py — wiki quality eval (LLM-as-judge metrics).

The judge is injected as a stub (Callable[[str], str] returning canned JSON), so
every metric and the gating layer is exercised with zero tokens/network. Adapter
tests mock subprocess.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_source_reader_does_not_escape_sources_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            wiki_root = Path(tmp)
            private = wiki_root / "private.txt"
            private.write_text("private material\n")
            self.assertEqual(ev._read_source(wiki_root, str(private.resolve())), "")


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

    def test_emit_brief_lists_grounding_evidence(self):
        (self.wiki_root / "sources").mkdir()
        (self.wiki_root / "sources/repo.md").write_text("manifest\n")
        (self.wiki_root / "sources/repo-evidence.md").write_text("evidence\n")
        write_md(
            self.wiki_root / "papers/a.md",
            conformant_fm(
                title="A", source="sources/repo.md", source_mode="manifest",
                evidence="sources/repo-evidence.md",
            ),
            PRIMARY_BODY,
        )
        brief = ev.emit_brief(self.wiki_root).read_text()
        self.assertIn("sources/repo.md", brief)
        self.assertIn("sources/repo-evidence.md", brief)


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

    def test_grounding_manifest_without_evidence_fails_before_judging(self):
        src = "sources/repo.md"
        (self.wiki_root / src).write_text("Repository: example/repo\nURL: https://example.com/repo\n")
        write_md(
            self.wiki_root / "papers/a.md",
            conformant_fm(title="A", type="paper", source=src, source_mode="manifest"),
            "Claim derived from repository inspection.",
        )

        def should_not_run(_prompt):
            self.fail("judge should not run without required grounding evidence")

        r = ev.check_grounding(ev.load_pages(self.wiki_root), self.wiki_root, should_not_run)
        self.assertEqual(r.score, 0.0)
        self.assertFalse(r.passed)
        self.assertIn("requires evidence", r.detail)
        self.assertEqual(r.extra["missing_evidence"], ["papers/a.md"])

    def test_grounding_manifest_uses_evidence_pack(self):
        src = "sources/repo.md"
        evidence = "sources/repo-evidence.md"
        (self.wiki_root / src).write_text("Repository: example/repo\nURL: https://example.com/repo\n")
        (self.wiki_root / evidence).write_text("The repository exposes four read-only graph tools.\n")
        write_md(
            self.wiki_root / "papers/a.md",
            conformant_fm(
                title="A", type="paper", source=src, source_mode="manifest",
                evidence=[evidence],
            ),
            "The repository exposes four read-only graph tools.",
        )
        prompts = []

        def judge(prompt):
            prompts.append(prompt)
            return '{"score":1.0,"unsupported":[]}'

        r = ev.check_grounding(ev.load_pages(self.wiki_root), self.wiki_root, judge)
        self.assertTrue(r.passed)
        self.assertIn("Repository: example/repo", prompts[0])
        self.assertIn("four read-only graph tools", prompts[0])
        self.assertIn(evidence, prompts[0])

    def test_grounding_pdf_uses_text_evidence_pack(self):
        src = "sources/paper.pdf"
        evidence = "sources/paper-evidence.md"
        (self.wiki_root / src).write_bytes(b"%PDF-1.7\x00binary")
        (self.wiki_root / evidence).write_text("The study reports a 12 percent improvement.\n")
        write_md(
            self.wiki_root / "papers/a.md",
            conformant_fm(title="A", type="paper", source=src, evidence=evidence),
            "The study reports a 12 percent improvement.",
        )
        prompts = []

        def judge(prompt):
            prompts.append(prompt)
            return '{"score":1.0,"unsupported":[]}'

        r = ev.check_grounding(ev.load_pages(self.wiki_root), self.wiki_root, judge)
        self.assertTrue(r.passed)
        self.assertIn("12 percent improvement", prompts[0])
        self.assertNotIn("%PDF-1.7", prompts[0])

    def test_grounding_missing_primary_source_fails_even_with_evidence(self):
        evidence = "sources/repo-evidence.md"
        (self.wiki_root / evidence).write_text("Repository evidence.\n")
        write_md(
            self.wiki_root / "papers/a.md",
            conformant_fm(
                title="A", type="paper", source="sources/missing-repo.md",
                source_mode="manifest", evidence=evidence,
            ),
            "Repository claim.",
        )
        r = ev.check_grounding(
            ev.load_pages(self.wiki_root), self.wiki_root,
            lambda _prompt: self.fail("judge should not run with a missing primary source"),
        )
        self.assertEqual(r.score, 0.0)
        self.assertIn("does not exist", r.detail)

    def test_grounding_oversized_evidence_fails_instead_of_truncating(self):
        src = "sources/repo.md"
        evidence = "sources/repo-evidence.md"
        (self.wiki_root / src).write_text("manifest\n")
        (self.wiki_root / evidence).write_text("x" * (ev.GROUNDING_MATERIAL_LIMIT + 1))
        write_md(
            self.wiki_root / "papers/a.md",
            conformant_fm(
                title="A", type="paper", source=src, source_mode="manifest",
                evidence=evidence,
            ),
            "Repository claim.",
        )
        r = ev.check_grounding(
            ev.load_pages(self.wiki_root), self.wiki_root,
            lambda _prompt: self.fail("judge should not run with oversized evidence"),
        )
        self.assertEqual(r.score, 0.0)
        self.assertIn("exceeds", r.detail)

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

    def test_documented_source_drift_is_reported_without_failing(self):
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="A"), PRIMARY_BODY)
        payload = (
            '{"score":0.5,"conflicts":[{"description":"README says 7 tools; current code has 4",'
            '"classification":"documented_source_drift"}]}'
        )
        r = ev.check_contradictions(ev.load_pages(self.wiki_root), stub(payload))
        self.assertEqual(r.score, 1.0)
        self.assertTrue(r.passed)
        self.assertIn("documented source drift", r.detail.lower())
        self.assertEqual(len(r.extra["documented_source_drift"]), 1)

    def test_unresolved_typed_contradiction_still_fails(self):
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="A"), PRIMARY_BODY)
        payload = (
            '{"score":1.0,"conflicts":[{"description":"Page A says X; page B says Y",'
            '"classification":"unresolved"}]}'
        )
        r = ev.check_contradictions(ev.load_pages(self.wiki_root), stub(payload))
        self.assertLess(r.score, 1.0)
        self.assertFalse(r.passed)
        self.assertIn("Page A says X", r.detail)

    def test_scoped_contradictions_report_but_do_not_fail_unchanged_conflicts(self):
        write_md(self.wiki_root / "papers/changed.md", conformant_fm(title="Changed"), PRIMARY_BODY)
        write_md(self.wiki_root / "papers/old-a.md", conformant_fm(title="Old A"), PRIMARY_BODY)
        write_md(self.wiki_root / "papers/old-b.md", conformant_fm(title="Old B"), PRIMARY_BODY)
        payload = (
            '{"score":0.0,"conflicts":[{"description":"Old A says X; Old B says Y",'
            '"classification":"unresolved","changed_page":null}]}'
        )
        r = ev.check_contradictions(
            ev.load_pages(self.wiki_root), stub(payload), focus_files={"papers/changed.md"},
        )
        self.assertTrue(r.passed)
        self.assertEqual(r.score, 1.0)
        self.assertEqual(r.extra["conflicts"], [])
        self.assertEqual(r.extra["out_of_scope_conflicts"], ["Old A says X; Old B says Y"])

    def test_scoped_contradictions_fail_when_changed_page_is_involved(self):
        write_md(self.wiki_root / "papers/changed.md", conformant_fm(title="Changed"), PRIMARY_BODY)
        write_md(self.wiki_root / "papers/old.md", conformant_fm(title="Old"), PRIMARY_BODY)
        payload = (
            '{"score":1.0,"conflicts":[{"description":"Changed says X; Old says Y",'
            '"classification":"unresolved","changed_page":"papers/changed.md"}]}'
        )
        r = ev.check_contradictions(
            ev.load_pages(self.wiki_root), stub(payload), focus_files={"papers/changed.md"},
        )
        self.assertFalse(r.passed)
        self.assertEqual(r.extra["conflicts"], ["Changed says X; Old says Y"])

    def test_scoped_contradictions_fail_loudly_on_missing_scope_attribution(self):
        write_md(self.wiki_root / "papers/changed.md", conformant_fm(title="Changed"), PRIMARY_BODY)
        payload = (
            '{"score":0.0,"conflicts":[{"description":"Unattributed conflict",'
            '"classification":"unresolved"}]}'
        )
        r = ev.check_contradictions(
            ev.load_pages(self.wiki_root), stub(payload), focus_files={"papers/changed.md"},
        )
        self.assertTrue(r.error)
        self.assertFalse(r.passed)

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

    def test_disambiguation_distinct_boolean_overrides_fuzzy_score(self):
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="A", source="sources/x.md"), PRIMARY_BODY)
        write_md(self.wiki_root / "papers/b.md", conformant_fm(title="B", source="sources/x.md"), PRIMARY_BODY)
        r = ev.check_disambiguation(
            ev.load_pages(self.wiki_root),
            stub('{"score":0.7,"distinct":true,"rationale":"Related but separate layers."}'),
        )
        self.assertEqual(r.score, 1.0)
        self.assertTrue(r.passed)
        self.assertEqual(r.extra["near_duplicates"], [])

    def test_disambiguation_not_distinct_boolean_overrides_fuzzy_score(self):
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="A", source="sources/x.md"), PRIMARY_BODY)
        write_md(self.wiki_root / "papers/b.md", conformant_fm(title="B", source="sources/x.md"), PRIMARY_BODY)
        r = ev.check_disambiguation(
            ev.load_pages(self.wiki_root),
            stub('{"score":1.0,"distinct":false,"rationale":"Same concept."}'),
        )
        self.assertEqual(r.score, 0.0)
        self.assertFalse(r.passed)

    def test_disambiguation_candidate_not_distinct_fails(self):
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="A", source="sources/x.md"), PRIMARY_BODY)
        write_md(self.wiki_root / "papers/b.md", conformant_fm(title="B", source="sources/x.md"), PRIMARY_BODY)
        r = ev.check_disambiguation(ev.load_pages(self.wiki_root), stub('{"score":0.0,"distinct":false}'))
        self.assertLess(r.score, 1.0)
        self.assertFalse(r.passed)

    def test_scoped_disambiguation_only_judges_pairs_touching_changed_pages(self):
        for path, title, source in [
            ("papers/changed.md", "Changed Alpha", "sources/alpha.md"),
            ("papers/alpha-peer.md", "Alpha Peer", "sources/alpha.md"),
            ("papers/old-one.md", "Old Gamma", "sources/gamma.md"),
            ("papers/old-two.md", "Old Delta", "sources/gamma.md"),
        ]:
            write_md(self.wiki_root / path, conformant_fm(title=title, source=source), PRIMARY_BODY)
        prompts = []

        def judge(prompt):
            prompts.append(prompt)
            return '{"distinct":true,"rationale":"separate"}'

        r = ev.check_disambiguation(
            ev.load_pages(self.wiki_root), judge, focus_files={"papers/changed.md"},
        )
        self.assertTrue(r.passed)
        self.assertEqual(len(prompts), 1)
        self.assertIn("papers/changed.md", prompts[0])
        self.assertIn("papers/alpha-peer.md", prompts[0])
        self.assertNotIn("papers/old-one.md", prompts[0])


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

    def test_run_once_scopes_judge_metrics_but_keeps_structural_global(self):
        (self.wiki_root / "sources/a.md").write_text("Alpha evidence.\n", encoding="utf-8")
        (self.wiki_root / "sources/b.md").write_text("Beta evidence.\n", encoding="utf-8")
        write_md(
            self.wiki_root / "papers/changed.md",
            conformant_fm(title="Changed Alpha", type="paper", source="sources/a.md"),
            PRIMARY_BODY + "Alpha claim.\n",
        )
        write_md(
            self.wiki_root / "papers/unchanged.md",
            conformant_fm(title="Stable Beta", type="paper", source="sources/b.md"),
            PRIMARY_BODY + "Beta claim.\n",
        )
        prompts = []

        def judge(prompt):
            prompts.append(prompt)
            if "strict groundedness judge" in prompt:
                return '{"score":1.0,"unsupported":[]}'
            if "consistency auditor" in prompt:
                return '{"score":1.0,"conflicts":[]}'
            if "documentation quality auditor" in prompt:
                return '{"score":0.8}'
            self.fail(f"unexpected judge prompt: {prompt[:80]}")

        with patch.object(ev, "check_structural", wraps=ev.check_structural) as structural:
            results = ev.run_once(
                self.wiki_root, judge, focus_files={"papers/changed.md"},
            )

        self.assertTrue(all(result.passed for result in results))
        self.assertEqual(len(structural.call_args.args[0]), 2)
        grounding = next(prompt for prompt in prompts if "strict groundedness judge" in prompt)
        redundancy = next(prompt for prompt in prompts if "documentation quality auditor" in prompt)
        contradictions = next(prompt for prompt in prompts if "consistency auditor" in prompt)
        self.assertIn("Alpha claim", grounding)
        self.assertNotIn("Beta claim", grounding)
        self.assertIn("Alpha claim", redundancy)
        self.assertNotIn("Beta claim", redundancy)
        self.assertIn("Alpha claim", contradictions)
        self.assertIn("Beta claim", contradictions)
        self.assertLess(contradictions.index("Alpha claim"), contradictions.index("Beta claim"))
        self.assertIn("papers/changed.md", contradictions)

    def test_changed_page_files_includes_tracked_and_untracked_pages(self):
        subprocess.run(["git", "init", "-q"], cwd=self.wiki_root, check=True)
        write_md(self.wiki_root / "papers/tracked.md", conformant_fm(title="Tracked"), PRIMARY_BODY)
        (self.wiki_root / "index.md").write_text("# Index\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.wiki_root, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "base"],
            cwd=self.wiki_root,
            check=True,
        )
        with (self.wiki_root / "papers/tracked.md").open("a", encoding="utf-8") as handle:
            handle.write("Tracked change.\n")
        write_md(self.wiki_root / "papers/untracked.md", conformant_fm(title="Untracked"), PRIMARY_BODY)
        (self.wiki_root / "sources/ignored.md").write_text("evidence\n", encoding="utf-8")
        (self.wiki_root / "index.md").write_text("# Changed index\n", encoding="utf-8")

        pages = ev.load_pages(self.wiki_root)
        self.assertEqual(
            ev.changed_page_files(self.wiki_root, "HEAD", pages),
            {"papers/tracked.md", "papers/untracked.md"},
        )

    def test_changed_page_files_rejects_invalid_ref(self):
        subprocess.run(["git", "init", "-q"], cwd=self.wiki_root, check=True)
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="A"), PRIMARY_BODY)
        with self.assertRaisesRegex(ValueError, "cannot diff Git ref"):
            ev.changed_page_files(self.wiki_root, "not-a-ref", ev.load_pages(self.wiki_root))

    def test_changed_page_files_rejects_empty_changed_slice(self):
        subprocess.run(["git", "init", "-q"], cwd=self.wiki_root, check=True)
        write_md(self.wiki_root / "papers/a.md", conformant_fm(title="A"), PRIMARY_BODY)
        subprocess.run(["git", "add", "."], cwd=self.wiki_root, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "base"],
            cwd=self.wiki_root,
            check=True,
        )
        with self.assertRaisesRegex(ValueError, "no changed wiki pages"):
            ev.changed_page_files(self.wiki_root, "HEAD", ev.load_pages(self.wiki_root))

    def test_write_run_record_creates_eval_dir(self):
        self._wiki_with_derived()
        runs = [ev.run_once(self.wiki_root, stub('{"score":1.0}'))]
        agg = ev.aggregate(runs, ev.load_thresholds(self.wiki_root))
        path = ev.write_run_record(self.wiki_root, agg, "stub")
        self.assertTrue(path.exists())
        self.assertTrue((self.wiki_root / ".eval" / "history.json").exists())

    def test_write_run_record_preserves_changed_slice_scope(self):
        self._wiki_with_derived()
        agg = ev.aggregate(
            [ev.run_once(self.wiki_root, stub('{"score":1.0}'))],
            ev.load_thresholds(self.wiki_root),
        )
        scope = {"kind": "changed_since", "base": "HEAD", "pages": ["papers/a.md"]}
        path = ev.write_run_record(self.wiki_root, agg, "stub", scope=scope)
        record = __import__("json").loads(path.read_text(encoding="utf-8"))
        history = __import__("json").loads(
            (self.wiki_root / ".eval" / "history.json").read_text(encoding="utf-8")
        )
        self.assertEqual(record["scope"], scope)
        self.assertEqual(history[-1]["scope"], scope)

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

    def test_build_report_names_changed_slice(self):
        agg = [{"name": "grounding", "score": 1.0, "threshold": 0.95, "passed": True,
                "runs": 1, "runs_passed": 1, "rationale": "x", "insights": ""}]
        scope = {"kind": "changed_since", "base": "HEAD", "pages": ["papers/a.md"]}
        report = ev.build_report(agg, "codex", scope=scope)
        self.assertIn("Changed since: `HEAD`", report)
        self.assertIn("`papers/a.md`", report)

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
