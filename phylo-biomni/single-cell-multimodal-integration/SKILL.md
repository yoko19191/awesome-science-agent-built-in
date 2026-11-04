---
name: single-cell-multimodal-integration
description: Integrate paired CITE-seq (RNA + antibody-derived tags) with modality-specific
  QC, dsb or CLR ADT normalization, Seurat WNN, optional totalVI, clustering, annotation,
  and replicate-aware differential abundance. Route paired RNA+ATAC to `scatac-multiome-analysis`;
  stop unsupported unpaired or mosaic designs.
category: integration
visibility: public
starting-prompt: Integrate multimodal single-cell measurements, annotate supported
  cell populations, and compare joint representations and condition-associated changes.
---

<!-- archetype: analysis-workflow -->
<!-- contract: evidence-v1 -->

# Single Cell Multimodal Integration

## Input Selection and Provenance

Launch `scripts/run_once.py` with an external config and explicit modality and metadata selections. The Python, shell, and R entrypoints verify the same sealed schema-v2 `input_selection.json` before science. Downloaded inputs require source, tool, version, command, and retrieval evidence; uploaded inputs record acquisition as `not_applicable`. Inference, substitution, package data, symlink escapes, incomplete role maps, and reused non-empty results roots are hard stops. Facts, report, and receipt bind the canonical manifest hash and selected dataset.

## When to Use This Skill

For paired CITE-seq integration; unpaired/diagonal and mosaic RNA+ATAC are detected only to hard-stop as unsupported.

## Why This, Not The Obvious Thing (READ FIRST)

Establishing the design regime before choosing a method is the field's most common error: a paired CITE-seq bundle is confirmed to carry Gene Expression and Antibody Capture (not Peaks) before integration, and paired RNA+ATAC is routed to `scatac-multiome-analysis` rather than force-integrated.

## Inputs

- A 10x CITE-seq filtered_feature_bc_matrix.h5 with both Gene Expression and Antibody Capture feature types (paired CITE-seq route).
- raw_feature_bc_matrix.h5 (unfiltered droplets) for requested dsb ambient background estimation; without a sufficient validated captured low-depth non-cell population, the requested dsb route fails loudly. CLR remains an explicit user/configuration choice.
- metadata.csv with per-cell or per-sample replicate and condition columns for replicate-aware differential abundance.
- Unpaired/diagonal or mosaic RNA+ATAC inputs are not accepted for integration; the router records an unsupported stop.
- A config file (scripts/config.example.yaml) naming isotype-control antibodies and setting the route, QC, WNN, totalVI, integration, and differential-abundance parameters.

## Outputs

- `report_single_cell_multimodal_integration.pdf` — provider-selected PDF built from provider-neutral `report_content.json`; `pdf-report-generation` is the contract default, while an immutable explicit user choice may select an installed enterprise styling provider
- `report_content.json` — provider-neutral report facts, ordered narrative blocks, exact tables, and exact figure references; styling providers consume it without changing the science
- `joint_cluster_celltype_assignment.csv` — the machine-readable paired-CITE-seq result: one row per joint WNN cluster with cluster id, supported cell type or explicit `unassigned` state, top RNA markers, top surface-protein (ADT) markers, mean RNA/ADT modality weight, and cell count
- `integrated.h5mu` — the integrated multimodal object (RNA + ADT assays, joint embedding, clusters, annotations, modality weights)
- `joint_qc_scorecard.csv` — integration metrics (batch-mixing, silhouette, and label purity only when an independent reference exists — see `label_purity_status.json`) in one table
- `label_purity_status.json` — machine-readable label-purity metric state: the reference source/join/coverage and its ARI/NMI, or `not_evaluable_independent_labels` with the reason; the report and receipt bind to this so a circular or non-evaluable metric is never narrated as integration quality
- `annotation_concordance.csv` — reference × marker-annotation confusion, surfacing novel/spurious labels and cluster split/merge instead of a single scalar
- `rna_markers.csv` / `protein_markers.csv` — per-cluster RNA log-fold-change markers and
  surface-protein normalized-score differences; declared isotype controls never appear as biological
  ADT markers
- `modality_weights_summary.csv` — per-cell WNN RNA/ADT modality-weight summary (weights in the closed unit interval)
- `report_facts.json` — evidence-bearing claims, operational definitions, provenance, and validated denominator/completion partitions


