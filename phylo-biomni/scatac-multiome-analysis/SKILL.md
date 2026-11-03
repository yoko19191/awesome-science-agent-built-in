---
name: scatac-multiome-analysis
description: 'Use for scATAC-seq, snATAC-seq, or 10x Multiome chromatin accessibility
  from fragments or Cell Ranger ATAC/ARC output: fragments-first QC, LSI clustering,
  MACS3 peak recalling, gene activity, and differential accessibility. For paired
  RNA+ATAC Multiome it adds a pairing and genome-identity gate, a WNN joint embedding
  with modality weights, integration metrics, and replicate-aware differential abundance.
  Unpaired/diagonal RNA+ATAC or CITE-seq is routed to a multimodal-integration skill.'
category: epigenomics
visibility: public
starting-prompt: Characterize chromatin accessibility from scATAC or paired multiome
  data, including quality control, cell annotation, regulatory features, and supported
  differential patterns.
---
<!-- archetype: analysis-workflow -->

<!-- contract: evidence-v1 -->

# scATAC-seq & 10x Multiome Analysis

Use ordinary `Read` with an image-capable model to inspect the actual image or every rendered PDF page. Loading media or extracting text alone is not a passing visual review. Fix visible defects before delivery; if visual input is unavailable, state that limitation. The programmatic receipt validates artifacts and provenance, and does not attest visual quality.

## When to Use This Skill

Use this skill for single-cell/single-nucleus **chromatin accessibility** and **paired 10x Multiome** analysis:

- **scATAC-seq / snATAC-seq** (fragments or Cell Ranger ATAC output) — fragments-first QC, TF-IDF/LSI clustering, per-cluster MACS3 peak recalling, gene-activity scoring, annotation, and differential accessibility.
- **10x Multiome, RNA-assisted ATAC annotation** — use the RNA modality to annotate cells and transfer labels to the ATAC assay (the existing multiome behavior).
- **10x Multiome, genuine paired RNA+ATAC joint analysis** — after a pairing/genome-identity gate passes: per-modality QC, a Seurat WNN joint embedding with per-cell modality weights, an integrated `.h5mu`, scIB-style integration metrics, an optional MultiVI sensitivity route, and replicate-aware differential abundance (Milo) plus cross-modality differential expression.

**Does not do (routed out or out of scope):** unpaired/diagonal RNA+ATAC and CITE-seq (`Antibody Capture`) are directed to `single-cell-multimodal-integration` rather than force-integrated; trimodal, spatial-multimodal, and perturbation-multimodal designs, and raw base-calling / `cellranger-arc` counting, are out of scope.

## Why This, Not The Obvious Thing (READ FIRST)

Treating a paired Multiome as scATAC-with-RNA-annotation, or integrating both modalities before confirming they came from the same cells and genome build, invents structure: the pairing and genome-identity gate must pass first, or the run stops and hands off rather than fabricating a joint embedding.

## Inputs

- A 10x Multiome filtered_feature_bc_matrix.h5 with both Gene Expression and Peaks feature types (joint/annotate route), or ATAC peaks/fragments only (scATAC route).
- atac_fragments.tsv.gz with a matching .tbi tabix index.
- For a Cell Ranger ARC combined matrix, its per-barcode metrics CSV with `atac_peak_region_fragments` and `atac_fragments`; combined-matrix Peaks values are cut-site counts and are never substituted for fragment-unit FRiP inputs.
- metadata.csv with per-cell or per-sample replicate and condition columns for replicate-aware differential abundance.
- An independent input genome identity: `/matrix/features/genome` in a combined 10x H5, a unique `genome` value in metadata, or `genome.input_build` for fragments-only input. `chr*` style alone does not distinguish hg19 from hg38.
- A config file (scripts/config.example.yaml) setting the annotation genome build and the route, pairing, QC, WNN, integration, MultiVI, and differential-abundance parameters.

## Outputs

