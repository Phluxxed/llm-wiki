from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_mcp.registry import doctor, register_wiki
from tests.wiki_fixture import create_wiki_root


VALID_CONFIG = """\
schema_version = "1"
runtime_contract = "3"
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
        self.assertEqual(result["runtime"]["contract"], "3")
        self.assertEqual(result["scripts"]["scripts/query.py"]["status"], "canonical_legacy_copy")
        self.assertEqual(result["providers"]["graph"], {"status": "unavailable"})

    def test_native_doctor_does_not_probe_loci(self):
        (self.wiki / ".llm-wiki.toml").write_text(VALID_CONFIG, encoding="utf-8")
        with (
            patch("shutil.which", side_effect=AssertionError("unexpected executable probe")),
            patch("llm_wiki_core.doctor.subprocess.run", side_effect=AssertionError("unexpected process")),
            patch.dict(os.environ, {"LLM_WIKI_LOCI_MCP_COMMAND": "/poison/loci"}),
        ):
            result = doctor("test")
        self.assertEqual(set(result["providers"]), {"seed", "frontmatter", "text", "graph", "source"})
        self.assertTrue(all(value == {"status": "ready"} for value in result["providers"].values()))
        self.assertNotIn("graph_backend", result["config"])

    def test_graph_readiness_respects_config(self):
        (self.wiki / ".llm-wiki.toml").write_text(
            VALID_CONFIG + '[compiler]\nproviders = ["text"]\n', encoding="utf-8",
        )
        result = doctor("test")
        self.assertEqual(result["providers"]["graph"], {"status": "disabled"})
        self.assertEqual(result["providers"]["text"], {"status": "ready"})

    def test_runtime_two_reports_actionable_migration(self):
        (self.wiki / ".llm-wiki.toml").write_text(
            VALID_CONFIG.replace('runtime_contract = "3"', 'runtime_contract = "2"'), encoding="utf-8",
        )
        result = doctor("test")
        self.assertEqual(result["compatibility"]["status"], "migration_available")
        self.assertEqual(result["config"]["status"], "migration_required")
        self.assertEqual(result["providers"]["graph"], {"status": "unavailable"})

    def test_unknown_local_script_modification_blocks_automatic_migration(self):
        (self.wiki / "scripts" / "query.py").write_text("# private behavior\n", encoding="utf-8")

        result = doctor("test")

        self.assertEqual(result["compatibility"]["status"], "blocked")
        self.assertEqual(result["scripts"]["scripts/query.py"]["status"], "modified_unknown")
        self.assertIn("scripts/query.py", result["compatibility"]["blockers"])

    def test_adapter_marker_cannot_disguise_modified_code(self):
        for contract in ("2", "3"):
            with self.subTest(contract=contract):
                (self.wiki / "scripts" / "query.py").write_text(
                    f"# llm-wiki-adapter runtime_contract={contract}\nraise RuntimeError('poison')\n",
                    encoding="utf-8",
                )
                result = doctor("test")
                self.assertEqual(result["scripts"]["scripts/query.py"]["status"], "modified_unknown")
                self.assertEqual(result["scripts"]["scripts/query.py"]["claimed_adapter_contract"], contract)
                self.assertEqual(result["compatibility"]["status"], "blocked")

    def test_compatible_config_reports_runtime_versions(self):
        (self.wiki / ".llm-wiki.toml").write_text(VALID_CONFIG, encoding="utf-8")

        result = doctor("test")

        self.assertEqual(result["compatibility"]["status"], "compatible")
        self.assertEqual(result["config"]["schema_version"], "1")
        self.assertEqual(result["config"]["runtime_contract"], "3")

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
