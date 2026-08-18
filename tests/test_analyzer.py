import unittest
from pathlib import Path

from analyzer import AnalysisExplainer, CodeAnalyzer


ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "code_reverse_agent" / "samples" / "async_pipeline.cpp"


class CodeAnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.analysis = CodeAnalyzer(ROOT).analyze(SAMPLE)

    def test_extracts_functions_and_entry_point(self):
        names = {item["name"] for item in self.analysis["functions"]}
        self.assertTrue({"main", "process_request", "persist_result"} <= names)
        entries = {
            item["name"]
            for item in self.analysis["functions"]
            if item["id"] in self.analysis["entry_points"]
        }
        self.assertEqual(entries, {"main"})

    def test_extracts_key_edge_types(self):
        edge_types = {item["type"] for item in self.analysis["edges"]}
        self.assertIn("direct", edge_types)
        self.assertIn("function_pointer", edge_types)
        self.assertIn("async", edge_types)
        self.assertIn("callback", edge_types)

    def test_explainer_cites_source(self):
        reply = AnalysisExplainer().answer(self.analysis, "有哪些异步回调？")
        self.assertIn("异步", reply["answer"])
        self.assertTrue(reply["citations"])


if __name__ == "__main__":
    unittest.main()
