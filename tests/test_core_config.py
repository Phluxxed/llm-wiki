from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.config import inspect_wiki_config


VALID_CONFIG = """\
schema_version = "1"
runtime_contract = "2"
profile = "default"

[content]
exclude_directories = [".git", ".venv", "node_modules"]
source_directory = "sources"

[compiler]
providers = ["seed", "frontmatter", "text", "graph", "source"]
target_bytes = 48000
max_bytes = 192000
target_items = 24
max_items = 96

[state]
field = "knowledge_state"
default = "unspecified"

[stewardship]
mode = "manual"
"""


class WikiConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_config(self, text: str) -> None:
        (self.root / ".llm-wiki.toml").write_text(text, encoding="utf-8")

    def test_missing_config_is_legacy_supported(self):
        result = inspect_wiki_config(self.root)

        self.assertEqual(result.status, "legacy_missing")
        self.assertIsNone(result.config)
        self.assertIsNone(result.error)

    def test_valid_config_is_typed_and_preserves_unknown_keys(self):
        self.write_config(VALID_CONFIG + "\n[extension]\nowner = \"local\"\n")

        result = inspect_wiki_config(self.root)

        self.assertEqual(result.status, "compatible")
        self.assertEqual(result.config.schema_version, "1")
        self.assertEqual(result.config.runtime_contract, "2")
        self.assertEqual(result.config.content.source_directory, "sources")
        self.assertEqual(result.config.compiler.target_bytes, 48_000)
        self.assertEqual(result.config.compiler.max_items, 96)
        self.assertEqual(result.config.raw["extension"], {"owner": "local"})

    def test_malformed_toml_is_invalid_with_structured_error(self):
        self.write_config('schema_version = "1"\nbroken = [\n')

        result = inspect_wiki_config(self.root)

        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.error.code, "WIKI_CONFIG_INVALID")
        self.assertIn(".llm-wiki.toml", result.error.message)

    def test_unsupported_schema_major_is_distinct(self):
        self.write_config(VALID_CONFIG.replace('schema_version = "1"', 'schema_version = "2"'))

        result = inspect_wiki_config(self.root)

        self.assertEqual(result.status, "unsupported_schema")
        self.assertEqual(result.error.code, "SCHEMA_VERSION_UNSUPPORTED")
        self.assertEqual(result.error.details["found"], "2")

    def test_newer_runtime_contract_is_incompatible(self):
        self.write_config(VALID_CONFIG.replace('runtime_contract = "2"', 'runtime_contract = "3"'))

        result = inspect_wiki_config(self.root)

        self.assertEqual(result.status, "runtime_incompatible")
        self.assertEqual(result.error.code, "RUNTIME_CONTRACT_INCOMPATIBLE")

    def test_secret_bearing_keys_are_rejected(self):
        self.write_config(VALID_CONFIG + '\n[provider]\napi_token = "do-not-store-this"\n')

        result = inspect_wiki_config(self.root)

        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.error.code, "WIKI_CONFIG_INVALID")
        self.assertEqual(result.error.details["key"], "provider.api_token")

    def test_content_directories_must_be_relative(self):
        self.write_config(VALID_CONFIG.replace('source_directory = "sources"', 'source_directory = "/tmp/sources"'))

        result = inspect_wiki_config(self.root)

        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.error.details["key"], "content.source_directory")

    def test_target_limits_cannot_exceed_maximums(self):
        self.write_config(VALID_CONFIG.replace("target_bytes = 48000", "target_bytes = 300000"))

        result = inspect_wiki_config(self.root)

        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.error.details["key"], "compiler.target_bytes")

    def test_unknown_provider_is_rejected_instead_of_silently_ignored(self):
        self.write_config(
            VALID_CONFIG.replace(
                'providers = ["seed", "frontmatter", "text", "graph", "source"]',
                'providers = ["seed", "mystery-provider"]',
            )
        )

        result = inspect_wiki_config(self.root)

        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.error.details["key"], "compiler.providers")
        self.assertEqual(result.error.details["value"], ["mystery-provider"])

    def test_python_310_toml_fallback_is_declared(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn('tomli>=2.0; python_version < "3.11"', pyproject)


if __name__ == "__main__":
    unittest.main()
