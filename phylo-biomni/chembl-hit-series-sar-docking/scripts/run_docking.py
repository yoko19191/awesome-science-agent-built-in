#!/usr/bin/env python3
"""Run AutoDock Vina docking for all prepared ligands.

Vina 1.2.5: no --log flag, parse stdout for scores.

Usage:
    python run_docking.py --receptor <pdbqt> --ligand-dir <dir> \
        --box <json> --selection <json> --outdir <dir> --output <csv> \
        --exhaustiveness 16 --seed 42
"""
import argparse, os, sys, json, subprocess, time, re
import pandas as pd
from scipy.stats import pearsonr


def parse_vina_stdout(stdout):
    """Parse Vina scores from stdout (no --log flag in 1.2.5)."""
    scores = []
    in_table = False
    for line in stdout.split("\n"):
        if "mode |" in line and "affinity" in line:
            in_table = True
            continue
        if in_table:
            if "-----+" in line:
                continue
            if line.strip() == "" or "Writing" in line:
                in_table = False
                continue
            parts = line.split()
            if len(parts) >= 4:
                try:
                    mode_num = int(parts[0])
                    affinity = float(parts[1])
                    rmsd_lb = float(parts[2])
                    rmsd_ub = float(parts[3])
                    scores.append({
                        "mode": mode_num, "affinity": affinity,
                        "rmsd_lb": rmsd_lb, "rmsd_ub": rmsd_ub,
                    })
                except (ValueError, IndexError):
                    pass
    return scores


def main():
    parser = argparse.ArgumentParser(description="Run AutoDock Vina docking")
    parser.add_argument("--receptor", required=True, help="Receptor PDBQT path")
    parser.add_argument("--ligand-dir", required=True, help="Directory with ligand PDBQT files")
    parser.add_argument("--box", required=True, help="Docking box JSON")
    parser.add_argument("--selection", required=True, help="Ligand selection JSON")
    parser.add_argument("--outdir", required=True, help="Output directory for docked poses")
    parser.add_argument("--output", required=True, help="Output CSV path")
    parser.add_argument("--exhaustiveness", type=int, default=16)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=int, default=300, help="Per-ligand timeout (seconds)")
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    with open(args.box) as f:
        box = json.load(f)
    with open(args.selection) as f:
        selection = json.load(f)
    sel_map = {s["chembl_id"]: s for s in selection}

    ligand_files = sorted([f for f in os.listdir(args.ligand_dir) if f.endswith(".pdbqt")])
    print(f"Docking {len(ligand_files)} ligands")

    results = []
    t0 = time.time()

    for i, lf in enumerate(ligand_files):
        cid = lf.replace(".pdbqt", "")
        lig_path = os.path.join(args.ligand_dir, lf)
        out_path = os.path.join(args.outdir, f"{cid}_docked.pdbqt")

        cmd = [
            "vina",
            "--receptor", args.receptor,
            "--ligand", lig_path,
            "--center_x", str(box["center_x"]),
            "--center_y", str(box["center_y"]),
            "--center_z", str(box["center_z"]),
            "--size_x", str(box["size_x"]),
            "--size_y", str(box["size_y"]),
            "--size_z", str(box["size_z"]),
            "--out", out_path,
            "--exhaustiveness", str(args.exhaustiveness),
            "--num_modes", "10",
            "--energy_range", "3",
            "--seed", str(args.seed),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
            scores = parse_vina_stdout(result.stdout)
            best_affinity = scores[0]["affinity"] if scores else None
            sel = sel_map.get(cid, {})

            results.append({
                "chembl_id": cid,
                "smiles": sel.get("smiles", ""),
                "series": sel.get("series", ""),
                "variant": sel.get("variant", "WT/unspecified"),
                "pchembl": sel.get("pchembl", None),
                "hit_score": sel.get("hit_score", None),
                "docking_score": best_affinity,
                "all_scores": [s["affinity"] for s in scores],
                "n_modes": len(scores),
                "status": "success" if scores else "failed",
            })

            elapsed = time.time() - t0
            print(f"  [{i+1}/{len(ligand_files)}] {cid}: score={best_affinity} "
                  f"({elapsed:.0f}s)", flush=True)

        except subprocess.TimeoutExpired:
            results.append({
                "chembl_id": cid, "smiles": sel.get("smiles", ""), "series": sel.get("series", ""),
                "variant": sel.get("variant", "WT/unspecified"),
                "pchembl": sel.get("pchembl", None), "hit_score": sel.get("hit_score", None),
                "docking_score": None, "all_scores": [], "n_modes": 0, "status": "timeout",
            })
            print(f"  [{i+1}/{len(ligand_files)}] {cid}: TIMEOUT")

    df = pd.DataFrame(results)
    df.to_csv(args.output, index=False)

    print(f"\nDocking complete. {len(df)} results.")
    print(f"Successful: {(df['status']=='success').sum()}")
    print(f"Failed: {(df['status']!='success').sum()}")

    # Compute and PRINT docking-potency correlation (never hallucinate this value)
    successful = df[df["status"] == "success"].copy()
    if len(successful) > 2 and successful["pchembl"].notna().sum() > 2:
        valid = successful[successful["docking_score"].notna() & successful["pchembl"].notna()]
        if len(valid) > 2:
            r, p = pearsonr(valid["docking_score"], valid["pchembl"])
            print(f"\nDocking-potency Pearson r = {r:.4f}, p = {p:.4f}")
            if p < 0.05:
                if abs(r) >= 0.5:
                    print(f"  Interpretation: strong correlation (r={r:.3f}, p={p:.3f})")
                elif abs(r) >= 0.3:
                    print(f"  Interpretation: moderate correlation (r={r:.3f}, p={p:.3f})")
                else:
                    print(f"  Interpretation: weak but significant correlation (r={r:.3f}, p={p:.3f})")
            else:
                print(f"  Interpretation: weak/not significant (r={r:.3f}, p={p:.3f})")
                print(f"  Caveat: weak correlation is expected for congeneric series with narrow potency ranges.")

    print(f"\nTop 10 by docking score:")
    successful = df[df["status"] == "success"].sort_values("docking_score")
    print(successful[["chembl_id", "series", "docking_score", "pchembl"]].head(10).to_string())
    print(f"\nTotal time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
