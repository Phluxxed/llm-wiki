from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.documents import parse_frontmatter
from llm_wiki_core.export_okf import ExportError, export_okf, _publish
from tests.wiki_fixture import base_fm, create_wiki_root, write_md


class ExportOkfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = create_wiki_root(self.parent / "wiki", with_scripts=False)
        self.output = self.parent / "bundle"

    def source(self, name="original.md", data=b"# Original\r\n\r\nNo frontmatter.\r\n"):
        path = self.root / "sources" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def page(self, **overrides):
        write_md(self.root / "notes/a.md", base_fm(**overrides), "Body.")

    def test_immutable_sources_temporal_history_and_honest_generation(self):
        original = self.source()
        temporal = "temporal_claim_revisions:\n- claim_scope: test\n  steward_evidence_refs:\n  - sources/original.md\n  recorded_at: '2026-06-23T00:00:00Z'\n"
        page = self.root / "notes/a.md"
        page.parent.mkdir()
        text = "---\ntype: Note\nstatus: Historical\nsource: sources/original.md\nevidence: [sources/original.md]\nsources:\n- resource: sources/original.md\n  title: Original\ntimestamp: '2026-06-23T00:00:00Z'\n" + temporal + "---\n[Source](./sources/original.md#section)\n"
        page.write_text(text)
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

        result = export_okf(self.root, self.output)

        self.assertTrue(result["conformant"])
        self.assertTrue(result["self_contained"])
        json.dumps(result)
        exported = (self.output / "notes/a.md").read_text()
        fm = parse_frontmatter(exported)
        self.assertEqual(fm["status"], "stable")
        self.assertEqual(fm["workflow_status"], "Historical")
        self.assertNotIn("generated", fm)
        self.assertNotIn("verified", fm)
        self.assertEqual(fm["timestamp"], "2026-06-23T00:00:00Z")
        self.assertIn(temporal, exported)
        self.assertEqual(fm["source"], "references/raw/sources/original.md.txt")
        self.assertEqual(fm["sources"][0]["resource"], "/references/raw/sources/original.md.txt")
        self.assertIn("(/sources/original.md#section)", exported)
        self.assertEqual((self.output / fm["source"]).read_bytes(), original.read_bytes())
        wrapper = parse_frontmatter((self.output / "sources/original.md").read_text())
        self.assertEqual(wrapper["type"], "Reference")
        manifest = json.loads((self.output / "export-manifest.json").read_text())
        self.assertEqual(manifest["files"][0]["sha256"], hashlib.sha256(original.read_bytes()).hexdigest())
        after = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        for path in self.output.rglob("*.md"):
            if path.name not in {"index.md", "log.md"}:
                self.assertTrue(parse_frontmatter(path.read_text())["type"])

    def test_links_mixed_conventions_titles_fragments_and_code(self):
        self.source("a (one).md")
        self.source("second.md")
        write_md(self.root / "notes/other.md", {"type": "Note"}, "Other")
        body = '''[root](./sources/second.md#part)
[relative](../sources/second.md)
[neighbor](other.md "title")
[angle](<../sources/a (one).md#part>)
[balanced](../sources/a\\ (one).md)
[definition][r]
[r]: ../sources/second.md "Title"
`[code](missing.md)`
````markdown
[code](missing-fenced.md)
````
    [code](missing-indented.md)
'''
        write_md(self.root / "notes/a.md", {"type": "Note"}, body)
        result = export_okf(self.root, self.output)
        self.assertTrue(result["self_contained"], result["diagnostics"])
        exported = (self.output / "notes/a.md").read_text()
        self.assertIn("[root](/sources/second.md#part)", exported)
        self.assertIn('[neighbor](/notes/other.md "title")', exported)
        self.assertIn("[angle](</sources/a%20%28one%29.md#part>)", exported)
        self.assertIn("/sources/a%20%28one%29.md#part", exported)
        self.assertIn("[r]: /sources/second.md", exported)
        self.assertIn("`[code](missing.md)`", exported)
        self.assertIn("[code](missing-fenced.md)", exported)

    def test_support_files_only_when_linked_and_reserved_files_regenerated(self):
        (self.root / "README.md").write_bytes(b"# Manual\r\nNo frontmatter\r\n")
        self.source("index.md", b"This was not an index.")
        self.source("log.md", b"## [2026-01-01] Old incompatible log")
        write_md(self.root / "notes/a.md", {"type": "Note"}, "[Manual](./README.md) [index](../sources/index.md) [log](../sources/log.md)")
        result = export_okf(self.root, self.output)
        self.assertTrue(result["self_contained"], result["diagnostics"])
        self.assertFalse((self.output / "wiki-agent.md").exists())
        self.assertEqual((self.output / "references/raw/README.md.txt").read_bytes(), (self.root / "README.md").read_bytes())
        self.assertTrue((self.output / "index.md").read_text().startswith('---\nokf_version: "0.2"\n---\n'))
        self.assertRegex((self.output / "log.md").read_text(), r"\n## \d{4}-\d{2}-\d{2}\n")
        self.assertNotIn("---", (self.output / "sources/index.md").read_text())
        self.assertNotIn("[2026-01-01]", (self.output / "sources/log.md").read_text())

    def test_missing_and_outside_references_reported_without_copy_or_path_mutation(self):
        secret = self.parent / "secret.md"
        secret.write_text("UNREADABLE_SECRET_SENTINEL")
        source = self.root / "sources"
        source.mkdir()
        (source / "escape.md").symlink_to(secret)
        self.page(source=str(secret), evidence=["sources/missing.md", "sources/escape.md"])
        result = export_okf(self.root, self.output)
        self.assertTrue(result["conformant"])
        self.assertFalse(result["self_contained"])
        self.assertTrue(result["diagnostics"])
        self.assertEqual(parse_frontmatter((self.output / "notes/a.md").read_text())["source"], str(secret))
        self.assertFalse((self.output / "references/raw/sources/escape.md.txt").exists())
        self.assertNotIn(b"UNREADABLE_SECRET_SENTINEL", b"".join(p.read_bytes() for p in self.output.rglob("*") if p.is_file()))

    def test_existing_and_overlapping_destinations_refused(self):
        self.page()
        self.output.mkdir()
        (self.output / "keep").write_text("keep")
        with self.assertRaises(ExportError) as caught:
            export_okf(self.root, self.output)
        self.assertEqual(caught.exception.code, "DESTINATION_EXISTS")
        self.assertEqual((self.output / "keep").read_text(), "keep")
        with self.assertRaises(ExportError) as caught:
            export_okf(self.root, self.root / "export")
        self.assertEqual(caught.exception.code, "OVERLAPPING_PATHS")

    def test_atomic_publication_rejects_destination_created_after_preflight(self):
        self.page()
        def race(stage, output):
            output.mkdir()
            _publish(stage, output)
        with patch("llm_wiki_core.export_okf._publish", side_effect=race):
            with self.assertRaises(ExportError) as caught:
                export_okf(self.root, self.output)
        self.assertEqual(caught.exception.code, "DESTINATION_EXISTS")
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(list(self.parent.glob(".bundle.export-*")), [])

    def test_failed_write_removes_stage_and_never_publishes(self):
        self.page()
        with patch.object(Path, "write_bytes", side_effect=OSError("disk full")):
            with self.assertRaises(ExportError) as caught:
                export_okf(self.root, self.output)
        self.assertEqual(caught.exception.code, "EXPORT_IO_ERROR")
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.parent.glob(".bundle.export-*")), [])

    def test_configured_source_directory_and_exclusions(self):
        (self.root / ".llm-wiki.toml").write_text('schema_version = "1"\nruntime_contract = "2"\n[content]\nsource_directory = "inputs"\nexclude_directories = ["private"]\n')
        write_md(self.root / "private/excluded.md", {"type": "Note"}, "Excluded")
        write_md(self.root / "notes/a.md", {"type": "Note", "source": "inputs/raw.md"}, "Included")
        (self.root / "inputs").mkdir()
        (self.root / "inputs/raw.md").write_text("Raw")
        result = export_okf(self.root, self.output)
        self.assertEqual(result["counts"]["concepts"], 1)
        self.assertFalse((self.output / "private/excluded.md").exists())
        self.assertEqual((self.output / "references/raw/inputs/raw.md.txt").read_text(), "Raw")


if __name__ == "__main__":
    unittest.main()