**Write the report to the results root under the name above.** Data tables, figures and intermediates go in `data/`, `figures/`, `tables/`. Note that `GenerateImage` strips directory components, so schematics always land at the root regardless of the path you pass it.

## Clarification Questions

1. **How should the run obtain its input bundle?** (select one)
   - `user_citeseq` — Use a user-provided 10x CITE-seq bundle; runtime branch ID `input_mode:user_citeseq`
2. **Which integration route should run?** (select one)
   - `auto` — Detect the design regime automatically (recommended); runtime branch ID `route_override:auto`
   - `citeseq` — Force the paired CITE-seq RNA+ADT route; runtime branch ID `route_override:citeseq`
   - `unpaired` — Confirm an unpaired/diagonal RNA+ATAC request and hard-stop it as unsupported; runtime branch ID `route_override:unpaired`
   - `mosaic` — Confirm a mosaic RNA+ATAC request and hard-stop it as unsupported; runtime branch ID `route_override:mosaic`
3. **How should antibody-derived tags be normalized?** (select one)
   - `dsb` — dsb background denoising (needs a validated background; uses isotype controls when available); runtime branch ID `adt_norm:dsb`
   - `clr` — CLR normalization (no empty droplets/isotypes required); runtime branch ID `adt_norm:clr`

## Standard Workflow

**Mandatory immutable one-shot boundary.** Treat the attached skill directory as read-only. Never
create `config.yaml`, helper scripts, notebooks, caches, or outputs inside it. Copy
`scripts/config.example.yaml` to a fresh external run workspace, edit only that external copy, and
set both `project.outdir` and `project.results_dir` to fresh external directories. Then invoke the
package-owned launcher exactly once:

```bash
python -B /path/to/attached/single-cell-multimodal-integration/scripts/run_once.py \
  --config /workspace/scmi-run/config.yaml
```

`run_once.py` verifies the packaged member manifest before analysis, records a full package-tree
snapshot outside the package, refuses a non-fresh results root, calls `run_all.sh` exactly once,
seals every scientific artifact, and verifies that the full package tree is unchanged. Do not call
`run_all.sh` directly, delete outputs, reset `qc_run_log.json`, rerun, or substitute a package-local
helper. A failure is terminal for that task and must be reproduced in a new local workspace.
Steps 1–9 below describe what that single launcher invocation performs; do not execute them as a
second analysis path.


**Step 1 — Load and validate the input.** Enforce every item in `## Inputs`; stop with `not_computable` on a mismatch.


**Step 2 — Validate modality identity and resolve the route.** Run scripts/00_route.R to detect the 10x feature types (Gene Expression, Antibody Capture, Peaks), barcode structure, and isotype-control presence. Proceed only with paired CITE-seq, route paired RNA+ATAC to `scatac-multiome-analysis`, and hard-stop unpaired/diagonal or mosaic RNA+ATAC because genuine GLUE/StabMap integration is not implemented. Stop with a specific reason on trimodal or scRNA-only bundles rather than force-integrating.


**Step 3 — QC each modality.** Run scripts/02_rna_qc.R for RNA QC (counts, features, percent mitochondrial) and scripts/03_adt_qc_dsb.R for ADT QC (total protein counts, isotype signal) with modality-specific thresholds; export per-modality QC tables and figures.


**Step 4 — Denoise antibody-derived tags with dsb.** At ingest, exclude the complete filtered-cell barcode set and any per-cell metadata barcodes from the raw matrix, then select only non-cell droplets with positive ADT capture inside the declared low-depth RNA/ADT bounds. Record the selection flow and assert zero called-cell overlap. Run scripts/03_adt_qc_dsb.R with that validated background and the named isotype controls when available. An explicit dsb request fails loudly when no valid background exists; it never treats all raw-minus-post-QC barcodes as ambient and never silently substitutes CLR. CLR remains an explicit normalization choice.


**Step 5 — Build the WNN joint embedding.** Run scripts/04_wnn_joint.R: compute RNA PCA and ADT PCA, run FindMultiModalNeighbors, and produce a joint UMAP, joint clusters, and per-cell modality weights asserted to lie in the closed unit interval.


