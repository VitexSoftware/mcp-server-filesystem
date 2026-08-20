"""Structural tests that don't require any external service."""

from __future__ import annotations

import asyncio

import pytest

from mcp_server_filesystem.server import delete, mcp, mkdir, write_file


def test_tools_are_registered() -> None:
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "list_dir",
        "read_file",
        "stat",
        "exists",
        "glob_search",
        "write_file",
        "mkdir",
        "delete",
        "move",
        "copy",
    }


def test_readonly_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FS_ROOT", str(tmp_path))
    monkeypatch.delenv("FS_READONLY", raising=False)
    from mcp_server_filesystem import server

    server._root.cache_clear()
    with pytest.raises(RuntimeError, match="read-only"):
        write_file("foo.txt", "hello")
    with pytest.raises(RuntimeError, match="read-only"):
        mkdir("subdir")
    with pytest.raises(RuntimeError, match="read-only"):
        delete("foo.txt")


def test_readonly_guard_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FS_ROOT", str(tmp_path))
    monkeypatch.setenv("FS_READONLY", "false")
    from mcp_server_filesystem import server

    server._root.cache_clear()
    result = write_file("foo.txt", "hello")
    assert result["bytes_written"] == 5
    assert (tmp_path / "foo.txt").read_text() == "hello"


def test_path_escape_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FS_ROOT", str(tmp_path))
    monkeypatch.delenv("FS_READONLY", raising=False)
    from mcp_server_filesystem.server import _resolve, _root

    _root.cache_clear()
    with pytest.raises(RuntimeError, match="escapes FS_ROOT"):
        _resolve("../../etc/passwd")


def test_list_dir_and_read_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FS_ROOT", str(tmp_path))
    from mcp_server_filesystem import server
    from mcp_server_filesystem.server import list_dir, read_file

    server._root.cache_clear()
    (tmp_path / "a.txt").write_text("content")

    entries = list_dir(".")
    assert {e["name"] for e in entries} == {"a.txt"}
    assert read_file("a.txt") == "content"