- `report_scatac-multiome-analysis.pdf` — Generate the PDF with `pdf-report-generation` by default. When the user explicitly selects a compatible installed report-styling skill, use that provider for presentation only; keep every scientific result, artifact, infographic, review, and receipt requirement unchanged.
- `joint_cluster_celltype_assignment.csv` — the machine-readable joint result: one row per joint WNN cluster with cluster id, assigned cell type, top marker genes, and cell count
- `top_DA_peaks_per_celltype.csv` — per-cell-type ATAC differential-accessibility evidence produced by both the joint and scATAC-only routes when that block runs
- `joint_qc_scorecard.csv`, `modality_weights_summary.csv`, `atac_initial_cluster_summary.json`, and `atac_recalled_cluster_summary.json` — current-run evidence that keeps WNN clusters, ATAC-only cluster stages, and modality-weight means/medians distinct
- `integrated.h5mu` — paired RNA/ATAC object for the joint route
- `report_facts.json` — evidence-bearing claims, operational definitions, provenance, and validated denominator/completion partitions
- `run_receipt.json` and `delivery_authorization.json` — strict terminal receipt and its report/receipt-bound authorization

**Write the report to the results root under the name above.** Data tables, figures and intermediates go in `data/`, `figures/`, `tables/`. Note that `GenerateImage` strips directory components, so schematics always land at the root regardless of the path you pass it.

## Clarification Questions

1. **How should the run obtain its input bundle?** (select one)
   - `user_paired` — Use a user-provided paired 10x Multiome bundle; runtime branch ID `input_mode:user_paired`
2. **Which analysis route should run?** (select one)
   - `auto` — Detect the regime automatically (recommended); runtime branch ID `route_override:auto`
   - `joint` — Force the paired RNA+ATAC joint route; runtime branch ID `route_override:joint`
   - `atac_only` — Force the scATAC-only route; runtime branch ID `route_override:atac_only`

## Standard Workflow

**Single-invocation boundary.** Install every dependency before analysis, then invoke `scripts/run_all.sh` exactly once through `report_qc.run_bundled`. Derive `expected_outputs` from the selected branch IDs with `execution_contract.command_outputs_for_selected_branches`; never hand-select a subset. This records every current-run output hash needed by the stage-status receipt, including joint-route ATAC differential accessibility. The non-installing `scripts/preflight_dependencies.R` first checks route-discovery libraries, then checks the resolved analysis route before any downstream stage. Its branch-derived contract includes `biovizBase`, requires `miloR` and `SingleCellExperiment` only when a joint input has at least two biological replicates per condition, and otherwise preserves the documented Milo/pseudobulk skip. A failed or duplicate `run_all` event invalidates the run; do not install a dependency, clean outputs, and retry in place.

```python
from execution_contract import command_outputs_for_selected_branches
from report_qc import run_bundled

expected_outputs = command_outputs_for_selected_branches(selected_branch_ids)
run_bundled(
    ["bash", run_all_path, config_path],
    "scripts/run_all.sh",
    expected_outputs,
    invocation_id=run_invocation_id,
)
```

**Step 1 — Load and validate the input.** Enforce every item in `## Inputs`; stop with `not_computable` on a mismatch.

**Step 2 — Validate pairing and resolve the analysis route.** Run scripts/00_route.R to detect feature types and barcode overlap, compare an independent matrix/metadata/explicit input genome identity with the configured annotation build, check seqlevel style separately, and evaluate sample identity. An absent or ambiguous input build is a machine `not_evaluable` hard stop; never infer hg19 versus hg38 from shared `chr*` style.

**Step 3 — QC and normalize each modality.** Run scripts/01_ingest_qc.R for fragments-first ATAC QC (TSS enrichment, nucleosome signal, FRiP, fragment counts) and scripts/07_rna_qc.R for RNA QC. FRiP uses the authoritative fragment-unit pair for the input type: `atac_peak_region_fragments / atac_fragments` for Cell Ranger ARC or `peak_region_fragments / passed_filters` for Cell Ranger ATAC. Only non-ARC peak matrices may fall back to compatible peak-matrix fragment counts over independently counted fragments. Missing, partial, or unit-incompatible pairs fail closed.

