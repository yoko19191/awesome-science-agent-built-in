---
name: spatial-transcriptomics
description: Detects Visium vs Xenium from an explicit manifest and file signature
  and stops on ambiguous input. Runs the preserved Visium spot-QC, clustering, and
  spatially-variable-gene workflow. Adds Xenium transcript and negative-control QC
  with segmentation assessment. Shares niches, SVGs, replicate-aware DE, and hypothesis-level
  CCI across platforms. Use to analyze 10x Visium or Xenium spatial transcriptomics
  end to end.
category: transcriptomics
visibility: public
starting-prompt: Characterize spatial transcriptomic quality, cell populations, tissue
  neighborhoods, spatial expression patterns, and candidate cell-cell interactions.
---
## Input contract

Before any scientific computation, use the documented production entrypoint to seal every caller-selected input in `input_selection.json` (schema 2.0). Each input must declare `uploaded` or `downloaded` origin, the requested source, a canonical resolved path, byte count, SHA-256, and acquisition provenance. Uploaded inputs record `acquisition.status=not_applicable`; downloaded inputs require complete source, tool, tool version, command/API, and retrieval time.

The entrypoint never infers a source, chooses a substitute, or reads package-owned data. Symlink escapes and package-resolved paths are rejected. The result root must be external, separate from all inputs, and nonexistent or empty before sealing. `package_data_used` is always false. The manifest self-hash is SHA-256 over canonical UTF-8 JSON with sorted keys and comma/colon separators after omitting `manifest_sha256`.

<!-- archetype: analysis-workflow -->

<!-- contract: evidence-v1 -->

# Spatial Transcriptomics

## When to Use This Skill

Use to analyze 10x Visium or Xenium spatial transcriptomics end to end.

## Why This, Not The Obvious Thing (READ FIRST)

A competent practitioner often infers the platform from filenames or applies one QC recipe to both assays; this router refuses filename inference and keeps Visium spot-QC and Xenium control/segmentation-QC strictly separate.

## Inputs

- A dataset/ directory containing spatial_manifest.json and samples.csv.
- For Visium: a Space Ranger matrix (filtered_feature_bc_matrix.h5 or matrix directory) plus a spatial/ folder with tissue positions and scalefactors_json.json.
- For Xenium: transcripts.parquet, a cell_feature_matrix, cells.parquet, a morphology image, and negative-control features.
- samples.csv with sample_id and condition columns for replicate-aware differential expression.

## Outputs

- `report_spatial_transcriptomics.pdf` — the one canonical provider-styled PDF; pdf-report-generation is the default provider and an explicit compatible provider may replace only its presentation branch
- `platform_decision.json` — the routing decision: platform, confidence, the input signature used, and `filename_inference: false`
- `qc.csv` — one row per cell (Xenium) or spot (Visium) with QC metrics and a pass/fail flag; the Visium branch records the resolved semantic gene-name source, matched mitochondrial prefix, mitochondrial feature count, and spots with mitochondrial counts; the Xenium branch adds `control_fraction`
- `niches.csv` — one row per cell/spot: assigned spatial niche, cell type, and platform
- `spatially_variable_genes.csv` — one row per gene: Moran's I plus every squidpy p-value/FDR column and platform (control features excluded); the headline count uses the authoritative asymptotic-normal `pval_norm_fdr_bh < 0.05` declared in `skill_contract.json:facts.svg_significance` (the permutation `pval_sim_fdr_bh` is present but is not the headline)
- `differential_expression.csv` — one row per gene: log2FC, p-value, BH-FDR, contrast, and DE status (blocked when replicates are insufficient)
- `cci.csv` — one row per cell-type pair: neighborhood-enrichment z, a `colocalized` flag, and `evidence_level = hypothesis`
- `provenance.json` — platform, parameters, thresholds (copied from the exact applied parameter object — never a hard-coded default), tool versions, `executed_dependencies`, and `code_provenance` script hashes; Visium records the resolved mitochondrial rule and evidence, while the Xenium `min_transcript_qv` block is written only for a Xenium run
- `adata_processed.h5ad` — the standardized processed spatial object for downstream reuse
- `dependency_receipt.json` — scientific executed dependencies plus conditional presentation evidence: ReportLab for the package-default branch or the selected installed provider source/hash for an explicit branch
- `figures/figure_3..7` and `figures/manifest.json` — four required per-run figures plus a DE volcano when the contrast is estimable; blocked DE is recorded as a one-line manifest reason with no placeholder image
- `scientific_visual_manifest.json` — hashes of the scientific figures produced by `scripts/run_spatial.py prepare`
- `report_content.json` and `report_structure.json` — provider-neutral facts, scientific provenance, required sections, captions, and exact figure hashes shared by every renderer
- `report_render_plan.json` — immutable provider selection, selection evidence, content hashes, and packaged-renderer or installed-provider source hash
- `report_render_provenance.json` and `visual_manifest.json` — conditional renderer/dependency evidence and final hashes for the prepared figures plus the canonical PDF
- `spatial_outputs_manifest.json` — results-root index for a two-platform sample, binding each required artifact to its per-platform namespaced path
- `report_facts.json` — evidence-bearing claims, operational definitions, provenance, and validated denominator/completion partitions

