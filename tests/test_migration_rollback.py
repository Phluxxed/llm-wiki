from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.doctor import inspect_runtime
from llm_wiki_core.config import inspect_wiki_config
from llm_wiki_core.migration import (
    GRAPH_ADAPTER, QUERY_ADAPTER, MigrationError, apply_migration,
    dry_run_migration, inspect_migration, rollback_migration, verify_migration,
)
from tests.wiki_fixture import create_wiki_root


def digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


class MigrationRollbackTest(unittest.TestCase):
    def test_custom_runtime_two_configuration_round_trips_exact_bytes(self):
        for backend in ('loci', 'legacy'):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmpdir:
                root = create_wiki_root(Path(tmpdir) / "wiki")
                config = f'''# local settings
schema_version = '1'
runtime_contract = '2' # explicit old behavior
profile = 'personal'

[content]
exclude_directories = ['.git', '.private']
source_directory = 'materials'

[compiler] # custom provider subset
providers = [
  'seed', # exact pages
  'loci', # remove external retrieval
  'text',
  'graph', # direct links
]
graph_backend = '{backend}' # old backend explanation
target_bytes = 12345
max_bytes = 50000
target_items = 7
max_items = 20

[state]
field = 'evidence_state'
default = 'current'

[extension]
owner = 'local'
note = 'loci is merely a word here'
'''.replace('\n', '\r\n').encode()
                (root / '.llm-wiki.toml').write_bytes(config)
                for relative, adapter in (('scripts/query.py', QUERY_ADAPTER), ('scripts/wiki_graph.py', GRAPH_ADAPTER)):
                    (root / relative).write_text(adapter.replace('runtime_contract=3', 'runtime_contract=2'), encoding='utf-8')
                before = {relative: (root / relative).read_bytes() for relative in ('.llm-wiki.toml', 'scripts/query.py', 'scripts/wiki_graph.py')}

                inspected = inspect_migration(root)
                self.assertEqual(inspected.plan_hash, dry_run_migration(root).plan_hash)
                self.assertEqual(inspected.blockers, ())
                self.assertEqual({relative: (root / relative).read_bytes() for relative in before}, before)
                receipt = apply_migration(root, plan_hash=inspected.plan_hash)

                self.assertEqual(verify_migration(root)['status'], 'passed')
                current = inspect_wiki_config(root).config
                self.assertEqual(current.compiler.providers, ('seed', 'text', 'graph'))
                self.assertEqual(current.compiler.target_bytes, 12345)
                self.assertEqual(current.content.source_directory, 'materials')
                self.assertEqual(current.state.field, 'evidence_state')
                self.assertEqual(current.raw['extension']['owner'], 'local')
                upgraded = (root / '.llm-wiki.toml').read_bytes()
                for comment in (b'# exact pages', b'# remove external retrieval', b'# direct links', b'# old backend explanation'):
                    self.assertIn(comment, upgraded)
                self.assertIn(b'\r\n', upgraded)

                rollback_migration(root, receipt_id=receipt['receipt_id'])
                self.assertEqual({relative: (root / relative).read_bytes() for relative in before}, before)
                self.assertEqual(inspect_wiki_config(root).status, 'migration_required')

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
