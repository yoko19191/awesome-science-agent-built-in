---
name: flow-cytometry-analysis
description: Use for automated analysis of flow, mass, CyTOF, or spectral cytometry
  FCS files. Covers acquisition and spillover QC, compensation, arcsinh/logicle transforms,
  permissive control-aware gating, FlowSOM/CATALYST clustering, immunophenotype annotation,
  cross-sample quantification, manual-gate reconciliation, and diffcyt differential
  abundance.
category: proteomics_metabolomics
visibility: public
starting-prompt: Can you identify and quantify cell populations in my flow/CyTOF FCS
  files and make a PDF report with methods, results, conclusions, figures, and next
  steps?
---
# Cytometry Clustering & Annotation

End-to-end workflow that turns raw flow or mass-cytometry (CyTOF) data into annotated cell
populations, quantifies them across samples, reconciles aggregate population statistics against
available manual-gating exports, optionally runs differential-abundance testing, and produces a Phylo-branded PDF
report with an infographic.

The pipeline is **R / Bioconductor** (CATALYST + FlowSOM + ConsensusClusterPlus + diffcyt, with
flowCore for FCS I/O and transforms). The **report + infographic** step is Python (ReportLab via the
`pdf-report-generation` skill + `GenerateImage`).

---

## Scope

**Does:** modality-aware QC/pre-gating → transform (+ compensation for flow) → unsupervised
clustering ("automated gating") → cell-type annotation → cross-sample abundance quantification →
manual-statistics reconciliation when available → (optional) differential abundance → PDF report.

Time-based acquisition QC (flow-rate, signal-stability, and margin-event checks over the Time
channel) and spillover **condition-number** diagnostics run and report by default but remove no
events. External compensation matrices, batch-aware cutoff harmonization, the **OpenCyto /
flowWorkspace** hierarchical-gating backend, and **CyTOF bead normalization**
(`CATALYST::normCytof`) are opt-in.

**Does NOT:**

- Interactive 2D manual polygon gating (this replaces it with clustering-based automated gating).
- **Targeted cytotoxicity dose-response assays** (ADCC/CDC/ADCP: gate a marker-defined target
  population → % viability-dye⁺ "DEAD" → fit a 4PL/Hill dose-response and AUC per antibody/compound).
  That is a *sequential targeted-gating* workflow, not unsupervised clustering — **do not silently
  substitute clustering for it.** This skill's clustering-based gating is the wrong tool for a per-well
  %DEAD-vs-dose readout. If asked for one, say so explicitly. (The gating principles here — permissive
  scatter gates, mandatory manual-gate reconciliation, and baseline sanity checks — still apply; see
  `references/qc_gating.md`.)
- **Spectral unmixing** — raw spectral data (unmixing pending) is detected and refused; **already-unmixed** spectral data (Cytek Aurora / Sony ID7000 standard output, identified by named fluorophore/marker channels) is accepted and processed as fluorescence flow.
- scRNA-seq → use `scrnaseq-scanpy-core-analysis` / `scrnaseq-seurat-core-analysis`.
- Bulk transcriptomics/proteomics clustering → use `bulk-omics-clustering`.

---

## Inputs

| Input                                                                     | Description                                                                                                                                                                                                                                                                                                                                                         |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `.fcs` files                                                            | One or many. Flow or CyTOF. User-supplied experimental data or an explicitly requested public dataset.                                                                                                                                                                                                                                                              |
| `metadata.csv` (multi-sample)                                           | Columns: `file, sample_id, group, batch, patient` (see `assets/metadata_template.csv`). `group`/`batch` drive differential abundance; extras are optional.                                                                                                                                                                                                  |
| `controls.csv` (optional)                                               | Unstained/FMO controls; builtin QC anchors viability cutoffs only (other marker controls need an explicitly scoped positivity workflow): `channel, control_file, control_type{unstained\|fmo}, percentile`. Supplied controls follow the sample compensation/transform path; unreadable, ambiguous, incompatible, or unused rows stop the run.                     |
| External spillover matrix (optional)                                      | A pre-computed compensation/spillover matrix as CSV (channels × channels, header + matching row names) for when the FCS lacks a usable embedded `$SPILL`. Pass via `--spillover`; aligned to the panel by channel-name intersection and its **condition number** reported.                                                                               |
| OpenCyto gating template (optional)                                       | A `gatingTemplate` CSV (`alias,pop,parent,dims,gating_method,gating_args,…`) for the opt-in backend. Branched templates require `--opencyto-terminal`; missing/ambiguous dimensions or implicit review bypass stop the run.                                                                                                                                  |
| Edited `gating_thresholds.csv` (optional)                               | Reviewed cutoffs from pass 1, applied only with `--gate-review apply --thresholds <file>`; merely supplying a file in proposal mode does not apply it or produce a pass. Schema: `assets/gating_thresholds_template.csv`.                                                                                                                                       |
| HDCytoData dataset name                                                   | A public dataset explicitly requested by the user; installs via BiocManager. Analysis mode reads expression and ordinary sample/channel metadata but not packaged reference labels or benchmark descriptions.                                                                                                                                                       |
| Rare-population definition (rare mode)                                    | Population name, at least two measured positive/negative defining markers, optional minimum plausible frequency, and whether it should recur in any, most, or all samples. These guide a marker-aware proposal; they are not treated as a confirmed label.                                                                                                          |
| CyTOF DNA channel override (optional)                                     | `--dna-channels` accepts one or two exact `$PnN`/`$PnS` values. The common `Ir191` + `Ir193` pair is handled together by default; use this option only when metadata exposes more than two possible DNA channels.                                                                                                                                         |
| Manual-gating **statistics** export (optional, strongly encouraged) | A FlowJo *Export Statistics* file (or any per-sample population count / % table). Drives the **mandatory reconciliation** in step 4b (`--manual`). Distinct from per-cell labels: this is aggregate per-sample stats, which real customer data usually has and per-cell labels usually don't. `.csv`/`.tsv`, or `.xlsx` if `readxl` is installed. |