**Step 6 — Compute the totalVI alternative and integration metrics.** Run scripts/05_totalvi.py and scripts/06_integrate_metrics.py: assemble an integrated .h5mu and compute batch-mixing, silhouette, and label-purity integration metrics into a joint QC scorecard. Label purity is scored (via scripts/label_purity.py) ONLY between the WNN clusters and an INDEPENDENT reference label preserved from user metadata before any annotation join, with verified alignment, coverage, and non-cluster-derived independence; otherwise it writes `label_purity_status.json` state `not_evaluable_independent_labels` with null ARI/NMI and claims no label purity. The marker-derived annotation is scored separately as `annotation_concordance.csv` (confusion surfacing novel/spurious labels and split/merge), never overwritten onto the reference. Run the totalVI probabilistic alternative only when its assumptions hold, otherwise record a documented skip.


**Step 7 — Annotate cells and rank RNA and protein markers.** Run scripts/07_annotate_markers.R for marker-based annotation on the joint clusters using the bundled PBMC panel. Package-owned canonicalization matches standard TotalSeq suffixes and R/Python separator variants; if no panel marker matches, export `unassigned` with null score/margin rather than selecting an arbitrary lineage. RNA markers retain log2 fold change. ADT markers use the arithmetic difference on the selected normalized-output scale with raw-count detection fractions and adjusted Wilcoxon P values; do not compute or label a log2 fold change on DSB standardized scores. Exclude declared isotype controls from biological marker tables, top-marker exports, and the annotation heatmap.


**Step 8 — Test replicate-aware differential abundance.** Run scripts/08_diff_abundance.R: build Milo neighborhoods on the joint embedding and test differential abundance with spatial FDR when at least two biological replicates per condition exist, and skip and flag rather than fabricate when replication is insufficient.


**Step 9 — Generate and validate figures.** Regenerate every figure from the current underlying data rather than reusing stale figures. The one-shot launcher renders only results produced by the preceding validated steps, writes `figures/manifest.json`, and calls the package-owned figure gate before it seals the scientific artifacts. Do not recreate or replace figures after this point.

## Figures

One representative figure per result-producing step, showing **this run's actual result** — not a schematic, not an illustrative example. Every figure needs a caption stating what it shows. If a step genuinely has nothing to plot, replace its row with a one-line reason. A caption is required at run time, so fill these in before the first run.

| Step | File | What it must make visible |
|---|---|---|
| 3 | `figures/figure_3_qc_each_modality.png` | Per-modality QC after filtering: RNA counts, genes per cell, and percent mitochondrial; ADT total protein counts and isotype signal, with the retained-cell thresholds marked. |
| 4 | `figures/figure_4_denoise_antibody_derived.png` | ADT signal before vs after normalization for the named isotype control and a matched surface marker when available; state the measured post-normalization isotype median without inferring a universal near-zero threshold (or state that the isotype diagnostic is not evaluable). |
| 5 | `figures/figure_5_build_the_wnn.png` | WNN joint UMAP colored by joint cluster, alongside the per-cell RNA/ADT modality-weight distribution with weights shown to lie in the closed unit interval. |
| 6 | `figures/figure_6_compute_the_totalvi.png` | Joint integration metrics (batch-mixing, silhouette, and label purity only when an independent reference exists — else flagged not-evaluable) beside the annotation-vs-reference confusion, which exposes any novel/spurious label and cluster split/merge. |
| 7 | `figures/figure_7_annotate_cells_and.png` | Per-cluster surface-protein signal heatmap, excluding declared isotype controls, with the supported cell type or explicit unassigned state and the confidence/margin flag. |
| 8 | `figures/figure_8_test_replicate_aware.png` | Milo neighborhood differential-abundance beeswarm across conditions with spatial-FDR significance, or the insufficient-replication skip flag when fewer than two biological replicates per condition. |

The report reads this inventory from `report_facts.json`'s `figures` array rather than restating it, so it cannot claim a figure that was never produced.
Every data-bearing figure owns an adjacent four-part paragraph labeled `Claim:`, `Evidence:`,
`Interpretation:`, and `Limitation:`. The report model nests those fields inside the figure block, the
default renderer keeps them with the image and caption, and final QC checks structural ownership plus
the exact ordered paragraphs in the current PDF text.

**Step 10 — Write `report_facts.json` from the runtime payload.** `run_once.py` reconstructs this
artifact from `facts_payload.json` and the validated figure inventory through the package-owned
semantic gate, then includes it in the one-shot seal. Do not hand-author facts or rerun this step.