**Write the report to the results root under the name above.** Data tables, figures and intermediates go in `data/`, `figures/`, `tables/`. Note that `GenerateImage` strips directory components, so schematics always land at the root regardless of the path you pass it.

## Clarification Questions

1. **Which platform is the input, or should the router auto-detect it from the manifest and file signature?** (select one)
   - `auto` — Auto-detect from manifest + input signature (recommended); stop if ambiguous; runtime branch ID `platform:auto`
   - `visium` — Force the Visium (Space Ranger / spot) branch; runtime branch ID `platform:visium`
   - `xenium` — Force the Xenium (imaging in situ) branch; runtime branch ID `platform:xenium`
2. **Run replicate-aware differential expression between conditions?** (select one)
   - `yes` — Yes, if samples.csv has >=2 biological replicates per condition; runtime branch ID `de:yes`
   - `no` — No, stop after niches and spatially variable genes; runtime branch ID `de:no`

## Standard Workflow

**Scientific preparation and PDF presentation are separate recorded phases.** `scripts/run_spatial.py prepare <bundle_dir> <out_dir>` performs Steps 2–7 and renders only the applicable data-bearing figures with `scripts/spatial_figures.py`. It writes `scientific_visual_manifest.json`; it does not import ReportLab, choose a report provider, or create a PDF. Run it through `report_qc.run_bundled` so scientific outputs and figure hashes bind to the receipt. After the facts gate, `scripts/report_content.py` creates the single provider-neutral content object. `scripts/finalize_report.py plan` resolves the provider only from immutable user messages and freezes the selected provider source/hash. `scripts/finalize_report.py finalize` then runs exactly one authorized presentation branch and refuses to overwrite an existing canonical PDF.

```python
import sys
from report_qc import run_bundled
DATA_OUTPUTS = ["platform_decision.json", "qc.csv", "niches.csv",
                "spatially_variable_genes.csv", "differential_expression.csv", "cci.csv",
                "provenance.json", "adata_processed.h5ad", "facts_payload.json"]
FIGURE_OUTPUTS = ["figures/figure_3_run_the_platform.png", "figures/figure_4_standardize_to_the.png",
                  "figures/figure_5_detect_spatially_variable.png",
                  "figures/figure_7_infer_hypothesis_level.png", "figures/manifest.json"]
COMMAND_OUTPUTS = DATA_OUTPUTS + FIGURE_OUTPUTS + ["scientific_visual_manifest.json"]
run_bundled([sys.executable, "scripts/run_spatial.py", "prepare", "dataset", "."],
            "scripts/run_spatial.py", COMMAND_OUTPUTS, invocation_id="spatial-prepare")
```

