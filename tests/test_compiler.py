from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.compiler import compile_context
from llm_wiki_core.contracts import CompileRequest, ContractError
from tests.wiki_fixture import base_fm, write_md


class FailingProvider:
    name = "broken"

    def collect(self, context):
        raise RuntimeError("provider exploded")


class CompilerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "sources").mkdir()
        (self.root / "sources" / "runtime.md").write_text(
            "Canonical llm-wiki owns shared traversal behavior.",
            encoding="utf-8",
        )
        write_md(
            self.root / "systems" / "llm-wiki.md",
            base_fm(
                title="llm-wiki",
                type="system",
                source="sources/runtime.md",
                knowledge_state="current",
                tags=["traversal", "runtime"],
            ),
            "## Ownership\n\nllm-wiki owns the shared traversal runtime.\n\n## Migration\n\nOld scripts become adapters.",
        )
        write_md(
            self.root / "systems" / "brain.md",
            base_fm(title="Brain", type="system", knowledge_state="current", tags=["knowledge"]),
            "## Role\n\nBrain stores durable maintained knowledge.",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def request(self, **overrides) -> CompileRequest:
        raw = {
            "alias": "test-wiki",
            "question": "Who owns the shared traversal runtime?",
            "seeds": ["systems/llm-wiki.md"],
        }
        raw.update(overrides)
        return CompileRequest.from_mapping(raw)

    def test_seed_compile_returns_provenance_state_and_authority(self):
        response = compile_context(self.root, self.request()).to_dict()

        self.assertEqual(response["kind"], "compiled_context")
        self.assertEqual(response["query"]["resolved_seeds"], ["systems/llm-wiki.md"])
        seed = response["evidence"][0]
        self.assertEqual(seed["provider"], "seed")
        self.assertEqual(seed["page"], "systems/llm-wiki.md")
        self.assertEqual(seed["authored_state"], "current")
        self.assertIn("source_reference", seed["authority_signals"])
        self.assertEqual(seed["locator"]["start_line"], 17)
        self.assertTrue(response["stop"]["sufficient"])

    def test_question_without_seed_finds_matching_section(self):
        request = self.request(
            question="How do old scripts migrate into adapters?",
            seeds=[],
        )

        response = compile_context(self.root, request).to_dict()

        matches = [item for item in response["evidence"] if item["provider"] == "text"]
        self.assertEqual(matches[0]["locator"]["section"], "Migration")
        self.assertIn("Old scripts become adapters.", matches[0]["content"])

    def test_identical_compiles_are_deterministic(self):
        request = self.request()

        first = compile_context(self.root, request).to_dict()
        second = compile_context(self.root, request).to_dict()

        self.assertEqual(first, second)

    def test_provider_failure_is_visible_without_erasing_valid_evidence(self):
        response = compile_context(self.root, self.request(), extra_providers=(FailingProvider(),)).to_dict()

        self.assertTrue(response["evidence"])
        self.assertEqual(response["diagnostics"][0]["provider"], "broken")
        self.assertEqual(response["diagnostics"][0]["code"], "PROVIDER_FAILED")
        self.assertNotIn("provider exploded", response["diagnostics"][0]["message"])

    def test_unresolved_seed_is_structured_input_error(self):
        with self.assertRaises(ContractError) as raised:
            compile_context(self.root, self.request(seeds=["missing.md"]))

        self.assertEqual(raised.exception.code, "PAGE_NOT_FOUND")
        self.assertEqual(raised.exception.details["seed"], "missing.md")


if __name__ == "__main__":
    unittest.main()
