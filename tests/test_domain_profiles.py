import unittest
from pathlib import Path

from analyzer import CodeAnalyzer
from server import DemoState


ROOT = Path(__file__).resolve().parents[2]
REDIS_FIXTURE = ROOT / "code_reverse_agent" / "samples" / "redis_event_loop.c"
LIBUV_SRC = ROOT / "libuv" / "src"


class DomainProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analysis = CodeAnalyzer(ROOT).analyze(REDIS_FIXTURE)
        cls.functions = {item["id"]: item["name"] for item in cls.analysis["functions"]}

    def edge_names(self, edge_type):
        return {
            (self.functions[edge["source"]], self.functions[edge["target"]])
            for edge in self.analysis["edges"] if edge["type"] == edge_type
        }

    def test_redis_fixture_is_not_reported_as_repository_evidence(self):
        profile = self.analysis["profile"]
        self.assertEqual(profile["kind"], "redis")
        self.assertEqual(profile["status"], "synthetic_validation")
        self.assertEqual(profile["evidence_basis"], "synthetic_fixture")
        self.assertFalse(profile["configuration_separation"])
        self.assertTrue(profile["limitations"])

    def test_registration_scheduling_and_invocation_are_separate(self):
        self.assertIn(
            ("main", "readQueryFromClient"),
            self.edge_names("registers_callback"),
        )
        self.assertIn(
            ("aeMain", "aeProcessEvents"),
            self.edge_names("scheduled_by"),
        )
        self.assertIn(
            ("aeProcessEvents", "readQueryFromClient"),
            self.edge_names("invokes_callback"),
        )
        self.assertIn(
            ("processTimeEvents", "serverCron"),
            self.edge_names("invokes_callback"),
        )

    def test_callback_slot_and_multiline_conditional_macro_have_evidence(self):
        slots = {item["slot"]: item for item in self.analysis["callback_slots"]}
        self.assertEqual(slots["rfileProc"]["resolution"], "observed")
        self.assertEqual(slots["timeProc"]["resolution"], "observed")
        macros = {item["name"]: item for item in self.analysis["macros"]}
        self.assertIn("AE_DISPATCH(mask, bit)", macros)
        self.assertIn("(((mask) & (bit)) != 0)", macros["AE_DISPATCH(mask, bit)"]["value"])
        self.assertTrue(macros["AE_BACKEND"]["conditional"])

    def test_versioned_graph_preserves_domain_edge_types(self):
        state = DemoState(ROOT)
        resource = state.create_api_analysis({
            "repository": {
                "name": "redis-rule-fixture",
                "kind": "redis",
                "path": "code_reverse_agent/samples/redis_event_loop.c",
            }
        })
        graph = state.api_graph(resource["analysis_id"], {"limit": "500"})
        edges = graph["graph"]["edges"]
        types = {edge["type"] for edge in edges}
        self.assertTrue({"registers_callback", "scheduled_by", "invokes_callback"} <= types)
        semantic_edges = [edge for edge in edges if edge["type"] != "direct"]
        self.assertTrue(all(edge["resolution"] == "inferred" for edge in semantic_edges))
        self.assertTrue(all(edge["semantics"] in {"registration", "dispatch"} for edge in semantic_edges))


@unittest.skipUnless(LIBUV_SRC.is_dir(), "libuv source snapshot is not present")
class LibuvRepositoryProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analysis = CodeAnalyzer(ROOT).analyze(LIBUV_SRC)
        cls.functions = {item["id"]: item["name"] for item in cls.analysis["functions"]}
        cls.edges = {
            (edge["type"], cls.functions[edge["source"]], cls.functions[edge["target"]])
            for edge in cls.analysis["edges"]
        }

    def test_real_repository_profile_and_event_loop_chain(self):
        profile = self.analysis["profile"]
        self.assertEqual(profile["kind"], "libuv")
        self.assertEqual(profile["status"], "source_verified")
        self.assertEqual(profile["evidence_basis"], "repository_snapshot")
        self.assertIn(("scheduled_by", "uv_run", "uv__io_poll"), self.edges)
        self.assertIn(("scheduled_by", "uv__io_poll", "uv__io_cb"), self.edges)
        self.assertIn(("invokes_callback", "uv__io_cb", "uv__stream_io"), self.edges)
        self.assertIn(("scheduled_by", "uv__stream_io", "uv__read"), self.edges)

    def test_real_callback_slots_and_complex_macros(self):
        slots = {item["slot"]: item for item in self.analysis["callback_slots"]}
        self.assertEqual(slots["read_cb"]["resolution"], "observed")
        macros = {item["name"]: item for item in self.analysis["macros"]}
        self.assertIn("uv__io_cb_set(w, cb)", macros)
        self.assertIn("(w)->bits", macros["uv__io_cb_set(w, cb)"]["value"])


if __name__ == "__main__":
    unittest.main()