**Two-platform bundle.** When the sample contains both a Visium and a Xenium dataset, run `prepare` once per platform into a namespaced subdirectory (`visium_run/`, `xenium_run/`), then run the content, plan, and finalization phases independently inside each subdirectory. Emit the results-root index with `scripts/run_spatial.py manifest . --run Visium=visium_run --run Xenium=xenium_run`. The index `spatial_outputs_manifest.json` binds every required artifact to its explicit per-platform path so both runs resolve from the results root without basename guessing.

```bash
    --min-genes 10 --min-cells 2 --n-top-genes 50 --n-pcs 20 --svgs-n-perms 50
```

**Step 1 — Load and validate the input.** Enforce every item in `## Inputs`; stop with `not_computable` on a mismatch.

**Step 2 — Validate and route the platform from the manifest and input signature.** Run scripts/platform_router.py route_platform() to decide Visium vs Xenium from spatial_manifest.json and the input-file signature, and write platform_decision.json. Never infer platform from a filename. Stop with not_computable on ambiguous, mixed, incomplete, or filename-only bundles.

**Step 3 — Run the platform-specific QC branch.** For Visium, run `scripts/spatial_workflow.py:run_spatial_analysis()` for spot and mitochondrial QC. Resolve mitochondrial features from semantic gene names, preferring annotated gene-name columns before `var_names`, with human `MT-` and mouse `mt-` prefixes. Record the resolved source, matched prefixes, number of mitochondrial features, and spots with mitochondrial counts in `qc.csv`, `facts_payload.json`, and `provenance.json`. Stop as `not_computable` if no mitochondrial feature matches or if matched mitochondrial features have zero counts in every spot; an all-0% mitochondrial metric must never silently pass. For Xenium, run `scripts/xenium_workflow.py:run_xenium_analysis()` to separate negative-control features, apply the recorded transcript-quality convention, and assess segmentation quality. Control probes never enter biological analysis. Write `qc.csv`.

**Step 4 — Standardize to the common spatial object and detect niches.** Standardize the QC'd branch object with scripts/spatial_common.py (recording the platform on every observation), build the spatial graph, and detect spatial neighborhoods/niches with scripts/shared_downstream.py. Write niches.csv.

**Step 5 — Detect spatially variable genes.** Run Moran's I spatial autocorrelation on genes only (control features excluded) with scripts/shared_downstream.py and write spatially_variable_genes.csv.

**Step 6 — Run replicate-aware differential expression.** Aggregate to per-sample pseudobulk and test conditions with BH-FDR; require >=2 biological replicates per condition or BLOCK to avoid pseudoreplication. Write differential_expression.csv.

**Step 7 — Infer hypothesis-level cell-cell interactions.** Compute neighborhood co-localization between cell types as spatial-adjacency hypotheses (no causal or ligand-receptor direction) with scripts/shared_downstream.py and write cci.csv, plus provenance.json capturing platform, parameters, thresholds, and versions.

**Step 8 — Validate the packaged figures.** The applicable figures and `figures/manifest.json` were rendered by the packaged `prepare` route (`scripts/spatial_figures.py`) from this run's own artifacts — not by ad-hoc plotting code. Validate every declared artifact or reason before facts or report content can use it. Finalization requires every produced figure to match both `scientific_visual_manifest.json` and the hashes embedded in `report_content.json`.

```python
from report_qc import assert_figures
figures = assert_figures("figures/manifest.json")
```

## Figures

One representative figure per result-producing step, showing **this run's actual result** — not a schematic, not an illustrative example. Every figure needs a caption stating what it shows. If a step genuinely has nothing to plot, replace its row with a one-line reason. A caption is required at run time, so fill these in before the first run.

