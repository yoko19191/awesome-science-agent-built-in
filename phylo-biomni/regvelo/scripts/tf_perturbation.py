"""
Run population-level in silico TF knock-out and compute perturbed fate probabilities.

Usage:
  from scripts.tf_perturbation import tf_perturbation
  adata, adata_perturb_dict = tf_perturbation(adata_regvelo, rgv_model, cluster_key, tf_ko_list, TERMINAL_STATES)
"""

import matplotlib.pyplot as plt

import cellrank as cr
import regvelo as rgv

#TERMINAL_STATES = ["mNC_head_mesenchymal","mNC_arch2","mNC_hox34","Pigment"]

def tf_perturbation(
    adata,
    rgv_model,
    cluster_key,
    tf_ko_list,
    TERMINAL_STATES
):
    """
    Run population-level in silico TF knock-out and compute perturbed cell fate
    probabilities for each TF.

    For each TF in tf_ko_list (or all TFs in adata if none are provided), the
    function simulates a block of TF activity, recomputes the CellRank velocity
    kernel and GPCCA macrostates, and collects the resulting fate probability
    shifts. Results are summarised in a perturbation barplot and saved to disk.

    Parameters
    ----------
    adata : AnnData
        AnnData produced by cellfate_prediction, containing velocity outputs,
        terminal state annotations in adata.obs['term_states_fwd'], and TF
        annotation in adata.var['TF'].
    rgv_model : REGVELOVI
        Trained RegVelo model returned by cellfate_prediction.
    cluster_key : str
        .obs column name used as cell-type annotation for macrostate computation.
    tf_ko_list : list of str or None
        TFs to knock out. If None, all TFs annotated in adata.var['TF'] are used.
    TERMINAL_STATES : list of str
        Terminal state labels matching values in adata.obs['term_states_fwd'].

    Returns
    -------
    adata : AnnData
        Unchanged baseline AnnData (returned for convenience in downstream steps).
    adata_perturb_dict : dict of str -> AnnData
        Dictionary mapping each TF name to its perturbed AnnData object,
        containing updated velocity and fate probability fields added by CellRank.

    Saves
    -----
    perturbation_out.csv
        Per-TF fate probability shift table produced by rgv.mt.cellfate_perturbation.
    perturbation_barplot.svg
        Barplot summarising fate probability changes across all knocked-out TFs.
    """

    if tf_ko_list:
        TF_candidate = tf_ko_list
    else:
        TF_candidate = list(adata.var_names[adata.var["TF"]])

    MODEL = rgv_model
    adata_perturb_dict = {}

    #=================================================
    # STEP 1 - RUN TF PERTURBATION
    #=================================================

    for TF in TF_candidate:
        adata_target_perturb, reg_vae_perturb = rgv.tl.in_silico_block_simulation(model=MODEL,
            adata=adata,
            TF=TF,
            cutoff=0
            )
        
        adata_perturb_dict[TF] = adata_target_perturb

    # Build per-terminal-state cell index map from baseline annotations
    ct_indices = {
        ct: adata.obs["term_states_fwd"][adata.obs["term_states_fwd"] == ct].index.tolist()
        for ct in TERMINAL_STATES}

    #==================================================
    # STEP 2 - COMPUTE PERTURBED TRANSITION PROBABILITY
    #==================================================

    for TF, adata_target_perturb in adata_perturb_dict.items():
        vkp = cr.kernels.VelocityKernel(adata_target_perturb).compute_transition_matrix()
        estimator = cr.estimators.GPCCA(vkp)
        estimator.compute_macrostates(n_states=10, cluster_key=cluster_key)
        estimator.set_terminal_states(ct_indices)
        estimator.compute_fate_probabilities()

        adata_perturb_dict[TF] = adata_target_perturb

    df = rgv.mt.cellfate_perturbation(
        perturbed=adata_perturb_dict,
        baseline=adata,
        terminal_state=TERMINAL_STATES,
    )

    df.to_csv("perturbation_out.csv")

    rgv.pl.cellfate_perturbation(
        adata=adata,
        df=df,
        fontsize=14,
        figsize=(8, 4),
        legend_loc="center left",
        legend_bbox=(1.02, 0.5),
        color_label=cluster_key,
    )

    plt.savefig("perturbation_barplot.svg", format="svg", bbox_inches="tight")
    plt.close()

    print("✅ TF perturbation complete")

    return adata, adata_perturb_dict
