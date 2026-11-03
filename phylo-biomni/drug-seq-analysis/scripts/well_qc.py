#!/usr/bin/env python3
"""Per-well QC, low-quality flagging, and plate/positional diagnostics for DRUG-seq.

Wells are experimental units, NOT cells. Metrics are per well: total UMIs, genes detected, and
(when available from the FASTQ front end) barcode assignment rate. Low-quality wells are DROPPED,
never imputed. Positional/edge structure is diagnosed so it cannot masquerade as compound biology.

CLI:
  python3 well_qc.py --counts counts.tsv --platemap platemap.csv --outdir /mnt/results \
      [--min-umi-frac 0.25] [--min-genes-frac 0.25] [--assignment-rate 1.0]
writes  <outdir>/tables/well_qc.csv  and prints a JSON summary to stdout.
"""
from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np
import pandas as pd

import scientific_logic

# Stop-gate default: below this whitelisted-barcode assignment rate the plate map / whitelist is
# almost certainly wrong (transposed map, wrong whitelist), so the run must stop and reconcile.
MIN_ASSIGNMENT_RATE = 0.5


def load_inputs(counts_path, platemap_path):
    counts = pd.read_csv(counts_path, sep="\t", index_col=0)
    meta = pd.read_csv(platemap_path)
    meta, vehicle_identity = scientific_logic.normalize_control_roles(meta)
    missing = [w for w in meta["well_id"] if w not in counts.columns]
    if missing:
        raise ValueError(f"{len(missing)} platemap wells absent from counts matrix (e.g. {missing[:3]})")
    counts = counts[list(meta["well_id"])]
    return counts, meta, vehicle_identity


