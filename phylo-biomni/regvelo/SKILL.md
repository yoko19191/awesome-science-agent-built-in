---
name: regvelo
description: Run RegVelo regulatory RNA velocity, cell-fate, transcription-factor
  perturbation, and Markov simulation analyses through the Phylo/Biomni HPC RegVelo
  tool using bundled workflow scripts, pre-trained murine and zebrafish models, example
  datasets, tutorials, and references. Use when the user asks for RegVelo, regulatory
  RNA velocity, a bundled pre-trained RegVelo model without retraining, gene-regulatory-informed
  single-cell dynamics, prior-GRN integration, CellRank fate probabilities, in silico
  TF knockout, perturbation screening, or RegVelo example data on full-length scRNA-seq
  AnnData with spliced/unspliced layers.
category: transcriptomics
visibility: public
starting-prompt: How do Mitf and Sox10 knockouts affect cell fates in the pretrained
  murine RegVelo model without retraining?
---
# RegVelo — Gene-regulatory-informed dynamics of single cells

RegVelo is an end-to-end framework to infer **gene-regulatory-informed dynamics of single cells** through coupled splicing dynamics. It is a generative model that jointly learns RNA velocity and a gene regulatory network (GRN), using a prior GRN (e.g. from pySCENIC/SCENIC+) as a structural constraint on the velocity field.

