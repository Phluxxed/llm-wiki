from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.config import (
    CompilerConfig,
    ContentConfig,
    StateConfig,
    StewardshipConfig,
    WikiConfig,
)
from llm_wiki_core.contracts import CompileRequest
from llm_wiki_core.documents import collect_pages
from llm_wiki_core.graph_adapter import (
    GraphAdapterError,
    canonical_page_roots,
    graph_profile,
    open_graph_mirror,
)
from llm_wiki_core.providers.base import ProviderContext
from llm_wiki_core.query_shape import classify_question, required_roles
from tests.wiki_fixture import base_fm, write_md


class WikiGraphAdapterTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.wiki = root / "wiki"
        self.cache = root / "cache"
        write_md(
            self.wiki / "projects" / "brain.md",
            base_fm(title="Brain", knowledge_state="current"),
            "Brain uses the [shared runtime](../concepts/runtime.md).",
        )
        write_md(
            self.wiki / "concepts" / "runtime.md",
            base_fm(
                title="Shared Runtime",
                knowledge_state="current",
                mentioned_in=["projects/brain.md"],
            ),
            "The runtime is owned by [llm-wiki](../systems/llm-wiki.md).",
        )
        write_md(
            self.wiki / "systems" / "llm-wiki.md",
            base_fm(title="llm-wiki", knowledge_state="current"),
            "Canonical traversal implementation.",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def context(self) -> ProviderContext:
        request = CompileRequest.from_mapping(
            {
                "alias": "test",
                "question": "How does Brain connect to llm-wiki?",
                "seeds": ["projects/brain.md", "systems/llm-wiki.md"],
            }
        )
        pages = collect_pages(self.wiki)
        shapes = classify_question(request.question)
        return ProviderContext(
            self.wiki,
            WikiConfig(
                schema_version="1",
                runtime_contract="2",
                profile="default",
                content=ContentConfig(),
                compiler=CompilerConfig(),
                state=StateConfig(),
                stewardship=StewardshipConfig(),
                raw={},
            ),
            request,
            pages,
            shapes,
            required_roles(shapes),
            request.seeds,
        )

    def roots(self, context: ProviderContext) -> dict[str, str]:
        return {path: f"{path}::{page.title}#section" for path, page in context.pages.items()}

    def test_profile_matches_the_approved_loci_namespace_contract(self):
        profile = graph_profile()

        self.assertEqual(profile["schema_version"], 1)
        self.assertEqual(profile["namespace"], "llm-wiki")
        self.assertEqual(
            [(item["type"], item["directed"], item["allowed_resolutions"]) for item in profile["edge_types"]],
            [
                ("body_link", True, ["declared"]),
                ("mentioned_in", True, ["declared"]),
            ],
        )

    def test_outline_mapping_requires_one_canonical_root_for_every_page(self):
        pages = ["concepts/runtime.md", "projects/brain.md"]
        outline = [
            {
                "file": path,
                "symbols": [
                    {
                        "id": f"{path}::child#section",
                        "line": 12,
                        "span_kind": "section",
                    },
                    {
                        "id": f"{path}::root#section",
                        "line": 1,
                        "span_kind": "page_root",
                    },
                ],
            }
            for path in pages
        ]

        roots = canonical_page_roots(outline, pages)

        self.assertEqual(roots, {path: f"{path}::root#section" for path in pages})

    def test_outline_mapping_rejects_a_root_owned_by_another_page(self):
        pages = ["concepts/runtime.md", "projects/brain.md"]
        outline = [
            {
                "file": path,
                "symbols": [
                    {
                        "id": "concepts/runtime.md::root#section",
                        "line": 1,
                        "span_kind": "page_root",
                    }
                ],
            }
            for path in pages
        ]

        with self.assertRaises(GraphAdapterError):
            canonical_page_roots(outline, pages)

    def test_mirror_emits_exact_contributions_without_mutating_the_wiki(self):
        context = self.context()
        source_before = {
            path: (self.wiki / path).read_bytes()
            for path in context.pages
        }

        with open_graph_mirror(context, cache_dir=self.cache) as mirror:
            self.assertIsNone(mirror.page_roots)
            roots = self.roots(context)
            mirror.write_contributions(roots)
            mirror.commit(roots)
            mirror_root = mirror.root

        self.assertFalse((self.wiki / ".loci").exists())
        self.assertEqual(
            source_before,
            {path: (self.wiki / path).read_bytes() for path in context.pages},
        )
        profile_path = mirror_root / ".loci" / "graph" / "profiles" / "llm-wiki.json"
        self.assertEqual(json.loads(profile_path.read_text(encoding="utf-8")), graph_profile())
        contribution_paths = sorted(
            (mirror_root / ".loci" / "graph" / "contributions").glob("*.json")
        )
        self.assertTrue(contribution_paths)
        edges = [
            edge
            for path in contribution_paths
            for edge in json.loads(path.read_text(encoding="utf-8"))["edges"]
        ]
        self.assertEqual({edge["type"] for edge in edges}, {"body_link", "mentioned_in"})
        self.assertTrue(all(edge["namespace"] == "llm-wiki" for edge in edges))
        self.assertTrue(all(edge["resolution"] == "declared" for edge in edges))
        for edge in edges:
            evidence = edge["evidence"]
            source_line = context.pages[evidence["file"]].text.splitlines()[evidence["line"] - 1]
            self.assertTrue(source_line.strip())
            self.assertEqual(len(evidence["content_hash"]), 64)

        with open_graph_mirror(context, cache_dir=self.cache) as cached:
            self.assertEqual(cached.root, mirror_root)
            self.assertEqual(cached.page_roots, roots)

    def test_changed_corpus_invalidates_manifest_and_removes_stale_pages(self):
        context = self.context()
        with open_graph_mirror(context, cache_dir=self.cache) as mirror:
            roots = self.roots(context)
            mirror.write_contributions(roots)
            mirror.commit(roots)
            mirror_root = mirror.root

        (self.wiki / "systems" / "llm-wiki.md").unlink()
        changed = self.context()

        with open_graph_mirror(changed, cache_dir=self.cache) as mirror:
            self.assertEqual(mirror.root, mirror_root)
            self.assertIsNone(mirror.page_roots)
            self.assertFalse((mirror.root / "systems" / "llm-wiki.md").exists())
            self.assertFalse(any((mirror.root / ".loci" / "graph" / "contributions").glob("*.json")))

    def test_tampered_contribution_shard_invalidates_the_cached_manifest(self):
        context = self.context()
        with open_graph_mirror(context, cache_dir=self.cache) as mirror:
            roots = self.roots(context)
            mirror.write_contributions(roots)
            mirror.commit(roots)
            mirror_root = mirror.root

        contribution = next(
            (mirror_root / ".loci" / "graph" / "contributions").glob("*.json")
        )
        payload = json.loads(contribution.read_text(encoding="utf-8"))
        payload["edges"] = []
        contribution.write_text(json.dumps(payload), encoding="utf-8")

        with open_graph_mirror(context, cache_dir=self.cache) as rebuilt:
            self.assertIsNone(rebuilt.page_roots)
            self.assertFalse(
                any((rebuilt.root / ".loci" / "graph" / "contributions").glob("*.json"))
            )


if __name__ == "__main__":
    unittest.main()