def per_well_metrics(counts: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    total_umi = counts.sum(axis=0)
    n_genes = (counts > 0).sum(axis=0)
    qc = meta.set_index("well_id").copy()
    qc["total_umi"] = total_umi.reindex(qc.index).astype(int)
    qc["n_genes_detected"] = n_genes.reindex(qc.index).astype(int)
    return qc.reset_index()


def flag_low_quality(qc: pd.DataFrame, min_umi_frac: float = 0.25,
                     min_genes_frac: float = 0.25) -> pd.DataFrame:
    """Data-driven: flag wells far below the plate medians in depth or complexity."""
    qc = qc.copy()
    med_umi = float(np.median(qc["total_umi"]))
    med_genes = float(np.median(qc["n_genes_detected"]))
    umi_floor = min_umi_frac * med_umi
    genes_floor = min_genes_frac * med_genes
    qc["umi_floor"] = umi_floor
    qc["genes_floor"] = genes_floor
    qc["low_umi"] = qc["total_umi"] < umi_floor
    qc["low_complexity"] = qc["n_genes_detected"] < genes_floor
    qc["pass_qc"] = ~(qc["low_umi"] | qc["low_complexity"])
    return qc


def _normalize_logcpm(counts: pd.DataFrame) -> pd.DataFrame:
    cpm = counts / counts.sum(axis=0) * 1e6
    return np.log1p(cpm)


def positional_diagnostics(counts: pd.DataFrame, meta: pd.DataFrame,
                           pass_wells: list[str], n_top: int = 500) -> dict:
    """Fraction of top-variable-gene PCA variance associated with plate column position.

    A high value means a positional/edge gradient is present and could confound contrasts unless
    modelled. Computed on QC-passing wells only. Uses a one-way R^2 of each PC on column.
    """
    sub = counts[pass_wells]
    logcpm = _normalize_logcpm(sub)
    var = logcpm.var(axis=1).sort_values(ascending=False)
    top = logcpm.loc[var.index[:min(n_top, len(var))]]
    X = top.T.values
    X = X - X.mean(axis=0, keepdims=True)
    # SVD for the first few PCs
    U, S, _ = np.linalg.svd(X, full_matrices=False)
    k = min(3, S.shape[0])
    scores = U[:, :k] * S[:k]
    ev = (S[:k] ** 2) / np.sum(S ** 2)
    cols = meta.set_index("well_id").loc[pass_wells, "col"].values.astype(float)
    # R^2 of each PC ~ column (one-way ANOVA on the discrete column factor)
    col_r2 = []
    for i in range(k):
        y = scores[:, i]
        grand = y.mean()
        ss_tot = np.sum((y - grand) ** 2) + 1e-12
        ss_between = 0.0
        for c in np.unique(cols):
            yc = y[cols == c]
            ss_between += len(yc) * (yc.mean() - grand) ** 2
        col_r2.append(ss_between / ss_tot)
    # Variance-weighted column association across the PCs.
    weighted = float(np.sum(np.array(col_r2) * ev) / np.sum(ev))
    return {
        "pc_variance_explained": [float(x) for x in ev],
        "pc_col_r2": [float(x) for x in col_r2],
        "positional_col_association": weighted,
        "positional_confounded": bool(weighted > 0.30),
    }


def check_identifiability(meta: pd.DataFrame) -> dict:
    """A design is non-identifiable if any treatment condition is perfectly confounded with a plate.

    Then batch (plate) and effect cannot be separated — a design-stage stop condition.
    """
    treated = scientific_logic.treatment_rows(meta)
    treated["condition"] = treated["compound"].astype(str) + "_" + treated["dose_uM"].astype(str)
    n_plates = meta["plate_id"].nunique()
    confounded = []
    if n_plates > 1:
        for cond, grp in treated.groupby("condition"):
            if grp["plate_id"].nunique() == 1 and (meta["plate_id"] == grp["plate_id"].iloc[0]).sum() == len(grp):
                confounded.append(cond)
    identifiable = len(confounded) == 0
    return {
        "n_plates": int(n_plates),
        "identifiable": bool(identifiable),
        "confounded_conditions": confounded,
        "reason": ("ok" if identifiable else
                   "one or more conditions occupy an entire plate; plate effect is not separable"),
    }


def run(counts_path, platemap_path, outdir, min_umi_frac=0.25, min_genes_frac=0.25,
        assignment_rate: float = 1.0) -> dict:
    outdir = pathlib.Path(outdir)
    (outdir / "tables").mkdir(parents=True, exist_ok=True)
    counts, meta, vehicle_identity = load_inputs(counts_path, platemap_path)
    qc = per_well_metrics(counts, meta)
    qc = flag_low_quality(qc, min_umi_frac, min_genes_frac)
    qc.to_csv(outdir / "tables" / "well_qc.csv", index=False)
    pass_wells = list(qc.loc[qc["pass_qc"], "well_id"])
    pos = positional_diagnostics(counts, meta, pass_wells)
    ident = check_identifiability(meta)
    # Barcode assignment stop-gate (meaningful for the FASTQ front end; counts mode passes 1.0).
    assignment_stop = bool(assignment_rate < MIN_ASSIGNMENT_RATE)
    summary = {
        "n_wells_total": int(len(qc)),
        "n_wells_pass_qc": int(qc["pass_qc"].sum()),
        "n_wells_dropped_qc": int((~qc["pass_qc"]).sum()),
        "dropped_wells": list(qc.loc[~qc["pass_qc"], "well_id"]),
        "barcode_assignment_rate": float(assignment_rate),
        "assignment_stop": assignment_stop,
        "positional": pos,
        "identifiability": ident,
        "pass_wells": pass_wells,
        "vehicle_identity": vehicle_identity,
    }
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-well QC and plate/positional diagnostics.")
    ap.add_argument("--counts", required=True)
    ap.add_argument("--platemap", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--min-umi-frac", type=float, default=0.25)
    ap.add_argument("--min-genes-frac", type=float, default=0.25)
    ap.add_argument("--assignment-rate", type=float, default=1.0)
    args = ap.parse_args()
    summary = run(args.counts, args.platemap, args.outdir, args.min_umi_frac,
                  args.min_genes_frac, args.assignment_rate)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
