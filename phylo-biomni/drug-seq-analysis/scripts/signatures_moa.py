#!/usr/bin/env python3
"""Per-compound perturbational signatures, MOA clustering, and connectivity export.

Signatures are built from well-pseudobulk DE (top-dose log2FC per compound over the union of
significant genes), capped per compound so a potent compound cannot dominate the shared gene space.
Compounds are clustered by signature similarity (Pearson correlation, deterministic hierarchical
clustering). Connectivity/MoA against an external compendium (CMap/LINCS) is NOT computed here: it is
a documented continuation in `signature-reversal-lincs`, so this step exports a query signature and
stops cleanly when no reference is supplied. See references/connectivity_handoff.md.

CLI:
  python3 signatures_moa.py --de tables/de_all_contrasts.csv --outdir /mnt/results \
      [--cap 150] [--top-n 50]
writes signature_matrix.csv, moa_similarity.csv, moa_clusters.csv, query_signatures.json.
"""
from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform


def build_signatures(de: pd.DataFrame, cap: int = 150) -> pd.DataFrame:
    if "contrast_family" not in de.columns:
        raise ValueError("DE table is missing semantic contrast_family")
    de = de[
        de["compound"].notna() & (de["contrast_family"] == "treatment_vs_vehicle")
    ].copy()
    # Top dose per compound = the perturbational signature carrier.
    top_dose = de.groupby("compound")["dose_uM"].transform("max")
    tops = de[de["dose_uM"] == top_dose].copy()
    sig_genes = sorted(set(de.loc[de["is_sig"], "gene_id"]))
    if not sig_genes:
        sig_genes = sorted(set(de["gene_id"]))[:cap]
    mat = {}
    for comp, sub in tops.groupby("compound"):
        s = sub.set_index("gene_id")["log2FoldChange"].reindex(sig_genes).fillna(0.0)
        # Cap: keep the strongest `cap` genes for this compound, zero the rest.
        if cap and len(s) > cap:
            keep = s.abs().sort_values(ascending=False).index[:cap]
            s = s.where(s.index.isin(keep), 0.0)
        mat[comp] = s
    return pd.DataFrame(mat).loc[sig_genes]


def moa_similarity(sig: pd.DataFrame) -> pd.DataFrame:
    return sig.corr(method="pearson")


def moa_clusters(sim: pd.DataFrame) -> pd.DataFrame:
    comps = list(sim.columns)
    if len(comps) < 2:
        return pd.DataFrame({"compound": comps, "cluster": [1] * len(comps)})
    dist = 1.0 - sim.values
    np.fill_diagonal(dist, 0.0)
    dist = (dist + dist.T) / 2.0
    Z = linkage(squareform(dist, checks=False), method="average")
    # Deterministic cut: separate anything less than 0.5 correlated (distance > 0.5).
    labels = fcluster(Z, t=0.5, criterion="distance")
    return pd.DataFrame({"compound": comps, "cluster": [int(x) for x in labels]})


def query_signatures(de: pd.DataFrame, top_n: int = 50) -> dict:
    if "contrast_family" not in de.columns:
        raise ValueError("DE table is missing semantic contrast_family")
    de = de[
        de["compound"].notna() & (de["contrast_family"] == "treatment_vs_vehicle")
    ].copy()
    top_dose = de.groupby("compound")["dose_uM"].transform("max")
    tops = de[(de["dose_uM"] == top_dose) & (de["is_sig"])].copy()
    out = {}
    for comp, sub in tops.groupby("compound"):
        up = sub[sub["log2FoldChange"] > 0].sort_values("log2FoldChange", ascending=False)
        dn = sub[sub["log2FoldChange"] < 0].sort_values("log2FoldChange")
        out[comp] = {"up": list(up["gene_id"].head(top_n)),
                     "down": list(dn["gene_id"].head(top_n))}
    return out


def run(de_path, outdir, cap: int = 150, top_n: int = 50) -> dict:
    outdir = pathlib.Path(outdir)
    (outdir / "tables").mkdir(parents=True, exist_ok=True)
    de = pd.read_csv(de_path)
    sig = build_signatures(de, cap)
    sim = moa_similarity(sig)
    clusters = moa_clusters(sim)
    qsig = query_signatures(de, top_n)
    sig.to_csv(outdir / "tables" / "signature_matrix.csv")
    sim.to_csv(outdir / "tables" / "moa_similarity.csv")
    clusters.to_csv(outdir / "tables" / "moa_clusters.csv", index=False)
    connectivity = {
        "connectivity_status": "ready_for_signature_reversal_lincs",
        "detail": ("Query signatures exported for connectivity/MoA scoring. No external CMap/LINCS "
                   "reference was supplied to this run, so connectivity (tau) is NOT computed here; "
                   "continue with signature-reversal-lincs and report at least two metrics."),
        "signatures": qsig,
    }
    (outdir / "tables" / "query_signatures.json").write_text(json.dumps(connectivity, indent=2))
    return {
        "n_compounds": int(sig.shape[1]),
        "n_signature_genes": int(sig.shape[0]),
        "clusters": {row["compound"]: int(row["cluster"]) for _, row in clusters.iterrows()},
        "connectivity_status": "ready_for_signature_reversal_lincs",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Signatures, MOA clustering, connectivity export.")
    ap.add_argument("--de", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--cap", type=int, default=150)
    ap.add_argument("--top-n", type=int, default=50)
    args = ap.parse_args()
    print(json.dumps(run(args.de, args.outdir, args.cap, args.top_n), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
