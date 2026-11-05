"""Temporal authority must survive ordinary page and source coexistence."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.compiler import compile_context
from llm_wiki_core.contracts import CompileRequest, ContractError
from llm_wiki_core.temporal_persistence import build_temporal_claim_revision
from tests.wiki_fixture import base_fm, create_wiki_root, write_md


NOW = "2026-09-06T12:00:00Z"


def revision(value, *, page="mode.md", predicate="deploy:mode", recorded="2026-01-02T00:00:00Z",
             start="2026-01-01", end=None, decision="accept", sources=("sources/mode.md",), **relations):
    return build_temporal_claim_revision(
        subject={"kind": "resolved_page", "page": page}, predicate=predicate,
        object_ref={"kind": "literal", "datatype": "type:text", "value": value},
        world_validity={
            "from": {"kind": "known", "value": start} if start else {"kind": "unknown", "reason": "No supported start date"},
            "to": {"kind": "known", "value": end} if end else {"kind": "open"},
        },
        recorded_at=recorded, candidate_ids=["temporal-candidate:sha256:" + "a" * 64],
        observation_ids=["temporal-observation:sha256:" + "b" * 64],
        steward_evidence_refs=sources, decision=decision, **relations,
    )


class TemporalContextTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = create_wiki_root(Path(self.directory.name) / "wiki", with_scripts=False)
        (self.root / ".llm-wiki.toml").write_text(
            'schema_version = "1"\nruntime_contract = "2"\n'
            '[compiler]\nproviders = ["seed", "frontmatter", "text", "source"]\n'
            'graph_backend = "legacy"\n', encoding="utf-8",
        )

    def page(self, revisions=None, *, path="mode.md", body="Deploy mode is blue; use blue for every deployment.",
             state="current", source="sources/mode.md", evidence=()):
        fm = base_fm(title=Path(path).stem.title(), type="concept", source=source, evidence=list(evidence))
        if state is not None:
            fm["knowledge_state"] = state
        if revisions is not None:
            fm["temporal_claim_revisions"] = [r.to_dict() if hasattr(r, "to_dict") else r for r in revisions]
        write_md(self.root / path, fm, body)
        return fm

    def source(self, path="sources/mode.md", text="Deploy mode was blue in the archived deployment decision."):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def compile(self, question="What is the current deploy mode?", *, view="current", seeds=(), **temporal):
        budget = temporal.pop("budget", {})
        return compile_context(self.root, CompileRequest.from_mapping({
            "contract_version": "3", "alias": "temporal-test", "question": question,
            "seeds": list(seeds), "state_view": "current",
            "temporal": {"view": view, "request_time": NOW, **temporal},
            "budget": {"target_bytes": 12000, "max_bytes": 24000, "target_items": 12, "max_items": 24, **budget},
        })).to_dict()

    def test_current_replacement_precedes_and_qualifies_old_prose(self):
        old = revision("Deploy mode is blue", end="2026-08-01")
        new = revision("Deploy mode is green", start="2026-08-01", recorded="2026-08-02T00:00:00Z",
                       decision="supersede", supersedes_revision_ids=[old.revision_id], sources=("sources/green.md",))
        self.page([old, new], evidence=["sources/green.md"])
        self.source()
        self.source("sources/green.md", "Deploy mode is green following the accepted mode decision.")
        response = self.compile(seeds=["mode.md"])
        self.assertEqual(response["evidence"][0]["provider"], "temporal")
        facts = [json.loads(e["content"]) for e in response["evidence"] if e["provider"] == "temporal"]
        self.assertEqual([f["revision_id"] for f in facts], [new.revision_id])
        self.assertEqual(facts[0]["status"], "settled")
        for item in response["evidence"]:
            if item["provider"] not in {"temporal", "source"}:
                self.assertNotIn("current_claim", item["roles"])
                self.assertNotIn("authority", item["roles"])
                self.assertIn("Temporal context:", item["content"])
        stale_source = next(e for e in response["evidence"] if e["source"] == "sources/mode.md" and e["provider"] == "source")
        self.assertNotIn("authority", stale_source["roles"])

    def test_query_precision_matches_existing_whole_second_reader(self):
        # Host clocks contain fractions. Reject unsupported explicit precision
        # at the query seam, not as an invalid authored history during reading.
        for field in ("request_time", "known_at", "world_at"):
            temporal = {"view": "current", "request_time": NOW,
                        field: "2026-09-06T12:00:00.123456Z"}
            with self.subTest(field=field), self.assertRaises(ContractError):
                CompileRequest.from_mapping({"alias": "test", "contract_version": "3",
                                             "question": "deploy mode", "temporal": temporal})
        with self.assertRaises(ContractError):
            CompileRequest.from_mapping({"alias": "test", "contract_version": "3",
                "question": "deploy mode", "temporal": {"view": "transition", "request_time": NOW,
                "transition": {"from": "2026-09-01T12:00:00.000001Z", "to": NOW}}})

    def test_older_source_still_supports_the_selected_current_claim(self):
        accepted = revision("Deploy mode is green", sources=("sources/decision-2020.md",))
        self.page([accepted], source="sources/decision-2020.md")
        self.source("sources/decision-2020.md", "Deploy mode is green because the old decision favoured reversible releases.")
        response = self.compile()
        source = next(e for e in response["evidence"] if e["provider"] == "source")
        self.assertIn("authority", source["roles"])
        self.assertIn("Temporal context:", source["content"])
        self.assertIn("sources/decision-2020.md", next(e for e in response["evidence"] if e["provider"] == "temporal")["locator"]["steward_evidence_refs"])

    def test_temporal_authority_does_not_outrank_stronger_question_relevance(self):
        self.page([revision("Deploy mode has operational history")])
        self.page(path="browser.md", body="Browser UI language starts with TypeScript; evaluate this starting candidate first.",
                  source="sources/browser.md")
        self.source()
        self.source("sources/browser.md", "Browser UI language starts with TypeScript.")
        response = self.compile("For browser UI language, what starting candidate should be evaluated first in deploy mode?")
        self.assertEqual(response["evidence"][0]["page"], "browser.md")

    def test_current_conflict_is_visible_without_a_settled_winner(self):
        old = revision("Deploy mode is blue")
        contested = revision("Deploy mode is green", decision="contradict", recorded="2026-08-02T00:00:00Z",
                             contradicts_revision_ids=[old.revision_id])
        self.page([old, contested])
        self.source()
        response = self.compile()
        self.assertIn("TEMPORAL_CONFLICT", [d["code"] for d in response["diagnostics"]])
        self.assertFalse(any(e["provider"] == "temporal" for e in response["evidence"]))
        self.assertFalse(any("current_claim" in e["roles"] or "authority" in e["roles"] for e in response["evidence"]))
        self.assertFalse(response["stop"]["sufficient"])
        conflict = self.compile(view="conflict")
        self.assertEqual(len([e for e in conflict["evidence"] if e["provider"] == "temporal"]), 2)
        for item in conflict["evidence"]:
            if item["provider"] == "temporal":
                self.assertNotIn("current_claim", item["roles"])
                self.assertNotIn("authority", item["roles"])
                self.assertEqual(json.loads(item["content"])["status"], "contested")

    def test_unknown_world_time_and_unknown_legacy_state_are_explicit(self):
        self.page([revision("Deploy mode is blue", start=None)])
        self.page(path="runbook.md", state=None, body="Deploy mode can be inspected in the runbook.", source="sources/runbook.md")
        self.source()
        self.source("sources/runbook.md", "Deploy mode inspection notes.")
        response = self.compile()
        self.assertIn("TEMPORAL_STATE_UNCERTAIN", [d["code"] for d in response["diagnostics"]])
        self.assertFalse(any("current_claim" in e["roles"] or "authority" in e["roles"] for e in response["evidence"]))
        self.assertTrue(all("Temporal context:" in e["content"] for e in response["evidence"]))

    def test_invalid_history_has_no_prose_or_source_authority_fallback(self):
        bad = revision("Deploy mode is blue").to_dict()
        bad["revision_id"] = "temporal-revision:sha256:" + "0" * 64
        self.page([bad])
        self.source()
        self.page([revision("Release audit is verified", page="audit.md", predicate="release:audit", sources=("sources/audit.md",))],
                  path="audit.md", body="Release audit is verified.", source="sources/audit.md")
        self.source("sources/audit.md", "Release audit verification evidence.")
        response = self.compile()
        self.assertIn("TEMPORAL_HISTORY_INVALID", [d["code"] for d in response["diagnostics"]])
        self.assertFalse(any(e["page"] == "mode.md" for e in response["evidence"]))
        unaffected = self.compile("What is the current release audit?")
        self.assertTrue(any(e["provider"] == "temporal" and e["page"] == "audit.md" for e in unaffected["evidence"]))
        self.assertNotIn("TEMPORAL_HISTORY_INVALID", [d["code"] for d in unaffected["diagnostics"]])

    def test_known_time_excludes_late_facts_and_their_prose_source_bypass(self):
        late = revision("Deploy mode is green", recorded="2026-10-02T00:00:00Z")
        self.page([late], body="Deploy mode is green.")
        self.source(text="Deploy mode is green.")
        response = self.compile()
        self.assertFalse(any("green" in e["content"] for e in response["evidence"]))
        self.assertIn("TEMPORAL_STATE_UNCERTAIN", [d["code"] for d in response["diagnostics"]])

    def test_historical_and_lineage_queries_do_not_need_state_view_rewriting(self):
        old = revision("Deploy mode is blue", end="2026-08-01")
        new = revision("Deploy mode is green", start="2026-08-01", recorded="2026-08-02T00:00:00Z",
                       decision="supersede", supersedes_revision_ids=[old.revision_id])
        self.page([old, new])
        self.source()
        historical = self.compile("What was the deploy mode?", view="historical", world_at="2026-07-01")
        facts = [e for e in historical["evidence"] if e["provider"] == "temporal"]
        self.assertEqual(len(facts), 1)
        self.assertEqual(json.loads(facts[0]["content"])["revision_id"], old.revision_id)
        self.assertEqual(facts[0]["authored_state"], "historical")
        self.assertNotIn("current_claim", facts[0]["roles"])
        lineage = self.compile("Why did the deploy mode change?", view="lineage")
        self.assertEqual(len([e for e in lineage["evidence"] if e["provider"] == "temporal"]), 2)

    def test_temporal_warning_survives_a_tight_response_or_fails_explicitly(self):
        self.page([revision("Deploy mode is blue", start=None)], body="Deploy mode background. " * 300)
        self.source(text="Deploy mode archival background. " * 300)
        for limit in (1400, 2600, 4200):
            with self.subTest(limit=limit):
                try:
                    response = self.compile(budget={"target_bytes": limit, "max_bytes": limit})
                except ContractError as error:
                    self.assertEqual(error.code, "BUDGET_TOO_SMALL")
                else:
                    self.assertIn("TEMPORAL_STATE_UNCERTAIN", [d["code"] for d in response["diagnostics"]])
                    self.assertLessEqual(len(json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode()), limit)

    def test_provenance_annotation_preserves_revision_identity_and_known_time_authority(self):
        accepted = revision("Deploy mode is blue")
        frontmatter = self.page([accepted])
        frontmatter.update({
            "status": "stable",
            "generated": {"by": "writer/v2", "at": "2026-01-02T00:00:00Z"},
            "verified": {"by": "human:vik", "at": "2026-10-01T00:00:00Z"},
        })
        write_md(self.root / "mode.md", frontmatter, "Deploy mode is blue.")
        before = (self.root / "mode.md").read_bytes()
        self.source()

        response = self.compile()

        claim = next(item for item in response["evidence"] if item["provider"] == "temporal")
        self.assertEqual(json.loads(claim["content"])["revision_id"], accepted.revision_id)
        self.assertEqual(claim["authored_state"], "current")
        self.assertIn("authority", claim["roles"])
        self.assertEqual(claim["metadata"]["trust"], "unverified")
        self.assertIn("verification_after_known_at", claim["derived_flags"])
        self.assertEqual((self.root / "mode.md").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
