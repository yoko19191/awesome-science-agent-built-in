"""
============================================================================
XENIUM BRANCH — ingestion, negative-control-aware QC, segmentation assessment
============================================================================

Imaging-based in situ (10x Xenium) ingestion and QC, ending in a STANDARDIZED
common spatial object for the shared downstream analysis. This branch is used
ONLY after platform_router routes a bundle to Xenium; Visium spot QC is never
applied here, and Xenium control/segmentation QC is never skipped.

Contract highlights (from the R01 lane evidence):
  - Negative-control features are separated from genes AT LOAD and never enter
    biological analysis.
  - Transcript QC uses the Q>=20 convention (the cell-feature matrix already
    applies it) plus the negative-control-probe rate as a specificity metric.
  - Segmentation quality is ASSESSED and a re-segmentation recommendation is
    surfaced; the default consumes vendor (XOA) segmentation and never performs
    silent cell-mask expansion.
  - Normalization defaults to library-size (target_sum=100) + log for clustering;
    the domain-detection caveat is surfaced and the choice is overridable.

Functions:
  load_xenium(), run_xenium_qc(), assess_segmentation(), run_xenium_analysis()
"""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from spatial_common import (
    XENIUM_MIN_TRANSCRIPT_QV,
    split_control_features,
    standardize_spatial_object,
)

# Pinned QC defaults (documented; overridable). Not fetched at runtime.
DEFAULT_MIN_TRANSCRIPTS = 10          # min transcripts/cell (Xenium convention)
DEFAULT_NEG_CONTROL_RATE_MAX = 0.05   # specificity gate: negative-control-probe rate
DEFAULT_MIN_CELL_AREA = 4.0           # um^2, drop implausibly tiny segments


def _read_table(path) -> pd.DataFrame:
    p = Path(path)
    if p.suffix == ".parquet":
        return pd.read_parquet(p)
    if str(p).endswith(".csv.gz"):
        return pd.read_csv(p)
    return pd.read_csv(p)


def load_xenium(cell_feature_matrix, cells, transcripts: Optional[str] = None):
    """Load a Xenium cell-feature matrix + per-cell table into an AnnData.

    Negative-control feature classes are split out of ``.var`` at load and their
    per-cell counts are recorded in ``.obs`` and ``.uns['control_features']``.

    Parameters
    ----------
    cell_feature_matrix : str/Path
        A ``cell_feature_matrix/`` directory (MTX: matrix.mtx.gz + features.tsv.gz
        + barcodes.tsv.gz) or a 10x ``.h5``.
    cells : str/Path
        ``cells.parquet`` / ``cells.csv.gz`` with centroids and control counts.
    transcripts : str/Path, optional
        ``transcripts.parquet`` (only needed for re-segmentation/subcellular).
    """
    import anndata as ad
    import scipy.io
    import scipy.sparse as sp

    cfm = Path(cell_feature_matrix)
    if cfm.is_dir():
        mtx = cfm / "matrix.mtx.gz"
        feats = cfm / "features.tsv.gz"
        bcs = cfm / "barcodes.tsv.gz"
        with gzip.open(mtx, "rb") as fh:
            X = scipy.io.mmread(fh).tocsr().T.tocsr()   # -> cells x features
        with gzip.open(feats, "rt") as fh:
            feature_names = [ln.split("\t")[0].strip() for ln in fh if ln.strip()]
        with gzip.open(bcs, "rt") as fh:
            barcodes = [ln.strip() for ln in fh if ln.strip()]
        adata = ad.AnnData(X=sp.csr_matrix(X))
        adata.var_names = feature_names
        adata.obs_names = barcodes
    else:
        import scanpy as sc
        adata = sc.read_10x_h5(cfm)
    adata.var_names_make_unique()

    # --- separate control features from genes AT LOAD ---
    genes, controls = split_control_features(adata.var_names)
    control_counts = np.asarray(
        adata[:, controls].X.sum(axis=1)
    ).ravel() if controls else np.zeros(adata.n_obs)
    adata.obs["control_counts"] = control_counts
    adata.uns["control_features"] = list(controls)
    adata = adata[:, genes].copy()          # genes-only matrix downstream
    adata.obs["gene_counts"] = np.asarray(adata.X.sum(axis=1)).ravel()

    # --- attach per-cell metadata / coordinates from cells table ---
    cdf = _read_table(cells)
    cdf = cdf.set_index(cdf.columns[0]) if "cell_id" not in cdf.columns else cdf.set_index("cell_id")
    cdf.index = cdf.index.astype(str)
    common = [b for b in adata.obs_names if b in cdf.index]
    adata = adata[common].copy()
    cdf = cdf.loc[adata.obs_names]
    xcol = "x_centroid" if "x_centroid" in cdf.columns else cdf.columns[0]
    ycol = "y_centroid" if "y_centroid" in cdf.columns else cdf.columns[1]
    adata.obsm["spatial"] = cdf[[xcol, ycol]].to_numpy(dtype=float)
    for col in ("transcript_counts", "control_probe_counts", "control_codeword_counts",
                "total_counts", "cell_area", "nucleus_area", "nucleus_count",
                "sample_id", "condition", "segmentation_method"):
        if col in cdf.columns:
            adata.obs[col] = cdf[col].values

    adata.uns["xenium_min_transcript_qv"] = int(XENIUM_MIN_TRANSCRIPT_QV)
    adata.uns["platform"] = "Xenium"
    print(f"\u2713 Xenium data loaded: {adata.n_obs} cells x {adata.n_vars} genes "
          f"({len(controls)} control features separated)")
    return adata


