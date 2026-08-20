<img src="debian/mcp-server-filesystem.svg" width="96" height="96" alt="mcp-server-filesystem icon">

# mcp-server-filesystem

MCP server exposing Linux filesystem operations as tools for LLM agents,
built with [FastMCP](https://gofastmcp.com). Designed as a lightweight,
low-dependency test server for the [mcprack](https://github.com/VitexSoftware/mcprack)
MCP proxy/catalog tool.

## Configuration

No credentials are needed. All configuration is via environment variables,
read once at startup:

| Variable | Required | Description |
|---|---|---|
| `FS_ROOT` | no | Directory all paths are confined to (default: current working directory at startup) |
| `FS_READONLY` | no | `true` (default) blocks mutating tools; set to `false` to allow writes/deletes/moves |

All paths given to tools are resolved against `FS_ROOT` and rejected if they
would escape it (via `..` or a symlink). This is an application-level
boundary, not a hardened sandbox (no chroot/mount namespace).

## Tools

Read-only: `list_dir`, `read_file`, `stat`, `exists`, `glob_search`.

Mutating (require `FS_READONLY=false`): `write_file`, `mkdir`, `delete`,
`move`, `copy`.

## Usage

```sh
pip install -e .
export FS_ROOT=/path/to/sandbox
mcp-server-filesystem
```

### mcprack integration

Register this server in [mcprack](https://github.com/VitexSoftware/mcprack) with:

- Command: `mcp-server-filesystem`
- Env: `FS_ROOT=/path/to/sandbox`, `FS_READONLY=false` (if writes are needed)

mcprack spawns it as a stdio subprocess and proxies it over HTTP per user.

## Development

```sh
pytest
```