### Step 11 — Final report (MANDATORY TERMINAL STEP)
**The run is not complete until this step has produced `report_single_cell_multimodal_integration.pdf` at the results root.**
Resolve the report-style provider from immutable user-message evidence. The contract default is
`pdf-report-generation`; an explicitly selected installed enterprise provider overrides it. Both
paths consume the same provider-neutral `report_content.json` and exact figure files. The bundled
ReportLab renderer is default-only and must stop before writing when an explicit provider is selected.

Produce one combined PDF with a short narrative. Place the qualitative GenerateImage infographic near the beginning as the first substantive visual; rendered page images are inspection evidence only, never substitutes for the PDF. End the task with a concise conclusion and links to the PDF and supporting artifacts, not a bare file listing.

The PDF must use these visible top-level sections in this order, adapting their contents to this skill rather than adding generic filler. Cite sources inline with numbered markers like `[1]` and include matching exact numbered references. Regenerate every figure from the current underlying data rather than reusing stale figures:

1. `Task Context` — the research or practitioner question, supplied inputs, scope, and decision the output informs.
2. `Methods & Sources` — Methods & Sources covers data provenance, filtering, parameters, software, and the analysis design; Results reports validated quantitative findings and figures.
3. `Results` — the run's actual outputs and evidence, never a description of what the skill could do.
4. `Conclusions & Interpretation` — supported takeaways, their practical meaning, and appropriate next steps.
5. `Next Steps` — concrete validation or follow-up work warranted by this run's evidence.
6. `Limitations` — run-specific uncertainty, missing coverage, failed or unavailable checks, and claims the evidence does not support.
7. `References` — exact numbered entries matching every inline `[N]` citation.

Include references and a compact output-artifact table where applicable. Empty boilerplate does not satisfy a section.

Generate `report_single_cell_multimodal_integration_infographic.png` with Biomni `GenerateImage`
before report preparation by submitting `assets/infographic_prompt.txt` verbatim. Resolve the report
provider first and load its complete installed instructions and assets, then apply only the resolved
provider's palette and typography. The infographic must remain a five-stage directional workflow with
minimal text: no logo, wordmark, watermark, table, dashboard, result plot, or run-specific value.
Immediately after the tool
returns—and before any transformation or replacement—snapshot its trace and pixels:

```python
from report_qc import record_generated_infographic
record_generated_infographic(
    "report_single_cell_multimodal_integration_infographic.png",
    log_path="qc_run_log.json",
)
```

Run the import-safe report workflow from the same external config and run workspace:

```bash
# Default style: prepare, build a fresh workspace PDF, publish it, and render every page.
python -B /path/to/attached/single-cell-multimodal-integration/scripts/make_report.py \
  --config /workspace/scmi-run/config.yaml --phase all

# Explicit provider: prepare only, then let that installed provider consume report_content.json
# and its exact figure paths into a fresh workspace PDF.
python -B /path/to/attached/single-cell-multimodal-integration/scripts/make_report.py \
  --config /workspace/scmi-run/config.yaml --phase prepare

# After every rendered page has actually been inspected and recorded:
python -B /path/to/attached/single-cell-multimodal-integration/scripts/make_report.py \
  --config /workspace/scmi-run/config.yaml --phase finalize \
  [--provider-rendered-report /workspace/fresh-provider-report.pdf]
```

`prepare` freezes the content, source/figure hashes, immutable provider selection, and installed
provider-source hash in `report_preparation.json`. `finalize` re-resolves the selection and rechecks
every hash, verifies the selected-provider PDF, refuses to overwrite an existing provider PDF, and
writes receipt-v3. An explicit provider may either supply a fresh workspace candidate to finalize or
publish the canonical results-root PDF itself; the bundled renderer never replaces it.

The one-shot launcher owns `run_bundled`, the QC command event, and the sealed scientific-artifact
inventory; do not call it separately, author events, or copy transcript identifiers. `--phase all`
performs `record_pdf_render` for the default provider; an explicit provider must call it after
publishing its PDF. Use ordinary `Read` to inspect the current PDF when supported or every rendered
page PNG with an image-capable model. Loading media is not a passing review;
inspect the pixels and fix defects. If visual input is unavailable, state that
limitation. The receipt verifies rendering and hashes but does not attest visual
quality. The finalizer
also rejects changed content, figures, provider source, provider selection, PDF bytes, scientific
artifacts, package members, duplicate execution, unmatched hashes, or source-value disagreement.

