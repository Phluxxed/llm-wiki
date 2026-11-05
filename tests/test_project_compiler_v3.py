from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.compiler import compile_context
from llm_wiki_core.contracts import CompileRequest, ContractError, WorkspaceIdentity
from tests.wiki_fixture import base_fm, write_md


class ProjectCompilerV3Test(unittest.TestCase):
    def request(self, **overrides) -> CompileRequest:
        raw = {
            "contract_version": "3",
            "alias": "fixture",
            "question": "What should I work on next?",
            "workspace_identity": {
                "directory_alias": "renamed-checkout",
                "remotes": ["github.com/acme/alpha"],
            },
        }
        raw.update(overrides)
        return CompileRequest.from_mapping(raw)

    def test_v3_identity_is_strict_and_v1_v2_reject_it(self):
        request = self.request()
        self.assertIsInstance(request.workspace_identity, WorkspaceIdentity)
        self.assertEqual(
            request.to_dict()["workspace_identity"],
            {
                "directory_alias": "renamed-checkout",
                "remotes": ["github.com/acme/alpha"],
            },
        )
        for version in ("1", "2"):
            with self.subTest(version=version), self.assertRaises(ContractError) as raised:
                CompileRequest.from_mapping(
                    {
                        "contract_version": version,
                        "alias": "fixture",
                        "question": "What?",
                        "workspace_identity": {"directory_alias": "alpha"},
                    }
                )
            self.assertEqual(raised.exception.details["field"], "workspace_identity")

    def test_v3_rejects_path_bearing_and_non_normalized_identity_values(self):
        bad_values = (
            {"directory_alias": "/tmp/alpha"},
            {"directory_alias": "alpha\\checkout"},
            {"remotes": ["https://github.com/acme/alpha"]},
            {"remotes": ["github.com/acme/../alpha"]},
            {"remotes": ["GitHub.com/acme/alpha"]},
        )
        for identity in bad_values:
            with self.subTest(identity=identity), self.assertRaises(ContractError):
                CompileRequest.from_mapping(
                    {
                        "contract_version": "3",
                        "alias": "fixture",
                        "question": "What?",
                        "workspace_identity": identity,
                    }
                )

    def test_remote_resolution_and_project_orientation_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fixture(Path(tmpdir))
            response = compile_context(root, self.request()).to_dict()

        self.assertEqual(
            response["query"]["project_resolution"],
            {
                "status": "matched",
                "project_id": "alpha",
                "page": "projects/alpha.md",
                "matched_by": "remote",
            },
        )
        fallback = next(item for item in response["evidence"] if item["provider"] == "project")
        self.assertEqual(fallback["route"], "project_orientation_fallback")
        self.assertEqual(fallback["page"], "projects/alpha.md")
        self.assertIn("active_project:alpha", fallback["selection_reasons"])
        self.assertNotIn("projects/beta.md", str(response["evidence"]))
        self.assertTrue(any(item["page"] == "global.md" for item in response["evidence"]))

    def test_orientation_fallback_survives_weak_project_candidate_selection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fallback_selection_fixture(Path(tmpdir))
            response = compile_context(
                root,
                self.request(
                    workspace_identity={
                        "directory_alias": "llm-wiki",
                        "remotes": ["github.com/phluxxed/llm-wiki"],
                    }
                ),
            ).to_dict()

        self.assertTrue(
            any(
                item["provider"] == "source" and item["page"] == "global.md"
                for item in response["evidence"]
            )
        )
        project_evidence = [
            item
            for item in response["evidence"]
            if item["page"] == "projects/llm-wiki.md"
        ]
        self.assertTrue(project_evidence)
        self.assertTrue(
            any(item["route"] == "project_orientation_fallback" for item in project_evidence)
        )
        # Matching work may supplement the result, but must not replace the
        # initial project orientation that was required before supplementation.
        pages = [item["page"] for item in response["evidence"]]
        self.assertLess(pages.index("projects/llm-wiki.md"), pages.index("projects/llm-wiki-work.md"))

    def test_scope_seed_graph_discovery_does_not_suppress_orientation_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fixture(Path(tmpdir))
            write_md(
                root / "projects" / "alpha.md",
                base_fm(
                    title="Alpha",
                    type="project",
                    identity={
                        "project_id": "alpha",
                        "aliases": ["Alpha"],
                        "remotes": ["github.com/acme/alpha"],
                    },
                ),
                "Alpha orientation links to [context](alpha-context.md).",
            )
            write_md(
                root / "projects" / "alpha-context.md",
                base_fm(title="Alpha Context", projects=["alpha"]),
                "Context-only project material.",
            )
            response = compile_context(
                root,
                self.request(question="How do systems connect?"),
            ).to_dict()

        self.assertFalse(any("bridge" in item["roles"] for item in response["evidence"]))
        self.assertIn("bridge", response["coverage"]["uncovered_roles"])
        fallback = next(item for item in response["evidence"] if item["provider"] == "project")
        self.assertEqual(fallback["route"], "project_orientation_fallback")


    def test_alias_fallback_unknown_and_ambiguous_resolution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fixture(Path(tmpdir))
            alias_response = compile_context(
                root,
                self.request(
                    workspace_identity={"directory_alias": "Alpha"},
                ),
            ).to_dict()
            unknown_response = compile_context(
                root,
                self.request(
                    workspace_identity={"directory_alias": "unmapped", "remotes": []},
                ),
            ).to_dict()
            write_md(
                root / "projects" / "duplicate.md",
                base_fm(
                    title="Duplicate",
                    type="project",
                    identity={"project_id": "duplicate", "aliases": ["alpha"]},
                ),
                "Duplicate project.",
            )
            ambiguous_response = compile_context(
                root,
                self.request(
                    workspace_identity={"directory_alias": "alpha", "remotes": []},
                ),
            ).to_dict()

        self.assertEqual(alias_response["query"]["project_resolution"]["matched_by"], "alias")
        self.assertEqual(unknown_response["query"]["project_resolution"], {"status": "unknown"})
        self.assertFalse(any(item["provider"] == "project" for item in unknown_response["evidence"]))
        self.assertEqual(ambiguous_response["query"]["project_resolution"]["status"], "ambiguous")
        self.assertEqual(ambiguous_response["query"]["project_resolution"]["candidate_count"], 2)
        self.assertEqual(
            [item["code"] for item in ambiguous_response["diagnostics"] if item["code"] == "PROJECT_IDENTITY_AMBIGUOUS"],
            ["PROJECT_IDENTITY_AMBIGUOUS"],
        )

    def test_question_identity_and_explicit_seed_membership_widen_scope(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fixture(Path(tmpdir))
            question_response = compile_context(
                root,
                self.request(question="How should Beta work next?"),
            ).to_dict()
            seeded_response = compile_context(
                root,
                self.request(seeds=["projects/beta-work.md"]),
            ).to_dict()

        self.assertEqual(
            question_response["query"]["project_scope"]["active_project_ids"],
            ["alpha", "beta"],
        )
        self.assertEqual(
            question_response["query"]["project_scope"]["expansions"],
            [{"project_id": "beta", "reason": "question_identity_match"}],
        )
        self.assertEqual(
            seeded_response["query"]["project_scope"]["expansions"],
            [{"project_id": "beta", "reason": "explicit_seed_membership"}],
        )
        beta_seed = next(item for item in seeded_response["evidence"] if item["page"] == "projects/beta-work.md")
        self.assertIn("explicit_seed_cross_project", beta_seed["selection_reasons"])

    def test_invalid_membership_is_never_global_but_exact_seed_is_admitted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = _fixture(Path(tmpdir))
            write_md(
                root / "invalid.md",
                base_fm(title="Invalid", projects=[]),
                "General next-step planning guidance that must not leak.",
            )
            ordinary = compile_context(root, self.request()).to_dict()
            seeded = compile_context(root, self.request(seeds=["invalid.md"])).to_dict()

        self.assertNotIn("must not leak", str(ordinary["evidence"]))
        self.assertIn("must not leak", str(seeded["evidence"]))
        self.assertTrue(
            any(
                item["code"] == "PROJECT_IDENTITY_INVALID" and item["details"]["page"] == "invalid.md"
                for item in ordinary["diagnostics"]
            )
        )


def _fixture(root: Path) -> Path:
    config = (
        'schema_version = "1"\n'
        'runtime_contract = "3"\n'
        '[compiler]\n'
        'providers = ["seed", "frontmatter", "text", "graph", "source"]\n'
    )
    root.mkdir(parents=True, exist_ok=True)
    (root / ".llm-wiki.toml").write_text(config, encoding="utf-8")
    write_md(
        root / "projects" / "alpha.md",
        base_fm(
            title="Alpha",
            type="project",
            identity={
                "project_id": "alpha",
                "aliases": ["Alpha"],
                "remotes": ["github.com/acme/alpha"],
            },
        ),
        "Alpha project orientation and durable implementation context.",
    )
    write_md(
        root / "projects" / "alpha-work.md",
        base_fm(title="Alpha Work", projects=["alpha"]),
        "Alpha implementation history.",
    )
    write_md(
        root / "projects" / "beta.md",
        base_fm(
            title="Beta",
            type="project",
            identity={
                "project_id": "beta",
                "aliases": ["Beta"],
                "remotes": ["github.com/acme/beta"],
            },
        ),
        "Beta project orientation.",
    )
    write_md(
        root / "projects" / "beta-work.md",
        base_fm(title="Beta Work", projects=["beta"]),
        "next " * 24,
    )
    write_md(
        root / "global.md",
        base_fm(title="Global Guidance"),
        "General next-step planning guidance.",
    )
    return root


def _fallback_selection_fixture(root: Path) -> Path:
    config = (
        'schema_version = "1"\n'
        'runtime_contract = "3"\n'
        '[compiler]\n'
        'providers = ["seed", "frontmatter", "text", "graph", "source"]\n'
    )
    root.mkdir(parents=True, exist_ok=True)
    (root / ".llm-wiki.toml").write_text(config, encoding="utf-8")
    write_md(
        root / "projects" / "llm-wiki.md",
        base_fm(
            title="llm-wiki",
            type="project",
            identity={
                "project_id": "llm_wiki",
                "aliases": ["llm-wiki"],
                "remotes": ["github.com/phluxxed/llm-wiki"],
            },
        ),
        "Repository orientation and durable implementation context.",
    )
    write_md(
        root / "projects" / "llm-wiki-work.md",
        base_fm(title="llm-wiki work", projects=["llm_wiki"]),
        "Work next item.",
    )
    write_md(
        root / "global.md",
        base_fm(title="Global Guidance", source="sources/global.md"),
        "General work next guidance.",
    )
    (root / "sources").mkdir(parents=True, exist_ok=True)
    (root / "sources" / "global.md").write_text(
        "General work next guidance from the shared source.",
        encoding="utf-8",
    )
    return root




if __name__ == "__main__":
    unittest.main()
