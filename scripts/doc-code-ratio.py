#!/usr/bin/env python3
"""Measure the documentation / code ratio of this repository (D-11, DOC-03).

The old repository drifted to 1,21 line of documentation per line of code, 93 %
of it planning material. That number must never be asserted from memory again:
it is measured here, by a versioned command, at a named SHA.

Only tracked files are counted (`git ls-files -z`), so ignored build output and
virtualenvs never inflate either side. Blank lines are not counted on either
side — a blank line is not a line of prose nor a line of code.

Buckets:
  doc       .md outside .planning/          — documentation shipped with the code
  pilotage  .md under .planning/            — planning material (D-07, DOC-03)
  code      .py .ts .js .mjs .cjs .svelte .css .html .sql .sh .ps1 .mako
  content   .svx                            — editorial pages
  config    .yml .yaml .toml .jsonc .ini .cfg .txt
  data      .json .csv .geojson             — fixtures, contracts, lockfiles
  binary    fonts, images, PDF, .gz fixtures — files counted, lines NOT counted
  other     everything else (counted, never mixed into a ratio)

A binary file has no lines: counting the "lines" of a gzip fixture or a woff2
font would add tens of thousands of meaningless units to the total. Binaries
are detected by a NUL byte in their first 8 KiB, filed under `binary`, and
contribute 0 lines.

Prints the detailed JSON then a single RATIO line. `--json` prints only the
JSON. Exits 1 if `code` is 0 (a ratio over zero is not a measurement).
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

CODE_EXT = {
    ".py", ".ts", ".js", ".mjs", ".cjs", ".svelte",
    ".css", ".html", ".sql", ".sh", ".ps1", ".mako",
}
CONTENT_EXT = {".svx"}
CONFIG_EXT = {".yml", ".yaml", ".toml", ".jsonc", ".ini", ".cfg", ".txt"}
DATA_EXT = {".json", ".csv", ".geojson"}
PLANNING_PREFIX = ".planning/"


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, check=True, text=False,
    )
    return result.stdout.decode("utf-8", errors="replace")


def is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"\0" in handle.read(8192)
    except OSError:
        return True


def bucket_of(relpath: str) -> str:
    suffix = Path(relpath).suffix.lower()
    if suffix == ".md":
        return "pilotage" if relpath.startswith(PLANNING_PREFIX) else "doc"
    if suffix in CODE_EXT:
        return "code"
    if suffix in CONTENT_EXT:
        return "content"
    if suffix in CONFIG_EXT:
        return "config"
    if suffix in DATA_EXT:
        return "data"
    return "other"


def non_empty_lines(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, IsADirectoryError):
        return 0
    return sum(1 for line in text.splitlines() if line.strip())


def main(argv: list[str]) -> int:
    json_only = "--json" in argv[1:]

    root = Path(git("rev-parse", "--show-toplevel").strip())
    sha = git("rev-parse", "--short", "HEAD").strip()
    tracked = [name for name in git("ls-files", "-z").split("\0") if name]

    lines: Counter[str] = Counter()
    files: Counter[str] = Counter()
    for relpath in tracked:
        target = root / relpath
        if not target.is_file():
            continue  # submodule pointer or a path removed from the worktree
        if is_binary(target):
            files["binary"] += 1
            continue  # a binary file has no lines
        bucket = bucket_of(relpath)
        files[bucket] += 1
        lines[bucket] += non_empty_lines(target)

    code = lines["code"]
    if code == 0:
        print("RATIO fail: 0 ligne de code comptée — mesure impossible", file=sys.stderr)
        return 1

    doc_ratio = round(lines["doc"] / code, 2)
    pilotage_ratio = round(lines["pilotage"] / code, 2)
    payload = {
        "sha": sha,
        "tracked_files": len(tracked),
        "lines": {bucket: lines[bucket] for bucket in sorted(lines)},
        "files": {bucket: files[bucket] for bucket in sorted(files)},
        "ratios": {"doc/code": doc_ratio, "pilotage/code": pilotage_ratio},
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if not json_only:
        print(
            f"RATIO doc/code={doc_ratio} pilotage/code={pilotage_ratio} "
            f"(doc={lines['doc']} pilotage={lines['pilotage']} code={code} "
            f"content={lines['content']} config={lines['config']} data={lines['data']}) "
            f"at {sha}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
