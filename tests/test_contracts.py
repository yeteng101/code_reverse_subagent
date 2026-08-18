import json
import unittest
from pathlib import Path

from analyzer import CodeAnalyzer
from server import DemoState


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "code_reverse_agent"


class ContractTests(unittest.TestCase):
    def test_contract_files_are_valid_json(self):
        contract_dir = APP / "contracts"
        files = sorted(contract_dir.glob("*.json")) + sorted((contract_dir / "examples").glob("*.json"))
        self.assertGreaterEqual(len(files), 8)
        for path in files:
            with self.subTest(path=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertIsInstance(payload, dict)
                if path.parent.name != "examples" and path.name != "openapi.json":
                    self.assertIn("$schema", payload)

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
        self.assertEqual(resource["summary"]["node_count"], 7)
        graph = state.api_graph(resource["analysis_id"], {"limit": "500"})
        self.assertEqual(graph["resource_type"], "graph")
        self.assertEqual(len(graph["graph"]["nodes"]), 7)
        self.assertEqual(len(graph["graph"]["edges"]), 7)
        self.assertTrue(all(node["node_id"].startswith("n_") for node in graph["graph"]["nodes"]))
        self.assertTrue(all(edge["edge_id"].startswith("e_") for edge in graph["graph"]["edges"]))

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


if __name__ == "__main__":
    unittest.main()
