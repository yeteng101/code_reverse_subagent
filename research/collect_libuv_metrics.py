#!/usr/bin/env python3
"""Collect repeatable metrics from the local libuv checkout.

The script is intentionally dependency-free so the group can rerun it after a
libuv checkout changes.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
LIBUV = WORKSPACE_ROOT / "libuv"


def count_files(directory: Path) -> int:
    return sum(1 for path in directory.glob("*.c") if path.is_file())


def count_lines(files: list[Path]) -> int:
    return sum(
        sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
        for path in files
    )


def main() -> None:
    if not LIBUV.exists():
        print(f"未找到 libuv 源码：{LIBUV}", file=sys.stderr)
        raise SystemExit(1)

    include = LIBUV / "include"
    unix = LIBUV / "src" / "unix"
    win = LIBUV / "src" / "win"
    shared = LIBUV / "src"

    compile_commands = json.loads(
        (LIBUV / "build" / "compile_commands.json").read_text(encoding="utf-8")
    )

    uv_h = (include / "uv.h").read_text(encoding="utf-8", errors="replace")
    unix_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(unix.glob("*.c"))
    )

    callback_typedefs = re.findall(
        r"^typedef void \(\*uv_[A-Za-z0-9_]+_cb\)",
        uv_h,
        flags=re.MULTILINE,
    )

    all_sources = (
        list(shared.glob("*.c"))
        + list(unix.glob("*.c"))
        + list(win.glob("*.c"))
    )
    callback_invocation_pattern = re.compile(
        r"->(?:read_cb|write_cb|connect_cb|close_cb|timer_cb|"
        r"work_cb|done_cb|connection_cb|cb)\("
    )
    callback_sites = [
        (str(path), line_no)
        for path in all_sources
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        )
        if callback_invocation_pattern.search(line)
    ]

    poll_backends = [
        path.name
        for path in sorted(unix.glob("*.c"))
        if re.search(
            r"^void uv__io_poll\(",
            path.read_text(encoding="utf-8", errors="replace"),
            flags=re.MULTILINE,
        )
    ]

    baseline = None
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from analyzer import CodeAnalyzer  # type: ignore

        started = time.perf_counter()
        result = CodeAnalyzer(WORKSPACE_ROOT).analyze(LIBUV / "src")
        elapsed = time.perf_counter() - started
        summary = result["summary"]
        baseline = {
            "elapsed_s": round(elapsed, 2),
            "files": summary["file_count"],
            "functions": summary["function_count"],
            "edges": summary["edge_count"],
            "direct_calls": summary["direct_calls"],
            "async_hints": summary["async_calls"],
            "callback_hints": summary["callback_calls"],
            "function_pointer_edges": summary["function_pointer_calls"],
            "macros": summary["macro_count"],
            "unresolved_calls": len(result["unresolved_calls"]),
        }
    except Exception as exc:  # Baseline is optional evidence, not the deliverable.
        baseline = {"error": str(exc)}

    report = {
        "libuv_commit_metadata": {
            "commit": "e43e3d8",
            "branch": "v1.x",
            "compile_units": len(compile_commands),
        },
        "source_size": {
            "unix_c_files": count_files(unix),
            "win_c_files": count_files(win),
            "shared_c_files": count_files(shared),
            "unix_c_lines": count_lines(list(unix.glob("*.c"))),
            "test_c_lines": count_lines(list((LIBUV / "test").glob("*.c"))),
        },
        "callback_model": {
            "callback_typedefs_in_uv_h": len(callback_typedefs),
            "callback_invocation_sites": len(callback_sites),
            "io_poll_backends": poll_backends,
            "io_poll_backend_count": len(poll_backends),
        },
        "configuration_space": {
            "conditional_directives_unix_sources": len(
                re.findall(
                    r"#(?:ifdef|ifndef|elif|if)\b",
                    unix_text,
                )
            ),
        },
        "lightweight_baseline_analyzer": baseline,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
