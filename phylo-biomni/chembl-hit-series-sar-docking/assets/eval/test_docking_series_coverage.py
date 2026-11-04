#!/usr/bin/env python3
"""Regression for Review Stop 4A finding 5: docking-representative selection must follow the SAR
recommendation priority (EXPAND first), so every EXPAND-recommended series gets a docked
representative -- not the first 15 series in table order.

The v2 source selected hits[...]['series'].unique()[:15] (table order), so EXPAND Series Q and T were
never docked. This test imports prepare_docking.select_series_for_docking -- absent in the frozen
source (ImportError -> FAIL there) -- and on the repaired package proves an EXPAND series placed far
down the table order is still covered, while the OLD table-order rule (replicated here) misses it.

Run:  python3 assets/eval/test_docking_series_coverage.py
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
    from prepare_docking import select_series_for_docking, rank_series_for_docking  # absent in source
    _IMPORT_ERR = None
except Exception as exc:
    _IMPORT_ERR = exc


def _fixture():
    """A hits table where EXPAND 'Series T' appears LAST, past the old unique()[:15] truncation."""
    rows = []
    for L in [chr(ord("A") + i) for i in range(16)]:          # 16 filler series first (A..P)
        rows.append({"molecule_chembl_id": f"C_{L}", "smiles": "CCO", "series": f"Series {L}",
                     "best_pchembl": 6.0, "hit_score": 0.5, "variant_best": "WT/unspecified"})
    rows.append({"molecule_chembl_id": "C_T", "smiles": "CCO", "series": "Series T",
                 "best_pchembl": 8.9, "hit_score": 0.9, "variant_best": "L858R"})
    hits = pd.DataFrame(rows)
    sar = {"Series T": {"recommendation": "EXPAND - High potency, good LE, sufficient SAR coverage"},
           "Series O": {"recommendation": "EXPAND - High potency"},
           "Series A": {"recommendation": "MONITOR - marginal"}}
    return hits, sar


def _old_table_order(hits):
    """The v2 defect, replicated, for discrimination."""
    return list(hits[hits["series"] != "Singleton/Other"]["series"].unique()[:15])


def test_expand_series_is_covered():
    hits, sar = _fixture()
    sel, prov = select_series_for_docking(hits, sar_results=sar,
                                          dock_top_per_series=3, dock_top_overall=10)
    covered = {x["series"] for x in sel}
    # RIGHT: the EXPAND series far down the table is covered.
    assert "Series T" in covered, "repaired selector did not cover EXPAND Series T"
    assert prov["ordering"] == "sar_recommendation", f"ordering was {prov['ordering']!r}"
    assert not prov["expand_series_missing"], f"EXPAND series left uncovered: {prov['expand_series_missing']}"
    # DISCRIMINATION: the OLD table-order rule (the bug) MISSES it -- so a regression flips this red.
    assert "Series T" not in _old_table_order(hits), \
        "old table-order rule unexpectedly covered Series T -- test not discriminating"
    return f"EXPAND covered {prov['expand_series_covered']}; old rule missed Series T"


def test_ranking_prioritises_expand():
    hits, sar = _fixture()
    ranked = rank_series_for_docking(hits, sar_results=sar)
    # RIGHT: both EXPAND series precede the MONITOR series in priority order.
    assert ranked.index("Series T") < ranked.index("Series A"), "EXPAND not ranked above MONITOR"
    assert ranked.index("Series O") < ranked.index("Series A"), "EXPAND not ranked above MONITOR"
    return "EXPAND series ranked ahead of MONITOR"


TESTS = [test_expand_series_is_covered, test_ranking_prioritises_expand]


def main() -> int:
    if _IMPORT_ERR is not None:
        print(f"FAIL import: prepare_docking.select_series_for_docking unavailable -> {_IMPORT_ERR}")
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
        return 2   # nothing ran at all
    return 0


if __name__ == "__main__":
    sys.exit(main())
