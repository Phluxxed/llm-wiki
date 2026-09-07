"""Regression coverage for lint-time temporal history validation."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from llm_wiki_core.temporal_persistence import build_temporal_claim_revision  # noqa: E402


OBSERVATION_A = "temporal-observation:sha256:" + "a" * 64
OBSERVATION_B = "temporal-observation:sha256:" + "b" * 64
CANDIDATE_A = "temporal-candidate:sha256:" + "a" * 64
CANDIDATE_B = "temporal-candidate:sha256:" + "b" * 64


def write_page(path: Path, frontmatter: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    path.write_text(f"---\n{payload}\n---\n\n# Page\n", encoding="utf-8")


def load_lint(wiki_root: Path):
    sys.modules.pop("lint", None)
    import lint  # noqa: E402

    lint.WIKI_ROOT = wiki_root
    return lint


def base_frontmatter(**overrides) -> dict:
    value = {
        "title": "Temporal test page",
        "category": "Test",
        "status": "Live",
        "owner": "tests",
        "tags": [],
        "created": "2026-08-10",
        "last_reviewed": "2026-08-10",
        "type": "meta",
        "description": "Lint temporal history fixture.",
        "timestamp": "2026-08-10T00:00:00Z",
    }
    value.update(overrides)
    return value


def revision(**changes):
    values = {
        "subject": {"kind": "resolved_page", "page": "entities/example.md"},
        "predicate": "status:has_state",
        "object_ref": {"kind": "literal", "datatype": "type:text", "value": "ready"},
        "world_validity": {
            "from": {"kind": "known", "value": "2026-01-01"},
            "to": {"kind": "open"},
        },
        "recorded_at": "2026-08-10T00:00:00Z",
        "candidate_ids": [CANDIDATE_A, CANDIDATE_B],
        "observation_ids": [OBSERVATION_A, OBSERVATION_B],
        "steward_evidence_refs": ["sources/example.md"],
        "decision": "accept",
    }
    values.update(changes)
    return build_temporal_claim_revision(**values).to_dict()


class TemporalHistoryLintTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wiki_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_temporal_lint(self, histories):
        lint = load_lint(self.wiki_root)
        write_page(
            self.wiki_root / "entities/example.md",
            base_frontmatter(temporal_claim_revisions=histories),
        )
        pages = lint.collect_pages()
        issues = lint.run_checks(pages, set(), set(), lint.collect_all_md_paths())
        return [issue for issue in issues if issue["check"] == "temporal_history"]

    def assert_temporal_error(self, histories, code, field=None):
        issues = self.run_temporal_lint(histories)
        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue["code"], code)
        self.assertIn(code, issue["detail"])
        if field is not None:
            self.assertEqual(issue["field"], field)

    def run_lint_cli(self, histories, *, json_output):
        lint = load_lint(self.wiki_root)
        write_page(
            self.wiki_root / "entities/example.md",
            base_frontmatter(temporal_claim_revisions=histories),
        )
        previous_argv = sys.argv
        sys.argv = ["lint.py"] + (["--json"] if json_output else [])
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                status = lint.main()
        finally:
            sys.argv = previous_argv
        return status, stdout.getvalue()

    def test_valid_history_cli_text_and_json_exit_zero(self):
        accepted = revision()
        superseding = revision(
            decision="supersede",
            recorded_at="2026-08-10T00:00:01Z",
            object_ref={"kind": "literal", "datatype": "type:text", "value": "running"},
            supersedes_revision_ids=[accepted["revision_id"]],
        )
        for json_output in (False, True):
            with self.subTest(json_output=json_output):
                status, output = self.run_lint_cli(
                    [accepted, superseding], json_output=json_output
                )
                self.assertEqual(status, 0)
                if json_output:
                    self.assertFalse(any(item["check"] == "temporal_history" for item in json.loads(output)))
                else:
                    self.assertNotIn("Temporal history", output)

    def test_invalid_history_cli_text_and_json_exit_nonzero(self):
        corrupted = revision()
        corrupted["revision_id"] = "temporal-revision:sha256:" + "0" * 64
        for json_output in (False, True):
            with self.subTest(json_output=json_output):
                status, output = self.run_lint_cli([corrupted], json_output=json_output)
                self.assertEqual(status, 1)
                if json_output:
                    issues = [item for item in json.loads(output) if item["check"] == "temporal_history"]
                    self.assertEqual(len(issues), 1)
                    self.assertEqual(issues[0]["code"], "TEMPORAL_ID_MISMATCH")
                else:
                    self.assertIn("Temporal history (1)", output)
                    self.assertIn("TEMPORAL_ID_MISMATCH", output)

    def test_valid_accept_then_supersede_history_passes(self):
        accepted = revision()
        superseding = revision(
            decision="supersede",
            recorded_at="2026-08-10T00:00:01Z",
            object_ref={"kind": "literal", "datatype": "type:text", "value": "running"},
            supersedes_revision_ids=[accepted["revision_id"]],
        )
        self.assertEqual(self.run_temporal_lint([accepted, superseding]), [])

    def test_accept_revision_cannot_have_relation_targets(self):
        accepted = revision()
        accepted["retires_revision_ids"] = ["temporal-revision:sha256:" + "f" * 64]
        self.assert_temporal_error(
            [accepted], "TEMPORAL_INVALID_FIELD", "decision"
        )

    def test_revision_id_and_claim_key_corruption_are_named(self):
        for field in ("revision_id", "claim_key"):
            with self.subTest(field=field):
                corrupted = revision()
                prefix = "temporal-revision:sha256:" if field == "revision_id" else "temporal-claim:sha256:"
                corrupted[field] = prefix + "0" * 64
                self.assert_temporal_error(
                    [corrupted], "TEMPORAL_ID_MISMATCH", field
                )

    def test_wrong_typed_evidence_is_named(self):
        corrupted = revision()
        corrupted["steward_evidence_refs"] = [{"path": "sources/example.md"}]
        self.assert_temporal_error(
            [corrupted], "TEMPORAL_INVALID_FIELD", "steward_evidence_refs[0]"
        )

    def test_history_ordering_and_dangling_targets_are_checked(self):
        accepted = revision()
        superseding = revision(
            decision="supersede",
            recorded_at="2026-08-10T00:00:01Z",
            supersedes_revision_ids=[accepted["revision_id"]],
        )
        self.assert_temporal_error(
            [superseding, accepted], "TEMPORAL_INVALID_FIELD", "temporal_claim_revisions"
        )

        dangling = revision(
            decision="supersede",
            recorded_at="2026-08-10T00:00:01Z",
            supersedes_revision_ids=["temporal-revision:sha256:" + "f" * 64],
        )
        self.assert_temporal_error(
            [accepted, dangling], "TEMPORAL_INVALID_FIELD", "temporal_claim_revisions"
        )


if __name__ == "__main__":
    unittest.main()
