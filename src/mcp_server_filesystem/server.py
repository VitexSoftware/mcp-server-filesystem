"""MCP server exposing Linux filesystem operations as tools.

All paths are confined to a root directory read once from the environment
at startup (``FS_ROOT``, default: the process's current working directory),
so an LLM driving this server cannot escape that subtree via ``..`` or a
symlink.

By default the server runs read-only: tools that write, delete, create, or
otherwise mutate the filesystem raise a clear error unless
``FS_READONLY=false`` is set in the environment.
"""

from __future__ import annotations

import base64
import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

mcp = FastMCP("Filesystem")


def _readonly() -> bool:
    return os.environ.get("FS_READONLY", "true") not in ("0", "false", "False", "")


def _require_writable() -> None:
    if _readonly():
        raise RuntimeError(
            "This server is running in read-only mode (FS_READONLY=true). "
            "Set FS_READONLY=false to enable filesystem mutations."
        )


@lru_cache(maxsize=1)
def _root() -> Path:
    root = os.environ.get("FS_ROOT")
    return Path(root).resolve() if root else Path.cwd().resolve()


def _resolve(path: str) -> Path:
    """Resolve ``path`` against the configured root and reject any escape."""
    if "\x00" in path:
        raise RuntimeError("path contains a null byte")
    root = _root()
    candidate = (root / path).resolve()
    if candidate != root and root not in candidate.parents:
        raise RuntimeError(f"path escapes FS_ROOT: {path!r}")
    return candidate


# --------------------------------------------------------------------------
# Read-only tools
# --------------------------------------------------------------------------


@mcp.tool
def list_dir(path: str = ".") -> list[dict[str, Any]]:
    """List the contents of a directory (name, is_dir, size, mtime)."""
    target = _resolve(path)
    entries = []
    for entry in sorted(target.iterdir()):
        info = entry.stat()
        entries.append(
            {
                "name": entry.name,
                "is_dir": entry.is_dir(),
                "size": info.st_size,
                "mtime": info.st_mtime,
            }
        )
    return entries


@mcp.tool
def read_file(
    path: str,
    encoding: str = "utf-8",
    binary: bool = False,
    max_bytes: int = 1_048_576,
) -> str:
    """Read a file's contents.

    Text files are decoded with ``encoding`` (errors replaced). Set
    ``binary=True`` to get the raw bytes base64-encoded instead. Reads are
    capped at ``max_bytes`` to avoid dumping huge files into context.
    """
    target = _resolve(path)
    data = target.read_bytes()[:max_bytes]
    if binary:
        return base64.b64encode(data).decode("ascii")
    return data.decode(encoding, errors="replace")


@mcp.tool
def stat(path: str) -> dict[str, Any]:
    """Fetch filesystem metadata for a path."""
    target = _resolve(path)
    info = target.lstat()
    return {
        "size": info.st_size,
        "mtime": info.st_mtime,
        "mode": info.st_mode,
        "is_dir": target.is_dir(),
        "is_file": target.is_file(),
        "is_symlink": target.is_symlink(),
    }


@mcp.tool
def exists(path: str) -> bool:
    """Check whether a path exists."""
    return _resolve(path).exists()


@mcp.tool
def glob_search(pattern: str, path: str = ".") -> list[str]:
    """Search for files matching a glob pattern (e.g. ``**/*.py``) under path."""
    target = _resolve(path)
    root = _root()
    return [str(p.relative_to(root)) for p in sorted(target.glob(pattern))]


# --------------------------------------------------------------------------
# Mutating tools (require FS_READONLY=false)
# --------------------------------------------------------------------------


@mcp.tool
def write_file(path: str, content: str, encoding: str = "utf-8", append: bool = False) -> dict[str, Any]:
    """Write text content to a file. Requires FS_READONLY=false."""
    _require_writable()
    target = _resolve(path)
    mode = "a" if append else "w"
    with target.open(mode, encoding=encoding) as f:
        written = f.write(content)
    return {"path": str(target.relative_to(_root())), "bytes_written": written}


@mcp.tool
def mkdir(path: str, parents: bool = True, exist_ok: bool = True) -> dict[str, Any]:
    """Create a directory. Requires FS_READONLY=false."""
    _require_writable()
    target = _resolve(path)
    target.mkdir(parents=parents, exist_ok=exist_ok)
    return {"path": str(target.relative_to(_root()))}


@mcp.tool
def delete(path: str, recursive: bool = False) -> dict[str, Any]:
    """Delete a file, or a directory if recursive=True. Requires FS_READONLY=false."""
    _require_writable()
    target = _resolve(path)
    if target.is_dir():
        if recursive:
            shutil.rmtree(target)
        else:
            target.rmdir()
    else:
        target.unlink()
    return {"path": str(target.relative_to(_root()))}


@mcp.tool
def move(src: str, dst: str) -> dict[str, Any]:
    """Move/rename a file or directory. Requires FS_READONLY=false."""
    _require_writable()
    source = _resolve(src)
    dest = _resolve(dst)
    shutil.move(str(source), str(dest))
    return {"src": str(source.relative_to(_root())), "dst": str(dest.relative_to(_root()))}


@mcp.tool
def copy(src: str, dst: str, recursive: bool = False) -> dict[str, Any]:
    """Copy a file, or a directory if recursive=True. Requires FS_READONLY=false."""
    _require_writable()
    source = _resolve(src)
    dest = _resolve(dst)
    if source.is_dir():
        if not recursive:
            raise RuntimeError(f"{src!r} is a directory; pass recursive=True to copy it")
        shutil.copytree(source, dest)
    else:
        shutil.copy2(source, dest)
    return {"src": str(source.relative_to(_root())), "dst": str(dest.relative_to(_root()))}


def main() -> None:
    import sys

    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__)
        print("Usage: mcp-server-filesystem")
        print("Environment: FS_ROOT (default: current working directory), FS_READONLY (default: true)")
        return
    mcp.run()


if __name__ == "__main__":
    main()
