"""Local web server for the code reverse-agent demo."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
from collections import defaultdict, deque
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from analyzer import AnalysisExplainer, CodeAnalyzer


APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
SAMPLE_FILE = APP_DIR / "samples" / "async_pipeline.cpp"
GRAPH_NODE_KINDS = {
    "repository", "module", "file", "function", "method", "type", "variable",
    "macro", "event_loop", "handle", "request", "callback", "scheduler",
    "io_watcher", "timer", "phase", "entry_point",
}
GRAPH_EDGE_TYPES = {
    "direct", "virtual", "callback", "async", "function_pointer",
    "registers_callback", "scheduled_by", "invokes_callback", "owns_lifecycle",
    "reads", "writes", "macro_variant", "unresolved",
}


class DemoState:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.analyzer = CodeAnalyzer(self.workspace)
        self.explainer = AnalysisExplainer()
        self.analyses: dict[str, dict] = {}
        self.api_resources: dict[str, dict] = {}

    def analyze(self, raw_path: str | None = None) -> dict:
        target = SAMPLE_FILE if not raw_path else self._resolve_target(raw_path)
        analysis = self.analyzer.analyze(target)
        self.analyses[analysis["analysis_id"]] = analysis
        return analysis

    def query(self, analysis_id: str, question: str) -> dict:
        analysis = self.analyses.get(analysis_id)
        if analysis is None:
            raise ValueError("分析结果已失效，请重新分析")
        if not question.strip():
            raise ValueError("问题不能为空")
        return self.explainer.answer(analysis, question)

    def create_api_analysis(self, body: dict) -> dict:
        repository = body.get("repository")
        if not isinstance(repository, dict):
            raise ValueError("repository 必须是 JSON 对象")
        raw_path = repository.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("repository.path 不能为空")
        build = body.get("build") if isinstance(body.get("build"), dict) else {}
        options = body.get("analysis") if isinstance(body.get("analysis"), dict) else {}
        configurations = build.get("configurations", [])
        if not isinstance(configurations, list):
            raise ValueError("build.configurations 必须是数组")
        configuration_ids = [
            item.get("id") for item in configurations
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        if len(configuration_ids) != len(set(configuration_ids)):
            raise ValueError("build.configurations.id 必须唯一")
        target = self._resolve_target(raw_path)
        identity = {
            "repository": {
                "path": str(target),
                "kind": repository.get("kind", "custom"),
                "revision": repository.get("revision"),
            },
            "build": build,
            "analysis": options,
            "request_id": body.get("request_id"),
        }
        analysis = self.analyzer.analyze(
            target,
            configurations=configurations,
            identity=identity,
        )
        self.analyses[analysis["analysis_id"]] = analysis
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        api_id = f"an_{analysis['analysis_id']}"
        resource = {
            "resource_type": "analysis",
            "schema_version": "1.0",
            "analysis_id": api_id,
            "status": "completed",
            "repository": {
                "name": str(repository.get("name") or Path(raw_path).name),
                "kind": repository.get("kind", "custom"),
                "path": raw_path,
                **({"url": repository["url"]} if repository.get("url") else {}),
                **({"revision": repository["revision"]} if repository.get("revision") else {}),
                "language": repository.get("language", "c_cpp"),
            },
            "progress": {
                "stage": "completed",
                "percent": 100,
                "message": "轻量分析完成",
                "files_processed": analysis["summary"]["file_count"],
                "files_total": analysis["summary"]["file_count"],
            },
            "summary": {
                "file_count": analysis["summary"]["file_count"],
                "function_count": analysis["summary"]["function_count"],
                "node_count": analysis["summary"]["function_count"],
                "edge_count": analysis["summary"]["edge_count"],
                "macro_count": analysis["summary"]["macro_count"],
                "unresolved_count": len(analysis["unresolved_calls"]),
                "by_edge_type": {
                    key.removesuffix("_calls"): value
                    for key, value in analysis["summary"].items()
                    if key.endswith("_calls")
                },
            },
            "profile": analysis["profile"],
            "configurations": build.get("configurations", []),
            "limitations": analysis["limitations"],
            "timestamps": {"created_at": now, "updated_at": now, "completed_at": now},
            "links": {
                "self": f"/v1/analyses/{api_id}",
                "graph": f"/v1/analyses/{api_id}/graph",
                "query": "/v1/queries",
            },
        }
        # Keep request options with the resource for future asynchronous workers.
        resource["_analysis_key"] = analysis["analysis_id"]
        resource["_profiles"] = options.get("profiles", [])
        self.api_resources[api_id] = resource
        return self._public_resource(resource)

    def get_api_resource(self, api_id: str) -> dict:
        resource = self.api_resources.get(api_id)
        if resource is None:
            raise KeyError(api_id)
        return self._public_resource(resource)

    def api_graph(self, api_id: str, params: dict[str, str]) -> dict:
        resource = self.api_resources.get(api_id)
        if resource is None:
            raise KeyError(api_id)
        analysis = self.analyses[resource["_analysis_key"]]
        functions = analysis["functions"]
        node_ids = {fn["id"]: f"n_{fn['id']}" for fn in functions}
        selected_kind = params.get("node_kind")
        selected_edge = params.get("edge_type")
        selected_configuration = params.get("configuration_id")
        if selected_kind and selected_kind not in GRAPH_NODE_KINDS:
            raise ValueError(f"node_kind 无效：{selected_kind}")
        if selected_edge and selected_edge not in GRAPH_EDGE_TYPES:
            raise ValueError(f"edge_type 无效：{selected_edge}")
        config_ids = [
            item["id"]
            for item in resource.get("configurations", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        if selected_configuration and selected_configuration not in config_ids:
            raise ValueError(f"configuration_id 不属于分析 {api_id}：{selected_configuration}")

        nodes_by_id = {}
        for fn in functions:
            kind = "function" if fn.get("kind") == "lambda" else "function"
            if selected_kind and selected_kind != kind:
                continue
            node_configurations = self._configurations_for_file(fn["file"], resource)
            if selected_configuration and selected_configuration not in node_configurations:
                continue
            node = {
                "node_id": node_ids[fn["id"]],
                "kind": kind,
                "name": fn["name"],
                "qualified_name": fn["qualified_name"],
                "module_id": fn["module"],
                "location": {
                    "file": fn["file"],
                    "start_line": fn["line"],
                    "end_line": fn["end_line"],
                },
                "configurations": [selected_configuration] if selected_configuration else node_configurations,
                "attributes": {"signature": fn["signature"], "entry_point": fn["id"] in analysis["entry_points"]},
            }
            nodes_by_id[node["node_id"]] = node
        edges = []
        for edge in analysis["edges"]:
            if edge["source"] not in node_ids or edge["target"] not in node_ids:
                continue
            source_id = node_ids[edge["source"]]
            target_id = node_ids[edge["target"]]
            if source_id not in nodes_by_id or target_id not in nodes_by_id:
                continue
            edge_type = edge["type"] if edge["type"] in GRAPH_EDGE_TYPES else "unresolved"
            if selected_edge and edge_type != selected_edge:
                continue
            raw_edge_configurations = edge.get("configurations")
            if raw_edge_configurations is None:
                raw_edge_configurations = config_ids
            if isinstance(raw_edge_configurations, tuple):
                raw_edge_configurations = list(raw_edge_configurations)
            if not isinstance(raw_edge_configurations, list):
                raw_edge_configurations = []
            edge_configurations = list(dict.fromkeys(
                value for value in raw_edge_configurations
                if isinstance(value, str) and (not config_ids or value in config_ids)
            ))
            if selected_configuration and selected_configuration not in edge_configurations:
                continue
            edge_id = "e_" + hashlib.sha1(
                f"{api_id}:{edge['source']}:{edge['target']}:{edge['type']}:{edge['line']}".encode("utf-8")
            ).hexdigest()[:14]
            semantics = {
                "registers_callback": "registration",
                "scheduled_by": "dispatch",
                "invokes_callback": "dispatch",
            }.get(edge["type"], "dispatch" if edge["type"] in {"async", "callback", "function_pointer"} else "call")
            resolution = "observed" if edge["type"] == "direct" else "inferred"
            edges.append({
                "edge_id": edge_id,
                "source": source_id,
                "target": target_id,
                "type": edge_type,
                "semantics": semantics,
                "resolution": resolution,
                "confidence": edge["confidence"],
                "evidence": [{
                    "kind": "source",
                    "location": {"file": edge["file"], "start_line": edge["line"]},
                    "text": edge["evidence"],
                }],
                "configurations": [selected_configuration] if selected_configuration else edge_configurations,
            })
        try:
            limit = int(params.get("limit", "500"))
        except (TypeError, ValueError) as exc:
            raise ValueError("limit 必须是 1-5000 的整数") from exc
        if not 1 <= limit <= 5000:
            raise ValueError("limit 必须是 1-5000 的整数")

        ordered_edges = sorted(edges, key=lambda item: item["edge_id"])
        incident_node_ids = {
            node_id
            for edge in ordered_edges
            for node_id in (edge["source"], edge["target"])
        }
        isolated_nodes = [] if selected_edge else sorted(
            (node for node_id, node in nodes_by_id.items() if node_id not in incident_node_ids),
            key=lambda item: item["node_id"],
        )
        records = [("edge", edge) for edge in ordered_edges]
        records.extend(("node", node) for node in isolated_nodes)
        fingerprint = self._graph_cursor_fingerprint(api_id, params)
        offset = self._decode_graph_cursor(params.get("cursor"), fingerprint)
        if offset > len(records):
            raise ValueError("cursor 已超出当前图查询范围")
        page_records = records[offset:offset + limit]
        page_edges = [item for kind, item in page_records if kind == "edge"]
        page_node_ids = {
            node_id
            for edge in page_edges
            for node_id in (edge["source"], edge["target"])
        }
        page_node_ids.update(item["node_id"] for kind, item in page_records if kind == "node")
        page_nodes = sorted(
            (nodes_by_id[node_id] for node_id in page_node_ids),
            key=lambda item: item["node_id"],
        )
        next_offset = offset + len(page_records)
        has_more = next_offset < len(records)
        return {
            "resource_type": "graph",
            "schema_version": "1.0",
            "analysis_id": api_id,
            "graph": {"nodes": page_nodes, "edges": page_edges},
            "page": {
                "limit": limit,
                "cursor": params.get("cursor"),
                "next_cursor": self._encode_graph_cursor(next_offset, fingerprint) if has_more else None,
                "has_more": has_more,
            },
        }

    @staticmethod
    def _file_scope(relative_file: str) -> str:
        parts = {part.lower() for part in Path(relative_file).parts}
        if "win" in parts or "windows" in parts:
            return "windows"
        if "unix" in parts:
            return "unix"
        return "common"

    @classmethod
    def _configuration_scope(cls, configuration: dict) -> str:
        text = " ".join(
            str(configuration.get(key, ""))
            for key in ("id", "target", "compiler")
        ).lower()
        defines = configuration.get("defines")
        if isinstance(defines, dict):
            text += " " + " ".join(str(key).lower() for key in defines)
        if any(token in text for token in ("windows", "win32", "msvc", "mingw")):
            return "windows"
        if any(token in text for token in ("linux", "darwin", "macos", "macosx", "unix", "posix", "bsd")):
            return "unix"
        return "common"

    @classmethod
    def _configurations_for_file(cls, relative_file: str, resource: dict) -> list[str]:
        configurations = resource.get("configurations", [])
        if not isinstance(configurations, list):
            return []
        file_scope = cls._file_scope(relative_file)
        result = []
        for configuration in configurations:
            if not isinstance(configuration, dict) or not isinstance(configuration.get("id"), str):
                continue
            config_scope = cls._configuration_scope(configuration)
            if file_scope == "common" or config_scope == "common" or file_scope == config_scope:
                result.append(configuration["id"])
        return result

    @staticmethod
    def _graph_cursor_fingerprint(api_id: str, params: dict[str, str]) -> str:
        material = "\0".join([
            api_id,
            params.get("configuration_id", ""),
            params.get("node_kind", ""),
            params.get("edge_type", ""),
        ])
        return hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _encode_graph_cursor(offset: int, fingerprint: str) -> str:
        return f"cur_{offset}_{fingerprint}"

    @staticmethod
    def _decode_graph_cursor(cursor: str | None, fingerprint: str) -> int:
        if cursor is None:
            return 0
        if not isinstance(cursor, str):
            raise ValueError("cursor 格式无效")
        parts = cursor.split("_")
        if len(parts) != 3 or parts[0] != "cur" or parts[2] != fingerprint:
            raise ValueError("cursor 不属于当前分析或过滤条件")
        try:
            offset = int(parts[1])
        except ValueError as exc:
            raise ValueError("cursor 格式无效") from exc
        if offset < 0:
            raise ValueError("cursor 格式无效")
        return offset

    def api_query(self, body: dict) -> dict:
        api_id = body.get("analysis_id")
        if not isinstance(api_id, str) or api_id not in self.api_resources:
            raise KeyError(str(api_id))
        question = body.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question 不能为空")
        resource = self.api_resources[api_id]
        analysis = self.analyses[resource["_analysis_key"]]
        scope = body.get("scope") if isinstance(body.get("scope"), dict) else {}
        configuration_ids = scope.get("configuration_ids", [])
        if configuration_ids is None:
            configuration_ids = []
        if not isinstance(configuration_ids, list) or any(not isinstance(item, str) for item in configuration_ids):
            raise ValueError("scope.configuration_ids 必须是字符串数组")
        known_configurations = {
            item.get("id") for item in resource.get("configurations", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        unknown_configurations = set(configuration_ids) - known_configurations
        if unknown_configurations:
            raise ValueError(f"configuration_id 不属于分析 {api_id}：{sorted(unknown_configurations)[0]}")
        edge_types = scope.get("edge_types", [])
        if edge_types is None:
            edge_types = []
        if not isinstance(edge_types, list) or any(item not in GRAPH_EDGE_TYPES for item in edge_types):
            raise ValueError("scope.edge_types 包含无效边类型")
        direction = scope.get("direction", "both")
        if direction not in {"forward", "backward", "both"}:
            raise ValueError("scope.direction 无效")
        try:
            max_hops = int(scope.get("max_hops", 3))
        except (TypeError, ValueError) as exc:
            raise ValueError("scope.max_hops 必须是 0-8 的整数") from exc
        if not 0 <= max_hops <= 8:
            raise ValueError("scope.max_hops 必须是 0-8 的整数")

        raw_functions = {item["id"]: item for item in analysis["functions"]}
        raw_node_ids = {f"n_{item_id}" for item_id in raw_functions}
        requested_nodes = scope.get("node_ids", [])
        if requested_nodes is None:
            requested_nodes = []
        if not isinstance(requested_nodes, list) or any(item not in raw_node_ids for item in requested_nodes):
            raise ValueError("scope.node_ids 必须属于当前分析")

        def edge_allowed(edge: dict) -> bool:
            if edge_types and edge["type"] not in edge_types:
                return False
            if not configuration_ids:
                return True
            values = edge.get("configurations")
            if values is None:
                return True
            if isinstance(values, tuple):
                values = list(values)
            return bool(set(values) & set(configuration_ids))

        scoped_edges = [edge for edge in analysis["edges"] if edge_allowed(edge)]
        # Keep the language answer grounded in exactly the same edge slice.
        scoped_analysis = dict(analysis)
        scoped_analysis["edges"] = scoped_edges
        reply = self.explainer.answer(scoped_analysis, question)
        reply_focus = [item for item in reply.get("focus", []) if item in raw_functions]
        focus = [item.removeprefix("n_") for item in requested_nodes] if requested_nodes else reply_focus
        if not focus:
            focus = list(analysis.get("entry_points", []))[:4]

        edge_ids: dict[tuple[str, str, str, int], str] = {}
        public_edges: list[dict] = []
        for edge in scoped_edges:
            key = (edge["source"], edge["target"], edge["type"], edge["line"])
            public_id = "e_" + hashlib.sha1(
                f"{api_id}:{edge['source']}:{edge['target']}:{edge['type']}:{edge['line']}".encode("utf-8")
            ).hexdigest()[:14]
            edge_ids[key] = public_id
            public_edges.append({**edge, "edge_id": public_id})
        adjacency: dict[str, list[tuple[str, dict]]] = defaultdict(list)
        for edge in public_edges:
            if direction in {"forward", "both"}:
                adjacency[edge["source"]].append((edge["target"], edge))
            if direction in {"backward", "both"}:
                adjacency[edge["target"]].append((edge["source"], edge))
        paths: list[dict] = []
        for start in focus:
            queue = deque([(start, [start], [])])
            while queue and len(paths) < 20:
                node, node_path, path_edges = queue.popleft()
                if path_edges:
                    paths.append({
                        "label": " -> ".join(raw_functions[item]["name"] for item in node_path),
                        "node_ids": [f"n_{item}" for item in node_path],
                        "edge_ids": [edge["edge_id"] for edge in path_edges],
                    })
                if len(path_edges) >= max_hops:
                    continue
                for target, edge in adjacency.get(node, []):
                    if target in node_path:
                        continue
                    queue.append((target, node_path + [target], path_edges + [edge]))
        include_source = body.get("include_source", True)
        citations = []
        if include_source:
            seen_citations = set()
            for edge in public_edges:
                evidence = edge.get("evidence", "")
                if not evidence:
                    continue
                marker = (edge["file"], edge["line"], evidence)
                if marker in seen_citations:
                    continue
                seen_citations.add(marker)
                citations.append({
                    "file": edge["file"],
                    "line": edge["line"],
                    "text": evidence,
                    "edge_id": edge["edge_id"],
                })
                if len(citations) >= 32:
                    break
        unresolved = analysis.get("unresolved_calls", [])
        query_id = "q_" + hashlib.sha1(
            json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:14]
        path_confidences = [
            min((edge["confidence"] for edge in public_edges if edge["edge_id"] in path["edge_ids"]), default=0.0)
            for path in paths
        ]
        return {
            "resource_type": "query",
            "schema_version": "1.0",
            "query_id": query_id,
            "analysis_id": api_id,
            "answer": reply["answer"],
            "confidence": min(path_confidences) if path_confidences else (0.82 if citations else 0.55),
            "focus": list(dict.fromkeys(f"n_{item}" for item in focus)),
            "paths": paths,
            "citations": citations,
            "uncertainty": {
                "has_unresolved": bool(unresolved),
                "items": [f"未解析调用：{item['name']}（{item['file']}:{item['line']}）" for item in unresolved[:8]],
            },
        }

    @staticmethod
    def _public_resource(resource: dict) -> dict:
        return {key: value for key, value in resource.items() if not key.startswith("_")}

    def _resolve_target(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = self.workspace / path
        path = path.resolve()
        try:
            path.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError(f"只能分析工作区内的路径：{self.workspace}") from exc
        return path


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "CodeReverseAgent/0.1"

    @property
    def state(self) -> DemoState:
        return self.server.state  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/v1/health":
            self._json({"status": "ok", "service": "code-reverse-agent", "schema_version": "1.0"})
            return
        parts = [part for part in path.split("/") if part]
        if len(parts) == 3 and parts[:2] == ["v1", "analyses"]:
            self._run_api(lambda: self.state.get_api_resource(parts[2]))
            return
        if len(parts) == 4 and parts[:2] == ["v1", "analyses"] and parts[3] == "graph":
            query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
            self._run_api(lambda: self.state.api_graph(parts[2], query))
            return
        if path == "/api/health":
            self._json({"status": "ok", "workspace": str(self.state.workspace)})
            return
        if path == "/api/demo":
            self._run_json(lambda: self.state.analyze())
            return
        self._serve_static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/v1/analyses":
            def create() -> dict:
                resource = self.state.create_api_analysis(self._body())
                self._response_location = f"/v1/analyses/{resource['analysis_id']}"
                return resource

            self._run_api(create, HTTPStatus.ACCEPTED)
            return
        if path == "/v1/queries":
            self._run_api(lambda: self.state.api_query(self._body()))
            return
        if path == "/api/analyze":
            self._run_json(lambda: self.state.analyze(self._body().get("path")))
            return
        if path == "/api/query":
            def query() -> dict:
                body = self._body()
                return self.state.query(str(body.get("analysis_id", "")), str(body.get("question", "")))

            self._run_json(query)
            return
        self._json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)

    def _run_api(self, action, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._response_location = None
        try:
            value = action()
            headers = {"Location": self._response_location} if self._response_location else None
            self._json(value, status, headers=headers)
        except KeyError as exc:
            missing = exc.args[0] if exc.args else ""
            self._json({"code": "NOT_FOUND", "message": f"资源不存在：{missing}", "retryable": False}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._json({"code": "INVALID_ARGUMENT", "message": str(exc), "retryable": False}, HTTPStatus.BAD_REQUEST)
        except OSError as exc:
            self._json({"code": "SOURCE_UNAVAILABLE", "message": str(exc), "retryable": True}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json({"code": "INTERNAL_ERROR", "message": str(exc), "retryable": True}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > 2_000_000:
            raise ValueError("请求体过大")
        raw = self.rfile.read(content_length)
        try:
            value = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise ValueError("请求不是合法 JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("请求必须是 JSON 对象")
        return value

    def _run_json(self, action) -> None:
        try:
            self._json(action())
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except OSError as exc:
            self._json({"error": f"无法读取目标：{exc}"}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # Keep the local demo responsive and report the failure.
            self._json({"error": f"分析失败：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path == "/" else unquote(request_path.lstrip("/"))
        candidate = (WEB_DIR / relative).resolve()
        try:
            candidate.relative_to(WEB_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        payload = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, value: dict, status: HTTPStatus = HTTPStatus.OK, headers: dict | None = None) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for key, header_value in (headers or {}).items():
            if header_value:
                self.send_header(key, header_value)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[web] {self.address_string()} {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="代码逆向 Agent 本地 Demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--workspace", default=str(APP_DIR.parent))
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    server.state = DemoState(Path(args.workspace))  # type: ignore[attr-defined]
    print(f"代码逆向 Agent 已启动：http://{args.host}:{args.port}")
    print(f"允许分析的工作区：{server.state.workspace}")  # type: ignore[attr-defined]
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
