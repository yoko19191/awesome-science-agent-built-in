"""
Visualise the single-cell effect of a TF knock-out on fate probabilities.

Usage:
  from scripts.single_cell_tf_perturbation import single_cell_tf_perturbation
  single_cell_tf_perturbation(adata, adata_perturb_dict, TF, TERMINAL_STATES)
"""

import matplotlib.pyplot as plt

import regvelo as rgv
import scanpy as sc


def single_cell_tf_perturbation(
    adata,
    adata_perturb_dict,
    TF,
    TERMINAL_STATES,
):
    """
    Compute and visualise the single-cell perturbation effect of a TF knock-out.

    Retrieves the perturbed AnnData for the TF of interest from adata_perturb_dict,
    computes per-cell fate probability shifts relative to the baseline using
    rgv.tl.perturbation_effect, and saves one UMAP per terminal state coloured
    by the perturbation effect magnitude.

    Parameters
    ----------
    adata : AnnData
        Baseline AnnData returned by cellfate_prediction, containing velocity
        and fate probability outputs.
    adata_perturb_dict : dict of str -> AnnData
        Dictionary mapping TF names to perturbed AnnData objects, as returned
        by tf_perturbation.
    TF : str
        Name of the TF to visualise. Must be a key in adata_perturb_dict.
    TERMINAL_STATES : list of str
        Terminal state labels. One UMAP is produced per entry.

    Returns
    -------
    None
        Results are saved to disk; the function does not return a value.

    Saves
    -----
    single_cell_perturbation_{TF}_{terminal_state}.svg
        One UMAP per terminal state coloured by the single-cell perturbation effect.
    """

    if TF not in adata_perturb_dict:
        raise KeyError(
            f"'{TF}' not found in adata_perturb_dict. "
            f"Available TFs: {list(adata_perturb_dict.keys())}"
        )

    adata_perturb = adata_perturb_dict[TF]

    adata = rgv.tl.perturbation_effect(
        adata_perturb=adata_perturb,
        adata=adata,
        terminal_state=TERMINAL_STATES,
    )

    for i, terminal_state in enumerate(TERMINAL_STATES):
        ax = sc.pl.umap(
            adata,
            color=f"perturbation effect on {terminal_state}",
            frameon=False,
            vcenter=0,
            cmap="vlag",
            show=False,
        )

        _ = ax.set_title(TF, style="italic")

        plt.tight_layout()
        plt.savefig(
            f"single_cell_perturbation_{TF}_{terminal_state}.svg",
            dpi=300,
            bbox_inches="tight",
        )
        plt.close()

    print(f"✅ Single-cell TF perturbation plots saved for {TF}")
