"""
Train RegVelo model or train and compare multiple RegVelo models to select best fit. 

Usage:
    from scripts.model_training import train_regvelo, model_comparison
    adata_regvelo, rgv_model = train_regvelo(adata, cluster_key)
    adata_regvelo, rgv_model = model_comparison(adata, cluster_key, TERMINAL_STATES)
  
"""

# Basic packages
import numpy as np
import pandas as pd
import scipy
import torch
import matplotlib.pyplot as plt
import mplscience
import seaborn as sns
from scipy.stats import ttest_ind
from typing import Dict, List, Tuple
import anndata
from matplotlib.axis import Axis

# RegVelo and related package
import scanpy as sc
import cellrank as cr
import scvi
import scvelo as scv
import regvelo as rgv
from regvelo import REGVELOVI
from regvelo import ModelComparison # Import ModelComparison

def train_regvelo(
    adata,
    cluster_key
):

    """Train RegVelo, plot velocity embedding stream and return trained object.
    
        Parameters
        ----------
        adata: .h5ad
            Preprocessed adata object.
        cluster_key: str
            adata.obs with cell type/stage annotations.
    
        Returns
        -------
        adata_trained: .h5ad
            Trained adata object with velocity estimates. 
        vae: .pt
            RegVelo model. 
    """
    
    # Global plot settings
    scv.settings.verbosity = 3
    cr.settings.verbosity = 2
    plt.rcParams["svg.fonttype"] = "none"
    scv.settings.set_figure_params(
        "scvelo", dpi=80, transparent=True, fontsize=14, color_map="viridis"
    )

    #=================================================
    # STEP 1 - MODEL TRAINING
    #=================================================

    TF = adata.var_names[adata.var["TF"]]

    W = adata.uns["skeleton"].copy()
    W = torch.tensor(np.array(W)).int()
    W = W.T

    sparsity = W.sum() / ((W.sum(1) != 0).sum() * W.shape[0])
    print("Network sparsity: " + str(np.array(sparsity)))

    REGVELOVI.setup_anndata(adata, spliced_layer="Ms", unspliced_layer="Mu")
    vae = REGVELOVI(adata, W=W, regulators=TF)

    vae.train()
    vae.save("regvelo_model")

    rgv.tl.set_output(adata, vae, n_samples=30, batch_size=256)
    scv.tl.velocity_graph(adata)

    scv.pl.velocity_embedding_stream(
        adata,
        basis="umap",
        color=cluster_key,
        legend_loc="right_margin",
        legend_fontsize=0,
        show=False
    )

    plt.tight_layout()
    plt.savefig("adata_velocity.svg", dpi=300, bbox_inches="tight")
    plt.close()

    adata.write_h5ad("adata_trained.h5ad")

    return adata, vae

def add_significance(
        ax: Axis, left: int, right: int, significance: str, level: int = 0, orientation: str = "horizontal", **kwargs
    ) -> None:
        """Add significance bracket to Matplotlib Axis plot.
    
        Parameters
        ----------
        ax
            Matplotlib axis.
        left
            Position of left (top) subplot.
        right
            Position of right (bottom) subplot.
        siginifcance
            Significance ID to plot.
        level
            Level of bracket.
        orientation
            Orientation of bracket.
        **kwargs
            Keyword arguments passed to Axis.plot and to specify bracket level (`bracket_level`) and height
            (`bracket_height`), and distance between text and bracket (`text_height`).
    
        Returns
        -------
        None.
        """
        bracket_level = kwargs.pop("bracket_level", 1)
        bracket_height = kwargs.pop("bracket_height", 0.02)
        text_height = kwargs.pop("text_height", 0.01)
    
        if orientation == "horizontal":
            bottom, top = ax.get_ylim()
        else:
            bottom, top = ax.get_xlim()
        axis_range = top - bottom
    
        bracket_level = (axis_range * 0.07 * level) + top * bracket_level
        bracket_height = bracket_level - (axis_range * bracket_height)
    
        if orientation == "horizontal":
            ax.plot([left, left, right, right], [bracket_height, bracket_level, bracket_level, bracket_height], **kwargs)
            ax.text(
                (left + right) * 0.5,
                bracket_level + (axis_range * text_height),
                significance,
                ha="center",
                va="bottom",
                c="k",
            )
        else:
            ax.plot([bracket_height, bracket_level, bracket_level, bracket_height], [left, left, right, right], **kwargs)
            ax.text(
                bracket_level + (axis_range * text_height),
                (left + right) * 0.5,
                significance,
                va="center",
                ha="left",
                c="k",
                rotation=-90,
            )

    

def model_comparison(
    adata,
    cluster_key,
    time_key,
    TERMINAL_STATES
):
    """Train multiple models and print out results to select the best one. 
    
        Parameters
        ----------
        adata: .h5ad
            Preprocessed adata object.
        cluster_key: str
            adata.obs with cell type/stage annotations.
        time_key: str
            adata.obs with labels for real-time progression. NOTE: Needs to be added prior to analysis.
        TERMINAL_STATES: list
            Cell types that mark endpoints of differentiation trajectory.
    
        Returns
        -------
        adata_trained: .h5ad
            Trained adata object with velocity estimates. 
        vae_sr0: .pt
            Best fit RegVelo model. 
    """
    scvi.settings.seed = 0
    scv.settings.verbosity = 3
    cr.settings.verbosity = 2
    plt.rcParams["svg.fonttype"] = "none"
    scv.settings.set_figure_params("scvelo", dpi=80, transparent=True, fontsize=14, color_map="viridis")

    adata.uns['skeleton'] = adata.uns['skeleton'].astype(np.float32)

    comp = ModelComparison(adata = adata,terminal_states=TERMINAL_STATES, n_states=8)
    comp.train(model_list=['soft','hard','soft_regularized'],
           lam2=[1.0],
           n_repeat=3)

    comp.evaluate(side_information='Real_Time',
              side_key=time_key)
    comp.plot_results(side_information='Real_Time')

    comp.df_Real_Time

    vae_sr0 = comp.MODEL_TRAINED['soft_regularized\nlam2:1.0_0']
    vae_sr0.save('vae_sr0')

    adata.write_h5ad("adata_trained.h5ad")

    return adata, vae_sr0
    




    
    
    

    