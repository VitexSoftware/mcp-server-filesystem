#!/usr/bin/env python3
"""Live capability scenario for the filesystem MCP server.

Uses a temporary sandbox as ``FS_ROOT`` (no credentials needed). Exercises
read tools and verifies mutating tools refuse under ``FS_READONLY=true``.

Usage:
  python tests/live_capability_scenario.py
  FS_READONLY=true python tests/live_capability_scenario.py
  python tests/live_capability_scenario.py --allow-writes   # also probes writes

Exit code is 0 only when every non-skipped check passes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@dataclass
class CheckResult:
    name: str
    kind: str  # tool | meta | guard
    ok: bool
    detail: str = ""
    sample: Any = None
    skipped: bool = False


@dataclass
class ScenarioReport:
    root: str
    read_only: bool
    results: List[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.ok and not r.skipped)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.ok and not r.skipped)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.skipped)


def _tool_is_readonly(tool: Any) -> bool:
    ann = getattr(tool, "annotations", None)
    if ann is None:
        return True
    if isinstance(ann, dict):
        return bool(ann.get("read_only_hint", ann.get("readOnlyHint", True)))
    hint = getattr(ann, "read_only_hint", None)
    if hint is None:
        hint = getattr(ann, "readOnlyHint", None)
    return True if hint is None else bool(hint)


def _run_check(
    report: ScenarioReport,
    name: str,
    kind: str,
    fn: Callable[[], Any],
    *,
    expect_error_substr: Optional[str] = None,
) -> Optional[Any]:
    try:
        raw = fn()
        if expect_error_substr:
            report.add(
                CheckResult(
                    name=name,
                    kind=kind,
                    ok=False,
                    detail=f"expected error containing {expect_error_substr!r}",
                    sample=str(raw)[:300],
                )
            )
            return raw
        report.add(
            CheckResult(
                name=name,
                kind=kind,
                ok=True,
                detail="ok",
                sample=str(raw)[:300],
            )
        )
        return raw
    except Exception as exc:  # noqa: BLE001
        if expect_error_substr:
            ok = expect_error_substr.lower() in str(exc).lower()
            report.add(
                CheckResult(
                    name=name,
                    kind=kind,
                    ok=ok,
                    detail=str(exc),
                )
            )
            return None
        report.add(
            CheckResult(
                name=name,
                kind=kind,
                ok=False,
                detail=f"{type(exc).__name__}: {exc}",
                sample=traceback.format_exc()[-600:],
            )
        )
        return None


def run_scenario(*, read_only: bool = True, allow_write_probe: bool = False) -> ScenarioReport:
    with tempfile.TemporaryDirectory(prefix="mcp-fs-live-") as tmp:
        root = Path(tmp)
        (root / "hello.txt").write_text("hello sandbox\n", encoding="utf-8")
        (root / "subdir").mkdir()
        (root / "subdir" / "nested.txt").write_text("nested\n", encoding="utf-8")

        os.environ["FS_ROOT"] = str(root)
        os.environ["FS_READONLY"] = "true" if read_only else "false"

        import importlib

        import mcp_server_filesystem.server as server

        server._root.cache_clear()
        server = importlib.reload(server)
        server._root.cache_clear()

        report = ScenarioReport(root=str(root), read_only=read_only)
        tools = asyncio.run(server.mcp.list_tools())
        tool_by_name = {t.name: t for t in tools}

        report.add(
            CheckResult(
                name="tool_catalog",
                kind="meta",
                ok=len(tools) == 10,
                detail=f"{len(tools)} tools registered (expect 10)",
            )
        )

        # Annotation coverage: every tool must declare readOnlyHint
        missing_ann = []
        for t in tools:
            ann = getattr(t, "annotations", None)
            has = False
            if ann is None:
                has = False
            elif isinstance(ann, dict):
                has = "read_only_hint" in ann or "readOnlyHint" in ann
            else:
                has = getattr(ann, "read_only_hint", None) is not None
            if not has:
                missing_ann.append(t.name)
        report.add(
            CheckResult(
                name="annotations:readOnlyHint",
                kind="meta",
                ok=not missing_ann,
                detail="all tools annotated" if not missing_ann else f"missing: {missing_ann}",
            )
        )

        # ---- read tools ----
        entries = _run_check(report, "list_dir", "tool", lambda: server.list_dir("."))
        if entries is not None:
            names = {e["name"] for e in entries} if isinstance(entries, list) else set()
            report.add(
                CheckResult(
                    name="list_dir:contents",
                    kind="meta",
                    ok=names >= {"hello.txt", "subdir"},
                    detail=f"names={sorted(names)}",
                )
            )

        _run_check(report, "read_file", "tool", lambda: server.read_file("hello.txt"))
        _run_check(report, "stat", "tool", lambda: server.stat("hello.txt"))
        _run_check(report, "exists", "tool", lambda: server.exists("hello.txt"))
        _run_check(
            report,
            "glob_search",
            "tool",
            lambda: server.glob_search("**/*.txt"),
        )

        # Path escape must always be rejected
        _run_check(
            report,
            "path_escape_guard",
            "guard",
            lambda: server._resolve("../../etc/passwd"),
            expect_error_substr="escapes FS_ROOT",
        )

        # ---- READ_ONLY guards ----
        if read_only:
            for name, fn in [
                ("write_file", lambda: server.write_file("evil.txt", "nope")),
                ("mkdir", lambda: server.mkdir("evil-dir")),
                ("delete", lambda: server.delete("hello.txt")),
                ("move", lambda: server.move("hello.txt", "moved.txt")),
                ("copy", lambda: server.copy("hello.txt", "copied.txt")),
            ]:
                _run_check(
                    report,
                    f"readonly_guard:{name}",
                    "guard",
                    fn,
                    expect_error_substr="read-only",
                )
                # Confirm sandbox untouched
            report.add(
                CheckResult(
                    name="sandbox_untouched",
                    kind="guard",
                    ok=(root / "hello.txt").read_text() == "hello sandbox\n"
                    and not (root / "evil.txt").exists(),
                    detail="mutating tools did not change sandbox",
                )
            )

            for t in tools:
                if _tool_is_readonly(t):
                    continue
                report.add(
                    CheckResult(
                        name=f"catalog:{t.name}",
                        kind="meta",
                        ok=t.name in tool_by_name,
                        detail="write tool registered",
                    )
                )

        if allow_write_probe and not read_only:
            _run_check(
                report,
                "write_file:allowed",
                "tool",
                lambda: server.write_file("written.txt", "ok"),
            )
            report.add(
                CheckResult(
                    name="write_file:persisted",
                    kind="meta",
                    ok=(root / "written.txt").read_text() == "ok",
                    detail="file on disk",
                )
            )
            _run_check(report, "mkdir:allowed", "tool", lambda: server.mkdir("newdir"))
            _run_check(
                report,
                "copy:allowed",
                "tool",
                lambda: server.copy("written.txt", "written-copy.txt"),
            )
            _run_check(
                report,
                "move:allowed",
                "tool",
                lambda: server.move("written-copy.txt", "written-moved.txt"),
            )
            _run_check(
                report,
                "delete:allowed",
                "tool",
                lambda: server.delete("written-moved.txt"),
            )

        # Keep tmp alive until checks finish (we're still inside the with block
        # only while this function runs; report is built before exit).
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="Set FS_READONLY=false and exercise mutating tools inside the sandbox",
    )
    parser.add_argument("--json-out", help="Write full report JSON here")
    args = parser.parse_args()

    read_only = not args.allow_writes
    report = run_scenario(read_only=read_only, allow_write_probe=args.allow_writes)

    print(f"Filesystem MCP live scenario: FS_ROOT={report.root}")
    print(f"FS_READONLY={'true' if report.read_only else 'false'}")
    print(f"passed={report.passed} failed={report.failed} skipped={report.skipped}")
    print()
    for r in report.results:
        if r.skipped:
            flag = "SKIP"
        elif r.ok:
            flag = "PASS"
        else:
            flag = "FAIL"
        print(f"  {flag:4} [{r.kind}] {r.name}: {r.detail}")

    if args.json_out:
        out = {
            "root": report.root,
            "read_only": report.read_only,
            "passed": report.passed,
            "failed": report.failed,
            "skipped": report.skipped,
            "results": [r.__dict__ for r in report.results],
        }
        Path(args.json_out).write_text(
            json.dumps(out, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        print(f"\nWrote {args.json_out}")

    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