| Step | File                                                        | What it must make visible                                                                                                                                                         |
| ---- | ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 3    | `figures/figure_3_run_the_platform.png`                   | Per-cell (Xenium) or per-spot (Visium) QC distributions with the negative-control-probe rate marked, so a failing or spiked-control run is obvious.                               |
| 4    | `figures/figure_4_standardize_to_the.png`                 | The tissue map colored by spatial niche, so niche boundaries and their cell-type composition are visible.                                                                         |
| 5    | `figures/figure_5_detect_spatially_variable.png`          | Top spatially variable genes ranked by Moran's I with BH-FDR, so the strongest spatial patterns stand out.                                                                        |
| 6    | `figures/figure_6_run_replicate_aware.png` when estimable | Volcano of replicate-aware pseudobulk DE (log2FC vs BH-FDR) with called genes labeled. If DE is blocked, omit the image and record a one-line reason in the manifest and Results. |
| 7    | `figures/figure_7_infer_hypothesis_level.png`             | Cell-type neighborhood co-localization (enrichment z), with co-localized pairs flagged as hypothesis-level.                                                                       |

The report reads this inventory from `figures/manifest.json` rather than restating it, so it cannot claim a figure that was never produced or omit a recorded reason.

**Step 9 — Write `report_facts.json` from the runtime payload.** The workflow must produce `facts_payload.json` with every headline, denominator, and partition-member field declared in `skill_contract.json`. Semantic gates run before the facts artifact is written, so a failing run never produces claims a report could quote.

```python
from report_qc import write_facts_from_artifact
write_facts_from_artifact('report_facts.json', source='facts_payload.json', figures=figures, contract="skill_contract.json")
```

### Step 10 — Final report (MANDATORY TERMINAL STEP)

**The run is not complete until this step has produced `report_spatial_transcriptomics.pdf` at the results root.**
Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references (cited inline with [N] markers), and next steps from all of the analyses. Regenerate any figure whenever its underlying data changes.

For the bundled five-source catalog, cite `[1]` through `[5]` inline and render exactly the matching numbered bibliography entries in that same order. Do not emit an orphan inline marker, an unnumbered bibliography entry, or an additional reference that is absent from the package catalog.

First derive `report_content.json` and `report_structure.json` from the gated facts, scientific provenance, routing decision, figure captions, and current figure hashes. Both files are provider-neutral: `presentation` is null and the scientific objects are copied without restyling or recomputation.

Resolve the report-style provider from immutable user messages. If no provider was explicitly selected, the default `pdf-report-generation` provider owns the canonical PDF and `scripts/spatial_report.py` renders `report_content.json`. If a compatible provider was explicitly selected, that explicit provider owns the same canonical filename, must render the exact frozen `report_content.json`, and the bundled `scripts/spatial_report.py` branch must not run. Never infer a provider from customer, tenant, project, or account context. A conflicting, revoked-after-plan, unavailable, incompatible, or mutated explicit provider fails closed; there is no fallback after an explicit selection.

Produce one combined PDF with a short narrative. rendered page images are inspection evidence only, never substitutes for the PDF. End the task with a concise conclusion and links to the PDF and supporting artifacts, not a bare file listing.

The PDF must use these visible top-level sections in this order, adapting their contents to this skill rather than adding generic filler:

1. `Task Context` — the research or practitioner question, supplied inputs, scope, and decision the output informs.
2. `Methods & Sources` — Methods & Sources covers data provenance, filtering, parameters, software, and the analysis design; Results reports validated quantitative findings and figures.
3. `Results` — the run's actual outputs and evidence, never a description of what the skill could do.
4. `Conclusions & Interpretation` — supported takeaways, their practical meaning, and appropriate next steps.
5. `Limitations` — run-specific uncertainty, missing coverage, failed or unavailable checks, and claims the evidence does not support.
6. `References` — the cited scientific and platform sources.
7. `Output Artifacts` — a compact table of the bound deliverables.

Empty boilerplate does not satisfy a section.

