from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.migration import (
    MigrationError,
    apply_migration,
    inspect_migration,
    verify_migration,
)
from tests.wiki_fixture import base_fm, create_wiki_root, write_md


def digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


class MigrationApplyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = create_wiki_root(Path(self._tmp.name) / "wiki")
        write_md(self.root / "notes" / "a.md", base_fm(title="A"), "A body.")

    def tearDown(self):
        self._tmp.cleanup()

    def test_apply_writes_receipt_and_verifies_compatible_runtime(self):
        plan = inspect_migration(self.root)

        receipt = apply_migration(self.root, plan_hash=plan.plan_hash)
        verification = verify_migration(self.root)

        self.assertEqual(receipt["status"], "applied")
        self.assertTrue((self.root / ".llm-wiki.toml").is_file())
        self.assertTrue((self.root / ".llm-wiki" / "migrations" / "latest.json").is_file())
        self.assertEqual(verification["status"], "passed")
        self.assertEqual(verification["compatibility"], "compatible")
        self.assertEqual(verification["compiler_smoke"], "passed")

    def test_apply_is_idempotent_after_success(self):
        plan = inspect_migration(self.root)
        first = apply_migration(self.root, plan_hash=plan.plan_hash)

        second_plan = inspect_migration(self.root)
        second = apply_migration(self.root, plan_hash=second_plan.plan_hash)

        self.assertEqual(first["status"], "applied")
        self.assertEqual(second["status"], "no_op")
        self.assertEqual(second["operations"], [])

    def test_stale_plan_hash_fails_before_writing(self):
        before = digest(self.root / "scripts" / "query.py")

        with self.assertRaises(MigrationError) as raised:
            apply_migration(self.root, plan_hash="stale-plan")

        self.assertEqual(raised.exception.code, "MIGRATION_PLAN_STALE")
        self.assertEqual(digest(self.root / "scripts" / "query.py"), before)
        self.assertFalse((self.root / ".llm-wiki").exists())

    def test_injected_partial_failure_restores_target_files(self):
        plan = inspect_migration(self.root)
        before = {
            operation.path: digest(self.root / operation.path)
            for operation in plan.operations
        }

        with self.assertRaises(MigrationError) as raised:
            apply_migration(self.root, plan_hash=plan.plan_hash, _fail_after=1)

        self.assertEqual(raised.exception.code, "MIGRATION_APPLY_FAILED")
        self.assertEqual(
            {path: digest(self.root / path) for path in before},
            before,
        )
        self.assertEqual(raised.exception.details["recovery"], "automatic_rollback_complete")

    def test_apply_rolls_back_when_target_venv_cannot_import_runtime(self):
        python = self.root / ".venv" / "bin" / "python3"
        python.parent.mkdir(parents=True)
        python.write_text(
            f"#!/bin/sh\nexec {sys.executable} -S \"$@\"\n",
            encoding="utf-8",
        )
        os.chmod(python, 0o755)
        plan = inspect_migration(self.root)

        with self.assertRaises(MigrationError) as raised:
            apply_migration(self.root, plan_hash=plan.plan_hash)

        self.assertEqual(raised.exception.code, "MIGRATION_APPLY_FAILED")
        self.assertEqual(raised.exception.details["cause"], "MIGRATION_VERIFICATION_FAILED")
        self.assertFalse((self.root / ".llm-wiki.toml").exists())

    def test_apply_rejects_receipt_directory_symlink_outside_wiki(self):
        plan = inspect_migration(self.root)
        outside = Path(self._tmp.name) / "outside-receipts"
        outside.mkdir()
        (self.root / ".llm-wiki").symlink_to(outside, target_is_directory=True)

        with self.assertRaises(MigrationError) as raised:
            apply_migration(self.root, plan_hash=plan.plan_hash)

        self.assertEqual(raised.exception.code, "MIGRATION_TARGET_INVALID")
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
