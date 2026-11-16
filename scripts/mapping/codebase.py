"""Run the installed CBM with the same environment as the Codex MCP server."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib


def main() -> int:
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    config_path = codex_home / "config.toml"
    config = (
        tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
        if config_path.exists()
        else {}
    )
    server = config.get("mcp_servers", {}).get("codebase-memory", {})
    binary = server.get("command") or shutil.which("codebase-memory-mcp")
    if not binary:
        print(
            "Install codebase-memory-mcp or configure its Codex MCP entry.",
            file=sys.stderr,
        )
        return 1
    env = dict(os.environ)
    env.update(server.get("env", {}))
    return subprocess.run(
        [binary, *server.get("args", []), *sys.argv[1:]],
        env=env,
        cwd=Path(__file__).resolve().parents[2],
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
