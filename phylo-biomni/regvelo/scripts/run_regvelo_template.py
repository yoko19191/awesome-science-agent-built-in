"""Template for /input/run_regvelo.py submitted to the HPC RegVelo tool.

Copy this file, edit the CONFIG block, then submit it as run_regvelo.py with
hpc_run_tool(tool_id="regvelo", command="python /input/run_regvelo.py", ...).
"""

from __future__ import annotations

import json
from pathlib import Path

import anndata as ad
import pandas as pd
import regvelo as rgv
from regvelo import REGVELOVI


INPUT = Path("/input")
OUTPUT = Path("/output")
OBJECTS = OUTPUT / "objects"
MODEL = OUTPUT / "model"
FIGURES = OUTPUT / "figures"
TABLES = OUTPUT / "tables"

CONFIG = {
    "adata": "adata_input.h5ad",
    "prior_grn": "prior_grn.parquet",
    "cluster_key": "cell_type",
    "spliced_layer": "Ms",
    "unspliced_layer": "Mu",
    "max_epochs": 400,
    "train_batch_size": 512,
    "output_batch_size": 256,
    "output_n_samples": 20,
    "lam": 1.0,
}


def ensure_output_dirs() -> None:
    for path in (OBJECTS, MODEL, FIGURES, TABLES):
        path.mkdir(parents=True, exist_ok=True)


def load_prior_grn(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    if path.suffix == ".csv":
        return pd.read_csv(path, index_col=0)
    raise ValueError(f"Unsupported prior GRN format: {path}")


def pick_layer(adata: ad.AnnData, preferred: str, fallback: str) -> str:
    if preferred in adata.layers:
        return preferred
    if fallback in adata.layers:
        return fallback
    raise ValueError(f"Missing required AnnData layer: {preferred!r} or {fallback!r}")


def main() -> None:
    ensure_output_dirs()

    adata_path = INPUT / CONFIG["adata"]
    grn_path = INPUT / CONFIG["prior_grn"]
    cluster_key = CONFIG["cluster_key"]

    adata = ad.read_h5ad(adata_path)
    if cluster_key not in adata.obs:
        raise ValueError(f"Missing adata.obs[{cluster_key!r}]")

    spliced_layer = pick_layer(adata, CONFIG["spliced_layer"], "spliced")
    unspliced_layer = pick_layer(adata, CONFIG["unspliced_layer"], "unspliced")
    prior_grn = load_prior_grn(grn_path)

    rgv.pp.set_prior_grn(adata, prior_grn)
    REGVELOVI.setup_anndata(
        adata,
        spliced_layer=spliced_layer,
        unspliced_layer=unspliced_layer,
    )

    weights = adata.uns["skeleton"].T if "skeleton" in adata.uns else None
    regulators = adata.uns.get("regulators")
    model = REGVELOVI(adata, W=weights, regulators=regulators, lam=CONFIG["lam"])
    model.train(
        max_epochs=CONFIG["max_epochs"],
        batch_size=CONFIG["train_batch_size"],
        accelerator="gpu",
        devices=1,
    )

    rgv.tl.set_output(
        adata,
        model,
        n_samples=CONFIG["output_n_samples"],
        batch_size=CONFIG["output_batch_size"],
    )

    adata.write_h5ad(OBJECTS / "adata_regvelo.h5ad")
    model.save(str(MODEL / "regvelo_model"), overwrite=True)

    summary = {
        "n_obs": int(adata.n_obs),
        "n_vars": int(adata.n_vars),
        "cluster_key": cluster_key,
        "spliced_layer": spliced_layer,
        "unspliced_layer": unspliced_layer,
        "train_batch_size": CONFIG["train_batch_size"],
        "output_batch_size": CONFIG["output_batch_size"],
        "output_n_samples": CONFIG["output_n_samples"],
        "regvelo_version": getattr(rgv, "__version__", "unknown"),
    }
    (TABLES / "regvelo_run_summary.json").write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
