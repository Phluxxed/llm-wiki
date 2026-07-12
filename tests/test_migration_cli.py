from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.cli import main
from tests.wiki_fixture import create_wiki_root


class MigrationCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = create_wiki_root(Path(self._tmp.name) / "wiki")

    def tearDown(self):
        self._tmp.cleanup()

    def call(self, args: list[str]) -> tuple[int, dict]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(args)
        return code, json.loads(stdout.getvalue())

    def test_doctor_and_migration_workflow_are_explicit_cli_commands(self):
        doctor_code, doctor = self.call(["doctor", "--wiki", str(self.root)])
        inspect_code, plan = self.call(["migrate", "inspect", "--wiki", str(self.root)])
        apply_code, receipt = self.call(
            [
                "migrate",
                "apply",
                "--wiki",
                str(self.root),
                "--plan-hash",
                plan["plan_hash"],
            ]
        )
        verify_code, verification = self.call(["migrate", "verify", "--wiki", str(self.root)])

        self.assertEqual(doctor_code, 0)
        self.assertEqual(doctor["compatibility"]["status"], "migration_available")
        self.assertEqual(inspect_code, 0)
        self.assertEqual(apply_code, 0)
        self.assertEqual(receipt["status"], "applied")
        self.assertEqual(verify_code, 0)
        self.assertEqual(verification["status"], "passed")

    def test_dry_run_alias_never_writes(self):
        code, plan = self.call(["migrate", "dry-run", "--wiki", str(self.root)])

        self.assertEqual(code, 0)
        self.assertEqual(plan["kind"], "wiki_migration_plan")
        self.assertFalse((self.root / ".llm-wiki.toml").exists())


if __name__ == "__main__":
    unittest.main()
