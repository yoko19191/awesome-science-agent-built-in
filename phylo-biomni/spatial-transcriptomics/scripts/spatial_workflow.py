"""
============================================================================
SPATIAL TRANSCRIPTOMICS ANALYSIS WORKFLOW
============================================================================

Complete Visium spatial analysis: QC -> filtering -> normalization ->
clustering -> spatial neighbors -> SVGs -> neighborhood enrichment ->
co-occurrence.

Functions:
  - run_spatial_analysis(): Execute complete spatial workflow

Usage:
  from spatial_workflow import run_spatial_analysis
  adata = run_spatial_analysis(adata)
"""

import numpy as np
import pandas as pd
from typing import Optional, List


class SpatialNotComputable(Exception):
    """Stop the run with a structured, actionable result instead of an opaque downstream error.

    Finding 2 (v5): raised when a stage cannot proceed on the given inputs/thresholds — e.g. platform
    QC filtering removed every spot, which would otherwise crash deep inside normalize/HVG/PCA with an
    empty-matrix/scikit-learn error. Carries the threshold that caused it, what was observed, and an
    explicit next step, so the caller can surface a not_computable contract rather than a traceback.
    """

    def __init__(self, *, reason: str, threshold: dict, observed: dict, suggestion: str, stage: str):
        super().__init__(reason)
        self.reason = reason
        self.threshold = threshold
        self.observed = observed
        self.suggestion = suggestion
        self.stage = stage

    def as_dict(self, *, platform: Optional[str] = None) -> dict:
        payload = {
            "result": "not_computable",
            "stage": self.stage,
            "reason": self.reason,
            "threshold": self.threshold,
            "observed": self.observed,
            "suggestion": self.suggestion,
        }
        if platform is not None:
            payload["platform"] = platform
        return payload


# Threshold keys that MUST be reported from the exact applied parameter object (Finding 3). The
# provenance builder copies these from ``adata.uns['spatial_analysis_params']`` — never a hard-coded
# provenance default — and the consistency gate refuses a mismatch.
VISIUM_THRESHOLD_KEYS = ("min_genes", "min_cells", "max_pct_mito")

# 10x matrices may use human-style upper-case symbols (``MT-CO1``) or mouse-style lower-case
# symbols (``mt-Co1``). Prefer explicitly annotated semantic gene-name columns over feature IDs,
# then retain ``var_names`` as the standard Scanpy/Space Ranger compatibility path.
MITOCHONDRIAL_PREFIXES = ("MT-", "mt-")
MITOCHONDRIAL_MATCHING_RULE = "case_sensitive_species_prefix"
SEMANTIC_GENE_NAME_COLUMNS = (
    "gene_symbols", "gene_symbol", "feature_name", "gene_name", "symbol",
)


