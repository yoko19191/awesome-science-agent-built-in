#!/usr/bin/env python3
"""SAR analysis within each major chemical series.

For each series: MCS extraction (with CORRECT rdFMCS API), Murcko scaffold,
activity cliffs, property-potency correlations, R-group decomposition, and
series recommendation.

CRITICAL FIX: Uses `completeRingsOnly=True` (not `completeRings=True`) for
rdFMCS.FindMCS. The original cold run used the wrong parameter name, causing
silent failure of MCS and R-group decomposition in ALL series.

Usage:
    python sar_analysis.py --input <hits_scored_clustered.csv> --outdir <dir> \
        --top-n-series 20
"""
import argparse, json, os, sys
import pandas as pd
import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, DataStructs, rdFMCS
from rdkit.Chem.Scaffolds import MurckoScaffold
from rdkit.Chem import rdRGroupDecomposition
from rdkit import RDLogger

RDLogger.DisableLog("rdApp.*")


def main():
    parser = argparse.ArgumentParser(description="Run SAR analysis per series")
    parser.add_argument("--input", required=True, help="Path to hits_scored_clustered.csv")
    parser.add_argument("--outdir", required=True, help="Output directory")
    parser.add_argument("--top-n-series", type=int, default=20,
                        help="Number of top series to analyze")
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    hits = pd.read_csv(args.input)
    series_stats = pd.read_csv(os.path.join(args.outdir, "series_stats.csv"), index_col=0)
    top_series = series_stats.head(args.top_n_series).index.tolist()
    print(f"Running SAR for top {len(top_series)} series")

    sar_results = {}
    scaffold_smarts = {}
    mcs_success_count = 0
    rgroup_success_count = 0

    for series_name in top_series:
        sdf = hits[hits["series"] == series_name].copy()
        if len(sdf) < 10:
            print(f"  {series_name}: skipping (n={len(sdf)} < 10)")
            continue
        mols = [Chem.MolFromSmiles(s) for s in sdf["smiles"]]
        pchembls = sdf["best_pchembl"].values

        result = {
            "series": series_name,
            "n_members": len(sdf),
            "pchembl_range": [float(pchembls.min()), float(pchembls.max())],
            "pchembl_median": float(np.median(pchembls)),
            "pchembl_std": float(np.std(pchembls)),
        }

        # 1. MCS — CRITICAL FIX: completeRingsOnly, not completeRings
        try:
            mcs = rdFMCS.FindMCS(mols, timeout=10, threshold=0.6,
                                 atomCompare=rdFMCS.AtomCompare.CompareElements,
                                 bondCompare=rdFMCS.BondCompare.CompareOrder,
                                 ringMatchesRingOnly=True,
                                 completeRingsOnly=True)  # ← FIX
            result["mcs_smarts"] = mcs.smartsString
            result["mcs_num_atoms"] = mcs.numAtoms
            scaffold_smarts[series_name] = mcs.smartsString
            mcs_success_count += 1
        except Exception as e:
            result["mcs_smarts"] = None
            result["mcs_error"] = str(e)[:200]
            print(f"  {series_name}: MCS failed: {str(e)[:100]}")

        # 2. Murcko scaffold analysis (always runs, robust)
        scaffolds = []
        for m in mols:
            try:
                sc = MurckoScaffold.GetScaffoldForMol(m)
                scaffolds.append(Chem.MolToSmiles(sc))
            except Exception:
                scaffolds.append(None)
        sdf["murcko_scaffold"] = scaffolds
        scaffold_counts = pd.Series(scaffolds).value_counts()
        result["n_unique_scaffolds"] = int(scaffold_counts.shape[0])
        result["top_scaffold"] = scaffold_counts.index[0] if scaffold_counts.shape[0] > 0 else None
        result["top_scaffold_count"] = int(scaffold_counts.iloc[0]) if scaffold_counts.shape[0] > 0 else 0

        # 3. Activity cliffs: Tanimoto >= 0.85, delta pChEMBL >= 2.0
        fps = [AllChem.GetMorganFingerprintAsBitVect(m, 2, nBits=2048) for m in mols]
        cliffs = []
        n_cliffs = 0
        n_pairs_checked = 0
        for i in range(len(mols)):
            for j in range(i + 1, len(mols)):
                sim = DataStructs.TanimotoSimilarity(fps[i], fps[j])
                if sim >= 0.85:
                    n_pairs_checked += 1
                    dp = abs(pchembls[i] - pchembls[j])
                    if dp >= 2.0:
                        n_cliffs += 1
                        if len(cliffs) < 20:
                            cliffs.append({
                                "mol_i": sdf.iloc[i]["molecule_chembl_id"],
                                "mol_j": sdf.iloc[j]["molecule_chembl_id"],
                                "similarity": float(sim),
                                "pchembl_i": float(pchembls[i]),
                                "pchembl_j": float(pchembls[j]),
                                "delta_pchembl": float(dp),
                            })
        result["n_activity_cliffs"] = n_cliffs
        result["n_similar_pairs"] = n_pairs_checked
        result["cliff_examples"] = cliffs

        # 4. Property-potency correlations
        props = ["mol_wt", "logp", "tpsa", "hbd", "hba", "rot_bonds", "heavy_atoms",
                 "aromatic_rings", "ligand_efficiency", "lipe"]
        correlations = {}
        for p in props:
            if p in sdf.columns and sdf[p].notna().sum() > 5:
                corr = sdf[p].corr(sdf["best_pchembl"])
                correlations[p] = float(corr) if not np.isnan(corr) else None
        result["property_correlations"] = correlations

        # 5. R-group decomposition (requires MCS success)
        if result.get("mcs_smarts"):
            try:
                mcs_mol = Chem.MolFromSmarts(result["mcs_smarts"])
                if mcs_mol is not None:
                    # CRITICAL FIX: asRows=True is required as keyword arg
                    groups, _ = rdRGroupDecomposition.RGroupDecompose(
                        [mcs_mol], mols, asSmiles=True, asRows=True)
                    result["rgroup_success"] = True
                    result["n_rgroups"] = len(groups[0]) - 1 if groups else 0
                    if groups:
                        rgroup_cols = [k for k in groups[0].keys() if k.startswith("R")]
                        result["rgroup_labels"] = rgroup_cols
                    rgroup_success_count += 1
            except Exception as e:
                result["rgroup_success"] = False
                result["rgroup_error"] = str(e)[:200]
        else:
            result["rgroup_success"] = False
            result["rgroup_error"] = "MCS unavailable"

        # 6. Best and worst members
        best_idx = pchembls.argmax()
        worst_idx = pchembls.argmin()
        result["best_member"] = {
            "chembl_id": sdf.iloc[best_idx]["molecule_chembl_id"],
            "smiles": sdf.iloc[best_idx]["smiles"],
            "pchembl": float(pchembls[best_idx]),
        }
        result["worst_member"] = {
            "chembl_id": sdf.iloc[worst_idx]["molecule_chembl_id"],
            "smiles": sdf.iloc[worst_idx]["smiles"],
            "pchembl": float(pchembls[worst_idx]),
        }

        # 7. Series recommendation
        median_p = result["pchembl_median"]
        n = result["n_members"]
        n_cliffs_r = result["n_activity_cliffs"]
        le_median = float(np.median(sdf["ligand_efficiency"]))
        lipe_median = float(np.median(sdf["lipe"]))

        if median_p >= 8.0 and n >= 50 and le_median >= 0.3:
            recommendation = "EXPAND - High potency, good LE, sufficient SAR coverage"
        elif median_p >= 7.0 and n >= 30:
            recommendation = "EXPLORE - Moderate potency with SAR headroom"
        elif median_p >= 6.0 and n >= 20:
            recommendation = "MONITOR - Marginal potency, limited upside"
        elif median_p < 6.0:
            recommendation = "DROP - Low potency series"
        else:
            recommendation = "MONITOR - Insufficient data for expansion"

        if n_cliffs_r > 10:
            # Coarse orientation aid only -- deliberately NOT phrased as a cliff statistic.
            # Cliff counts swing by an order of magnitude with the similarity criterion
            # (0.13% Tanimoto-only vs 6.04% consensus on one dataset), so quantitative cliff
            # work belongs to the matched-molecular-pairs skill. This string is rendered
            # verbatim into the HTML and PDF, so the wording matters here, not just in the
            # report templates.
            recommendation += " (discontinuous SAR flagged - coarse indicator, see matched-molecular-pairs)"
        result["recommendation"] = recommendation
        result["le_median"] = le_median
        result["lipe_median"] = lipe_median

        sar_results[series_name] = result
        print(f"  {series_name}: n={n}, median_p={median_p:.2f}, "
              f"MCS={'Y' if result.get('mcs_smarts') else 'N'}, "
              f"RGD={'Y' if result.get('rgroup_success') else 'N'}, "
              f"cliffs={n_cliffs_r}, rec={recommendation[:40]}")

    # Save
    with open(os.path.join(args.outdir, "sar_results.json"), "w") as f:
        json.dump(sar_results, f, indent=2, default=str)
    with open(os.path.join(args.outdir, "scaffold_smarts.json"), "w") as f:
        json.dump(scaffold_smarts, f, indent=2)

    print(f"\nSAR analysis complete for {len(sar_results)} series")
    print(f"MCS success: {mcs_success_count}/{len(sar_results)}")
    print(f"R-group decomposition success: {rgroup_success_count}/{len(sar_results)}")
    if mcs_success_count == 0 and len(sar_results) > 0:
        print("WARNING: MCS failed for all series. Check rdFMCS API parameters.", file=sys.stderr)
    print(f"Results saved to {os.path.join(args.outdir, 'sar_results.json')}")


if __name__ == "__main__":
    main()
