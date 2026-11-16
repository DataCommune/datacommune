"""Shared by the tests that run a shell doubles script from the backend suite."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def find_bash() -> str | None:
    """Git's bash on Windows (never the WSL launcher of System32), else PATH's."""
    if os.name == "nt":
        git = shutil.which("git")
        if git is None:
            return None
        for parent in Path(git).resolve().parents:
            candidate = parent / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
        return None
    return shutil.which("bash")


def scenarios_of(script: str) -> list[str]:
    """The scenario letters of a doubles script, from its ``# (x) `` headers."""
    texte = (REPO / script).read_text(encoding="utf-8")
    return re.findall(r"^# \(([a-z0-9]+)\) ", texte, flags=re.MULTILINE)


def run_groups(
    bash: str,
    script: str,
    env: dict[str, str],
    var: str,
    groups: list[str],
    timeout: float,
) -> dict[str, tuple[int, str, str]]:
    """Play every group of a doubles script at once, one bash each.

    The scripts spend their time spawning the processes of their doubles
    (KI-19); the groups share nothing (each makes its own scratch directory),
    so they run side by side. Returns, per group, its exit code, stdout and
    stderr.
    """
    procs = {
        group: subprocess.Popen(
            [bash, script],
            cwd=REPO,
            env={**env, var: group},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for group in groups
    }
    deadline = time.monotonic() + timeout
    results: dict[str, tuple[int, str, str]] = {}
    try:
        for group, proc in procs.items():
            out, err = proc.communicate(timeout=max(deadline - time.monotonic(), 1))
            results[group] = (proc.returncode, out, err)
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
    return results