Record each answer from `## Clarification Questions` in `selected_branch_ids` using the displayed `<question_id>:<choice_id>` value. The receipt derives its required outputs only from those selected branches; do not union mutually exclusive branch artifacts.

`--phase all` returns the current page manifest. Open every returned page image with the native
Read/media inspection tool. Do not replace that action with authored observations or file-size
checks. Then run `make_report.py --phase finalize`; the receipt checks rendering and hashes.
GenerateImage lineage and explicit report-style selection use that same literal read-only platform
trace captured once. A results/workspace transcript cannot verify either. If the platform trace is
not exposed, the contract-default provider is retained (no override is invented), GenerateImage
lineage is recorded `not_evaluable_platform_trace`, and independent post-run evidence review is required.

For contract-audit purposes, the ordered package-owned wiring across `run_once.py` and the finalizer
is equivalent to the block below. Do not run this block separately or hand-author facts or the
receipt: `run_once.py` owns the figure/facts phase, and `--phase finalize` owns publication and the
receipt after first re-verifying the preparation record:

```python
from report_qc import (
    assert_figures,
    finalize_delivery,
    staged_copy,
    write_facts_from_artifact,
)

# run_once.py, before the immutable scientific-artifact seal
figures = assert_figures("figures/manifest.json")
write_facts_from_artifact(
    "report_facts.json",
    source="facts_payload.json",
    figures=figures,
    contract="skill_contract.json",
)

# make_report.py --phase finalize, after current preparation/hash verification
staged_copy(provider_workspace_pdf, "report_single_cell_multimodal_integration.pdf")
delivery = finalize_delivery(
    "report_single_cell_multimodal_integration.pdf",
    figures,
    figure_not_applicable_reason=None,
    bundled_files=["scripts/run_all.sh"],
    outputs=selected_outputs + [
        "report_content.json",
        "report_preparation.json",
        "report_style_selection.json",
    ],
    infographics=["report_single_cell_multimodal_integration_infographic.png"],
    style_provider=selected_provider,
    style_selection_record="report_style_selection.json",
    qc_run_log="qc_run_log.json",
    package_snapshot="attached_package_snapshot.json",
    execution_seal="one_shot_execution.json",
    contract="skill_contract.json",
    receipt_path="run_receipt.json",
)
print(delivery["terminal_message"])
```

The receipt is `run_receipt.json` at the **results root**, not beside this SKILL.md. It records `attached_package_tree_unchanged`, `one_shot_execution_verified`, `execution_contract_satisfied`, `outputs_appeared`, `report_at_results_root`, `figure_contract_satisfied`, `facts_artifact_verified`, `report_style_verified`, `text_extracted`, `pages_rendered`, `visual_review_attested`, `report_sections_present`, `report_citations_numbered`, `figure_local_narrative_verified`, `source_assertions_verified`, and `infographic_lineage_verified`, each with concrete evidence. `visual_review_attested` is `not_applicable`: the receipt does not attest visual inspection. Finalization then writes `delivery_authorization.json`, binding the current package, sealed science artifacts, facts, totalVI status, scorecard, receipt, and PDF hashes. End the task with exactly the returned `delivery["terminal_message"]`; do not report first-pass or remembered values.

**Do not write this file by hand.** `check_skill.py --require-run-receipt` requires the schema marker and per-outcome evidence, so a hand-written block of `true`s fails. Whatever did not hold is recorded `false` with a `<key>_reason`; fix the run rather than the receipt.

## Scientific caveats

