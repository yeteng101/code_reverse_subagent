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
from dataclasses import asdict, dataclass, replace
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
DOMAIN_CALLBACK_APIS = {
    "libuv": {"uv_read_start": {1: "alloc_cb", 2: "read_cb"}, "uv_close": {1: "close_cb"},
              "uv_timer_start": {1: "timer_cb"}, "uv_async_init": {2: "async_cb"},
              "uv_queue_work": {2: "work_cb", 3: "after_work_cb"}},
    "redis": {"aeCreateFileEvent": {3: "rfileProc"},
              "aeCreateTimeEvent": {2: "timeProc", 4: "finalizerProc"},
              "RedisModule_CreateCommand": {2: "module_command"}},
}
CALLBACK_SLOT_NAMES = {
    "alloc_cb", "read_cb", "close_cb", "timer_cb", "async_cb", "work_cb", "after_work_cb",
    "rfileProc", "wfileProc", "timeProc", "finalizerProc", "file_proc", "time_proc",
    "finalizer_proc", "module_command",
}
DOMAIN_SEMANTIC_CALLS = {
    "libuv": {("uv_run", "uv__io_poll"): "scheduled_by",
              ("uv__io_poll", "uv__io_cb"): "scheduled_by",
              ("uv__io_cb", "uv__stream_io"): "invokes_callback",
              ("uv__stream_io", "uv__read"): "scheduled_by"},
    "redis": {("aeMain", "aeProcessEvents"): "scheduled_by",
              ("aeProcessEvents", "processTimeEvents"): "scheduled_by"},
}


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
    configurations: tuple[str, ...] = ()


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


def _split_arguments(arguments: str) -> list[str]:
    """Split a call argument list without breaking nested expressions."""

    result: list[str] = []
    start = 0
    depths = {"(": 0, "[": 0, "{": 0}
    closing = {")": "(", "]": "[", "}": "{"}
    for index, character in enumerate(arguments):
        if character in depths:
            depths[character] += 1
        elif character in closing:
            opener = closing[character]
            depths[opener] = max(0, depths[opener] - 1)
        elif character == "," and not any(depths.values()):
            result.append(arguments[start:index].strip())
            start = index + 1
    result.append(arguments[start:].strip())
    return result


