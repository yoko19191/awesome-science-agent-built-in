#!/usr/bin/env python3
"""Regression for family F04 (final_delivery_and_output_manifest_drift), acceptance check
post_staging_result_inventory_matches_declared_contract_and_receipt.

Two source-owned defects are closed here and both make the frozen parent FAIL (ImportError), while
the repaired child PASSES:

  1. process_activities emitted the variant_composition.json key 'variant_filter_applied' while the
     output contract (assets/output_contract.json) required 'applied_filter', so a faithful run
     failed assert_output_contract even though the old fixture -- which hard-coded 'applied_filter'
     -- stayed green (a real test blind spot). The child extracts build_variant_report(...) which
     emits the contract key, so this test exercises the REAL emitted schema, not a stand-in.

  2. Nothing reconciled the WHOLE delivery after staging. The child adds
     report_qc.assert_delivery_receipt, which finalizes the contract only AFTER staging and fails
     closed unless the on-disk inventory matches BOTH the declared contract AND the packaged
     delivery receipt (report/table/figure/provenance/test/manifest). Removing or renaming any
     receipt-named artifact after staging fails it (the distinct edge case).

Run:  python3 assets/eval/test_post_staging_inventory.py
      SKILL_PKG=/path/to/pkg python3 assets/eval/test_post_staging_inventory.py
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
    # assert_delivery_receipt and build_variant_report are BOTH absent in the frozen parent.
    from report_qc import assert_delivery_receipt, assert_output_contract, GateFailure
    from process_activities import build_variant_report
    _IMPORT_ERR = None
except Exception as exc:
    _IMPORT_ERR = exc


# The v2-recovery-r1 writer, replicated for discrimination: it used the WRONG contract key.
def _old_variant_report(filter_selector):
    return {"variant_field_source": "x", "pooled": True,
            "composition": [{"variant": "WT/unspecified", "n_records": 10}],
            "variant_filter_applied": filter_selector}


def _write_variant_composition(path, report):
    pathlib.Path(path).write_text(json.dumps(report, indent=2), encoding="utf-8")


def _stage_full_delivery(tmp):
    """A complete staged /mnt/results tree: report + tables + figures + provenance + test + manifest.

    variant_composition.json is produced by the REAL build_variant_report so the contract key is
    exercised, not hard-coded.
    """
    r = pathlib.Path(tmp)
    (r / "report_EGFR_hit_triage.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 30000)
    (r / "EGFR_hit_triage_report.html").write_text("<html>" + "x" * 3000 + "</html>")
    with open(r / "EGFR_hits_scored_clustered.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["molecule_chembl_id", "smiles", "hit_score", "tier", "cluster_id", "series",
                    "variant_best", "variant_summary", "n_variants", "assay_variant_accession"])
        w.writerow(["C1", "CCO", 0.9, "Tier 1", 3, "Series A", "WT", "WT", 1, ""])
    with open(r / "EGFR_docking_results.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["chembl_id", "series", "variant", "pchembl", "docking_score"])
        w.writerow(["C1", "Series A", "WT", 8.2, -9.1])
    with open(r / "EGFR_series_stats.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["series", "size"]); w.writerow(["Series A", 79])
    (r / "EGFR_sar_results.json").write_text(json.dumps({"Series A": {"recommendation": "EXPAND - x"}}))
    # REAL emitted schema (contract key applied_filter):
    comp = [{"variant": "WT/unspecified", "n_records": 10, "pct_records": 100.0, "n_molecules": 8}]
    _write_variant_composition(
        r / "variant_composition.json",
        build_variant_report(comp, 10, 1, 0, False, "any", 10, 8, None))
    (r / "docking_box.json").write_text(json.dumps(
        {"pdb_selection": {"mode": "auto", "rationale": "best-resolution ligand-bound"},
         "receptor_variant": "WT", "excluded_additives": ["EDO", "HOH", "NO3"]}))
    figs = r / "figures"; figs.mkdir()
    (figs / "fig1_potency_distribution.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 6000)
    prov = r / "provenance"; prov.mkdir()
    (prov / "PROVENANCE_NOTE.md").write_text("auto-captured vs reconstructed steps")
    (prov / "package_manifest.json").write_text(json.dumps({"files": []}))
    (r / "test_receipt.json").write_text(json.dumps({"suite": "deterministic", "exit": 0}))
    return r


def test_applied_filter_key_closes_contract_blindspot():
    with tempfile.TemporaryDirectory() as tmp:
        # RIGHT: the real writer emits the contract key, and assert_output_contract passes on it.
        rep = build_variant_report(
            [{"variant": "WT/unspecified", "n_records": 10, "pct_records": 100.0, "n_molecules": 8}],
            10, 1, 0, False, "WT", 10, 8, None)
        assert "applied_filter" in rep and rep["applied_filter"] == "WT", \
            f"build_variant_report did not emit contract key applied_filter: {sorted(rep)}"
        assert "variant_filter_applied" not in rep, "the legacy wrong key must be gone"
        r = _stage_full_delivery(tmp)
        assert_output_contract(target="EGFR", results_dir=tmp)  # passes on the real schema
        # DISCRIMINATION: the OLD report (wrong key) fails the contract -> the blind spot is real.
        _write_variant_composition(r / "variant_composition.json", _old_variant_report("WT"))
        try:
            assert_output_contract(target="EGFR", results_dir=tmp)
        except GateFailure:
            return "applied_filter passes contract; legacy variant_filter_applied fails it"
        raise AssertionError("contract did NOT fail on the legacy variant_filter_applied key")


def test_post_staging_result_inventory_matches_declared_contract_and_receipt():
    with tempfile.TemporaryDirectory() as tmp:
        _stage_full_delivery(tmp)
        res = assert_delivery_receipt(target="EGFR", results_dir=tmp)
        assert res["n_contract_artifacts"] >= 8, res
        for cat in ("report", "table", "figure", "provenance", "test", "manifest"):
            assert res["categories"].get(cat, 0) >= 1, f"receipt category {cat} not resolved: {res}"
        return (f"inventory == contract ({res['n_contract_artifacts']}) == receipt "
                f"({res['n_receipt_files']} files across {len(res['categories'])} categories)")


def test_removed_receipt_manifest_fails():
    # EDGE CASE: a receipt-named artifact (the per-file manifest) removed after staging -> fail closed.
    with tempfile.TemporaryDirectory() as tmp:
        r = _stage_full_delivery(tmp)
        (r / "provenance" / "package_manifest.json").unlink()
        try:
            assert_delivery_receipt(target="EGFR", results_dir=tmp)
        except GateFailure:
            return "removed receipt-named manifest -> GateFailure"
        raise AssertionError("gate did NOT fail on a removed receipt-named artifact")


def test_renamed_receipt_figure_fails():
    # EDGE CASE: rename the only figure so the figure category cannot resolve -> fail closed.
    with tempfile.TemporaryDirectory() as tmp:
        r = _stage_full_delivery(tmp)
        (r / "figures" / "fig1_potency_distribution.png").rename(r / "figures" / "renamed.txt")
        try:
            assert_delivery_receipt(target="EGFR", results_dir=tmp)
        except GateFailure:
            return "renamed receipt-named figure -> GateFailure"
        raise AssertionError("gate did NOT fail on a renamed receipt-named figure")


TESTS = [test_applied_filter_key_closes_contract_blindspot,
         test_post_staging_result_inventory_matches_declared_contract_and_receipt,
         test_removed_receipt_manifest_fails,
         test_renamed_receipt_figure_fails]


def main() -> int:
    if _IMPORT_ERR is not None:
        print(f"FAIL import: report_qc.assert_delivery_receipt / process_activities."
              f"build_variant_report unavailable -> {_IMPORT_ERR}")
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