If the user did not supply data, ask them to upload or identify their files. Do not silently substitute
a bundled example: this skill contains no FCS, RDS, observational CSV, or sample dataset. Public
Bioconductor datasets are fetched only when a user explicitly requests that dataset,
and must be stored outside the extracted skill directory.

### Installable-package boundary

The installable skill is source-only. It may contain `SKILL.md`, production analytical scripts,
scientific references, schema-only templates, and required configuration assets. It must never contain
evaluation harnesses, revert tests, `assets/eval/`, sample data, observational rows, PDFs, figures,
reports, traces, manifests, or generated results. Runtime inputs belong outside the extracted skill;
runtime outputs belong under the requested `/mnt/results/<run>/` directory.

---

## Outputs (all per-run artifacts go to `/mnt/results/<run>/`)

- **SCE** (`sce_annotated.rds`) — QC'd, transformed, clustered, annotated SingleCellExperiment.
- **QC/transform log** (`qc_transform_log.txt`/`.csv`) — modality detected, transform + compensation
  applied, cells removed at each QC stage.
- **Tables** (`.csv`) — **editable `gating_thresholds_template.csv`** (proposed QC cutoffs, per-gate,
  for review/override) and `gating_thresholds_applied.csv` after application; abundance per sample;
  (if ≥3/group) diffcyt DA/DS results; (if a manual export was supplied) `validation_vs_manual.csv`
  with per-sample/per-population pipeline-vs-manual deltas and a distinct
  **PASS/REVIEW/INCOMPLETE verdict**; canonical gating, transform, control, lineage-bias, resolution,
  and annotation state tables; `resolution_candidates.csv`, `resolution_shortlist.csv`, and the
  resumable `sce_cluster_candidates.rds` checkpoint.
- **Figures** (`.png` + `.svg`) — **per-gate QC diagnostics** (`gate_<sample>_<gate>.png`, actual
  cutoff/2D region drawn on the density), marker z-score heatmap, multi-metric resolution diagnostics,
  delta-area, UMAP/tSNE, abundance,
  raw-percentage frequency heatmap or a ranked single-sample percentage chart, resolution sensitivity.
- **PDF report** (`report_<dataset>.pdf`) — **mandatory** final deliverable, written beside the case
  artifacts in `/mnt/results/<run>/`: infographic +
  Introduction, Methods, Results, Conclusions, References (verbatim, citation-verified), Next steps.
- **Evidence contract** — `report_claims.csv`, `artifact_manifest.csv`, review proposal/receipt state,
  `qc_uncertainty.csv`, `time_qc_diagnostics.csv`, `review_artifact_index.csv`, and canonical
  stage-state files reconcile every report claim with its source and index the figures needed for review.
- **Rare-population evidence when requested** — `rare_population_candidates.csv`, a deduplicated
  shortlist, cross-seed stability and marker-evidence figure, `sce_rare_candidates.rds` resume checkpoint,
  separate refined SCE, assignments, state, and review receipt.

---

## Workflow

This is a general orchestration skill: run the production helpers in stages and pause at the explicit human review checkpoints. A single unattended analytical runner cannot replace these review/resume decisions. Gating `auto` permits re-entry only with the same persisted input fingerprint and no QC uncertainties. Annotation proposals still require reviewed `apply` or a fresh output directory.

Run the scripts in order (each writes an `.rds` the next reads). All scripts take arguments so they
generalize across supported inputs. **Exception:**
`08_validate_vs_manual.R` (step **4b** below) is a validation *gate*, not a pipeline stage — despite
its file number it should run **as early as its inputs allow** (right after `01` for the total-cell
over-gating check; re-run after `04` for the population-level check) and **before** you trust `05`/`06`/`07`.
Every real run **ends with the mandatory report** (step 7): `build_manifest.R` collects the run's
numbers into `run_manifest.json`, then `07_build_report.py` turns them, the figures, and
CrossRef-verified references into the PDF — written to the same per-run directory.

