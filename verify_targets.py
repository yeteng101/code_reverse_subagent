"""Run reproducible libuv/Redis domain-profile checks and emit a JSON report."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path

from analyzer import CodeAnalyzer


APP_DIR = Path(__file__).resolve().parent
WORKSPACE = APP_DIR.parent


def revision(repository: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repository), "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def edge_names(analysis: dict) -> set[tuple[str, str, str]]:
    functions = {item["id"]: item["name"] for item in analysis["functions"]}
    return {
        (edge["type"], functions[edge["source"]], functions[edge["target"]])
        for edge in analysis["edges"]
    }


def inspect_target(label: str, repository: Path, target: Path, expected: dict) -> dict:
    analysis = CodeAnalyzer(WORKSPACE).analyze(target)
    observed_edges = edge_names(analysis)
    observed_symbols = {item["name"] for item in analysis["functions"]}
    checks = {
        "profile_kind": analysis["profile"]["kind"] == expected["kind"],
        "profile_basis": analysis["profile"]["evidence_basis"] == expected["basis"],
        "required_symbols": set(expected["symbols"]) <= observed_symbols,
        "required_semantic_edges": all(tuple(item) in observed_edges for item in expected["edges"]),
        "macro_evidence": analysis["summary"]["macro_count"] > 0,
    }
    expected_slots = expected.get("slots") or ([expected["slot"]] if expected.get("slot") else [])
    if expected_slots:
        observed_slots = {
            item["slot"] for item in analysis["callback_slots"] if item["resolution"] == "observed"
        }
        checks["callback_slot"] = set(expected_slots) <= observed_slots
    return {
        "target": label,
        "repository": str(repository),
        "analyzed_path": str(target),
        "revision": revision(repository),
        "profile": analysis["profile"],
        "summary": analysis["summary"],
        "edge_types": dict(sorted(Counter(edge["type"] for edge in analysis["edges"]).items())),
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="验证 libuv/Redis 领域分析规则")
    parser.add_argument("--libuv", type=Path, default=WORKSPACE / "libuv")
    parser.add_argument("--redis", type=Path, default=WORKSPACE / "redis")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    libuv_target = args.libuv / "src" if (args.libuv / "src").is_dir() else args.libuv
    reports = [inspect_target("libuv", args.libuv, libuv_target, {
        "kind": "libuv",
        "basis": "repository_snapshot",
        "symbols": ["uv_run", "uv__io_poll", "uv__io_cb", "uv__stream_io", "uv_read_start"],
        "edges": [
            ["scheduled_by", "uv_run", "uv__io_poll"],
            ["scheduled_by", "uv__io_poll", "uv__io_cb"],
            ["invokes_callback", "uv__io_cb", "uv__stream_io"],
            ["scheduled_by", "uv__stream_io", "uv__read"],
        ],
        "slot": "read_cb",
    })]

    if args.redis.is_dir():
        redis_target = args.redis / "src" if (args.redis / "src").is_dir() else args.redis
        redis_repo = args.redis
        redis_basis = "repository_snapshot"
    else:
        redis_target = APP_DIR / "samples" / "redis_event_loop.c"
        redis_repo = redis_target
        redis_basis = "synthetic_fixture"
    reports.append(inspect_target("redis", redis_repo, redis_target, {
        "kind": "redis",
        "basis": redis_basis,
        "symbols": ["aeMain", "aeProcessEvents", "aeCreateFileEvent", "serverCron"],
        "edges": [
            ["scheduled_by", "aeMain", "aeProcessEvents"],
            ["scheduled_by", "aeProcessEvents", "processTimeEvents"],
            ["invokes_callback", "processTimeEvents", "serverCron"],
        ],
        "slots": ["rfileProc", "timeProc"],
    }))

    report = {"schema_version": "1.0", "passed": all(item["passed"] for item in reports),
              "targets": reports}
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