def _looks_like_callback(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(("_cb", "callback", "handler", "listener", "proc"))


def _symbol_id(relative_file: str, qualified_name: str, line: int) -> str:
    raw = f"{relative_file}:{qualified_name}:{line}".encode("utf-8")
    return "fn_" + hashlib.sha1(raw).hexdigest()[:12]


def _module_for(relative_file: str) -> str:
    parts = Path(relative_file).parts
    if len(parts) > 1:
        return parts[0]
    return "root"


def _platform_scope(relative_file: str) -> str:
    """Return the build-domain scope encoded by the repository layout."""

    parts = {part.lower() for part in Path(relative_file).parts}
    if "win" in parts or "windows" in parts:
        return "windows"
    if "unix" in parts:
        return "unix"
    return "common"


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

    def analyze(
        self,
        target: Path,
        *,
        configurations: list[dict] | None = None,
        identity: object | None = None,
    ) -> dict:
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

        profile_kind = self._profile_kind(target, parsed)
        edges, unresolved, callback_slots = self._build_edges(parsed, profile_kind)
        functions = [item.function for item in parsed]
        modules = self._modules(functions, files)
        entry_points = self._entry_points(functions, edges)

        configuration_list = [item for item in (configurations or []) if isinstance(item, dict)]
        edges = self._apply_configurations(edges, parsed, configuration_list)
        result = {
            "target": str(target),
            "language": "C/C++",
            "files": [self._relative(path) for path in files],
            "functions": [asdict(item) for item in functions],
            "edges": [asdict(item) for item in edges],
            "macros": [asdict(item) for item in macros],
            "modules": modules,
            "entry_points": entry_points,
            "profile": self._profile(
                target,
                profile_kind,
                functions,
                callback_slots,
                bool(configuration_list),
            ),
            "callback_slots": callback_slots,
            "configuration_ids": [
                item["id"] for item in configuration_list
                if isinstance(item.get("id"), str)
            ],
            "unresolved_calls": unresolved[:100],
            "limitations": [
                "Demo 使用轻量语法分析，不等同于 Clang 完整语义分析",
                "跨翻译单元类型推导、模板实例化和复杂别名可能不完整",
                *([] if configuration_list else ["未提供编译配置；平台关系暂按工作区源码推断"]),
                "低置信度关系需要结合编译配置或动态轨迹复核",
            ],
        }
        result["summary"] = self._summary(result)
        result["analysis_id"] = hashlib.sha1(
            json.dumps(
                {"result": result, "identity": identity},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:12]
        return result

    @staticmethod
    def _configuration_scope(configuration: dict) -> str:
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
    def _apply_configurations(
        cls,
        edges: list[Edge],
        parsed: list[ParsedFunction],
        configurations: list[dict],
    ) -> list[Edge]:
        if not configurations:
            return edges
        functions = {item.function.id: item.function for item in parsed}
        result: list[Edge] = []
        for edge in edges:
            source = functions.get(edge.source)
            target = functions.get(edge.target)
            scopes = {
                _platform_scope(item.file)
                for item in (source, target)
                if item is not None
            } or {"common"}
            applicable: list[str] = []
            for configuration in configurations:
                config_id = configuration.get("id")
                if not isinstance(config_id, str):
                    continue
                config_scope = cls._configuration_scope(configuration)
                if "common" in scopes or config_scope == "common" or config_scope in scopes:
                    applicable.append(config_id)
            result.append(replace(edge, configurations=tuple(applicable)))
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
        logical_lines: list[tuple[int, str]] = []
        physical = source.splitlines()
        index = 0
        while index < len(physical):
            start_line = index + 1
            parts = [physical[index]]
            while parts[-1].rstrip().endswith("\\") and index + 1 < len(physical):
                parts[-1] = parts[-1].rstrip()[:-1]
                index += 1
                parts.append(physical[index])
            logical_lines.append((start_line, " ".join(part.strip() for part in parts)))
            index += 1

        conditional_names: set[str] = set()
        for _, line in logical_lines:
            directive = re.match(r"\s*#\s*(?:if|elif|ifdef|ifndef)\b(?P<expr>.*)", line)
            if directive:
                conditional_names.update(re.findall(r"\b[A-Za-z_]\w*\b", directive.group("expr")))
                conditional_names.discard("defined")

        macros: list[Macro] = []
        conditional_depth = 0
        for line_no, line in logical_lines:
            if re.match(r"\s*#\s*(?:if|ifdef|ifndef)\b", line):
                conditional_depth += 1
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
                        conditional=base_name in conditional_names or conditional_depth > 0,
                    )
                )
            if re.match(r"\s*#\s*endif\b", line):
                conditional_depth = max(0, conditional_depth - 1)
        existing = {macro.name.split("(", 1)[0] for macro in macros}
        for name in sorted(conditional_names - existing):
            macros.append(Macro(name=name, value="构建参数", file=relative_file, line=1, conditional=True))
        return macros

    def _build_edges(
        self, parsed: list[ParsedFunction], profile_kind: str
    ) -> tuple[list[Edge], list[dict], list[dict]]:
        by_short: dict[str, list[Function]] = defaultdict(list)
        by_qualified: dict[str, list[Function]] = defaultdict(list)
        for item in parsed:
            by_short[item.function.name].append(item.function)
            by_qualified[item.function.qualified_name].append(item.function)

        edges: list[Edge] = []
        unresolved: list[dict] = []
        seen: set[tuple[str, str, str, int]] = set()
        for item in parsed:
            pointer_targets, pointer_ambiguities = self._pointer_assignments(
                item.body_masked, by_short, item.function
            )
            for ambiguity in pointer_ambiguities:
                unresolved.append({
                    "source": item.function.id,
                    "name": ambiguity["target"],
                    "file": item.function.file,
                    "line": _line_number(item.source, item.body_offset + ambiguity["offset"]),
                    "reason": "ambiguous_function_pointer_target",
                    "candidates": ambiguity["candidates"],
                })
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
                candidates = (
                    by_qualified.get(raw_name, [])
                    if "::" in raw_name
                    else by_short.get(short_name, [])
                )
                if candidates:
                    targets, confidence = self._scoped_candidates(item.function, candidates)
                    if not targets or len(targets) > 1:
                        unresolved.append({
                            "source": item.function.id,
                            "name": short_name,
                            "file": item.function.file,
                            "line": line,
                            "reason": "ambiguous_call_target",
                            "candidates": self._candidate_evidence(candidates),
                        })
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
                if not targets and not candidates and short_name not in pointer_targets and "::" not in raw_name:
                    unresolved.append({
                        "source": item.function.id,
                        "name": short_name,
                        "file": item.function.file,
                        "line": line,
                        "reason": "target_not_found",
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
        self._append_semantic_call_edges(edges, seen, parsed, profile_kind)
        callback_slots = self._callback_slots(parsed)
        self._append_registered_callback_edges(
            edges, seen, unresolved, parsed, by_short, callback_slots, profile_kind
        )
        return edges, unresolved, callback_slots

    def _append_semantic_call_edges(
        self,
        edges: list[Edge],
        seen: set[tuple[str, str, str, int]],
        parsed: list[ParsedFunction],
        profile_kind: str,
    ) -> None:
        rules = DOMAIN_SEMANTIC_CALLS.get(profile_kind, {})
        parsed_by_id = {item.function.id: item for item in parsed}
        functions_by_id = {item.function.id: item.function for item in parsed}
        for edge in list(edges):
            source = functions_by_id[edge.source]
            target = functions_by_id[edge.target]
            edge_type = rules.get((source.name, target.name))
            if edge_type:
                self._append_edge(
                    edges, seen, source, target, edge_type, parsed_by_id[source.id],
                    edge.line, min(edge.confidence, 0.94), edge.evidence,
                )

    def _append_registered_callback_edges(
        self,
        edges: list[Edge],
        seen: set[tuple[str, str, str, int]],
        unresolved: list[dict],
        parsed: list[ParsedFunction],
        by_short: dict[str, list[Function]],
        callback_slots: list[dict],
        profile_kind: str,
    ) -> None:
        apis = DOMAIN_CALLBACK_APIS.get(profile_kind, {})
        if not apis:
            return
        slot_targets: dict[tuple[str, str], list[Function]] = defaultdict(list)
        for item in parsed:
            for call in re.finditer(r"(?<![#.])\b((?:[A-Za-z_]\w*::)*[A-Za-z_]\w*)\s*\(", item.body_masked):
                api_name = call.group(1).split("::")[-1]
                callback_args = apis.get(api_name)
                if not callback_args:
                    continue
                opening = call.end() - 1
                closing = _matching_paren(item.body_masked, opening)
                if closing is None:
                    continue
                arguments = _split_arguments(item.body_masked[opening + 1:closing])
                line = _line_number(item.source, item.body_offset + call.start(1))
                for argument_index, slot_name in callback_args.items():
                    if argument_index >= len(arguments):
                        continue
                    argument = arguments[argument_index]
                    candidate_names = set(re.findall(r"\b[A-Za-z_]\w*\b", argument)) & by_short.keys()
                    for name in candidate_names:
                        targets, confidence = self._scoped_candidates(item.function, by_short[name])
                        if len(targets) != 1:
                            unresolved.append({
                                "source": item.function.id,
                                "name": name,
                                "file": item.function.file,
                                "line": line,
                                "reason": "ambiguous_callback_target",
                                "candidates": self._candidate_evidence(by_short[name]),
                            })
                            # A callback slot is shared by many unrelated structs in
                            # large C repositories.  Do not manufacture a Cartesian
                            # product when the argument cannot be scoped uniquely.
                            continue
                        for target in targets:
                            if target.id == item.function.id:
                                continue
                            scope_key = (slot_name, _platform_scope(item.function.file))
                            if target not in slot_targets[scope_key]:
                                slot_targets[scope_key].append(target)
                            self._append_edge(
                                edges, seen, item.function, target, "registers_callback", item,
                                line, min(confidence, 0.94), _line_text(item.source, line),
                            )

        parsed_by_id = {item.function.id: item for item in parsed}
        for slot in callback_slots:
            for site in slot["invocations"]:
                item = parsed_by_id[site["function_id"]]
                invocation_scope = _platform_scope(item.function.file)
                compatible_scopes = {invocation_scope, "common"}
                if invocation_scope == "common":
                    compatible_scopes = {"common"}
                candidates = {
                    target
                    for scope in compatible_scopes
                    for target in slot_targets.get((slot["slot"], scope), [])
                }
                if len(candidates) != 1:
                    if candidates:
                        unresolved.append({
                            "source": site["function_id"],
                            "name": slot["slot"],
                            "file": site["file"],
                            "line": site["line"],
                            "reason": "ambiguous_callback_slot",
                            "candidates": self._candidate_evidence(candidates),
                        })
                    continue
                for invocation in candidates:
                    self._append_edge(
                        edges, seen, item.function, invocation, "invokes_callback", item,
                        site["line"], 0.86, site["evidence"],
                    )

    @staticmethod
    def _callback_slots(parsed: list[ParsedFunction]) -> list[dict]:
        slots: dict[str, dict] = {}

        def record(name: str, role: str, item: ParsedFunction, offset: int) -> None:
            line = _line_number(item.source, item.body_offset + offset)
            slot = slots.setdefault(name, {"slot": name, "registrations": [], "invocations": []})
            slot[role].append({"function_id": item.function.id,
                               "function": item.function.qualified_name,
                               "file": item.function.file, "line": line,
                               "evidence": _line_text(item.source, line)})

        for item in parsed:
            for match in re.finditer(
                r"(?:->|\.)\s*(?P<slot>[A-Za-z_]\w*)\s*=\s*(?P<value>[A-Za-z_]\w*)",
                item.body_masked,
            ):
                if match.group("slot") in CALLBACK_SLOT_NAMES or _looks_like_callback(match.group("slot")) or _looks_like_callback(match.group("value")):
                    record(match.group("slot"), "registrations", item, match.start())
            for match in re.finditer(
                r"(?:->|\.)\s*(?P<slot>[A-Za-z_]\w*)\s*\(", item.body_masked
            ):
                if match.group("slot") in CALLBACK_SLOT_NAMES or _looks_like_callback(match.group("slot")):
                    record(match.group("slot"), "invocations", item, match.start())

        for slot in slots.values():
            slot["resolution"] = "observed" if slot["registrations"] and slot["invocations"] else "partial"
        return sorted(slots.values(), key=lambda item: item["slot"])

    @classmethod
    def _pointer_assignments(
        cls,
        body: str,
        by_short: dict[str, list[Function]],
        caller: Function,
    ) -> tuple[dict[str, list[Function]], list[dict]]:
        result: dict[str, list[Function]] = {}
        ambiguities: list[dict] = []
        patterns = [
            r"\(\s*\*\s*(?P<var>[A-Za-z_]\w*)\s*\)\s*\([^;=]*\)\s*=\s*&?\s*(?P<target>[A-Za-z_]\w*)",
            r"\b(?P<var>[A-Za-z_]\w*)\s*=\s*&\s*(?P<target>[A-Za-z_]\w*)",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, body):
                target_name = match.group("target")
                if target_name in by_short:
                    targets, _ = cls._scoped_candidates(caller, by_short[target_name])
                    if targets:
                        result[match.group("var")] = targets
                    if len(targets) != 1:
                        ambiguities.append({
                            "target": target_name,
                            "offset": match.start("target"),
                            "candidates": cls._candidate_evidence(by_short[target_name]),
                        })
        return result, ambiguities

    @staticmethod
    def _candidate_evidence(candidates: Iterable[Function]) -> list[dict]:
        return [
            {
                "function_id": candidate.id,
                "qualified_name": candidate.qualified_name,
                "file": candidate.file,
                "line": candidate.line,
                "platform_scope": _platform_scope(candidate.file),
            }
            for candidate in candidates
        ]

    @classmethod
    def _scoped_candidates(
        cls, caller: Function, candidates: Iterable[Function]
    ) -> tuple[list[Function], float]:
        """Keep plausible translation-unit targets without crossing platform trees."""

        unique = list({candidate.id: candidate for candidate in candidates}.values())
        same_file = [candidate for candidate in unique if candidate.file == caller.file]
        if same_file:
            return same_file, 0.96 if len(same_file) == 1 else 0.62

        caller_scope = _platform_scope(caller.file)
        if caller_scope == "common":
            common = [candidate for candidate in unique if _platform_scope(candidate.file) == "common"]
            scoped = common or unique
        else:
            scoped = [
                candidate
                for candidate in unique
                if _platform_scope(candidate.file) in {caller_scope, "common"}
            ]
        if len(scoped) == 1:
            return scoped, 0.94
        if scoped:
            return scoped, 0.55
        return [], 0.0

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
            "registers_callback_calls": counts["registers_callback"],
            "scheduled_by_calls": counts["scheduled_by"],
            "invokes_callback_calls": counts["invokes_callback"],
            "callback_slot_count": len(result.get("callback_slots", [])),
        }

    @staticmethod
    def _profile_kind(target: Path, parsed: list[ParsedFunction]) -> str:
        names = {item.function.name for item in parsed}
        parts = {part.lower() for part in target.parts}
        if "libuv" in parts or names & {"uv_run", "uv__io_poll", "uv_read_start", "uv__stream_io"}:
            return "libuv"
        if "redis" in parts or names & {"aeMain", "aeProcessEvents", "aeCreateFileEvent", "serverCron"}:
            return "redis"
        return "generic_c_cpp"

    @staticmethod
    def _profile(
        target: Path,
        kind: str,
        functions: list[Function],
        callback_slots: list[dict],
        configuration_separation: bool = False,
    ) -> dict:
        key_symbols = {
            "libuv": ("uv_run", "uv__io_poll", "uv__io_cb", "uv__stream_io", "uv_read_start"),
            "redis": ("aeMain", "aeProcessEvents", "aeCreateFileEvent", "serverCron"),
        }.get(kind, ())
        by_name: dict[str, list[Function]] = defaultdict(list)
        for function in functions:
            by_name[function.name].append(function)
        signals = [
            {"symbol": name, "file": by_name[name][0].file, "line": by_name[name][0].line}
            for name in key_symbols if by_name.get(name)
        ]
        synthetic = any(part in {"samples", "fixtures"} for part in target.parts)
        source_repository = any((candidate / ".git").exists() for candidate in (target, *target.parents))
        if synthetic:
            status, basis = "synthetic_validation", "synthetic_fixture"
        elif source_repository and kind != "generic_c_cpp":
            status, basis = ("source_verified" if len(signals) >= 3 else "partial"), "repository_snapshot"
        else:
            status, basis = ("partial" if kind != "generic_c_cpp" else "unclassified"), "workspace_source"
        return {"kind": kind, "status": status, "evidence_basis": basis,
                "signals": signals, "callback_slots": len(callback_slots),
                "configuration_separation": configuration_separation,
                "limitations": [
                    "领域语义边为 inferred；当前 Demo 使用源码路径和配置目标做平台筛选"
                    if configuration_separation
                    else "领域语义边为 inferred；当前 Demo 未收到编译配置"
                ]}


class AnalysisExplainer:
    """Offline natural-language explainer grounded in an analysis result."""

    def answer(self, analysis: dict, question: str) -> dict:
        question = question.strip()
        lowered = question.lower()
        functions = {item["id"]: item for item in analysis["functions"]}
        edges = analysis["edges"]
        citations: list[dict] = []

        matched = next(
            (
                fn
                for fn in sorted(functions.values(), key=lambda item: len(item["name"]), reverse=True)
                if len(fn["name"]) > 2
                and re.search(rf"(?<![A-Za-z0-9_]){re.escape(fn['name'].lower())}(?![A-Za-z0-9_])", lowered)
            ),
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
            selected = [edge for edge in edges if edge["type"] in {
                "async", "callback", "registers_callback", "scheduled_by", "invokes_callback"
            }]
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