Generate one qualitative workflow infographic via the **Biomni GenerateImage tool** using the exact fixed prompt in `assets/infographic_prompt.txt`; read the file immediately before the call and do not edit or paraphrase it. The infographic must not contain run-specific values or resemble a table, dashboard, report page, or result plot. Give the same file to the selected provider for page 1 and record its filename in `write_receipt`. If transcript join IDs are unavailable, the infographic lineage outcome is honestly non-passing and must be disclosed.

Run every packaged phase through `run_bundled`. `finalize_report.py plan` freezes content hashes, immutable selection evidence, and either the packaged default renderer hash or the explicit installed-provider source/hash. On the default-provider branch, `finalize` invokes `spatial_report.py` itself. On the explicit-provider branch, render to a workspace staging path through the selected provider first and pass that path with `--provider-rendered-report`; the finalizer verifies provider markers, required sections and bound claims, scientific figure hashes, provider/source immutability, and conditional dependency provenance before it creates the canonical PDF. It refuses any pre-existing canonical PDF or finalization sidecar.

The finalizer's only publication primitive is `staged_copy(candidate_pdf, "report_spatial_transcriptomics.pdf")`, implemented in `finalize_report.py` with exclusive-create semantics: unlike the legacy helper, it never unlinks or replaces a pre-existing destination. Thus pdf-report-generation remains the safe package default while an explicitly selected compatible provider supplies different staged bytes through the same immutable publication boundary.

```python
import json
import sys
from report_qc import run_bundled

run_bundled(
    [sys.executable, "scripts/report_content.py", "--results-dir", "."],
    "scripts/report_content.py",
    ["report_content.json", "report_structure.json"],
    invocation_id="spatial-report-content",
)
run_bundled(
    [sys.executable, "scripts/finalize_report.py", "plan", "--results-dir", "."],
    "scripts/finalize_report.py",
    ["report_render_plan.json"],
    invocation_id="spatial-report-plan",
)
render_plan = json.load(open("report_render_plan.json", encoding="utf-8"))
finalize_argv = [sys.executable, "scripts/finalize_report.py", "finalize",
                 "--results-dir", ".", "--workspace-dir", "/workspace",
                 "--infographic", "spatial_summary_infographic.png"]
if render_plan["render_branch"] == "explicit_provider":
    # The explicitly selected installed provider must render the exact frozen
    # report_content.json to this staging path without rerunning the science.
    provider_staged_pdf = "/workspace/provider_spatial_report.pdf"
    finalize_argv += ["--provider-rendered-report", provider_staged_pdf]
run_bundled(
    finalize_argv,
    "scripts/finalize_report.py",
    ["report_spatial_transcriptomics.pdf", "report_render_provenance.json",
     "visual_manifest.json"],
    invocation_id="spatial-report-finalize",
)
```

Then verify the run and write its receipt with a single call. `write_receipt` checks the selected provider's markers, the conditional render provenance, prepared and finalized visual manifests, scientific code and dependency provenance, report content, figures, PDF review evidence, and infographic lineage. Do not author execution events or copy transcript identifiers. **After the pages are rendered, open every `report_review/page-*.png` with the `Read` tool, visually inspect it, and record one page-specific observation per page in `visual_review_notes`.** The attestation string is advisory only and never makes `visual_review_attested` pass: native trace evidence must bind one image-inspection call/result pair to every current rendered page.

Record each answer from `## Clarification Questions` in `selected_branch_ids` using the displayed `<question_id>:<choice_id>` value. The receipt derives its required outputs only from those selected branches; do not union mutually exclusive branch artifacts.

