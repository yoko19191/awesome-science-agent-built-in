#!/usr/bin/env python3
"""Regression for Review Stop 4A finding 7: docking_box.json must record an explicit
'excluded_additives' field (the crystallisation additives/ions kept out of the box), so the
additive_ignore_list_in_both_paths invariant is provable from the artifact, not only console output.

The v2 source applied the ignore list but omitted the field. This test imports
prepare_docking.build_box_info -- absent in the frozen source (ImportError -> FAIL there) -- and on
the repaired package proves the field is present and correct, while the OLD inline box dict
(replicated here) lacked it.

Run:  python3 assets/eval/test_docking_box_excluded_additives.py
Exit: 0 all passed | 1 a test failed | 2 everything skipped (a failure, not a pass).
"""
from __future__ import annotations

import os
import pathlib
import sys

_PKG = pathlib.Path(os.environ.get("SKILL_PKG", pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PKG / "scripts"))

try:
    from prepare_docking import build_box_info                 # absent in source -> ImportError
    _IMPORT_ERR = None
except Exception as exc:
    _IMPORT_ERR = exc


def _old_box_dict(pdb_id, ligand_resnames, receptor_variant, pdb_selection):
    """The v2 box_info, replicated -- it had no excluded_additives field (discrimination)."""
    return {"center_x": 0.0, "center_y": 0.0, "center_z": 0.0,
            "size_x": 24.0, "size_y": 24.0, "size_z": 24.0, "pdb_id": pdb_id,
            "ligand_resnames": list(ligand_resnames), "receptor_variant": receptor_variant,
            "pdb_selection": pdb_selection}


def test_excluded_additives_recorded():
    coords = [[0, 0, 0], [2, 0, 0], [0, 2, 0], [0, 0, 2]]
    box = build_box_info(coords, {"KY9"}, {"NO3", "EDO", "HOH"}, "8A27", "WT",
                         "RCSB: 0 mutations", {"mode": "auto", "rationale": "best-res ligand-bound"})
    # RIGHT: the field exists, is sorted, and names exactly the excluded additives.
    assert "excluded_additives" in box, "docking_box has no excluded_additives field (finding 7)"
    assert box["excluded_additives"] == ["EDO", "HOH", "NO3"], \
        f"excluded_additives wrong: {box['excluded_additives']}"
    # provenance and box geometry survive.
    assert box["pdb_selection"]["mode"] == "auto" and box["receptor_variant"] == "WT"
    # DISCRIMINATION: the old inline dict LACKED the field -- so a regression flips this red.
    old = _old_box_dict("8A27", {"KY9"}, "WT", {"mode": "auto"})
    assert "excluded_additives" not in old, "old box dict unexpectedly had the field -- not discriminating"
    return "excluded_additives=['EDO','HOH','NO3']; old dict lacked it"


def test_empty_excluded_is_empty_list():
    box = build_box_info([[0, 0, 0], [1, 0, 0]], {"LIG"}, set(), "1ABC", "unspecified", "src",
                         {"mode": "user_pdb_id", "rationale": "user"})
    assert box["excluded_additives"] == [], "empty exclusion should be [], not missing"
    return "no additives -> excluded_additives=[]"


TESTS = [test_excluded_additives_recorded, test_empty_excluded_is_empty_list]


def main() -> int:
    if _IMPORT_ERR is not None:
        print(f"FAIL import: prepare_docking.build_box_info unavailable -> {_IMPORT_ERR}")
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