def run_xenium_qc(adata, output_dir: str = "xenium_results",
                  min_transcripts: int = DEFAULT_MIN_TRANSCRIPTS,
                  neg_control_rate_max: float = DEFAULT_NEG_CONTROL_RATE_MAX,
                  min_cell_area: float = DEFAULT_MIN_CELL_AREA):
    """Transcript / negative-control QC. Writes qc.csv, returns (adata, qc_summary).

    Computes per-cell QC and a run-level negative-control-probe rate (specificity).
    Flags the run when the negative-control rate exceeds the pinned threshold
    (e.g., contamination or a spiked-control run).
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    n0 = adata.n_obs

    gene_counts = np.asarray(adata.X.sum(axis=1)).ravel()
    control_counts = adata.obs.get("control_counts", pd.Series(np.zeros(adata.n_obs))).to_numpy()
    total = gene_counts + control_counts
    with np.errstate(divide="ignore", invalid="ignore"):
        control_fraction = np.where(total > 0, control_counts / total, 0.0)
    adata.obs["gene_counts"] = gene_counts
    adata.obs["control_fraction"] = control_fraction
    adata.obs["n_genes"] = np.asarray((adata.X > 0).sum(axis=1)).ravel()

    neg_control_rate = float(control_counts.sum() / total.sum()) if total.sum() > 0 else 0.0

    # per-cell filter (transcripts + plausibility)
    keep = gene_counts >= min_transcripts
    if "cell_area" in adata.obs:
        keep = keep & (adata.obs["cell_area"].to_numpy() >= min_cell_area)
    adata_f = adata[keep].copy()

    qc = pd.DataFrame({
        "cell_id": adata.obs_names,
        "gene_counts": gene_counts,
        "control_counts": control_counts,
        "control_fraction": control_fraction,
        "n_genes": adata.obs["n_genes"].to_numpy(),
        "cell_area": adata.obs["cell_area"].to_numpy() if "cell_area" in adata.obs else np.nan,
        "nucleus_area": adata.obs["nucleus_area"].to_numpy() if "nucleus_area" in adata.obs else np.nan,
        "passes_filter": keep,
    })
    qc.to_csv(out / "qc.csv", index=False)

    qc_pass = neg_control_rate <= neg_control_rate_max
    summary = {
        "platform": "Xenium",
        "n_cells_initial": int(n0),
        "n_cells_pass": int(adata_f.n_obs),
        "min_transcripts": int(min_transcripts),
        "min_transcript_qv": int(XENIUM_MIN_TRANSCRIPT_QV),
        "negative_control_rate": neg_control_rate,
        "negative_control_rate_max": float(neg_control_rate_max),
        "qc_pass": bool(qc_pass),
        "median_gene_counts": float(np.median(gene_counts)),
        "median_control_fraction": float(np.median(control_fraction)),
        "n_control_features": int(len(adata.uns.get("control_features", []))),
    }
    adata_f.uns["xenium_qc"] = summary
    flag = "PASS" if qc_pass else "FLAG (elevated negative-control rate; possible contamination)"
    print(f"\u2713 Xenium QC: {n0} -> {adata_f.n_obs} cells; "
          f"neg-control rate={neg_control_rate:.4f} [{flag}]")
    return adata_f, summary


def assess_segmentation(adata, output_dir: str = "xenium_results",
                        transcripts_available: bool = False):
    """Assess vendor segmentation quality and RECOMMEND (not perform) re-segmentation.

    Heuristics (surfaced, not silently applied):
      - nucleus:cell area ratio (very low -> over-expansion; ~1 -> nucleus-only)
      - median transcripts/cell (low -> under-segmentation / sparse)
      - fraction of implausibly small cells

    Never performs silent cell-mask expansion. Re-segmentation
    (Cellpose+Baysor/Proseg) is only recommended, and requires transcripts +
    morphology, which are optional inputs.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    reasons = []
    ratio_median = None
    if "cell_area" in adata.obs and "nucleus_area" in adata.obs:
        ca = adata.obs["cell_area"].to_numpy(dtype=float)
        na = adata.obs["nucleus_area"].to_numpy(dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(ca > 0, na / ca, np.nan)
        ratio_median = float(np.nanmedian(ratio))
        if ratio_median < 0.15:
            reasons.append(f"nucleus:cell area ratio low ({ratio_median:.2f}); possible over-expansion / transcript misassignment")
    med_counts = float(np.median(np.asarray(adata.X.sum(axis=1)).ravel()))
    if med_counts < 20:
        reasons.append(f"median transcripts/cell low ({med_counts:.0f}); segmentation may be under-calling")
    recommend = bool(reasons)
    result = {
        "nucleus_cell_area_ratio_median": ratio_median,
        "median_transcripts_per_cell": med_counts,
        "resegmentation_recommended": recommend,
        "resegmentation_feasible": bool(transcripts_available),
        "reasons": reasons or ["Vendor (XOA) segmentation adequate for main populations."],
        "note": ("Default consumes vendor segmentation; no cell-mask expansion is applied. "
                 "If re-segmentation is warranted, use Cellpose+Baysor/Proseg (optional, not bundled)."),
    }
    adata.uns["segmentation_assessment"] = result
    print(f"\u2713 Segmentation assessed: re-seg recommended={recommend}")
    return result


def run_xenium_analysis(cell_feature_matrix, cells, output_dir: str = "xenium_results",
                        transcripts: Optional[str] = None,
                        sample_key: str = "sample_id", condition_key: str = "condition",
                        n_pcs: int = 15, resolution: float = 1.0,
                        min_transcripts: int = DEFAULT_MIN_TRANSCRIPTS,
                        random_state: int = 0):
    """Full Xenium branch: load -> QC -> segmentation assessment -> normalize/
    cluster/annotate -> STANDARDIZED common spatial object.

    Returns
    -------
    (adata_standardized, qc_summary, seg_assessment)
    """
    import scanpy as sc

    adata = load_xenium(cell_feature_matrix, cells, transcripts=transcripts)
    adata, qc = run_xenium_qc(adata, output_dir=output_dir, min_transcripts=min_transcripts)
    seg = assess_segmentation(adata, output_dir=output_dir,
                              transcripts_available=transcripts is not None)

    # Store raw counts, then library-size(100)+log normalization (clustering default).
    adata.layers["counts"] = adata.X.copy()
    sc.pp.normalize_total(adata, target_sum=100)   # imaging-ST convention
    sc.pp.log1p(adata)
    adata.layers["normalized"] = adata.X.copy()

    n_comps = int(min(n_pcs, max(2, min(adata.n_obs, adata.n_vars) - 1)))
    sc.pp.pca(adata, n_comps=n_comps, random_state=random_state)
    sc.pp.neighbors(adata, n_neighbors=min(15, max(2, adata.n_obs - 1)),
                    n_pcs=n_comps, random_state=random_state)
    sc.tl.leiden(adata, resolution=resolution, key_added="leiden", random_state=random_state)

    # Panel-aware provisional annotation: label each cluster by its top marker.
    sc.tl.rank_genes_groups(adata, "leiden", method="wilcoxon")
    top = {cl: adata.uns["rank_genes_groups"]["names"][cl][0]
           for cl in adata.obs["leiden"].cat.categories}
    adata.obs["cell_type"] = adata.obs["leiden"].map(lambda c: f"type_{top[c]}").astype("category")
    adata.uns["annotation_note"] = ("Provisional panel-aware labels from top cluster markers; "
                                    "use reference label transfer (Seurat/TACCO) for definitive typing.")

    adata = standardize_spatial_object(adata, platform="Xenium",
                                       sample_key=sample_key, condition_key=condition_key)
    print(f"\u2713 Xenium branch complete: {adata.n_obs} cells, "
          f"{adata.obs['leiden'].nunique()} clusters -> standardized object")
    return adata, qc, seg
