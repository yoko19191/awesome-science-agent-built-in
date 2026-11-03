#!/usr/bin/env python3
"""
STAGE 09 (JOINT route) — Python bridge: assemble the integrated .h5mu, compute
scIB-style integration metrics on the joint representation, and run the OPTIONAL
MultiVI sensitivity route (only when its assumptions are met).

Consumes stage-08 exports in <outdir>/joint_export/. Writes:
  - integrated.h5mu                (MuData; written to /workspace then copied)
  - joint_qc_scorecard.csv / .json (per-modality QC + modality weights + metrics)
  - integration_metrics.csv
  - multivi_status.json            (ran | skipped, with an explicit reason)

Design: WNN is the headline joint embedding (stage 08). MultiVI is a probabilistic
alternative that is *skipped with a recorded reason* when cells are too few or
scvi-tools is unavailable — a documented skip, never a fabricated latent. Integration
metrics are computed with well-defined, deterministic kNN measures we control, and
additionally via scib-metrics when that package imports cleanly.
"""
import argparse, json, os, shutil, sys
import numpy as np
import pandas as pd
import scipy.io, scipy.sparse as sp

from integration_metrics import (
    compute_wnn_metrics,
    load_wnn_coordinates,
    load_wnn_neighbors,
    neighbors_from_coordinates,
    sha256_barcode_order,
    sha256_file,
)

def log(*a): print("[09_bridge]", *a, flush=True)

def should_run_multivi(enable, n_cells, min_cells):
    """Pure assumptions gate: returns (run: bool, status: str, reason: str).
    MultiVI is a sensitivity route; on too-few cells it is a documented SKIP,
    never a fabricated latent. WNN remains the headline joint embedding."""
    if enable is False:
        return (False, "disabled", "multivi.enable=false")
    if enable is True:
        return (True, "ran", "forced by multivi.enable=true")
    if n_cells < min_cells:
        return (False, "skipped",
                f"n_cells {n_cells} < multivi.min_cells {min_cells}; WNN remains the joint "
                f"embedding. MultiVI latent NOT produced (documented skip, not fabricated).")
    return (True, "ran", f"n_cells {n_cells} >= multivi.min_cells {min_cells}")

def load_yaml(p):
    import yaml
    with open(p) as fh: return yaml.safe_load(fh)

