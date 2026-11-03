# Model Training Guide (RegVelo)

## Overview

Step 2 of the RegVelo workflow trains the `REGVELOVI` model — a Bayesian deep
generative model of single-cell splicing dynamics that couples gene dynamics
through a gene regulatory network (GRN) [1, 2]. Unlike classical RNA velocity
models that fit a set of decoupled, single-gene closed-form ODEs, RegVelo solves
a single, high-dimensional, GRN-coupled ODE system and learns its kinetic and
neural-network parameters by stochastic variational inference [1, 2].

This guide documents **every tunable parameter** of the model (the constructor
`REGVELOVI(...)`, the AnnData registration step `setup_anndata(...)`, and the
training routine `train(...)`) and provides a **self-contained mathematical
description** of what those parameters control. It is written to stand on its own:
you do not need to read any other file to understand the parameters. For extended
narrative on perturbation inference and the broader generative framing, see the
companion `references/model_details.md`; for what happens *after* training (kernels,
macrostates, fate probabilities) see `references/cellrank_guide.md`.

The training call used by this skill is in `scripts/model_training.py`
(`train_regvelo` and `model_comparison`); the **"Defaults used by this skill"**
section below lists exactly what those wrappers pass.

## Model Formulation

This section is self-contained and written at the depth needed to understand and
choose every tunable parameter. Equations follow the RegVelo paper [1] and the
`model_details.md` summary [2].

### Splicing dynamics (the coupled ODE)

For every gene *g*, RegVelo models the unspliced (*u*) and spliced (*s*) abundances
with the standard splicing-kinetics ODE pair, but does **not** assume a constant
transcription rate [1, 2]:

$$
\begin{aligned}
\frac{\mathrm{d}\,u_{g}(t)}{\mathrm{d}t} &= \alpha_{g}(t) - \beta_{g}\, u_{g}(t), \\[4pt]
\frac{\mathrm{d}\,s_{g}(t)}{\mathrm{d}t} &= \beta_{g}\, u_{g}(t) - \gamma_{g}\, s_{g}(t),
\end{aligned}
$$

where:

- $\alpha_{g}(t)$ — **time- and cell-dependent transcription rate** (this is where the GRN enters; see below),
- $\beta_{g}$ — **splicing rate** (constant per gene),
- $\gamma_{g}$ — **degradation rate** (constant per gene).

Classical models treat each gene independently with an analytic solution. RegVelo
instead couples all genes through $\alpha_{g}(t)$, so there is no closed-form
solution; it integrates the **whole system at once** with a parallelizable ODE
solver [1, 2]. The kinetic parameters $\beta_g$, $\gamma_g$ (and an initial
transcriptional activation $\alpha_1$) are recovered after training via
`REGVELOVI.get_rates()` [3].

### GRN-coupled transcription

The transcription rate is produced by a shallow neural network whose linear core is
the GRN weight matrix $W$ [1, 2]:

$$
\alpha_{g} = h\!\left( \left[\, W\, s(t) + b \,\right]_{g} \right)
$$

where:

- $W$ — the **GRN weight matrix** (`W` constructor argument), with **rows = target genes, columns = regulators (TFs)**. Each entry is the effect of a regulator on a target: **positive = activation, negative = repression, zero = no regulatory edge** [1].
- $s(t)$ — the spliced abundances (the regulators' expression drives their targets' transcription),
- $b$ — a **base transcription** bias term (neural-network bias), the regulation-independent component of transcription [1, 2],
- $h$ — a **nonlinear activation function** [1, 2].

Because $\alpha_g$ depends on the spliced state $s(t)$ of *other* genes through $W$,
the genes' ODEs are coupled into one high-dimensional system. During training the
entries of $W$ are updated, so RegVelo both **refines** the supplied prior edges and
can **learn additional** regulatory interactions in a data-driven way [1].

### Variational inference framing (conceptual)

RegVelo is a VAE-style generative model [1, 2]:

1. An **encoder** network maps each cell's $(u, s)$ to the posterior parameters of a
   low-dimensional **latent cell representation** [1, 2].
