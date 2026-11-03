# CellRank Guide (RegVelo)

## Overview

In RegVelo, CellRank converts the **regulatory-informed RNA velocity field** inferred by a trained RegVelo model (`REGVELOVI`) into cell fate probabilities. After the velocity field is written into the AnnData with `rgv.tl.set_output`, CellRank builds a transition matrix from that velocity, identifies terminal states via GPCCA, and computes per-cell absorption (fate) probabilities toward each terminal state. RegVelo then summarizes commitment with an entropy-based commitment score. This is the cell-fate backbone shared by the baseline analysis and every downstream perturbation (in silico TF knockout and Markov simulation).

## Kernels

CellRank uses "kernels" to model cell-state transitions. RegVelo feeds its inferred velocity (`adata.layers["velocity"]`) into these kernels.

### VelocityKernel (primary in RegVelo)
- Uses the RegVelo-inferred RNA velocity to compute directed transition probabilities.
- Captures the directionality of cell-state changes implied by the regulatory velocity field.
- Constructed with `cr.kernels.VelocityKernel(adata).compute_transition_matrix()`.
- Used on its own in the murine and zebrafish tutorials.

### ConnectivityKernel
- Based on the cell-cell similarity (k-NN) graph in `adata.obsp["connectivities"]`.
- Undirected — provides smoothing/robustness.
- Constructed with `cr.kernels.ConnectivityKernel(adata).compute_transition_matrix()`.

### Combined kernel (VelocityKernel + ConnectivityKernel)
- A weighted sum stabilizes the transition matrix: `0.8 * VelocityKernel + 0.2 * ConnectivityKernel`.
- Used in the human limb tutorial; the murine tutorial notes this combination as an option for quantifying fate probabilities.
- **Consistency rule:** whatever kernel recipe is used for the baseline must be reused for perturbed objects (see Common Issues).

## Analysis Pipeline

1. **Write velocity into AnnData** — `rgv.tl.set_output(adata, vae, n_samples=30, batch_size=adata.n_obs)` extracts velocity, latent time, and uncertainty from the trained RegVelo model.
2. **Compute transition matrix** from the chosen kernel(s) — `vk = cr.kernels.VelocityKernel(adata).compute_transition_matrix()` (optionally combined with a ConnectivityKernel).
3. **Estimate macrostates** with GPCCA — `estimator = cr.estimators.GPCCA(vk)`, then `estimator.compute_macrostates(n_states=..., cluster_key=...)`.
4. **Set terminal states** — `estimator.set_terminal_states(TERMINAL_STATES)` using known endpoint cell types (or terminal-state indices).
5. **Compute fate probabilities** — `estimator.compute_fate_probabilities(solver="direct")`; for each cell, the probability of reaching each terminal state.
6. **Score commitment** — `rgv.pl.commitment_score(adata, lineage_key="lineages_fwd", ...)` plots the entropy of each cell's fate-probability distribution.

> **Combined-kernel variant (human limb):** instead of a velocity kernel alone, build a weighted estimator from `0.8 * VelocityKernel + 0.2 * ConnectivityKernel`, then run the same `compute_macrostates` / `set_terminal_states` / `compute_fate_probabilities` steps (the human limb tutorial uses `n_states=8` and `cluster_key="leiden_R_celltype"`).

## Key Outputs

GPCCA writes its results back into the AnnData. The RegVelo workflow relies on these fields downstream.

### Macrostates and terminal states
- `adata.obs["macrostates_fwd"]` — coarse-grained metastable states from GPCCA.
- `adata.obs["term_states_fwd"]` — cells assigned to the chosen terminal states (consumed by perturbation and Markov steps).
- `adata.obs["term_states_fwd_probs"]` — membership probability for the assigned terminal state.

### Fate probabilities
- `adata.obsm["lineages_fwd"]` — per-cell × terminal-state matrix; each row sums to ~1.

| Probability toward a fate | Interpretation |
|---------------------------|----------------|
| > 0.8 | Strongly committed to this fate |
| 0.4 – 0.8 | Partially committed |
| < 0.4 | Uncommitted (progenitor-like) |

### Commitment score
- `adata.obs["commitment_score"]` — entropy of the fate-probability distribution. **Committed cells have a LOW commitment score** (low entropy → mass concentrated on one fate); multipotent/progenitor cells have a high score.

## Interpreting CellRank Results

### Fate probability UMAPs
`estimator.plot_fate_probabilities(same_plot=False)` shows one panel per terminal state; cells are colored by their probability of reaching that fate. Committed lineages light up near their endpoint and fade toward progenitors.

### Commitment score map
A UMAP colored by `commitment_score` (e.g. `cmap="coolwarm"`). Low values mark cells that have resolved their fate; high values mark uncommitted progenitors at branch points.

### Terminal-state choice
Terminal states should correspond to biological endpoints (leaf cell types). In the tutorials they are set explicitly (e.g. murine: Dorsal root ganglion neurons, Enteric neurons, Melanocyte cells, Myelinating Schwann cells, Myelinating Schwann cells (Tgfb2+), Otic sensory neurons; human limb: PAX7+MyoProg1, MYH3+MyoC, MYL3+MyoC) rather than left to auto-detection.

## Common Issues

| Issue | Solution |
|-------|----------|
| `petsc4py`/`slepc4py` import error in `compute_macrostates` | Install the GPCCA extras: `pip install petsc4py slepc4py` |
| "Using `n` components would split a block of complex conjugate eigenvalues" | GPCCA bumps `n_states` by 1; pick an `n_states` value not flagged by the warning (e.g. avoid the exact reported number) |
| Wrong/empty terminal states | Set them explicitly with `set_terminal_states(...)` using known endpoint labels instead of relying on inference |
| Perturbed fate probabilities not comparable to baseline | Use the **same** kernel recipe and `n_states`/`cluster_key` for baseline and perturbed objects |
| All cells appear committed | Include earlier-timepoint progenitors so the trajectory has an uncommitted root |
| Memory error on large datasets | Reduce cells/PCs, or use `solver="direct"` carefully on very large transition matrices |

## Version Compatibility

RegVelo uses the **CellRank 2.0+** API (pulled in by `regvelo`):
- Kernels: `cr.kernels.VelocityKernel`, `cr.kernels.ConnectivityKernel` (not `cr.tl.transition_matrix`).
- Estimator: `cr.estimators.GPCCA` with `compute_macrostates`, `set_terminal_states`, `compute_fate_probabilities` (not `cr.tl.terminal_states`).
- RegVelo wrappers used here: `rgv.tl.set_output` (writes velocity/latent time) and `rgv.pl.commitment_score` (entropy-based commitment).
- GPCCA macrostate computation requires the `petsc4py`/`slepc4py` extras.