Before step 1, run `scripts/runtime_preflight.py --outdir <run> --profile full` (use
`--profile differential` only when differential abundance is requested, or `--profile opencyto`
when OpenCyto is requested). Do not begin analysis
until `environment_state.csv` is `PASS_ready`. Install missing dependencies in one bounded foreground
step and rerun the preflight. If infrastructure prepares an environment asynchronously, preserve
`environment_resume.json` and resume this same task only after the job is terminal; otherwise report
`INCOMPLETE_environment_resume`. A dependency-only task is not a completed analysis.
This checkpoint cannot repair a platform scheduler that fails to redeliver a task after background
environment preparation. Record that condition as a platform issue; never describe it as a completed
or scientifically failed cytometry analysis.

> If a spec item does not match the code you find, report it rather than inventing a substitute.

### Interview and review protocol

Ask exactly one decision question per message. Use a short heading, one plain-language sentence
explaining what the choice changes biologically, and two or three mutually exclusive choices. Put the
recommended choice first and explain its consequence; do not embed further questions, “Q1/Q2”, or
parameter jargon inside a choice. Never answer a review question on the user's behalf.

Before a gating or QC question, send a short context message with direct Markdown links to every
relevant figure that exists. Read `review_artifact_index.csv`, verify each target exists, and link the
individual file (for example, `[FSC debris gate](figures/gate_<sample>_debris_fsc.png)`), not only the
`figures/` folder. Keep these links and explanations outside the clarification-question widget so the
question itself stays clean. Explain what the orange boundary applies, what any green alternative means,
and what biological events could be lost; never ask an immunologist to infer this from method names.

Use this order:

1. Ask what level of biology matters: broad immune lineages, standard subsets, fine subsets, or rare
   population discovery. Explain that finer settings protect small populations but create more
   clusters to merge and review.
2. Ask which population, if any, must not be merged.
3. If rare discovery was requested, ask which measured markers should be positive and negative. Explain
   that at least two are needed to distinguish a population from nearby cells.
4. Ask separately for the minimum plausible frequency, if known.
5. Ask separately whether the population should recur in any, most, or all samples.
6. If the user is unsure about the phenotype, recommend retaining only the global clustering and state
   that unsupported rare-population claims will remain unresolved.
7. Run gating in proposal mode. Ask: “Do the orange applied cutoffs keep the intact singlet cells you
   expect?” Explain that FSC/SSC orange lines are deliberately permissive percentile floors, while a
   dashed green line is only an alternative density valley. Offer: accept all, revise specified gates,
   or stop.
8. If `qc_uncertainty.csv` has rows, pause again and ask about one uncertainty at a time. State what was
   observed, what could go wrong, and the recommended conservative action.
9. Present the resolution shortlist. Explain separation, repeatability, rare-population protection,
   and merge burden without asking the user to interpret PAC, silhouette, or DBI unaided.
10. For rare mode, present the separate marker/stability shortlist and direct figure link. Explain that
    the main clustering is unchanged, and that partially supported alternatives remain REVIEW overrides.
11. Present annotation and merge decisions. Unsupported labels remain unresolved. Ask whether to apply
    the completed map, continue provisionally, or stop and revise the panel/granularity.

Human approval must come from an actual user message, with the real task/message IDs and
reviewer identity. Delegating analysis or selecting unattended mode does not supply a human
checkpoint decision. If explicit approval is absent, stay at the review checkpoint; never
invent an identity, message ID, or approval. Record the relaying agent separately from the human.
Keep reviewed decision inputs separate from generated outputs. Never hand-edit receipt-state
CSVs or hashes to pass a verifier; rerun the checkpoint only after reconciling the original
approval and exact decision bytes. Local receipt checks verify consistency, not the authenticity
of a remote human message; independent review must compare them with the execution trace.

Gating and annotation proposal CSVs also contain `review_input_sha256`, binding the proposal to the scientific inputs and settings. Preserve that column unchanged. A different dataset, preprocessing configuration, or clustered SCE requires a newly generated proposal and an explicit review; an earlier approval cannot be carried across. Historical proposals without this binding remain historical evidence and cannot authorize a new application.

For each accepted proposal, create a receipt conforming to `assets/review_receipt.schema.json`. Bind it
to the proposal SHA-256, the exact persisted decision artifact, selected value when applicable, and the
external user's task/message identifiers. The execution agent is never a valid source of approval.
For resolution and rare-population review, fill the generated `<stage>_decision.csv` before hashing it
into the receipt. Do not delete or rewrite the proposal after the user reviews it.