```python
from report_qc import outputs_for_selected_branches, record_pdf_review, write_receipt
selected_outputs = outputs_for_selected_branches(selected_branch_ids)
# Inspection truth: open EACH rendered page with Read before writing the receipt, e.g.
#   Read(file_path="report_review/page-1.png")   # ... one per page
# and note one observation per page in visual_review_notes. write_receipt binds those Read
# call/result pairs to each page's SHA-256; without them visual_review_attested is not_evaluable.
record_pdf_review(
    report_name="report_spatial_transcriptomics.pdf", text_artifact=extracted_text_file,
    rendered_page_files=rendered_page_files,
    reviewed_page_numbers=reviewed_page_numbers,
    review_attestation=visual_review_notes,
)
write_receipt(
    report_name="report_spatial_transcriptomics.pdf",
    figures=figures,
    figure_not_applicable_reason=None,
    bundled_files=["scripts/run_spatial.py", "scripts/report_content.py",
                   "scripts/finalize_report.py"],
    outputs=selected_outputs,
    infographics=["spatial_summary_infographic.png"],
    qc_run_log="qc_run_log.json",
)
```

The receipt is `run_receipt.json` at the **results root** and uses `phylo-run-receipt/3`. `code_provenance_verified` covers only scientific preparation; `presentation_provenance_verified` separately binds the provider-neutral content, controller scripts, and exactly one renderer branch. `dependency_inventory_verified` records scientific imports plus ReportLab only for the default branch, or the installed explicit-provider source for the explicit branch. `visual_provenance_verified` requires both the preparation and finalization command events and refuses figure, PDF, provider-source, or manifest drift.

**Do not write this file by hand.** `check_skill.py --require-run-receipt` requires the schema marker and per-outcome evidence, so a hand-written block of `true`s fails. Whatever did not hold is recorded `false` with a `<key>_reason`; fix the run rather than the receipt.

## Scientific caveats

- A competent practitioner often infers the platform from filenames or applies one QC recipe to both assays; this router refuses filename inference and keeps Visium spot-QC and Xenium control/segmentation-QC strictly separate. Evidence: `platform_decision.json:filename_inference (always false); reason records the signature used`.
- Visium mitochondrial QC must resolve semantic gene names with human `MT-` or mouse `mt-` prefixes; absent matches or matched-but-all-zero counts stop as `not_computable`, because an all-0% result would silently disable the filter. Evidence: `report_facts.json:mitochondrial_qc`; `qc.csv:mitochondrial_gene_name_source,mitochondrial_matched_prefixes,n_mito_features,n_spots_with_mito_counts`; `provenance.json:mitochondrial_qc`.
- Negative-control features (probes, codewords, blanks) must never enter biology; they are split at load and only summarized as a specificity metric. Evidence: `report_facts.json:features.n_control and qc.csv:control_fraction`.
- Differential expression requires biological replicates; with fewer than 2 samples per condition it is BLOCKED to avoid pseudoreplication. Evidence: `report_facts.json:de_n_sig and differential_expression.csv:status`.
- Cell-cell interactions are spatial-adjacency hypotheses with no ground truth; no causal or ligand-receptor direction is claimed. Evidence: `cci.csv:evidence_level (every row is 'hypothesis')`.
- For imaging-based spatial data, library-size normalization can encode tissue structure and degrade spatial-domain detection; the default library-size(100)+log is overridable. Evidence: `provenance.json:xenium.normalization`.
- Segmentation choice is consequential; the default consumes vendor segmentation and only recommends re-segmentation, never applying silent cell-mask expansion. Evidence: `provenance.json:xenium.resegmentation_recommended`.

Each caveat must name the artifact field or the number that says whether it fired. An unbound caveat is prose; a bound one is a gate.

## Evidence Tier

Contract maturity: `structurally_valid`. Run-specific support is limited to the sealed bundle identity, scientific and render provenance, facts, figures, report, and receipt.

## Data Sources & Licenses

