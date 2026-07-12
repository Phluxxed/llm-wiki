from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.doctor import inspect_runtime
from llm_wiki_core.migration import MigrationError, apply_migration, inspect_migration, rollback_migration
from tests.wiki_fixture import create_wiki_root


def digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


class MigrationRollbackTest(unittest.TestCase):
    def test_rollback_restores_pre_migration_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = create_wiki_root(Path(tmpdir) / "wiki")
            plan = inspect_migration(root)
            before = {
                operation.path: digest(root / operation.path)
                for operation in plan.operations
            }
            receipt = apply_migration(root, plan_hash=plan.plan_hash)

            result = rollback_migration(root, receipt_id=receipt["receipt_id"])
            restored = inspect_runtime(root)

            self.assertEqual(result["status"], "rolled_back")
            self.assertEqual(
                {path: digest(root / path) for path in before},
                before,
            )
            self.assertFalse((root / ".llm-wiki.toml").exists())
            self.assertEqual(restored["compatibility"]["status"], "migration_available")

    def test_rollback_validates_every_backup_before_changing_targets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = create_wiki_root(Path(tmpdir) / "wiki")
            plan = inspect_migration(root)
            receipt = apply_migration(root, plan_hash=plan.plan_hash)
            after = {
                operation["path"]: digest(root / operation["path"])
                for operation in receipt["operations"]
            }
            backed_up = next(item for item in receipt["operations"] if item["backup"])
            backup = (
                root
                / ".llm-wiki"
                / "migrations"
                / receipt["receipt_id"]
                / backed_up["backup"]
            )
            backup.write_text("tampered backup\n", encoding="utf-8")

            with self.assertRaises(MigrationError) as raised:
                rollback_migration(root, receipt_id=receipt["receipt_id"])

            self.assertEqual(raised.exception.code, "MIGRATION_RECEIPT_INVALID")
            self.assertEqual(
                {path: digest(root / path) for path in after},
                after,
            )


if __name__ == "__main__":
    unittest.main()
