"""
Predict cell fate probabilities using CellRank.

Usage:
  from scripts.cellfate_prediction import cellfate_prediction
  adata_regvelo = cellfate_prediction(adata_trained, rgv_model, cluster_key, TERMINAL_STATES)
"""

import matplotlib.pyplot as plt
import numpy as np
import torch

import cellrank as cr
import regvelo as rgv
from regvelo import REGVELOVI
import scanpy as sc
import scvelo as scv

def cellfate_prediction(
    adata,
    rgv_model,
    cluster_key,
    TERMINAL_STATES,
    combine_kernel=False
):
    """
    Predict cell fates on a user dataset using CellRank.

    Trains a REGVELOVI model on the provided AnnData object, computes RNA
    velocity, and estimates macrostate fate probabilities with a GPCCA
    estimator. Optionally combines the velocity kernel with a connectivity
    kernel.

    Parameters
    ----------
    adata : AnnData
        Preprocessed AnnData object with prior GRN stored in adata.uns['skeleton'],
        TF annotation in adata.var['TF'], and moment-smoothed layers 'Ms' and 'Mu'.
    rgv_model : .pt
        Trained regvelo model.
    cluster_key : str
        .obs column name used as cell-type annotation for macrostate computation
        and UMAP colouring.
    TERMINAL_STATES : list of str or None
        Terminal states to pass to the GPCCA estimator. If None, terminal
        states are inferred automatically from the macrostate decomposition.
    combine_kernel : bool, optional
        If True, combines the VelocityKernel (80%) with a ConnectivityKernel
        (20%) before computing macrostates. Default is False.

    Returns
    -------
    adata : AnnData
        Input AnnData updated in-place with velocity outputs, transition
        matrices, and fate probability estimates stored by CellRank.
        Written to 'adata_regvelo.h5ad'.
    regvelo_model/
        Trained REGVELOVI model weights and configuration.
    vae : REGVELOVI
        Trained RegVelo model. Saved to the 'regvelo_model/' directory.

    Saves
    -----
    cellfate_probability.svg
        Per-lineage fate probability UMAPs.
    commitment_score.svg
        UMAP coloured by commitment score.
    adata_regvelo.h5ad
        AnnData with all velocity and fate outputs written to disk.
    """
    
    # Global plot settings
    scv.settings.verbosity = 3
    cr.settings.verbosity = 2
    plt.rcParams["svg.fonttype"] = "none"
    scv.settings.set_figure_params(
        "scvelo", dpi=80, transparent=True, fontsize=14, color_map="viridis"
    )
    
    vk = cr.kernels.VelocityKernel(adata).compute_transition_matrix()
    vk.write_to_adata()
    vk.plot_projection()

    if combine_kernel:
        ck = cr.kernels.ConnectivityKernel(adata)
        ck.compute_transition_matrix()
        combined_kernel = 0.8 * vk + 0.2 * ck
        ck.plot_projection()
        ck.write_to_adata()
        estimator = cr.estimators.GPCCA(combined_kernel)
    else:
        estimator = cr.estimators.GPCCA(vk)

    estimator.compute_macrostates(n_states=10, cluster_key=cluster_key)
    estimator.plot_macrostates(which="all", legend_loc="right", size=100)

    if TERMINAL_STATES:
        estimator.set_terminal_states(TERMINAL_STATES)
        estimator.plot_macrostates(which="terminal", legend_loc="right", size=100)
    else:
        pass

    estimator.compute_fate_probabilities(tol=1e-5)
    estimator.plot_fate_probabilities(same_plot=False, basis="umap")

    plt.savefig("cellfate_probability.svg", format="svg", bbox_inches="tight")
    plt.close()

    rgv.pl.commitment_score(
        adata=adata,
        lineage_key="lineages_fwd",
        frameon=False,
        s=40,
        cmap="coolwarm",
        title="Commitment score",
    )

    plt.savefig("commitment_score.svg", format="svg", bbox_inches="tight")
    plt.close()

    adata.write_h5ad("adata_regvelo.h5ad")

    print("✅ Cell fate prediction complete")

    return adata
