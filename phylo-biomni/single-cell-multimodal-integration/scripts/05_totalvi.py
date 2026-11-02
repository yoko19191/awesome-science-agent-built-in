#!/usr/bin/env python3
"""
05_totalvi.py — totalVI probabilistic RNA+ADT alternative to WNN.

Reads the ingest MatrixMarket counts + the WNN-retained cells, trains totalVI
(scvi-tools) for a bounded number of epochs on CPU, and writes a joint latent
space. totalVI outputs are model PREDICTIONS, not measurements: it runs only
when its assumptions hold (>= totalvi.min_cells joint cells and scvi-tools
importable), otherwise a documented skip is recorded in totalvi_status.json.

Usage: python 05_totalvi.py --config config.yaml
"""
import argparse, gzip, json, os, sys, warnings
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, scipy.io as sio, scipy.sparse as sp

ap = argparse.ArgumentParser(); ap.add_argument("--config", required=True)
CFG = __import__("yaml").safe_load(open(ap.parse_args().config))
OUT = CFG["project"]["outdir"]
IG = os.path.join(OUT, "ingest"); CK = os.path.join(OUT, "checkpoints")
TAB = os.path.join(OUT, "tables"); os.makedirs(TAB, exist_ok=True); os.makedirs(CK, exist_ok=True)
TV = CFG.get("totalvi", {}) or {}


def status(d):
    json.dump(d, open(os.path.join(TAB, "totalvi_status.json"), "w"), indent=2)
    print("totalvi_status:", d.get("reason"))


def read_mtx(mtx, feats, bcs):
    m = sio.mmread(gzip.open(mtx)).tocsr()                 # features x cells
    f = gzip.open(feats, "rt").read().split("\n"); f = [x for x in f if x]
    b = gzip.open(bcs, "rt").read().split("\n"); b = [x for x in b if x]
    return m, f, b


def main():
    if not (TV.get("enabled", True)):
        return status({"ran": False, "reason": "totalvi.enabled=false in config", "method": "totalVI"})
    wc = pd.read_csv(os.path.join(CK, "wnn_cells.csv"))
    keep = wc["barcode"].astype(str).tolist()
    min_cells = int(TV.get("min_cells", 200))
    if len(keep) < min_cells:
        return status({"ran": False, "method": "totalVI", "n_cells": len(keep),
                       "reason": f"{len(keep)} joint cells < totalvi.min_cells ({min_cells}); WNN remains the headline embedding"})
    try:
        import torch, scvi, anndata as ad
        torch.set_num_threads(max(1, os.cpu_count() or 1))
        _seed = int(TV.get("seed", 0))
        torch.manual_seed(_seed)
        np.random.seed(_seed)
        rna, genes, bcs = read_mtx(os.path.join(IG, "rna.mtx.gz"),
                                   os.path.join(IG, "rna_features.tsv.gz"), os.path.join(IG, "barcodes.tsv.gz"))
        adt, adtn, _ = read_mtx(os.path.join(IG, "adt.mtx.gz"),
                                os.path.join(IG, "adt_features.tsv.gz"), os.path.join(IG, "barcodes.tsv.gz"))
        idx = {b: i for i, b in enumerate(bcs)}
        sel = [idx[b] for b in keep if b in idx]
        X = sp.csr_matrix(rna[:, sel].T).astype(np.float32)     # cells x genes
        P = np.asarray(adt[:, sel].T.todense(), dtype=np.float32)  # cells x adt
        adata = ad.AnnData(X=X); adata.obs_names = [bcs[i] for i in sel]; adata.var_names = genes
        adata.layers["counts"] = adata.X.copy()
        adata.obsm["protein_expression"] = pd.DataFrame(P, index=adata.obs_names, columns=adtn)
        scvi.model.TOTALVI.setup_anndata(adata, layer="counts", protein_expression_obsm_key="protein_expression")
        model = scvi.model.TOTALVI(adata, latent_distribution="normal", n_latent=int(TV.get("n_latent", 15)))
        model.train(max_epochs=int(TV.get("max_epochs", 40)), accelerator="cpu",
                    early_stopping=True, enable_progress_bar=False)
        Z = model.get_latent_representation()
        pd.DataFrame(Z, index=adata.obs_names,
                     columns=[f"totalVI_{i+1}" for i in range(Z.shape[1])]).to_csv(
            os.path.join(CK, "totalvi_latent.csv"))
        hist = model.history.get("elbo_train")
        elbo = float(hist.iloc[-1, 0]) if hist is not None and len(hist) else None
        status({"ran": True, "method": "totalVI", "n_cells": int(adata.n_obs),
                "n_latent": int(Z.shape[1]), "max_epochs": int(TV.get("max_epochs", 40)),
                "final_elbo_train": elbo,
                "reason": "totalVI trained; latent space is a model prediction, not a measurement",
                "caveat": "Cross-modality denoising/imputation from totalVI are hypotheses to validate against measured data."})
    except Exception as e:
        status({"ran": False, "method": "totalVI", "n_cells": len(keep),
                "reason": f"totalVI unavailable or failed -> documented skip: {type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