- squidpy (>=1.4): https://github.com/scverse/squidpy — license BSD-3-Clause; commercial use `allowed` (BSD-3-Clause is a permissive license that allows commercial use.); included `true`; verify with `https://github.com/scverse/squidpy/blob/main/LICENSE`.
- scanpy (>=1.9): https://github.com/scverse/scanpy — license BSD-3-Clause; commercial use `allowed` (BSD-3-Clause permits commercial use.); included `true`; verify with `https://github.com/scverse/scanpy/blob/main/LICENSE`.
- anndata (>=0.8): https://github.com/scverse/anndata — license BSD-3-Clause; commercial use `allowed` (BSD-3-Clause permits commercial use.); included `true`; verify with `https://github.com/scverse/anndata/blob/main/LICENSE`.
- scikit-learn (>=1.0): https://github.com/scikit-learn/scikit-learn — license BSD-3-Clause; commercial use `allowed` (BSD-3-Clause permits commercial use.); included `true`; verify with `https://github.com/scikit-learn/scikit-learn/blob/main/COPYING`.
- scipy (>=1.7): https://github.com/scipy/scipy — license BSD-3-Clause; commercial use `allowed` (BSD-3-Clause permits commercial use.); included `true`; verify with `https://github.com/scipy/scipy/blob/main/LICENSE.txt`.
- numpy (>=1.21): https://github.com/numpy/numpy — license BSD-3-Clause; commercial use `allowed` (BSD-3-Clause permits commercial use.); included `true`; verify with `https://github.com/numpy/numpy/blob/main/LICENSE.txt`.
- pandas (>=1.3): https://github.com/pandas-dev/pandas — license BSD-3-Clause; commercial use `allowed` (BSD-3-Clause permits commercial use.); included `true`; verify with `https://github.com/pandas-dev/pandas/blob/main/LICENSE`.
- pyarrow (>=10): https://github.com/apache/arrow — license Apache-2.0; commercial use `allowed` (Apache-2.0 permits commercial use.); included `true`; verify with `https://github.com/apache/arrow/blob/main/LICENSE.txt`.
- tifffile (>=2023): https://github.com/cgohlke/tifffile — license BSD-3-Clause; commercial use `allowed` (BSD-3-Clause permits commercial use.); included `true`; verify with `https://github.com/cgohlke/tifffile/blob/master/LICENSE`.
- matplotlib (>=3.5): https://github.com/matplotlib/matplotlib — license PSF-based (matplotlib license); commercial use `allowed` (The matplotlib license is BSD-compatible and permits commercial use.); included `true`; verify with `https://github.com/matplotlib/matplotlib/blob/main/LICENSE/LICENSE`. Executed by scripts/spatial_figures.py.
- reportlab (>=4.0): https://www.reportlab.com/ — license BSD-3-Clause (ReportLab BSD license); commercial use `allowed` (The open-source ReportLab Toolkit is distributed under a BSD license — OSI-approved 'BSD License' classifier — which permits commercial use.); included `true`; verify with `https://pypi.org/project/reportlab/`. Executed only by the package-default `scripts/spatial_report.py` branch, never by scientific preparation or an explicit provider branch.
- Baysor (optional re-segmentation) (unpinned): https://github.com/kharchenkolab/Baysor — license MIT (verify at integration); commercial use `no_prohibition_found` (Reported MIT in the lane tool matrix; verify the upstream LICENSE before integration.); included `false`; verify with `https://github.com/kharchenkolab/Baysor/blob/master/LICENSE`.
- SPARK-X (optional SVG) (unpinned): https://github.com/xzhoulab/SPARK — license GPL-3.0 (copyleft); commercial use `no_prohibition_found` (GPL-3.0 permits commercial use under copyleft obligations; not linked or bundled.); included `false`; verify with `https://github.com/xzhoulab/SPARK/blob/master/LICENSE`.
- CellChat v2 (optional CCI) (unpinned): https://github.com/jinworks/CellChat — license GPL-3.0 (copyleft); commercial use `no_prohibition_found` (GPL-3.0 permits commercial use under copyleft; not bundled.); included `false`; verify with `https://github.com/jinworks/CellChat/blob/main/LICENSE`.
- Xenium 5K UC reference dataset (Zenodo record): https://doi.org/10.5281/zenodo.17781898 — license verify (Zenodo record terms); commercial use `not_checked` (Cited only; not downloaded, bundled, or used in this package.); included `false`; verify with `https://doi.org/10.5281/zenodo.17781898`.