This skill wraps the `regvelo` package (v0.4.2); see [References](#references).

## When to use this skill

Use RegVelo when you have a dynamic, **full-length** scRNA-seq dataset **with spliced/unspliced layers** and **a prior GRN** (or the means to generate one via pySCENIC/SCENIC+), and you want to:

- ✅ Order cells along a differentiation trajectory (pseudotime)
- ✅ Identify branching points and terminal cell fates
- ✅ Discover genes driving cell state transitions
- ✅ Visualize RNA velocity (direction of cell state change)
- ✅ Compute cell fate probabilities with CellRank
- ✅ Conduct in silico KO perturbations
- ✅ Visualize gene regulatory networks of transcription factors (see Step 6)

Don't use RegVelo for:

- ❌ scRNA-seq data without spliced/unspliced transcript information
- ❌ Cells that are terminally differentiated with no trajectory (e.g. resting PBMCs)
- ❌ Fewer than 1000 cells

## Purpose

Use the HPC tool `regvelo` as the execution environment. Do not install RegVelo locally. Use the bundled scripts as task-specific building blocks, but submit one coherent `/input/run_regvelo.py` entry point to HPC. Do not run tutorial notebooks unless the user explicitly asks for a tutorial.

The HPC image already contains `regvelo`, `scvi-tools`, `scanpy`, `scvelo`, `cellrank`, `petsc4py`, `slepc4py`, CUDA-enabled PyTorch, `regvelo-healthcheck`, and the optional `regvelo-run` helper.

## Pre-Trained Model Option

Before planning a training job, determine whether the user wants to train a model or use a bundled pre-trained model. If they want fate analysis, TF perturbation, or Markov simulation without retraining, use the matched murine assets below. Do not search for them at `<skill-root>/trained_data`; their exact skill-relative paths are:

- Trained AnnData: `scripts/tutorials/trained_data/adata_murine_trained.h5ad`
- Model checkpoint: `scripts/tutorials/trained_data/regvelo_model_murine/model.pt`
- Model directory to pass to `REGVELOVI.load(...)`: `scripts/tutorials/trained_data/regvelo_model_murine`

Resolve those paths relative to the directory containing this `SKILL.md`, not relative to the current working directory. Confirm both the AnnData file and checkpoint exist before submission. Stage the AnnData as `/input/adata_murine_trained.h5ad` and the checkpoint as `/input/regvelo_model_murine/model.pt`, preserving the model directory name. Local skill paths do not exist inside the HPC container.

The HPC image currently uses RegVelo `0.5.0.dev12`. Because its development API differs from older tutorials, record the installed version and `inspect.signature(...)` for each RegVelo function used before running the full analysis.

Pre-trained run warnings:

- The HPC harness discards all `/output` files on non-zero exit. Catch import and runtime errors, always exit 0, and write status plus traceback to `/output/tables/regvelo_run_summary.json`; judge success from that file.
- `model.pt` requires the matching trained AnnData, and `REGVELOVI.load(...)` takes the directory containing `model.pt`.
- Register `Ms` and `Mu` with `setup_anndata(...)` before loading:

```python
adata = ad.read_h5ad("/input/adata_murine_trained.h5ad")
rgv.REGVELOVI.setup_anndata(adata, spliced_layer="Ms", unspliced_layer="Mu")
model = rgv.REGVELOVI.load("/input/regvelo_model_murine", adata)
```

- Use `solver="direct"` for baseline and TF-knockout CellRank fate probabilities on datasets up to roughly 30,000-50,000 cells; the iterative solver can fail with negative values.
- Try uploading the original 257 MB AnnData first; it usually succeeds, but `input_files` is base64-encoded and can occasionally time out. If it times out, upload a temporary slim copy without `fit_t`, `latent_time_velovi`, stored velocity, or redundant `uns["network"]`; downcast to `float32` CSR but preserve `_scvi_*` UUIDs. Never modify the bundled source.
- Intersect every TF knockout list with truthy `adata.var["TF"]` entries and report unavailable TFs; unsupported knockouts may silently no-op.

Do not construct a new model, train, or rerun output extraction unless required. The matched AnnData contains the prior GRN in `adata.uns["skeleton"]`, TF flags in `adata.var["TF"]`, and fate labels in `adata.obs["celltype_update"]`. Default to `Mitf`, `Sox10`, `Pou4f1`, and `Neurod1`, then intersect with `adata.var["TF"]`.

## GPU and Memory Constraints

Assume the RegVelo HPC tool has one NVIDIA GPU, not a multi-GPU training node. The legacy deployment requests a T4; the `phylo-hpc-tools` deployment places RegVelo on the L4 pool. The current tool definition requests about 8 vCPU and 30 GiB container memory. Always confirm the live `hpc_search_tools("regvelo")` usage in case the deployment changed.

RegVelo/scvi defaults are memory-risky:

- `REGVELOVI.train(batch_size=None)` means the full dataset can be used per step. Do not leave this default for medium or large datasets.
- `rgv.tl.set_output(..., batch_size=adata.n_obs)` runs output extraction over all cells at once. Do not use this on real datasets.
- TF knockout and CellRank recomputation can multiply memory use by the number of TFs or perturbation objects retained.

Default to bounded settings in submitted scripts:

```python
TRAIN_BATCH_SIZE = 512
OUTPUT_BATCH_SIZE = 256
OUTPUT_N_SAMPLES = 20

model.train(
    max_epochs=max_epochs,
    batch_size=TRAIN_BATCH_SIZE,
    accelerator="gpu",
    devices=1,
)
rgv.tl.set_output(
    adata,
    model,
    n_samples=OUTPUT_N_SAMPLES,
    batch_size=OUTPUT_BATCH_SIZE,
)
```

For larger data, lower `TRAIN_BATCH_SIZE` to `256` or `128`, lower `OUTPUT_N_SAMPLES`, and keep output extraction chunked. For small smoke tests, use fewer epochs and a small TF subset first. Do not run an all-TF perturbation screen in one job unless the dataset is already proven to fit; chunk screens into small TF batches and write intermediate tables to `/output/tables`.

## Fit Check

Use RegVelo when the user has or wants:

- Full-length scRNA-seq velocity data in `.h5ad`.
- `adata.layers["spliced"]` and `adata.layers["unspliced"]`, or precomputed `Ms`/`Mu` layers.
- A prior gene regulatory network, preferably matched to the dataset, as `.parquet` or `.csv`.
- Cell type or cluster annotations in `adata.obs`.
- RNA velocity, fate probabilities, regulatory dynamics, or TF knockout predictions.

Do not use RegVelo for droplet data without spliced/unspliced counts, terminal/static populations with no plausible trajectory, very small datasets under roughly 1000 cells, or claims of experimentally validated perturbation effects. Treat in silico TF knockout output as hypotheses.

## Workflow and Clarification

If the request is underspecified, ask Q1 first and by itself:

- Does the user want to train on their own AnnData, use the bundled pre-trained murine model without retraining, or use example/tutorial data?

Branch from Q1:

- If the user has their own data, collect only missing answers from Q2-Q6 before writing the HPC script.
- If the user wants the bundled pre-trained murine model, use the exact paths in **Pre-Trained Model Option**, skip training questions, and ask only for the desired downstream scope, TF list, and terminal states when missing.
- If the user wants example data, offer the bundled datasets listed below. Build a small HPC script around the dataset loaders if the live HPC usage/environment supports downloads; otherwise ask the user to upload the example inputs. Use a tutorial notebook only when the user asks for a tutorial or a notebook-specific reproduction.

For user-provided data, ask these only when not already provided:

- Q2 prerequisites: Does the AnnData have `spliced`/`unspliced` or `Ms`/`Mu` layers? Is a prior GRN available as `.csv` or `.parquet`?
- Q3 analysis scope: validation only, training only, training plus CellRank fate probabilities, TF perturbation screen, or optional Markov/GRN visualization.
- Q4 gene lists: optional TF list for perturbation and optional marker genes for plots.
- Q5 metadata: `cluster_key`, starting population, and optional terminal states.
- Q6 trajectory shape: linear, branching, or unknown.

Do not keep asking questions mid-run. If a choice was not specified, use the defaults below and record them in `/output/tables/regvelo_run_summary.json`.

## Analysis Steps

Build `run_regvelo.py` around the requested subset of these steps:

For the bundled pre-trained branch, skip steps 2-4 and begin with the already-trained AnnData and model described in **Pre-Trained Model Option**.

1. **Input validation**: read AnnData and prior GRN, confirm required layers, confirm `cluster_key`, and fail early on missing biological inputs.
2. **Preprocessing / prior-GRN alignment**: align the GRN to AnnData genes, annotate regulators, preserve velocity-informative genes, and run `REGVELOVI.setup_anndata(...)`.
3. **Model training**: train `REGVELOVI` on one GPU using explicit batch sizes. Save the model and trained AnnData before downstream analysis.
4. **Output extraction / velocity**: run `rgv.tl.set_output(...)` with bounded `n_samples` and `batch_size`; optionally compute velocity graph and UMAP/stream plots.
5. **Cell fate**: if requested, use CellRank-style velocity/connectivity kernels, terminal-state assignment, fate probabilities, and commitment scores.
6. **TF perturbation**: if requested, screen a bounded TF list first. Chunk larger TF screens across jobs and write one table per chunk.
7. **Optional Markov/GRN visualization**: run only after a successful trained model plus fate/perturbation outputs exist. For Markov visit-distribution plots, use the bundled `markov_density_screening(...)` entry point (multi-TF density screening that stores result tables in `adata.uns["markov_density_screening"]`) and `plot_visits_dist_screen(...)` (combined visit-distribution boxplot, one figure per terminal state) from `scripts/markov_simulation_screen.py` rather than agent-developed custom figures. See `references/markov_simulation_guide.md` for their exact outputs and DataFrame schema.

If no prior GRN is available, do not pretend RegVelo can run a proper prior-informed analysis. Offer to stop after validation or propose a separate pySCENIC/SCENIC+/CellOracle GRN-building workflow.

## Bundled Resource Map

Read only the resources needed for the requested branch. Treat scripts as adaptable building blocks rather than a universal pipeline.

### Scripts

- `scripts/run_regvelo_template.py`: preferred safe starting point for a standard HPC training job; adapt its configuration, validation, explicit batching, and `/input`/`/output` paths.
- `scripts/load_data.py`: load user-provided `.h5ad` data and validate spliced/unspliced layers, TFs, and marker genes.
- `scripts/regvelo_preprocess.py`: preprocess counts, compute neighbors/moments, align a prior GRN, retain velocity genes/TFs, and write baseline artifacts.
- `scripts/model_training.py`: train one RegVelo model or compare soft, hard, and soft-regularized models when real-time labels and terminal states are available.
- `scripts/cellfate_prediction.py`: compute CellRank velocity or velocity-plus-connectivity kernels, macrostates, terminal states, fate probabilities, and commitment scores.
- `scripts/tf_perturbation.py`: run population-level TF knockout simulations and compare perturbed fate probabilities.
- `scripts/single_cell_tf_perturbation.py`: calculate and plot per-cell perturbation effects for a selected TF and terminal state.
- `scripts/markov_simulation_screen.py`: run multi-TF Markov density screening (`markov_density_screening`) comparing control and perturbed short-term dynamics with stepwise or one-step simulations, and plot combined visit-distribution boxplots (`plot_visits_dist_screen`).
- `scripts/regvelo_plot.py`: rank TF knockout effects, scan regulons, and plot per-TF GRNs, weights, and expression summaries.

Some modular scripts retain research-workflow defaults. Before HPC submission, adapt them to the bounded training/output settings in `scripts/run_regvelo_template.py`, redirect every artifact to `/output`, and keep CellRank parameters consistent between baseline and perturbed objects.

### Dataset Loaders

The public loader API is exported by `scripts/datasets/__init__.py`; implementations and download URLs live in `scripts/datasets/_datasets.py`.

- `zebrafish_nc()`: preprocessed zebrafish neural crest Smart-seq3 time-course data.
- `zebrafish_grn()`: zebrafish neural crest prior GRN.
- `zebrafish_perturb()`: zebrafish neural crest Perturb-seq data with single and combinatorial TF perturbations.
- `murine_nc(data_type=...)`: mouse neural crest data in `preprocessed`, `normalized`, or `velocyto` form; the preprocessed object includes a pySCENIC GRN in `adata.uns["skeleton"]`.
- `human_limb()`: preprocessed human limb single-cell data.
- `hindbrain(data_type=...)`: human embryonic hindbrain data in `original` or `preprocessed` form.
- `hindbrain_grn()`: human embryonic hindbrain prior GRN.
- `schwann()`: preprocessed mouse Schwann-cell data.

Dataset loaders download remote files when local defaults are absent. Confirm downloads are supported in the live HPC environment and do not assume temporary Google Drive URLs remain valid; otherwise request uploaded inputs.

### Tutorials

- `scripts/tutorials/human_limb_tutorial.ipynb`: human limb RegVelo workflow and developmental dynamics example.
- `scripts/tutorials/murine_preprocess_tutorial.ipynb`: mouse neural crest preprocessing and prior-GRN integration.
- `scripts/tutorials/murine_pySCENIC_tutorial.ipynb`: infer and reshape a pySCENIC GRN for RegVelo.
- `scripts/tutorials/murine_perturbation_tutorial.ipynb`: mouse neural crest training, CellRank fate analysis, population-level TF knockout, and single-cell perturbation effects.
- `scripts/tutorials/zebrafish_perturbation_tutorial.ipynb`: end-to-end zebrafish neural crest dynamics, perturbation screening, depletion likelihood, and Markov simulation.

Tutorials are examples, not production entry points. Read the relevant notebook for dataset-specific choices, then port only the needed steps into the submitted script.

### References

- `references/model_training_guide.md`: model formulation, constructor/setup/training parameters, constraint-mode selection, defaults, outputs, and common issues.
- `references/cellrank_guide.md`: kernel selection, GPCCA analysis, fate outputs, interpretation, and troubleshooting.
- `references/perturbation_guide.md`: TF blocking, perturbed fate estimation, depletion likelihood, optional single-cell effects, and screening.
- `references/markov_simulation_guide.md`: transition matrices, simulation methods/parameters, visit statistics, interpretation, and troubleshooting.
- `references/model_details.md`: concise mathematical model and perturbation background.
- `references/references.bib`: primary bibliography for RegVelo and related methods.

## Decision Defaults

- **TF selection**: use user-provided TFs when available. Otherwise start with a small biologically plausible or ranked subset, not all TFs.
- **Terminal states**: use user-provided terminal states when available. Otherwise infer with CellRank/macrostates and label the result as inferred.
- **Constraint mode**: default to soft prior-GRN constraint with `lam=1.0`. Use hard constraint only when the prior topology is trusted. Use soft regularization only when comparing models or when the user asks for regularized GRN inference.
- **Batching**: start with `train_batch_size=512`, `output_batch_size=256`, `output_n_samples=20`; lower these before reducing biology.
- **Communication**: keep infrastructure details quiet except for material limitations such as missing inputs, GPU rate limits, or OOM mitigation. Report biological progress and results: trajectory structure, fate probabilities, TF effects, and caveats from prior-GRN quality.

## Required Workflow

1. Import the HPC functions in a notebook cell (`NotebookEdit` + `NotebookRun`):

```python
from biomni.tool import hpc_search_tools, hpc_run_tool, hpc_get_job_results, hpc_get_logs
```

2. Search the live registry and read the returned usage:

```python
tools = hpc_search_tools("regvelo regulatory RNA velocity")
print(tools)
```

Use the live `usage` field as the source of truth. It should identify `tool_id="regvelo"` and the script-first command:

```bash
python /input/run_regvelo.py
```

3. Prepare a caller-provided `/input/run_regvelo.py` script. Keep it task-specific and make it write every durable artifact under `/output`.
4. Submit the job:

```python
job = hpc_run_tool(
    tool_id="regvelo",
    command="python /input/run_regvelo.py",
    input_files={
        "run_regvelo.py": "/path/to/run_regvelo.py",
        "adata_input.h5ad": "/path/to/adata_input.h5ad",
        "prior_grn.parquet": "/path/to/prior_grn.parquet",
    },
)
print(job)
```

For large `.h5ad` files, prefer the current platform-supported URL or presigned input path if available in the live HPC usage/API. Avoid base64-uploading large training data when a URL input route exists.

5. After the HPC callback reports completion or failure, call:

```python
results = hpc_get_job_results(job["job_id"])
print(results)
```

Use `hpc_get_logs(job_id)` only for one-time debugging of failed or unclear jobs. Do not poll in a loop.

## Script Construction Rules

The submitted script must:

- Read inputs from `/input`.
- Write outputs under `/output/objects`, `/output/model`, `/output/figures`, and `/output/tables`.
- Validate required layers and `cluster_key` before training.
- Align the prior GRN to AnnData genes before model construction.
- Call `REGVELOVI.setup_anndata(...)` before creating the model when layer registration is needed.
- Train with an explicit `batch_size`; never rely on `batch_size=None` for real datasets.
- Run `rgv.tl.set_output` with explicit `n_samples` and `batch_size`; never process all cells at once unless this is a tiny smoke test.
- Limit TF perturbation screens to a user-provided or ranked subset first, and chunk broader screens across jobs.
- Save the trained AnnData and model even if later plotting or perturbation steps fail.
- Emit a small JSON summary under `/output/tables`.

Use `scripts/run_regvelo_template.py` as a compact starting point for a standard training script. Read and adapt it before submitting; it is intentionally a template, not a universal pipeline.

## Output Expectations

Prefer these output names unless the task requires otherwise:

- `/output/objects/adata_regvelo.h5ad`
- `/output/model/regvelo_model/`
- `/output/tables/regvelo_run_summary.json`
- `/output/tables/*.csv` or `*.parquet` for fate and TF perturbation summaries
- `/output/figures/*.png` or `*.svg` for UMAP, velocity, fate, perturbation, or GRN plots
- `/output/tables/markov_dd_score_by_TF.csv`, `markov_visits_by_TF.csv`, `markov_screen_perturbation_rate.csv` for multi-TF Markov screens (written by `markov_density_screening` with `save=True`)
- `/output/figures/*.png` or `*.svg` for combined visit-distribution boxplots from `plot_visits_dist_screen` (the function calls `plt.show()`; save figures explicitly in the submitted script)

When reporting results, focus on biological interpretation: velocity direction, fate structure, terminal states, TFs that shift fate probabilities, and caveats from prior-GRN quality. Mention infrastructure details only when they affect the result.

## Failure Handling

- If the job fails quickly, inspect logs once with `hpc_get_logs(job_id)`.
- If CUDA or imports look broken, submit a short diagnostic command from the live usage, usually `regvelo-healthcheck --require-cuda --json /output/regvelo_healthcheck.json`.
- If the job OOMs during training, reduce `train_batch_size` first, then reduce genes/cells or epochs. If it OOMs during output extraction, reduce `output_batch_size` and `output_n_samples`.
- If inputs are invalid, explain the missing layer, missing GRN, or missing annotation column and stop. Do not invent defaults for biological metadata.
- If GPU capacity returns a rate-limit error, tell the user the concurrent GPU job limit was hit. Do not promise automatic retry.
