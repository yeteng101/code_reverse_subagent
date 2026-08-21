import json
import unittest
from pathlib import Path

from server import DemoState
from schema_assertions import SchemaAssertionError, SchemaStore


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "code_reverse_agent"
CONTRACTS = APP / "contracts"


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.schemas = SchemaStore(CONTRACTS)

    def test_contract_files_are_valid_json(self):
        files = sorted(CONTRACTS.glob("*.json")) + sorted((CONTRACTS / "examples").glob("*.json"))
        self.assertGreaterEqual(len(files), 12)
        for path in files:
            with self.subTest(path=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertIsInstance(payload, dict)
                if path.parent.name != "examples" and path.name != "openapi.json":
                    self.assertIn("$schema", payload)

    def test_all_contract_references_resolve_locally(self):
        for path in sorted(CONTRACTS.glob("*.json")):
            with self.subTest(path=path.name):
                self.schemas.assert_all_local_refs_resolve(path)

    def test_checked_in_examples_match_their_schemas(self):
        cases = {
            "create-libuv-analysis.json": "analysis.create.schema.json",
            "create-redis-analysis.json": "analysis.create.schema.json",
            "query-async-chain.json": "query.request.schema.json",
            "tool-trace-async-chain.invoke.json": "tool.invoke.schema.json",
            "tool-trace-async-chain.result.json": "tool.result.schema.json",
        }
        for example_name, schema_name in cases.items():
            with self.subTest(example=example_name):
                example = json.loads((CONTRACTS / "examples" / example_name).read_text(encoding="utf-8"))
                self.schemas.assert_valid(example, schema_name)

    def test_subagent_tool_registry_and_branches_stay_in_sync(self):
        invocation = json.loads((CONTRACTS / "tool.invoke.schema.json").read_text(encoding="utf-8"))
        result = json.loads((CONTRACTS / "tool.result.schema.json").read_text(encoding="utf-8"))
        openapi = json.loads((CONTRACTS / "openapi.json").read_text(encoding="utf-8"))
        tool_names = invocation["$defs"]["toolName"]["enum"]
        branch_names = [item["if"]["properties"]["tool_name"]["const"] for item in invocation["allOf"]]
        result_branch_names = [
            item["if"]["properties"]["tool_name"]["const"]
            for item in result["allOf"]
            if "tool_name" in item["if"].get("properties", {})
        ]
        advertised_names = openapi["x-subagent-tool-protocol"]["tools"]
        self.assertEqual(tool_names, branch_names)
        self.assertEqual(tool_names, result_branch_names)
        self.assertEqual(tool_names, advertised_names)
        self.assertEqual(
            openapi["components"]["schemas"]["SubAgentToolInvocation"]["$ref"],
            "tool.invoke.schema.json",
        )
        self.assertEqual(
            openapi["components"]["schemas"]["SubAgentToolResult"]["$ref"],
            "tool.result.schema.json",
        )

    def test_tool_arguments_reject_the_wrong_shape(self):
        invalid = {
            "schema_version": "1.0",
            "tool_call_id": "tc_invalid",
            "analysis_id": "an_demo",
            "tool_name": "trace_async_chain",
            "arguments": {"query": "uv_read_start"},
        }
        with self.assertRaises(SchemaAssertionError):
            self.schemas.assert_valid(invalid, "tool.invoke.schema.json")

    def test_every_subagent_tool_has_a_valid_invocation_and_result_branch(self):
        arguments = {
            "find_symbol": {"query": "uv_run"},
            "get_call_edges": {"node_id": "n_uv_run"},
            "trace_async_chain": {"start_node_id": "n_uv_read_start"},
            "resolve_pointer": {"callsite": {"source_node_id": "n_uv_stream_io"}},
            "read_slice": {"file": "src/unix/stream.c", "start_line": 1200},
            "query_configuration": {},
            "report_uncertainty": {},
        }
        result_types = {
            "find_symbol": "symbols",
            "get_call_edges": "edges",
            "trace_async_chain": "paths",
            "resolve_pointer": "pointer_candidates",
            "read_slice": "source_slices",
            "query_configuration": "configurations",
            "report_uncertainty": "uncertainties",
        }
        for tool_name, tool_arguments in arguments.items():
            with self.subTest(tool=tool_name):
                invocation = {
                    "schema_version": "1.0",
                    "tool_call_id": f"tc_{tool_name}",
                    "analysis_id": "an_demo",
                    "tool_name": tool_name,
                    "arguments": tool_arguments,
                }
                self.schemas.assert_valid(invocation, "tool.invoke.schema.json")
                result = {
                    "resource_type": "tool_result",
                    "schema_version": "1.0",
                    "tool_call_id": f"tc_{tool_name}",
                    "analysis_id": "an_demo",
                    "tool_name": tool_name,
                    "ok": True,
                    "result": {"result_type": result_types[tool_name], "items": []},
                    "evidence": [],
                }
                self.schemas.assert_valid(result, "tool.result.schema.json")

    def test_public_request_schemas_reject_invalid_discriminators(self):
        invalid_build = {
            "repository": {"name": "libuv", "kind": "libuv", "path": "targets/libuv"},
            "build": {"mode": "compile_commands"},
        }
        with self.assertRaises(SchemaAssertionError):
            self.schemas.assert_valid(invalid_build, "analysis.create.schema.json")

        invalid_query = {
            "analysis_id": "an_demo",
            "question": "trace callback",
            "scope": {"edge_types": ["guessed_by_model"]},
        }
        with self.assertRaises(SchemaAssertionError):
            self.schemas.assert_valid(invalid_query, "query.request.schema.json")

    def test_tool_result_success_and_error_payloads_are_mutually_exclusive(self):
        example = json.loads(
            (CONTRACTS / "examples" / "tool-trace-async-chain.result.json").read_text(encoding="utf-8")
        )
        invalid_success = {**example, "error": {"code": "INTERNAL_ERROR", "message": "unexpected"}}
        with self.assertRaises(SchemaAssertionError):
            self.schemas.assert_valid(invalid_success, "tool.result.schema.json")

        invalid_failure = {**example, "ok": False}
        with self.assertRaises(SchemaAssertionError):
            self.schemas.assert_valid(invalid_failure, "tool.result.schema.json")

        valid_failure = {
            key: value
            for key, value in example.items()
            if key not in {"result", "pagination"}
        }
        valid_failure.update({
            "ok": False,
            "evidence": [],
            "error": {"code": "NOT_FOUND", "message": "symbol not found", "retryable": False},
        })
        self.schemas.assert_valid(valid_failure, "tool.result.schema.json")

    def test_versioned_analysis_resource_and_graph(self):
        state = DemoState(ROOT)
        resource = state.create_api_analysis({
            "repository": {
                "name": "demo",
                "kind": "custom",
                "path": "code_reverse_agent/samples/async_pipeline.cpp",
            },
            "build": {"configurations": [{"id": "test", "target": "host"}]},
        })
        self.assertTrue(resource["analysis_id"].startswith("an_"))
        self.assertEqual(resource["status"], "completed")
        self.assertEqual(resource["profile"]["status"], "synthetic_validation")
        self.assertEqual(resource["summary"]["node_count"], 7)
        self.schemas.assert_valid(resource, "analysis.resource.schema.json")
        graph = state.api_graph(resource["analysis_id"], {"limit": "500"})
        self.schemas.assert_valid(graph, "graph.response.schema.json")
        self.assertEqual(graph["resource_type"], "graph")
        self.assertEqual(len(graph["graph"]["nodes"]), 7)
        self.assertEqual(len(graph["graph"]["edges"]), 7)
        self.assertTrue(all(node["node_id"].startswith("n_") for node in graph["graph"]["nodes"]))
        self.assertTrue(all(edge["edge_id"].startswith("e_") for edge in graph["graph"]["edges"]))

    def test_graph_pagination_has_no_dangling_edges_and_covers_all_edges(self):
        state = DemoState(ROOT)
        resource = state.create_api_analysis({
            "repository": {
                "name": "paged-demo",
                "kind": "custom",
                "path": "code_reverse_agent/samples/async_pipeline.cpp",
            }
        })
        cursor = None
        edge_ids = set()
        while True:
            params = {"limit": "1"}
            if cursor is not None:
                params["cursor"] = cursor
            page = state.api_graph(resource["analysis_id"], params)
            self.schemas.assert_valid(page, "graph.response.schema.json")
            node_ids = {node["node_id"] for node in page["graph"]["nodes"]}
            self.assertTrue(all(
                edge["source"] in node_ids and edge["target"] in node_ids
                for edge in page["graph"]["edges"]
            ))
            edge_ids.update(edge["edge_id"] for edge in page["graph"]["edges"])
            if not page["page"]["has_more"]:
                self.assertIsNone(page["page"]["next_cursor"])
                break
            cursor = page["page"]["next_cursor"]
            self.assertIsNotNone(cursor)
        self.assertEqual(len(edge_ids), resource["summary"]["edge_count"])

    def test_graph_cursor_is_bound_to_analysis_and_filters(self):
        state = DemoState(ROOT)
        first = state.create_api_analysis({
            "repository": {
                "name": "first",
                "kind": "custom",
                "path": "code_reverse_agent/samples/async_pipeline.cpp",
            },
            "build": {"configurations": [{"id": "first-config", "target": "host"}]},
        })
        second = state.create_api_analysis({
            "repository": {
                "name": "second",
                "kind": "custom",
                "path": "code_reverse_agent/samples/redis_event_loop.c",
            },
            "build": {"configurations": [{"id": "second-config", "target": "host"}]},
        })
        cursor = state.api_graph(first["analysis_id"], {"limit": "1"})["page"]["next_cursor"]
        self.assertIsNotNone(cursor)
        with self.assertRaises(ValueError):
            state.api_graph(first["analysis_id"], {"limit": "1", "cursor": cursor, "edge_type": "direct"})
        with self.assertRaises(ValueError):
            state.api_graph(second["analysis_id"], {"limit": "1", "cursor": cursor})
        with self.assertRaises(ValueError):
            state.api_graph(first["analysis_id"], {"configuration_id": "second-config"})

    def test_graph_configuration_filter_uses_edge_membership(self):
        state = DemoState(ROOT)
        resource = state.create_api_analysis({
            "repository": {
                "name": "configured-demo",
                "kind": "custom",
                "path": "code_reverse_agent/samples/async_pipeline.cpp",
            },
            "build": {"configurations": [
                {"id": "linux", "target": "linux"},
                {"id": "macos", "target": "macos"},
            ]},
        })
        stored = state.api_resources[resource["analysis_id"]]
        analysis = state.analyses[stored["_analysis_key"]]
        for index, edge in enumerate(analysis["edges"]):
            edge["configurations"] = ["linux" if index % 2 == 0 else "macos"]
        linux = state.api_graph(resource["analysis_id"], {
            "configuration_id": "linux",
            "limit": "500",
        })
        self.schemas.assert_valid(linux, "graph.response.schema.json")
        self.assertTrue(linux["graph"]["edges"])
        self.assertTrue(all(edge["configurations"] == ["linux"] for edge in linux["graph"]["edges"]))
        self.assertEqual(
            len(linux["graph"]["edges"]),
            sum(index % 2 == 0 for index in range(len(analysis["edges"]))),
        )

    def test_graph_rejects_invalid_limit_and_filter_values(self):
        state = DemoState(ROOT)
        resource = state.create_api_analysis({
            "repository": {
                "name": "invalid-filter-demo",
                "kind": "custom",
                "path": "code_reverse_agent/samples/async_pipeline.cpp",
            }
        })
        for params in ({"limit": "0"}, {"limit": "many"}, {"edge_type": "invented"}, {"node_kind": "lambda"}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                state.api_graph(resource["analysis_id"], params)

    def test_versioned_query_is_grounded(self):
        state = DemoState(ROOT)
        resource = state.create_api_analysis({
            "repository": {
                "name": "libuv-demo",
                "kind": "libuv",
                "path": "code_reverse_agent/samples/async_pipeline.cpp",
            }
        })
        reply = state.api_query({
            "analysis_id": resource["analysis_id"],
            "question": "有哪些异步回调？",
        })
        self.assertEqual(reply["resource_type"], "query")
        self.assertTrue(reply["citations"])
        self.assertTrue(all(item["file"] for item in reply["citations"]))
        self.schemas.assert_valid(reply, "query.response.schema.json")


if __name__ == "__main__":
    unittest.main()
