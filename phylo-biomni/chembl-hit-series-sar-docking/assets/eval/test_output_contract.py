#!/usr/bin/env python3
"""Regression for Review Stop 4A finding 1: a fail-closed output-artifact contract gate must confirm
every declared output artifact exists on disk with the declared content (variant columns, the
WT/unspecified composition bucket, docking-box provenance incl. excluded_additives) BEFORE facts are
written.

The v2 source had no such gate, so 'expected output artifacts were not confirmed'. This test imports
report_qc.assert_output_contract -- absent in the frozen source (ImportError -> FAIL there) -- and on
the repaired package proves it PASSES on a complete fixture and FAILS on each specific gap.

Run:  python3 assets/eval/test_output_contract.py
Exit: 0 all passed | 1 a test failed | 2 everything skipped (a failure, not a pass).
"""
from __future__ import annotations

import csv
import json
import os
import pathlib
import sys
import tempfile

_PKG = pathlib.Path(os.environ.get("SKILL_PKG", pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PKG / "scripts"))

try:
    from report_qc import assert_output_contract, GateFailure   # absent in source -> ImportError
    _IMPORT_ERR = None
except Exception as exc:
    _IMPORT_ERR = exc


def _build_results(tmp, *, wt=True, excluded=True, variant_cols=True, box_provenance=True):
    """A synthetic /mnt/results tree; toggles drop one declared piece to prove the gate catches it."""
    r = pathlib.Path(tmp)
    (r / "report_EGFR_hit_triage.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 30000)
    (r / "EGFR_hit_triage_report.html").write_text("<html>" + "x" * 3000 + "</html>")
    cols = ["molecule_chembl_id", "smiles", "hit_score", "tier", "cluster_id", "series"]
    if variant_cols:
        cols += ["variant_best", "variant_summary", "n_variants", "assay_variant_accession"]
    with open(r / "EGFR_hits_scored_clustered.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(cols)
        w.writerow(["C1", "CCO", 0.9, "Tier 1", 3, "Series A"] + (["WT", "WT", 1, ""] if variant_cols else []))
    with open(r / "EGFR_docking_results.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["chembl_id", "series", "variant", "pchembl", "docking_score"])
        w.writerow(["C1", "Series A", "WT", 8.2, -9.1])
    with open(r / "EGFR_series_stats.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["series", "size"]); w.writerow(["Series A", 79])
    comp = [{"variant": "WT/unspecified", "n": 10}] if wt else [{"variant": "L858R", "n": 10}]
    (r / "variant_composition.json").write_text(json.dumps(
        {"applied_filter": "any", "pooled": True, "composition": comp}))
    (r / "EGFR_sar_results.json").write_text(json.dumps({"Series A": {"recommendation": "EXPAND - x"}}))
    box = {"receptor_variant": "WT"}
    if box_provenance:
        box["pdb_selection"] = {"mode": "auto", "rationale": "best-resolution ligand-bound"}
    if excluded:
        box["excluded_additives"] = ["EDO", "HOH", "NO3"]
    (r / "docking_box.json").write_text(json.dumps(box))
    return r


def test_complete_fixture_passes():
    with tempfile.TemporaryDirectory() as tmp:
        _build_results(tmp)
        res = assert_output_contract(target="EGFR", results_dir=tmp)
        assert res["n_confirmed"] >= 8, f"expected >=8 artifacts confirmed, got {res['n_confirmed']}"
    return "complete fixture confirmed"


def _expect_fail(label, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        _build_results(tmp, **kw)
        try:
            assert_output_contract(target="EGFR", results_dir=tmp)
        except GateFailure:
            return
        raise AssertionError(f"gate did NOT fail when {label}")


def test_missing_excluded_additives_fails():   # finding 7 enforced at the output level
    _expect_fail("docking_box.json lacks excluded_additives", excluded=False)
    return "missing excluded_additives -> GateFailure"


def test_missing_wt_bucket_fails():
    _expect_fail("variant_composition.json has no WT/unspecified bucket", wt=False)
    return "missing WT/unspecified bucket -> GateFailure"


def test_missing_variant_columns_fails():
    _expect_fail("hits CSV lacks variant provenance columns", variant_cols=False)
    return "missing variant columns -> GateFailure"


def test_missing_pdb_provenance_fails():
    _expect_fail("docking_box.json lacks pdb_selection provenance", box_provenance=False)
    return "missing pdb_selection -> GateFailure"


def test_missing_artifact_fails():
    with tempfile.TemporaryDirectory() as tmp:
        r = _build_results(tmp)
        (r / "EGFR_docking_results.csv").unlink()
        try:
            assert_output_contract(target="EGFR", results_dir=tmp)
        except GateFailure:
            return "missing artifact -> GateFailure"
        raise AssertionError("gate did NOT fail on a missing artifact")


TESTS = [test_complete_fixture_passes, test_missing_excluded_additives_fails,
         test_missing_wt_bucket_fails, test_missing_variant_columns_fails,
         test_missing_pdb_provenance_fails, test_missing_artifact_fails]


def main() -> int:
    if _IMPORT_ERR is not None:
        print(f"FAIL import: report_qc.assert_output_contract unavailable -> {_IMPORT_ERR}")
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
