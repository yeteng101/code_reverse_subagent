"""Lightweight, evidence-first C/C++ source analyzer used by the demo.

This intentionally avoids pretending to be a full compiler. It extracts a useful
subset of symbols and relationships, marks inferred edges with lower confidence,
and keeps source evidence for every conclusion.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
IGNORED_DIRS = {".git", ".svn", "build", "dist", "node_modules", "vendor", "third_party"}
CONTROL_WORDS = {"if", "for", "while", "switch", "catch", "sizeof", "alignof", "decltype", "return"}
CALL_IGNORES = CONTROL_WORDS | {
    "defined", "static_cast", "dynamic_cast", "reinterpret_cast", "const_cast",
    "new", "delete", "throw", "typeid", "noexcept", "assert",
}
ASYNC_HINTS = ("async", "dispatch", "post", "enqueue", "schedule", "submit", "thread", "task")
CALLBACK_HINTS = ("callback", "handler", "listener", "subscribe", "register", "connect", "then", "on_")


@dataclass(frozen=True)
class Function:
    id: str
    name: str
    qualified_name: str
    file: str
    line: int
    end_line: int
    signature: str
    module: str
    kind: str = "function"


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    type: str
    file: str
    line: int
    confidence: float
    evidence: str


@dataclass(frozen=True)
class Macro:
    name: str
    value: str
    file: str
    line: int
    conditional: bool = False


@dataclass
class ParsedFunction:
    function: Function
    body: str
    body_masked: str
    body_offset: int
    source: str


def _mask_comments_and_strings(source: str) -> str:
    """Replace comments/string contents with spaces while preserving newlines."""

    result = list(source)
    i = 0
    state = "code"
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                result[i] = result[i + 1] = " "
                i += 2
                state = "line_comment"
                continue
            if ch == "/" and nxt == "*":
                result[i] = result[i + 1] = " "
                i += 2
                state = "block_comment"
                continue
            if ch in ('"', "'"):
                result[i] = " "
                quote = ch
                state = f"string:{quote}"
                i += 1
                continue
        elif state == "line_comment":
            if ch == "\n":
                state = "code"
            else:
                result[i] = " "
        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                result[i] = result[i + 1] = " "
                i += 2
                state = "code"
                continue
            if ch != "\n":
                result[i] = " "
        else:
            quote = state[-1]
            if ch == "\\":
                result[i] = " "
                if i + 1 < len(source) and source[i + 1] != "\n":
                    result[i + 1] = " "
                i += 2
                continue
            if ch == quote:
                result[i] = " "
                state = "code"
            elif ch != "\n":
                result[i] = " "
        i += 1
    return "".join(result)


def _matching_brace(masked: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(masked)):
        if masked[index] == "{":
            depth += 1
        elif masked[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _matching_paren(masked: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(masked)):
        if masked[index] == "(":
            depth += 1
        elif masked[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _line_number(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _line_text(source: str, line: int) -> str:
    lines = source.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()[:240]
    return ""


def _symbol_id(relative_file: str, qualified_name: str, line: int) -> str:
    raw = f"{relative_file}:{qualified_name}:{line}".encode("utf-8")
    return "fn_" + hashlib.sha1(raw).hexdigest()[:12]


def _module_for(relative_file: str) -> str:
    parts = Path(relative_file).parts
    if len(parts) > 1:
        return parts[0]
    return "root"


FUNCTION_RE = re.compile(
    r"(?P<prefix>(?:[A-Za-z_~][\w:<>,*&\[\]\s]*?\s+)?)"
    r"(?P<name>(?:[A-Za-z_]\w*::)*~?[A-Za-z_]\w*)\s*"
    r"\((?P<params>[^;{}()]*(?:\([^;{}()]*\)[^;{}()]*)*)\)\s*"
    r"(?:const\s*)?(?:noexcept(?:\s*\([^)]*\))?\s*)?(?:override\s*)?(?:final\s*)?"
    r"(?:->[^{;]+)?\s*\{",
    re.MULTILINE,
)

LAMBDA_RE = re.compile(
    r"\bauto\s+(?P<name>[A-Za-z_]\w*)\s*=\s*\[[^\]]*\]\s*"
    r"(?:\((?P<params>[^)]*)\))?\s*(?:mutable\s*)?(?:->[^{]+)?\{",
    re.MULTILINE,
)


class CodeAnalyzer:
    """Analyze a C/C++ file or directory into a small evidence graph."""

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()

    def analyze(self, target: Path) -> dict:
        target = target.resolve()
        files = list(self._source_files(target))
        parsed: list[ParsedFunction] = []
        macros: list[Macro] = []
        file_sources: dict[str, str] = {}

        for file_path in files:
            relative = self._relative(file_path)
            source = file_path.read_text(encoding="utf-8", errors="replace")
            file_sources[relative] = source
            parsed.extend(self._parse_functions(source, relative))
            macros.extend(self._parse_macros(source, relative))

        edges, unresolved = self._build_edges(parsed)
        functions = [item.function for item in parsed]
        modules = self._modules(functions, files)
        entry_points = self._entry_points(functions, edges)

        result = {
            "target": str(target),
            "language": "C/C++",
            "files": [self._relative(path) for path in files],
            "functions": [asdict(item) for item in functions],
            "edges": [asdict(item) for item in edges],
            "macros": [asdict(item) for item in macros],
            "modules": modules,
            "entry_points": entry_points,
            "unresolved_calls": unresolved[:100],
            "limitations": [
                "Demo 使用轻量语法分析，不等同于 Clang 完整语义分析",
                "跨翻译单元类型推导、模板实例化和复杂别名可能不完整",
                "低置信度关系需要结合编译配置或动态轨迹复核",
            ],
        }
        result["summary"] = self._summary(result)
        result["analysis_id"] = hashlib.sha1(
            json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        return result

    def _source_files(self, target: Path) -> Iterable[Path]:
        if not target.exists():
            raise ValueError(f"路径不存在：{target}")
        if target.is_file():
            if target.suffix.lower() not in SOURCE_SUFFIXES:
                raise ValueError("Demo 当前只支持 C/C++ 源文件")
            yield target
            return
        for path in sorted(target.rglob("*")):
            if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES:
                if not any(part in IGNORED_DIRS for part in path.parts):
                    yield path

    def _relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.workspace).as_posix()
        except ValueError:
            return path.name

    def _parse_functions(self, source: str, relative_file: str) -> list[ParsedFunction]:
        masked = _mask_comments_and_strings(source)
        candidates: list[tuple[int, re.Match[str], str]] = []
        for match in FUNCTION_RE.finditer(masked):
            short_name = match.group("name").split("::")[-1]
            if short_name not in CONTROL_WORDS:
                candidates.append((match.start(), match, "function"))
        for match in LAMBDA_RE.finditer(masked):
            candidates.append((match.start(), match, "lambda"))

        parsed: list[ParsedFunction] = []
        occupied: list[tuple[int, int]] = []
        for _, match, kind in sorted(candidates, key=lambda item: item[0]):
            opening = match.end() - 1
            closing = _matching_brace(masked, opening)
            if closing is None:
                continue
            if any(start <= match.start() < end for start, end in occupied) and kind != "lambda":
                continue
            name = match.group("name")
            qualified = name
            line = _line_number(source, match.start())
            end_line = _line_number(source, closing)
            signature = " ".join(source[match.start():opening].split())[:300]
            function = Function(
                id=_symbol_id(relative_file, qualified, line),
                name=name.split("::")[-1],
                qualified_name=qualified,
                file=relative_file,
                line=line,
                end_line=end_line,
                signature=signature,
                module=_module_for(relative_file),
                kind=kind,
            )
            parsed.append(
                ParsedFunction(
                    function=function,
                    body=source[opening + 1:closing],
                    body_masked=masked[opening + 1:closing],
                    body_offset=opening + 1,
                    source=source,
                )
            )
            if kind == "function":
                occupied.append((match.start(), closing + 1))
        return parsed

    def _parse_macros(self, source: str, relative_file: str) -> list[Macro]:
        macros: list[Macro] = []
        conditional_names: set[str] = set()
        for line_no, line in enumerate(source.splitlines(), 1):
            conditional = re.match(r"\s*#\s*(?:ifn?def)\s+([A-Za-z_]\w*)", line)
            if conditional:
                conditional_names.add(conditional.group(1))
            for name in re.findall(r"defined\s*\(?\s*([A-Za-z_]\w*)", line):
                conditional_names.add(name)
            definition = re.match(r"\s*#\s*define\s+([A-Za-z_]\w*(?:\([^)]*\))?)\s*(.*)", line)
            if definition:
                raw_name = definition.group(1)
                base_name = raw_name.split("(", 1)[0]
                macros.append(
                    Macro(
                        name=raw_name,
                        value=definition.group(2).strip() or "1",
                        file=relative_file,
                        line=line_no,
                        conditional=base_name in conditional_names,
                    )
                )
        existing = {macro.name.split("(", 1)[0] for macro in macros}
        for name in sorted(conditional_names - existing):
            macros.append(Macro(name=name, value="构建参数", file=relative_file, line=1, conditional=True))
        return macros

    def _build_edges(self, parsed: list[ParsedFunction]) -> tuple[list[Edge], list[dict]]:
        by_short: dict[str, list[Function]] = defaultdict(list)
        by_qualified: dict[str, Function] = {}
        for item in parsed:
            by_short[item.function.name].append(item.function)
            by_qualified[item.function.qualified_name] = item.function

        edges: list[Edge] = []
        unresolved: list[dict] = []
        seen: set[tuple[str, str, str, int]] = set()
        for item in parsed:
            pointer_targets = self._pointer_assignments(item.body_masked, by_short)
            calls = re.finditer(r"(?<![#.])\b((?:[A-Za-z_]\w*::)*[A-Za-z_]\w*)\s*\(", item.body_masked)
            for call in calls:
                raw_name = call.group(1)
                short_name = raw_name.split("::")[-1]
                if short_name in CALL_IGNORES or short_name == item.function.name:
                    continue
                absolute = item.body_offset + call.start(1)
                line = _line_number(item.source, absolute)
                targets = []
                edge_type = "direct"
                confidence = 0.96
                if raw_name in by_qualified:
                    targets = [by_qualified[raw_name]]
                elif short_name in by_short:
                    targets = by_short[short_name]
                    if len(targets) > 1:
                        confidence = 0.68
                elif short_name in pointer_targets:
                    targets = pointer_targets[short_name]
                    edge_type = "function_pointer"
                    confidence = 0.78

                lowered = short_name.lower()
                hinted_type = "async" if any(hint in lowered for hint in ASYNC_HINTS) else "callback"
                hints = ASYNC_HINTS if hinted_type == "async" else CALLBACK_HINTS
                opening = call.end() - 1
                closing = _matching_paren(item.body_masked, opening)
                arguments = item.body_masked[opening + 1:closing] if closing is not None else ""
                if short_name not in pointer_targets and any(hint in lowered for hint in hints):
                    for candidate_name, candidate_functions in by_short.items():
                        if re.search(rf"(?:&\s*)?\b{re.escape(candidate_name)}\b", arguments):
                            for target in candidate_functions:
                                if target.id != item.function.id:
                                    self._append_edge(
                                        edges, seen, item.function, target, hinted_type,
                                        item, line, 0.82, _line_text(item.source, line),
                                    )

                for target in targets:
                    self._append_edge(
                        edges, seen, item.function, target, edge_type,
                        item, line, confidence, _line_text(item.source, line),
                    )
                if not targets and short_name not in by_short and "::" not in raw_name:
                    unresolved.append({
                        "source": item.function.id,
                        "name": short_name,
                        "file": item.function.file,
                        "line": line,
                    })

            for variable, targets in pointer_targets.items():
                if re.search(rf"\b{re.escape(variable)}\s*\(", item.body_masked):
                    for target in targets:
                        invoke = re.search(rf"\b{re.escape(variable)}\s*\(", item.body_masked)
                        if invoke:
                            line = _line_number(item.source, item.body_offset + invoke.start())
                            self._append_edge(
                                edges, seen, item.function, target, "function_pointer",
                                item, line, 0.84, _line_text(item.source, line),
                            )
        return edges, unresolved

    @staticmethod
    def _pointer_assignments(body: str, by_short: dict[str, list[Function]]) -> dict[str, list[Function]]:
        result: dict[str, list[Function]] = {}
        patterns = [
            r"\(\s*\*\s*(?P<var>[A-Za-z_]\w*)\s*\)\s*\([^;=]*\)\s*=\s*&?\s*(?P<target>[A-Za-z_]\w*)",
            r"\b(?P<var>[A-Za-z_]\w*)\s*=\s*&\s*(?P<target>[A-Za-z_]\w*)",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, body):
                target_name = match.group("target")
                if target_name in by_short:
                    result[match.group("var")] = by_short[target_name]
        return result

    @staticmethod
    def _append_edge(
        edges: list[Edge],
        seen: set[tuple[str, str, str, int]],
        source: Function,
        target: Function,
        edge_type: str,
        parsed: ParsedFunction,
        line: int,
        confidence: float,
        evidence: str,
    ) -> None:
        key = (source.id, target.id, edge_type, line)
        if key in seen:
            return
        seen.add(key)
        edges.append(
            Edge(
                source=source.id,
                target=target.id,
                type=edge_type,
                file=parsed.function.file,
                line=line,
                confidence=confidence,
                evidence=evidence,
            )
        )

    def _modules(self, functions: list[Function], files: list[Path]) -> list[dict]:
        grouped: dict[str, dict] = {}
        for path in files:
            relative = self._relative(path)
            module = _module_for(relative)
            grouped.setdefault(module, {"name": module, "files": 0, "functions": 0})["files"] += 1
        for function in functions:
            grouped.setdefault(function.module, {"name": function.module, "files": 0, "functions": 0})["functions"] += 1
        return sorted(grouped.values(), key=lambda item: item["name"])

    @staticmethod
    def _entry_points(functions: list[Function], edges: list[Edge]) -> list[str]:
        incoming = {edge.target for edge in edges}
        main = [fn.id for fn in functions if fn.name in {"main", "WinMain", "DllMain"}]
        if main:
            return main
        return [fn.id for fn in functions if fn.id not in incoming][:8]

    @staticmethod
    def _summary(result: dict) -> dict:
        counts = defaultdict(int)
        for edge in result["edges"]:
            counts[edge["type"]] += 1
        return {
            "file_count": len(result["files"]),
            "function_count": len(result["functions"]),
            "edge_count": len(result["edges"]),
            "macro_count": len(result["macros"]),
            "direct_calls": counts["direct"],
            "async_calls": counts["async"],
            "callback_calls": counts["callback"],
            "function_pointer_calls": counts["function_pointer"],
        }


class AnalysisExplainer:
    """Offline natural-language explainer grounded in an analysis result."""

    def answer(self, analysis: dict, question: str) -> dict:
        question = question.strip()
        lowered = question.lower()
        functions = {item["id"]: item for item in analysis["functions"]}
        edges = analysis["edges"]
        citations: list[dict] = []

        matched = next(
            (fn for fn in functions.values() if fn["name"].lower() in lowered and len(fn["name"]) > 2),
            None,
        )
        if matched:
            related = [edge for edge in edges if matched["id"] in (edge["source"], edge["target"])]
            outgoing = [functions[edge["target"]]["qualified_name"] for edge in related if edge["source"] == matched["id"]]
            incoming = [functions[edge["source"]]["qualified_name"] for edge in related if edge["target"] == matched["id"]]
            citations = self._citations(related)
            answer = (
                f"`{matched['qualified_name']}` 定义在 {matched['file']}:{matched['line']}。"
                f"它调用 {self._names(outgoing)}；调用它的函数有 {self._names(incoming)}。"
            )
            return {"answer": answer, "citations": citations, "focus": [matched["id"]]}

        if any(word in lowered for word in ("异步", "回调", "async", "callback")):
            selected = [edge for edge in edges if edge["type"] in {"async", "callback"}]
            return self._edge_answer("异步与回调", selected, functions)
        if any(word in lowered for word in ("函数指针", "指针", "pointer")):
            selected = [edge for edge in edges if edge["type"] == "function_pointer"]
            return self._edge_answer("函数指针", selected, functions)
        if any(word in lowered for word in ("宏", "条件编译", "macro")):
            macros = analysis["macros"]
            names = "、".join(f"`{item['name']}`" for item in macros[:10]) or "未发现"
            citations = [{"file": item["file"], "line": item["line"], "evidence": f"#define {item['name']} {item['value']}"} for item in macros[:8]]
            return {"answer": f"共发现 {len(macros)} 个宏或构建条件：{names}。", "citations": citations, "focus": []}
        if any(word in lowered for word in ("入口", "调用链", "main", "流程")):
            chains = self._entry_chains(analysis, functions)
            answer = "主要入口链：\n" + "\n".join(f"{index + 1}. {chain}" for index, chain in enumerate(chains))
            chain_edges = [edge for edge in edges if edge["source"] in analysis["entry_points"]]
            return {"answer": answer, "citations": self._citations(chain_edges), "focus": analysis["entry_points"]}

        summary = analysis["summary"]
        modules = "、".join(item["name"] for item in analysis["modules"])
        answer = (
            f"该目标包含 {summary['file_count']} 个 C/C++ 文件、{summary['function_count']} 个函数和 "
            f"{summary['edge_count']} 条已解析关系。模块包括 {modules or 'root'}。"
            f"其中直接调用 {summary['direct_calls']} 条，异步/回调 "
            f"{summary['async_calls'] + summary['callback_calls']} 条，函数指针 {summary['function_pointer_calls']} 条。"
        )
        return {"answer": answer, "citations": [], "focus": analysis["entry_points"]}

    def _edge_answer(self, title: str, edges: list[dict], functions: dict[str, dict]) -> dict:
        if not edges:
            return {"answer": f"当前轻量分析没有发现{title}关系。", "citations": [], "focus": []}
        descriptions = [
            f"`{functions[edge['source']]['qualified_name']}` -> `{functions[edge['target']]['qualified_name']}`"
            f"（置信度 {edge['confidence']:.0%}）"
            for edge in edges[:8]
        ]
        focus = list(dict.fromkeys([value for edge in edges for value in (edge["source"], edge["target"])]))
        return {
            "answer": f"发现 {len(edges)} 条{title}关系：\n" + "\n".join(f"{i + 1}. {text}" for i, text in enumerate(descriptions)),
            "citations": self._citations(edges),
            "focus": focus,
        }

    @staticmethod
    def _citations(edges: list[dict]) -> list[dict]:
        return [
            {"file": edge["file"], "line": edge["line"], "evidence": edge["evidence"]}
            for edge in edges[:8]
        ]

    @staticmethod
    def _names(names: list[str]) -> str:
        unique = list(dict.fromkeys(names))
        return "、".join(f"`{name}`" for name in unique) if unique else "无已解析目标"

    @staticmethod
    def _entry_chains(analysis: dict, functions: dict[str, dict]) -> list[str]:
        adjacency: dict[str, list[str]] = defaultdict(list)
        for edge in analysis["edges"]:
            adjacency[edge["source"]].append(edge["target"])
        chains: list[str] = []
        for entry in analysis["entry_points"][:4]:
            queue = deque([(entry, [entry])])
            longest = [entry]
            while queue:
                node, path = queue.popleft()
                if len(path) > len(longest):
                    longest = path
                if len(path) >= 7:
                    continue
                for target in adjacency[node]:
                    if target not in path:
                        queue.append((target, path + [target]))
            chains.append(" -> ".join(functions[node]["qualified_name"] for node in longest))
        return chains or ["未识别出入口函数"]