**Step 4 — Cluster open chromatin with TF-IDF and LSI.** Run scripts/02_lsi_cluster.R: TF-IDF normalization, singular value decomposition (LSI), drop the depth-correlated first component, and build the ATAC neighbor graph, UMAP, and clusters.

**Step 5 — Recall peaks and score gene activity.** Run scripts/03_recall_peaks.R for per-cluster MACS3 peak recalling on the fragments and scripts/04_annotate.R for gene-activity scoring and marker-based annotation using the genome triad and the bundled PBMC panel.

**Step 6 — Build the WNN joint embedding.** Run scripts/08_wnn_joint.R: intersect paired barcodes, recompute RNA PCA and ATAC LSI, run FindMultiModalNeighbors, and produce a joint UMAP, joint clusters, and per-cell modality weights asserted to lie in the closed unit interval.

**Step 7 — Assemble the integrated object and integration metrics.** Run scripts/09_integrate_bridge.py: assemble an integrated .h5mu; compute kNN metrics on the exported WNN `weighted.nn` graph and coordinate silhouettes on the WNN UMAP; and bind both inputs to barcode order, shape, and SHA-256 evidence. Label metrics require labels independent of WNN clustering, while single-level/missing labels or batches emit explicit non-evaluable/degenerate states instead of numeric scores.

**Step 8 — Test replicate-aware differential abundance.** Run scripts/10_diff_abundance.R: build Milo neighborhoods on the joint embedding and test differential abundance with spatial FDR when at least two biological replicates per condition exist, add pseudobulk RNA differential expression and preserved per-cell-type ATAC differential accessibility, and skip and flag rather than fabricate when replication is insufficient.

**Step 9 — Generate and validate figures.** Render only results produced by the preceding validated steps, write `figures/manifest.json`, then validate every declared artifact before facts or the receipt can use it. Render the integration-metrics figure only when at least one metric has a finite numeric score. When every integration metric is non-evaluable or structurally degenerate, do not create an annotation-only plotting frame: account for step 7 in the manifest with `"file": null` and the reason `All integration metrics are non-evaluable or structurally degenerate; no finite numeric score is available to plot.` Keep the individual statuses and reasons in the scorecard and summarize them in report prose or a compact table.

```python
from report_qc import assert_figures
figures = assert_figures("figures/manifest.json")
```

## Figures

One representative figure per result-producing step, showing **this run's actual result** — not a schematic, not an illustrative example. Every figure needs a caption stating what it shows. If a step genuinely has nothing to plot, replace its row with a one-line reason. A caption is required at run time, so fill these in before the first run.

| Step | File                                             | What it must make visible                                                                                                                                                                                                                                                                |
| ---- | ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 3    | `figures/figure_3_qc_and_normalize.png`        | Per-modality QC after filtering: RNA counts, features, and percent mitochondrial; ATAC TSS enrichment, nucleosome signal, and FRiP, with retained-cell thresholds.                                                                                                                       |
| 4    | `figures/figure_4_cluster_open_chromatin.png`  | ATAC LSI UMAP colored by cluster, computed after dropping the depth-correlated first LSI component.                                                                                                                                                                                      |
| 5    | `figures/figure_5_recall_peaks_and.png`        | Per-cluster MACS3-recalled peak counts alongside the gene-activity marker evidence used to label each cluster.                                                                                                                                                                           |
| 6    | `figures/figure_6_build_the_wnn.png`           | WNN joint UMAP colored by joint cluster, with the per-cell RNA/ATAC modality-weight distribution (weights in the closed unit interval).                                                                                                                                                  |
| 7    | `figures/figure_7_assemble_the_integrated.png` | Joint QC scorecard when at least one WNN-graph or WNN-UMAP metric has a finite numeric score; label individual unavailable metrics. If none is numeric, use a reasoned `file: null` manifest entry and report the statuses as text or a compact table instead of rendering empty axes. |
| 8    | `figures/figure_8_test_replicate_aware.png`    | Milo neighborhood differential-abundance beeswarm across conditions with spatial-FDR significance.                                                                                                                                                                                       |