### 1. Load, transform, and QC — `scripts/01_load_and_qc.R`  **(QC is first-class — see below)**

- Load FCS (`flowCore::read.flowSet`) or an HDCytoData dataset; build a CATALYST SCE via `prepData`.
- **Auto-detect modality** from FCS keywords: `$CYT`/`$CYTSN` + mass-tag channel names (isotope
  masses, `Di`) ⇒ **CyTOF**; fluorochrome channels + `$PnV` voltages + `FSC/SSC` ⇒ **fluorescence flow**.
- **Transform (logged explicitly, override-able):** one definition is built before gating and reused
  without re-estimation for samples, controls, diagnostics, and the final expression assay.
  - CyTOF ⇒ arcsinh **cofactor = 5**.
  - Flow ⇒ **logicle/biexponential** (`flowCore::estimateLogicle`, per-channel, data-driven) or a
    **per-channel estimated arcsinh cofactor**. Never silently hardcode a cofactor.
- **Compensation (flow):** apply embedded spillover (`flowCore::spillover` → `compensate`) if present;
  else warn and proceed uncompensated, noting it. The embedded matrix is **validated before inversion**
  (square, finite, invertible via `solve()`): a **singular / non-invertible** spillover matrix (seen in
  real BD LSRII exports) no longer crashes the run — it is skipped with a warning and the data proceed
  **UNCOMPENSATED**. **Raw spectral panels** (unmixing pending) are refused with an override hint; already-unmixed spectral data proceeds as fluorescence flow.
  Every applied matrix (embedded or external) is graded by **`spillover_diagnostics`**
  — an SVD **condition-number (κ)** check returning verdict well/ill/singular; an **ill-conditioned**
  matrix (κ > `--spillover-kappa-max`, default 1000) is **reported but still applied** (diagnostics-on),
  a **singular** one is skipped (UNCOMPENSATED). An **external** compensation matrix can be supplied as
  CSV via `--spillover` and is aligned to the panel by channel-name intersection.
- **Provenance:** read cohort/tissue/disease/source from user-supplied FCS/`metadata.csv` metadata and
  never infer it. For public packaged datasets, analysis mode records the package name/version and
  requested dataset identifier but does not inspect benchmark descriptions or reference labels.
- See `references/transform_and_compensation.md`.

For HDCytoData CyTOF inputs, the flow-frame detector names come from mass-tagged `channel_name` while
the biological antigen labels remain `marker_name`. This is required so digit-less antigens such as
HLA-DR are not silently dropped by mass-channel parsing.

### QC and reviewed pre-gating

Read `references/qc_workflow.md` before running QC. Default `propose` and reviewed `apply` preserve the proposal → user review → apply checkpoint. `--gate-review auto` applies proposed gates only with an empty QC uncertainty table, recording an auto receipt state; otherwise it persists the proposal and stops (exit 3) for review. This applies to both engines. Every proposed sample/gate needs exactly one effective reviewed row; rejection or stop never authorizes application. Inspect the diagnostic figures and recorded uncertainties before requesting a decision.

### Gating backend — built-in (default) vs OpenCyto (opt-in) — `scripts/gating_opencyto.R`

QC and marker gating run through one of two engines, selected by `--gate-engine`:

- **`builtin`** (default) — the data-driven per-gate engine described above (density-valley or
  explicit percentile cutoffs, diagnostic 2D density contours, honesty guards, editable template,
  and per-gate figures).
- **`opencyto`** (opt-in) — a real **openCyto / flowWorkspace** `GatingSet` driven by a
  `gatingTemplate` CSV for reproducible, shareable **hierarchical** gating. It reuses the run-wide
  transform definition. Shipped default
  templates live in `assets/gating_template_flow.csv` and `assets/gating_template_cytof.csv`; supply
  your own with `--gate-template` (which auto-selects this engine). Requires the openCyto stack
  (openCyto, flowWorkspace, CytoML) — the run **stops with a clear message** if it is missing rather
  than silently degrading. Template dimensions are ambiguity-checked; supplied controls are rejected
  because this backend cannot consume them safely; branched hierarchies require an exact or uniquely
  named `--opencyto-terminal`. The hierarchy is written to `gating_hierarchy.txt`.
  `propose` persists `opencyto_template_proposed.csv`; `apply` requires a matching proposal and
  receipt, with the reviewed decision CSV supplied as `--gate-template`.

### 2. Cluster ("automated gating") — `scripts/02_cluster.R`

- `CATALYST::cluster(sce, features = "type", xdim = 10, ydim = 10, maxK = <adaptive>, seed = ...)`
  (FlowSOM SOM → ConsensusClusterPlus metaclustering).
- **Clarify before clustering:** ask whether the objective is broad lineages, standard subsets, fine
  subsets, or rare-population discovery. Then, in a separate question, ask which populations,
  defining markers, or minimum frequencies matter. Ask merge/keep timing separately. Never put
  multiple numbered questions or an extra “Q” inside one prompt.
