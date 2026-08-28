from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import textwrap
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from llm_wiki_core.config import (
    CompilerConfig,
    ContentConfig,
    StateConfig,
    StewardshipConfig,
    WikiConfig,
)
from llm_wiki_core.compiler import compile_context
from llm_wiki_core.contracts import CompileRequest
from llm_wiki_core.documents import collect_pages
from llm_wiki_core.providers.base import ProviderContext
from llm_wiki_core.providers.loci_graph import LociGraphProvider
from llm_wiki_core.providers.loci_graph import LociGraphMcpGateway
from llm_wiki_core.providers.loci_graph import _retrieve_arguments
from llm_wiki_core.providers.loci_transport import LociGatewayError, LociMcpClient
from llm_wiki_core.query_shape import classify_question, required_roles
from tests.wiki_fixture import base_fm, write_md


class FakeGraphGateway:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = []

    def retrieve(self, context):
        self.calls.append(context)
        if self.error is not None:
            raise self.error
        return self.response


class LociGraphProviderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._runtime_tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.runtime_root = Path(self._runtime_tmp.name)
        write_md(
            self.root / "projects" / "brain.md",
            base_fm(title="Brain", knowledge_state="current"),
            "## Runtime\n\nBrain uses the [shared runtime](../concepts/runtime.md).",
        )
        write_md(
            self.root / "concepts" / "runtime.md",
            base_fm(title="Shared Runtime", knowledge_state="current"),
            "## Ownership\n\nThe runtime is owned by [llm-wiki](../systems/llm-wiki.md).",
        )
        write_md(
            self.root / "systems" / "llm-wiki.md",
            base_fm(title="llm-wiki", knowledge_state="current"),
            "## Role\n\nCanonical traversal implementation.",
        )

    def tearDown(self):
        self._tmp.cleanup()
        self._runtime_tmp.cleanup()

    def context(self) -> ProviderContext:
        request = CompileRequest.from_mapping(
            {
                "alias": "test",
                "question": "How does Brain connect to llm-wiki?",
                "seeds": ["projects/brain.md", "systems/llm-wiki.md"],
            }
        )
        pages = collect_pages(self.root)
        shapes = classify_question(request.question)
        config = WikiConfig(
            schema_version="1",
            runtime_contract="2",
            profile="default",
            content=ContentConfig(),
            compiler=CompilerConfig(),
            state=StateConfig(),
            stewardship=StewardshipConfig(),
            raw={},
        )
        return ProviderContext(
            self.root,
            config,
            request,
            pages,
            shapes,
            required_roles(shapes),
            request.seeds,
        )

    def response(self) -> dict:
        context = self.context()
        first_file = "projects/brain.md"
        second_file = "concepts/runtime.md"
        third_file = "systems/llm-wiki.md"
        first_line, first_content = self._line_containing(first_file, "shared runtime")
        second_line, second_content = self._line_containing(second_file, "owned by")
        first_id = f"{first_file}::Brain#section"
        second_id = f"{second_file}::Shared Runtime#section"
        third_id = f"{third_file}::llm-wiki#section"
        return {
            "schema_version": 1,
            "selection": "explicit",
            "paths": [
                {
                    "support_kind": "semantic_bridge",
                    "semantic_bridge": {
                        "required": True,
                        "required_terms": ["connect"],
                        "matched_terms": ["connect"],
                    },
                    "retrieval_score": 8.25,
                    "score_components": {"question_overlap": 2.0},
                    "nodes": [
                        self._node(first_id, first_file),
                        self._node(second_id, second_file),
                        self._node(third_id, third_file),
                    ],
                    "steps": [
                        self._step(
                            first_id,
                            second_id,
                            first_file,
                            first_line,
                            first_content,
                            context.pages[first_file].text,
                        ),
                        self._step(
                            second_id,
                            third_id,
                            second_file,
                            second_line,
                            second_content,
                            context.pages[second_file].text,
                        ),
                    ],
                }
            ],
            "rejected_paths": [],
            "diagnostics": [],
            "budget": {"evidence_bytes": len(first_content.encode()) + len(second_content.encode())},
        }

    def inferred_context(self) -> ProviderContext:
        context = self.context()
        request = replace(context.request, seeds=())
        return replace(context, request=request, resolved_seeds=())

    def inferred_response(self, *, crosses_subjects: bool) -> dict:
        response = self.response()
        response["selection"] = "inferred"
        path = response["paths"][0]
        response["anchors"] = [
            {
                "node": path["nodes"][0],
                "reason": {"kind": "inferred", "matched_terms": ["brain"]},
            },
            {
                "node": path["nodes"][2],
                "reason": {"kind": "inferred", "matched_terms": ["llm", "wiki"]},
            },
        ]
        if not crosses_subjects:
            path["support_kind"] = "direct_authored_edge"
            path["semantic_bridge"] = {
                "required": False,
                "required_terms": ["connect"],
                "matched_terms": [],
            }
            path["nodes"] = path["nodes"][:2]
            path["steps"] = path["steps"][:1]
        return response

    def test_selected_loci_path_becomes_atomic_bridge_candidate(self):
        gateway = FakeGraphGateway(self.response())
        provider = LociGraphProvider(gateway=gateway)

        result = provider.collect(self.context())

        path_candidates = [
            item for item in result.candidates if item.route == "evidence_backed_path"
        ]
        self.assertEqual(len(path_candidates), 1)
        candidate = path_candidates[0]
        self.assertEqual(candidate.provider, "graph")
        self.assertEqual(candidate.route, "evidence_backed_path")
        self.assertEqual(candidate.roles, ("bridge",))
        self.assertTrue(candidate.atomic)
        self.assertEqual(candidate.retrieval_rank, 0)
        self.assertEqual(candidate.authored_state, "current")
        self.assertEqual(candidate.authority_signals, ())
        self.assertEqual(candidate.locator["support_kind"], "semantic_bridge")
        self.assertNotIn("content", candidate.locator["steps"][0]["evidence"])
        self.assertIn("Brain uses the [shared runtime]", candidate.content)
        self.assertIn("owned by [llm-wiki]", candidate.content)

        node_candidates = [
            item for item in result.candidates if item.route == "path_node_section"
        ]
        self.assertTrue(node_candidates)
        self.assertTrue(
            any("Brain uses the [shared runtime]" in item.content for item in node_candidates)
        )
        self.assertTrue(all(item.content.startswith("description: A test page.") for item in node_candidates))
        self.assertTrue(all("loci_path_node" in item.selection_signals for item in node_candidates))
        self.assertTrue(all(item.authority_signals == () for item in node_candidates))

    def test_inferred_path_crossing_distinct_subject_anchors_is_bridge_evidence(self):
        provider = LociGraphProvider(
            gateway=FakeGraphGateway(self.inferred_response(crosses_subjects=True))
        )

        result = provider.collect(self.inferred_context())

        candidate = next(
            item for item in result.candidates if item.route == "evidence_backed_path"
        )
        self.assertEqual(candidate.roles, ("bridge",))
        self.assertIn("relationship_claim_bridge", candidate.selection_signals)

    def test_inferred_path_within_one_subject_cluster_cannot_satisfy_bridge_role(self):
        provider = LociGraphProvider(
            gateway=FakeGraphGateway(self.inferred_response(crosses_subjects=False))
        )

        result = provider.collect(self.inferred_context())

        candidate = next(
            item for item in result.candidates if item.route == "evidence_backed_path"
        )
        self.assertEqual(candidate.roles, ("support",))
        self.assertIn("relationship_ancillary_path", candidate.selection_signals)

    def test_inferred_paths_without_question_anchors_are_rejected(self):
        response = self.inferred_response(crosses_subjects=True)
        del response["anchors"]
        provider = LociGraphProvider(gateway=FakeGraphGateway(response))

        result = provider.collect(self.inferred_context())

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.diagnostics[0].code, "LOCI_GRAPH_RESULT_INVALID")

    def test_scope_only_explicit_response_is_ancillary_not_relationship_bridge(self):
        base_context = self.context()
        context = replace(
            base_context,
            request=replace(base_context.request, question="How do pieces interoperate?", seeds=()),
            resolved_seeds=(),
            scope_seeds=("projects/brain.md",),
        )
        provider = LociGraphProvider(gateway=FakeGraphGateway(self.response()))

        result = provider.collect(context)

        candidate = next(
            item for item in result.candidates if item.route == "evidence_backed_path"
        )
        self.assertEqual(candidate.roles, ("support",))
        self.assertEqual(candidate.locator["relationship_support"], "ancillary_path")
        self.assertIn("relationship_ancillary_path", candidate.selection_signals)

    def test_inferred_anchor_without_matched_question_terms_is_rejected(self):
        response = self.inferred_response(crosses_subjects=True)
        response["anchors"][0]["reason"]["matched_terms"] = []
        provider = LociGraphProvider(gateway=FakeGraphGateway(response))

        result = provider.collect(self.inferred_context())

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.diagnostics[0].code, "LOCI_GRAPH_RESULT_INVALID")

    def test_compiler_does_not_treat_ancillary_path_as_relationship_sufficiency(self):
        self._write_compiler_config()
        request = self.inferred_context().request
        graph_response = self.inferred_response(crosses_subjects=False)
        graph_response["rejected_paths"] = [
            {
                "nodes": [
                    graph_response["anchors"][0]["node"]["id"],
                    graph_response["anchors"][1]["node"]["id"],
                ],
                "reason": "HUB_SHORTCUT",
            }
        ]

        with patch(
            "llm_wiki_core.providers.loci_graph.LociGraphMcpGateway.retrieve",
            return_value=graph_response,
        ):
            response = compile_context(self.root, request).to_dict()

        self.assertFalse(response["stop"]["sufficient"])
        self.assertEqual(response["stop"]["reason"], "candidate_exhausted")
        self.assertIn("bridge", response["coverage"]["uncovered_roles"])
        self.assertTrue(
            any(item["code"] == "LOCI_GRAPH_PATH_REJECTED" for item in response["diagnostics"])
        )
        ancillary = next(
            item
            for item in response["evidence"]
            if item["provider"] == "graph" and item["route"] == "evidence_backed_path"
        )
        self.assertEqual(ancillary["roles"], ["support"])

    def test_rejected_loci_path_remains_inspectable_without_becoming_evidence(self):
        response = self.response()
        response["paths"] = []
        response["rejected_paths"] = [
            {
                "nodes": ["projects/brain.md::Brain#section", "systems/llm-wiki.md::llm-wiki#section"],
                "reason": "HUB_SHORTCUT",
            }
        ]
        provider = LociGraphProvider(gateway=FakeGraphGateway(response))

        result = provider.collect(self.context())

        self.assertEqual(result.candidates, ())
        diagnostic = next(item for item in result.diagnostics if item.code == "LOCI_GRAPH_PATH_REJECTED")
        self.assertEqual(diagnostic.provider, "graph")
        self.assertEqual(diagnostic.details["reason"], "HUB_SHORTCUT")

    def test_cached_evidence_must_match_the_original_wiki_snapshot(self):
        response = self.response()
        response["paths"][0]["steps"][0]["evidence_span"]["content"] = "tampered cache content"
        provider = LociGraphProvider(gateway=FakeGraphGateway(response))

        result = provider.collect(self.context())

        self.assertEqual(result.candidates, ())
        self.assertTrue(any(item.code == "LOCI_GRAPH_RESULT_INVALID" for item in result.diagnostics))

    def test_gateway_failure_is_explicit_and_does_not_return_graph_evidence(self):
        provider = LociGraphProvider(
            gateway=FakeGraphGateway(
                error=LociGatewayError(
                    "LOCI_MCP_FAILED",
                    "loci failed",
                    {"transport": "mcp_stdio"},
                )
            )
        )

        result = provider.collect(self.context())

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.diagnostics[0].code, "LOCI_GRAPH_MCP_FAILED")
        self.assertEqual(result.diagnostics[0].provider, "graph")

    def test_compiler_uses_loci_graph_backend_by_default(self):
        self._write_compiler_config()
        request = self.context().request

        with patch(
            "llm_wiki_core.providers.loci_graph.LociGraphMcpGateway.retrieve",
            return_value=self.response(),
        ):
            response = compile_context(self.root, request).to_dict()

        graph_evidence = [item for item in response["evidence"] if item["provider"] == "graph"]
        self.assertEqual(
            [item["route"] for item in graph_evidence if item["route"] == "evidence_backed_path"],
            ["evidence_backed_path"],
        )
        self.assertTrue(any(item["route"] == "path_node_section" for item in graph_evidence))
        self.assertTrue(response["stop"]["sufficient"])

    def test_legacy_backend_is_an_explicit_compiler_rollback(self):
        self._write_compiler_config(graph_backend="legacy")
        request = self.context().request

        with patch(
            "llm_wiki_core.providers.loci_graph.LociGraphMcpGateway.retrieve",
            side_effect=AssertionError("loci graph backend must not run"),
        ):
            response = compile_context(self.root, request).to_dict()

        graph_evidence = [item for item in response["evidence"] if item["provider"] == "graph"]
        self.assertTrue(graph_evidence)
        self.assertTrue(all(item["route"] == "connecting_path" for item in graph_evidence))

    def test_loci_failure_does_not_silently_fall_back_to_legacy_graph(self):
        self._write_compiler_config()
        request = self.context().request

        with patch(
            "llm_wiki_core.providers.loci_graph.LociGraphMcpGateway.retrieve",
            side_effect=LociGatewayError("LOCI_MCP_FAILED", "loci failed"),
        ):
            response = compile_context(self.root, request).to_dict()

        self.assertFalse(any(item["provider"] == "graph" for item in response["evidence"]))
        self.assertFalse(response["stop"]["sufficient"])
        self.assertTrue(
            any(item["code"] == "LOCI_GRAPH_MCP_FAILED" for item in response["diagnostics"])
        )

    def test_unseeded_retrieval_omits_seed_ids_to_enable_loci_inference(self):
        context = replace(self.context(), resolved_seeds=())

        arguments = _retrieve_arguments(
            context,
            self.runtime_root / "mirror",
            self._page_roots(context),
        )

        self.assertNotIn("seed_ids", arguments)

    def test_scope_seed_retrieval_joins_existing_loci_seed_argument(self):
        context = replace(
            self.context(),
            resolved_seeds=("systems/llm-wiki.md", "projects/brain.md"),
            scope_seeds=("projects/brain.md", "concepts/runtime.md"),
        )

        arguments = _retrieve_arguments(
            context,
            self.runtime_root / "mirror",
            self._page_roots(context),
        )

        self.assertEqual(
            arguments["seed_ids"],
            [
                "systems/llm-wiki.md::root#section",
                "projects/brain.md::root#section",
                "concepts/runtime.md::root#section",
            ],
        )
        self.assertNotIn("scope_seed_ids", arguments)

    def test_fresh_process_mcp_builds_external_graph_mirror_and_retrieves_path(self):
        server = self.runtime_root / "fake_loci_graph_mcp.py"
        calls = self.runtime_root / "fake-loci-calls.jsonl"
        server.write_text(
            textwrap.dedent(
                f"""\
                import json
                from collections import deque
                from pathlib import Path
                from mcp.server import MCPServer

                CALLS = Path({str(calls)!r})
                mcp = MCPServer("fake-loci-graph")

                def record(name, payload):
                    with CALLS.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps({{"name": name, "payload": payload}}, sort_keys=True) + "\\n")

                @mcp.tool()
                def loci_index(path: str, incremental: bool = True):
                    record("loci_index", {{"path": path, "incremental": incremental}})
                    return {{"path": path, "graph_status": "healthy", "graph_diagnostics": []}}

                @mcp.tool()
                def loci_outline(path: str, file: str | None = None):
                    record("loci_outline", {{"path": path}})
                    root = Path(path)
                    files = []
                    for source in sorted(root.rglob("*.md")):
                        relative = source.relative_to(root).as_posix()
                        files.append({{
                            "file": relative,
                            "symbols": [{{
                                "id": f"{{relative}}::root#section",
                                "line": 1,
                                "end_line": len(source.read_text(encoding="utf-8").splitlines()),
                                "span_kind": "page_root",
                            }}],
                        }})
                    return {{"files": files}}

                @mcp.tool()
                def loci_graph_retrieve(
                    repo: str,
                    question: str,
                    seed_ids: list[str] | None = None,
                    namespaces: list[str] | None = None,
                    edge_types: list[str] | None = None,
                    resolutions: list[str] | None = None,
                    direction: str = "either",
                    max_anchors: int = 10,
                    max_hops: int = 3,
                    max_nodes: int = 64,
                    max_paths: int = 8,
                    path_offset: int = 0,
                    max_evidence_bytes: int = 32768,
                    max_estimated_tokens: int = 8192,
                ):
                    record("loci_graph_retrieve", {{
                        "repo": repo,
                        "namespaces": namespaces,
                        "edge_types": edge_types,
                        "resolutions": resolutions,
                        "direction": direction,
                        "max_hops": max_hops,
                        "max_paths": max_paths,
                    }})
                    root = Path(repo)
                    edges = []
                    for path in sorted((root / ".loci/graph/contributions").glob("*.json")):
                        edges.extend(json.loads(path.read_text(encoding="utf-8"))["edges"])
                    source, target = seed_ids[0], seed_ids[1]
                    queue = deque([(source, [], [source])])
                    found = None
                    while queue:
                        node, steps, nodes = queue.popleft()
                        if node == target:
                            found = (steps, nodes)
                            break
                        if len(steps) >= max_hops:
                            continue
                        for edge in edges:
                            if edge["from"] == node:
                                neighbor, traversed = edge["to"], "forward"
                            elif edge["to"] == node:
                                neighbor, traversed = edge["from"], "reverse"
                            else:
                                continue
                            if neighbor not in nodes:
                                queue.append((neighbor, [*steps, (edge, traversed)], [*nodes, neighbor]))
                    steps, nodes = found
                    hydrated = []
                    for edge, traversed in steps:
                        evidence = edge["evidence"]
                        content = (root / evidence["file"]).read_text(encoding="utf-8").splitlines()[evidence["line"] - 1]
                        hydrated.append({{
                            "traversed": traversed,
                            "edge": edge,
                            "evidence_span": {{
                                "file": evidence["file"],
                                "start_line": evidence["line"],
                                "end_line": evidence["line"],
                                "content": content,
                            }},
                        }})
                    return {{
                        "schema_version": 1,
                        "selection": "explicit",
                        "paths": [{{
                            "support_kind": "semantic_bridge",
                            "semantic_bridge": {{
                                "required": True,
                                "required_terms": ["connect"],
                                "matched_terms": ["connect"],
                            }},
                            "retrieval_score": 9.0,
                            "score_components": {{"question_overlap": 2.0}},
                            "nodes": [{{
                                "id": node,
                                "namespace": "loci",
                                "kind": "section",
                                "attributes": {{
                                    "file": node.split("::", 1)[0],
                                    "line": 1,
                                    "end_line": 50,
                                }},
                            }} for node in nodes],
                            "steps": hydrated,
                        }}],
                        "rejected_paths": [],
                        "diagnostics": [],
                    }}

                if __name__ == "__main__":
                    mcp.run(transport="stdio")
                """
            ),
            encoding="utf-8",
        )
        cache = self.runtime_root / "graph-cache"
        client = LociMcpClient(
            command=sys.executable,
            args=(str(server),),
            timeout_seconds=5.0,
        )
        provider = LociGraphProvider(
            gateway=LociGraphMcpGateway(client=client, cache_dir=cache)
        )

        result = provider.collect(self.context())

        self.assertEqual(
            len([item for item in result.candidates if item.route == "evidence_backed_path"]),
            1,
            result.diagnostics,
        )
        self.assertTrue(any(item.route == "path_node_section" for item in result.candidates))
        self.assertFalse((self.root / ".loci").exists())
        call_records = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(
            [record["name"] for record in call_records],
            ["loci_index", "loci_outline", "loci_index", "loci_graph_retrieve"],
        )
        arguments = call_records[-1]["payload"]
        self.assertEqual(arguments["namespaces"], ["llm-wiki"])
        self.assertEqual(arguments["edge_types"], ["body_link", "mentioned_in"])
        self.assertEqual(arguments["resolutions"], ["declared"])
        self.assertEqual(arguments["max_hops"], 3)

    def _line_containing(self, file_path: str, needle: str) -> tuple[int, str]:
        lines = self.context().pages[file_path].text.splitlines()
        return next(
            (index, line)
            for index, line in enumerate(lines, start=1)
            if needle in line
        )

    @staticmethod
    def _page_roots(context: ProviderContext) -> dict[str, str]:
        return {
            path: f"{path}::root#section"
            for path in context.pages
        }

    def _write_compiler_config(self, *, graph_backend: str | None = None) -> None:
        backend = f'graph_backend = "{graph_backend}"\n' if graph_backend is not None else ""
        (self.root / ".llm-wiki.toml").write_text(
            'schema_version = "1"\n'
            'runtime_contract = "2"\n'
            '[compiler]\n'
            'providers = ["seed", "frontmatter", "text", "graph", "source"]\n'
            f"{backend}",
            encoding="utf-8",
        )

    @staticmethod
    def _node(node_id: str, file_path: str) -> dict:
        return {
            "id": node_id,
            "namespace": "loci",
            "kind": "section",
            "attributes": {"file": file_path, "line": 1, "end_line": 20},
        }

    @staticmethod
    def _step(
        from_id: str,
        to_id: str,
        file_path: str,
        line: int,
        content: str,
        page_text: str,
    ) -> dict:
        return {
            "traversed": "forward",
            "edge": {
                "from": from_id,
                "to": to_id,
                "type": "body_link",
                "directed": True,
                "namespace": "llm-wiki",
                "resolution": "declared",
                "evidence": {
                    "file": file_path,
                    "line": line,
                    "content_hash": hashlib.sha256(page_text.encode("utf-8")).hexdigest(),
                },
            },
            "evidence_span": {
                "file": file_path,
                "start_line": line,
                "end_line": line,
                "content": content,
            },
        }


if __name__ == "__main__":
    unittest.main()