The report reads this inventory from `report_facts.json`'s `figures` array rather than restating it, so it cannot claim a figure that was never produced.

**Step 10 — Write `report_facts.json` from the runtime payload.** The workflow must produce `facts_payload.json` with every headline, denominator, and partition-member field declared in `skill_contract.json`. Semantic gates run before the facts artifact is written, so a failing run never produces claims a report could quote.

```python
from report_qc import write_facts_from_artifact
write_facts_from_artifact('report_facts.json', source='facts_payload.json', figures=figures, contract="skill_contract.json")
```

**Step 10a — Derive pipeline-status prose from current-run evidence.** Write the package-owned stage receipt after `run_all.sh` and before report assembly. It derives required outputs and block applicability from `selected_branch_ids` plus the recorded replicate-design gate, reconciles the overall command event with the inner Milo, pseudobulk, ATAC-DA, and MultiVI status artifacts, and hash-binds every required selected-branch output. A documented single-sample Milo/pseudobulk skip is `not_applicable`, not failed or missing. Use the canonical sentence verbatim in Results; never infer “all nine stages succeeded” from the shell exit code alone.

```python
from report_qc import write_stage_status_receipt, derive_stage_status_statement
write_stage_status_receipt(selected_branch_ids, "stage_status_receipt.json")
stage_status_sentence = derive_stage_status_statement("stage_status_receipt.json")
```

**Step 10b — Derive report facts from current artifacts.** Use both canonical sentences from `scripts/report_grounding.py` verbatim. They distinguish mean from median WNN modality weights and the initial-ATAC, recalled-ATAC, and later WNN cluster counts. Never infer these values from plot appearance or rewrite the labels by hand. WNN weights describe contributions within the joint graph; without an explicit unimodal comparator they cannot show that WNN is preferred, superior, or more informative. Similar pre/post peak-recall cluster counts are descriptive and cannot establish structural stability without a cellwise stability statistic. The report gate rejects either overclaim.

```python
from pathlib import Path
from report_grounding import derive_report_grounding
grounding = derive_report_grounding(Path(results_root))
modality_weight_sentence = grounding["sentences"]["modality_weights"]
cluster_stage_sentence = grounding["sentences"]["cluster_stages"]
```

### Step 11 — Final report (MANDATORY TERMINAL STEP)

**The run is not complete until this step has produced `report_scatac-multiome-analysis.pdf` at the results root.**
Generate the PDF with `pdf-report-generation` by default. When the user explicitly selects a compatible installed report-styling skill, use that provider for presentation only; keep every scientific result, artifact, infographic, review, and receipt requirement unchanged.

Resolve any override only from an affirmative user message in the immutable execution transcript. A tenant, customer, project, account, or dataset name is context, not a style selection. Before rendering, load the resolved provider's complete `SKILL.md` and applicable assets and follow them as a unit; never reconstruct a provider from marker colors or a sample receipt. Missing, malformed, conflicting, or unavailable provider evidence blocks the run rather than falling back.

Produce one combined PDF with a short narrative. Place the qualitative GenerateImage infographic near the beginning as the first substantive visual; rendered page images are inspection evidence only, never substitutes for the PDF. End the task with a concise conclusion and links to the PDF and supporting artifacts, not a bare file listing.

The PDF must use these visible top-level sections in this order, adapting their contents to this skill rather than adding generic filler:

1. `Task Context` — the research or practitioner question, supplied inputs, scope, and decision the output informs.
2. `Methods & Sources` — Methods & Sources covers data provenance, filtering, parameters, software, and the analysis design; Results reports validated quantitative findings and figures.
3. `Results` — the run's actual outputs and evidence, never a description of what the skill could do.
4. `Conclusions & Interpretation` — supported takeaways, their practical meaning, and appropriate next steps.
5. `Limitations` — run-specific uncertainty, missing coverage, failed or unavailable checks, and claims the evidence does not support.