- Broad, standard, and fine modes run FlowSOM exactly once. Reconstruct only ConsensusClusterPlus on the stored SOM codes using the
  identical seed/parameters and fail closed if partitions differ. For every `k`, record CATALYST's
  original delta-area value, PAC (0.1–0.9), median/minimum cluster consensus, event-weighted and
  cluster-balanced silhouette, Davies–Bouldin index, event abundance, and sample coverage. UMAP is
  never used to choose `k`.
- `--resolution-goal broad|standard|fine|rare` selects a biological role, not a fixed cluster count.
  The default search ceiling is the smaller of occupied SOM nodes and the number available in each
  80% consensus resample: a 10x10 SOM therefore evaluates through `k=80`. A lower explicit `--maxK`
  wins; an infeasible larger value stops. Every run reports broad, balanced, fine, and rare-preserving
  candidates. All are quality-ranked. Rare-preserving candidates occupy the upper quarter and must
  also pass minimum-cluster consensus, so rare mode never blindly chooses the largest stable `k`.
- Default `--resolution-review propose` writes `resolution_candidates.csv`, a shortlist, diagnostic
  figure, state, and `sce_cluster_candidates.rds`, then stops. Resume without retraining FlowSOM with
  `--resume-candidates ... --resolution-review apply --k <exact k>`. When no stable choice exists, the
  shortlist exposes an actionable `INCOMPLETE_resolution_ambiguous` checkpoint and a deterministic
  finest-defensible fallback. An automatic fallback is `REVIEW_ambiguous_auto_applied`. If no candidate
  has positive silhouette, remain `INCOMPLETE_weak_structure`; an exact override requires
  `--decision-reason` and remains `REVIEW_weak_structure_user_override`.
- A reviewed resolution additionally requires `--review-receipt <receipt.json>` fingerprinted to the
  exact `resolution_candidates.csv`, filled `resolution_decision.csv`, and selected `k`; pass the same
  non-empty reason with `--decision-reason`. Applying a choice without a matching external
  receipt remains visibly unverified. Every resolution and delta-area panel marks the proposed or
  selected `k` with a labelled vertical line.
- See `references/clustering_and_resolution.md`.

### 2b. Rare-population refinement (only for a rare objective) — `scripts/02b_refine_rare.R`

- Preserve the selected global clustering and scan distinct stored metacluster branches using the
  user-declared positive/negative phenotype, frequency, and sample recurrence.
- Collapse byte-equivalent event sets found at several `k` values so duplicates cannot dominate review.
- Compare marker contrast against nearby competing SOM nodes and test event-set stability using the
  primary FlowSOM plus two additional deterministic seeds. Reference labels are not available to ranking.
- A supported automatic candidate remains `REVIEW_rare_auto_proposed`; an externally reviewed shortlist
  choice is `REVIEW_rare_user_selected`, never a biological pass. Any persisted candidate may be selected;
  a non-shortlisted or partially supported choice requires an external receipt and reason and remains
  `REVIEW_rare_user_override`. The review artifact lists resolution, frequency, sample coverage,
  stability, SOM-node membership, every local marker contrast, and direction conflicts. Local contrasts
  compare nearby SOM nodes; they do not define biological marker positivity.
- The user may reject every candidate with `--rare-review reject`, a filled
  `rare_population_decision.csv`, a reason, and a matching receipt. This finishes as
  `INCOMPLETE_rare_resolution` with concrete follow-up options and no forced label.
- Workflow completion, selection, and identity-validation states are separate. Applied branches record
  `PASS_rare_deliverable`, one of the `REVIEW_rare_*` selection states, and
  `NOT_EVALUABLE_rare_identity` unless independent event-level evidence exists.
- Resume from `sce_rare_candidates.rds` to apply a reviewed candidate without repeating FlowSOM.

### 3. Annotate + two-tier refinement — `scripts/03_annotate.R`

- Compute per-cluster median marker expression; z-score lineage markers → heatmap.
- Normalize antigen/channel names before matching. A population proposal is emitted only when a
  conservative measured-marker signature is supported without a conflicting lineage signal.
  Unsupported clusters remain `UNRESOLVED` with likely alternatives, supporting/conflicting markers,
  and missing-marker guidance. **CellMarker2** supplies supplemental alternatives only when at least
  two reference markers were measured; a positive-only score is never sufficient for a proposal or
  confirmed label.
- Default `--annotation-review propose` writes proposed label, top markers, event count/frequency,
  sample coverage, and blank `population`, `merge_decision`, and `decision_reason` fields, then stops.
  Repeated population names merge only when their rows explicitly say `merge`. A reviewed pass requires
  a consistent merge/keep decision and reason for every cluster plus a valid external
  `--review-receipt` matching the proposal and applied file. Incomplete or legacy two-column maps can
  continue provisionally but remain `REVIEW_merge_pending`; complete maps without a matching receipt
  remain `REVIEW_applied_unverified`; `auto` uses generic IDs and remains `REVIEW_merge_pending`.
