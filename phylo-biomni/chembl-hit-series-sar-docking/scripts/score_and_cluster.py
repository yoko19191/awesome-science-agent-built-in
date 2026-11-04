#!/usr/bin/env python3
"""Score hits by potency/efficacy and cluster into chemical series via Butina clustering.

Distance computation
---------------------
Tanimoto distances are built with ``DataStructs.BulkTanimotoSimilarity`` (one C++
call per row) written straight into a preallocated NumPy array, instead of a
Python-level double loop appending 54.4M boxed floats to a ``list`` (~1.7 GB).
Production hands Butina a dense symmetric matrix (``bulk_distance_matrix``) so it
uses its matrix code path and skips the internal 1-D -> dense expansion. On the
EGFR set (10.4k molecules, 54.4M pairs) this is ~15x faster on distances and cuts
whole-step peak RSS from ~5.5 GB to ~1.4 GB, with bit-identical distances and
identical clusters -- which is what makes the default 1-CPU worker viable. See
``scripts/benchmark_clustering.py`` for the measured before/after numbers.

Usage:
    python score_and_cluster.py --input <hits_clean.csv> --outdir <dir> \
        --butina-cutoff 0.40 --min-series-size 5
"""
import argparse, os, sys, time
import pandas as pd
import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, DataStructs
from rdkit.ML.Cluster import Butina
from rdkit import RDLogger

RDLogger.DisableLog("rdApp.*")


def bulk_condensed_distances(fps):
    """Lower-triangle Tanimoto *distance* vector (row-major, j<i) as a NumPy float64 array.

    Element order is identical to the reference double loop
    ``for i in range(n): for j in range(i): 1 - Tanimoto(fp[i], fp[j])`` and is
    exactly the layout ``Butina.ClusterData(..., isDistData=True)`` expects (which
    accepts a 1-D ``numpy.ndarray``).

    Uses one ``BulkTanimotoSimilarity`` call per row (fps[i] vs fps[:i]) written into
    a single preallocated array — no 54.4M-element Python list of boxed floats
    (~1.7 GB) and no per-append reallocation.
    """
    n = len(fps)
    num_pairs = n * (n - 1) // 2
    dists = np.empty(num_pairs, dtype=np.float64)
    pos = 0
    for i in range(1, n):
        sims = DataStructs.BulkTanimotoSimilarity(fps[i], fps[:i])
        dists[pos:pos + i] = np.subtract(1.0, np.asarray(sims, dtype=np.float64))
        pos += i
    return dists


def bulk_distance_matrix(fps):
    """Full symmetric Tanimoto *distance* matrix (n x n float64) via BulkTanimotoSimilarity.

    Passing a dense matrix to ``Butina.ClusterData(..., isDistData=True)`` uses its matrix
    code path directly and avoids the internal 1-D -> dense expansion (a ``zeros((n, n))``
    allocation plus ``tril_indices`` plus a transpose temporary). On the EGFR set this cuts
    whole-step peak RSS from ~5.5 GB (old list path) to ~1.4 GB while returning identical
    clusters. Off-diagonal values are bit-identical to ``bulk_condensed_distances``; the
    diagonal is 0.
    """
    n = len(fps)
    dmat = np.zeros((n, n), dtype=np.float64)
    for i in range(1, n):
        d = np.subtract(1.0, np.asarray(DataStructs.BulkTanimotoSimilarity(fps[i], fps[:i]),
                                        dtype=np.float64))
        dmat[i, :i] = d
        dmat[:i, i] = d
    return dmat


