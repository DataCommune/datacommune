"""mypy ratchet: fail on any type error that is not in the recorded baseline.

The codebase carries ~500 known mypy errors, so a plain `mypy app/` gate would
be red forever. This gate compares errors by (file, code, message) -- line
numbers are dropped because unrelated edits move them -- and fails only when an
error appears more often than the baseline records. Fixed errors are reported
so the baseline can shrink with `--update`.

`--platform linux` keeps a baseline written on Windows valid on the Ubuntu CI.
mypy runs on a fresh cache every time: a warm incremental cache does not
re-report some errors of cached modules (e.g. `import-untyped`), which once
let a local `--update` drop errors the cold CI run still sees.

Usage (from api/backend):
    uv run python scripts/mypy_ratchet.py            # check
    uv run python scripts/mypy_ratchet.py --update   # rewrite the baseline
"""

import argparse
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
BASELINE = BACKEND_DIR / "mypy-baseline.txt"
TARGETS = ["app/"]
ERROR_LINE = re.compile(r"^(?P<path>[^:]+):\d+(?::\d+)?: error: (?P<rest>.*)$")


def current_errors() -> Counter[str]:
    with tempfile.TemporaryDirectory(prefix="mypy-ratchet-") as cache_dir:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--platform",
                "linux",
                "--cache-dir",
                cache_dir,
                "--no-pretty",
                "--no-error-summary",
                *TARGETS,
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    # mypy exits 1 when it finds errors and 2 when it cannot run at all.
    if proc.returncode not in (0, 1):
        sys.stderr.write(proc.stdout + proc.stderr)
        sys.exit(proc.returncode)
    errors: Counter[str] = Counter()
    for line in proc.stdout.splitlines():
        match = ERROR_LINE.match(line)
        if match:
            path = match["path"].replace("\\", "/")
            errors[f"{path}: {match['rest']}"] += 1
    return errors


def read_baseline() -> Counter[str]:
    if not BASELINE.exists():
        return Counter()
    lines = BASELINE.read_text(encoding="utf-8").splitlines()
    return Counter(line for line in lines if line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update", action="store_true", help="rewrite the baseline")
    args = parser.parse_args()

    errors = current_errors()
    if args.update:
        BASELINE.write_text(
            "".join(f"{e}\n" for e in sorted(errors.elements())), encoding="utf-8"
        )
        print(f"mypy baseline written: {sum(errors.values())} errors")
        return 0

    baseline = read_baseline()
    new = errors - baseline
    fixed = baseline - errors
    if fixed:
        print(
            f"{sum(fixed.values())} baseline errors are gone -- shrink the baseline "
            "with `uv run python scripts/mypy_ratchet.py --update`."
        )
    if new:
        print(f"{sum(new.values())} new mypy errors (not in mypy-baseline.txt):")
        for error in sorted(new.elements()):
            print(f"  {error}")
        return 1
    print(f"mypy ratchet OK: {sum(errors.values())} known errors, none new")
    return 0


if __name__ == "__main__":
    sys.exit(main())
