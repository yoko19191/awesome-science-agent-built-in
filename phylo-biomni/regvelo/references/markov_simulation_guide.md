# Markov Simulation Guide (RegVelo)

## Overview

The Markov density simulation captures the **short-term** effect of an in silico TF perturbation. Instead of comparing long-run absorption probabilities (the depletion likelihood; see `perturbation_guide.md`), it simulates progenitor cells taking a finite number of stochastic steps over the velocity-based transition matrix, in both the control and perturbed systems, and compares how often each terminal cell is reached. The difference in visit counts (a *density-difference* score) quantifies whether a knockout depletes or enriches flow into each terminal state on a short time horizon.

## Simulation Concepts

### Transition matrices (control vs. perturbed)
- Built with CellRank from the baseline and perturbed velocity fields. The dense transition matrix is passed to the simulator as `kernel.transition_matrix.A`.
- Either a plain `VelocityKernel` (zebrafish) or a combined `0.8 * VelocityKernel + 0.2 * ConnectivityKernel` (human limb) can be used — match the recipe used for fate estimation.

### Start and terminal indices
- `start_indices` — row indices of the **progenitor / starting** cells (e.g. zebrafish `NPB_nohox`, human limb `PAX3+MyoProg`), obtained by matching the starting cell type(s) in `adata.obs["cell_type"]`.
- `terminal_indices` — row indices of cells in the terminal states, obtained by matching `TERMINAL_STATES` in `adata.obs["term_states_fwd"]`.

### Random-walk methods
`rgv.tl.markov_density_simulation` runs with `method="stepwise"` (default) or `method="one-step"`:
- **stepwise** — simulates transitions one step at a time; each trajectory terminates upon reaching a terminal cell or after `n_steps`, whichever comes first.
- **one-step** — computes the cell-state distribution after `n_steps` and samples directly from it, without modeling intermediate transitions.

### Key parameters
- `n_steps` (default **100**) — number of random-walk steps. Choose it by checking the proportion of control trajectories that reach a terminal cell.
- `n_simulations` (default **1000**) — number of simulations per starting cell.

## Analysis Pipeline

1. **Obtain perturbed AnnData** for the TF of interest via `rgv.tl.in_silico_block_simulation(..., cutoff=0)` (see `perturbation_guide.md`).
2. **Build transition matrices** for control and perturbed systems with CellRank; extract dense matrices (`.transition_matrix.A`).
3. **Define** `start_indices` and `terminal_indices`.
4. **Simulate control** — `total_simulations = rgv.tl.markov_density_simulation(adata, vkt, start_indices, terminal_indices, TERMINAL_STATES, method=method)`.
5. **Simulate perturbed** — `rgv.tl.markov_density_simulation(adata_perturb, vkt_p, start_indices, terminal_indices, TERMINAL_STATES, method=method)`.
6. **Score the difference** — `dd_score, dd_sig = rgv.tl.simulated_visit_diff(adata, adata_perturb, TERMINAL_STATES)` (mean visit-count difference + paired-t-test p-values).
7. **Visualize** — `rgv.pl.simulated_visit_diff(adata, adata_perturb, TERMINAL_STATES, total_simulations, title=...)` for the per-cell density-difference scatter, plus the bundled visit-distribution boxplot `plot_visits_dist_screen` (multi-TF screen) from `scripts/markov_simulation_screen.py`. See **Visit-distribution boxplots** under Interpreting Markov Results for its exact outputs.

> **Combined-kernel variant (human limb):** build `combined_kernel = 0.8 * VelocityKernel + 0.2 * ConnectivityKernel` for both systems and pass `combined_kernel.transition_matrix.A` to `markov_density_simulation` instead of the plain velocity-kernel matrix.

## Key Outputs