def main():
    parser = argparse.ArgumentParser(description="Score and cluster hits")
    parser.add_argument("--input", required=True, help="Path to hits_clean.csv")
    parser.add_argument("--outdir", required=True, help="Output directory")
    parser.add_argument("--butina-cutoff", type=float, default=0.40,
                        help="Butina distance cutoff (1 - Tanimoto similarity)")
    parser.add_argument("--min-series-size", type=int, default=5,
                        help="Minimum cluster size to label as a named series")
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    hits = pd.read_csv(args.input)
    print(f"Loaded {len(hits)} hits")

    # ---- SCORING ----
    pmin, pmax = 5.0, 11.0
    hits["potency_score"] = np.clip((hits["best_pchembl"] - pmin) / (pmax - pmin), 0, 1)

    le_norm = np.clip((hits["ligand_efficiency"] - 0.2) / (0.6 - 0.2), 0, 1)
    lipe_norm = np.clip((hits["lipe"] - 0) / (8 - 0), 0, 1)
    hits["efficacy_score"] = 0.5 * le_norm + 0.5 * lipe_norm

    hits["druglike_score"] = hits["ro5_pass"] * 0.5 + np.clip(
        1 - (hits["mol_wt"] - 300) / (500 - 300), 0, 1
    ) * 0.5

    hits["hit_score"] = (0.50 * hits["potency_score"] +
                         0.30 * hits["efficacy_score"] +
                         0.20 * hits["druglike_score"])

    def tier(s):
        if s >= 0.70: return "Tier 1 (Expand)"
        elif s >= 0.50: return "Tier 2 (Explore)"
        elif s >= 0.30: return "Tier 3 (Monitor)"
        else: return "Tier 4 (Drop)"
    hits["tier"] = hits["hit_score"].apply(tier)

    print(f"\nTier distribution:\n{hits['tier'].value_counts()}")

    # ---- CLUSTERING ----
    mols = [Chem.MolFromSmiles(s) for s in hits["smiles"]]
    fps = [AllChem.GetMorganFingerprintAsBitVect(m, 2, nBits=2048) for m in mols]

    n = len(fps)
    n_pairs = n * (n - 1) // 2
    print(f"\nComputing Tanimoto distances for {n} molecules "
          f"({n_pairs:,} pairs) via BulkTanimotoSimilarity...")
    t0 = time.time()
    dmat = bulk_distance_matrix(fps)
    dt = time.time() - t0
    print(f"  distance matrix: {n}x{n} float64, {dmat.nbytes/1e6:.0f} MB, {dt:.1f}s "
          f"({(n_pairs/dt/1e6):.2f}M pairs/s)")

    clusters = Butina.ClusterData(dmat, n, args.butina_cutoff, isDistData=True)
    print(f"Butina clusters (cutoff={args.butina_cutoff}, sim>={1-args.butina_cutoff:.2f}): {len(clusters)}")

    cluster_id = np.full(n, -1, dtype=int)
    for cid, members in enumerate(clusters):
        for m in members:
            cluster_id[m] = cid
    hits["cluster_id"] = cluster_id

    # Label series with >= min_series_size members
    cluster_sizes = hits[hits["cluster_id"] >= 0].groupby("cluster_id").size().sort_values(ascending=False)
    series_map = {}
    for rank, (cid, size) in enumerate(cluster_sizes.items()):
        if size >= args.min_series_size:
            letter = chr(ord("A") + rank) if rank < 26 else f"S{rank}"
            series_map[cid] = f"Series {letter}"
    hits["series"] = hits["cluster_id"].map(series_map).fillna("Singleton/Other")

    print(f"\nSeries with >={args.min_series_size} members: {len(series_map)}")
    print(f"Singletons/Other: {(hits['series']=='Singleton/Other').sum()}")

    # Per-series variant mix: makes it visible when a cluster merges chemotypes
    # developed against different assay variants (see SKILL.md caveat #1).
    def variant_mix(s):
        if "variant_best" not in s.columns:
            return pd.Series({"n_variants_in_series": 1, "dominant_variant": "WT/unspecified"})
        vc = s["variant_best"].value_counts()
        return pd.Series({
            "n_variants_in_series": int(vc.shape[0]),
            "dominant_variant": vc.index[0] if len(vc) else "WT/unspecified",
        })

    agg_kwargs = dict(
        size=("molecule_chembl_id", "count"),
        median_pchembl=("best_pchembl", "median"),
        max_pchembl=("best_pchembl", "max"),
        median_le=("ligand_efficiency", "median"),
        median_lipe=("lipe", "median"),
        median_mw=("mol_wt", "median"),
        median_logp=("logp", "median"),
        mean_hit_score=("hit_score", "mean"),
    )
    named = hits[hits["series"] != "Singleton/Other"]
    series_stats = named.groupby("series").agg(**agg_kwargs)
    vmix = named.groupby("series").apply(variant_mix, include_groups=False) \
        if "variant_best" in hits.columns else None
    if vmix is not None:
        series_stats = series_stats.join(vmix)
    series_stats = series_stats.sort_values("size", ascending=False)

    print(f"\nTop 15 series by size:")
    print(series_stats.head(15).to_string())

    # Save
    hits_path = os.path.join(args.outdir, "hits_scored_clustered.csv")
    hits.to_csv(hits_path, index=False)
    print(f"\nSaved scored+clustered hits to {hits_path}")

    stats_path = os.path.join(args.outdir, "series_stats.csv")
    series_stats.to_csv(stats_path)
    print(f"Saved series stats to {stats_path}")


if __name__ == "__main__":
    main()
