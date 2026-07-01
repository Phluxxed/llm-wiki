import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


def load_eval_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "eval.py"
    spec = importlib.util.spec_from_file_location("llm_wiki_eval_judge_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class JudgeDetectionTests(unittest.TestCase):
    def setUp(self):
        self.eval = load_eval_module()

    def test_default_auto_detect_still_prefers_claude_then_codex(self):
        with patch.object(
            self.eval.shutil,
            "which",
            side_effect=lambda name: f"/bin/{name}" if name in {"claude", "codex"} else None,
        ):
            _judge, label = self.eval.detect_judge(None)

        self.assertEqual(label, "claude")

    def test_default_auto_detect_falls_back_to_codex(self):
        with patch.object(
            self.eval.shutil,
            "which",
            side_effect=lambda name: f"/bin/{name}" if name == "codex" else None,
        ):
            _judge, label = self.eval.detect_judge(None)

        self.assertEqual(label, "codex")

    def test_owner_lock_auto_detects_only_owner(self):
        self.eval.OWNER_JUDGE = "codex"
        self.eval.OWNER_LABEL = "Codex Brain"

        with patch.object(
            self.eval.shutil,
            "which",
            side_effect=lambda name: f"/bin/{name}" if name in {"claude", "codex"} else None,
        ):
            _judge, label = self.eval.detect_judge(None)

        self.assertEqual(label, "codex")

    def test_owner_lock_falls_back_to_none_when_owner_missing(self):
        self.eval.OWNER_JUDGE = "codex"

        with patch.object(
            self.eval.shutil,
            "which",
            side_effect=lambda name: f"/bin/{name}" if name == "claude" else None,
        ):
            judge, label = self.eval.detect_judge(None)

        self.assertIsNone(judge)
        self.assertEqual(label, "none")

    def test_owner_lock_allows_judge_none(self):
        self.eval.OWNER_JUDGE = "codex"

        judge, label = self.eval.detect_judge("none")

        self.assertIsNone(judge)
        self.assertEqual(label, "none")

    def test_owner_lock_rejects_non_owner_known_judge(self):
        self.eval.OWNER_JUDGE = "codex"
        self.eval.OWNER_LABEL = "Codex Brain"

        with self.assertRaisesRegex(ValueError, "Codex Brain evals must use codex"):
            self.eval.detect_judge("claude")

    def test_owner_lock_supports_claude_owner(self):
        self.eval.OWNER_JUDGE = "claude"
        self.eval.OWNER_LABEL = "Claude Brain"

        with patch.object(
            self.eval.shutil,
            "which",
            side_effect=lambda name: f"/bin/{name}" if name in {"claude", "codex"} else None,
        ):
            _judge, label = self.eval.detect_judge(None)

        self.assertEqual(label, "claude")
        with self.assertRaisesRegex(ValueError, "Claude Brain evals must use claude"):
            self.eval.detect_judge("codex")

    def test_owner_lock_rejects_custom_judge_commands(self):
        self.eval.OWNER_JUDGE = "codex"

        with self.assertRaisesRegex(ValueError, "Custom judge commands are disabled"):
            self.eval.detect_judge("some-judge --json")

    def test_codex_judge_does_not_force_low_reasoning_by_default(self):
        calls = []

        def fake_run(cmd):
            calls.append(cmd)
            out_path = Path(cmd[cmd.index("--output-last-message") + 1])
            out_path.write_text('{"score":1,"rationale":"ok"}', encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch.object(self.eval, "_run", side_effect=fake_run):
            result = self.eval.codex_judge("judge prompt")

        self.assertEqual(result, '{"score":1,"rationale":"ok"}')
        self.assertEqual(len(calls), 1)
        cmd = calls[0]
        self.assertIn("--ephemeral", cmd)
        self.assertNotIn('model_reasoning_effort="low"', cmd)

    def test_codex_judge_accepts_explicit_reasoning_override(self):
        calls = []

        def fake_run(cmd):
            calls.append(cmd)
            out_path = Path(cmd[cmd.index("--output-last-message") + 1])
            out_path.write_text('{"score":1,"rationale":"ok"}', encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with patch.dict(os.environ, {"CODEX_JUDGE_REASONING_EFFORT": "low"}):
            with patch.object(self.eval, "_run", side_effect=fake_run):
                self.eval.codex_judge("judge prompt")

        self.assertIn("-c", calls[0])
        self.assertIn('model_reasoning_effort="low"', calls[0])

    def test_run_raises_with_stderr_on_nonzero_judge_command(self):
        proc = subprocess.CompletedProcess(["codex"], 1, "stdout detail", "stderr detail")

        with patch.object(self.eval.subprocess, "run", return_value=proc):
            with self.assertRaisesRegex(RuntimeError, "judge command failed.*stderr detail"):
                self.eval._run(["codex"])


class ContradictionScoringTests(unittest.TestCase):
    def setUp(self):
        self.eval = load_eval_module()
        self.pages = [{"file": "a.md", "text": "Body A."},
                      {"file": "b.md", "text": "Body B."}]

    def test_empty_conflicts_scores_perfect_even_if_judge_underscores(self):
        judge = lambda _prompt: '{"score":0.92,"conflicts":[],"rationale":"none"}'
        result = self.eval.check_contradictions(self.pages, judge)
        self.assertEqual(result.score, 1.0)
        self.assertTrue(result.passed)

    def test_listed_conflict_cannot_reach_a_perfect_score(self):
        judge = lambda _prompt: '{"score":1.0,"conflicts":["X in a.md vs Y in b.md"],"rationale":"one"}'
        result = self.eval.check_contradictions(self.pages, judge)
        self.assertLess(result.score, 1.0)
        self.assertFalse(result.passed)


if __name__ == "__main__":
    unittest.main()
