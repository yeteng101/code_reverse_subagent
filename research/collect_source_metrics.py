#!/usr/bin/env python3
"""Collect reproducible evidence from local libuv and Redis source snapshots.

The report deliberately distinguishes missing source from a zero count. It can
therefore be checked into a research workflow before both repositories are
available without turning unverified Redis claims into measured facts.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
SOURCE_SUFFIXES = {".c", ".h"}


LIBUV_SYMBOLS = {
    "event_loop": ["uv_run"],
    "io_dispatch": ["uv__io_poll", "uv__io_cb", "uv__io_start"],
    "stream_read": ["uv_read_start", "uv__read_start", "uv__stream_io"],
    "stream_accept": ["uv_listen", "uv__tcp_listen", "uv__server_io"],
    "timer": ["uv_timer_start", "uv__run_timers"],
    "threadpool": ["uv_queue_work", "uv__work_submit", "uv__work_done"],
    "async_wakeup": ["uv_async_init", "uv_async_send", "uv__async_io"],
}


REDIS_SYMBOLS = {
    "bootstrap": ["main", "initServer", "aeMain"],
    "event_loop": ["aeCreateEventLoop", "aeCreateFileEvent", "aeProcessEvents"],
    "network_read": ["acceptTcpHandler", "createClient", "readQueryFromClient"],
    "command_dispatch": ["processInputBuffer", "processCommand", "call"],
    "timer": ["aeCreateTimeEvent", "processTimeEvents", "serverCron"],
    "persistence": ["rdbSave", "rdbSaveBackground", "flushAppendOnlyFile"],
    "background_work": ["bioCreateBackgroundJob", "bioProcessBackgroundJobs"],
    "module_api": ["moduleLoad", "moduleRegisterCoreAPI", "RM_CreateCommand"],
}


LIBUV_MARKERS = [
    "uv__io_cb_get",
    "uv__io_cb_set",
    "read_cb",
    "connection_cb",
    "timer_cb",
    "work_cb",
    "after_work_cb",
]


REDIS_MARKERS = [
    "aeApiPoll",
    "rfileProc",
    "wfileProc",
    "timeProc",
    "redisCommandTable",
    "REDISMODULE_ONLOAD_FUNC",
    "RedisModule_OnLoad",
]


def git_value(repository: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def source_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SOURCE_SUFFIXES
        and ".git" not in path.parts
        and "deps" not in path.parts
        and "build" not in path.parts
    )


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def count_lines(files: Iterable[Path]) -> int:
    return sum(read_text(path).count("\n") + 1 for path in files)


def find_symbol_locations(
    repository: Path,
    files: list[Path],
    symbol_groups: dict[str, list[str]],
) -> dict[str, dict[str, list[dict[str, object]]]]:
    result: dict[str, dict[str, list[dict[str, object]]]] = {}
    for group, symbols in symbol_groups.items():
        group_result: dict[str, list[dict[str, object]]] = {}
        for symbol in symbols:
            locations: list[dict[str, object]] = []
            # Require a declaration-like return-type prefix. A bare symbol at
            # the start of a line is a call, and a prototype is rejected below
            # because it reaches ';' rather than a function body.
            definition = re.compile(
                rf"^[ \t]*(?:[A-Za-z_]\w*[ \t*]+)+"
                rf"(?P<symbol>{re.escape(symbol)})[ \t]*\(",
                re.MULTILINE,
            )
            for path in files:
                text = read_text(path)
                for match in definition.finditer(text):
                    opening = text.find("(", match.start("symbol"), match.end())
                    depth = 0
                    closing = None
                    for index in range(opening, min(len(text), opening + 4000)):
                        if text[index] == "(":
                            depth += 1
                        elif text[index] == ")":
                            depth -= 1
                            if depth == 0:
                                closing = index
                                break
                    if closing is None:
                        continue
                    trailer = re.search(r"[;{]", text[closing + 1:closing + 1000])
                    if trailer is None or trailer.group() != "{":
                        continue
                    line = text.count("\n", 0, match.start("symbol")) + 1
                    locations.append(
                        {
                            "file": path.relative_to(repository).as_posix(),
                            "line": line,
                        }
                    )
            group_result[symbol] = locations
        result[group] = group_result
    return result


def top_level_file_counts(source_root: Path, files: list[Path]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in files:
        relative = path.relative_to(source_root)
        bucket = relative.parts[0] if len(relative.parts) > 1 else "shared"
        counts[bucket] = counts.get(bucket, 0) + 1
    return dict(sorted(counts.items()))


def find_marker_locations(
    repository: Path,
    files: list[Path],
    markers: list[str],
    limit_per_marker: int = 20,
) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    for marker in markers:
        locations: list[dict[str, object]] = []
        pattern = re.compile(rf"\b{re.escape(marker)}\b")
        for path in files:
            if len(locations) >= limit_per_marker:
                break
            for line_no, line in enumerate(read_text(path).splitlines(), 1):
                if pattern.search(line):
                    locations.append(
                        {
                            "file": path.relative_to(repository).as_posix(),
                            "line": line_no,
                            "text": line.strip()[:240],
                        }
                    )
                    if len(locations) >= limit_per_marker:
                        break
        result[marker] = locations
    return result


def callback_metrics(files: list[Path], typedef_prefix: str) -> dict[str, int]:
    combined = "\n".join(read_text(path) for path in files)
    typedefs = re.findall(
        rf"typedef[^;]*\(\s*\*\s*{re.escape(typedef_prefix)}[A-Za-z0-9_]*",
        combined,
    )
    indirect_calls = re.findall(
        r"(?:->|\.)[A-Za-z_][A-Za-z0-9_]*(?:Proc|_cb|Callback|callback)\s*\(",
        combined,
    )
    return {
        "callback_typedef_candidates": len(typedefs),
        "member_callback_invocation_candidates": len(indirect_calls),
    }


def common_report(repository: Path, source_root: Path) -> tuple[dict, list[Path]]:
    files = source_files(source_root)
    report = {
        "path": str(repository),
        "available": repository.is_dir() and source_root.is_dir(),
    }
    if not report["available"]:
        report["status"] = "source_unavailable"
        return report, []
    commit = git_value(repository, "rev-parse", "HEAD")
    report.update(
        {
            "status": "measured" if commit else "measured_unversioned",
            "commit": commit,
            "describe": git_value(repository, "describe", "--tags", "--always"),
            "branch": git_value(repository, "branch", "--show-current"),
            "snapshot_pinned": commit is not None,
            "worktree_dirty": bool(git_value(repository, "status", "--porcelain")),
            "source_files": len(files),
            "source_lines": count_lines(files),
            "source_buckets": top_level_file_counts(source_root, files),
            "conditional_directives": sum(
                len(re.findall(r"^\s*#\s*(?:if|ifdef|ifndef|elif)\b", read_text(path), re.MULTILINE))
                for path in files
            ),
        }
    )
    return report, files


def libuv_report(repository: Path) -> dict:
    report, files = common_report(repository, repository / "src")
    if not files:
        return report
    report["callback_model"] = callback_metrics(files + source_files(repository / "include"), "uv_")
    report["symbol_evidence"] = find_symbol_locations(repository, files, LIBUV_SYMBOLS)
    report["indirect_semantic_markers"] = find_marker_locations(
        repository,
        files + source_files(repository / "include"),
        LIBUV_MARKERS,
    )
    report["io_poll_backends"] = sorted(
        location["file"]
        for location in report["symbol_evidence"]["io_dispatch"]["uv__io_poll"]
    )
    compile_commands = repository / "build" / "compile_commands.json"
    report["compile_commands"] = {
        "available": compile_commands.is_file(),
        "translation_units": (
            len(json.loads(compile_commands.read_text(encoding="utf-8")))
            if compile_commands.is_file()
            else None
        ),
    }
    return report


def redis_report(repository: Path) -> dict:
    report, files = common_report(repository, repository / "src")
    if not files:
        report["required_next_step"] = (
            "Place an official Redis checkout at this path or pass --redis PATH; "
            "then rerun to bind the research report to a commit and source lines."
        )
        return report
    report["callback_model"] = callback_metrics(files, "ae")
    report["symbol_evidence"] = find_symbol_locations(repository, files, REDIS_SYMBOLS)
    report["indirect_semantic_markers"] = find_marker_locations(
        repository,
        files,
        REDIS_MARKERS,
    )
    report["event_backends"] = sorted(
        path.relative_to(repository).as_posix()
        for path in files
        if re.fullmatch(r"ae_(?:epoll|kqueue|evport|select)\.c", path.name)
    )
    report["core_files_present"] = {
        name: (repository / "src" / name).is_file()
        for name in (
            "server.c",
            "ae.c",
            "networking.c",
            "aof.c",
            "rdb.c",
            "replication.c",
            "cluster.c",
            "module.c",
            "bio.c",
        )
    }
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--libuv", type=Path, default=WORKSPACE_ROOT / "libuv")
    parser.add_argument("--redis", type=Path, default=WORKSPACE_ROOT / "redis")
    parser.add_argument(
        "--require-redis",
        action="store_true",
        help="exit with status 2 when Redis source or its Git commit is absent",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = {
        "schema_version": "1.0",
        "measurement_policy": (
            "Counts and locations come only from local source snapshots; missing "
            "repositories are reported as source_unavailable, never as zero."
        ),
        "libuv": libuv_report(args.libuv.resolve()),
        "redis": redis_report(args.redis.resolve()),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.require_redis and not result["redis"].get("snapshot_pinned"):
        print("A Git-versioned Redis source snapshot is required.", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