## Resource Identity

Not applicable: The skill emits spatial-analysis result tables and provenance, not external citations, accessions, or versioned resource identifiers resolved against an authority; panel/chemistry/platform identifiers are recorded as provenance only.

## Common Issues

| Symptom                                     | Likely cause                                                                                                                          | Action                                                                                                                                                                                                                                                                                                      |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Platform routing stops                      | The manifest and file signature are missing, mixed, or disagree                                                                       | Correct `spatial_manifest.json` and the bundle contents; do not force a platform based on filenames alone.                                                                                                                                                                                                |
| Xenium control fraction is high             | Decoding errors, off-target probe binding, low gene-transcript recovery, or an assay/image issue can elevate negative-control metrics | Inspect the Xenium analysis summary and per-FOV RNA images; keep control features out of biological matrices. See the [10x quality-score and control definitions](https://www.10xgenomics.com/support/software/xenium-onboard-analysis/latest/algorithms-overview/xoa-algorithms).                           |
| Xenium tissue map disagrees with morphology | Vendor segmentation can under- or over-segment cells, changing transcript assignment and apparent neighborhoods                       | Review morphology, cell boundaries, transcripts within cells, and marker localization; recommend re-segmentation only when the evidence supports it. See the [10x segmentation documentation](https://www.10xgenomics.com/support/software/xenium-onboard-analysis/latest/algorithms-overview/segmentation). |
| Visium spots fail unexpectedly              | Tissue-specific RNA complexity or mitochondrial content makes generic thresholds inappropriate                                        | Inspect QC distributions against tissue morphology, then change thresholds explicitly and record the applied parameter object.                                                                                                                                                                              |
| Niche structure follows section or batch    | Sample/section identity may be confounded with condition, or graph settings may connect inappropriate spatial units                   | Check each section separately, preserve sample identity, and compare conclusions across reasonable spatial-neighbor settings before biological interpretation.                                                                                                                                              |
| No significant SVGs                         | The authoritative Moran's I FDR column may contain no calls, spatial graph settings may be unsuitable, or power may be limited        | Report the zero-result state; inspect effect statistics and graph construction without changing the predeclared headline column after seeing results. See [Squidpy spatial autocorrelation](https://squidpy.readthedocs.io/en/stable/api/squidpy.gr.spatial_autocorr.html).                                  |
| Differential expression is blocked          | Fewer than two biological replicates exist in one or more conditions                                                                  | Add independent biological samples; do not treat cells or spots as replicates.                                                                                                                                                                                                                              |
| No co-localized cell-type pairs             | No pair exceeded the pre-specified neighborhood-enrichment criterion, or labels/graph resolution are uninformative                    | Keep Figure 7 as an explicit zero-result panel; inspect label quality, rare populations, and spatial graph sensitivity without promoting the result to ligand-receptor evidence.                                                                                                                            |

## Suggested Next Steps

- If QC fails, review raw tissue morphology, segmentation, and spatial control patterns before interpreting niches or differential results.
- Validate stable niches and SVGs in an independent tissue section or batch, retaining biological sample as the replication unit.
- For Visium, use an appropriate single-cell reference to deconvolve mixed spots, then check whether spatial conclusions are robust to reference choice.
- For Xenium, compare selected marker localization with morphology and known biology; evaluate re-segmentation only when assignment evidence is poor.
- Follow replicate-supported differential or spatially variable genes with pathway analysis and orthogonal expression or imaging validation.
- Treat neighborhood enrichment as a spatial-adjacency hypothesis; run a separately validated ligand-receptor workflow only after confirming cell labels and proximity.

## Related Skills

- `single-cell-multimodal-integration` — build and validate reference cell states before cross-modal spatial interpretation.
- `deconvolution-bulk-rnaseq` — estimate mixed cell-type composition when spot-level measurements need reference-guided deconvolution.
- `pathway-enrichment` — interpret replicate-supported differential genes or spatial gene programs without changing the spatial significance calls.
