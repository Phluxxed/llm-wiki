from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.cli import main
from llm_wiki_core.snapshot import SnapshotError, publish_snapshot, resolve_snapshot
from tests.wiki_fixture import base_fm, create_wiki_root, write_md


class SnapshotPublicationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_digest_is_deterministic_across_source_and_output_locations(self):
        first_wiki = create_wiki_root(self.root / "first-wiki", with_scripts=False)
        second_wiki = create_wiki_root(self.root / "second-wiki", with_scripts=False)
        for wiki in (first_wiki, second_wiki):
            write_md(wiki / "projects" / "anvil.md", base_fm(title="Anvil"), "Project facts.")

        first = publish_snapshot(first_wiki, alias="brain", output_root=self.root / "first-output")
        second = publish_snapshot(second_wiki, alias="brain", output_root=self.root / "second-output")

        self.assertEqual(first["contract_version"], "1")
        self.assertEqual(first["status"], "published")
        self.assertEqual(first["digest"], second["digest"])
        self.assertNotEqual(first["snapshot_wiki_root"], second["snapshot_wiki_root"])

    def test_failed_publication_preserves_current_snapshot(self):
        wiki = create_wiki_root(self.root / "wiki", with_scripts=False)
        write_md(wiki / "projects" / "anvil.md", base_fm(title="Anvil"), "Accepted facts.")
        output = self.root / "snapshots"
        first = publish_snapshot(wiki, alias="brain", output_root=output)
        before_receipt = (output / "brain" / "current.json").read_bytes()

        outside = self.root / "outside-agent.md"
        outside.write_text("# Untrusted replacement\n", encoding="utf-8")
        (wiki / "wiki-agent.md").unlink()
        (wiki / "wiki-agent.md").symlink_to(outside)

        with self.assertRaises(SnapshotError) as raised:
            publish_snapshot(wiki, alias="brain", output_root=output)

        self.assertEqual(raised.exception.code, "SNAPSHOT_SYMLINK_REJECTED")
        self.assertEqual((output / "brain" / "current.json").read_bytes(), before_receipt)
        self.assertEqual(resolve_snapshot(alias="brain", output_root=output).digest, first["digest"])

    def test_published_snapshot_content_is_isolated_and_read_only(self):
        wiki = create_wiki_root(self.root / "wiki", with_scripts=False)
        page = wiki / "projects" / "anvil.md"
        write_md(page, base_fm(title="Anvil"), "Published facts.")

        published = publish_snapshot(wiki, alias="brain", output_root=self.root / "snapshots")
        snapshot_page = Path(published["snapshot_wiki_root"]) / "projects" / "anvil.md"
        snapshot_bytes = snapshot_page.read_bytes()
        write_md(page, base_fm(title="Anvil"), "Later source edit.")

        self.assertEqual(snapshot_page.read_bytes(), snapshot_bytes)
        self.assertEqual(stat.S_IMODE(snapshot_page.stat().st_mode) & 0o222, 0)
        self.assertEqual(stat.S_IMODE(snapshot_page.parent.stat().st_mode) & 0o222, 0)

    def test_optional_brain_bootstrap_is_published_and_integrity_checked(self):
        wiki = create_wiki_root(self.root / "wiki", with_scripts=False)
        bootstrap_content = '{"brain_id":"codex","version":1}\n'
        (wiki / "brain-bootstrap.json").write_text(bootstrap_content, encoding="utf-8")
        output = self.root / "snapshots"
        published = publish_snapshot(wiki, alias="brain", output_root=output)
        snapshot_bootstrap = Path(published["snapshot_wiki_root"]) / "brain-bootstrap.json"

        self.assertEqual(snapshot_bootstrap.read_text(encoding="utf-8"), bootstrap_content)

        snapshot_bootstrap.chmod(0o600)
        snapshot_bootstrap.write_text('{"brain_id":"wrong","version":1}\n', encoding="utf-8")
        snapshot_bootstrap.chmod(0o400)
        with self.assertRaises(SnapshotError) as raised:
            resolve_snapshot(alias="brain", output_root=output)

        self.assertEqual(raised.exception.code, "SNAPSHOT_UNAVAILABLE")
        self.assertEqual(raised.exception.details["failures"][0]["code"], "SNAPSHOT_INTEGRITY_FAILED")

    def test_reader_rejects_snapshot_with_content_digest_mismatch(self):
        wiki = create_wiki_root(self.root / "wiki", with_scripts=False)
        write_md(wiki / "projects" / "anvil.md", base_fm(title="Anvil"), "Published facts.")
        output = self.root / "snapshots"
        published = publish_snapshot(wiki, alias="brain", output_root=output)
        snapshot_page = Path(published["snapshot_wiki_root"]) / "projects" / "anvil.md"
        snapshot_page.chmod(0o600)
        snapshot_page.write_text("tampered\n", encoding="utf-8")
        snapshot_page.chmod(0o400)

        with self.assertRaises(SnapshotError) as raised:
            resolve_snapshot(alias="brain", output_root=output)

        self.assertEqual(raised.exception.code, "SNAPSHOT_UNAVAILABLE")
        self.assertEqual(raised.exception.details["failures"][0]["code"], "SNAPSHOT_INTEGRITY_FAILED")

    def test_reader_uses_verified_previous_snapshot_when_current_is_corrupt(self):
        wiki = create_wiki_root(self.root / "wiki", with_scripts=False)
        page = wiki / "projects" / "anvil.md"
        write_md(page, base_fm(title="Anvil"), "First generation.")
        output = self.root / "snapshots"
        first = publish_snapshot(wiki, alias="brain", output_root=output)
        write_md(page, base_fm(title="Anvil"), "Second generation.")
        second = publish_snapshot(wiki, alias="brain", output_root=output)
        current_page = Path(second["snapshot_wiki_root"]) / "projects" / "anvil.md"
        current_page.chmod(0o600)
        current_page.write_text("corrupt\n", encoding="utf-8")
        current_page.chmod(0o400)

        resolved = resolve_snapshot(alias="brain", output_root=output)

        self.assertEqual(resolved.status, "last_known_good")
        self.assertEqual(resolved.digest, first["digest"])

    def test_publish_snapshot_cli_returns_the_storage_contract_without_source_path(self):
        wiki = create_wiki_root(self.root / "wiki", with_scripts=False)
        write_md(wiki / "projects" / "anvil.md", base_fm(title="Anvil"), "Project facts.")
        output = self.root / "snapshots"
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "publish-snapshot",
                    "--wiki",
                    str(wiki),
                    "--alias",
                    "brain",
                    "--output-root",
                    str(output),
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["contract_version"], "1")
        self.assertEqual(payload["status"], "published")
        self.assertEqual(Path(payload["snapshot_wiki_root"]).parent.name, payload["digest"])
        self.assertNotIn("source_wiki_root", payload)

    def test_resolve_snapshot_cli_returns_verified_current_contract(self):
        wiki = create_wiki_root(self.root / "wiki", with_scripts=False)
        write_md(wiki / "projects" / "anvil.md", base_fm(title="Anvil"), "Project facts.")
        output = self.root / "snapshots"
        published = publish_snapshot(wiki, alias="brain", output_root=output)
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "resolve-snapshot",
                    "--alias",
                    "brain",
                    "--output-root",
                    str(output),
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload,
            {
                "contract_version": "1",
                "alias": "brain",
                "digest": published["digest"],
                "snapshot_wiki_root": published["snapshot_wiki_root"],
                "status": "current",
            },
        )

    def test_resolve_snapshot_cli_reports_verified_last_known_good(self):
        wiki = create_wiki_root(self.root / "wiki", with_scripts=False)
        page = wiki / "projects" / "anvil.md"
        write_md(page, base_fm(title="Anvil"), "First generation.")
        output = self.root / "snapshots"
        first = publish_snapshot(wiki, alias="brain", output_root=output)
        write_md(page, base_fm(title="Anvil"), "Second generation.")
        current = publish_snapshot(wiki, alias="brain", output_root=output)
        current_page = Path(current["snapshot_wiki_root"]) / "projects" / "anvil.md"
        current_page.chmod(0o600)
        current_page.write_text("corrupt\n", encoding="utf-8")
        current_page.chmod(0o400)
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "resolve-snapshot",
                    "--alias",
                    "brain",
                    "--output-root",
                    str(output),
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["digest"], first["digest"])
        self.assertEqual(payload["snapshot_wiki_root"], first["snapshot_wiki_root"])
        self.assertEqual(payload["status"], "last_known_good")

    def test_republishing_identical_content_reports_already_current(self):
        wiki = create_wiki_root(self.root / "wiki", with_scripts=False)
        write_md(wiki / "projects" / "anvil.md", base_fm(title="Anvil"), "Project facts.")
        output = self.root / "snapshots"
        first = publish_snapshot(wiki, alias="brain", output_root=output)
        receipt_before = (output / "brain" / "current.json").read_bytes()

        second = publish_snapshot(wiki, alias="brain", output_root=output)

        self.assertEqual(second["status"], "already_current")
        self.assertEqual(second["digest"], first["digest"])
        self.assertEqual((output / "brain" / "current.json").read_bytes(), receipt_before)

    def test_referenced_source_symlink_is_rejected_instead_of_rewritten(self):
        wiki = create_wiki_root(self.root / "wiki", with_scripts=False)
        sources = wiki / "sources"
        sources.mkdir()
        (sources / "real.md").write_text("Source evidence.\n", encoding="utf-8")
        (sources / "linked.md").symlink_to(sources / "real.md")
        write_md(
            wiki / "projects" / "anvil.md",
            base_fm(title="Anvil", source="sources/linked.md"),
            "Project facts.",
        )

        with self.assertRaises(SnapshotError) as raised:
            publish_snapshot(wiki, alias="brain", output_root=self.root / "snapshots")

        self.assertEqual(raised.exception.code, "SNAPSHOT_SYMLINK_REJECTED")

    def test_reader_rejects_snapshot_that_has_become_writable(self):
        wiki = create_wiki_root(self.root / "wiki", with_scripts=False)
        write_md(wiki / "projects" / "anvil.md", base_fm(title="Anvil"), "Project facts.")
        output = self.root / "snapshots"
        published = publish_snapshot(wiki, alias="brain", output_root=output)
        snapshot_page = Path(published["snapshot_wiki_root"]) / "projects" / "anvil.md"
        snapshot_page.chmod(0o600)

        with self.assertRaises(SnapshotError) as raised:
            resolve_snapshot(alias="brain", output_root=output)

        self.assertEqual(raised.exception.code, "SNAPSHOT_UNAVAILABLE")
        self.assertEqual(raised.exception.details["failures"][0]["code"], "SNAPSHOT_INTEGRITY_FAILED")

    def test_reader_rejects_snapshot_with_missing_manifest(self):
        wiki = create_wiki_root(self.root / "wiki", with_scripts=False)
        write_md(wiki / "projects" / "anvil.md", base_fm(title="Anvil"), "Project facts.")
        output = self.root / "snapshots"
        published = publish_snapshot(wiki, alias="brain", output_root=output)
        snapshot_root = Path(published["snapshot_wiki_root"]).parent
        snapshot_root.chmod(0o700)
        (snapshot_root / "snapshot.json").unlink()
        snapshot_root.chmod(0o500)

        with self.assertRaises(SnapshotError) as raised:
            resolve_snapshot(alias="brain", output_root=output)

        self.assertEqual(raised.exception.code, "SNAPSHOT_UNAVAILABLE")
        self.assertEqual(raised.exception.details["failures"][0]["code"], "SNAPSHOT_INTEGRITY_FAILED")


if __name__ == "__main__":
    unittest.main()