Include the method citations from `references/citations.md` as a numbered References section, cited inline (e.g., [1], [2]) where each method is first described in Methods & Sources. Also cite the input dataset accession (from `config.yaml -> report.dataset_accession`) as the final numbered reference. Include a compact output-artifact table. Empty boilerplate does not satisfy a section.

Generate `infographic.png` with Biomni `GenerateImage` before report assembly. Read `assets/infographic_prompt.txt` and submit it verbatim: it is the package-fixed qualitative workflow prompt, contains no data/results/outcomes or branding, and includes the visible “not evidence that any step passed” disclaimer. Do not add numbers, result plots, weights, clusters, cell types, significance, completion, verification language, logos, watermarks, tables, dashboards, or report-page facsimiles. Immediately after the tool returns—and before any transformation or replacement—snapshot its prompt trace and pixels:

```python
from report_qc import record_generated_infographic
record_generated_infographic(
    "infographic.png",
    prompt_file="assets/infographic_prompt.txt",
    log_path="qc_run_log.json",
)
```

Load the generated image with ordinary `Read` and inspect the pixels directly. It must be workflow-only, contain no result-like plots or values and no outcome/completion claims, and visibly carry the disclaimer. If visual input is unavailable, state that limitation; do not claim a passing semantic review.

Render the PDF to a fresh workspace file named by `workspace_report_file`; do not open or truncate an existing PDF on the object-backed results mount. The `staged_copy` call below publishes the completed file under its declared results-root name.

Then verify the run and authorize delivery with one `finalize_delivery` call. It runs the strict receipt, verifies the report facts and exactly one successful `run_all` event, then creates `delivery_authorization.json` bound to the current report and receipt. `strict=False` is not a delivery path. Do not catch its `GateFailure`, advertise a failed receipt, author execution events, copy transcript identifiers, or retry the remote run. Visual inspection belongs to the agent and is not attested by the delivery authorization.

Record each answer from `## Clarification Questions` in `selected_branch_ids` using the displayed `<question_id>:<choice_id>` value. The receipt derives its required outputs only from those selected branches; do not union mutually exclusive branch artifacts.

```python
from report_qc import (assert_figures, authorize_delivery, outputs_for_selected_branches,
                       record_pdf_review, staged_copy, write_facts_from_artifact,
                       write_receipt)
selected_outputs = outputs_for_selected_branches(selected_branch_ids)
figures = assert_figures("figures/manifest.json")
write_facts_from_artifact(
    "report_facts.json", source="facts_payload.json", figures=figures,
    contract="skill_contract.json",
)
staged_copy(workspace_report_file, "report_scatac-multiome-analysis.pdf")
record_pdf_review(
    report_name="report_scatac-multiome-analysis.pdf", text_artifact=extracted_text_file,
    rendered_page_files=rendered_page_files,
)
receipt = write_receipt(
    report_name="report_scatac-multiome-analysis.pdf",
    figures=figures,
    figure_not_applicable_reason=None,
    bundled_files=['scripts/run_all.sh'],
    outputs=selected_outputs,
    infographics=["infographic.png"],
    qc_run_log="qc_run_log.json",
    strict=True,
)
authorization = authorize_delivery(
    "report_scatac-multiome-analysis.pdf",
    receipt_path=receipt,
)
print(authorization["terminal_message"])
```

The receipt is `run_receipt.json` at the **results root**, not beside this SKILL.md. It records `execution_contract_satisfied`, `outputs_appeared`, `report_at_results_root`, `figure_contract_satisfied`, `facts_artifact_verified`, `report_style_verified`, `report_content_within_margins`, `text_extracted`, `pages_rendered`, `visual_review_attested`, `report_sections_present`, `stage_status_prose_verified`, `report_facts_grounded`, `source_assertions_verified`, `infographic_lineage_verified`, and `infographic_semantics_verified`. Every required true outcome must carry non-empty package-derived evidence, and `execution_contract_satisfied` requires exactly one raw successful `run_all.sh` event rather than a failed attempt followed by a retry. Lineage must be fully verified. `visual_review_attested` and `infographic_semantics_verified` are `not_applicable`: the receipt makes no visual-quality claim. Only the package-generated `delivery_authorization.json` permits terminal success.

