from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.compiler import compile_context
from llm_wiki_core.contracts import CompileRequest
from llm_wiki_core.temporal_persistence import build_temporal_claim_revision
from tests.wiki_fixture import base_fm, create_wiki_root, write_md


def _configure_compiler(root: Path) -> None:
    (root / ".llm-wiki.toml").write_text(
        'schema_version = "1"\n'
        'runtime_contract = "2"\n'
        '[compiler]\n'
        'providers = ["seed", "frontmatter", "text", "source"]\n'
        'graph_backend = "legacy"\n',
        encoding="utf-8",
    )


def _write_source(root: Path, reference: str, content: str) -> None:
    source = root / reference
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(content, encoding="utf-8")


def _request(
    question: str,
    *,
    seeds: tuple[str, ...] = (),
    state_view: str = "current",
    temporal: dict | None = None,
) -> CompileRequest:
    raw = {
        "contract_version": "2",
        "alias": "relevance-test",
        "question": question,
        "seeds": list(seeds),
        "state_view": state_view,
    }
    if temporal is not None:
        raw["temporal"] = temporal
    return CompileRequest.from_mapping(raw)


def _current_temporal_query() -> dict[str, str]:
    return {
        "view": "current",
        "request_time": "2026-08-10T00:00:00Z",
        "world_at": "2026-08-10",
        "known_at": "2026-08-10T00:00:00Z",
    }


def _revision(page: str, predicate: str, value: str, *, label: str):
    candidate_digest = hashlib.sha256(f"candidate:{label}".encode()).hexdigest()
    observation_digest = hashlib.sha256(f"observation:{label}".encode()).hexdigest()
    return build_temporal_claim_revision(
        subject={"kind": "resolved_page", "page": page},
        predicate=predicate,
        object_ref={"kind": "literal", "datatype": "type:text", "value": value},
        world_validity={
            "from": {"kind": "known", "value": "2025-01-01"},
            "to": {"kind": "open"},
        },
        recorded_at="2025-01-02T00:00:00Z",
        candidate_ids=[f"temporal-candidate:sha256:{candidate_digest}"],
        observation_ids=[f"temporal-observation:sha256:{observation_digest}"],
        steward_evidence_refs=[f"sources/{Path(page).stem}.md"],
    )


