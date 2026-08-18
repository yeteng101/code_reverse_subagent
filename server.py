"""Local web server for the code reverse-agent demo."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from analyzer import AnalysisExplainer, CodeAnalyzer


APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
SAMPLE_FILE = APP_DIR / "samples" / "async_pipeline.cpp"


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
        analysis = self.analyze(raw_path)
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        api_id = f"an_{analysis['analysis_id']}"
        build = body.get("build") if isinstance(body.get("build"), dict) else {}
        options = body.get("analysis") if isinstance(body.get("analysis"), dict) else {}
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
        nodes = []
        for fn in functions:
            kind = "function" if fn.get("kind") == "lambda" else "function"
            if selected_kind and selected_kind != kind:
                continue
            nodes.append({
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
                "attributes": {"signature": fn["signature"], "entry_point": fn["id"] in analysis["entry_points"]},
            })
        edges = []
        config_ids = [item["id"] for item in resource.get("configurations", []) if isinstance(item, dict) and item.get("id")]
        for edge in analysis["edges"]:
            if selected_edge and edge["type"] != selected_edge:
                continue
            if edge["source"] not in node_ids or edge["target"] not in node_ids:
                continue
            edge_id = "e_" + hashlib.sha1(
                f"{api_id}:{edge['source']}:{edge['target']}:{edge['type']}:{edge['line']}".encode("utf-8")
            ).hexdigest()[:14]
            semantics = "dispatch" if edge["type"] in {"async", "callback", "function_pointer"} else "call"
            resolution = "observed" if edge["type"] == "direct" else "inferred"
            edges.append({
                "edge_id": edge_id,
                "source": node_ids[edge["source"]],
                "target": node_ids[edge["target"]],
                "type": edge["type"] if edge["type"] in {"direct", "async", "callback", "function_pointer"} else "unresolved",
                "semantics": semantics,
                "resolution": resolution,
                "confidence": edge["confidence"],
                "evidence": [{
                    "kind": "source",
                    "location": {"file": edge["file"], "start_line": edge["line"]},
                    "text": edge["evidence"],
                }],
                "configurations": config_ids,
            })
        try:
            limit = max(1, min(5000, int(params.get("limit", "500"))))
        except ValueError:
            limit = 500
        return {
            "resource_type": "graph",
            "schema_version": "1.0",
            "analysis_id": api_id,
            "graph": {"nodes": nodes[:limit], "edges": edges[:limit]},
            "page": {"limit": limit, "cursor": params.get("cursor"), "next_cursor": None, "has_more": False},
        }

    def api_query(self, body: dict) -> dict:
        api_id = body.get("analysis_id")
        if not isinstance(api_id, str) or api_id not in self.api_resources:
            raise KeyError(str(api_id))
        question = body.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question 不能为空")
        resource = self.api_resources[api_id]
        reply = self.query(resource["_analysis_key"], question)
        query_id = "q_" + hashlib.sha1(f"{api_id}:{question}".encode("utf-8")).hexdigest()[:14]
        citations = []
        for citation in reply.get("citations", []):
            citations.append({
                "file": citation["file"],
                "line": citation["line"],
                "text": citation.get("evidence", ""),
            })
        unresolved = self.analyses[resource["_analysis_key"]].get("unresolved_calls", [])
        return {
            "resource_type": "query",
            "schema_version": "1.0",
            "query_id": query_id,
            "analysis_id": api_id,
            "answer": reply["answer"],
            "confidence": 0.82 if citations else 0.55,
            "focus": [f"n_{item.removeprefix('n_')}" if item.startswith("n_") else f"n_{item}" for item in reply.get("focus", [])],
            "paths": [],
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