**Do not write this file by hand.** `check_skill.py --require-run-receipt` requires the schema marker and per-outcome evidence, so a hand-written block of `true`s fails. Whatever did not hold is recorded `false` with a `<key>_reason`; fix the run rather than the receipt.

### Step 12 — Validate the infographic embedding claim against the receipt (MANDATORY)

The report narrative, the closing summary, and any infographic embedding/identity sentence must be **derived from `run_receipt.json`**, never asserted from memory. Reserve "pixel-identical" — or any equivalent decoded-pixel-identity phrasing — for a completed decoded-pixel comparison that bound the generated infographic to exactly one first image on page 1 with both hashes recorded, i.e. only when `infographic_lineage_verified` is `true` with a matching evidence item. If the comparison did not run (`false`/not-evaluable) or is `not_applicable`, state that honest status and its reason; the count-only `figures_embedded` result does **not** establish image identity.

```python
from embedding_statement import derive_embedding_statement, assert_embedding_prose_supported
claim = derive_embedding_statement("run_receipt.json")   # honest sentence; strong phrase only if verified
# Use claim["sentence"] verbatim for the embedding statement, then gate the finished prose before finishing:
assert_embedding_prose_supported(final_report_text, "run_receipt.json")
assert_embedding_prose_supported(closing_summary_text, "run_receipt.json")
```

`assert_embedding_prose_supported` raises if any pixel-identity claim is unproven, and also if the receipt records `true` without the backing decoded-pixel hashes (a receipt may not out-run its evidence). Do not weaken the production gate, disable the infographic requirement, or hand-author lineage evidence to make the claim pass: when platform join IDs are unavailable the honest state is `not_evaluable`, and a fresh supported-environment run is required before promotion.

## Bundled implementation

- `scripts/00_route.R` and `scripts/_route.R` establish modality, pairing, genome, and supported-route identity before integration.
- `scripts/01_ingest_qc.R` through `scripts/10_diff_abundance.R` implement QC, ATAC analysis, paired WNN integration, metrics, and replicate-aware inference.
- `scripts/stage_status_grounding.py` binds report prose to actual stage outcomes; `scripts/table_layout.py` protects wide diagnostic tables from page clipping.
- `scripts/report_qc.py` owns terminal scientific, lineage, source, style, and review gates.

## Scientific caveats

- Treating a paired Multiome as scATAC-with-RNA-annotation, or integrating both modalities before confirming they came from the same cells and genome build, invents structure: the pairing and genome-identity gate must pass first, or the run stops and hands off rather than fabricating a joint embedding. Evidence: `report_facts.json (pairing_barcode_overlap)`.
- Per-cell modality weights are asserted to lie in the closed unit interval and describe how much each modality drove the joint neighbor graph; a near-degenerate weight distribution means one modality dominated and the joint embedding is close to a single-modality result. They do not establish that WNN is preferred, superior, or more informative than either modality without an explicit unimodal comparison. Evidence: `modality_weights_summary.csv`.
- Initial and recalled-peak cluster counts describe two analysis stages but do not establish peak-recall structural stability without a cellwise correspondence or stability statistic. Evidence: `atac_initial_cluster_summary.json` and `atac_recalled_cluster_summary.json`.
- MultiVI outputs are model predictions, not measurements, and run only when assumptions are met; otherwise a documented skip is recorded rather than a fabricated latent space. Evidence: `multivi_status.json`.
- Milo differential abundance and pseudobulk differential expression require at least two biological replicates per condition; with insufficient replication they are skipped and flagged, never reported with a fabricated FDR. Evidence: `diffabund_status.json`.
- The genome gate compares an independent matrix/metadata/explicit input build with the configured annotation build and checks seqlevel style separately. Missing, ambiguous, or mismatched identity hard-stops; `chr*` style alone is never treated as hg19/hg38 identity. Evidence: `provenance_manifest.json (genome.comparison_status, genome.identity_violations)`.
- Label-purity and label-silhouette metrics require an independent reference label; marker annotations derived from WNN clusters are never reused to validate those clusters. Single-level batch/label inputs are recorded as non-evaluable or structurally degenerate. Evidence: `joint_qc_scorecard.json (integration_metrics.*_status, *_reason)`.
- This skill is hypothesis-generating: joint clusters, integration metrics, and neighborhoods are exploratory and are not validated at any threshold. Evidence: `report_facts.json`.
- Infographic embedding/identity statements are derived from the receipt: "pixel-identical" is used only for a completed decoded-pixel comparison that bound the generated image to one page-1 embedding with both hashes recorded; otherwise the honest not-evaluable/not-applicable status and its reason are stated, and the count-only `figures_embedded` result is never treated as image identity. Evidence: `run_receipt.json (infographic_lineage_verified)`.

