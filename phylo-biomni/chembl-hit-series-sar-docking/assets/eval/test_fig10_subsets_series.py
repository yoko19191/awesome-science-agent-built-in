#!/usr/bin/env python3
"""Regression for handoff item recovery-2: the docking-by-series figure (fig10) must subset to the
top-N series so it stays legible on high-series targets.

The v2-recovery-r1 fig10 drew EVERY docked series (471 for EGFR) and was illegible. The child adds
generate_figures.top_series_for_figure (mirrors fig5's top-N subsetting), absent in the frozen parent
(ImportError -> FAIL there). This test needs no plotting -- it exercises the pure selection helper.

Run:  python3 assets/eval/test_fig10_subsets_series.py
      SKILL_PKG=/path/to/pkg python3 assets/eval/test_fig10_subsets_series.py
Exit: 0 all passed | 1 a test failed | 2 everything skipped (a failure, not a pass).
"""
from __future__ import annotations

import os
import pathlib
import sys

_PKG = pathlib.Path(os.environ.get("SKILL_PKG", pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PKG / "scripts"))

try:
    import pandas as pd
    from generate_figures import top_series_for_figure, MAX_SERIES_IN_FIGURE  # absent in parent
    _IMPORT_ERR = None
except Exception as exc:
    _IMPORT_ERR = exc


def _docked(n_series, per=3):
    rows = [{"series": f"S{i}", "docking_score": -7.0 - (i % 5) * 0.1}
            for i in range(n_series) for _ in range(per)]
    return pd.DataFrame(rows)


def _old_all_series(dock_plot):
    """The v2 fig10 behaviour, replicated: it drew every docked series (no subsetting)."""
    return list(dock_plot.groupby("series")["docking_score"].median().sort_values().index)


def test_high_series_target_is_subset():
    dock = _docked(471)                    # EGFR-scale: 471 docked series
    stats = pd.DataFrame({"size": [10] * 471}, index=[f"S{i}" for i in range(471)])
    chosen = top_series_for_figure(dock, stats)
    # RIGHT: capped to a legible number.
    assert 0 < len(chosen) <= MAX_SERIES_IN_FIGURE, f"fig10 not capped: {len(chosen)}"
    # DISCRIMINATION: the old all-series rule would have drawn all 471 -> a regression flips this red.
    assert len(_old_all_series(dock)) == 471 and len(_old_all_series(dock)) > MAX_SERIES_IN_FIGURE, \
        "old rule unexpectedly subset -- test not discriminating"
    return f"471 docked series -> fig10 shows {len(chosen)} (cap {MAX_SERIES_IN_FIGURE}); old drew 471"


def test_small_target_keeps_all():
    dock = _docked(5)
    stats = pd.DataFrame({"size": [10] * 5}, index=[f"S{i}" for i in range(5)])
    chosen = top_series_for_figure(dock, stats)
    assert len(chosen) == 5, f"a 5-series target should keep all 5, got {len(chosen)}"
    return "5-series target keeps all 5"


def test_falls_back_without_series_stats():
    dock = _docked(40)
    chosen = top_series_for_figure(dock, series_stats=None)
    assert 0 < len(chosen) <= MAX_SERIES_IN_FIGURE, chosen
    return "no series_stats -> falls back to most-docked series, still capped"


TESTS = [test_high_series_target_is_subset, test_small_target_keeps_all,
         test_falls_back_without_series_stats]


def main() -> int:
    if _IMPORT_ERR is not None:
        print(f"FAIL import: generate_figures.top_series_for_figure unavailable -> {_IMPORT_ERR}")
        print(f"\n0 passed, 1 failed, 0 skipped  (package: {_PKG})")
        return 1
    passed = failed = 0
    for t in TESTS:
        try:
            print(f"PASS {t.__name__}: {t()}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, 0 skipped  (package: {_PKG})")
    if failed:
        return 1
    if passed == 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