def scib_metrics_optional(emb, labels, batch):
    """Best-effort scib-metrics; records not_computable rather than fabricating."""
    res = {}
    try:
        import scib_metrics as sm
    except Exception as e:
        res["scib_metrics"] = "not_available"; res["scib_metrics_reason"] = str(e); return res
    try:
        res["clisi_knn"] = float(np.asarray(sm.clisi_knn(emb, np.asarray(labels))).mean())
    except Exception as e:
        res["clisi_knn_reason"] = str(e)
    if batch is not None and len(np.unique(batch)) > 1:
        try:
            res["ilisi_knn"] = float(np.asarray(sm.ilisi_knn(emb, np.asarray(batch))).mean())
        except Exception as e:
            res["ilisi_knn_reason"] = str(e)
    return res

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_yaml(args.config)
    seed = int(cfg.get("project", {}).get("seed", 1234)); np.random.seed(seed)
    outdir = cfg["project"]["outdir"]; results_dir = cfg["project"]["results_dir"]
    ex = os.path.join(outdir, "joint_export")
    tab = os.path.join(outdir, "tables"); os.makedirs(tab, exist_ok=True)
    os.makedirs(results_dir, exist_ok=True)
    im = cfg.get("integration_metrics", {}) or {}
    batch_key = im.get("batch_key", "replicate"); label_key = im.get("label_key", "cell_type")

    # ---- load exports ----
    obs = pd.read_csv(os.path.join(ex, "obs.csv"))
    barcodes = [l.strip() for l in open(os.path.join(ex, "barcodes.txt"))]
    genes = [l.strip() for l in open(os.path.join(ex, "rna_genes.txt"))]
    peaks = [l.strip() for l in open(os.path.join(ex, "atac_peaks.txt"))]
    rna = scipy.io.mmread(os.path.join(ex, "rna_counts.mtx")).tocsr().T.tocsr()   # cells x genes
    atac = scipy.io.mmread(os.path.join(ex, "atac_counts.mtx")).tocsr().T.tocsr() # cells x peaks
    coordinate_path = os.path.join(ex, "joint_embedding.csv")
    neighbor_path = os.path.join(ex, "wnn_neighbors.csv")
    Xj = load_wnn_coordinates(coordinate_path, barcodes)
    wnn_neighbors = load_wnn_neighbors(neighbor_path, barcodes)
    umap = pd.read_csv(os.path.join(ex, "wnn_umap.csv")).set_index("barcode").loc[barcodes]
    obs = obs.set_index("barcode").loc[barcodes]
    log(f"loaded {rna.shape[0]} cells x {rna.shape[1]} genes / {atac.shape[1]} peaks")

    independent_labels = obs["reference_label"].values if "reference_label" in obs else None
    annotation_labels = obs["annotation_label"].astype(str).values if "annotation_label" in obs else None
    batch = obs[batch_key].astype(str).values if batch_key in obs else None

    # ---- integration metrics on actual WNN outputs ----
    metrics = {"coordinate_space": "wnn_umap_2d", "neighbor_space": "weighted.nn_graph",
               "coordinate_source": "joint_export/joint_embedding.csv",
               "coordinate_sha256": sha256_file(coordinate_path),
               "coordinate_shape": list(Xj.shape),
               "neighbor_source": "joint_export/wnn_neighbors.csv",
               "neighbor_sha256": sha256_file(neighbor_path),
               "neighbor_shape": list(wnn_neighbors.shape),
               "barcode_order_sha256": sha256_barcode_order(barcodes),
               "n_cells": int(len(obs)),
               "batch_key": batch_key, "label_key_requested": label_key,
               "label_source": (f"independent metadata column '{label_key}' exported as reference_label"
                                if independent_labels is not None else
                                "not_evaluable: no independent label; marker annotation is WNN-cluster-derived"),
               "n_batches": (int(len(np.unique(batch))) if batch is not None else 0)}
    metrics.update(compute_wnn_metrics(Xj, wnn_neighbors, independent_labels, batch))
    if metrics["label_purity_status"] == "computed":
        metrics.update(scib_metrics_optional(Xj, independent_labels, batch))
    else:
        metrics.update(scib_metrics="not_evaluable_independent_labels",
                       scib_metrics_reason="no independent label vector")

    # ---- assemble MuData .h5mu (write to /workspace, then copy to results) ----
    h5mu_ok, h5mu_reason = True, ""
    try:
        import anndata as ad, mudata as md
        a_rna = ad.AnnData(X=rna, obs=obs.copy(), var=pd.DataFrame(index=genes))
        a_atac = ad.AnnData(X=atac, obs=obs.copy(), var=pd.DataFrame(index=peaks))
        for a in (a_rna, a_atac):
            a.obsm["X_wnn_umap"] = umap.values.astype(np.float32)
        mdata = md.MuData({"rna": a_rna, "atac": a_atac})
        mdata.obs = obs.copy()
        tmp = os.path.join(outdir, "integrated.h5mu"); mdata.write(tmp)
        shutil.copy(tmp, os.path.join(results_dir, "integrated.h5mu"))
        log("wrote integrated.h5mu")
    except Exception as e:
        h5mu_ok, h5mu_reason = False, str(e); log("h5mu FAILED:", e)

    # ---- MultiVI sensitivity route (assumptions-gated) ----
    mv = cfg.get("multivi", {}) or {}
    enable = mv.get("enable", "auto"); min_cells = int(mv.get("min_cells", 1000))
    run_mv, mv_stat, mv_reason = should_run_multivi(enable, len(obs), min_cells)
    mv_status = {"requested": enable, "min_cells": min_cells, "n_cells": int(len(obs)),
                 "status": mv_stat, "reason": mv_reason}
    if run_mv:
        try:
            import scvi, anndata as ad
            scvi.settings.seed = seed
            comb = ad.AnnData(X=sp.hstack([rna, atac]).tocsr(), obs=obs.copy())
            comb.var["modality"] = (["Gene Expression"] * rna.shape[1]) + (["Peaks"] * atac.shape[1])
            comb.var_names = list(genes) + list(peaks)
            scvi.model.MULTIVI.setup_anndata(comb, batch_key=batch_key if batch is not None else None)
            model = scvi.model.MULTIVI(comb, n_genes=rna.shape[1], n_regions=atac.shape[1])
            model.train(max_epochs=int(mv.get("max_epochs", 50)))
            lat = model.get_latent_representation()
            np.save(os.path.join(outdir, "multivi_latent.npy"), lat)
            mv_metrics = {"embedding": "multivi_latent"}
            mv_latent = lat.astype(np.float32)
            mv_neighbors = neighbors_from_coordinates(mv_latent)
            mv_metrics.update(compute_wnn_metrics(
                mv_latent, mv_neighbors, independent_labels, batch
            ))
            mv_status.update(status="ran", n_latent=int(lat.shape[1]), metrics=mv_metrics)
            log("MultiVI ran; latent dim", lat.shape[1])
        except Exception as e:
            mv_status.update(status="skipped", reason=f"MultiVI unavailable/failed: {e}. WNN remains the joint embedding.")
            log("MultiVI skipped:", e)
    json.dump(mv_status, open(os.path.join(tab, "multivi_status.json"), "w"), indent=2)

    # ---- joint QC scorecard ----
    wcols = [c for c in obs.columns if c.endswith(".weight")]
    scorecard = {
        "n_joint_cells": int(len(obs)),
        "n_genes": int(rna.shape[1]), "n_peaks": int(atac.shape[1]),
        "n_clusters": int(obs["wnn_clusters"].nunique()) if "wnn_clusters" in obs else None,
        "n_cell_types": (int(pd.Series(annotation_labels).nunique())
                         if annotation_labels is not None else None),
        "modality_weight_means": {c.replace(".weight", ""): round(float(obs[c].mean()), 4) for c in wcols},
        "integration_metrics": metrics,
        "multivi": {k: mv_status[k] for k in ("status", "reason") if k in mv_status},
        "h5mu_written": h5mu_ok, "h5mu_reason": h5mu_reason,
    }
    json.dump(scorecard, open(os.path.join(tab, "joint_qc_scorecard.json"), "w"), indent=2)
    # flat CSV of metrics
    flat = {k: v for k, v in metrics.items() if not isinstance(v, dict)}
    pd.DataFrame([flat]).to_csv(os.path.join(tab, "integration_metrics.csv"), index=False)
    pd.DataFrame([{
        "n_joint_cells": scorecard["n_joint_cells"], "n_genes": scorecard["n_genes"],
        "n_peaks": scorecard["n_peaks"], "n_clusters": scorecard["n_clusters"],
        "n_cell_types": scorecard["n_cell_types"], "multivi_status": mv_status.get("status"),
        **{f"weight_{k}": v for k, v in scorecard["modality_weight_means"].items()},
        **{k: v for k, v in flat.items() if isinstance(v, (int, float))},
    }]).to_csv(os.path.join(tab, "joint_qc_scorecard.csv"), index=False)
    log("STAGE09_OK")

if __name__ == "__main__":
    main()
