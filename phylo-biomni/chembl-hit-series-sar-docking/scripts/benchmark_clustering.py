#!/usr/bin/env python3
"""Benchmark the Butina distance computation: OLD (nested Python loop + list of floats)
vs NEW (BulkTanimotoSimilarity + array('d')), at EGFR scale.

Reports wall-clock and peak RSS for each path, confirms the distances are bit-identical,
and confirms Butina returns the same number of clusters. Designed to run on the DEFAULT
1-CPU worker so the reported numbers are the 1-CPU verdict.

Usage:
    python benchmark_clustering.py --input <hits_clean.csv>                # both + verify
    python benchmark_clustering.py --input <hits_clean.csv> --method old   # one path (subprocess use)
"""
import argparse, os, sys, time, resource, hashlib, subprocess, json
import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, DataStructs
from rdkit.ML.Cluster import Butina
from rdkit import RDLogger

RDLogger.DisableLog("rdApp.*")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score_and_cluster import bulk_condensed_distances, bulk_distance_matrix  # production paths

CUTOFF = 0.40


def peak_rss_mb():
    # ru_maxrss is KB on Linux.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def build_fps(smiles):
    mols = [Chem.MolFromSmiles(s) for s in smiles]
    return [AllChem.GetMorganFingerprintAsBitVect(m, 2, nBits=2048) for m in mols]


def old_condensed_distances(fps):
    """The path being replaced: nested Python loop appending to a list of floats."""
    n = len(fps)
    dists = []
    for i in range(n):
        for j in range(i):
            dists.append(1 - DataStructs.TanimotoSimilarity(fps[i], fps[j]))
    return dists


def load_smiles(path):
    import pandas as pd
    return pd.read_csv(path)["smiles"].tolist()


def run_one(path, method):
    """Full-scale single-method run; prints a JSON line with timing/memory/hash/clusters.

    OLD reproduces the original path exactly: a Python list of floats passed straight to
    Butina. NEW is the production path: a dense distance matrix (bulk_distance_matrix) passed
    to Butina's matrix code path. Peak RSS is captured BEFORE the identity hash so the reported
    memory reflects the clustering path only, not the extra hashing step.
    """
    smiles = load_smiles(path)
    fps = build_fps(smiles)
    n = len(fps)
    t0 = time.time()
    if method == "old":
        data = old_condensed_distances(fps)            # Python list (original path)
        struct_mb = n * (n - 1) // 2 * 32 / 1e6        # list ptr(8B) + float obj(~24B) per pair
    else:
        data = bulk_distance_matrix(fps)               # dense n x n matrix (production path)
        struct_mb = data.nbytes / 1e6
    t_dist = time.time() - t0
    t1 = time.time()
    clusters = Butina.ClusterData(data, n, CUTOFF, isDistData=True)
    t_butina = time.time() - t1
    peak = peak_rss_mb()                                # capture before hashing
    # Full-scale byte-identity hash of the condensed lower triangle (post-measurement).
    if method == "old":
        raw = np.asarray(data, dtype=np.float64).tobytes()
    else:
        raw = data[np.tril_indices(n, -1)].tobytes()
    digest = hashlib.sha256(raw).hexdigest()
    out = {
        "method": method, "n": n, "n_pairs": n * (n - 1) // 2,
        "t_dist_s": round(t_dist, 2), "t_butina_s": round(t_butina, 2),
        "dist_struct_mb": round(struct_mb, 1), "peak_rss_mb": round(peak, 1),
        "n_clusters": len(clusters), "dist_sha256": digest,
    }
    print("BENCH_JSON " + json.dumps(out))
    return out


def verify_bit_identity(path, limit=2000):
    """Reproduce the reported bit-identity check on the first `limit` molecules."""
    smiles = load_smiles(path)[:limit]
    fps = build_fps(smiles)
    old = np.asarray(old_condensed_distances(fps), dtype=np.float64)
    new = bulk_condensed_distances(fps)
    max_abs = float(np.max(np.abs(old - new))) if len(old) else 0.0
    print(f"[verify] {len(fps)} molecules, {len(old):,} pairs -> "
          f"max abs difference OLD vs NEW = {max_abs:.1e}")
    return max_abs, len(old)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--method", default="both", choices=["both", "old", "new", "verify"])
    ap.add_argument("--verify-limit", type=int, default=2000)
    args = ap.parse_args()

    if args.method in ("old", "new"):
        run_one(args.input, args.method)
        return
    if args.method == "verify":
        verify_bit_identity(args.input, args.verify_limit)
        return

    # both: verify inline, then run each full-scale path in its own subprocess for clean peak RSS
    print("=" * 72)
    print("CLUSTERING BENCHMARK (run on this machine's CPU; single-threaded distance path)")
    print("=" * 72)
    max_abs, npairs = verify_bit_identity(args.input, args.verify_limit)

    results = {}
    for method in ("old", "new"):
        print(f"\n--- full-scale {method.upper()} path (isolated subprocess) ---", flush=True)
        proc = subprocess.run([sys.executable, os.path.abspath(__file__),
                               "--input", args.input, "--method", method],
                              capture_output=True, text=True)
        sys.stdout.write(proc.stdout)
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr)
            print(f"{method} run FAILED"); return
        for line in proc.stdout.splitlines():
            if line.startswith("BENCH_JSON "):
                results[method] = json.loads(line[len("BENCH_JSON "):])

    o, nw = results["old"], results["new"]
    speedup = o["t_dist_s"] / nw["t_dist_s"] if nw["t_dist_s"] else float("nan")
    mem_ratio = o["peak_rss_mb"] / nw["peak_rss_mb"] if nw["peak_rss_mb"] else float("nan")
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("-" * 72)
    print(f"molecules={nw['n']:,}  pairs={nw['n_pairs']:,}")
    print(f"bit-identity check: max abs diff = {max_abs:.1e} over {npairs:,} pairs "
          f"({'IDENTICAL' if max_abs == 0.0 else 'DIFFERS'})")
    print(f"clusters: OLD={o['n_clusters']}  NEW={nw['n_clusters']}  "
          f"({'MATCH' if o['n_clusters']==nw['n_clusters'] else 'MISMATCH'})")
    print(f"distance sha256 match: {o['dist_sha256']==nw['dist_sha256']}")
    print(f"{'':<26}{'OLD':>14}{'NEW':>14}")
    print(f"{'distance wall-clock (s)':<26}{o['t_dist_s']:>14}{nw['t_dist_s']:>14}")
    print(f"{'Butina wall-clock (s)':<26}{o['t_butina_s']:>14}{nw['t_butina_s']:>14}")
    print(f"{'distance struct (MB)':<26}{o['dist_struct_mb']:>14}{nw['dist_struct_mb']:>14}")
    print(f"{'process peak RSS (MB)':<26}{o['peak_rss_mb']:>14}{nw['peak_rss_mb']:>14}")
    print("-" * 72)
    print(f"distance speedup: {speedup:.1f}x   peak-RSS reduction: {mem_ratio:.1f}x")
    print("=" * 72)


if __name__ == "__main__":
    main()