def resolve_mitochondrial_qc(adata) -> tuple[np.ndarray, np.ndarray, dict]:
    """Resolve mitochondrial features and prove that the resulting percentage is evaluable.

    The first semantic gene-name source with a recognized human or mouse prefix wins. A run stops
    explicitly when annotation cannot identify any mitochondrial feature, or when identified
    mitochondrial features have zero counts in every spot. Both states previously collapsed to an
    apparently healthy all-0% metric and silently disabled the mitochondrial filter.
    """
    checked_sources: list[str] = []
    candidates: list[tuple[str, np.ndarray]] = []
    var = getattr(adata, "var", None)
    if var is not None:
        for column in SEMANTIC_GENE_NAME_COLUMNS:
            if column in var.columns:
                candidates.append((f"var.{column}", np.asarray(var[column], dtype=str)))
    candidates.append(("var_names", np.asarray(adata.var_names, dtype=str)))

    selected_source = None
    selected_mask = None
    matched_prefixes: list[str] = []
    for source, names in candidates:
        checked_sources.append(source)
        masks = [np.char.startswith(names, prefix) for prefix in MITOCHONDRIAL_PREFIXES]
        mask = np.logical_or.reduce(masks)
        if mask.any():
            selected_source = source
            selected_mask = np.asarray(mask, dtype=bool)
            matched_prefixes = [
                prefix for prefix, prefix_mask in zip(MITOCHONDRIAL_PREFIXES, masks)
                if np.asarray(prefix_mask).any()
            ]
            break

    if selected_mask is None:
        raise SpatialNotComputable(
            reason=("No mitochondrial features matched the recognized human/mouse gene-symbol "
                    "prefixes; mitochondrial QC is not evaluable."),
            threshold={"accepted_prefixes": list(MITOCHONDRIAL_PREFIXES),
                       "matching_rule": MITOCHONDRIAL_MATCHING_RULE},
            observed={"checked_gene_name_sources": checked_sources, "n_mito_features": 0},
            suggestion=("Provide semantic gene symbols in var_names or one of "
                        f"{list(SEMANTIC_GENE_NAME_COLUMNS)} and retain human MT- or mouse mt- "
                        "mitochondrial symbols."),
            stage="visium_mitochondrial_qc",
        )

    counts = adata.X.toarray() if hasattr(adata.X, "toarray") else np.asarray(adata.X)
    counts = np.asarray(counts, dtype=float)
    if counts.ndim != 2 or counts.shape[1] != len(selected_mask):
        raise ValueError("Visium count matrix columns do not align with resolved gene names")
    mt_counts = counts[:, selected_mask].sum(axis=1).ravel()
    n_spots_with_mito_counts = int(np.count_nonzero(mt_counts > 0))
    if n_spots_with_mito_counts == 0:
        raise SpatialNotComputable(
            reason=("Mitochondrial features were recognized but every spot has zero mitochondrial "
                    "counts; refusing to treat an all-0% mitochondrial metric as a passing QC result."),
            threshold={"accepted_prefixes": list(MITOCHONDRIAL_PREFIXES),
                       "matching_rule": MITOCHONDRIAL_MATCHING_RULE},
            observed={"gene_name_source": selected_source,
                      "matched_prefixes": matched_prefixes,
                      "n_mito_features": int(selected_mask.sum()),
                      "n_spots_with_mito_counts": 0},
            suggestion=("Verify that the count matrix and semantic gene annotation are aligned and "
                        "that mitochondrial reads were not removed upstream."),
            stage="visium_mitochondrial_qc",
        )

    evidence = {
        "status": "evaluated",
        "matching_rule": MITOCHONDRIAL_MATCHING_RULE,
        "accepted_prefixes": list(MITOCHONDRIAL_PREFIXES),
        "matched_prefixes": matched_prefixes,
        "gene_name_source": selected_source,
        "n_mito_features": int(selected_mask.sum()),
        "n_spots_with_mito_counts": n_spots_with_mito_counts,
    }
    return selected_mask, mt_counts, evidence


def compute_visium_qc(adata, min_genes: int = 200, max_pct_mito: float = 50.0) -> pd.DataFrame:
    """Per-spot Visium QC with a REAL pass/fail flag, computed on the pre-filter object.

    Returns one row per original spot so ``qc.csv`` records both retained and filtered spots (the
    source driver hardcoded ``passes_filter=True`` on the already-filtered object, hiding every drop).
    The pass/fail flag applies the SAME thresholds later recorded in provenance.
    """
    X = adata.X
    counts = X.toarray() if hasattr(X, "toarray") else np.asarray(X)
    counts = np.asarray(counts, dtype=float)
    n_genes = (counts > 0).sum(axis=1).astype(int).ravel()
    total_counts = counts.sum(axis=1).ravel()
    _, mt_counts, mitochondrial_qc = resolve_mitochondrial_qc(adata)
    with np.errstate(divide="ignore", invalid="ignore"):
        pct_mito = np.where(total_counts > 0, 100.0 * mt_counts / total_counts, 0.0)
    passes = (n_genes >= int(min_genes)) & (pct_mito < float(max_pct_mito))
    qc = pd.DataFrame({
        "spot_id": list(adata.obs_names),
        "n_genes": n_genes,
        "total_counts": total_counts,
        "pct_counts_mt": pct_mito,
        "passes_filter": passes.astype(bool),
    })
    qc.attrs["mitochondrial_qc"] = mitochondrial_qc
    return qc


def visium_provenance_params(adata) -> dict:
    """Extract the EXACT applied QC thresholds/params for provenance from the run's own params object.

    Reads ``adata.uns['spatial_analysis_params']`` (populated by ``run_spatial_analysis`` with the
    values actually applied). No independent default is substituted — the single source of truth is
    the applied parameter object, so provenance can never disagree with what the run did.
    """
    params = dict(adata.uns.get("spatial_analysis_params", {}))
    thresholds = {k: params[k] for k in VISIUM_THRESHOLD_KEYS if k in params}
    return {"thresholds": thresholds, "parameters": params}


