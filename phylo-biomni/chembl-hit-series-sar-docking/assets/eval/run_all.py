#!/usr/bin/env python3
"""Run every regression in this directory against one package and summarise.

Each test honours SKILL_PKG=<pkg-dir> (default: the package this runner ships in), so the same suite
runs against the frozen source (expected: failures -- the defects are present) and the repaired
package (expected: all pass). Each test is a separate subprocess so module imports never leak between
packages.

Run:  python3 assets/eval/run_all.py            # tests the bundled package
      SKILL_PKG=/path/to/other python3 assets/eval/run_all.py
Exit: 0 all suites passed | 1 any suite failed | 2 any suite fully skipped.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_TESTS = sorted(p.name for p in _HERE.glob("test_*.py"))


def main() -> int:
    pkg = os.environ.get("SKILL_PKG", str(_HERE.parents[1]))
    env = dict(os.environ, SKILL_PKG=pkg, PYTHONDONTWRITEBYTECODE="1")
    print(f"Regression suite over package: {pkg}\n" + "=" * 72)
    worst = 0
    for name in _TESTS:
        r = subprocess.run([sys.executable, str(_HERE / name)], env=env,
                           capture_output=True, text=True)
        tail = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
        verdict = {0: "PASS", 1: "FAIL", 2: "SKIP"}.get(r.returncode, f"?{r.returncode}")
        print(f"[{verdict}] {name:42} {tail}")
        worst = max(worst, r.returncode)
    print("=" * 72)
    print({0: "ALL SUITES PASSED", 1: "A SUITE FAILED", 2: "A SUITE SKIPPED"}.get(worst, "ERROR"))
    return worst


if __name__ == "__main__":
    sys.exit(main())
