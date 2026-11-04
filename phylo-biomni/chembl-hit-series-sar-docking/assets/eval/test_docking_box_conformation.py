#!/usr/bin/env python3
"""Regression for family F10 (scientific_or_fixture_specific_defect), acceptance check
candidate_specific_negative_control_and_edge_case.

The exact scientific review recorded that receptor conformation / docking-box choice was not
discussed as a driver of the near-zero docking-vs-potency correlation: a docking box built from a
large co-crystal ligand is permissive and scores poses across a wide region rather than one focused
pocket. The repair DERIVES this from box geometry (prepare_docking.box_diagnostics) and enforces the
surfaced caveat (report_qc.assert_docking_box_diagnostics) -- it does not merely restate a caveat and
it encodes no structure-specific answer (the same rule flags any oversized box, passes any focused
box). Both functions are absent in the frozen parent (ImportError -> FAIL there).

The edge case is a DISTINCT large box (38 x 26 x 28 A), deliberately not the review's 8A27 box
(34.2 x 24.1 x 30.0 A), so the test exercises the mechanism without encoding the sample answer.

Run:  python3 assets/eval/test_docking_box_conformation.py
      SKILL_PKG=/path/to/pkg python3 assets/eval/test_docking_box_conformation.py
Exit: 0 all passed | 1 a test failed | 2 everything skipped (a failure, not a pass).
"""
from __future__ import annotations

import os
import pathlib
import sys

_PKG = pathlib.Path(os.environ.get("SKILL_PKG", pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PKG / "scripts"))

try:
    from prepare_docking import box_diagnostics, build_box_info   # absent in parent -> ImportError
    from report_qc import assert_docking_box_diagnostics, GateFailure
    _IMPORT_ERR = None
except Exception as exc:
    _IMPORT_ERR = exc


def _box(sx, sy, sz):
    return {"size_x": float(sx), "size_y": float(sy), "size_z": float(sz)}


# The v2-recovery-r1 box dict, replicated for discrimination: it carried NO derived diagnostics.
def _old_box_dict():
    return {"center_x": 0.0, "center_y": 0.0, "center_z": 0.0,
            "size_x": 38.0, "size_y": 26.0, "size_z": 28.0, "pdb_id": "XXXX",
            "ligand_resnames": ["LIG"], "excluded_additives": [], "receptor_variant": "WT",
            "pdb_selection": {"mode": "auto"}}


def test_candidate_specific_negative_control_and_edge_case():
    # NEGATIVE CONTROL: a focused pocket box (24 A cube) is NOT permissive and needs no caveat;
    # the gate must pass and must not raise a false caveat.
    focused = box_diagnostics(_box(24.0, 24.0, 24.0), n_ligand_atoms=22)
    assert focused["box_permissive"] is False, f"focused box wrongly flagged permissive: {focused}"
    assert focused["docking_caveats"] == [], "focused box must carry no permissiveness caveat"
    ok = assert_docking_box_diagnostics({**_box(24.0, 24.0, 24.0), **focused})
    assert ok["box_permissive"] is False and ok["n_caveats"] == 0, ok

    # EDGE CASE: a DISTINCT large permissive box (38 x 26 x 28 A, not the review's 8A27 numbers) is
    # flagged and MUST surface the caveat; the gate passes only when the caveat is present.
    big = box_diagnostics(_box(38.0, 26.0, 28.0), n_ligand_atoms=58)
    assert big["box_permissive"] is True, f"large box not flagged permissive: {big}"
    assert any("permissive" in str(c).lower() for c in big["docking_caveats"]), \
        "permissive box did not surface a permissiveness caveat"
    ok2 = assert_docking_box_diagnostics({**_box(38.0, 26.0, 28.0), **big})
    assert ok2["box_permissive"] is True and ok2["n_caveats"] >= 1, ok2

    # DISCRIMINATION 1: strip the caveat from a permissive box -> gate fails closed.
    stripped = {**_box(38.0, 26.0, 28.0), **big, "docking_caveats": []}
    try:
        assert_docking_box_diagnostics(stripped)
        raise AssertionError("gate did NOT fail on a permissive box with the caveat stripped")
    except GateFailure:
        pass

    # DISCRIMINATION 2: the OLD box dict (no derived diagnostics at all) -> gate fails closed.
    try:
        assert_docking_box_diagnostics(_old_box_dict())
        raise AssertionError("gate did NOT fail on the old box dict lacking derived diagnostics")
    except GateFailure:
        pass
    return ("focused box passes with no caveat; distinct 38x26x28 permissive box requires the "
            "surfaced caveat; old undiagnosed box rejected")


def test_build_box_info_carries_diagnostics():
    # build_box_info must now enrich docking_box.json with the derived diagnostic fields, without
    # disturbing the finding-7 fields (excluded_additives / pdb_selection / receptor_variant).
    box = build_box_info([[0, 0, 0], [2, 0, 0], [0, 2, 0], [0, 0, 2]], {"KY9"},
                         {"NO3", "EDO", "HOH"}, "8A27", "WT", "RCSB: 0 mutations",
                         {"mode": "auto", "rationale": "best-res ligand-bound"})
    for k in ("box_volume_A3", "box_max_dim_A", "box_permissive", "docking_caveats"):
        assert k in box, f"build_box_info missing derived field {k}"
    assert box["excluded_additives"] == ["EDO", "HOH", "NO3"] and box["pdb_selection"]["mode"] == "auto"
    return "build_box_info carries derived diagnostics alongside finding-7 provenance"


TESTS = [test_candidate_specific_negative_control_and_edge_case,
         test_build_box_info_carries_diagnostics]


def main() -> int:
    if _IMPORT_ERR is not None:
        print(f"FAIL import: prepare_docking.box_diagnostics / report_qc."
              f"assert_docking_box_diagnostics unavailable -> {_IMPORT_ERR}")
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