def run_spatial_analysis(
    adata: 'anndata.AnnData',
    min_genes: int = 200,
    min_cells: int = 10,
    max_pct_mito: float = 50.0,
    n_top_genes: int = 2000,
    n_pcs: int = 30,
    resolution: float = 0.8,
    n_neighbors: int = 15,
    svgs_n_perms: int = 100,
    svgs_n_jobs: int = 1,
    output_dir: str = "visium_results"
) -> 'anndata.AnnData':
    """
    Run complete spatial transcriptomics analysis.

    Pipeline:
    1. QC metrics (mitochondrial %, total counts, genes per spot)
    2. Filter spots (min_genes, max_mito)
    3. Normalize + log1p
    4. HVG selection (seurat_v3 on raw counts)
    5. PCA + Neighbors + UMAP + Leiden clustering
    6. Spatial neighbors graph
    7. Spatially variable genes (Moran's I)
    8. Neighborhood enrichment
    9. Co-occurrence analysis

    Parameters
    ----------
    adata : AnnData
        Caller-selected Visium data loaded by the production input adapter.
    min_genes : int
        Minimum genes per spot (default: 200).
    min_cells : int
        Minimum spots per gene (default: 10).
    max_pct_mito : float
        Maximum mitochondrial percentage (default: 50.0; heart tissue is MT-rich).
    n_top_genes : int
        Number of highly variable genes (default: 2000).
    n_pcs : int
        Number of PCA components (default: 30).
    resolution : float
        Leiden clustering resolution (default: 0.8).
    n_neighbors : int
        Number of neighbors for expression graph (default: 15).
    svgs_n_perms : int
        Permutations for Moran's I (default: 100).
    svgs_n_jobs : int
        Parallel jobs for Moran's I (default: 1).
    output_dir : str
        Output directory (default: "visium_results").

    Returns
    -------
    AnnData
        Processed adata with clustering, SVGs, enrichment results attached.
    """
    import scanpy as sc
    import squidpy as sq
    import os

    os.makedirs(output_dir, exist_ok=True)

    print("\n=== Step 2: Spatial Transcriptomics Analysis ===\n")
    n_spots_initial = adata.n_obs

    # --- 1. QC metrics ---
    print("1. Calculating QC metrics...")
    mt_mask, _, mitochondrial_qc = resolve_mitochondrial_qc(adata)
    adata.var['mt'] = mt_mask
    sc.pp.calculate_qc_metrics(
        adata, qc_vars=['mt'], percent_top=None, log1p=False, inplace=True
    )
    print(f"   Median genes/spot: {adata.obs['n_genes_by_counts'].median():.0f}")
    print(f"   Median counts/spot: {adata.obs['total_counts'].median():.0f}")
    print(f"   Median %MT: {adata.obs['pct_counts_mt'].median():.1f}%")

    # --- 2. Filter spots ---
    print(f"\n2. Filtering spots (min_genes={min_genes}, max_mito={max_pct_mito}%)...")

    # Store raw counts BEFORE any processing
    adata.layers['counts'] = adata.X.copy()

    sc.pp.filter_cells(adata, min_genes=min_genes)
    sc.pp.filter_genes(adata, min_cells=min_cells)
    adata = adata[adata.obs['pct_counts_mt'] < max_pct_mito, :].copy()

    n_spots_after = adata.n_obs
    retention = 100 * n_spots_after / n_spots_initial
    print(f"   Spots: {n_spots_initial} -> {n_spots_after} ({retention:.1f}% retained)")

    # Finding 2 (v5): if filtering removed every spot (or every gene), stop NOW with a structured,
    # actionable not_computable result. Continuing would crash opaquely inside normalize/HVG/PCA on an
    # empty matrix. Real-data defaults are unchanged; this only converts a guaranteed downstream
    # failure into an actionable stop that names the responsible threshold.
    if n_spots_after == 0 or adata.n_vars == 0:
        raise SpatialNotComputable(
            reason=(f"No spots passed QC: all {n_spots_initial} spots were removed by the filter "
                    f"(min_genes>={min_genes}); {adata.n_vars} genes remained after "
                    f"min_cells>={min_cells}."),
            threshold={"min_genes": int(min_genes), "min_cells": int(min_cells),
                       "max_pct_mito": float(max_pct_mito)},
            observed={"n_spots_initial": int(n_spots_initial), "n_spots_after": int(n_spots_after),
                      "n_genes_after": int(adata.n_vars)},
            suggestion=("Retry with a lower --min-genes suited to this dataset's per-spot gene counts "
                        "(an explicit user decision; defaults are unchanged)."),
            stage="visium_spot_filter",
        )

    # --- 3. Normalize ---
    print("\n3. Normalizing (target sum + log1p)...")
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)

    # Store normalized counts for SVG computation later
    adata.layers['normalized'] = adata.X.copy()

    # --- 4. HVG selection ---
    print(f"\n4. Selecting {n_top_genes} highly variable genes...")
    sc.pp.highly_variable_genes(
        adata, n_top_genes=n_top_genes, flavor='seurat_v3',
        layer='counts'
    )
    n_hvgs = adata.var['highly_variable'].sum()
    print(f"   Highly variable genes: {n_hvgs}")

    # --- 5. PCA + Neighbors + UMAP + Leiden ---
    print("\n5. Dimensionality reduction and clustering...")
    sc.pp.scale(adata, max_value=10)
    sc.tl.pca(adata, n_comps=n_pcs, svd_solver='arpack')
    sc.pp.neighbors(adata, n_neighbors=n_neighbors, n_pcs=n_pcs)
    sc.tl.umap(adata)
    sc.tl.leiden(adata, resolution=resolution, key_added='leiden')

    n_clusters = adata.obs['leiden'].nunique()
    print(f"   PCA: {n_pcs} components")
    print(f"   Leiden clusters: {n_clusters} (resolution={resolution})")

    # --- 6. Spatial neighbors ---
    print("\n6. Building spatial neighbors graph...")
    sq.gr.spatial_neighbors(adata, coord_type='grid', n_neighs=6)
    n_edges = adata.obsp['spatial_connectivities'].nnz
    print(f"   Spatial graph: {n_edges} edges")

    # --- 7. Spatially variable genes (Moran's I) ---
    print(f"\n7. Computing spatially variable genes (Moran's I, n_perms={svgs_n_perms})...")

    # Restore normalized expression for SVG computation (scale modifies X)
    adata.X = adata.layers['normalized'].copy()

    sq.gr.spatial_autocorr(
        adata,
        mode='moran',
        n_perms=svgs_n_perms,
        n_jobs=svgs_n_jobs
    )

    # Extract significant SVGs
    moranI = adata.uns['moranI'].copy()
    svgs = moranI[moranI['pval_norm_fdr_bh'] < 0.05].sort_values('I', ascending=False)
    n_svgs = len(svgs)
    print(f"   Spatially variable genes (FDR < 0.05): {n_svgs}")
    if n_svgs > 0:
        top5 = svgs.index[:5].tolist()
        print(f"   Top SVGs: {', '.join(top5)}")

    # Store SVG results
    adata.uns['svg_results'] = svgs

    # --- 8. Neighborhood enrichment ---
    print("\n8. Computing neighborhood enrichment...")
    sq.gr.nhood_enrichment(adata, cluster_key='leiden')
    print(f"   Enrichment matrix: {n_clusters}x{n_clusters}")

    # --- 9. Co-occurrence ---
    print("\n9. Computing co-occurrence scores...")
    sq.gr.co_occurrence(adata, cluster_key='leiden')
    print(f"   Co-occurrence computed for {n_clusters} clusters")

    # --- Store analysis parameters ---
    adata.uns['spatial_analysis_params'] = {
        'min_genes': min_genes,
        'min_cells': min_cells,
        'max_pct_mito': max_pct_mito,
        'n_top_genes': n_top_genes,
        'n_pcs': n_pcs,
        'resolution': resolution,
        'n_neighbors': n_neighbors,
        'svgs_n_perms': svgs_n_perms,
        'n_spots_initial': int(n_spots_initial),
        'n_spots_after': int(n_spots_after),
        'n_clusters': int(n_clusters),
        'n_svgs': int(n_svgs),
        'mitochondrial_qc': mitochondrial_qc,
    }
    adata.uns['mitochondrial_qc'] = mitochondrial_qc

    print("\n" + "=" * 50)
    print("✓ Spatial analysis completed successfully!")
    print("=" * 50)
    print(f"\n  Spots analyzed: {n_spots_after}")
    print(f"  Clusters found: {n_clusters}")
    print(f"  SVGs identified: {n_svgs}")

    return adata
