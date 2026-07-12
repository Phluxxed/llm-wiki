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
from llm_wiki_core.compiler import compile_context
from llm_wiki_core.contracts import CompileRequest
from tests.wiki_fixture import base_fm, write_md


class CompilerCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write_md(
            self.root / "runtime.md",
            base_fm(title="Runtime", knowledge_state="current"),
            "Runtime owns traversal.",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_cli_and_core_return_the_same_contract(self):
        args = [
            "compile-context",
            "--wiki",
            str(self.root),
            "--alias",
            "test",
            "--question",
            "What owns traversal?",
            "--seed",
            "runtime.md",
        ]
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            exit_code = main(args)

        expected = compile_context(
            self.root,
            CompileRequest.from_mapping(
                {
                    "alias": "test",
                    "question": "What owns traversal?",
                    "seeds": ["runtime.md"],
                }
            ),
        ).to_dict()
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), expected)

    def test_cli_errors_are_structured_and_nonzero(self):
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            exit_code = main(
                [
                    "compile-context",
                    "--wiki",
                    str(self.root),
                    "--alias",
                    "test",
                    "--question",
                    " ",
                ]
            )

        payload = json.loads(stderr.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["error"]["code"], "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main()
