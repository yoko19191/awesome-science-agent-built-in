"""
Run scRNA-seq and prior GRN preprocessing.

Usage:
  from scripts.regvelo_preprocess import adata_preprocess
  adata_baseline = adata_preprocess(adata, prior_net, cluster_key, tf_list, marker_genes)
  
"""

import matplotlib.pyplot as plt  
import numpy as np
import pandas as pd
import regvelo as rgv
import scanpy as sc
import scvelo as scv


def adata_preprocess(
    adata,
    prior_net,
    cluster_key,
    tf_list=None,
    marker_genes=None,
    n_hvg=3000,
):
    """
    Run complete preprocessing pipeline for scRNA-seq data and prior GRN integration.

    Parameters
    ----------
    adata : AnnData
        scRNA-seq data with unspliced and spliced counts. If tf_list is provided,
        a raw 'counts' layer must also be present for HVG selection.
    prior_net : pd.DataFrame
        Prior GRN produced with SCENIC+/cellORacle. Rows are target genes,
        columns are TFs (transposed internally before passing to set_prior_grn).
    cluster_key : str
        .obs column name to use as cell-type annotation for UMAP colouring
        and marker gene plots.
    tf_list : list of str, optional
        Set of transcription factors to retain in the analysis. When provided,
        HVG selection is performed using the 'counts' layer (seurat_v3 flavour)
        and restricted to the union of HVGs and provided TFs. When None,
        standard scVelo dispersion-based HVG selection is used.
    marker_genes : list of str, optional
        Set of genes to visualise in a matrixplot after preprocessing.
    n_hvg : int, optional
        Number of highly variable genes to select (default 3000).

    Returns
    -------
    adata : AnnData
        Preprocessed AnnData object with the following additions:

        .uns['skeleton']
            Filtered and updated prior GRN connectivity matrix aligned to
            adata.var_names.
        .var['velocity_genes']
            Boolean column indicating genes selected as velocity genes.
        .var['TF']
            Boolean column indicating genes identified as active transcription
            factors (regulate at least one target in the skeleton).

    Saves
    -----
    adata_preprocessed.svg
        UMAP coloured by cluster_key after scRNA-seq preprocessing.
    adata_velocity.svg
        Side-by-side UMAP and velocity stream plot.
    marker_gene_expression.svg
        Matrixplot of marker gene expression per cluster.
    adata_baseline.h5ad
        Final preprocessed AnnData written to disk.
    """

    #=================================================
    # STEP 1 - scRNA-seq PREPROCESSING
    #=================================================

    hvg_layer = "counts" if "counts" in adata.layers else "spliced"
    print(f"Using '{hvg_layer}' layer raw counts for HVG selection")

    #Keep raw counts for HVG selection
    adata.layers["counts"] = adata.layers[hvg_layer].copy()
    
    scv.pp.filter_genes(adata, min_shared_counts=20)
    scv.pp.normalize_per_cell(adata)

    if tf_list:
        # HVG selection using raw counts layer, then intersect with provided TFs
        sc.pp.highly_variable_genes(adata, n_top_genes=n_hvg, flavor="seurat_v3", layer="counts")
        hvg_set = set(adata.var_names[adata.var["highly_variable"]])
        TF_to_keep = set(tf_list)
        mask = adata.var_names.isin(hvg_set | TF_to_keep)
        adata = adata[:, mask]
        sc.pp.log1p(adata)

    else:
        scv.pp.filter_genes_dispersion(adata, n_top_genes=n_hvg)
        sc.pp.log1p(adata)

    sc.pp.neighbors(adata, n_pcs=30, n_neighbors=50)
    sc.tl.umap(adata)
    scv.pp.moments(adata, n_pcs=None, n_neighbors=None)

    sc.pl.umap(adata, color=cluster_key, show=False)

    plt.tight_layout()
    plt.savefig("adata_preprocessed.svg", dpi=300, bbox_inches="tight")
    plt.close()

    print("✅ scRNA-seq object preprocessed")
    print("Loading prior GRN...")

    #=================================================
    # STEP 2 - ADD PRIOR GRN CONNECTIONS
    #=================================================

    GRN = prior_net

    adata = rgv.pp.set_prior_grn(adata, GRN.T)

    velocity_genes = rgv.pp.preprocess_data(adata.copy()).var_names.tolist()

    # Select TFs that regulate at least one gene
    TF = adata.var_names[adata.uns["skeleton"].sum(1) != 0]
    var_mask = np.union1d(TF, velocity_genes)

    adata = adata[:, var_mask].copy()

    # Update skeleton matrix to match filtered var_names
    adata.uns["skeleton"] = adata.uns["skeleton"].loc[
        adata.var_names.tolist(), adata.var_names.tolist()
    ]

    adata = rgv.pp.filter_genes(adata)

    # Perform min-max scaling
    adata = rgv.pp.preprocess_data(adata, filter_on_r2=False)

    adata.var["velocity_genes"] = adata.var_names.isin(velocity_genes)
    adata.var["TF"] = adata.var_names.isin(TF)

    if marker_genes:
        sc.pl.matrixplot(
            adata,
            marker_genes,
            groupby=cluster_key,
            dendrogram=False,
            swap_axes=True,
            show=False,
        )
        plt.savefig("marker_gene_expression.svg", format="svg", bbox_inches="tight")
        plt.close()

    #=======================================================
    # Summary
    #=======================================================
    print("\n" + "=" * 60)
    print("PREPROCESSING SUMMARY")
    print("=" * 60)
    print(f"Cells after preprocessing: {adata.n_obs:,}")
    print(f"Velocity genes: {adata.var['velocity_genes'].sum()}")
    print(f"Transcription factors: {adata.var['TF'].sum()}")

    adata.write_h5ad("adata_baseline.h5ad")

    print("✅ RegVelo preprocessing complete")

    return adata
