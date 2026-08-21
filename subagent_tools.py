"""Evidence-first runtime for the seven internal SubAgent tools.

The runtime consumes analyses already owned by ``DemoState``.  It intentionally
uses only that analysis snapshot and its repository metadata: tools cannot read
an arbitrary host path or mix IDs from different analyses.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable


TOOL_NAMES = {
    "find_symbol",
    "get_call_edges",
    "trace_async_chain",
    "resolve_pointer",
    "read_slice",
    "query_configuration",
    "report_uncertainty",
}
NODE_KINDS = {
    "repository", "module", "file", "function", "method", "type", "variable",
    "macro", "event_loop", "handle", "request", "callback", "scheduler",
    "io_watcher", "timer", "phase", "entry_point",
}
EDGE_TYPES = {
    "direct", "virtual", "callback", "async", "function_pointer",
    "registers_callback", "scheduled_by", "invokes_callback", "owns_lifecycle",
    "reads", "writes", "macro_variant", "unresolved",
}
ASYNC_EDGE_TYPES = {
    "callback", "async", "function_pointer", "registers_callback",
    "scheduled_by", "invokes_callback",
}
UNCERTAINTY_CODES = {
    "UNRESOLVED_TARGET", "MISSING_BUILD_CONFIGURATION", "LOW_CONFIDENCE",
    "CONDITIONAL_VARIANT", "SOURCE_UNAVAILABLE", "EVIDENCE_GAP",
}
ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
TOOL_CALL_RE = re.compile(r"^tc_[A-Za-z0-9_-]+$")
ANALYSIS_RE = re.compile(r"^an_[A-Za-z0-9_-]+$")
NODE_RE = re.compile(r"^n_[A-Za-z0-9_-]+$")
EDGE_RE = re.compile(r"^e_[A-Za-z0-9_-]+$")


class ToolInvocationError(ValueError):
    """Raised only when no schema-valid result envelope can be constructed."""


class _ToolFailure(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.retryable = retryable


@dataclass(frozen=True)
class _Snapshot:
    analysis_id: str
    analysis: dict[str, Any]
    resource: dict[str, Any]
    configurations: list[dict[str, Any]]
    configuration_ids: tuple[str, ...]
    nodes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]
    nodes_by_id: dict[str, dict[str, Any]]
    edges_by_id: dict[str, dict[str, Any]]
    functions_by_raw_id: dict[str, dict[str, Any]]


class SubAgentToolRuntime:
    """Execute contract-shaped tool invocations against a ``DemoState``.

    ``state`` is deliberately duck-typed to keep this module independent from
    the HTTP server.  It must expose ``workspace``, ``analyses`` and
    ``api_resources``, matching the existing ``DemoState``.
    """

    _ARGUMENTS: dict[str, set[str]] = {
        "find_symbol": {"query", "match", "kinds", "configuration_ids", "limit", "cursor"},
        "get_call_edges": {
            "node_id", "direction", "edge_types", "configuration_ids",
            "minimum_confidence", "limit", "cursor",
        },
        "trace_async_chain": {
            "start_node_id", "end_node_id", "configuration_ids", "edge_types",
            "direction", "max_hops", "max_paths", "minimum_confidence",
        },
        "resolve_pointer": {
            "callsite", "configuration_ids", "include_inferred", "max_candidates",
        },
        "read_slice": {
            "node_id", "edge_id", "file", "start_line", "end_line",
            "context_lines", "configuration_id",
        },
        "query_configuration": {
            "configuration_ids", "symbol", "file", "include_defines",
            "include_variants", "limit", "cursor",
        },
        "report_uncertainty": {
            "node_ids", "edge_ids", "configuration_ids", "maximum_confidence",
            "codes", "limit", "cursor",
        },
    }

    def __init__(self, state: Any):
        self.state = state
        self.workspace = Path(state.workspace).resolve()

    def invoke(self, invocation: dict[str, Any]) -> dict[str, Any]:
        """Execute one invocation and always return a result envelope when possible.

        Invalid envelope IDs or an unknown tool name cannot be echoed in a
        schema-valid result and therefore raise ``ToolInvocationError``.  Once a
        valid envelope is established, all semantic failures are returned as an
        ``ok=false`` tool-result envelope.
        """

        envelope = self._correlation_envelope(invocation)
        try:
            self._validate_envelope(invocation)
            arguments = invocation["arguments"]
            self._validate_arguments(envelope["tool_name"], arguments)
            snapshot = self._snapshot(envelope["analysis_id"])
            handler: Callable[[dict[str, Any], _Snapshot], tuple[dict, list[dict], dict | None]]
            handler = getattr(self, f"_tool_{envelope['tool_name']}")
            result, evidence, pagination = handler(arguments, snapshot)
            response = {
                "resource_type": "tool_result",
                "schema_version": "1.0",
                **envelope,
                "ok": True,
                "result": result,
                "evidence": self._deduplicate_evidence(evidence)[:100],
            }
            if pagination is not None:
                response["pagination"] = pagination
            return response
        except _ToolFailure as exc:
            error: dict[str, Any] = {
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            }
            if exc.details:
                error["details"] = exc.details
            return {
                "resource_type": "tool_result",
                "schema_version": "1.0",
                **envelope,
                "ok": False,
                "evidence": [],
                "error": error,
            }
        except Exception as exc:  # Keep concurrent tool calls correlated on failure.
            return {
                "resource_type": "tool_result",
                "schema_version": "1.0",
                **envelope,
                "ok": False,
                "evidence": [],
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": f"工具执行失败：{type(exc).__name__}",
                    "details": {"reason": str(exc)},
                    "retryable": False,
                },
            }

    def _correlation_envelope(self, invocation: Any) -> dict[str, str]:
        if not isinstance(invocation, dict):
            raise ToolInvocationError("tool invocation must be a JSON object")
        tool_call_id = invocation.get("tool_call_id")
        analysis_id = invocation.get("analysis_id")
        tool_name = invocation.get("tool_name")
        if not isinstance(tool_call_id, str) or TOOL_CALL_RE.fullmatch(tool_call_id) is None:
            raise ToolInvocationError("tool_call_id must match ^tc_[A-Za-z0-9_-]+$")
        if not isinstance(analysis_id, str) or ANALYSIS_RE.fullmatch(analysis_id) is None:
            raise ToolInvocationError("analysis_id must match ^an_[A-Za-z0-9_-]+$")
        if not isinstance(tool_name, str) or tool_name not in TOOL_NAMES:
            raise ToolInvocationError(f"unsupported tool_name: {tool_name!r}")
        return {
            "tool_call_id": tool_call_id,
            "analysis_id": analysis_id,
            "tool_name": tool_name,
        }

    def _validate_envelope(self, invocation: dict[str, Any]) -> None:
        allowed = {"schema_version", "tool_call_id", "analysis_id", "tool_name", "arguments"}
        unknown = sorted(set(invocation) - allowed)
        if unknown:
            raise _ToolFailure("INVALID_ARGUMENT", "调用信封包含未知字段", details={"fields": unknown})
        if invocation.get("schema_version") != "1.0":
            raise _ToolFailure("INVALID_ARGUMENT", "schema_version 必须为 1.0")
        if not isinstance(invocation.get("arguments"), dict):
            raise _ToolFailure("INVALID_ARGUMENT", "arguments 必须是 JSON 对象")

    def _validate_arguments(self, tool_name: str, arguments: dict[str, Any]) -> None:
        unknown = sorted(set(arguments) - self._ARGUMENTS[tool_name])
        if unknown:
            self._invalid("工具参数包含未知字段", fields=unknown)

        if tool_name == "find_symbol":
            self._string(arguments, "query", required=True, maximum=512)
            self._choice(arguments, "match", {"exact", "prefix", "substring"})
            self._enum_array(arguments, "kinds", NODE_KINDS, maximum=20)
            self._page_arguments(arguments)
        elif tool_name == "get_call_edges":
            self._identifier(arguments, "node_id", NODE_RE, required=True)
            self._choice(arguments, "direction", {"incoming", "outgoing", "both"})
            self._enum_array(arguments, "edge_types", EDGE_TYPES, maximum=16)
            self._number(arguments, "minimum_confidence", minimum=0, maximum=1)
            self._page_arguments(arguments)
        elif tool_name == "trace_async_chain":
            self._identifier(arguments, "start_node_id", NODE_RE, required=True)
            self._identifier(arguments, "end_node_id", NODE_RE)
            self._enum_array(arguments, "edge_types", ASYNC_EDGE_TYPES)
            self._choice(arguments, "direction", {"forward", "backward", "both"})
            self._integer(arguments, "max_hops", minimum=1, maximum=12)
            self._integer(arguments, "max_paths", minimum=1, maximum=20)
            self._number(arguments, "minimum_confidence", minimum=0, maximum=1)
        elif tool_name == "resolve_pointer":
            callsite = arguments.get("callsite")
            if not isinstance(callsite, dict):
                self._invalid("callsite 是必填 JSON 对象")
            unknown_callsite = sorted(set(callsite) - {"source_node_id", "file", "line", "expression"})
            if unknown_callsite:
                self._invalid("callsite 包含未知字段", fields=unknown_callsite)
            self._identifier(callsite, "source_node_id", NODE_RE)
            self._string(callsite, "file")
            self._integer(callsite, "line", minimum=1)
            self._string(callsite, "expression", allow_empty=True, maximum=1000)
            if "source_node_id" not in callsite and not ({"file", "line"} <= set(callsite)):
                self._invalid("callsite 需要 source_node_id 或 file+line")
            self._boolean(arguments, "include_inferred")
            self._integer(arguments, "max_candidates", minimum=1, maximum=100)
        elif tool_name == "read_slice":
            self._identifier(arguments, "node_id", NODE_RE)
            self._identifier(arguments, "edge_id", EDGE_RE)
            self._string(arguments, "file")
            self._integer(arguments, "start_line", minimum=1)
            self._integer(arguments, "end_line", minimum=1)
            self._integer(arguments, "context_lines", minimum=0, maximum=50)
            self._identifier(arguments, "configuration_id", ID_RE)
            if not any(key in arguments for key in ("node_id", "edge_id")) and not {
                "file", "start_line"
            } <= set(arguments):
                self._invalid("read_slice 需要 node_id、edge_id 或 file+start_line")
        elif tool_name == "query_configuration":
            self._string(arguments, "symbol", maximum=512)
            self._string(arguments, "file")
            self._boolean(arguments, "include_defines")
            self._boolean(arguments, "include_variants")
            self._page_arguments(arguments)
        elif tool_name == "report_uncertainty":
            self._id_array(arguments, "node_ids", NODE_RE, maximum=100)
            self._id_array(arguments, "edge_ids", EDGE_RE, maximum=100)
            self._number(arguments, "maximum_confidence", minimum=0, maximum=1)
            self._enum_array(arguments, "codes", UNCERTAINTY_CODES)
            self._page_arguments(arguments)

        self._id_array(arguments, "configuration_ids", ID_RE, maximum=32)

    def _snapshot(self, analysis_id: str) -> _Snapshot:
        resources = getattr(self.state, "api_resources", {})
        analyses = getattr(self.state, "analyses", {})
        resource = resources.get(analysis_id)
        if resource is None:
            raw_id = analysis_id.removeprefix("an_")
            analysis = analyses.get(raw_id)
            if analysis is None:
                raise _ToolFailure("NOT_FOUND", f"分析不存在：{analysis_id}")
            resource = {"analysis_id": analysis_id, "configurations": [], "_analysis_key": raw_id}
        else:
            analysis = analyses.get(resource.get("_analysis_key"))
            if analysis is None:
                raise _ToolFailure("NOT_FOUND", f"分析数据已失效：{analysis_id}")

        configurations = [
            deepcopy(item)
            for item in resource.get("configurations", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        configuration_ids = tuple(item["id"] for item in configurations)
        entry_points = set(analysis.get("entry_points", []))
        nodes = tuple(
            {
                "node_id": self._node_id(function["id"]),
                "kind": "function",
                "name": function["name"],
                "qualified_name": function.get("qualified_name", function["name"]),
                "module_id": function.get("module", "root"),
                "location": {
                    "file": function["file"],
                    "start_line": function["line"],
                    "end_line": function.get("end_line", function["line"]),
                },
                "configurations": list(configuration_ids),
                "attributes": {
                    "signature": function.get("signature", ""),
                    "entry_point": function["id"] in entry_points,
                    "analyzer_kind": function.get("kind", "function"),
                },
            }
            for function in analysis.get("functions", [])
        )
        nodes_by_id = {item["node_id"]: item for item in nodes}
        functions_by_raw_id = {
            function["id"]: function for function in analysis.get("functions", [])
        }
        edges = tuple(
            self._edge_resource(analysis_id, edge, configuration_ids)
            for edge in analysis.get("edges", [])
            if self._node_id(edge.get("source", "")) in nodes_by_id
            and self._node_id(edge.get("target", "")) in nodes_by_id
        )
        return _Snapshot(
            analysis_id=analysis_id,
            analysis=analysis,
            resource=resource,
            configurations=configurations,
            configuration_ids=configuration_ids,
            nodes=nodes,
            edges=edges,
            nodes_by_id=nodes_by_id,
            edges_by_id={item["edge_id"]: item for item in edges},
            functions_by_raw_id=functions_by_raw_id,
        )

    def _tool_find_symbol(
        self, arguments: dict[str, Any], snapshot: _Snapshot
    ) -> tuple[dict, list[dict], dict]:
        self._selected_configurations(arguments, snapshot)
        query = arguments["query"]
        match = arguments.get("match", "exact")
        kinds = set(arguments.get("kinds", []))

        def matches(value: str) -> bool:
            if match == "exact":
                return value == query
            if match == "prefix":
                return value.startswith(query)
            return query in value

        nodes = [
            node for node in snapshot.nodes
            if (not kinds or node["kind"] in kinds)
            and (matches(node["name"]) or matches(node.get("qualified_name", "")))
        ]
        nodes.sort(key=lambda item: (item["name"] != query, item["name"], item["location"]["file"], item["location"]["start_line"]))
        page, pagination = self._paginate(nodes, arguments)
        evidence = [self._node_evidence(node) for node in page]
        return {"result_type": "symbols", "items": page}, evidence, pagination

    def _tool_get_call_edges(
        self, arguments: dict[str, Any], snapshot: _Snapshot
    ) -> tuple[dict, list[dict], dict]:
        node_id = arguments["node_id"]
        self._require_node(snapshot, node_id)
        selected_configs = self._selected_configurations(arguments, snapshot)
        direction = arguments.get("direction", "both")
        edge_types = set(arguments.get("edge_types", []))
        minimum = arguments.get("minimum_confidence", 0)

        edges = []
        for edge in snapshot.edges:
            direction_matches = (
                direction == "both"
                or (direction == "incoming" and edge["target"] == node_id)
                or (direction == "outgoing" and edge["source"] == node_id)
            )
            if direction == "both":
                direction_matches = node_id in (edge["source"], edge["target"])
            if not direction_matches or (edge_types and edge["type"] not in edge_types):
                continue
            if edge["confidence"] < minimum or not self._configuration_match(edge, selected_configs):
                continue
            edges.append(edge)
        edges.sort(key=lambda item: (item["type"], item["source"], item["target"], item["edge_id"]))
        page, pagination = self._paginate(edges, arguments)
        evidence = [proof for edge in page for proof in edge["evidence"]]
        return {"result_type": "edges", "items": page}, evidence, pagination

    def _tool_trace_async_chain(
        self, arguments: dict[str, Any], snapshot: _Snapshot
    ) -> tuple[dict, list[dict], dict]:
        start = arguments["start_node_id"]
        end = arguments.get("end_node_id")
        self._require_node(snapshot, start)
        if end is not None:
            self._require_node(snapshot, end)
        selected_configs = self._selected_configurations(arguments, snapshot)
        selected_types = set(arguments.get(
            "edge_types", ["registers_callback", "scheduled_by", "invokes_callback"]
        ))
        direction = arguments.get("direction", "forward")
        max_hops = arguments.get("max_hops", 6)
        max_paths = arguments.get("max_paths", 8)
        minimum = arguments.get("minimum_confidence", 0)

        outgoing: dict[str, list[tuple[dict, str]]] = defaultdict(list)
        for edge in snapshot.edges:
            if edge["type"] not in selected_types or edge["confidence"] < minimum:
                continue
            if not self._configuration_match(edge, selected_configs):
                continue
            if direction in {"forward", "both"}:
                outgoing[edge["source"]].append((edge, edge["target"]))
            if direction in {"backward", "both"}:
                outgoing[edge["target"]].append((edge, edge["source"]))
        for neighbors in outgoing.values():
            neighbors.sort(key=lambda item: (item[0]["type"], item[1], item[0]["edge_id"]))

        completed: list[tuple[list[str], list[dict]]] = []
        queue = deque([([start], [])])
        while queue and len(completed) < max_paths:
            node_ids, path_edges = queue.popleft()
            current = node_ids[-1]
            if end is not None and current == end and path_edges:
                completed.append((node_ids, path_edges))
                continue
            if len(path_edges) >= max_hops:
                if end is None and path_edges:
                    completed.append((node_ids, path_edges))
                continue
            candidates = [
                (edge, target) for edge, target in outgoing.get(current, [])
                if target not in node_ids and edge["edge_id"] not in {item["edge_id"] for item in path_edges}
            ]
            if not candidates:
                if end is None and path_edges:
                    completed.append((node_ids, path_edges))
                continue
            for edge, target in candidates:
                queue.append((node_ids + [target], path_edges + [edge]))

        items = []
        evidence = []
        for node_ids, path_edges in completed[:max_paths]:
            confidence = min(edge["confidence"] for edge in path_edges)
            resolutions = {edge["resolution"] for edge in path_edges}
            resolution = "unresolved" if "unresolved" in resolutions else (
                "inferred" if "inferred" in resolutions else "observed"
            )
            raw = ":".join(edge["edge_id"] for edge in path_edges)
            items.append({
                "path_id": "p_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14],
                "label": " -> ".join(snapshot.nodes_by_id[node]["name"] for node in node_ids),
                "node_ids": node_ids,
                "edge_ids": [edge["edge_id"] for edge in path_edges],
                "stages": [
                    {
                        "kind": self._async_stage(edge["type"]),
                        "node_id": node_ids[index],
                        "edge_id": edge["edge_id"],
                    }
                    for index, edge in enumerate(path_edges)
                ],
                "resolution": resolution,
                "confidence": confidence,
            })
            evidence.extend(proof for edge in path_edges for proof in edge["evidence"])
        return (
            {"result_type": "paths", "items": items},
            evidence,
            {"next_cursor": None, "has_more": bool(queue)},
        )

    def _tool_resolve_pointer(
        self, arguments: dict[str, Any], snapshot: _Snapshot
    ) -> tuple[dict, list[dict], dict]:
        selected_configs = self._selected_configurations(arguments, snapshot)
        callsite = arguments["callsite"]
        source_node = callsite.get("source_node_id")
        canonical_file = None
        if source_node is not None:
            self._require_node(snapshot, source_node)
        if "file" in callsite:
            canonical_file, _ = self._resolve_source(snapshot, callsite["file"])
        line = callsite.get("line")
        expression = callsite.get("expression", "")
        include_inferred = arguments.get("include_inferred", True)
        max_candidates = arguments.get("max_candidates", 20)

        candidates_by_target: dict[str, tuple[dict, str]] = {}
        primary_types = {"function_pointer"}
        inferred_types = {"callback", "invokes_callback", "registers_callback"}
        allowed_types = primary_types | (inferred_types if include_inferred else set())
        evidence = []
        for edge in snapshot.edges:
            if edge["type"] not in allowed_types or not self._configuration_match(edge, selected_configs):
                continue
            if source_node is not None and edge["source"] != source_node:
                continue
            location = edge["evidence"][0]["location"]
            if canonical_file is not None and location["file"] != canonical_file:
                continue
            if line is not None and location["start_line"] != line:
                continue
            if not include_inferred and edge["resolution"] != "observed":
                continue
            target = snapshot.nodes_by_id[edge["target"]]
            expression_match = bool(
                expression and (
                    expression in edge["evidence"][0]["text"]
                    or target["name"] in expression
                )
            )
            rationale = f"{edge['type']} 边在源码调用点指向 {target['qualified_name']}"
            if expression:
                rationale += "；表达式与证据匹配" if expression_match else "；表达式仅作为调用点上下文"
            previous = candidates_by_target.get(edge["target"])
            if previous is None or edge["confidence"] > previous[0]["confidence"]:
                candidates_by_target[edge["target"]] = (edge, rationale)

        ordered = sorted(
            candidates_by_target.values(),
            key=lambda item: (-item[0]["confidence"], snapshot.nodes_by_id[item[0]["target"]]["name"]),
        )[:max_candidates]
        items = []
        for edge, rationale in ordered:
            items.append({
                "target_node_id": edge["target"],
                "resolution": edge["resolution"],
                "confidence": edge["confidence"],
                "rationale": rationale,
                "configurations": edge["configurations"],
            })
            evidence.extend(edge["evidence"])
        return (
            {"result_type": "pointer_candidates", "items": items},
            evidence,
            {"next_cursor": None, "has_more": len(candidates_by_target) > max_candidates},
        )

    def _tool_read_slice(
        self, arguments: dict[str, Any], snapshot: _Snapshot
    ) -> tuple[dict, list[dict], dict]:
        if "configuration_id" in arguments:
            self._require_configuration(snapshot, arguments["configuration_id"])
        context = arguments.get("context_lines", 5)
        if "node_id" in arguments:
            node = self._require_node(snapshot, arguments["node_id"])
            file_name = node["location"]["file"]
            start = node["location"]["start_line"]
            end = node["location"].get("end_line", start)
        elif "edge_id" in arguments:
            edge = self._require_edge(snapshot, arguments["edge_id"])
            location = edge["evidence"][0]["location"]
            file_name = location["file"]
            start = location["start_line"]
            end = location.get("end_line", start)
        else:
            file_name = arguments["file"]
            start = arguments["start_line"]
            end = arguments.get("end_line", start)
        if end < start:
            self._invalid("end_line 不能小于 start_line", start_line=start, end_line=end)

        canonical, path = self._resolve_source(snapshot, file_name)
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            raise _ToolFailure(
                "SOURCE_UNAVAILABLE", f"无法读取分析源码：{canonical}",
                details={"reason": str(exc)}, retryable=True,
            ) from exc
        if not lines or start > len(lines):
            raise _ToolFailure(
                "NOT_FOUND", f"源码行超出文件范围：{canonical}:{start}",
                details={"line_count": len(lines)},
            )
        slice_start = max(1, start - context)
        slice_end = min(len(lines), end + context)
        selected: list[str] = []
        size = 0
        for source_line in lines[slice_start - 1:slice_end]:
            addition = len(source_line) + (1 if selected else 0)
            if size + addition > 50000:
                if not selected:
                    selected.append(source_line[:50000])
                break
            selected.append(source_line)
            size += addition
        actual_end = slice_start + len(selected) - 1
        code = "\n".join(selected)
        snippet_hash = "sha256:" + hashlib.sha256(code.encode("utf-8")).hexdigest()
        item = {
            "file": canonical,
            "start_line": slice_start,
            "end_line": actual_end,
            "language": self._language(path),
            "code": code,
            "snippet_hash": snippet_hash,
        }
        evidence = [{
            "kind": "source",
            "location": {"file": canonical, "start_line": slice_start, "end_line": actual_end},
            "text": code[:2000],
            "snippet_hash": snippet_hash,
        }]
        return (
            {"result_type": "source_slices", "items": [item]},
            evidence,
            {"next_cursor": None, "has_more": actual_end < slice_end},
        )

    def _tool_query_configuration(
        self, arguments: dict[str, Any], snapshot: _Snapshot
    ) -> tuple[dict, list[dict], dict]:
        selected_ids = self._selected_configurations(arguments, snapshot)
        file_filter = arguments.get("file")
        if file_filter is not None:
            self._resolve_source(snapshot, file_filter)
        symbol = arguments.get("symbol")
        related_node_ids: set[str] = set()
        if symbol is not None:
            related_node_ids = {
                node["node_id"] for node in snapshot.nodes
                if node["name"] == symbol or node.get("qualified_name") == symbol
            }
            if not related_node_ids:
                return (
                    {"result_type": "configurations", "items": []}, [],
                    {"next_cursor": None, "has_more": False},
                )

        include_defines = arguments.get("include_defines", False)
        include_variants = arguments.get("include_variants", True)
        items = []
        for configuration in snapshot.configurations:
            if selected_ids and configuration["id"] not in selected_ids:
                continue
            public = deepcopy(configuration)
            if not include_defines:
                public.pop("defines", None)
            variant_edge_ids: list[str] = []
            if include_variants:
                for edge in snapshot.edges:
                    if edge["type"] != "macro_variant":
                        continue
                    if file_filter and edge["evidence"][0]["location"]["file"] != self._canonical_file(snapshot, file_filter):
                        continue
                    if related_node_ids and not related_node_ids.intersection({edge["source"], edge["target"]}):
                        continue
                    variant_edge_ids.append(edge["edge_id"])
            items.append({
                "configuration": public,
                "active": True,
                "source": "manual",
                "variant_edge_ids": sorted(set(variant_edge_ids)),
            })
        page, pagination = self._paginate(items, arguments)
        return {"result_type": "configurations", "items": page}, [], pagination

    def _tool_report_uncertainty(
        self, arguments: dict[str, Any], snapshot: _Snapshot
    ) -> tuple[dict, list[dict], dict]:
        self._selected_configurations(arguments, snapshot)
        node_scope = set(arguments.get("node_ids", []))
        edge_scope = set(arguments.get("edge_ids", []))
        for node_id in node_scope:
            self._require_node(snapshot, node_id)
        for edge_id in edge_scope:
            self._require_edge(snapshot, edge_id)
        codes = set(arguments.get("codes", []))
        maximum = arguments.get("maximum_confidence", 0.7)
        scoped = bool(node_scope or edge_scope)
        records: list[tuple[dict, list[dict]]] = []

        for unresolved in snapshot.analysis.get("unresolved_calls", []):
            node_id = self._node_id(unresolved.get("source", ""))
            if scoped and node_id not in node_scope:
                continue
            proof = self._source_line_evidence(
                snapshot, unresolved.get("file", ""), unresolved.get("line", 1)
            )
            records.append(({
                "code": "UNRESOLVED_TARGET",
                "severity": "warning",
                "message": f"无法解析调用目标 {unresolved.get('name', '<unknown>')}（{unresolved.get('file')}:{unresolved.get('line')}）",
                "node_ids": [node_id] if node_id in snapshot.nodes_by_id else [],
                "recommended_action": "补充 compile_commands.json、类型信息或动态调用轨迹。",
            }, [proof] if proof else []))

        for edge in snapshot.edges:
            if edge["confidence"] > maximum:
                continue
            if edge_scope and edge["edge_id"] not in edge_scope:
                continue
            if node_scope and not node_scope.intersection({edge["source"], edge["target"]}):
                continue
            records.append(({
                "code": "LOW_CONFIDENCE",
                "severity": "warning",
                "message": f"{edge['type']} 关系置信度为 {edge['confidence']:.2f}。",
                "node_ids": [edge["source"], edge["target"]],
                "edge_ids": [edge["edge_id"]],
                "recommended_action": "结合编译配置、指针赋值点或运行时轨迹复核。",
            }, edge["evidence"]))

        if not scoped and not snapshot.configurations:
            records.append(({
                "code": "MISSING_BUILD_CONFIGURATION",
                "severity": "warning",
                "message": "分析未提供构建配置，条件宏和平台分支尚未隔离。",
                "recommended_action": "提供 compile_commands.json 或至少一组 manual configuration。",
            }, []))

        if not scoped:
            for macro in snapshot.analysis.get("macros", []):
                if not macro.get("conditional"):
                    continue
                proof = self._macro_evidence(macro)
                records.append(({
                    "code": "CONDITIONAL_VARIANT",
                    "severity": "info",
                    "message": f"宏 {macro['name']} 受条件编译影响。",
                    "recommended_action": "分别在目标配置下展开宏并比较关系图。",
                }, [proof]))
            for limitation in snapshot.analysis.get("limitations", []):
                records.append(({
                    "code": "EVIDENCE_GAP",
                    "severity": "info",
                    "message": str(limitation),
                    "recommended_action": "对关键结论使用完整编译前端或运行时轨迹复核。",
                }, []))
            for file_name in snapshot.analysis.get("files", []):
                try:
                    _, source_path = self._resolve_source(snapshot, file_name)
                except _ToolFailure:
                    source_path = None
                if source_path is None or not source_path.is_file():
                    records.append(({
                        "code": "SOURCE_UNAVAILABLE",
                        "severity": "error",
                        "message": f"分析源码当前不可用：{file_name}",
                        "recommended_action": "恢复与分析 ID 对应的源码快照后重试。",
                    }, []))

        if codes:
            records = [record for record in records if record[0]["code"] in codes]
        records.sort(key=lambda record: (
            {"error": 0, "warning": 1, "info": 2}[record[0]["severity"]],
            record[0]["code"], record[0]["message"],
        ))
        page, pagination = self._paginate(records, arguments)
        items = [item for item, _ in page]
        evidence = [proof for _, proofs in page for proof in proofs]
        return {"result_type": "uncertainties", "items": items}, evidence, pagination

    def _edge_resource(
        self, analysis_id: str, edge: dict[str, Any], configuration_ids: Iterable[str]
    ) -> dict[str, Any]:
        raw = f"{analysis_id}:{edge['source']}:{edge['target']}:{edge['type']}:{edge['line']}"
        edge_id = "e_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]
        semantics = {
            "registers_callback": "registration",
            "scheduled_by": "dispatch",
            "invokes_callback": "dispatch",
        }.get(edge["type"], "dispatch" if edge["type"] in {
            "async", "callback", "function_pointer"
        } else "call")
        resolution = "observed" if edge["type"] == "direct" else (
            "unresolved" if edge["type"] == "unresolved" else "inferred"
        )
        proof = {
            "kind": "source",
            "location": {"file": edge["file"], "start_line": edge["line"]},
            "text": str(edge.get("evidence", ""))[:2000],
        }
        return {
            "edge_id": edge_id,
            "source": self._node_id(edge["source"]),
            "target": self._node_id(edge["target"]),
            "type": edge["type"] if edge["type"] in EDGE_TYPES else "unresolved",
            "semantics": semantics,
            "resolution": resolution,
            "confidence": max(0.0, min(1.0, float(edge.get("confidence", 0)))),
            "evidence": [proof],
            "configurations": list(configuration_ids),
        }

    @staticmethod
    def _node_id(raw_id: str) -> str:
        return raw_id if raw_id.startswith("n_") else f"n_{raw_id}"

    @staticmethod
    def _async_stage(edge_type: str) -> str:
        if edge_type == "registers_callback":
            return "registration"
        if edge_type in {"scheduled_by", "async"}:
            return "scheduling"
        return "execution"

    def _node_evidence(self, node: dict[str, Any]) -> dict[str, Any]:
        location = node["location"]
        text = self._read_source_line(location["file"], location["start_line"])
        if not text:
            text = node.get("attributes", {}).get("signature", node["name"])
        return {
            "kind": "source",
            "location": location,
            "text": str(text)[:2000],
        }

    def _source_line_evidence(
        self, snapshot: _Snapshot, file_name: str, line: int
    ) -> dict[str, Any] | None:
        try:
            canonical, _ = self._resolve_source(snapshot, file_name)
        except _ToolFailure:
            return None
        text = self._read_source_line(canonical, line)
        if not text:
            return None
        return {
            "kind": "source",
            "location": {"file": canonical, "start_line": line},
            "text": text[:2000],
        }

    @staticmethod
    def _macro_evidence(macro: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "macro",
            "location": {"file": macro["file"], "start_line": macro["line"]},
            "text": f"#define {macro['name']} {macro.get('value', '')}"[:2000],
        }

    def _read_source_line(self, file_name: str, line: int) -> str:
        path = (self.workspace / PurePosixPath(file_name)).resolve()
        try:
            path.relative_to(self.workspace)
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except (ValueError, OSError):
            return ""
        return lines[line - 1].strip() if 1 <= line <= len(lines) else ""

    def _resolve_source(self, snapshot: _Snapshot, requested: str) -> tuple[str, Path]:
        canonical = self._canonical_file(snapshot, requested)
        path = (self.workspace / PurePosixPath(canonical)).resolve()
        try:
            path.relative_to(self.workspace)
        except ValueError as exc:
            raise _ToolFailure("SOURCE_UNAVAILABLE", "源码路径越过工作区边界") from exc
        if not path.is_file():
            raise _ToolFailure("SOURCE_UNAVAILABLE", f"分析源码当前不可用：{canonical}")
        return canonical, path

    def _canonical_file(self, snapshot: _Snapshot, requested: str) -> str:
        if not isinstance(requested, str) or not requested.strip():
            self._invalid("file 不能为空")
        requested_path = PurePosixPath(requested.replace("\\", "/"))
        if requested_path.is_absolute() or ".." in requested_path.parts:
            raise _ToolFailure("SOURCE_UNAVAILABLE", "只能读取当前分析中的相对源码路径")
        normalized = requested_path.as_posix().lstrip("./")
        files = [str(item).replace("\\", "/") for item in snapshot.analysis.get("files", [])]
        if normalized in files:
            return normalized

        target = Path(snapshot.analysis.get("target", "")).resolve()
        aliases: dict[str, list[str]] = defaultdict(list)
        for canonical in files:
            full_path = (self.workspace / PurePosixPath(canonical)).resolve()
            if target.is_dir():
                try:
                    aliases[full_path.relative_to(target).as_posix()].append(canonical)
                except ValueError:
                    pass
            aliases[PurePosixPath(canonical).name].append(canonical)
        matches = sorted(set(aliases.get(normalized, [])))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            self._invalid("源码路径不唯一，请使用分析结果中的完整相对路径", file=requested, matches=matches[:20])
        raise _ToolFailure("SOURCE_UNAVAILABLE", f"文件不属于当前分析：{requested}")

    @staticmethod
    def _language(path: Path) -> str:
        return "cpp" if path.suffix.lower() in {".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"} else "c"

    @staticmethod
    def _deduplicate_evidence(evidence: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        seen = set()
        for proof in evidence:
            location = proof.get("location", {})
            key = (
                proof.get("kind"), location.get("file"), location.get("start_line"),
                location.get("end_line"), proof.get("text"),
            )
            if key not in seen:
                seen.add(key)
                result.append(proof)
        return result

    @staticmethod
    def _configuration_match(edge: dict[str, Any], selected: set[str]) -> bool:
        return not selected or bool(selected.intersection(edge.get("configurations", [])))

    def _selected_configurations(self, arguments: dict[str, Any], snapshot: _Snapshot) -> set[str]:
        selected = set(arguments.get("configuration_ids", []))
        missing = selected - set(snapshot.configuration_ids)
        if missing:
            raise _ToolFailure(
                "NOT_FOUND", "构建配置不属于当前分析",
                details={"configuration_ids": sorted(missing)},
            )
        return selected

    def _require_configuration(self, snapshot: _Snapshot, configuration_id: str) -> None:
        if configuration_id not in snapshot.configuration_ids:
            raise _ToolFailure("NOT_FOUND", f"构建配置不属于当前分析：{configuration_id}")

    @staticmethod
    def _require_node(snapshot: _Snapshot, node_id: str) -> dict[str, Any]:
        node = snapshot.nodes_by_id.get(node_id)
        if node is None:
            raise _ToolFailure("NOT_FOUND", f"节点不属于当前分析：{node_id}")
        return node

    @staticmethod
    def _require_edge(snapshot: _Snapshot, edge_id: str) -> dict[str, Any]:
        edge = snapshot.edges_by_id.get(edge_id)
        if edge is None:
            raise _ToolFailure("NOT_FOUND", f"边不属于当前分析：{edge_id}")
        return edge

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        token = base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")
        return f"cur_{token}"

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if cursor is None:
            return 0
        if not isinstance(cursor, str) or not cursor.startswith("cur_"):
            raise _ToolFailure("INVALID_ARGUMENT", "分页游标无效")
        token = cursor[4:]
        try:
            padded = token + "=" * (-len(token) % 4)
            offset = int(base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii"))
        except (binascii.Error, ValueError, UnicodeError) as exc:
            raise _ToolFailure("INVALID_ARGUMENT", "分页游标无效") from exc
        if offset < 0:
            raise _ToolFailure("INVALID_ARGUMENT", "分页游标无效")
        return offset

    def _paginate(
        self, items: list[Any], arguments: dict[str, Any]
    ) -> tuple[list[Any], dict[str, Any]]:
        limit = arguments.get("limit", 100)
        offset = self._decode_cursor(arguments.get("cursor"))
        page = items[offset:offset + limit]
        next_offset = offset + len(page)
        has_more = next_offset < len(items)
        return page, {
            "next_cursor": self._encode_cursor(next_offset) if has_more else None,
            "has_more": has_more,
        }

    def _page_arguments(self, arguments: dict[str, Any]) -> None:
        self._integer(arguments, "limit", minimum=1, maximum=500)
        self._string(arguments, "cursor", maximum=512)

    @staticmethod
    def _invalid(message: str, **details: Any) -> None:
        raise _ToolFailure("INVALID_ARGUMENT", message, details=details or None)

    def _string(
        self,
        data: dict[str, Any],
        key: str,
        *,
        required: bool = False,
        allow_empty: bool = False,
        maximum: int | None = None,
    ) -> None:
        if key not in data:
            if required:
                self._invalid(f"{key} 是必填字段")
            return
        value = data[key]
        if not isinstance(value, str) or (not allow_empty and not value):
            self._invalid(f"{key} 必须是非空字符串")
        if maximum is not None and len(value) > maximum:
            self._invalid(f"{key} 长度不能超过 {maximum}")

    def _identifier(
        self, data: dict[str, Any], key: str, pattern: re.Pattern[str], *, required: bool = False
    ) -> None:
        if key not in data:
            if required:
                self._invalid(f"{key} 是必填字段")
            return
        value = data[key]
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            self._invalid(f"{key} 格式无效")

    def _choice(self, data: dict[str, Any], key: str, choices: set[str]) -> None:
        if key in data and data[key] not in choices:
            self._invalid(f"{key} 取值无效", allowed=sorted(choices))

    def _enum_array(
        self, data: dict[str, Any], key: str, choices: set[str], *, maximum: int | None = None
    ) -> None:
        if key not in data:
            return
        value = data[key]
        if not isinstance(value, list) or any(not isinstance(item, str) or item not in choices for item in value):
            self._invalid(f"{key} 包含无效取值", allowed=sorted(choices))
        if len(value) != len(set(value)):
            self._invalid(f"{key} 不能包含重复项")
        if maximum is not None and len(value) > maximum:
            self._invalid(f"{key} 最多包含 {maximum} 项")

    def _id_array(
        self, data: dict[str, Any], key: str, pattern: re.Pattern[str], *, maximum: int
    ) -> None:
        if key not in data:
            return
        value = data[key]
        if not isinstance(value, list) or any(
            not isinstance(item, str) or pattern.fullmatch(item) is None for item in value
        ):
            self._invalid(f"{key} 格式无效")
        if len(value) != len(set(value)):
            self._invalid(f"{key} 不能包含重复项")
        if len(value) > maximum:
            self._invalid(f"{key} 最多包含 {maximum} 项")

    def _integer(
        self, data: dict[str, Any], key: str, *, minimum: int, maximum: int | None = None
    ) -> None:
        if key not in data:
            return
        value = data[key]
        if not isinstance(value, int) or isinstance(value, bool):
            self._invalid(f"{key} 必须是整数")
        if value < minimum or (maximum is not None and value > maximum):
            self._invalid(f"{key} 超出范围")

    def _number(
        self, data: dict[str, Any], key: str, *, minimum: float, maximum: float
    ) -> None:
        if key not in data:
            return
        value = data[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            self._invalid(f"{key} 必须是数值")
        if value < minimum or value > maximum:
            self._invalid(f"{key} 超出范围")

    def _boolean(self, data: dict[str, Any], key: str) -> None:
        if key in data and not isinstance(data[key], bool):
            self._invalid(f"{key} 必须是布尔值")


def invoke_tool(state: Any, invocation: dict[str, Any]) -> dict[str, Any]:
    """Convenience entry point for callers that do not retain a runtime."""

    return SubAgentToolRuntime(state).invoke(invocation)


__all__ = ["SubAgentToolRuntime", "ToolInvocationError", "invoke_tool"]