- `CATALYST::mergeClusters` to consolidate redundant clusters into named populations.
- **Two-tier strategy (baked in):** annotate at a coarse, interpretable resolution, then **subcluster
  specifically the lineages flagged as merged** (e.g. split a CD3+ cluster into CD4/CD8).

### 4. Quantify + dimensionality reduction — `scripts/04_quantify_dr.R`

- `runDR` UMAP (+ tSNE cross-check) on a **balanced subsample** (default **10,000 cells/sample**,
  `--cells_per_sample`, capped at the smallest sample) for speed. **Visualization only** — clustering
  and abundance/% use all cells.
- Abundance counts and raw percentages per sample. One-sample analyses use a ranked percentage chart;
  multi-sample analyses use a raw-percentage heatmap. Figures with more than 20 populations show the 20
  most abundant plus `other`; the complete abundance table and assignments remain unchanged. A
  supplemental z-score heatmap is produced only for at least three samples. UMAP/t-SNE captions state
  that they are subsampled and use the same top-20-plus-other display for large partitions.

> **Subsampling policy (by stage) — no events are lost in analysis.** QC (`01`), clustering /
> population discovery (`02`, FlowSOM trains on **every** post-QC event), annotation (`03`), abundance
> and % (`04`) and differential abundance (`06`) all use **ALL post-QC cells** —
> rare/mixed populations are never thinned. The **only** subsample in the whole pipeline is the
> UMAP/t-SNE **embedding** in `04` (`--cells_per_sample`, default 10,000/sample, capped at the smallest
> sample), which sets **plot density only** and feeds no count, frequency, cluster assignment, or
> statistic. `02` and `04` log the exact cell counts used at each stage so this is auditable. (There is
> no dose-response / per-well biomarker subsampling here — that ADCC-style targeted-gating workflow is
> out of scope; see **Scope**.)

### 4b. Validate vs a manual-gating export (MANDATORY when one exists) — `scripts/08_validate_vs_manual.R`

Whenever a manual-gating export is available (a FlowJo *Export Statistics* file, or any per-sample
population count / % table), **reconcile the pipeline against it before trusting downstream fits,
abundance comparisons, or the report.** This is a gate, not polish — it is the check that catches
gating errors the pipeline's own diagnostics cannot see.

