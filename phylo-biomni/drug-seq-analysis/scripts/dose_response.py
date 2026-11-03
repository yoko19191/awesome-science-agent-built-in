#!/usr/bin/env python3
"""Concentration-response and transcriptomic point of departure (tPOD) from DE contrasts.

Reads de_all_contrasts.csv (per compound x dose vs vehicle). For each compound with >= MIN_DOSES
doses, orients its top-dose DEGs by their top-dose sign, subtracts the lowest-dose aggregate as the
within-compound reference, and interpolates the concentration at which that signed response crosses a
benchmark response (BMR). That concentration is the tPOD.

tPOD is method-sensitive by an order of magnitude, so the method and BMR are always recorded, and a
compound with fewer than MIN_DOSES doses returns tpod_uM = null (never a fabricated value).

CLI:
  python3 dose_response.py --de tables/de_all_contrasts.csv --outdir /mnt/results [--bmr 1.0]
writes <outdir>/tables/dose_response.csv and prints a JSON summary.
"""
from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np
import pandas as pd

MIN_DOSES = 4
BMR = 1.0
MIN_POSITIVE_DOSE_ASSOCIATION = 0.0
FLAT_RESPONSE_ATOL = 1e-8
TPOD_METHOD = (
    "top-dose-direction-aligned mean log2FC above the lowest-dose reference "
    "versus log10(dose); linear interpolation to BMR=1.0 log2FC"
)


def _tpod_for_compound(sub: pd.DataFrame, bmr: float) -> dict:
    doses = sorted(sub["dose_uM"].unique())
    out = {"n_doses": len(doses), "doses_uM": [float(d) for d in doses],
           "tpod_uM": None, "tpod_method": TPOD_METHOD, "bmr_log2fc": bmr,
           "monotonic": None, "dose_association_rho": None,
           "response_status": "not_estimable", "n_responsive_genes": 0}
    if len(doses) < MIN_DOSES:
        out["response_status"] = "not_estimable_insufficient_doses"
        out["reason"] = f"only {len(doses)} doses (<{MIN_DOSES}); tPOD not estimated"
        return out
    if any(not np.isfinite(d) or d <= 0 for d in doses):
        out["response_status"] = "not_estimable_invalid_doses"
        out["reason"] = "dose values must be finite and positive for log10-dose interpolation"
        return out
    top = max(doses)
    top_rows = sub[(sub["dose_uM"] == top) & (sub["is_sig"])].copy()
    top_rows["log2FoldChange"] = pd.to_numeric(top_rows["log2FoldChange"], errors="coerce")
    top_rows = top_rows[np.isfinite(top_rows["log2FoldChange"])]
    top_rows = top_rows[top_rows["log2FoldChange"] != 0]
    direction = top_rows.groupby("gene_id")["log2FoldChange"].mean().apply(np.sign)
    responsive = set(direction.index)
    out["n_responsive_genes"] = len(responsive)
    if not responsive:
        out["response_status"] = "null_response"
        out["reason"] = "no DEGs at the top dose; tPOD not estimable"
        return out
    aligned = []
    for d in doses:
        dose_rows = sub[(sub["dose_uM"] == d) & (sub["gene_id"].isin(responsive))].copy()
        dose_rows["log2FoldChange"] = pd.to_numeric(
            dose_rows["log2FoldChange"], errors="coerce"
        )
        dose_rows = dose_rows[np.isfinite(dose_rows["log2FoldChange"])]
        per_gene = dose_rows.groupby("gene_id")["log2FoldChange"].mean()
        matched = per_gene.index.intersection(direction.index)
        if len(matched) != len(direction):
            out["response_status"] = "not_estimable_incomplete_effects"
            out["reason"] = (
                f"dose {float(d):g} lacks finite effects for one or more top-dose responsive genes"
            )
            return out
        aligned.append(float((per_gene.loc[matched] * direction.loc[matched]).mean()))
    aligned = np.asarray(aligned, dtype=float)
    response = aligned - aligned[0]
    out["lowest_dose_reference"] = float(aligned[0])
    out["lowest_dose_referenced_signed_response"] = [float(x) for x in response]
    if np.allclose(response, 0.0, atol=FLAT_RESPONSE_ATOL, rtol=0.0):
        out["response_status"] = "null_response"
        out["reason"] = "flat signed response after lowest-dose referencing; tPOD is null"
        return out
    log10d = np.log10(np.array(doses))
    # Rank correlation is recorded and required to be positive; this prevents a downward or
    # unordered excursion from being relabelled as increasing pharmacologic response.
    rho = float(np.corrcoef(np.argsort(np.argsort(log10d)),
                            np.argsort(np.argsort(response)))[0, 1])
    out["dose_association_rho"] = rho
    out["monotonic"] = bool(rho > 0)
    if not np.isfinite(rho) or rho <= MIN_POSITIVE_DOSE_ASSOCIATION:
        out["response_status"] = "null_response"
        out["reason"] = "signed response has no positive dose association; tPOD is null"
        return out
    # First crossing of BMR: interpolate on log10-dose.
    tpod = None
    for i in range(1, len(response)):
        if response[i - 1] < bmr <= response[i]:
            frac = (bmr - response[i - 1]) / (response[i] - response[i - 1])
            log_tpod = log10d[i - 1] + frac * (log10d[i] - log10d[i - 1])
            tpod = float(10 ** log_tpod)
            break
    out["tpod_uM"] = tpod
    if tpod is None:
        out["response_status"] = "null_response"
        out["reason"] = "lowest-dose-referenced signed response never reaches BMR across the tested range"
    else:
        out["response_status"] = "estimated"
    return out


def run(de_path, outdir, bmr: float = BMR) -> dict:
    outdir = pathlib.Path(outdir)
    (outdir / "tables").mkdir(parents=True, exist_ok=True)
    de = pd.read_csv(de_path)
    if "contrast_family" not in de.columns:
        raise ValueError("DE table is missing semantic contrast_family")
    de = de[
        de["compound"].notna() & (de["contrast_family"] == "treatment_vs_vehicle")
    ]
    rows, summary = [], {}
    for comp, sub in de.groupby("compound"):
        res = _tpod_for_compound(sub, bmr)
        summary[comp] = res
        response = res.get(
            "lowest_dose_referenced_signed_response", [np.nan] * res["n_doses"]
        )
        for d, value in zip(res["doses_uM"], response):
            n_sig = int(sub[(sub["dose_uM"] == d) & (sub["is_sig"])].shape[0])
            rows.append({"compound": comp, "dose_uM": d, "n_sig": n_sig,
                         "lowest_dose_referenced_signed_response": value,
                         "response_status": res["response_status"],
                         "response_reason": res.get("reason", ""),
                         "tpod_method": res["tpod_method"],
                         "tpod_uM": res["tpod_uM"], "n_doses": res["n_doses"]})
    columns = [
        "compound", "dose_uM", "n_sig", "lowest_dose_referenced_signed_response",
        "response_status", "response_reason", "tpod_method", "tpod_uM", "n_doses",
    ]
    pd.DataFrame(rows, columns=columns).to_csv(
        outdir / "tables" / "dose_response.csv", index=False
    )
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Dose-response and tPOD from DE contrasts.")
    ap.add_argument("--de", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--bmr", type=float, default=BMR)
    args = ap.parse_args()
    summary = run(args.de, args.outdir, args.bmr)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
