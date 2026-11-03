# In Silico Perturbation Guide (RegVelo)

## Overview

RegVelo is a generative model that couples cellular dynamics with a gene regulatory network (GRN). For a **trained** RegVelo model, you can run *in silico counterfactual* perturbations: knock out a transcription factor (TF) by removing all of its regulatory effects, regenerate the perturbed RNA velocity field, and quantify how cell-fate decisions shift relative to the unperturbed baseline. This predicts which terminal states are depleted (or enriched) when a regulator is lost, turning the trained model into a hypothesis generator for TF function in lineage specification.

Two complementary readouts of a perturbation are used:
- **(a) Long-term effect** — change in CellRank cell-fate probabilities (the *depletion likelihood*), described here.
- **(b) Short-term effect** — stochastic Markov simulation of cell transitions (see `markov_simulation_guide.md`).

## Perturbation Concepts

### Blocking a TF (`rgv.tl.in_silico_block_simulation`)
- Removes a target TF's regulatory effects from the trained model and returns a perturbed AnnData whose `layers["velocity"]` holds the perturbed velocity field, plus the perturbed model object.
- `cutoff=0` fully blocks the TF's regulatory output (a complete knockout).
- Operates on the **saved baseline model** (a path string), so the original trained weights are reused for every TF.

```python
import regvelo as rgv

MODEL = "regvelo_model"          # path to the trained REGVELOVI model
TF_candidate = ["Mitf", "Sox10", "Pou4f1", "Neurod1"]

adata_perturb_dict = {}
for TF in TF_candidate:
    adata_perturb, reg_vae_perturb = rgv.tl.in_silico_block_simulation(
        model=MODEL, adata=adata_baseline, TF=TF, cutoff=0
    )
    adata_perturb_dict[TF] = adata_perturb
```

### Re-estimating fate on the perturbed field
- The perturbed velocity is fed back through the **same** CellRank recipe used for the baseline (VelocityKernel → GPCCA → macrostates → terminal states → fate probabilities). Processing parameters (`n_states`, `cluster_key`, kernel weights) must match the baseline.
- To avoid recomputing terminal states each time, pass the baseline terminal-state indices (`ct_indices`) directly to the scoring function.

### Depletion likelihood (long-term score)
- For terminal state *k*, the **depletion likelihood** is the normalized Mann-Whitney *U* statistic comparing baseline vs. perturbed fate probabilities:

  l_d = (1 / N_c²) · Σ_n Σ_n* 𝕀( Π[:,k](n) > Π*[:,k](n*) )

  where Π and Π* are the control and perturbed fate-probability matrices.
- **l_d > 0.5 ⇒ long-term depletion** of that terminal state under the knockout. Because each cell's fate probabilities sum to ~1, depleting one fate can *enrich* others (redistribution).
- Scoring method is selectable: `method="likelihood"` (default, normalized Mann-Whitney *U*) or `method="t-statistics"` (t-test).

## Analysis Pipeline

1. **Train and save the RegVelo model** (`vae.train()`, `vae.save(MODEL)`), then write velocity into the baseline AnnData with `rgv.tl.set_output(...)`.
2. **Compute baseline fate** with CellRank (VelocityKernel → GPCCA → `set_terminal_states` → `compute_fate_probabilities`); this populates `adata.obs["term_states_fwd"]`.
3. **Block each TF** with `rgv.tl.in_silico_block_simulation(model=MODEL, adata=..., TF=..., cutoff=0)` → collect perturbed AnnData objects.
4. **Re-estimate perturbed fate** per TF using the same kernel/GPCCA settings (reuse `ct_indices`).
5. **Score the shift** with `rgv.mt.cellfate_perturbation(perturbed=adata_perturb_dict, baseline=adata, terminal_state=TERMINAL_STATES)`.
6. **Visualize** the per-TF, per-terminal-state depletion with `rgv.pl.cellfate_perturbation(...)`.
7. *(Optional)* **Single-cell effect** for one TF via `rgv.tl.perturbation_effect(...)`.
8. *(Optional)* **Gene-expression prediction** under KO via `vae.rgv_expression_fit(...)`.
9. *(Optional)* **End-to-end screen** of TF combinations via `rgv.tl.TFscreening(...)`.

> **Tip:** if terminal-state indices are already defined, score directly on them by passing `terminal_indices=ct_indices` to `rgv.mt.cellfate_perturbation` (instead of `terminal_state=TERMINAL_STATES`).

### Single-cell perturbation effect (optional)
`rgv.tl.perturbation_effect` computes, per cell, the change in fate probability toward each terminal state (perturbed − baseline). **Negative scores = decreased probability** of reaching that fate. Results are written to `adata.obs["perturbation effect on <terminal state>"]`.