- Establishing the design regime before choosing a method is the field's most common error: a paired CITE-seq bundle is confirmed to carry Gene Expression and Antibody Capture (not Peaks) before integration, and paired RNA+ATAC is routed to `scatac-multiome-analysis` rather than force-integrated. Evidence: `provenance_manifest.json (assay.identity_violations)`.
- Antibody-derived tags carry ambient background: dsb denoising uses a captured low-depth non-cell population that excludes every filtered/metadata cell barcode, plus named isotype controls when available. The full selection diagnostics and measured post-normalization isotype median are recorded without a universal near-zero acceptance threshold. An explicit dsb request fails if no valid background exists; CLR is never silently substituted. Evidence: `adt_normalization_status.json (background_selection, isotype_background_median, isotype_background_interpretation)`.
- DSB standardized scores are not log-normalized counts. `protein_markers.csv` reports `avg_normalized_adt_difference` and raw-count detection fractions; declared isotype controls remain normalization diagnostics and are excluded from biological marker outputs. Evidence: `protein_marker_status.json` and `protein_markers.csv`.
- Per-cell WNN modality weights lie in the closed unit interval and describe how much each modality drove the joint neighbor graph; a near-degenerate distribution means one modality dominated and the joint embedding is close to a single-modality result. Evidence: `modality_weights_summary.csv`.
- Label purity (cluster ARI/NMI) is meaningful only against an INDEPENDENT reference label: the reference is preserved before any annotation join and scored only when cell-level alignment, non-missing coverage, and non-cluster-derived independence hold; otherwise `not_evaluable_independent_labels` is recorded and no ARI/NMI is claimed. Scoring clusters against a marker annotation derived from those clusters is circular and inflates the score — the annotation is instead evaluated separately as annotation-vs-reference concordance that surfaces novel/spurious labels and cluster split/merge. Evidence: `label_purity_status.json (state, circular_metric_emitted, metrics)`.
- totalVI outputs are model predictions, not measurements, and run only when their assumptions are met; otherwise a documented skip is recorded rather than a fabricated latent space. Evidence: `totalvi_status.json`.
- Unpaired/diagonal GLUE and mosaic StabMap are unsupported: the router hard-stops before preprocessing because no genuine guidance graph/shared bridge, model fit, or label transfer exists in the package. Evidence: `route_decision.json (route=stop, stop=true, reason)`.
- Milo differential abundance requires at least two biological replicates per condition and more than one condition; with insufficient replication it is skipped and flagged, never reported with a fabricated FDR. Evidence: `diffabund_status.json`.
- This skill is hypothesis-generating: joint clusters, integration metrics, and neighborhoods are exploratory and are not validated at any threshold. Evidence: `report_facts.json`.

Each caveat must name the artifact field or the number that says whether it fired. An unbound caveat is prose; a bound one is a gate.

## Evidence Tier

Derive the reportable evidence tier from the current run's sealed inputs, completed scientific gates, report facts, provenance, and accepted receipt. Never exceed what those run artifacts support.

## Data Sources & Licenses

- Seurat (>=5.0): https://github.com/satijalab/seurat — license MIT; commercial use `allowed` (MIT license (GitHub LICENSE); WNN joint embedding and RNA analysis.); included `false`; verify with `references/licenses.md`.
- dsb (>=1.0): https://github.com/niaid/dsb — license CC0-1.0; commercial use `allowed` (CRAN/GitHub dsb DESCRIPTION is CC0-1.0 (public domain dedication); commercial use permitted. Re-verify at authoritative source.); included `false`; verify with `references/licenses.md`.
- scvi-tools (totalVI) (>=1.1): https://github.com/scverse/scvi-tools — license BSD-3-Clause; commercial use `allowed` (BSD 3-Clause (GitHub LICENSE); totalVI probabilistic RNA+ADT latent space.); included `false`; verify with `references/licenses.md`.
- mudata / muon (>=0.2): https://github.com/scverse/mudata — license BSD-3-Clause; commercial use `allowed` (BSD 3-Clause (GitHub LICENSE); .h5mu container.); included `false`; verify with `references/licenses.md`.
- scanpy / anndata (>=1.9): https://github.com/scverse/scanpy — license BSD-3-Clause; commercial use `allowed` (BSD 3-Clause (GitHub LICENSE).); included `false`; verify with `references/licenses.md`.
- miloR (>=1.10): https://github.com/MarioniLab/miloR — license GPL-3; commercial use `allowed` (GPL-3 (Bioconductor DESCRIPTION); copyleft, commercial use allowed under GPL terms.); included `false`; verify with `references/licenses.md`.
- presto (>=1.0): https://github.com/immunogenomics/presto — license GPL-3.0-only; commercial use `allowed` (GPL-3.0-only (GitHub); copyleft, commercial use allowed under GPL terms. Optional accelerator; Seurat Wilcoxon runs without it.); included `false`; verify with `references/licenses.md`.
- scglue (GLUE) (>=0.3): https://github.com/gao-lab/GLUE — inventoried for method context only; included `false`; unavailable in this package and installing it does not enable the hard-stopped route; verify license context with `references/licenses.md`.
- StabMap (>=0.1): https://github.com/MarioniLab/StabMap — inventoried for method context only; included `false`; unavailable in this package and installing it does not enable the hard-stopped route; verify license context with `references/licenses.md`.
- reportlab (>=4): https://pypi.org/project/reportlab/ — license BSD-3-Clause; commercial use `allowed` (BSD (reportlab source LICENSE); PDF rendering via the delegated report skill.); included `false`; verify with `references/licenses.md`.
- CellMarker 2.0 (2.0): http://bio-bigdata.hrbmu.edu.cn/CellMarker/ — license Ambiguous (paper CC-BY-NC-4.0; mirrors CC0; site copyright, no explicit reuse grant); commercial use `not_checked` (Conflicting signals; commercial reuse could not be unambiguously verified. Optional only; a built-in PBMC marker panel ships as fallback.); included `false`; verify with `references/licenses.md`.

