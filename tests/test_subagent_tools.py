import unittest
from pathlib import Path

from schema_assertions import SchemaStore
from server import DemoState
from subagent_tools import SubAgentToolRuntime, ToolInvocationError, invoke_tool


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "code_reverse_agent"
CONTRACTS = APP / "contracts"
SAMPLE = "code_reverse_agent/samples/async_pipeline.cpp"


class SubAgentToolRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = SchemaStore(CONTRACTS)
        cls.state = DemoState(ROOT)
        cls.resource = cls.state.create_api_analysis({
            "repository": {"name": "tool-demo", "kind": "custom", "path": SAMPLE},
            "build": {
                "configurations": [{
                    "id": "host-debug",
                    "target": "host",
                    "compiler": "c++",
                    "defines": {"ENABLE_AUDIT": 1},
                    "flags": ["-g"],
                }]
            },
        })
        cls.runtime = SubAgentToolRuntime(cls.state)
        graph = cls.state.api_graph(cls.resource["analysis_id"], {"limit": "500"})
        cls.nodes = {node["name"]: node for node in graph["graph"]["nodes"]}

    def invoke(self, tool_name, arguments, suffix=None):
        invocation = {
            "schema_version": "1.0",
            "tool_call_id": f"tc_{suffix or tool_name}",
            "analysis_id": self.resource["analysis_id"],
            "tool_name": tool_name,
            "arguments": arguments,
        }
        self.schemas.assert_valid(invocation, "tool.invoke.schema.json")
        result = self.runtime.invoke(invocation)
        self.schemas.assert_valid(result, "tool.result.schema.json")
        self.assertEqual(result["tool_call_id"], invocation["tool_call_id"])
        self.assertEqual(result["analysis_id"], invocation["analysis_id"])
        self.assertEqual(result["tool_name"], invocation["tool_name"])
        return result

    def test_find_symbol_returns_protocol_nodes_and_source_evidence(self):
        result = self.invoke("find_symbol", {
            "query": "process",
            "match": "prefix",
            "kinds": ["function"],
            "configuration_ids": ["host-debug"],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["result_type"], "symbols")
        self.assertEqual([item["name"] for item in result["result"]["items"]], ["process_request"])
        self.assertTrue(result["result"]["items"][0]["node_id"].startswith("n_fn_"))
        self.assertEqual(result["evidence"][0]["kind"], "source")

    def test_get_call_edges_filters_direction_type_and_confidence(self):
        node_id = self.nodes["process_request"]["node_id"]
        result = self.invoke("get_call_edges", {
            "node_id": node_id,
            "direction": "outgoing",
            "edge_types": ["async", "callback", "function_pointer"],
            "minimum_confidence": 0.8,
            "configuration_ids": ["host-debug"],
        })
        self.assertTrue(result["ok"])
        edges = result["result"]["items"]
        self.assertEqual({edge["type"] for edge in edges}, {"async", "callback"})
        self.assertTrue(all(edge["source"] == node_id for edge in edges))
        self.assertTrue(all(edge["evidence"] for edge in edges))

    def test_trace_async_chain_returns_reproducible_paths(self):
        result = self.invoke("trace_async_chain", {
            "start_node_id": self.nodes["process_request"]["node_id"],
            "edge_types": ["callback", "async", "function_pointer"],
            "direction": "forward",
            "max_hops": 3,
            "max_paths": 8,
            "minimum_confidence": 0.7,
        })
        self.assertTrue(result["ok"])
        paths = result["result"]["items"]
        self.assertEqual(len(paths), 3)
        self.assertTrue(all(len(path["node_ids"]) == len(path["edge_ids"]) + 1 for path in paths))
        self.assertTrue(all(path["confidence"] <= 0.84 for path in paths))
        self.assertTrue({stage["kind"] for path in paths for stage in path["stages"]} <= {
            "registration", "scheduling", "execution"
        })

    def test_resolve_pointer_finds_candidates_at_source_node(self):
        result = self.invoke("resolve_pointer", {
            "callsite": {
                "source_node_id": self.nodes["process_request"]["node_id"],
                "expression": "handler(request)",
            },
            "configuration_ids": ["host-debug"],
            "include_inferred": True,
            "max_candidates": 10,
        })
        self.assertTrue(result["ok"])
        targets = {item["target_node_id"] for item in result["result"]["items"]}
        self.assertIn(self.nodes["persist_result"]["node_id"], targets)
        self.assertTrue(all(item["rationale"] for item in result["result"]["items"]))
        self.assertTrue(result["evidence"])

    def test_read_slice_uses_analysis_snapshot_and_hashes_code(self):
        result = self.invoke("read_slice", {
            "node_id": self.nodes["process_request"]["node_id"],
            "context_lines": 0,
            "configuration_id": "host-debug",
        })
        self.assertTrue(result["ok"])
        item = result["result"]["items"][0]
        self.assertIn("void process_request", item["code"])
        self.assertIn("handler(request);", item["code"])
        self.assertTrue(item["snippet_hash"].startswith("sha256:"))
        self.assertEqual(item["file"], SAMPLE)

    def test_query_configuration_can_include_or_hide_defines(self):
        hidden = self.invoke("query_configuration", {}, "query_configuration_hidden")
        shown = self.invoke("query_configuration", {
            "configuration_ids": ["host-debug"],
            "symbol": "process_request",
            "include_defines": True,
            "include_variants": True,
        }, "query_configuration_shown")
        self.assertNotIn("defines", hidden["result"]["items"][0]["configuration"])
        self.assertEqual(
            shown["result"]["items"][0]["configuration"]["defines"],
            {"ENABLE_AUDIT": 1},
        )

    def test_report_uncertainty_combines_unresolved_low_confidence_and_config_variants(self):
        result = self.invoke("report_uncertainty", {
            "maximum_confidence": 0.8,
            "codes": ["UNRESOLVED_TARGET", "LOW_CONFIDENCE", "CONDITIONAL_VARIANT"],
        })
        self.assertTrue(result["ok"])
        codes = {item["code"] for item in result["result"]["items"]}
        self.assertEqual(codes, {"UNRESOLVED_TARGET", "LOW_CONFIDENCE", "CONDITIONAL_VARIANT"})
        self.assertTrue(result["evidence"])

    def test_pagination_cursor_is_runtime_owned_and_stable(self):
        first = self.invoke("find_symbol", {
            "query": "e",
            "match": "substring",
            "limit": 2,
        }, "find_page_one")
        self.assertTrue(first["pagination"]["has_more"])
        second = self.invoke("find_symbol", {
            "query": "e",
            "match": "substring",
            "limit": 2,
            "cursor": first["pagination"]["next_cursor"],
        }, "find_page_two")
        first_ids = {item["node_id"] for item in first["result"]["items"]}
        second_ids = {item["node_id"] for item in second["result"]["items"]}
        self.assertFalse(first_ids & second_ids)

    def test_malformed_base64_cursor_returns_invalid_argument(self):
        result = self.invoke("find_symbol", {
            "query": "main",
            "cursor": "cur_a",
        }, "malformed_cursor")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
        self.assertNotIn("result", result)

    def test_empty_source_line_is_not_emitted_as_evidence(self):
        state = DemoState(ROOT)
        resource = state.create_api_analysis({
            "repository": {"name": "blank-line-demo", "kind": "custom", "path": SAMPLE},
        })
        raw_analysis = state.analyses[state.api_resources[resource["analysis_id"]]["_analysis_key"]]
        process_request = next(
            function for function in raw_analysis["functions"] if function["name"] == "process_request"
        )
        raw_analysis["unresolved_calls"] = [{
            "source": process_request["id"],
            "name": "blank_line_target",
            "file": SAMPLE,
            "line": 10,
        }]
        result = SubAgentToolRuntime(state).invoke({
            "schema_version": "1.0",
            "tool_call_id": "tc_blank_line_evidence",
            "analysis_id": resource["analysis_id"],
            "tool_name": "report_uncertainty",
            "arguments": {
                "node_ids": [f"n_{process_request['id']}"],
                "codes": ["UNRESOLVED_TARGET"],
            },
        })
        self.schemas.assert_valid(result, "tool.result.schema.json")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["result"]["items"]), 1)
        self.assertEqual(result["evidence"], [])

    def test_semantic_errors_return_failure_envelopes(self):
        result = self.invoke("get_call_edges", {
            "node_id": "n_does_not_belong_to_analysis",
        }, "foreign_node")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "NOT_FOUND")
        self.assertNotIn("result", result)

        escape = self.invoke("read_slice", {
            "file": "../server.py",
            "start_line": 1,
        }, "source_escape")
        self.assertFalse(escape["ok"])
        self.assertEqual(escape["error"]["code"], "SOURCE_UNAVAILABLE")

    def test_bad_arguments_return_invalid_argument_and_never_raise(self):
        invocation = {
            "schema_version": "1.0",
            "tool_call_id": "tc_bad_args",
            "analysis_id": self.resource["analysis_id"],
            "tool_name": "find_symbol",
            "arguments": {"query": "main", "unexpected": True},
        }
        result = invoke_tool(self.state, invocation)
        self.schemas.assert_valid(result, "tool.result.schema.json")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")

    def test_invalid_correlation_id_raises_explicitly(self):
        with self.assertRaises(ToolInvocationError):
            self.runtime.invoke({
                "schema_version": "1.0",
                "tool_call_id": "bad",
                "analysis_id": self.resource["analysis_id"],
                "tool_name": "find_symbol",
                "arguments": {"query": "main"},
            })


if __name__ == "__main__":
    unittest.main()