Each caveat must name the artifact field or the number that says whether it fired. An unbound caveat is prose; a bound one is a gate.

## Evidence Tier

Contract maturity: `evidence_validated`. Run-specific support is limited to the sealed inputs, route and dependency provenance, facts, figures, report, and receipt.

## Data Sources & Licenses

- EnsDb.Hsapiens.v86 (2.99.0): https://bioconductor.org/packages/EnsDb.Hsapiens.v86/ — license Artistic-2.0; commercial use `allowed` (Artistic-2.0 is an OSI-approved permissive license permitting commercial use; Bioconductor package DESCRIPTION.); included `false`; verify with `references/licenses.md`.
- BSgenome.Hsapiens.UCSC.hg38 (1.4.5): https://bioconductor.org/packages/BSgenome.Hsapiens.UCSC.hg38/ — license Artistic-2.0 (package); UCSC hg38 data terms (sequence); commercial use `allowed` (Package code is Artistic-2.0; the underlying UCSC hg38/GRCh38 assembly is freely available including for commercial use per UCSC data-use policy. Source vs assembly-data terms recorded separately.); included `false`; verify with `references/licenses.md`.
- CellMarker 2.0 (2.0): http://bio-bigdata.hrbmu.edu.cn/CellMarker/ — license Ambiguous: source paper CC-BY-NC-4.0; some mirrors CC0; site shows university copyright with no explicit reuse grant; commercial use `not_checked` (Conflicting signals: the CellMarker 2.0 NAR paper is CC-BY-NC-4.0 (non-commercial), aggregators list CC0, and the CellMarker2 website shows a Harbin Medical University copyright with no explicit data-reuse license; commercial reuse could not be unambiguously verified.); included `false`; verify with `references/licenses.md`.
- 10x Genomics PBMC 10k Multiome public dataset (2.0.0): https://www.10xgenomics.com/datasets — license 10x Genomics dataset terms; commercial use `no_prohibition_found` (10x Genomics public datasets are provided for research use under 10x dataset terms with no redistribution prohibition found for internal analysis; never shipped.); included `false`; verify with `references/licenses.md`.
- Signac (>=1.12): https://github.com/stuart-lab/signac — license MIT; commercial use `allowed` (MIT license (GitHub LICENSE); commercial use permitted.); included `false`; verify with `references/licenses.md`.
- Seurat (>=5.0): https://github.com/satijalab/seurat — license MIT; commercial use `allowed` (MIT license (GitHub LICENSE); WNN implementation.); included `false`; verify with `references/licenses.md`.
- MACS3 (>=3.0): https://github.com/macs3-project/MACS — license BSD-3-Clause; commercial use `allowed` (BSD 3-Clause (GitHub LICENSE); commercial use permitted.); included `false`; verify with `references/licenses.md`.
- presto (1.0.0): https://github.com/immunogenomics/presto — license GPL-3.0-only; commercial use `allowed` (GPL-3.0-only (bioconda r-presto; GitHub); copyleft, commercial use allowed under GPL terms.); included `false`; verify with `references/licenses.md`.
- scvi-tools (MultiVI) (>=1.1): https://github.com/scverse/scvi-tools — license BSD-3-Clause; commercial use `allowed` (BSD 3-Clause (GitHub LICENSE); MultiVI sensitivity route.); included `false`; verify with `references/licenses.md`.
- mudata / muon (>=0.2): https://github.com/scverse/mudata — license BSD-3-Clause; commercial use `allowed` (BSD 3-Clause (GitHub LICENSE); .h5mu container.); included `false`; verify with `references/licenses.md`.
- scanpy (>=1.9): https://github.com/scverse/scanpy — license BSD-3-Clause; commercial use `allowed` (BSD 3-Clause (GitHub LICENSE).); included `false`; verify with `references/licenses.md`.
- scib-metrics (>=0.5): https://github.com/YosefLab/scib-metrics — license BSD-3-Clause; commercial use `allowed` (BSD 3-Clause (GitHub LICENSE / pyproject); scIB-style integration metrics.); included `false`; verify with `references/licenses.md`.
- miloR (>=1.10): https://github.com/MarioniLab/miloR — license GPL-3; commercial use `allowed` (GPL-3 (Bioconductor DESCRIPTION); copyleft, commercial use allowed under GPL terms.); included `false`; verify with `references/licenses.md`.

