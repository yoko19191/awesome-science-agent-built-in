#!/usr/bin/env python3
"""Regression for handoff item recovery-4: figures must use a colorblind-safe colormap (not RdYlGn)
and legible annotation/legend fonts.

The v2-recovery-r1 figures used the non-colorblind-safe RdYlGn colormap and 8-9 pt annotation/legend
fonts. The child exposes the palette/font choices as module constants (SEQ_CMAP, ANNOT_FONTSIZE,
LEGEND_FONTSIZE), absent in the frozen parent (ImportError -> FAIL there), and no figure uses RdYlGn
as a colormap/palette value.

Run:  python3 assets/eval/test_figure_palette.py
      SKILL_PKG=/path/to/pkg python3 assets/eval/test_figure_palette.py
Exit: 0 all passed | 1 a test failed | 2 everything skipped (a failure, not a pass).
"""
from __future__ import annotations

import os
import pathlib
import sys

_PKG = pathlib.Path(os.environ.get("SKILL_PKG", pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PKG / "scripts"))

# Perceptually-uniform, colorblind-safe sequential colormaps (matplotlib).
_CB_SAFE = {"viridis", "cividis", "magma", "plasma", "inferno"}

try:
    from generate_figures import SEQ_CMAP, ANNOT_FONTSIZE, LEGEND_FONTSIZE  # absent in parent
    _IMPORT_ERR = None
except Exception as exc:
    _IMPORT_ERR = exc


def test_colorblind_safe_colormap():
    assert SEQ_CMAP in _CB_SAFE, f"SEQ_CMAP {SEQ_CMAP!r} is not a colorblind-safe sequential map"
    assert SEQ_CMAP != "RdYlGn", "SEQ_CMAP must not be RdYlGn"
    return f"SEQ_CMAP = {SEQ_CMAP} (colorblind-safe)"


def test_no_rdylgn_colormap_value_in_source():
    src = (_PKG / "scripts" / "generate_figures.py").read_text(encoding="utf-8")
    # DISCRIMINATION: the frozen parent used cmap="RdYlGn"/palette="RdYlGn"; the child uses none.
    assert '"RdYlGn"' not in src and "'RdYlGn'" not in src, \
        "generate_figures.py still uses RdYlGn as a colormap/palette value"
    return "no RdYlGn colormap/palette value in generate_figures.py"


def test_fonts_legible():
    assert ANNOT_FONTSIZE >= 11, f"ANNOT_FONTSIZE {ANNOT_FONTSIZE} < 11 (too small)"
    assert LEGEND_FONTSIZE >= 11, f"LEGEND_FONTSIZE {LEGEND_FONTSIZE} < 11 (too small)"
    return f"annotation {ANNOT_FONTSIZE} pt / legend {LEGEND_FONTSIZE} pt (legible)"


TESTS = [test_colorblind_safe_colormap, test_no_rdylgn_colormap_value_in_source, test_fonts_legible]


def main() -> int:
    if _IMPORT_ERR is not None:
        print(f"FAIL import: generate_figures palette/font constants unavailable -> {_IMPORT_ERR}")
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
