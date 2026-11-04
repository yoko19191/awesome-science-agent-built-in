#!/usr/bin/env python3
"""Regression for Review Stop 4A findings 3/4/6: the dependency/commercial-use inventory must be
DERIVED from declared + executed dependencies, each bound to an authoritative licence-evidence URL,
and the gate must fail closed on (a) a licence claim conflicting with the authoritative evidence
(ADFR 'Apache 2.0' -> actually LGPL2), (b) Open Babel's copyleft mislabelled as a commercial
prohibition, and (c) a self-clear (license_gate: PASS) while a hold is open -- never self-clearing.

The v2 source had no such mechanism. This test imports scripts/commercial_inventory.py -- absent in
the frozen source (ImportError -> FAIL there) -- and on the repaired package proves the mechanism
detects each defect on synthetic fixtures, ignores corrections, and passes a corrected package while
KEEPING the overall status at HOLD.

Run:  python3 assets/eval/test_commercial_inventory.py
Exit: 0 all passed | 1 a test failed | 2 everything skipped (a failure, not a pass).
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

_PKG = pathlib.Path(os.environ.get("SKILL_PKG", pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PKG / "scripts"))

try:
    from commercial_inventory import (build_inventory, assert_commercial_inventory_consistent,
                                      CommercialInventoryError)                 # absent in source
    _IMPORT_ERR = None
except Exception as exc:
    _IMPORT_ERR = exc

_SCRIPT_STUB = (
    "import subprocess, shutil\n"
    "shutil.which('prepare_receptor')  # ADFR fallback exercised\n"
    "subprocess.run(['vina', '--help'])\n"
    "# also references mk_prepare_receptor and obabel\n"
    "import pandas, numpy, rdkit\n"
)


def _make_pkg(tmp, data_sources: str, license_gate: str) -> str:
    p = pathlib.Path(tmp)
    (p / "scripts").mkdir(parents=True, exist_ok=True)
    (p / "scripts" / "prepare_docking.py").write_text(_SCRIPT_STUB)
    (p / "DATA_SOURCES.md").write_text(data_sources)
    (p / "eval.yaml").write_text("commercial_use:\n  - license_gate: " + license_gate + "\n")
    return str(p)


_ADFR_WRONG = "ADFR prepare_receptor (Apache 2.0) is the receptor-prep fallback.\n"
_ADFR_RIGHT = ("ADFR prepare_receptor (LGPL2) is the fallback "
               "(https://ccsb.scripps.edu/adfr/license/).\n")
_ADFR_CORRECTION = ("ADFR prepare_receptor (LGPL2, https://ccsb.scripps.edu/adfr/license/) "
                    "-- corrected from the wrong 'Apache 2.0' claim.\n")
_OB_WRONG = "| Open Babel | 3.1.0 | GPL-2.0 | Restricted (copyleft) -- avoid for commercial use |\n"
_OB_RIGHT = ("| Open Babel | 3.1.0 | GPL-2.0 | Copyleft: source-distribution obligation on "
             "derivatives, not a commercial-use ban; avoided by default |\n")
_CHEMBL = "ChEMBL is CC BY-SA 3.0 (attribution + share-alike).\n"


def test_detects_adfr_apache_conflict():
    with tempfile.TemporaryDirectory() as tmp:
        pkg = _make_pkg(tmp, _ADFR_WRONG + _OB_RIGHT + _CHEMBL, "HOLD")
        inv = build_inventory(pkg)
        assert "ADFR prepare_receptor" in inv["conflicts"], "did not flag ADFR Apache-2.0 conflict"
        try:
            assert_commercial_inventory_consistent(inv)
            raise AssertionError("gate PASSED despite ADFR licence conflict")
        except CommercialInventoryError:
            return "ADFR 'Apache 2.0' flagged as a licence conflict"


def test_detects_openbabel_prohibition_conflict():
    with tempfile.TemporaryDirectory() as tmp:
        pkg = _make_pkg(tmp, _ADFR_RIGHT + _OB_WRONG + _CHEMBL, "HOLD")
        inv = build_inventory(pkg)
        assert "Open Babel" in inv["conflicts"], "did not flag Open Babel commercial mislabel"
        try:
            assert_commercial_inventory_consistent(inv)
            raise AssertionError("gate PASSED despite Open Babel mislabel")
        except CommercialInventoryError:
            return "Open Babel 'Restricted/avoid for commercial' flagged as a conflict"


def test_detects_self_clear_with_holds():
    with tempfile.TemporaryDirectory() as tmp:
        pkg = _make_pkg(tmp, _ADFR_RIGHT + _OB_RIGHT + _CHEMBL, "PASS")   # correct licences, but PASS
        inv = build_inventory(pkg)
        assert inv["self_clear_detected"], "did not detect license_gate: PASS self-clear"
        assert inv["holds"], "expected open holds (ChEMBL share-alike)"
        try:
            assert_commercial_inventory_consistent(inv)
            raise AssertionError("gate PASSED despite self-clear while a hold is open")
        except CommercialInventoryError:
            return "license_gate: PASS with an open hold -> GateFailure"


def test_corrected_package_passes_with_hold():
    with tempfile.TemporaryDirectory() as tmp:
        pkg = _make_pkg(tmp, _ADFR_RIGHT + _OB_RIGHT + _CHEMBL, "HOLD")
        inv = build_inventory(pkg)
        assert not inv["conflicts"], f"unexpected conflicts on corrected pkg: {inv['conflicts']}"
        assert not inv["self_clear_detected"], "unexpected self-clear on corrected pkg"
        summary = assert_commercial_inventory_consistent(inv)   # must NOT raise
        # DISCRIMINATION: passing the gate does NOT mean cleared -- overall stays HOLD.
        assert summary["overall_status"] == "HOLD", "corrected package must remain HOLD, not cleared"
        assert "ChEMBL" in inv["holds"], "ChEMBL share-alike obligation must remain a hold"
        return "corrected package passes gate; overall stays HOLD; ChEMBL hold retained"


def test_correction_not_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        pkg = _make_pkg(tmp, _ADFR_CORRECTION + _OB_RIGHT + _CHEMBL, "HOLD")
        inv = build_inventory(pkg)
        assert "ADFR prepare_receptor" not in inv["conflicts"], \
            "a correction ('LGPL2 ... not Apache 2.0') was wrongly flagged as a conflict"
        return "correction text not counted as a conflict"


def test_material_dep_is_evidence_bound():
    with tempfile.TemporaryDirectory() as tmp:
        pkg = _make_pkg(tmp, _ADFR_RIGHT + _OB_RIGHT + _CHEMBL, "HOLD")
        inv = build_inventory(pkg)
        for d in inv["dependencies"]:
            if d["distribution_role"] in {"used", "included"}:
                assert d["evidence_url"], f"material dep {d['name']} has no evidence URL"
        return "every material dependency is bound to an authoritative evidence URL"


TESTS = [test_detects_adfr_apache_conflict, test_detects_openbabel_prohibition_conflict,
         test_detects_self_clear_with_holds, test_corrected_package_passes_with_hold,
         test_correction_not_flagged, test_material_dep_is_evidence_bound]


def main() -> int:
    if _IMPORT_ERR is not None:
        print(f"FAIL import: commercial_inventory unavailable -> {_IMPORT_ERR}")
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