## Resource Identity

Resolve `assay.declared_regime` against https://www.10xgenomics.com/support/software/cell-ranger/latest/analysis/outputs/cr-outputs-h5-matrices and compare `assay.observed_feature_types, assay.isotype_controls_present`. Write decisions to `provenance_manifest.json` and require `assay.identity_violations = 0` through a source-assertion witness. A valid identifier attached to mismatched metadata is a failure, not a resolved resource; exclude it or finalize `not_computable` before facts or the report are written.

## Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| Route resolves to `handoff` for paired RNA+ATAC | The bundle is genuinely paired RNA+ATAC multiome (`Gene Expression` + `Peaks`), not CITE-seq | Run `scatac-multiome-analysis`; this skill integrates paired CITE-seq only |
| Route resolves to `stop` (scRNA-only or trimodal) | The matrix carries only `Gene Expression`, or carries all three of GEX/ADT/Peaks | Use a scRNA-seq skill for single-modality RNA; trimodal (TEA/DOGMA-seq) is a documented out-of-scope future mode |
| Explicit DSB normalization stops | The raw matrix is absent, called/metadata cells cannot be excluded, or fewer than `adt.dsb_background.min_droplets` captured low-depth non-cell droplets survive | Correct the input identity or tune the declared assay-specific background bounds; choose `adt.normalization: clr` explicitly if ambient modeling is not intended |
| Near-degenerate modality weights (median ≈ 0 or 1) | One modality (usually RNA) dominates the joint graph | Inspect `modality_weights_summary.csv`; the joint embedding is close to single-modality — check ADT depth/denoising before interpreting |
| totalVI skipped | Fewer joint cells than `totalvi.min_cells`, or scvi-tools/torch unavailable | WNN remains the headline embedding; lower `totalvi.min_cells` only with justification (`totalvi_status.json` records the reason) |
| Milo differential abundance skipped and flagged | Fewer than two biological replicates per condition, or a single condition | Add replicates/conditions, or accept the documented skip in `diffabund_status.json`; DA is never fabricated |
| Route stops as unsupported for GLUE/StabMap | The package has no genuine guidance graph/shared bridge, model fit, or label transfer for that design | Do not install a backend and retry; use a separately validated integration workflow or implement and review the missing scientific route first |

## Suggested Next Steps

- Confirm exploratory joint clusters, modality weights, and differential abundance in an independent replicate cohort with orthogonal annotation before treating them as validated.
- Feed per-cluster RNA differential-expression results into `functional-enrichment-from-degs` for pathway context.
- For paired RNA+ATAC, continue in `scatac-multiome-analysis` for WNN, peak-to-gene linking, and chromVAR on chromatin.
- Treat totalVI cross-modality imputation as a hypothesis and validate against measured data; this package emits no GLUE/StabMap imputation.

## Related Skills

- **`scatac-multiome-analysis`** — the workflow for genuinely paired RNA+ATAC multiome; this skill stops and points there rather than duplicating its joint workflow.
- `scrnaseq-seurat-core-analysis` / `scrnaseq-scanpy-core-analysis` — single-modality RNA QC, normalization, clustering, and annotation.
- `multi-omics-integration` — MOFA+ factor analysis for bulk / general 2+ omics (this skill is single-cell).
- `functional-enrichment-from-degs` — enrichment on per-cluster RNA differential expression.
