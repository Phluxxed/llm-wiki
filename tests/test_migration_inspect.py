from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.migration import MigrationError, dry_run_migration, inspect_migration
from tests.wiki_fixture import create_wiki_root


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


class MigrationInspectTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = create_wiki_root(Path(self._tmp.name) / "wiki")

    def tearDown(self):
        self._tmp.cleanup()

    def test_inspect_and_dry_run_make_no_writes(self):
        before = tree_hash(self.root)

        inspected = inspect_migration(self.root).to_dict()
        dry_run = dry_run_migration(self.root).to_dict()

        self.assertEqual(tree_hash(self.root), before)
        self.assertEqual(inspected["plan_hash"], dry_run["plan_hash"])
        self.assertEqual(inspected["blockers"], [])
        self.assertEqual(
            [operation["path"] for operation in inspected["operations"]],
            [".llm-wiki.toml", "scripts/query.py", "scripts/wiki_graph.py"],
        )

    def test_known_agents_exclusion_translates_to_config(self):
        query = self.root / "scripts" / "query.py"
        query.write_text(
            query.read_text(encoding="utf-8").replace(
                'EXCLUDE_DIRS = {"sources", "_templates", "scripts", ".git",',
                'EXCLUDE_DIRS = {"sources", "_templates", "scripts", ".agents", ".git",',
            ),
            encoding="utf-8",
        )
        graph = self.root / "scripts" / "wiki_graph.py"
        graph.write_text(
            graph.read_text(encoding="utf-8").replace(
                'DEFAULT_EXCLUDE_DIRS = {"sources", "_templates", "scripts", ".git",',
                'DEFAULT_EXCLUDE_DIRS = {"sources", "_templates", "scripts", ".agents", ".git",',
            ),
            encoding="utf-8",
        )

        plan = inspect_migration(self.root).to_dict()

        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["translated_customizations"], ["exclude_directory:.agents"])
        config = next(item for item in plan["operations"] if item["path"] == ".llm-wiki.toml")
        self.assertIn('".agents"', config["content"])

    def test_unknown_script_modification_blocks_replacement(self):
        (self.root / "scripts" / "query.py").write_text("# private behavior\n", encoding="utf-8")

        plan = inspect_migration(self.root).to_dict()

        self.assertIn("scripts/query.py:modified_unknown", plan["blockers"])
        self.assertNotIn(
            "scripts/query.py",
            [operation["path"] for operation in plan["operations"]],
        )

    def test_current_config_and_adapters_need_no_operations(self):
        initial = inspect_migration(self.root)
        for operation in initial.operations:
            path = self.root / operation.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(operation.content, encoding="utf-8")

        plan = inspect_migration(self.root).to_dict()

        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["operations"], [])

    def test_inspect_rejects_symlinked_tooling_outside_wiki(self):
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        (outside / "query.py").write_text("# outside\n", encoding="utf-8")
        (outside / "wiki_graph.py").write_text("# outside\n", encoding="utf-8")
        shutil.rmtree(self.root / "scripts")
        (self.root / "scripts").symlink_to(outside, target_is_directory=True)

        with self.assertRaises(MigrationError) as raised:
            inspect_migration(self.root)

        self.assertEqual(raised.exception.code, "MIGRATION_TARGET_INVALID")
        self.assertEqual((outside / "query.py").read_text(encoding="utf-8"), "# outside\n")


if __name__ == "__main__":
    unittest.main()