2. A **decoder** network maps samples of that representation to a **gene-wise latent
   time** for each cell (as in veloVI) [1, 2, 4].
3. The ODE above is integrated forward to predict $\hat{u}_g, \hat{s}_g$; these
   predictions are taken as the **mean of a Gaussian likelihood** over the observed
   smoothed abundances, and **all** neural-network and kinetic parameters are
   optimized **simultaneously by stochastic variational inference** with
   gradient-based updates [1, 2].

After optimization, **RNA velocity** is defined as the spliced (splicing) velocity:

$$
v_{g} \;=\; \frac{\mathrm{d}\,s_{g}(t)}{\mathrm{d}t} \;=\; \beta_{g}\, u_{g}(t) - \gamma_{g}\, s_{g}(t),
$$

computed from the estimated rates and predicted abundances [1, 2]. Because the model
is Bayesian, you can **sample velocities from the learned posterior**, which yields
*intrinsic* uncertainty (cell-state-change uncertainty along the manifold) and
*extrinsic* uncertainty (uncertainty in the future cell state) [1, 4]. These are
named here only to motivate `n_samples` in the output step; their derivation is out
of scope for this guide.

### Constraint modes and regularization (what `soft_constraint`, `lam`, `lam2` do)

GRN inference faces two problems: the prior GRN may be **wrong/incomplete**, and real
regulatory networks are **sparse** [1]. RegVelo addresses both with a prior penalty
and an optional sparsity penalty, which the constructor exposes as
`soft_constraint`, `lam` ($\lambda_1$), and `lam2` ($\lambda_2$) [1, 3].

- **Soft constraint (`soft_constraint=True`, default).** The prior GRN is imposed as
  a **penalty** that pulls the learned $W$ toward the prior, weighted by
  $\lambda_1$ (`lam`). Conceptually the objective adds a term

  $$
  \mathcal{L} \;=\; \underbrace{-\,\mathbb{E}_q\!\big[\log p(u,s \mid \cdot)\big] + \mathrm{KL}}_{\text{ELBO (reconstruction + latent regularization)}} \;+\; \lambda_1\, \Omega_{\text{prior}}(W, W_{\text{prior}}) \;+\; \lambda_2\, \big\lVert J \big\rVert_{1},
  $$

  where $\Omega_{\text{prior}}$ penalizes departure from the prior network. Edges
  **absent** from the prior can still be learned, and prior edges can be reweighted —
  the prior is guidance, not a hard mask [1].
  - $\lambda_1 < 1$: GRN inference is **robust to a corrupted prior** (performance is
    largely insensitive to prior errors) [1].
  - $\lambda_1 = 1$ (**default**): chosen to balance prior influence against
    inference robustness [1].
  - $\lambda_1 = 10$: the model **over-trusts the prior** and becomes sensitive to
    prior corruption [1].

- **Hard constraint (`soft_constraint=False`).** The prior GRN is imposed as a
  **fixed structural mask**: only the regulatory edges present in the prior are
  allowed (zeros stay zero); the model cannot add new edges. Use this when you trust
  the prior topology and want to restrict learning to edge weights [1].

- **Soft-regularized / "soft-r" (`soft_constraint=True` with `lam2 > 0`).** A soft
  constraint **plus** an **L1 penalty on the Jacobian** $J$ of the dynamical system,
  weighted by $\lambda_2$ (`lam2`). The Jacobian-L1 term imposes **sparsity and
  smoothness of the dynamics**, which improved cell-state-transition predictions in
  the paper's benchmarks (sparser NeuralODE-style dynamics) [1].
  - $\lambda_2 = 0$ (**default**): no Jacobian regularization (plain soft constraint) [1].
  - $\lambda_2 < 1$: GRN inference remains generally stable; the optimal value is
    dataset-dependent [1].

> **Mapping to the skill's three named modes.** The skill's `model_comparison`
> compares `'soft'`, `'hard'`, and `'soft_regularized'` [2]. These correspond to:
> `soft` = `soft_constraint=True, lam2=0`; `hard` = `soft_constraint=False`;
> `soft_regularized` = `soft_constraint=True, lam2>0` (the skill uses `lam2=1.0`).
> The paper's Figure benchmarks label these **Soft**, **Hard**, and **Soft-r**, and
> selected **Soft-r** as best on the FUCCI cell-cycle dataset by agreement with known
> stages [1].

