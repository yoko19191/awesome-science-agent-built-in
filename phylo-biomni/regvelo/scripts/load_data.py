"""
Load user scRNA-seq dataset and define custom genes.

Usage:
  from scripts.load_data import load_user_data
  adata, valid_tf, valid_marker_genes = load_user_data(adata_path, tf_list, marker_genes)
  
"""

import anndata as ad
import pandas as pd


def load_user_data(
    adata_path,
    tf_list=None,
    marker_genes=None
):
    """
    Load user-provided adata and gene lists.

    Parameters
    ----------
    adata_path : str
        Path to .h5ad file.
    tf_list : list of str, optional
        Set of transcription factor gene names to maintain in analysis.
        Genes not present in adata.var_names will be skipped with a warning.
    marker_genes : list of str, optional
        Set of marker gene names to plot.
        Genes not present in adata.var_names will be skipped with a warning.

    Returns
    -------
    adata : AnnData
        Loaded AnnData object with validated spliced/unspliced layers.

    """

    #=================================================
    # STEP 1 - LOAD USER DATA
    #=================================================

    adata = ad.read_h5ad(adata_path)

    has_velocity = "spliced" in adata.layers and "unspliced" in adata.layers

    if has_velocity:
        print("Unspliced/spliced layers detected")
    else:
        raise ValueError(
            "No velocity layers detected in AnnData. "
            "Expected both 'spliced' and 'unspliced' layers."
        )

    #=================================================
    # STEP 2 - VALIDATE CUSTOM TFs AND MARKER GENES
    #=================================================

    valid_tf = None

    if tf_list:
        if len(tf_list) > 50:
            print(f"WARNING: Addition of too many non-variable TFs can lead to poor perturbation results!")
                  
        missing_tf = [g for g in tf_list if g not in adata.var_names]
        valid_tf = [g for g in tf_list if g in adata.var_names]

        if missing_tf:
            print(f"WARNING: TF genes not found in adata and will be skipped: {missing_tf}")
        if not valid_tf:
            raise ValueError("None of the provided TF genes were found in adata.var_names.")

        print(f"Using custom TFs: {valid_tf}")

    valid_marker_genes = None

    if marker_genes:
        missing = [g for g in marker_genes if g not in adata.var_names]
        valid_marker_genes = [g for g in marker_genes if g in adata.var_names]

        if missing:
            print(f"WARNING: marker genes not found in adata and will be skipped: {missing}")
        if not valid_marker_genes:
            raise ValueError("None of the provided marker genes were found in adata.var_names.")

        print(f"Using marker genes: {valid_marker_genes}")

    print("✅ Data loaded successfully")

    return adata, valid_tf, valid_marker_genes