class QuestionRelevanceTest(unittest.TestCase):
    def test_temporal_claim_cannot_borrow_relevance_from_its_subject_page(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = create_wiki_root(Path(tmpdir) / "wiki", with_scripts=False)
            (root / ".llm-wiki.toml").write_text(
                'schema_version = "1"\nruntime_contract = "2"\n'
                '[compiler]\nproviders = ["seed"]\ngraph_backend = "legacy"\n',
                encoding="utf-8",
            )
            revision = _revision("api.md", "billing:price", "ten credits", label="api-price")
            write_md(
                root / "api.md",
                base_fm(title="API", source="sources/api.md", knowledge_state="current",
                        temporal_claim_revisions=[revision.to_dict()]),
                "The API page contains several independent claims.",
            )
            response = compile_context(
                root, _request("What is the current API status?", temporal=_current_temporal_query())
            ).to_dict()
        self.assertEqual(response["evidence"], [])
        self.assertFalse(response["stop"]["sufficient"])

    def test_temporal_validity_alone_cannot_make_an_unrelated_claim_sufficient(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = create_wiki_root(Path(tmpdir) / "wiki", with_scripts=False)
            _configure_compiler(root)
            unrelated = _revision(
                "release.md", "release:has_state", "shipped", label="unrelated-release"
            )
            write_md(
                root / "release.md",
                base_fm(
                    title="Unrelated Release",
                    tags=["release"],
                    source="sources/release.md",
                    knowledge_state="current",
                    temporal_claim_revisions=[unrelated.to_dict()],
                ),
                "# Unrelated Release\n\nThe unrelated product shipped.",
            )
            _write_source(root, "sources/release.md", "The unrelated product shipped.")

            response = compile_context(
                root,
                _request(
                    "What is the current API status?",
                    temporal=_current_temporal_query(),
                ),
            ).to_dict()

        self.assertFalse(response["evidence"])
        self.assertFalse(response["stop"]["sufficient"])
        self.assertTrue(
            {"answer", "authority", "current_claim"}.issubset(
                set(response["coverage"]["uncovered_roles"])
            )
        )

    def test_relevant_claim_and_source_can_cover_answer_and_authority(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = create_wiki_root(Path(tmpdir) / "wiki", with_scripts=False)
            _configure_compiler(root)
            write_md(
                root / "status.md",
                base_fm(
                    title="API Status",
                    tags=["api", "status"],
                    source="sources/status.md",
                    knowledge_state="current",
                ),
                "# API Status\n\nThe API status is blocked.",
            )
            _write_source(
                root,
                "sources/status.md",
                "The API status is blocked according to the status monitor.",
            )

            response = compile_context(
                root, _request("What is the current API status?")
            ).to_dict()

        self.assertTrue(
            any(item["page"] == "status.md" and item["provider"] != "source"
                for item in response["evidence"])
        )
        self.assertTrue(
            any(
                item["provider"] == "source"
                and item["source"] == "sources/status.md"
                for item in response["evidence"]
            )
        )
        self.assertFalse(response["coverage"]["uncovered_roles"])
        self.assertTrue(response["stop"]["sufficient"])

    def test_generic_conversation_and_title_substrings_do_not_count_as_relevance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = create_wiki_root(Path(tmpdir) / "wiki", with_scripts=False)
            _configure_compiler(root)
            write_md(
                root / "status.md",
                base_fm(
                    title="Status",
                    tags=["deployment"],
                    source="sources/status.md",
                    knowledge_state="current",
                ),
                "# Status\n\nA status label is used for deployment dashboards; operators can continue after review.",
            )
            _write_source(
                root,
                "sources/status.md",
                "Status labels describe deployment dashboards. Operators can continue after review.",
            )

            generic = compile_context(root, _request("continue")).to_dict()
            substring = compile_context(root, _request("What is statusbook?")).to_dict()

        self.assertFalse(generic["evidence"])
        self.assertFalse(generic["stop"]["sufficient"])
        self.assertTrue(
            {"claim", "support", "authority"}.issubset(
                set(generic["coverage"]["uncovered_roles"])
            )
        )
        self.assertFalse(substring["evidence"])
        self.assertFalse(substring["stop"]["sufficient"])
        self.assertTrue(
            {"answer", "authority"}.issubset(
                set(substring["coverage"]["uncovered_roles"])
            )
        )

    def test_unrelated_source_cannot_fill_authority_but_remains_useful_for_its_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = create_wiki_root(Path(tmpdir) / "wiki", with_scripts=False)
            _configure_compiler(root)
            write_md(
                root / "status.md",
                base_fm(
                    title="API Status",
                    tags=["api", "status"],
                    knowledge_state="current",
                ),
                "# API Status\n\nThe API status is blocked.",
            )
            write_md(
                root / "skills.md",
                base_fm(
                    title="Agent Skills Selection",
                    tags=["skills", "selection"],
                    source="sources/skills.md",
                    knowledge_state="historical",
                ),
                "# Agent Skills Selection\n\nThe current status phrase is mentioned only as an unrelated example.\n\nSource grounding was added to the agent skills selection page in August 2026.",
            )
            _write_source(
                root,
                "sources/skills.md",
                "The current status phrase is mentioned only as an unrelated example. Source grounding was added to the agent skills selection page in August 2026.",
            )

            unrelated = compile_context(
                root, _request("What is the current API status?")
            ).to_dict()
            historical = compile_context(
                root,
                _request(
                    "Why was source grounding added to the agent skills selection page in August 2026?",
                    state_view="all",
                ),
            ).to_dict()

        self.assertTrue(
            any(item["page"] == "status.md" for item in unrelated["evidence"])
        )
        self.assertFalse(
            any(item.get("source") == "sources/skills.md" for item in unrelated["evidence"])
        )
        self.assertFalse(unrelated["stop"]["sufficient"])
        self.assertIn("authority", unrelated["coverage"]["uncovered_roles"])

        self.assertTrue(
            any(item["page"] == "skills.md" for item in historical["evidence"])
        )
        self.assertTrue(
            any(item.get("source") == "sources/skills.md" for item in historical["evidence"])
        )
        self.assertFalse(historical["coverage"]["uncovered_roles"])
        self.assertTrue(historical["stop"]["sufficient"])

    def test_explicit_seed_remains_selectable_for_an_unrelated_question(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = create_wiki_root(Path(tmpdir) / "wiki", with_scripts=False)
            _configure_compiler(root)
            write_md(
                root / "release.md",
                base_fm(
                    title="Release Notes",
                    type="policy",
                    tags=["release"],
                    source="sources/release.md",
                    knowledge_state="current",
                ),
                "# Release Notes\n\nThe unrelated product shipped.",
            )
            _write_source(root, "sources/release.md", "The unrelated product shipped.")

            response = compile_context(
                root,
                _request(
                    "What is the current API status?",
                    seeds=("release.md",),
                ),
            ).to_dict()

        self.assertTrue(any(item["provider"] == "seed" for item in response["evidence"]))
        self.assertFalse(response["coverage"]["uncovered_roles"])
        self.assertTrue(response["stop"]["sufficient"])

    def test_source_can_answer_when_linked_page_summary_has_no_query_overlap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = create_wiki_root(Path(tmpdir) / "wiki", with_scripts=False)
            _configure_compiler(root)
            write_md(
                root / "status.md",
                base_fm(
                    title="Status",
                    tags=["operations"],
                    source="sources/status.md",
                    knowledge_state="current",
                ),
                "# Status\n\nOperators review routine dashboard labels.",
            )
            _write_source(
                root,
                "sources/status.md",
                "Rollback policy: restore the previous release when the deployment health check fails.",
            )

            response = compile_context(
                root, _request("What is the rollback policy?")
            ).to_dict()

        self.assertEqual(
            [item["provider"] for item in response["evidence"]],
            ["source"],
        )
        self.assertEqual(response["evidence"][0]["source"], "sources/status.md")
        self.assertIn("Rollback policy", response["evidence"][0]["content"])
        self.assertFalse(response["coverage"]["uncovered_roles"])
        self.assertTrue(response["stop"]["sufficient"])

    def test_shared_source_supports_selected_page_after_an_unrelated_first_reference(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = create_wiki_root(Path(tmpdir) / "wiki", with_scripts=False)
            _configure_compiler(root)
            write_md(
                root / "a-unrelated.md",
                base_fm(
                    title="Operations Guide",
                    tags=["operations"],
                    source="sources/shared.md",
                    knowledge_state="current",
                ),
                "# Operations Guide\n\nRoutine deployment notes.",
            )
            write_md(
                root / "b-status.md",
                base_fm(
                    title="API Status",
                    tags=["api", "status"],
                    source="sources/shared.md",
                    knowledge_state="current",
                ),
                "# API Status\n\nThe API status is blocked.",
            )
            _write_source(
                root,
                "sources/shared.md",
                "The current API status is blocked according to the status monitor.",
            )

            response = compile_context(
                root, _request("What is the current API status?")
            ).to_dict()

        source = next(item for item in response["evidence"] if item["provider"] == "source")
        self.assertEqual(source["page"], "a-unrelated.md")
        self.assertIn("source_for:a-unrelated.md", source["selection_reasons"])
        self.assertIn("source_for:b-status.md", source["selection_reasons"])
        self.assertTrue(
            any(
                item["page"] == "b-status.md" and item["provider"] != "source"
                for item in response["evidence"]
            )
        )
        self.assertFalse(response["coverage"]["uncovered_roles"])
        self.assertTrue(response["stop"]["sufficient"])

    def test_evidence_source_is_selected_when_primary_source_is_unrelated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = create_wiki_root(Path(tmpdir) / "wiki", with_scripts=False)
            _configure_compiler(root)
            write_md(
                root / "operations.md",
                base_fm(
                    title="Operations Guide",
                    tags=["operations"],
                    source="sources/primary.md",
                    evidence=["sources/status-evidence.md"],
                    knowledge_state="current",
                ),
                "# Operations Guide\n\nRoutine deployment notes.",
            )
            _write_source(root, "sources/primary.md", "Unrelated deployment history.")
            _write_source(
                root,
                "sources/status-evidence.md",
                "The API condition is blocked according to the status monitor.",
            )

            response = compile_context(
                root, _request("What is the API condition?")
            ).to_dict()

        sources = [item for item in response["evidence"] if item["provider"] == "source"]
        self.assertEqual([item["source"] for item in sources], ["sources/status-evidence.md"])
        self.assertIn("The API condition is blocked", sources[0]["content"])
        self.assertFalse(response["coverage"]["uncovered_roles"])
        self.assertTrue(response["stop"]["sufficient"])

    def test_title_alias_seed_preserves_its_source_without_question_overlap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = create_wiki_root(Path(tmpdir) / "wiki", with_scripts=False)
            _configure_compiler(root)
            write_md(
                root / "release.md",
                base_fm(
                    title="Release Notes",
                    tags=["release"],
                    source="sources/release.md",
                ),
                "# Release Notes\n\nThe unrelated product shipped.",
            )
            _write_source(root, "sources/release.md", "The unrelated product shipped.")

            response = compile_context(
                root,
                _request(
                    "What is the API condition?",
                    seeds=("Release Notes",),
                ),
            ).to_dict()

        self.assertEqual(response["query"]["resolved_seeds"], ["release.md"])
        source = next(item for item in response["evidence"] if item["provider"] == "source")
        self.assertEqual(source["source"], "sources/release.md")
        self.assertIn("explicit_seed_discovery", source["selection_reasons"])
        self.assertFalse(response["coverage"]["uncovered_roles"])
        self.assertTrue(response["stop"]["sufficient"])


if __name__ == "__main__":
    unittest.main()