### Gene-expression prediction under perturbation (optional, human limb)
The selected model can predict spliced/unspliced expression in control vs. knockout to inspect downstream genes. Fit baseline expression with `vae.rgv_expression_fit(...)`, block the TF with `rgv.tl.in_silico_block_simulation`, fit the perturbed expression, then compare representative genes (e.g. `MYOG`, `MYH3`, `TNNT1`) between control and the in silico knockdown.

### End-to-end screening (optional, zebrafish)
`rgv.tl.TFscreening` runs the full block→re-estimate→score loop across a list of TFs. `n_repeats` (e.g. `5`) retrains and aggregates across runs for robustness (default: one run).

## Key Outputs

### Per-TF depletion table (`rgv.mt.cellfate_perturbation`)
Long-form DataFrame with one row per (TF, terminal state):

| Column | Meaning |
|--------|---------|
| `Depletion likelihood` | Normalized Mann-Whitney *U*; **> 0.5 = depleted** in the KO |
| `p-value` | Significance of the fate shift |
| `FDR adjusted p-value` | Multiple-testing-corrected p-value |
| `Terminal state` | Terminal cell type being scored |
| `TF` | Knocked-out transcription factor |

### Single-cell effect columns
- `adata.obs["perturbation effect on <terminal state>"]` — per-cell, signed fate-probability change (negative = decreased commitment to that fate).

## Interpreting Perturbation Results

### Depletion likelihood
- **> 0.5 with low FDR** → knocking out the TF depletes that terminal state (the TF promotes that fate). Example results: *Mitf* KO depletes Melanocyte cells; *Sox10* KO depletes Myelinating Schwann cells; *Neurod1* KO depletes Enteric neurons and Otic sensory neurons; *Pou4f1* KO depletes Dorsal root ganglion / Otic sensory neurons. In human limb, *MSC* KO depletes PAX7+MyoProg1 (loss of progenitor identity) while *MYOG* KO depletes the differentiated MYH3+MyoC and MYL3+MyoC fates.
- Values near 0.5 with high p-values → no meaningful long-term effect.
- Remember redistribution: depleting one fate can apparently enrich another because fate probabilities are row-normalized.

### Single-cell maps
UMAPs of `perturbation effect on <state>` (e.g. `cmap="vlag"`, `vcenter=0`) reveal *where* in the trajectory the KO redirects cells — typically the transient/progenitor cells feeding the affected terminal state.

### Validate against known biology
Treat predictions as hypotheses; cross-check against literature for the TF (e.g. Mitf↔melanocytes, Sox10↔peripheral glia, Pou4f1/Brn3a↔sensory neurons, Neurod1↔neuronal differentiation, MSC↔muscle stem-cell maintenance, MYOG↔terminal myogenic differentiation).

## Common Issues

| Issue | Solution |
|-------|----------|
| `term_states_fwd` missing / KeyError on terminal states | Run the baseline cell-fate step (set terminal states) **before** perturbation; pass concrete `TERMINAL_STATES`, not `None` |
| Perturbed and baseline scores not comparable | Use identical CellRank settings (`n_states`, `cluster_key`, kernel weights) for baseline and every perturbed object |
| `n_states` warning about complex eigenvalues | Choose an `n_states` not flagged by GPCCA (it otherwise bumps `n_states` by 1) |
| Model path not found in `in_silico_block_simulation` | Pass the directory where `vae.save(...)` wrote the model; ensure `model.pt` exists |
| Implausible KO predictions | Predictions are conditioned on the prior GRN — a poor/organism-mismatched GRN propagates into KO results; use a matched GRN and validate experimentally |
| TF not in screen results | Confirm the TF is annotated in `adata.var["TF"]` and present in `adata.var_names` |

## Version Compatibility

- **Function names (code vs. prose):** the runnable API is `rgv.mt.cellfate_perturbation` and `rgv.pl.cellfate_perturbation`. Some tutorial *prose* refers to these as `depletion_score` (`rgv.mt.depletion_score` / `rgv.pl.depletion_score`); the executed cells use `cellfate_perturbation`. Use `cellfate_perturbation` in code.
- `rgv.tl.in_silico_block_simulation` accepts `cutoff=0` for a full knockout; the human limb gene-expression example uses the `effects=0` argument form.
- CellRank 2.0+ API throughout (`cr.kernels.VelocityKernel`, `cr.estimators.GPCCA`); GPCCA needs the `petsc4py`/`slepc4py` extras.
- RegVelo 0.4.2 perturbation API: `rgv.tl.in_silico_block_simulation`, `rgv.tl.perturbation_effect`, `rgv.tl.TFscreening`, `rgv.mt.cellfate_perturbation`, `rgv.pl.cellfate_perturbation`, plus `REGVELOVI.rgv_expression_fit` for expression prediction.
