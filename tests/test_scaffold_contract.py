from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.compiler import compile_context
from llm_wiki_core.config import inspect_wiki_config
from llm_wiki_core.contracts import CompileRequest
from llm_wiki_core.doctor import inspect_runtime
from llm_wiki_core.legacy import LegacyRuntime
from llm_wiki_core.migration import GRAPH_ADAPTER, QUERY_ADAPTER
from tests.wiki_fixture import base_fm, create_wiki_root, write_md


class ScaffoldContractTest(unittest.TestCase):
    def test_scaffold_and_migration_install_the_same_adapters(self):
        self.assertEqual(
            (REPO_ROOT / "_templates" / "adapters" / "query.py").read_text(encoding="utf-8"),
            QUERY_ADAPTER,
        )
        self.assertEqual(
            (REPO_ROOT / "_templates" / "adapters" / "wiki_graph.py").read_text(encoding="utf-8"),
            GRAPH_ADAPTER,
        )

    def test_fresh_scaffold_uses_compatible_config_and_thin_adapters(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = create_wiki_root(Path(tmpdir) / "wiki", with_scripts=False)
            shutil.copyfile(REPO_ROOT / "_templates" / "llm-wiki.toml", root / ".llm-wiki.toml")
            scripts = root / "scripts"
            scripts.mkdir()
            for name in ("query.py", "wiki_graph.py"):
                source = REPO_ROOT / "_templates" / "adapters" / name
                shutil.copyfile(source, scripts / name)
                self.assertLess(len(source.read_text(encoding="utf-8").splitlines()), 30)
            for name in ("lint.py", "render.py"):
                shutil.copyfile(REPO_ROOT / "scripts" / name, scripts / name)
            write_md(root / "notes" / "a.md", base_fm(title="A"), "A body.")

            config = inspect_wiki_config(root)
            doctor = inspect_runtime(root)
            compiled = compile_context(
                root,
                CompileRequest.from_mapping(
                    {"alias": "fixture", "question": "What does A say?", "seeds": ["notes/a.md"]}
                ),
            )
            legacy = LegacyRuntime(root).context_pack("notes/a.md", tokens=1200)
            lint = subprocess.run(
                [sys.executable, str(scripts / "lint.py"), "--json"],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            render = subprocess.run(
                [sys.executable, str(scripts / "render.py"), "--output", str(root / "wiki.html")],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            legacy_cli = subprocess.run(
                [
                    sys.executable,
                    str(scripts / "query.py"),
                    "--context-pack",
                    "notes/a.md",
                    "--json",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(config.status, "compatible")
            self.assertEqual(doctor["compatibility"]["status"], "compatible")
            self.assertEqual(compiled.runtime_contract, "2")
            self.assertEqual(legacy["kind"], "context_pack")
            self.assertEqual(lint.returncode, 0, lint.stderr)
            self.assertEqual(render.returncode, 0, render.stderr)
            self.assertTrue((root / "wiki.html").is_file())
            self.assertEqual(legacy_cli.returncode, 0, legacy_cli.stderr)
            self.assertEqual(json.loads(legacy_cli.stdout)["kind"], "context_pack")

    def test_query_adapter_fails_loudly_when_runtime_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            scripts = root / "scripts"
            scripts.mkdir()
            shutil.copyfile(REPO_ROOT / "_templates" / "adapters" / "query.py", scripts / "query.py")
            env = {**os.environ, "PYTHONPATH": ""}

            result = subprocess.run(
                [sys.executable, "-S", str(scripts / "query.py"), "--help"],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("install or upgrade llm-wiki", result.stderr + result.stdout)

    def test_query_adapter_rejects_incompatible_runtime_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = create_wiki_root(Path(tmpdir) / "wiki", with_scripts=False)
            (root / ".llm-wiki.toml").write_text(
                'schema_version = "1"\nruntime_contract = "999"\n',
                encoding="utf-8",
            )
            scripts = root / "scripts"
            scripts.mkdir()
            shutil.copyfile(REPO_ROOT / "_templates" / "adapters" / "query.py", scripts / "query.py")

            result = subprocess.run(
                [sys.executable, str(scripts / "query.py"), "--json"],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("RUNTIME_CONTRACT_INCOMPATIBLE", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
