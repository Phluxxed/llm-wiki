from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_mcp.registry import doctor, register_wiki
from tests.wiki_fixture import create_wiki_root


VALID_CONFIG = """\
schema_version = "1"
runtime_contract = "2"
profile = "default"
"""


class DoctorTest(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get("LLM_WIKI_HOME")
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        os.environ["LLM_WIKI_HOME"] = str(self.tmp / "home")
        self.wiki = create_wiki_root(self.tmp / "wiki")
        register_wiki("test", self.wiki)

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("LLM_WIKI_HOME", None)
        else:
            os.environ["LLM_WIKI_HOME"] = self._old_home
        self._tmp.cleanup()

    def test_unmodified_legacy_wiki_is_migration_available(self):
        result = doctor("test")

        self.assertEqual(result["compatibility"]["status"], "migration_available")
        self.assertEqual(result["config"]["status"], "legacy_missing")
        self.assertEqual(result["runtime"]["contract"], "2")
        self.assertEqual(result["scripts"]["scripts/query.py"]["status"], "canonical_legacy_copy")
        self.assertEqual(result["providers"]["loci"]["status"], "disabled")

    def test_unknown_local_script_modification_blocks_automatic_migration(self):
        (self.wiki / "scripts" / "query.py").write_text("# private behavior\n", encoding="utf-8")

        result = doctor("test")

        self.assertEqual(result["compatibility"]["status"], "blocked")
        self.assertEqual(result["scripts"]["scripts/query.py"]["status"], "modified_unknown")
        self.assertIn("scripts/query.py", result["compatibility"]["blockers"])

    def test_adapter_marker_cannot_disguise_modified_code(self):
        (self.wiki / "scripts" / "query.py").write_text(
            "# llm-wiki-adapter runtime_contract=2\nraise RuntimeError('poison')\n",
            encoding="utf-8",
        )

        result = doctor("test")

        self.assertEqual(result["scripts"]["scripts/query.py"]["status"], "modified_unknown")
        self.assertEqual(
            result["scripts"]["scripts/query.py"]["claimed_adapter_contract"],
            "2",
        )
        self.assertEqual(result["compatibility"]["status"], "blocked")

    def test_compatible_config_reports_runtime_versions(self):
        (self.wiki / ".llm-wiki.toml").write_text(VALID_CONFIG, encoding="utf-8")

        result = doctor("test")

        self.assertEqual(result["compatibility"]["status"], "compatible")
        self.assertEqual(result["config"]["schema_version"], "1")
        self.assertEqual(result["config"]["runtime_contract"], "2")

    def test_incompatible_config_fails_closed(self):
        (self.wiki / ".llm-wiki.toml").write_text(
            VALID_CONFIG.replace('schema_version = "1"', 'schema_version = "2"'),
            encoding="utf-8",
        )

        result = doctor("test")

        self.assertEqual(result["compatibility"]["status"], "incompatible")
        self.assertEqual(result["config"]["error"]["code"], "SCHEMA_VERSION_UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