## Resource Identity

Resolve `genome.build` to the registry triad, then compare it against an independent build identity from the 10x matrix, metadata, or `genome.input_build`. Record the evidence, canonical input build, comparison status, seqlevel-style status, and derived violation rows in `provenance_manifest.json`. Missing/ambiguous identity is `not_evaluable`; disagreement is failed; both stop before facts or report assembly.

## Common Issues

| Symptom                                                  | Cause                                                                                                                      | Fix                                                                                                                                                                                                                        |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Route resolves to `stop` with a barcode-overlap reason | RNA and ATAC barcodes do not overlap — not truly paired, or mismatched files                                              | Confirm both modalities come from the same 10x Multiome run; verify the h5 carries `Gene Expression` + `Peaks` and the fragments match; use `single-cell-multimodal-integration` for deliberate diagonal integration |
| Route directs the input elsewhere                        | CITE-seq (`Antibody Capture`) or two separate single-modality inputs                                                     | Use `single-cell-multimodal-integration`; this skill only analyzes genuinely paired RNA+ATAC                                                                                                                             |
| `stop` with a genome not-evaluable/mismatch reason     | No independent input build was provided, declarations disagree, or the canonical input build differs from `genome.build` | Preserve `/matrix/features/genome`, provide one unique metadata genome, or set the actual `genome.input_build`; align input and annotation rather than inferring from `chr*`                                         |
| Milo / pseudobulk DA skipped and flagged                 | fewer than two biological replicates per condition                                                                         | Add replicates, or accept the documented skip in `diffabund_status.json`; DA is never fabricated                                                                                                                         |
| MultiVI skipped                                          | fewer cells than `multivi.min_cells`, or a modality missing                                                              | WNN remains the default embedding; lower `multivi.min_cells` only with justification (`multivi_status.json` records the reason)                                                                                        |
| Missing `.tbi` error at ingest                         | fragments file lacks a tabix index                                                                                         | `bgzip` the fragments if needed, then `tabix -p bed atac_fragments.tsv.gz`                                                                                                                                             |

## Suggested Next Steps

- Add motif / TF-activity analysis (chromVAR) and peak-to-gene linkage on the integrated object.
- Confirm exploratory joint clusters and differential abundance in an independent replicate cohort before treating them as validated.
- Feed joint RNA differential-expression results into `functional-enrichment-from-degs` for pathway context.
- For trajectory structure on the joint embedding, continue with `scrna-trajectory-inference`.

## Related Skills

- `single-cell-multimodal-integration` — analyze CITE-seq and separately measured multimodal inputs; this skill stops and points there rather than force-integrating.
- `scrnaseq-seurat-core-analysis` / `scrnaseq-scanpy-core-analysis` — RNA-only single-cell analysis.
- `chip-atlas-peak-enrichment` — external ChIP/ATAC peak-set context for called peaks.
- `functional-enrichment-from-degs` — enrichment on joint RNA differential expression.