- Reconciles, per sample: (1) **total post-QC cell count** vs the manual singlet/"Cells" count (the
  primary **over-gating detector** — needs only `01`'s `sce_prepped.rds`, so run it early), and
  (2) **per-population counts/%** vs the manual export (when an abundance table from `04` exists).
- Requires an explicit or unambiguous manual parent, honors tidy count/percentage overrides and
  population mappings, and records full sample/population coverage. Percentage comparisons require a
  verified per-row `pct_denominator` matching the manual counterpart of retained SCE cells; unknown or
  incompatible denominators are not computable and carry no delta/flag. Counts remain comparable.
- Emits **PASS** only for complete agreement, **REVIEW** for matched disagreement, and **INCOMPLETE**
  for missing or ambiguous samples/populations or percentage denominators (exit codes 0/2/3).
- **Independent ground truth beats internal self-consistency.** Bimodal histograms, a monotonic
  dose-response, and plausible correlations are *not* validation — a biased gate produces those too.
  When any external reference exists, use it; do not treat self-consistency as proof.
- The report embeds agreement tables, per-population deltas, and coverage-gap reasons from the
  run root or `manual_reconciliation/`. Its reconciliation verdict remains separate from
  `overall_state`, which reflects the analysis artifacts and checkpoints. An incomplete external
  comparison remains clearly disclosed and never becomes evidence of agreement.
- See `references/validation_vs_manual.md`.

### 6. Differential abundance (conditional, strict) — `scripts/06_diff_abundance.R`

- Run **diffcyt-DA-edgeR** (abundance) and **DS-limma** (state markers) **only when ≥3 samples per
  group** and ≥2 groups. Otherwise **refuse to emit p-values** and report descriptive abundance +
  an explicit limitation. This refusal is the point — do not p-hack n=2.
- Design formula includes **covariates**: `~ batch + group` (not group alone).
- See `references/diffcyt_design.md`.

### 7. Report + infographic (MANDATORY) — `scripts/07_build_report.py`

The PDF report is **not optional** — every real run ends here.

**Mandatory instruction:** Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses.

- **Required delivery order:** build the run manifest, generate the infographic, build the PDF,
  then run `verify_run_complete.py`. The report copies an external `--infographic` into the run
  directory before refreshing artifact hashes.
- **Build the run manifest first** — `scripts/build_manifest.R <run_dir>` writes `run_manifest.json`
  (modality; cell/sample/marker/cluster/population counts; transform; compensation; QC removal
  summary + detail; top population; and — when present — `diff_abundance{}` block)
  by reading the pipeline artifacts **read-only**. `07` consumes this manifest and it **never edits**
  `01`–`08`.
- Generate a landscape **text-free workflow illustration** with the **Biomni `GenerateImage` tool**:
  generic events/dots flowing through QC, transform, clustering, review, and quantification. Put no
  words, letters, numbers, marker names, population names, lineage-specific icons, or biological
  identities in the generated raster. `07` supplies the exact title, counts, states, and metrics from
  `run_manifest.json` and the reviewed annotation artifacts, so the composed first page remains an
  infographic without asking an image model to reproduce scientific claims. The illustration is
  **required** — `07` **fails fast** if it is missing or the file does not exist.
- Inspect the generated illustration before calling `07`. Discard and regenerate it if any visible
  text, named phenotype, or lineage identity appears. In particular, never turn an `UNRESOLVED`
  cluster into a B-cell, NK-cell, myeloid, activation-state, or rare-population depiction. After the
  PDF is built, inspect every page and regenerate on clipped captions, overlaps, unreadable labels,
  unsupported figure content, or excessive blank spill pages.
- Build the Phylo PDF by following the **`pdf-report-generation`** system skill (ReportLab template):
  Introduction, Methods (exact transform/compensation + **QC gates & counts removed** + **per-gate
  removal %, any OVER-GATING alarm, and the manual-gate validation verdict** + clustering
  params + resolution rationale), Results (figures + tables, with merged-population labels),
  Conclusions, **References**, **Next steps**.
- **Citation integrity (hard rule).** Reference metadata (title, venue, year, volume, issue, page,
  DOI) is taken **verbatim from CrossRef** and stored in `assets/references_cytometry.json`; `07`
  renders references from that store — **never from memory** — and unavailable fields render as `n/a`.
  Run `scripts/verify_citations.py` to audit DOI↔metadata consistency **before** shipping a report;
  real DOIs with fabricated titles are exactly the failure mode this guards against.
- **Output location:** the PDF is written inside the run directory supplied by `--outdir`, alongside
  the exact state and evidence artifacts it summarizes.
- **Never bury a non-pass state or over-gating alarm.** The manifest derives component and overall
  state from machine artifacts; the report marks results provisional when any required checkpoint is
  REVIEW or INCOMPLETE.
- Run a visual inspection on every figure and on the final PDF; regenerate on failure.

---

## Environment resources this skill uses

- **R/Bioconductor:** CATALYST, FlowSOM, ConsensusClusterPlus, diffcyt, flowCore, SingleCellExperiment,
  ComplexHeatmap, ggplot2/ggprism/ggrepel, svglite, uwot, Rtsne, scater; **flowDensity + flowClust** (data-driven
  valley / model-based gating) and **diptest + mclust** (unimodality guard and GMM antimode) for
  `gating_engine.R`. Install cytometry packages on demand via `BiocManager` into
  `/workspace/.Rlib` (not in the base R set). flowCore/CATALYST provide `estimateLogicle`, `compensate`,
  `spillover`, `normCytof`. The gating engine **degrades gracefully** — if flowDensity/flowClust/mclust/
  diptest are absent it uses native base-R fallbacks (KDE valley, Otsu, KDE peak-count for
  unimodality, percentile, and deterministic binned high-density envelopes).
- **OpenCyto backend (opt-in — `--gate-engine opencyto`):** openCyto, flowWorkspace, CytoML
  (with their graph deps Rgraphviz/RBGL/graph) for `scripts/gating_opencyto.R`, installed on demand
  into `/workspace/.Rlib` and not required for the default built-in path. Time QC supports native
  MAD only; flowAI/PeacoQC adapters are not implemented, and requests for them stop explicitly.
- **R (validation):** base R for `08_validate_vs_manual.R`; `readxl` **only** if the manual export is
  `.xlsx` (CSV/TSV need no extra package).
- **Python:** fcsparser (quick FCS sanity/preview), reportlab, pypdf; `requests` + `PyYAML` for
  `scripts/verify_citations.py`, the CrossRef citation-integrity audit over
  `assets/references_cytometry.json` (verbatim reference store consumed by `07`).
- **Data lake:** **CellMarker2** for cluster→cell-type annotation scaffolding; MSigDB / Human Protein
  Atlas optional for marker context.
- **Tools/skills:** `LiteratureSearch` (grounded citations), `pdf-report-generation` (PDF), `GenerateImage`
  (infographic — the **Biomni GenerateImage tool**), `ManageMachine` (right-size compute for real runs).
  `scripts/build_manifest.R` assembles the read-only `run_manifest.json` that the report step consumes.

Run `scripts/verify_run_complete.py --outdir <run>` after the PDF and infographic exist. Do not tell the
user that the analysis is complete unless it writes `PASS_deliverables_complete`; a missing report,
manifest, required state artifact, or unresolved environment produces an explicit incomplete state.

## Compute

Authoring/small runs are trivial. For real datasets (~1e5–1e6 cells × 30–40 markers, memory-light,
< ~4–8 GB): the bottlenecks are Bioconductor install (~15–40 min) and ConsensusClusterPlus
metaclustering (minutes). Provision ~8 CPU / 32 GB via `ManageMachine`; run UMAP/tSNE on a subsample;
subsample/chunk SOM training for >2–3e6 cells. Set seeds for reproducibility.

## Scientific caveats

- Transform and compensation **must match modality**; raw spectral data must be unmixed upstream; already-unmixed spectral data is detected from named fluorophore/marker channels and processed as flow (override with `--spectral-state`).
- **QC before clustering** removes debris/doublets/dead/beads; skipping it produces junk clusters.
- **Gate GENTLY — over-gating is the dominant silent failure mode.** Tight scatter/singlet gates
  discard *real* cells non-randomly and bias every downstream number without erroring. Default to
  permissive gates; heed the OVER-GATING alarm (>30% scatter/singlet removal); never hardcode a ratio
  window like 0.75–1.25.
- **Data-driven cutoffs are proposals, not gospel.** Valley/GMM/Otsu 1D thresholds and 2D gates are
  written to an editable template with per-gate figures; a `REVIEW_*` status (unimodal / shallow valley
  / too few events / no matching control) means the pipeline **refused** to trust an automatic cutoff —
  inspect the figure and confirm or override before relying on it. See `references/threshold_selection.md`.
- **On any automated-vs-manual discrepancy, inspect the scatter/singlet gate first** — then the
  marker-positive gate, then the viability threshold.
- **Reconcile against a manual-gating export whenever one exists** (step 4b) *before* trusting fits.
  Internal self-consistency (bimodality, monotonic trends, plausible correlations) is **not**
  validation — seek independent ground truth.
- **Sanity-check implausible readouts** (e.g. an ADCC 0-dose control >25–30% dead, or per-well target
  counts that track dose): treat as a gating-review trigger, not a biological explanation to accept.
- Benchmarking uses labeled cells only and **flags merges** (many-to-one) rather than reporting bare F1=0.
- Differential abundance needs **≥3 samples/group** with covariates; below that, descriptive only.
- Resolution selection and annotation involve judgment. Use the data-adaptive multi-metric roles and,
  for a named rare target, a separate marker/stability refinement; never treat delta area or maximum `k`
  as the answer by itself.
- Selecting a persisted rare branch is reproducible workflow evidence, not identity validation. A human
  receipt records the selection but cannot create a biological PASS. Without control-derived thresholds,
  a manual event-level gate, reference labels, or orthogonal validation, rare identity is
  `NOT_EVALUABLE_rare_identity` and the overall scientific state remains REVIEW.
- **Time-based acquisition QC is diagnostics-first.** By default (`--time-qc auto`, flow) it
  **flags** unstable flow-rate / signal drift / margin pile-ups and writes `figures/time_qc_<sample>.png`
  but **removes no events**; opt in with `--time-qc remove`. Non-native backend requests stop:
  using another scientific method requires a separately validated workflow, never silent fallback.
- **A high spillover condition number is a warning, not an auto-fix.** Large κ means
  compensation is numerically fragile; the matrix is reported and still applied. Fix it upstream — do
  not trust marker-positivity on a κ ≫ 1000 panel without review; a singular matrix is skipped and the
  data proceed UNCOMPENSATED.
- **Batch harmonization is opt-in and confidence-weighted.** `--threshold-scope batch` pulls
  each gate's cutoff toward its within-batch consensus by `(1 − valley_confidence)·shrink`, leaving
  confident valleys alone; it cannot rescue a mis-specified `batch` column — provenance still comes from
  metadata, and groups smaller than the minimum are left unchanged.
- **OpenCyto is an alternative backend, not a validator.** It yields reproducible hierarchical
  gates, but a `gatingTemplate` still encodes human choices — reconcile against a manual export (step 4b)
  exactly as for the built-in engine.
- **CyTOF bead normalization needs bead channels.** `--cytof-norm on|auto` acts only when bead
  channels exist and **skips** the bead-removal gate so beads survive for `normCytof`, which then removes
  them — do not additionally gate beads out.
- **Citations are verbatim, not remembered.** Reference titles/venues/years/DOIs come from CrossRef
  and live in `assets/references_cytometry.json`; the report renders them from that store and
  `scripts/verify_citations.py` audits DOI↔metadata consistency. Never hand-type a citation from
  memory — real DOIs paired with fabricated titles are a known failure mode.
- Read **provenance from metadata**, never infer cohort/health status.
