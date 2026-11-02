#!/usr/bin/env python3
"""
06_integrate_metrics.py — assemble the integrated .h5mu and the joint QC
scorecard, and render figure 6.

Builds a MuData (rna + prot) carrying raw counts, RNA log-normalization, dsb/CLR
ADT normalization, WNN clusters/weights/UMAP, totalVI latent (when it ran), and
annotations. Computes integration metrics with scikit-learn (dependency-light,
deterministic): cluster silhouette, label purity (ARI/NMI of WNN clusters vs an
INDEPENDENT reference label — never the cluster-derived annotation — via the
independent-label evidence model in label_purity.py, which emits
`not_evaluable_independent_labels` when independence/alignment/coverage cannot be
established), a separate annotation-vs-reference concordance/confusion that
surfaces novel/spurious labels and split/merge, and a kNN batch-mixing entropy by
replicate. HDF5 is written to /workspace first, then copied to the results root
(object store has no random-access writes).

Usage: python 06_integrate_metrics.py --config config.yaml
"""
import argparse, gzip, json, os, shutil, subprocess, warnings
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, scipy.io as sio, scipy.sparse as sp

from adt_contract import (
    assert_h5mu_normalized_adt,
    canonical_adt_feature_names,
    load_validated_normalized_adt,
)
from mudata_compat import construct_mudata, write_readback_validate

ap = argparse.ArgumentParser(); ap.add_argument("--config", required=True)
CFG = __import__("yaml").safe_load(open(ap.parse_args().config))
OUT = CFG["project"]["outdir"]; RES = CFG["project"]["results_dir"]
IG = os.path.join(OUT, "ingest"); CK = os.path.join(OUT, "checkpoints")
TAB = os.path.join(OUT, "tables"); FIG = os.path.join(OUT, "figures")
for d in (TAB, FIG): os.makedirs(d, exist_ok=True)
INT = CFG.get("integration", {}) or {}
batch_key = INT.get("batch_key", "replicate"); label_key = INT.get("label_key", "cell_type")


def read_mtx(mtx, feats, bcs):
    m = sio.mmread(gzip.open(mtx)).tocsr()
    f = [x for x in gzip.open(feats, "rt").read().split("\n") if x]
    b = [x for x in gzip.open(bcs, "rt").read().split("\n") if x]
    return m, f, b


def knn_batch_entropy(emb, batches, k=30):
    """Mean normalized Shannon entropy of batch labels among k neighbors (1=well mixed)."""
    from sklearn.neighbors import NearestNeighbors
    b = pd.Categorical(batches); codes = b.codes; nb = len(b.categories)
    if nb < 2:
        return np.nan
    k = min(k, len(codes) - 1)
    nn = NearestNeighbors(n_neighbors=k + 1).fit(emb)
    idx = nn.kneighbors(emb, return_distance=False)[:, 1:]
    ent = []
    for row in idx:
        _, cnt = np.unique(codes[row], return_counts=True)
        p = cnt / cnt.sum()
        ent.append(-(p * np.log(p)).sum() / np.log(nb))
    return float(np.mean(ent))