### Per-cell visit columns (written by `markov_density_simulation`)
| Field | Meaning |
|-------|---------|
| `adata.obs["visits"]` | Absolute number of times each terminal cell was visited |
| `adata.obs["visits_dens"]` | Relative (density-normalized) visit count per terminal cell |
| `adata.obs["visits_diff"]` | Sigmoid-scaled difference (perturbed − control), scaled by 1/√(#simulations) |
| `adata.obs["visits_diff_smooth"]` | `visits_diff` smoothed over neighboring cells for a spatially coherent signal |

### Density-difference score (`rgv.tl.simulated_visit_diff`)
- `dd_score` — list of mean visit-count differences (perturbed − control), **one value per terminal state**. **Negative = depletion** of visits under the knockout; positive = enrichment.
- `dd_sig` — list of paired-t-test p-values (`scipy.stats.ttest_rel`), one per terminal state.

### Result tables (written by `markov_density_screening` with `save=True`)
| File | Contents |
|------|----------|
| `markov_dd_score_by_TF.csv` | Per-TF density-difference scores, significance values, and baseline/perturbed absorption rates |
| `markov_visits_by_TF.csv` | Per-cell visit counts, densities, and differences for each TF |
| `markov_screen_perturbation_rate.csv` | Long-form per-terminal-state visit differences with significance annotation for every TF |

### Figures (displayed by `plot_visits_dist_screen`)
| Output | When |
|--------|------|
| On-screen boxplot via `plt.show()` (one figure per terminal state) | Multi-TF screen — the function does not save to file; save figures explicitly in the submitted script |

## Interpreting Markov Results

### Density-difference plot
`rgv.pl.simulated_visit_diff` colors each terminal cell by `visits_diff`. Because the raw difference is passed through a sigmoid after the 1/√(#simulations) scaling, **values below 0.5 indicate relative depletion** of visits to that terminal cell under perturbation; values above 0.5 indicate enrichment.

### Visit-distribution boxplots (bundled functions — use these, not custom plots)

The skill ships one plotting helper (`plot_visits_dist_screen`) plus the `markov_density_screening` entry point in `scripts/markov_simulation_screen.py`. **Always call these instead of writing custom Markov plotting code.** Both use `mplscience` styling and the `SIGNIFICANCE_PALETTE` defined locally in the script (`n.s.` → `#dedede`, `*` → `#90BAAD`, `**` → `#A1E5AB`, `***` → `#ADF6B1`).

#### Multi-TF combined boxplot — `plot_visits_dist_screen(adata, terminal_states, candidate_list, tick_range, sig_to_keep=["n.s.","*","**","***"], figsize=None, key="markov_density_screening")`
A **vertical** boxplot producing **one figure per terminal state**. Within each figure, TFs are on the x-axis (`Factor`), density-change-likelihood on the y-axis (`Value`), and `hue="significance"` colors boxes by significance level. TFs are ordered by median `visits_diff` (ascending). Only TFs whose significance label is in `sig_to_keep` (default `["n.s.", "*", "**", "***"]`, i.e. every factor is kept) are shown. The y-axis is centered at 0.5 with half-width `tick_range` (required, no default). If `figsize` is `None`, the width adapts to the number of factors shown (clamped to 5–25 inches). The function calls `plt.show()` and does not save to file — save figures explicitly in the submitted script.

**Required DataFrame schema.** `plot_visits_dist_screen` reads the long-form `df_all` stored in `adata.uns[key]["screen_perturbation_rate"]`, built inside `markov_density_screening(...)` from `visits_diff_per_tf(...)` calls and the `res_table` significance values, with columns:
- `Value` — per-cell `visits_diff` (float)
- `Group` — terminal state label
- `Factor` — TF name
- `significance` — label from `rgv.mt.get_significance(p_value)` (`*` / `**` / `***` / `n.s.`)

Do not reconstruct this DataFrame by hand; it is assembled by `markov_density_screening(...)` and stored in `adata.uns["markov_density_screening"]`.

#### Interpretation
Because `visits_diff` is sigmoid-scaled around 0.5, **values below 0.5 indicate relative depletion** of visits to that terminal cell under the knockout; values above 0.5 indicate enrichment. Worked example (zebrafish `nr2f5` KO): all four terminal states show negative `dd_score` with significant p-values, i.e. broad short-term depletion; `elf1` KO shows a different, more mixed pattern.

### stepwise vs. one-step
Run both to confirm robustness. `stepwise` reflects trajectories that may be absorbed early; `one-step` reflects the bulk distribution after `n_steps`. Consistent signs across methods strengthen the conclusion.

## Common Issues

| Issue | Solution |
|-------|----------|
| Few trajectories reach terminal cells | Increase `n_steps` (default 100); pick it from the proportion of control trajectories that reach a terminal cell |
| Noisy / unstable `visits_diff` | Increase `n_simulations` (default 1000) |
| Stale results when reusing the same `adata` across TFs | Delete prior columns before the next run: `del adata.obs['visits'], adata.obs['visits_dens'], adata.obs['visits_diff'], adata.obs['visits_diff_smooth']` |
| Mismatch between perturbed and baseline annotations | Copy the cluster labels onto the perturbed object: `adata_perturb.obs["cell_type"] = adata.obs["cell_type"].copy()` |
| Transition matrix shape error | Pass the dense matrix `kernel.transition_matrix.A`, not the kernel object |
| Kernel recipe differs from fate estimation | Use the same kernel (plain VelocityKernel or 0.8 vk + 0.2 ck) as the baseline cell-fate step |

## Version Compatibility

- **Function names (code vs. prose):** the runnable API is `rgv.tl.simulated_visit_diff` and `rgv.pl.simulated_visit_diff`. Some tutorial *prose* calls these `rgv.tl.simulated_density_diff` / `rgv.pl.simulated_density_diff`; the executed cells use the `simulated_visit_diff` names. Use `simulated_visit_diff` in code.
- `rgv.tl.markov_density_simulation` signature used in tutorials: `markov_density_simulation(adata, transition_matrix, start_indices, terminal_indices, terminal_states, method=...)` with `n_steps` (default 100) and `n_simulations` (default 1000) as additional knobs.
- The skill wrapper exposes a `markov_density_screening(...)` helper with `method` (default `"stepwise"`), `n_step_to_use` (default 500), `n_simulations` (default 1000), `seed` (default 0), `output_dir`, `plot`, and `save` parameters; the raw tutorial API uses `n_steps`/`n_simulations` directly.
- Significance helper: `rgv.mt.get_significance(p_value)` → `*`/`**`/`***`.
- CellRank 2.0+ kernels (`cr.kernels.VelocityKernel`, `cr.kernels.ConnectivityKernel`) supply the transition matrices.
