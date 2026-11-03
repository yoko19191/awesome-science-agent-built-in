"""
============================================================================
SHARED DOWNSTREAM (platform-agnostic)
============================================================================

Runs on the STANDARDIZED common spatial object produced by either the Visium or
the Xenium branch. The platform label travels into every output.

Stages (all shared across platforms):
  1. Spatial neighbors graph (grid for Visium; generic kNN for Xenium/imaging)
  2. Spatial neighborhoods / niches (neighbor cell-type composition -> kmeans)
  3. Spatially variable genes (Moran's I via squidpy; SPARK-X optional/not bundled)
  4. Replicate-aware pseudobulk differential expression
       HARD GATE: >= min_reps biological replicates per condition, else BLOCK
       (pseudoreplication is refused, not silently run)
  5. Cell-cell interaction — neighborhood co-localization of cell types, reported
       strictly as HYPOTHESES (no ground truth; no causal claims)

Outputs (all under output_dir): niches.csv, spatially_variable_genes.csv,
differential_expression.csv, cci.csv.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from spatial_common import replicate_summary

# --- Authoritative spatially-variable-gene significance definition -----------------------------
# squidpy's Moran's I output carries TWO FDR columns: the asymptotic-normal ``pval_norm_fdr_bh`` and
# the permutation ``pval_sim_fdr_bh``. They give different counts, so the headline column MUST be
# named, not left implicit. These defaults are the authoritative headline definition and are mirrored
# in skill_contract.json:facts.svg_significance; the export/facts path fails if they disagree or the
# column is absent (no silent zero).
SVG_PVALUE_COLUMN = "pval_norm"
SVG_FDR_COLUMN = "pval_norm_fdr_bh"
SVG_METHOD = "moran_normal_asymptotic"
SVG_FDR_THRESHOLD = 0.05
NICHE_KMEANS_RESTARTS = 5
NICHE_KMEANS_MAX_ITERATIONS = 100
NICHE_SILHOUETTE_MAX_POINTS = 500
NICHE_MAX_CANDIDATES = 10


def adjacency_spec(platform: str) -> tuple[str, str]:
    """Return the valid adjacency entity and label for a standardized platform."""
    if platform == "Visium":
        return "spatial_cluster", "Visium spatial-cluster adjacency"
    return "cell_type", "provisional cell-type adjacency"


def derive_gene_universe(tested_names, available_names,
                         highly_variable_names=()) -> dict:
    """Describe the tested SVG universe from the actual feature-selection state."""
    tested = {str(name) for name in tested_names}
    available = {str(name) for name in available_names}
    highly_variable = {str(name) for name in highly_variable_names}
    if tested == available:
        selection_basis = "all_post_qc_gene_features"
        definition = (
            f"All {len(available)} gene features remaining after platform QC and feature filtering; "
            "the highly-variable flag, when present, is used for embedding/clustering and does not "
            "restrict this Moran's I test."
        )
    elif highly_variable and tested == highly_variable:
        selection_basis = "highly_variable_flag"
        definition = (
            f"The {len(highly_variable)} features marked highly_variable in the analyzed object."
        )
    else:
        selection_basis = "explicit_subset_of_post_qc_features"
        definition = (
            f"The {len(tested)} features present in squidpy's Moran's I result, drawn from "
            f"{len(available)} post-QC gene features."
        )
    return {
        "gene_universe_definition": definition,
        "gene_universe_selection_basis": selection_basis,
        "n_features_available": len(available),
        "n_highly_variable_flagged": len(highly_variable),
    }


def svg_significance_summary(moran: pd.DataFrame,
                            pvalue_column: str = SVG_PVALUE_COLUMN,
                            fdr_column: str = SVG_FDR_COLUMN,
                            method: str = SVG_METHOD,
                            threshold: float = SVG_FDR_THRESHOLD) -> dict:
    """Return the named-column SVG significance summary, or RAISE if the column is absent.

    Refuses an ambiguous or missing headline column rather than silently reporting zero.
    """
    missing = [c for c in (pvalue_column, fdr_column) if c not in moran.columns]
    if missing:
        raise ValueError(
            f"authoritative SVG column(s) {missing} absent from Moran's I output; "
            f"available columns: {list(moran.columns)}. Refusing an ambiguous headline count."
        )
    n_sig = int((pd.to_numeric(moran[fdr_column], errors="coerce") < threshold).sum())
    return {"pvalue_column": pvalue_column, "fdr_column": fdr_column, "method": method,
            "threshold": float(threshold), "n_sig": n_sig, "n_genes_tested": int(len(moran))}


def _bh_fdr(pvals: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg FDR (no statsmodels dependency)."""
    p = np.asarray(pvals, dtype=float)
    n = p.size
    if n == 0:
        return p
    order = np.argsort(p)
    ranked = p[order] * n / (np.arange(n) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty_like(ranked)
    out[order] = np.clip(ranked, 0, 1)
    return out


def build_spatial_graph(adata, platform: str, n_neighs: int = 6):
    """Build the spatial neighbors graph appropriate to the platform."""
    import squidpy as sq
    has_grid = ("array_row" in adata.obs and "array_col" in adata.obs)
    if platform == "Visium" and has_grid:
        sq.gr.spatial_neighbors(adata, coord_type="grid", n_neighs=6)
        graph = "grid(n_neighs=6)"
    else:
        k = int(min(n_neighs if platform != "Visium" else 6, max(2, adata.n_obs - 1)))
        sq.gr.spatial_neighbors(adata, coord_type="generic", n_neighs=k)
        graph = f"generic-knn(n_neighs={k})"
    adata.uns["spatial_graph_type"] = graph
    return graph


def select_niche_count(features: np.ndarray, *,
                       max_niches: int = NICHE_MAX_CANDIDATES,
                       random_state: int = 0) -> tuple[np.ndarray, dict]:
    """Select k by silhouette over the observed neighbor-composition features."""
    x = np.asarray(features, dtype=float)
    if x.ndim != 2 or len(x) == 0:
        raise ValueError("niche features must be a non-empty two-dimensional matrix")
    n_unique = int(len(np.unique(x, axis=0)))
    maximum = min(int(max_niches), n_unique, max(1, len(x) - 1))
    if maximum < 2:
        return np.zeros(len(x), dtype=int), {
            "method": "silhouette_over_neighbor_composition",
            "candidate_k": [1],
            "silhouette_by_k": {"1": None},
            "selected_k": 1,
        }
    scores = {}
    labels_by_k = {}
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    for k in range(2, maximum + 1):
        labels = KMeans(
            n_clusters=k,
            random_state=random_state,
            n_init=NICHE_KMEANS_RESTARTS,
            max_iter=NICHE_KMEANS_MAX_ITERATIONS,
        ).fit_predict(x)
        labels_by_k[k] = labels
        scores[k] = float(silhouette_score(
            x,
            labels,
            sample_size=min(len(x), NICHE_SILHOUETTE_MAX_POINTS),
            random_state=random_state,
        ))
    selected = max(scores, key=lambda candidate: (scores[candidate], -candidate))
    return labels_by_k[selected], {
        "method": "silhouette_over_neighbor_composition",
        "candidate_k": list(range(2, maximum + 1)),
        "silhouette_by_k": {str(k): float(scores[k]) for k in scores},
        "selected_k": int(selected),
    }


def detect_niches(adata, output_dir: str, cluster_key: str = "cell_type",
                  max_niches: int = NICHE_MAX_CANDIDATES, random_state: int = 0):
    """Detect spatial niches with data-driven k over observed neighbor composition."""
    if cluster_key not in adata.obs:
        cluster_key = "leiden"
    conn = adata.obsp["spatial_connectivities"]
    cats = list(pd.Categorical(adata.obs[cluster_key]).categories)
    onehot = pd.get_dummies(adata.obs[cluster_key].astype(str))[[str(c) for c in cats]].to_numpy(float)
    # neighbor composition = row-normalized adjacency @ onehot (+ self)
    comp = (conn @ onehot) + onehot
    rs = comp.sum(axis=1, keepdims=True)
    comp = np.divide(comp, rs, out=np.zeros_like(comp), where=rs > 0)
    labels, selection = select_niche_count(
        comp, max_niches=max_niches, random_state=random_state
    )
    selection["feature_definition"] = (
        f"row-normalized spatial-neighbor composition over observed {cluster_key} categories"
    )
    selection["feature_names"] = [str(category) for category in cats]
    adata.uns["niche_selection"] = selection
    adata.obs["niche"] = pd.Categorical([f"niche_{i}" for i in labels])
    niche_df = pd.DataFrame({
        "cell_id": adata.obs_names,
        "niche": adata.obs["niche"].astype(str).values,
        cluster_key: adata.obs[cluster_key].astype(str).values,
        "platform": adata.obs["platform"].values,
    })
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    niche_df.to_csv(Path(output_dir) / "niches.csv", index=False)
    return niche_df


def spatially_variable_genes(adata, output_dir: str, n_perms: int = 100,
                             n_jobs: int = 1,
                             pvalue_column: str = SVG_PVALUE_COLUMN,
                             fdr_column: str = SVG_FDR_COLUMN,
                             method: str = SVG_METHOD,
                             fdr_threshold: float = SVG_FDR_THRESHOLD):
    """Moran's I spatially variable genes (control features already excluded).

    The headline significance count is taken from the AUTHORITATIVE, named ``fdr_column`` (default the
    asymptotic-normal ``pval_norm_fdr_bh``); the function RAISES if that column is absent rather than
    silently returning zero, and records the column/method into ``adata.uns['svg_significance']``.
    """
    import squidpy as sq
    if "normalized" in adata.layers:
        adata.X = adata.layers["normalized"].copy()
    sq.gr.spatial_autocorr(adata, mode="moran", n_perms=n_perms, n_jobs=n_jobs, seed=0)
    moran = adata.uns["moranI"].copy()
    summary = svg_significance_summary(moran, pvalue_column=pvalue_column, fdr_column=fdr_column,
                                       method=method, threshold=fdr_threshold)
    highly_variable = []
    if "highly_variable" in adata.var:
        highly_variable = [
            str(name) for name, selected in zip(adata.var_names, adata.var["highly_variable"])
            if bool(selected)
        ]
    summary.update(derive_gene_universe(
        moran.index, adata.var_names, highly_variable
    ))
    moran = moran.sort_values("I", ascending=False)
    moran.insert(0, "gene", moran.index)
    moran["platform"] = adata.uns.get("spatial_platform", "unknown")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    moran.to_csv(Path(output_dir) / "spatially_variable_genes.csv", index=False)
    n_sig = summary["n_sig"]
    adata.uns["svg_results"] = moran
    adata.uns["svg_significance"] = summary
    return moran, n_sig


def replicate_aware_de(adata, output_dir: str, sample_key: str = "sample_id",
                       condition_key: str = "condition", min_reps: int = 2):
    """Pseudobulk DE with a biological-replicate gate.

    Aggregates raw counts to per-sample pseudobulk, then tests two conditions
    gene-by-gene (Welch t on log-CPM) with BH-FDR. If fewer than ``min_reps``
    independent samples exist in any condition, DE is BLOCKED (pseudoreplication
    is refused) and a reason is returned.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    rep = replicate_summary(adata, sample_key=sample_key, condition_key=condition_key)
    status = {"stage": "differential_expression", "platform": adata.uns.get("spatial_platform")}

    if not rep["has_condition"]:
        status.update(run=False, reason="No condition labels present; DE not applicable.",
                      **rep)
        pd.DataFrame(columns=["gene", "log2fc", "pval", "fdr", "status"]).to_csv(
            Path(output_dir) / "differential_expression.csv", index=False)
        return None, status

    conditions = sorted(set(adata.obs[condition_key].astype(str)))
    if len(conditions) < 2 or rep["min_reps"] < min_reps:
        status.update(
            run=False,
            reason=(f"Replicate-aware DE requires >= {min_reps} biological replicates per condition "
                    f"and >= 2 conditions; observed {rep['samples_per_condition']}. "
                    f"BLOCKED to avoid pseudoreplication."),
            **rep,
        )
        pd.DataFrame(columns=["gene", "log2fc", "pval", "fdr", "status"]).to_csv(
            Path(output_dir) / "differential_expression.csv", index=False)
        print(f"\u2717 DE BLOCKED: {status['reason']}")
        return None, status

    from scipy import stats
    counts = adata.layers.get("counts", adata.X)
    counts = np.asarray(counts.todense()) if hasattr(counts, "todense") else np.asarray(counts)
    samples = adata.obs[sample_key].astype(str).values
    cond_of = (adata.obs[[sample_key, condition_key]].astype(str)
               .drop_duplicates().set_index(sample_key)[condition_key].to_dict())
    uniq = sorted(set(samples))
    pb = np.vstack([counts[samples == s].sum(axis=0) for s in uniq])          # samples x genes
    libsize = pb.sum(axis=1, keepdims=True)
    logcpm = np.log1p(pb / np.where(libsize > 0, libsize, 1) * 1e4)
    grp = np.array([cond_of[s] for s in uniq])
    a, b = conditions[0], conditions[1]
    Xa, Xb = logcpm[grp == a], logcpm[grp == b]
    tstat, pval = stats.ttest_ind(Xb, Xa, axis=0, equal_var=False)
    pval = np.nan_to_num(pval, nan=1.0)
    log2fc = (Xb.mean(0) - Xa.mean(0)) / np.log(2)
    fdr = _bh_fdr(pval)
    de = pd.DataFrame({
        "gene": list(adata.var_names),
        "log2fc": log2fc, "pval": pval, "fdr": fdr,
        "contrast": f"{b}_vs_{a}", "platform": adata.uns.get("spatial_platform"),
    }).sort_values("fdr")
    de["status"] = "tested"
    de.to_csv(Path(output_dir) / "differential_expression.csv", index=False)
    status.update(run=True, contrast=f"{b}_vs_{a}", n_genes=int(de.shape[0]),
                  n_sig=int((de["fdr"] < 0.05).sum()), **rep)
    print(f"\u2713 DE: {b} vs {a}, {status['n_sig']} genes at FDR<0.05")
    return de, status


def cci_hypotheses(adata, output_dir: str, cluster_key: str = "cell_type"):
    """Hypothesis-level cell-cell interaction from neighborhood co-localization.

    Uses squidpy neighborhood enrichment between cell types. Results are strictly
    HYPOTHESES about which cell types are spatially adjacent (sender/receiver);
    no ligand-receptor causality or directionality is claimed. Offline and
    deterministic (no ligand-receptor database download).
    """
    import squidpy as sq
    if cluster_key not in adata.obs:
        cluster_key = "leiden"
    adata.obs[cluster_key] = adata.obs[cluster_key].astype("category")
    sq.gr.nhood_enrichment(adata, cluster_key=cluster_key, seed=0)
    z = adata.uns[f"{cluster_key}_nhood_enrichment"]["zscore"]
    cats = list(adata.obs[cluster_key].cat.categories)
    relationship_label = adata.uns.get("adjacency_relationship_label", "cell-type adjacency")
    rows = []
    for i, a in enumerate(cats):
        for j, b in enumerate(cats):
            if j <= i:
                continue
            rows.append({"type_a": a, "type_b": b, "enrichment_z": float(z[i, j]),
                         "colocalized": bool(z[i, j] > 2),
                         "platform": adata.uns.get("spatial_platform"),
                         "evidence_level": "hypothesis",
                         "relationship_label": relationship_label,
                         "label_basis": cluster_key})
    cci = pd.DataFrame(rows).sort_values("enrichment_z", ascending=False)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    cci.to_csv(Path(output_dir) / "cci.csv", index=False)
    return cci


# ---------------------------------------------------------------------------
# Platform resolution (position-safe, staged)
# ---------------------------------------------------------------------------
# Canonical assay labels used across the package. Same mapping the router uses
# (platform_router._VALID) and the labels standardize_spatial_object() writes to
# .uns['spatial_platform'] / .obs['platform']. Kept as a local literal so this
# module stays dependency-light and does not import the router.
_CANONICAL_PLATFORMS = {"visium": "Visium", "xenium": "Xenium"}


def _normalize_platform(value) -> str:
    """Map a raw platform value to the package's canonical label.

    Recognized labels are normalized case-insensitively to ``Visium``/``Xenium``;
    an empty/``None`` value becomes the declared ``unknown``; any other explicit
    value is passed through unchanged. Platform is NEVER guessed from a filename.
    """
    if value is None:
        return "unknown"
    label = str(value).strip()
    if not label:
        return "unknown"
    return _CANONICAL_PLATFORMS.get(label.lower(), label)


def _first_platform_obs(obs):
    """Position-safe first value of ``obs['platform']`` or ``None``.

    Returns ``None`` unless the column exists and the Series is non-empty, then
    uses ``.iloc[0]`` (never ``[0]``, which is label-based on a Pandas Series and
    raises ``KeyError: 0`` on a non-zero integer index / empty Series).
    """
    if obs is None:
        return None
    columns = getattr(obs, "columns", None)
    if columns is None or "platform" not in columns:
        return None
    series = obs["platform"]
    if series is None or len(series) == 0:
        return None
    return series.iloc[0]


def _resolve_platform(adata) -> str:
    """Resolve the spatial platform in explicit, position-safe stages.

    1. Prefer a non-empty ``adata.uns['spatial_platform']``.
    2. Otherwise, only if ``adata.obs['platform']`` exists and is non-empty, take
       its first value with position-safe ``.iloc[0]``.
    3. Otherwise, the declared ``unknown`` path.

    The selected value is normalized to the package's canonical labels. Unlike the
    original one-line expression, no fallback that indexes ``obs`` is evaluated
    when ``uns`` already carries a valid platform, so a present ``spatial_platform``
    can never be defeated by a hazardous observation index.
    """
    uns = getattr(adata, "uns", None)
    uns_val = uns.get("spatial_platform") if uns is not None else None
    if uns_val is not None and str(uns_val).strip():
        return _normalize_platform(uns_val)

    obs_val = _first_platform_obs(getattr(adata, "obs", None))
    if obs_val is not None and str(obs_val).strip():
        return _normalize_platform(obs_val)

    return "unknown"


def run_shared_downstream(adata, output_dir: str = "results",
                          cluster_key: str = "cell_type",
                          sample_key: str = "sample_id", condition_key: str = "condition",
                          n_perms: int = 100, min_reps: int = 2):
    """Run the full shared downstream on a standardized object; return a results dict."""
    platform = _resolve_platform(adata)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    graph = build_spatial_graph(adata, platform)
    niches = detect_niches(adata, output_dir, cluster_key=cluster_key)
    svg, n_sig_svg = spatially_variable_genes(adata, output_dir, n_perms=n_perms)
    de, de_status = replicate_aware_de(adata, output_dir, sample_key=sample_key,
                                       condition_key=condition_key, min_reps=min_reps)
    cci = cci_hypotheses(adata, output_dir, cluster_key=cluster_key)
    return {
        "platform": platform,
        "spatial_graph": graph,
        "n_niches": int(pd.Series(niches["niche"]).nunique()),
        "niche_selection": adata.uns.get("niche_selection", {}),
        "n_svg_sig": int(n_sig_svg),
        "svg_significance": adata.uns.get("svg_significance", {}),
        "de_status": de_status,
        "n_cci_colocalized": int(cci["colocalized"].sum()),
        "adjacency_relationship_label": adata.uns.get(
            "adjacency_relationship_label", "cell-type adjacency"
        ),
        "adjacency_label_basis": cluster_key,
        "outputs": ["niches.csv", "spatially_variable_genes.csv",
                    "differential_expression.csv", "cci.csv"],
    }
