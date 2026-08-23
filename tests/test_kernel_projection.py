from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import tempfile
import unittest
from pathlib import Path


from llm_wiki_core import KernelProjectionError, compile_kernel_projection
from llm_wiki_core.cli import main
from llm_wiki_core.snapshot import publish_snapshot
from tests.wiki_fixture import base_fm, create_wiki_root, write_md


class KernelProjectionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.wiki = create_wiki_root(self.root / "wiki", with_scripts=False)
        self.snapshot_store = self.root / "snapshots"

    def tearDown(self):
        self._tmp.cleanup()

    def test_core_projects_exact_ordered_sections_from_the_resolved_snapshot(self):
        page = self.wiki / "core" / "collaboration.md"
        write_md(
            page,
            base_fm(title="Collaboration"),
            "# Collaboration\n"
            "Page introduction must not be selected.\n"
            "## Identity\n"
            "Work with Vik 🛠.\n"
            "### Working detail\n"
            "Keep this nested content.\n"
            "## Doctrine\n"
            "Prefer current evidence.\n"
            "# Appendix\n"
            "Outside the selected sections.",
        )
        published = publish_snapshot(self.wiki, alias="brain", output_root=self.snapshot_store)
        write_md(page, base_fm(title="Collaboration"), "## Identity\nLater mutable content.")

        projection = compile_kernel_projection(
            alias="brain",
            output_root=self.snapshot_store,
            sources=(
                {"role": "identity", "page": "core/collaboration.md", "section": "Identity"},
                {"role": "doctrine", "page": "core/collaboration.md", "section": "Doctrine"},
            ),
        ).to_dict()

        self.assertEqual(projection["kind"], "collaboration_kernel_projection")
        self.assertEqual(projection["contract_version"], "1")
        self.assertEqual(projection["wiki"]["digest"], published["digest"])
        self.assertEqual(
            [record["roles"] for record in projection["evidence"]],
            [["identity"], ["doctrine"]],
        )
        self.assertEqual(
            projection["evidence"][0]["content"],
            "Work with Vik 🛠.\n### Working detail\nKeep this nested content.\n",
        )
        self.assertEqual(projection["evidence"][1]["content"], "Prefer current evidence.\n")
        self.assertEqual(
            projection["budget"]["content_bytes"],
            len("Work with Vik 🛠.\n### Working detail\nKeep this nested content.\n".encode("utf-8"))
            + len("Prefer current evidence.\n".encode("utf-8")),
        )
        self.assertEqual(
            projection["coverage"],
            {
                "required_roles": ["identity", "doctrine"],
                "covered_roles": ["identity", "doctrine"],
                "uncovered_roles": [],
            },
        )
        self.assertEqual(projection["omissions"], [])
        self.assertEqual(projection["diagnostics"], [])
        self.assertEqual(projection["stop"]["reason"], "all_sources_projected")
        self.assertTrue(projection["stop"]["sufficient"])

    def test_source_roles_are_required_and_unique(self):
        write_md(
            self.wiki / "core" / "collaboration.md",
            base_fm(title="Collaboration"),
            "## Identity\nWork together.\n## Doctrine\nUse evidence.",
        )
        publish_snapshot(self.wiki, alias="brain", output_root=self.snapshot_store)

        cases = (
            ((), "KERNEL_ROLE_MISSING"),
            (
                ({"page": "core/collaboration.md", "section": "Identity"},),
                "KERNEL_SOURCE_INVALID",
            ),
            (
                (
                    {"role": "identity", "page": "core/collaboration.md", "section": "Identity"},
                    {"role": "identity", "page": "core/collaboration.md", "section": "Doctrine"},
                ),
                "KERNEL_ROLE_DUPLICATE",
            ),
        )
        for sources, code in cases:
            with self.subTest(code=code), self.assertRaises(KernelProjectionError) as raised:
                compile_kernel_projection(alias="brain", output_root=self.snapshot_store, sources=sources)
            self.assertEqual(raised.exception.code, code)

    def test_page_and_section_resolution_fail_closed(self):
        write_md(
            self.wiki / "core" / "identity.md",
            base_fm(title="Identity"),
            "## Identity\nFirst.\n## Identity\nSecond.",
        )
        publish_snapshot(self.wiki, alias="brain", output_root=self.snapshot_store)
        cases = (
            ("../core/identity.md", "Identity", "KERNEL_PAGE_PATH_UNSAFE"),
            ("core\\identity.md", "Identity", "KERNEL_PAGE_PATH_UNSAFE"),
            ("core/missing.md", "Identity", "KERNEL_PAGE_NOT_FOUND"),
            ("core/identity.md", "Missing", "KERNEL_SECTION_NOT_FOUND"),
            ("core/identity.md", "Identity", "KERNEL_SECTION_AMBIGUOUS"),
        )

        for page, section, code in cases:
            with self.subTest(code=code), self.assertRaises(KernelProjectionError) as raised:
                compile_kernel_projection(
                    alias="brain",
                    output_root=self.snapshot_store,
                    sources=({"role": "identity", "page": page, "section": section},),
                )
            self.assertEqual(raised.exception.code, code)

    def test_section_resolution_ignores_heading_syntax_inside_fenced_code(self):
        write_md(
            self.wiki / "core" / "identity.md",
            base_fm(title="Identity"),
            "## Identity\n"
            "The real body.\n"
            "```markdown\n"
            "## Identity\n"
            "This is an example, not a section.\n"
            "```\n"
            "## Next\n"
            "Not selected.",
        )
        publish_snapshot(self.wiki, alias="brain", output_root=self.snapshot_store)

        projection = compile_kernel_projection(
            alias="brain",
            output_root=self.snapshot_store,
            sources=({"role": "identity", "page": "core/identity.md", "section": "Identity"},),
        ).to_dict()

        self.assertEqual(
            projection["evidence"][0]["content"],
            "The real body.\n```markdown\n## Identity\nThis is an example, not a section.\n```\n",
        )

    def test_section_resolution_supports_setext_markdown_headings(self):
        write_md(
            self.wiki / "core" / "identity.md",
            base_fm(title="Identity"),
            "Identity\n"
            "--------\n"
            "Setext body.\n"
            "### Nested\n"
            "Still identity.\n"
            "Doctrine\n"
            "--------\n"
            "Not selected.",
        )
        publish_snapshot(self.wiki, alias="brain", output_root=self.snapshot_store)

        projection = compile_kernel_projection(
            alias="brain",
            output_root=self.snapshot_store,
            sources=({"role": "identity", "page": "core/identity.md", "section": "Identity"},),
        ).to_dict()

        self.assertEqual(
            projection["evidence"][0]["content"],
            "Setext body.\n### Nested\nStill identity.\n",
        )
        self.assertEqual(projection["evidence"][0]["locator"]["heading_level"], 2)

    def test_atomic_kernel_sections_fail_before_exceeding_the_hard_content_ceiling(self):
        write_md(
            self.wiki / "core" / "identity.md",
            base_fm(title="Identity"),
            "## Identity\n" + ("x" * 4_096),
        )
        publish_snapshot(self.wiki, alias="brain", output_root=self.snapshot_store)

        with self.assertRaises(KernelProjectionError) as raised:
            compile_kernel_projection(
                alias="brain",
                output_root=self.snapshot_store,
                sources=(
                    {"role": "identity", "page": "core/identity.md", "section": "Identity"},
                ),
            )

        self.assertEqual(raised.exception.code, "KERNEL_CONTENT_CEILING_EXCEEDED")
        self.assertEqual(raised.exception.details["content_bytes"], 4_097)
        self.assertEqual(raised.exception.details["max_content_bytes"], 4_096)
        self.assertEqual(raised.exception.details["roles"], ["identity"])

    def test_target_is_advisory_and_reports_utf8_content_bytes(self):
        body = "🛠" * 769
        write_md(
            self.wiki / "core" / "identity.md",
            base_fm(title="Identity"),
            "## Identity\n" + body,
        )
        publish_snapshot(self.wiki, alias="brain", output_root=self.snapshot_store)

        projection = compile_kernel_projection(
            alias="brain",
            output_root=self.snapshot_store,
            sources=({"role": "identity", "page": "core/identity.md", "section": "Identity"},),
        ).to_dict()

        expected_bytes = len((body + "\n").encode("utf-8"))
        self.assertEqual(expected_bytes, 3_077)
        self.assertEqual(projection["budget"]["content_bytes"], expected_bytes)
        self.assertTrue(projection["budget"]["target_exceeded"])
        self.assertEqual(projection["diagnostics"][0]["code"], "KERNEL_TARGET_EXCEEDED")
        self.assertEqual(projection["diagnostics"][0]["details"]["content_bytes"], expected_bytes)
        self.assertEqual(
            projection["reporting"]["diagnostics"],
            {"total": 1, "returned": 1},
        )

    def test_cli_compiles_the_same_versioned_projection_from_repeatable_json_sources(self):
        write_md(
            self.wiki / "core" / "collaboration.md",
            base_fm(title="Collaboration"),
            "## Identity\nWork together.\n## Doctrine\nUse current evidence.",
        )
        publish_snapshot(self.wiki, alias="brain", output_root=self.snapshot_store)
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "compile-kernel",
                    "--alias",
                    "brain",
                    "--output-root",
                    str(self.snapshot_store),
                    "--source",
                    json.dumps(
                        {
                            "role": "doctrine",
                            "page": "core/collaboration.md",
                            "section": "Doctrine",
                        }
                    ),
                    "--source",
                    json.dumps(
                        {
                            "role": "identity",
                            "page": "core/collaboration.md",
                            "section": "Identity",
                        }
                    ),
                ]
            )

        projection = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(projection["kind"], "collaboration_kernel_projection")
        self.assertEqual(projection["contract_version"], "1")
        self.assertEqual(
            projection["projection"]["sources"],
            [
                {"role": "doctrine", "page": "core/collaboration.md", "section": "Doctrine"},
                {"role": "identity", "page": "core/collaboration.md", "section": "Identity"},
            ],
        )
        self.assertEqual(
            [record["roles"] for record in projection["evidence"]],
            [["doctrine"], ["identity"]],
        )


if __name__ == "__main__":
    unittest.main()