### How the math maps to constructor arguments

| Math object | Constructor argument | Meaning |
|-------------|----------------------|---------|
| $W$ (prior GRN, targets × regulators) | `W` | GRN skeleton/weights; rows = targets, columns = regulators |
| set of regulators (TFs) | `regulators` | which genes act as upstream factors |
| prior-as-penalty vs prior-as-mask | `soft_constraint` | `True` = soft penalty, `False` = hard mask |
| $\lambda_1$ (prior-penalty weight) | `lam` | strength of prior-GRN incorporation |
| $\lambda_2$ (Jacobian-L1 weight) | `lam2` | strength of sparsity/smoothness regularization |
| $\beta_g$, $\gamma_g$, $\alpha_1$ | learned (read via `get_rates()`) | splicing/degradation/initial-activation rates |

## Constructor Parameters — `REGVELOVI(...)`

Signature [3]:

```python
REGVELOVI(adata, W=None, regulators=None, soft_constraint=True, lam=1, lam2=0, **model_kwargs)
```

| Parameter | Type | Default | Commonly tuned? | Description / guidance |
|-----------|------|---------|-----------------|------------------------|
| `adata` | `AnnData` | — (required) | n/a | AnnData already registered via `setup_anndata()` (must have the spliced/unspliced layers registered) [3]. |
| `W` | `Tensor`, shape `[n_targets, n_regulators]` | `None` | Set per dataset | The GRN prior as a weight matrix with **rows = targets, columns = regulators** [3]. In this skill, built from `adata.uns["skeleton"]` (which is `targets × regulators` after the preprocessing transpose) and passed as an int tensor [2]. If `None`, no structural prior is supplied (a fully connected network can be used to infer the GRN unbiasedly, as in the paper's FUCCI analysis) [1]. |
| `regulators` | `list` | `None` | Set per dataset | List of transcription factors (the genes allowed to act as regulators) [3]. In this skill it is `adata.var_names[adata.var["TF"]]` [2]. |
| `soft_constraint` | `bool` | `True` | **Yes** | `True` = prior GRN as a **soft penalty** (edges can be added/reweighted); `False` = prior GRN as a **hard mask** (only prior edges allowed) [1, 3]. See *Constraint modes* above. |
| `lam` ($\lambda_1$) | `float` | `1` | **Yes** | Strength of prior-GRN incorporation [3]. Default `1` balances prior influence vs robustness; `<1` is robust to a corrupted prior; `10` over-trusts the prior [1]. |
| `lam2` ($\lambda_2$) | `float` | `0` | **Yes (for soft-r)** | Strength of **L1 regularization on the Jacobian** (sparsity/smoothness of dynamics) [3]. Default `0` = off (plain soft constraint). Set `>0` for the **soft-regularized** mode (skill uses `1.0`); stable for `<1`, optimum is dataset-dependent [1]. |
| `**model_kwargs` | — | — | Rarely | Extra keyword arguments forwarded to the underlying `VELOVAE` module (e.g. network width/architecture knobs) [3]. Leave at default unless you have a specific architectural reason. |

> **Prior GRN orientation reminder.** `W` must be **targets (rows) × regulators
> (columns)**. The skill's preprocessing produces `adata.uns["skeleton"]` in this
> orientation; `scripts/model_training.py` then converts it to an int tensor before
> instantiating the model [2]. A transposed `W` silently scrambles the regulatory
> structure — see *Common Issues*.

## AnnData Registration — `setup_anndata(...)`

Signature [3]:

```python
REGVELOVI.setup_anndata(adata, spliced_layer=None, unspliced_layer=None, **kwargs)
```

| Parameter | Type | Default | Commonly tuned? | Description / guidance |
|-----------|------|---------|-----------------|------------------------|
| `adata` | `AnnData` | — (required) | n/a | AnnData with spliced and unspliced layers [3]. |
| `spliced_layer` | `str` | `None` | Set per dataset | Name of the layer holding **spliced normalized** expression [3]. This skill uses the first-moment-smoothed layer `"Ms"` [2]. |
| `unspliced_layer` | `str` | `None` | Set per dataset | Name of the layer holding **unspliced normalized** expression [3]. This skill uses `"Mu"` [2]. |
| `**kwargs` | — | — | Rarely | Forwarded to the `AnnDataManager` [3]. |

> **Why `Ms`/`Mu`?** RegVelo (like veloVI/scVelo) trains on abundances **smoothed by
> first-order moments over the kNN graph** (`scv.pp.moments` → `Ms`, `Mu`), not raw
> counts [1, 2]. Register the smoothed layers, not `spliced`/`unspliced` raw counts.

## Training Parameters — `train(...)`

Signature [3]:

```python
REGVELOVI.train(max_epochs=1500, lr=0.01, weight_decay=1e-05, eps=1e-16,
                train_size=0.9, batch_size=None, validation_size=None,
                early_stopping=True, gradient_clip_val=10, plan_kwargs=None,
                optimizer='AdamW', **trainer_kwargs)
```

| Parameter | Type | Default | Commonly tuned? | Description / guidance |
|-----------|------|---------|-----------------|------------------------|
| `max_epochs` | `int` | `1500` | **Yes** | Maximum training epochs [3]. With `early_stopping=True` the run usually stops earlier. Increase if the loss is still falling at the cap; reduce for quick tests. |
| `lr` | `float` | `0.01` | Sometimes | Optimizer learning rate [3]. Lower (e.g. `5e-3`) if training is unstable/diverging; the default is well-tested. |
| `weight_decay` | `float` | `1e-05` | Rarely | L2 weight-decay (regularization) coefficient for the optimizer [3]. |
| `eps` | `float` | `1e-16` | Rarely | Numerical-stability epsilon for the optimizer [3]. Leave at default. |
| `train_size` | `float` (0–1) | `0.9` | Rarely | Fraction of cells used for training [3]. The remainder (with `validation_size`) is held out for validation/early stopping. |
| `batch_size` | `int` or `None` | `None` | **Yes (memory)** | Minibatch size [3]. `None` = **full dataset per step**. On large datasets this can exhaust GPU memory — set an explicit batch size (e.g. `1024`) if you hit OOM. |
| `validation_size` | `float` or `None` | `None` | Rarely | Validation fraction [3]. If `None`, defaults to `1 - train_size`; if `train_size + validation_size < 1`, the remainder is a test set. |
| `early_stopping` | `bool` | `True` | Sometimes | Stop when validation loss stops improving [3]. Keep `True` for most runs; set `False` only if you want a fixed number of epochs. |
| `gradient_clip_val` | `float` | `10` | Rarely | Maximum gradient value (gradient clipping) during backprop [3]. Lower it if you see exploding gradients/NaNs. |
| `plan_kwargs` | `dict` or `None` | `None` | Rarely | Extra arguments for the modified SCVI `TrainingPlan` (advanced) [3]. |
| `optimizer` | `str` | `'AdamW'` | Rarely | Optimizer name [3]. Default `AdamW`; change only with a specific reason. |
| `**trainer_kwargs` | — | — | Sometimes | Extra arguments forwarded to the SCVI `TrainRunner` (e.g. `accelerator`, `devices`, `max_time`) [3]. Use these to pick CPU/GPU or cap wall-time. |

> **GPU vs CPU.** Training is a PyTorch/scvi-tools optimization; a **GPU is
> recommended**. CPU works for small datasets (~1k–5k cells) but is substantially
> slower, and runtime scales with cell and gene count (see the skill's *Compute*
> note). Use `accelerator`/`devices` via `**trainer_kwargs` to target hardware.

## Defaults Used by This Skill

`scripts/model_training.py` deliberately runs RegVelo at its **published defaults**
(soft constraint, $\lambda_1 = 1$, $\lambda_2 = 0$) [1, 2]. Concretely,
`train_regvelo` does:

```python
REGVELOVI.setup_anndata(adata, spliced_layer="Ms", unspliced_layer="Mu")
vae = REGVELOVI(adata, W=W, regulators=TF)   # soft_constraint=True, lam=1, lam2=0
vae.train()                                  # max_epochs=1500, lr=0.01, AdamW, early_stopping=True, ...
vae.save("regvelo_model")
rgv.tl.set_output(adata, vae, n_samples=30, batch_size=adata.n_obs)
```

- `W` is `adata.uns["skeleton"]` cast to an int tensor (targets × regulators) [2].
- `TF` is `adata.var_names[adata.var["TF"]]` [2].
- `set_output(..., n_samples=30, batch_size=adata.n_obs)` — draws **30 posterior
  samples** to estimate velocity/latent time/uncertainty, processing all cells in one
  batch [2, 3]. Raise `n_samples` for smoother uncertainty estimates at higher cost;
  lower `batch_size` here too if `n_obs` is large and memory is tight.

The optional `model_comparison` path uses:

```python
comp = ModelComparison(adata=adata, terminal_states=TERMINAL_STATES, n_states=8)
comp.train(model_list=['soft', 'hard', 'soft_regularized'], lam2=[1.0], n_repeat=3)
comp.evaluate(side_information='Real_Time', side_key=time_key)
```

- `n_states=8` — number of GPCCA macrostates used when scoring each candidate
  model's fate predictions [2].
- `lam2=[1.0]` — the $\lambda_2$ value used for the `soft_regularized` (soft-r) mode [2].
- `n_repeat=3` — three independent retrainings per mode, so you can judge both the
  **prediction score** and **run-to-run variability** [2].

## Choosing a Constraint Mode and Regularization

Use this when you must pick `soft_constraint`/`lam`/`lam2` (or interpret
`model_comparison` output):

1. **Start with the defaults** — `soft_constraint=True`, `lam=1`, `lam2=0`. These are
   the paper's defaults and the skill's `train_regvelo` setting; they are robust
   across a range of prior-GRN corruption levels [1, 2].
2. **If you have a trusted prior topology and want to restrict learning to edge
   weights** → try `hard` (`soft_constraint=False`).
3. **If cell-state-transition predictions look noisy / the GRN looks too dense** →
   try **soft-r** (`lam2 > 0`, e.g. `1.0`); the Jacobian-L1 penalty sparsifies the
   dynamics and improved transition predictions in the benchmarks [1].
4. **If you suspect the prior is unreliable** → keep $\lambda_1 \le 1$ (lower values
   are more robust to a corrupted prior); **avoid $\lambda_1 = 10$**, which over-trusts
   the prior [1].
5. **If real developmental time is available** → run the Step 2 `model_comparison`
   across `soft`/`hard`/`soft_regularized` with `n_repeat=3` and pick the mode with
   the **best prediction score** and the **least run-to-run variability** (the paper
   selected soft-r on the FUCCI dataset by agreement with known stages) [1, 2].

## Key Outputs of Training

Training fills the model object and (via `rgv.tl.set_output`) writes results back
into the AnnData:

- `adata.layers["velocity"]` — the regulatory-informed spliced velocity
  $v_g = \beta_g u_g - \gamma_g s_g$ (consumed by CellRank downstream) [1, 2].
- **Latent time** — gene/cell-specific latent time from the decoder
  (`get_latent_time()`) [3].
- **Kinetic rates** — `get_rates()` returns `{"beta", "gamma", "alpha_1"}` per gene
  (splicing rate, degradation rate, initial transcriptional activation) [3].
- **Uncertainty** — posterior sampling (`n_samples` in `set_output`) yields
  intrinsic/extrinsic velocity uncertainty [1, 4].
- **Inferred GRN** — the learned $W$ (extract with `rgv.tl.inferred_grn(adata, vae)`),
  which both refines prior edges and may add new ones [1].
- **Saved model** — `vae.save("regvelo_model")` writes a model directory reused by
  the in silico perturbation steps [2].

## Common Issues

| Issue | Solution |
|-------|----------|
| `W` orientation wrong (regulators ↔ targets swapped) | Pass `W` as **targets (rows) × regulators (columns)**; in this skill use `adata.uns["skeleton"]` after preprocessing (the script transposes the loaded prior) and cast to a tensor [2, 3]. |
| Out-of-memory during `train()` | Default `batch_size=None` uses the full dataset per step; set an explicit `batch_size` (e.g. `1024`). Also lower `set_output(batch_size=...)` from `adata.n_obs` [3]. |
| Out-of-memory in `set_output` on large datasets | Reduce `batch_size` and/or `n_samples` (default 30) [2, 3]. |
| Training does not converge / loss still falling at cap | Increase `max_epochs` (default 1500); keep `early_stopping=True` so good runs still stop early [3]. |
| Exploding gradients / NaNs | Lower `lr` (default 0.01) and/or `gradient_clip_val` (default 10) [3]. |
| Implausible / prior-driven GRN | The result is conditioned on the prior; lower `lam` ($\lambda_1 \le 1$) for robustness to a corrupted prior, or supply a better matched prior. Avoid `lam=10` [1]. |
| GRN too dense / noisy dynamics | Switch to **soft-r** with `lam2 > 0` to sparsify via the Jacobian-L1 penalty [1]. |
| Registered the wrong layers | `setup_anndata` must use the **smoothed** layers (`spliced_layer="Ms"`, `unspliced_layer="Mu"`), not raw `spliced`/`unspliced` [1, 2]. |
| Downstream `compute_macrostates` errors after training | Cell-fate steps need the `petsc4py`/`slepc4py` extras — that is a CellRank/GPCCA requirement, not a training parameter (see `cellrank_guide.md`). |

## Version Compatibility

- RegVelo **0.4.2** `REGVELOVI` API: constructor
  `REGVELOVI(adata, W=None, regulators=None, soft_constraint=True, lam=1, lam2=0, **model_kwargs)`
  and `train(max_epochs=1500, lr=0.01, weight_decay=1e-05, eps=1e-16, train_size=0.9, batch_size=None, validation_size=None, early_stopping=True, gradient_clip_val=10, plan_kwargs=None, optimizer='AdamW', **trainer_kwargs)` [3].
- Training backend is **scvi-tools / PyTorch** (a modified SCVI `TrainingPlan` and
  `TrainRunner`); the routine is adapted from veloVI [3, 4]. Default optimizer is
  `AdamW`.
- `setup_anndata` registers the spliced/unspliced layers; this skill uses the
  moment-smoothed `Ms`/`Mu` layers produced in preprocessing [2].
- Output extraction uses `rgv.tl.set_output` (wrapping the model's
  `add_regvelo_outputs_to_adata`) with `n_samples`/`batch_size` [2, 3].
- Model selection uses `rgv.ModelComparison` with `model_list=['soft','hard','soft_regularized']`,
  `lam2`, `n_repeat`, and a GPCCA `n_states`; evaluation against a real-time key [2].
- Companion guides: `references/model_details.md` (generative framing + perturbation),
  `references/cellrank_guide.md` (post-training fate mapping),
  `references/perturbation_guide.md`, `references/markov_simulation_guide.md`.

---

## References

1. Wang W, Hu Z, Weiler P, Mayes S, Lange M, Wang J, Xue Z, Sauka-Spengler T, Theis FJ. *RegVelo: gene-regulatory-informed dynamics of single cells.* Cell (2026). DOI: [10.1016/j.cell.2026.04.022](https://doi.org/10.1016/j.cell.2026.04.022). Full text: https://www.cell.com/cell/fulltext/S0092-8674(26)00457-5
2. RegVelo skill — `references/model_details.md` and `scripts/model_training.py` (RegVelo v0.4.2 workflow defaults).
3. RegVelo API reference — `regvelo.REGVELOVI` (constructor, `setup_anndata`, `train`, `get_rates`, `get_velocity`, `get_latent_time`, `save`/`load`). https://regvelo.readthedocs.io/en/latest/api/genapi/regvelo.REGVELOVI.html
4. Gayoso A, Weiler P, Lotfollahi M, Klein D, Hong J, Streets A, Theis FJ, Yosef N. *Deep generative modeling of transcriptional dynamics for RNA velocity analysis in single cells (veloVI).* Nature Methods 21, 50–59 (2024).