def main():
    import anndata as ad, mudata as md
    from sklearn.metrics import (silhouette_score, adjusted_rand_score,
                                 normalized_mutual_info_score)
    wc = pd.read_csv(os.path.join(CK, "wnn_cells.csv")).set_index("barcode")
    keep = wc.index.astype(str).tolist()
    # Preserve the INDEPENDENT reference label (user metadata -> ingest -> wnn_cells.csv) under a
    # distinct immutable field BEFORE any annotation is joined. It is never overwritten or aliased;
    # v3's `wc["cell_type"] = ann["cell_type"]` overwrite made label purity circular.
    if label_key in wc.columns:
        wc["reference_label"] = wc[label_key].values
    # The marker-derived annotation is loaded into a SEPARATE field (never onto the reference).
    ann_p = os.path.join(CK, "cell_annotations.csv")
    if os.path.exists(ann_p):
        ann = pd.read_csv(ann_p).set_index("barcode")
        wc["annotation_label"] = ann["cell_type"].reindex(wc.index).values

    rna, genes, bcs = read_mtx(os.path.join(IG, "rna.mtx.gz"),
                               os.path.join(IG, "rna_features.tsv.gz"), os.path.join(IG, "barcodes.tsv.gz"))
    adt, adtn, _ = read_mtx(os.path.join(IG, "adt.mtx.gz"),
                            os.path.join(IG, "adt_features.tsv.gz"), os.path.join(IG, "barcodes.tsv.gz"))
    adtn = canonical_adt_feature_names(adtn, context="integrated ADT panel")
    pos = {b: i for i, b in enumerate(bcs)}
    sel = [pos[b] for b in keep if b in pos]; obs_names = [bcs[i] for i in sel]

    Xr = sp.csr_matrix(rna[:, sel].T).astype(np.float32)
    rna_ad = ad.AnnData(X=Xr); rna_ad.obs_names = obs_names; rna_ad.var_names = genes
    rna_ad.layers["counts"] = rna_ad.X.copy()
    cpm = Xr.astype(np.float64); rs = np.asarray(cpm.sum(1)).ravel(); rs[rs == 0] = 1
    rna_ad.layers["lognorm"] = sp.csr_matrix(np.log1p(cpm.multiply(1e4 / rs[:, None]))).astype(np.float32)

    Pa = sp.csr_matrix(adt[:, sel].T).astype(np.float32)
    prot_ad = ad.AnnData(X=Pa); prot_ad.obs_names = obs_names; prot_ad.var_names = adtn
    prot_ad.layers["counts"] = prot_ad.X.copy()
    dsb_p = os.path.join(CK, "adt_normalized.csv")
    if os.path.exists(dsb_p):
        normalized_adt = load_validated_normalized_adt(dsb_p, obs_names, adtn)
        prot_ad.layers["normalized"] = normalized_adt
    else:
        raise FileNotFoundError(
            "normalized ADT export is missing; refusing an integrated object without its protein layer"
        )

    meta_cols = [c for c in ("condition", "replicate", "sample", "cell_type",
                             "reference_label", "annotation_label",
                             "seurat_clusters", "rna_weight", "adt_weight") if c in wc.columns]
    for a in (rna_ad, prot_ad):
        for c in meta_cols:
            a.obs[c] = wc.loc[obs_names, c].values
    umap = wc.loc[obs_names, ["wnnUMAP_1", "wnnUMAP_2"]].values.astype(np.float32)
    rna_ad.obsm["X_wnn_umap"] = umap; prot_ad.obsm["X_wnn_umap"] = umap
    tv_p = os.path.join(CK, "totalvi_latent.csv"); totalvi_ran = os.path.exists(tv_p)
    if totalvi_ran:
        tv = pd.read_csv(tv_p, index_col=0).reindex(obs_names)
        rna_ad.obsm["X_totalvi"] = tv.values.astype(np.float32)

    shared_obs = wc.loc[obs_names, meta_cols].copy()
    mdata = construct_mudata(
        md, {"rna": rna_ad, "prot": prot_ad}, obs_names, shared_obs=shared_obs
    )
    ws_h5mu = os.path.join(OUT, "integrated.h5mu")
    persisted = write_readback_validate(
        md, mdata, ws_h5mu,
        required_modality_obs_columns={"rna": meta_cols, "prot": meta_cols},
    )
    assert_h5mu_normalized_adt(persisted, normalized_adt, obs_names, adtn)
    subprocess.run(["cp", ws_h5mu, os.path.join(RES, "integrated.h5mu")], check=True)

    # ---- integration metrics (deterministic, sklearn) ----
    clusters = wc.loc[obs_names, "seurat_clusters"].astype(str).values
    rows = []
    def add(m, v, d): rows.append({"metric": m, "value": (round(float(v), 4) if v == v else "NA"), "description": d})
    add("n_joint_cells", len(obs_names), "cells in the joint embedding")
    add("n_joint_clusters", len(set(clusters)), "WNN clusters")
    add("median_rna_modality_weight", np.median(wc.loc[obs_names, "rna_weight"]), "median per-cell RNA weight (ADT=1-this)")
    try: add("cluster_silhouette_wnn", silhouette_score(umap, clusters), "cluster separation on WNN UMAP (-1..1)")
    except Exception: add("cluster_silhouette_wnn", np.nan, "cluster separation on WNN UMAP (-1..1)")
    # ---- label purity via the independent-label evidence model (never circular) ----
    import label_purity as lp
    reference = (wc.loc[obs_names, "reference_label"].values
                 if "reference_label" in wc.columns else [None] * len(obs_names))
    annotation = (wc.loc[obs_names, "annotation_label"].values
                  if "annotation_label" in wc.columns else None)
    lp_state = lp.evaluate_label_purity(
        clusters, reference, annotation=annotation, join_index=list(obs_names),
        reference_source={"field": label_key, "origin": "user_metadata_column",
                          "file": "checkpoints/wnn_cells.csv", "join_key": "barcode",
                          "annotation_field": "checkpoints/cell_annotations.csv:cell_type",
                          "captured_before_annotation": True},
        min_coverage=float(INT.get("label_min_coverage", 0.5)))
    concordance = (lp.annotation_concordance(reference, annotation, clusters=clusters)
                   if annotation is not None else
                   {"schema": lp.CONCORDANCE_SCHEMA, "n_cells_scored": 0, "confusion_long": [],
                    "splits": {}, "merges": {}, "unreconciled_annotation_labels": [],
                    "annotation_labels_absent_from_reference": [], "missing_reference_labels": [],
                    "concordance_frac": None})
    if lp_state["state"] == "evaluable":
        add("label_purity_ARI", lp_state["metrics"]["label_purity_ARI"],
            "WNN clusters vs INDEPENDENT reference labels (ARI); source=%s" % lp_state["label_source"].get("field"))
        add("label_purity_NMI", lp_state["metrics"]["label_purity_NMI"],
            "WNN clusters vs INDEPENDENT reference labels (NMI); source=%s" % lp_state["label_source"].get("field"))
        rows.append({"metric": "label_purity_state", "value": "evaluable",
                     "description": "independent reference established; coverage=%s, n_scored=%s"
                                    % (lp_state["coverage"]["coverage_frac"], lp_state.get("n_scored"))})
    else:
        rows.append({"metric": "label_purity_state", "value": "not_evaluable_independent_labels",
                     "description": "reason=%s; no ARI/NMI is claimed" % lp_state["reason"]})
    # persist the machine-readable metric state + the annotation-vs-reference confusion table
    lp_state["annotation_concordance"] = {k: concordance.get(k) for k in
        ("n_cells_scored", "concordance_frac", "unreconciled_annotation_labels",
         "annotation_labels_absent_from_reference", "missing_reference_labels", "splits", "merges")}
    with open(os.path.join(TAB, "label_purity_status.json"), "w") as fh:
        json.dump(lp_state, fh, indent=2, sort_keys=True)
    pd.DataFrame(concordance.get("confusion_long", []) or [],
                 columns=["reference", "annotation", "n_cells"]).to_csv(
        os.path.join(TAB, "annotation_concordance.csv"), index=False)
    # provenance witness block (flat scalars; survives the R read-modify-write in step 08)
    prov_p = os.path.join(RES, "provenance_manifest.json")
    prov = json.load(open(prov_p)) if os.path.exists(prov_p) else {}
    prov["label_purity"] = {
        "state": lp_state["state"], "reason": lp_state["reason"],
        "independence_established": bool(lp_state["independence_established"]),
        "circular_metric_emitted": int(lp_state["circular_metric_emitted"]),
        "circularity_guard_violations": int(lp_state["circularity_guard_violations"]),
        "reference_field": lp_state["label_source"].get("field"),
        "reference_origin": lp_state["label_source"].get("origin"),
        "join_key": lp_state["label_source"].get("join_key"),
        "coverage_frac": lp_state["coverage"]["coverage_frac"],
        "n_scored": int(lp_state.get("n_scored", 0)),
        "n_reference_labels": int(lp_state["n_reference_labels"]),
        "n_unreconciled_annotation_labels": len(concordance.get("unreconciled_annotation_labels", [])),
        "label_purity_ARI": lp_state["metrics"]["label_purity_ARI"],
        "label_purity_NMI": lp_state["metrics"]["label_purity_NMI"]}
    with open(prov_p, "w") as fh:
        json.dump(prov, fh, indent=2, sort_keys=True)
    if batch_key in wc.columns:
        add("batch_mixing_entropy", knn_batch_entropy(umap, wc.loc[obs_names, batch_key].values),
            f"kNN batch-label entropy by {batch_key} (1=well mixed)")
    if totalvi_ran:
        from sklearn.neighbors import NearestNeighbors
        k = min(30, len(obs_names) - 1)
        nn_w = NearestNeighbors(n_neighbors=k).fit(umap).kneighbors(umap, return_distance=False)
        nn_t = NearestNeighbors(n_neighbors=k).fit(rna_ad.obsm["X_totalvi"]).kneighbors(
            rna_ad.obsm["X_totalvi"], return_distance=False)
        ov = np.mean([len(set(a) & set(b)) / k for a, b in zip(nn_w, nn_t)])
        add("wnn_totalvi_knn_agreement", ov, "kNN overlap between WNN UMAP and totalVI latent (0..1)")
    pd.DataFrame(rows).to_csv(os.path.join(TAB, "joint_qc_scorecard.csv"), index=False)

    # ---- figure 6: integration metrics (left) + annotation-vs-reference concordance (right) ----
    import matplotlib; matplotlib.use("Agg")
    matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
    matplotlib.rcParams["svg.fonttype"] = "none"
    import matplotlib.pyplot as plt
    tv_stat = json.load(open(os.path.join(TAB, "totalvi_status.json"))) if os.path.exists(os.path.join(TAB, "totalvi_status.json")) else {"ran": False, "reason": "not run"}
    plot_m = [r for r in rows if isinstance(r["value"], float) and r["metric"] not in ("n_joint_cells",)]
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12.5, 4.8), gridspec_kw={"width_ratios": [1.05, 1]})
    names = [r["metric"] for r in plot_m]; vals = [r["value"] for r in plot_m]
    ax.barh(names, vals, color="#D4A04A", edgecolor="white")
    for i, v in enumerate(vals): ax.text(v, i, f" {v:.3f}", va="center", fontsize=8, color="#2C2A26")
    ax.set_xlabel("value"); ax.set_title("Joint integration metrics")
    lp_msg = (("label purity vs INDEPENDENT reference: ARI=%.3f, NMI=%.3f"
               % (lp_state["metrics"]["label_purity_ARI"], lp_state["metrics"]["label_purity_NMI"]))
              if lp_state["state"] == "evaluable"
              else ("label purity: not evaluable (%s) — no ARI/NMI claimed" % lp_state["reason"]))
    ax.text(0.0, -0.20, lp_msg, transform=ax.transAxes, ha="left", fontsize=8, color="#2C2A26")
    ax.text(0.0, -0.30, ("totalVI: ran (n_latent=%s)" % tv_stat.get("n_latent")) if tv_stat.get("ran")
            else "totalVI: documented skip — " + str(tv_stat.get("reason"))[:60],
            transform=ax.transAxes, ha="left", fontsize=8, color="#8A8378")
    conf = pd.DataFrame(concordance.get("confusion_long", []) or [])
    if len(conf):
        piv = conf.pivot_table(index="reference", columns="annotation", values="n_cells",
                               aggfunc="sum", fill_value=0)
        ax2.imshow(piv.values, cmap="Oranges", aspect="auto")
        ax2.set_xticks(range(piv.shape[1])); ax2.set_xticklabels(list(piv.columns), rotation=35, ha="right", fontsize=8)
        ax2.set_yticks(range(piv.shape[0])); ax2.set_yticklabels(list(piv.index), fontsize=8)
        vmax = piv.values.max() if piv.values.size else 1
        for i in range(piv.shape[0]):
            for j in range(piv.shape[1]):
                v = int(piv.values[i, j])
                if v:
                    ax2.text(j, i, str(v), ha="center", va="center", fontsize=7,
                             color="#2C2A26" if v < vmax * 0.6 else "white")
        ax2.set_xlabel("marker annotation"); ax2.set_ylabel("independent reference")
        novel = [d["annotation"] for d in concordance.get("unreconciled_annotation_labels", [])]
        ax2.set_title("Annotation vs reference" + (" — novel: " + ", ".join(novel) if novel else ""), fontsize=10)
    else:
        ax2.axis("off")
        ax2.text(0.5, 0.5, "label purity not evaluable\n(no independent reference labels)\n"
                 "annotation concordance not computed",
                 ha="center", va="center", fontsize=10, color="#8A8378")
    plt.tight_layout()
    for ext in ("png", "svg"):
        plt.savefig(os.path.join(FIG, f"figure_6_compute_the_totalvi.{ext}"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("integrated.h5mu + joint_qc_scorecard.csv + figure_6 written; label_purity=%s; totalVI ran=%s"
          % (lp_state["state"], tv_stat.get("ran")))


if __name__ == "__main__":
    main()
