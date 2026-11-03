---
name: drug-seq-analysis
description: 'Compound- and dose-resolved differential expression versus matched vehicle
  with FDR control and a mandatory vehicle-versus-vehicle null contrast. Use for DRUG-seq
  or plate-based bulk RNA-seq compound screens (counts+platemap or barcoded FASTQ):
  per-well QC, compound/dose differential expression, perturbational signatures with
  MoA clustering, dose-response/tPOD, and pathway/TF activity. Not for single-cell
  or Perturb-seq data; wells are samples, not cells.'
category: transcriptomics
visibility: public
starting-prompt: Assess per-well quality in a DRUG-seq compound screen, quantify compound-
  and dose-resolved differential expression, and summarize perturbational signatures,
  dose response, and pathway activity.
---
<!-- archetype: analysis-workflow -->

<!-- contract: evidence-v1 -->

# Drug Seq Analysis

## When to Use This Skill

Use for DRUG-seq or plate-based bulk RNA-seq compound screens (counts+platemap or barcoded FASTQ): per-well QC, compound/dose differential expression, perturbational signatures with MoA clustering, dose-response/tPOD, and pathway/TF activity. Not for single-cell or Perturb-seq data; wells are samples, not cells.

## Why This, Not The Obvious Thing (READ FIRST)

Treating each well as a single cell (running a scRNA/Perturb-seq pipeline on well pseudobulk), or ranking hits by raw p-value with no vehicle-versus-vehicle null, lets plate position and library-size differences read as compound biology; this skill uses well-level bulk DESeq2 against per-plate matched vehicle and a mandatory null contrast that must return about zero DEGs.

## Inputs

- counts.tsv - genes (rows) by wells (columns) matrix of integer UMI counts; the header lists well IDs matching platemap.csv.
- platemap.csv - one row per well with columns well_id, compound, dose_uM, timepoint_h, replicate, plate_id, row, col, and semantic `is_control`. Flag the matched negative-control vehicle independently of its compound name. Use one vehicle identity across the run; mixed vehicle identities stop before modeling.
- Optional FASTQ front end - R1 (well barcode + UMI) and R2 (cDNA) FASTQ, a barcode whitelist (barcodes.txt), and a reference.tsv of probe/target genes.
- Wells are experimental units (samples), not cells; dose is in micromolar; counts are UMI-deduplicated integers.
- The production entrypoint seals caller-selected inputs and their origin, source, path, byte count, SHA-256, and acquisition provenance in `input_selection.json`; it rejects undeclared substitutes, package-owned data, and symlink escapes.

## Outputs

- `report_drug-seq-analysis.pdf` — the single canonical PDF, rendered by the provider selected from immutable user messages; defaults to `pdf-report-generation`, with task context, methods/sources, results, conclusions, data-bearing figures, references, and next steps
- `infographic.png` — one qualitative DRUG-seq workflow overview, generated from the exact prompt selected by the same immutable report-style decision and embedded once as the PDF's first substantive visual
- `infographic_prompt_binding.json` — provider, exact prompt hash, same-ID `GenerateImage` evidence, and image byte/pixel hashes captured before report assembly
- `tables/de_all_contrasts.csv` — machine-readable result: one row per gene x contrast (compound x dose vs matched vehicle) with baseMean, log2FoldChange, padj, is_sig, and low_expr_hit, including the vehicle-vs-vehicle null contrast
- `tables/de_model_specification.csv` — executed treatment and vehicle-null formulas, including their shared plate/edge adjustment terms
- `tables/null_contrast_gate.json` — machine gate state for the pre-specified maximum of 5 apparent null DEGs
- `tables/well_qc.csv` — one row per well: total UMIs, genes detected, pass/drop flag, and the depth/complexity floors
- `tables/activity_scores.csv` — gene-set x compound ULM activity t-values
- `tables/dose_response.csv` — per compound x dose aggregate response and the transcriptomic point of departure (tPOD)
- `tables/moa_clusters.csv` — per-compound mechanism-of-action cluster assignment
- `tables/query_signatures.json` — exported up/down query signatures for optional connectivity analysis in `signature-reversal-lincs`
- `data/analysis_platemap.csv` — derived plate map carrying the normalized control role and resolved vehicle identity used by every consumer
- `report_facts.json` — evidence-bearing claims, operational definitions, provenance, and validated denominator/completion partitions

**Write the report to the results root under the name above.** Data tables, figures and intermediates go in `data/`, `figures/`, `tables/`.

## Clarification Questions

1. **Which DRUG-seq input do you have?** (select one)
   - `counts` — My own counts.tsv plus platemap.csv; runtime branch ID `qinput:counts`
   - `fastq` — Barcoded FASTQ plus whitelist and reference (demultiplexing front end); runtime branch ID `qinput:fastq`
2. **Adjust differential expression for plate row/column (edge) position?** (select one)
   - `none` — No positional covariate (use when position diagnostics are clean); runtime branch ID `qcovariate:none`
   - `edge` — Include an edge/position covariate in the DESeq2 model; runtime branch ID `qcovariate:edge`

## Standard Workflow

**Step 1 — Load and validate the input.** Enforce every item in `## Inputs`; stop with `not_computable` on a mismatch.

**Step 2 — Per-well and plate QC.** Run `scripts/well_qc.py` for per-well UMI/gene metrics, data-driven low-quality-well dropping, and plate row/column/edge positional diagnostics; stop with not_computable when the barcode assignment rate is below threshold. Success: `tables/well_qc.csv` with one row per well and the dropped-well list.

**Step 3 — Experimental design and normalization.** Run `scripts/design_preflight.py` to check per-condition replicate wells and design identifiability, stopping when a condition is confounded with a whole plate; normalize with a plate (and optional position) covariate. Success: `tables/design_preflight.csv` and an identifiable design.

**Step 4 — Differential expression contrasts with FDR control.** Run the bundled `scripts/de_deseq2.R` for each compound-by-dose versus matched vehicle at padj<0.05 and absolute log2 fold-change above 1, applying a baseMean floor so low-count genes are flagged not ranked. Success: `tables/de_all_contrasts.csv` and `tables/de_summary.csv`. When DESeq2 is used, `de_deseq2.R` also queries and persists the DESeq2 **package** version (`packageVersion("DESeq2")`) to `tables/software_versions.csv`, kept separate from the R interpreter version; `run_analysis.py` surfaces both as distinct fields in the facts payload (`software.r.version`, `software.deseq2.version`), so a report or summary never prints the R interpreter version as DESeq2's (the "DESeq2 4.4.3" conflation, where 4.4.3 is R's version).

**Step 5 — Vehicle-versus-vehicle null contrast.** Fit the mandatory `NULL_VEHICLE_vs_VEHICLE` contrast by splitting the `is_control`-flagged vehicle wells under the same plate and optional edge adjustment terms used for treatment contrasts. At least four QC-passing vehicle wells, both null groups, and a full-rank null design are required. Apply the version-controlled, pre-specified `scientific_logic.MAX_NULL_DEGS=5` gate immediately after DESeq2; more than 5 apparent DEGs hard-stops the run before activity, tPOD, figures, facts, or report generation. Success: `tables/de_model_specification.csv`, `tables/null_contrast_gate.json`, and `null_contrast.status=passed` with `null_contrast.n_deg<=5`.

**Step 6 — Concentration-response and transcriptomic POD.** Run `scripts/dose_response.py` to orient top-dose DEGs by their signed top-dose direction, subtract the lowest tested dose as a within-compound reference, and interpolate the tPOD at BMR=1 only for compounds with at least four doses and positive dose association. Fewer than four doses is reported as `not_estimable_insufficient_doses`, not as a null response; observed differential-expression evidence remains visible. A flat, downward, or non-crossing response with an otherwise sufficient series is explicitly null rather than assigned an absolute-value noise floor. Success: `tables/dose_response.csv` with the version-controlled method, response state, tPOD, or an explicit non-estimable/null reason.

**Step 7 — Perturbational signatures, MOA clustering, and connectivity export.** Run `scripts/signatures_moa.py` to build per-compound signatures, cluster compounds by signature correlation, and export query signatures that can be analyzed with `signature-reversal-lincs`. Success: `tables/moa_clusters.csv` and `tables/query_signatures.json`.

**Step 8 — Pathway and TF-activity scoring.** Run the bundled `scripts/activity_ulm.R` to score gene-set and TF activity per compound by univariate linear model over the supplied gene sets. Headline sets are ranked only by the version-controlled maximum absolute ULM t-value in `scientific_logic.PATHWAY_RANKING_METRIC`; the selected value retains its sign for direction, and there is no runtime or review-time ranking branch. Success: `tables/activity_scores.csv` with signed activity t-values.

**Step 9 — Generate and validate figures.** Render only results produced by the preceding validated steps, write `figures/manifest.json`, then validate every declared artifact before facts or the receipt can use it.

```python
from report_qc import assert_figures
figures = assert_figures("figures/manifest.json")
```

## Figures

One representative figure per result-producing step, showing **this run's actual result** — not a schematic, not an illustrative example. Every figure needs a caption stating what it shows and a short adjacent block labelled `Claim:`, `Evidence:`, `Interpretation:`, and `Limitation:`. Keep that block on the same PDF page as its figure and exact manifest caption. If a step genuinely has nothing to plot, replace its row with a one-line reason. Both narrative fields are required at run time.

| Step | File                                                       | What it must make visible                                                                                                                                                                                                |
| ---- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 2    | `figures/figure_2_per_well_and.png`                      | Per-well sequencing depth with the QC floor marked, and the plate-position heatmap, so dropped low-quality wells and any edge/positional gradient are obvious                                                            |
| 3    | `figures/figure_3_experimental_design_and.png`           | PCA of QC-passing wells colored by compound, with the exact caption returned by `scientific_logic.positional_caption(...)`, derived from the executed positional-confounding flag and selected DE-model edge covariate |
| 4    | `figures/figure_4_differential_expression_contrasts.png` | Volcano of the strongest compound x dose contrast, with called DEGs (padj<0.05, absolute log2FC above 1) highlighted and the up/down counts readable                                                                     |
| 5    | `figures/figure_5_vehicle_versus_vehicle.png`            | Volcano of the vehicle-vs-vehicle null contrast, which exists only after its apparent DEG count passes the pre-specified maximum of 5                                                                                    |
| 6    | `figures/figure_6_concentration_response_and.png`        | Lowest-dose-referenced, top-dose-direction-aligned signed response versus dose, with the benchmark line and either the interpolated tPOD or explicit null state                                                          |
| 7    | `figures/figure_7_perturbational_signatures_moa.png`     | Pairwise compound signature-correlation heatmap with the mechanism-of-action cluster structure visible                                                                                                                   |
| 8    | `figures/figure_8_pathway_and_tf.png`                    | Gene-set / TF activity (ULM t-value) per compound, with headline sets selected by the pinned maximum-absolute-t metric and their signed t-values shown                                                                   |

The report reads this inventory from `report_facts.json`'s `figures` array rather than restating it, so it cannot claim a figure that was never produced.

**Step 10 — Write `report_facts.json` from the runtime payload.** The workflow must produce `facts_payload.json` with every headline, denominator, and partition-member field declared in `skill_contract.json`. Semantic gates run before the facts artifact is written, so a failing run never produces claims a report could quote.

```python
from report_qc import write_facts_from_artifact
write_facts_from_artifact('report_facts.json', source='facts_payload.json', figures=figures, contract="skill_contract.json")
```

### Step 11 — Final report (MANDATORY TERMINAL STEP)

**The run is not complete until this step has produced `report_drug-seq-analysis.pdf` at the results root.**
Resolve the provider with `report_qc.report_render_plan()` before creating any PDF bytes. The resolver reads immutable user-message evidence only; a provider name in an assistant message, CLI argument, customer name, tenant, project, account, folder, or dataset is not selection evidence.

```python
from report_qc import RESULTS, report_render_plan
from infographic_style import resolve_prompt
render_plan = report_render_plan()
selected_infographic_prompt_file = resolve_prompt(render_plan, RESULTS)
```

- With no explicit user style request, follow the default `pdf-report-generation` path and render the report sections and data-bearing figures specified below.
- With an explicit compatible provider, load that provider's complete instructions and required assets and render the same canonical filename.
- Missing, malformed, conflicting, revoked, or incompatible provider selection blocks completion. Never fall back, approximate a requested style, or produce an unverified companion PDF.
- The selected provider owns presentation only. Preserve every fact, table, figure, caption, adjacent interpretation, threshold, claim, reference, limitation, provenance record, artifact path, and non-PDF output.

Produce one combined PDF with a short narrative. rendered page images are inspection evidence only, never substitutes for the PDF. End the task with a concise conclusion and links to the PDF and supporting artifacts, not a bare file listing.

Regenerate any figure whenever its underlying data change; do not reuse a figure derived from stale inputs or tables.

The PDF must use these visible top-level sections in this order, adapting their contents to this skill rather than adding generic filler:

1. `Task Context` — the research or practitioner question, supplied inputs, scope, and decision the output informs.
2. `Methods & Sources` — Methods & Sources covers data provenance, filtering, parameters, software, and the analysis design; Results reports validated quantitative findings and figures.
3. `Results` — the run's actual outputs and evidence, never a description of what the skill could do.
4. `Conclusions & Interpretation` — supported takeaways, their practical meaning, and appropriate next steps.
5. `Limitations` — run-specific uncertainty, missing coverage, failed or unavailable checks, and claims the evidence does not support.
6. `References` — the complete numbered user-facing entries returned by `scripts/report_references.py`; do not substitute the internal review IDs used in the on-demand reference notes.

Load `citation_marker()` and `reference_paragraphs()` from `scripts/report_references.py`. Cite each catalog source at least once outside the `References` section using its returned canonical inline marker (`[1]`, `[2]`, ...), and render every returned numbered paragraph unchanged in `References`. Catalog order is the only numbering source: do not freehand, renumber, omit, rewrite, or invent citations. The receipt rejects missing or unmatched inline numbers, an unnumbered or reordered bibliography, and incomplete entries. The selected report provider controls presentation only; it must not select, omit, rewrite, or invent these scientific sources. Include a compact output-artifact table where applicable. Empty boilerplate does not satisfy a section.

The Results or Limitations prose must quote `report_facts.json:headline_sentences.positional`
verbatim. That sentence is derived from `caveats_fired.positional_confounded`; do not freehand a
claim that an edge or positional gradient was detected, absent, or flagged. The receipt rejects the
current report when any additional run-specific positional claim contradicts that boolean.
Build the Figure 3 manifest caption by calling `scientific_logic.positional_caption(...)` with that
same boolean, the executed positional covariate, and the computed PC1/PC2 variance fractions; keep
the returned caption unchanged in the PDF. The contradiction gate understands the helper's explicit
negative wording ("was not flagged") as a negative claim, while still rejecting affirmative claims
when the Boolean is false.

The Results or Limitations prose must also quote
`report_facts.json:headline_sentences.cytotoxicity` verbatim. It is derived from the strongest
top-dose signature, its tested-gene denominator, and whether the plate map contains a nonmissing
viability measurement. It discloses cytotoxicity, global transcription/translation inhibition, and
general stress as plausible confounds; the receipt rejects a report that omits this limitation.

Generate exactly one qualitative workflow image at `infographic.png`. Submit the complete contents of `selected_infographic_prompt_file` to one `GenerateImage` call verbatim, with `file_name="infographic.png"`; do not append facts or visual instructions. The default prompt keeps Phylo presentation inside the `pdf-report-generation` route. An explicit enterprise route instead uses the selected provider's complete palette, typography, spacing, and logo guidance and contains no Phylo styling.

The infographic must remain a workflow overview: counts and plate map → well/plate QC → differential expression and vehicle-null gate → dose response and signatures → MoA/pathway interpretation → evidence-bound report. It must visibly say that it is a typical workflow rather than run-specific results. It must not contain run-specific numbers, compound results, identifiers, plots, axes, data encodings, pass/fail badges, inferred findings, or claims that an analysis outcome occurred.

Immediately after the same-ID `GenerateImage` result succeeds, bind the exact selected provider and prompt to that call/result and snapshot the generated bytes and decoded pixels before report assembly:

```python
from infographic_style import record_provider_bound_infographic
record_provider_bound_infographic(
    "infographic.png", render_plan, selected_infographic_prompt_file,
    results=RESULTS,
)
```

Embed `infographic.png` exactly once, on page 1, as the first substantive PDF visual. Give it a plain workflow caption that says it is qualitative and not a results figure. The existing seven data-bearing figures must follow it and retain their existing captions and result associations.

Render the PDF to a fresh workspace file named by `workspace_report_file`; do not open or truncate an existing PDF on the object-backed results mount. The `staged_copy` call below publishes the completed file under its declared results-root name.

Then verify the run with `report_qc.write_receipt`. It derives obligations from `skill_contract.json`: report verification and `infographic_lineage_verified` are both required, with `infographics=["infographic.png"]`. It also verifies execution, outputs, facts, figures, selected-provider markers, source witnesses, report sections, and review evidence. It raises after writing diagnostic reasons when a required surface fails.

Record each answer from `## Clarification Questions` in `selected_branch_ids` using the displayed `<question_id>:<choice_id>` value. The receipt derives its required outputs only from those selected branches; do not union mutually exclusive branch artifacts.

```python
from report_qc import (outputs_for_selected_branches, record_pdf_review, staged_copy,
                       write_receipt)
selected_outputs = outputs_for_selected_branches(selected_branch_ids)
selected_outputs.append("infographic_prompt_binding.json")
staged_copy(workspace_report_file, "report_drug-seq-analysis.pdf")
record_pdf_review(
    report_name="report_drug-seq-analysis.pdf", text_artifact=extracted_text_file,
    rendered_page_files=rendered_page_files,
    reviewed_page_numbers=reviewed_page_numbers,
    review_attestation=visual_review_notes,
)
write_receipt(
    report_name="report_drug-seq-analysis.pdf",
    figures=figures,
    figure_not_applicable_reason=None,
    bundled_files=['scripts/de_deseq2.R', 'scripts/activity_ulm.R'],
    outputs=selected_outputs,
    infographics=["infographic.png"],
    qc_run_log="qc_run_log.json",
)
```

The receipt is `run_receipt.json` at the **results root**, not beside this SKILL.md — once this skill is installed its own directory is read-only, so a per-run receipt written there cannot work. It uses `phylo-run-receipt/3` and records `execution_contract_satisfied`, `outputs_appeared`, `report_at_results_root`, `figure_contract_satisfied`, `facts_artifact_verified`, `report_style_verified`, `text_extracted`, `pages_rendered`, `visual_review_attested`, `report_sections_present`, `source_assertions_verified`, `infographic_lineage_verified`, each with the path, byte count, selected-provider markers, source hashes, or transcript record the outcome was read from. The prompt-binding artifact is also a declared output, so the receipt hashes its selected provider, exact prompt, same-ID call/result, and image identity alongside the existing infographic snapshot and single-embedding lineage check.

**Do not write this file by hand.** `check_skill.py --require-run-receipt` requires the schema marker and per-outcome evidence, so a hand-written block of `true`s fails. Whatever did not hold is recorded `false` with a `<key>_reason`; fix the run rather than the receipt.

## Scientific caveats

- Treating each well as a single cell (running a scRNA/Perturb-seq pipeline on well pseudobulk), or ranking hits by raw p-value with no vehicle-versus-vehicle null, lets plate position and library-size differences read as compound biology; this skill uses well-level bulk DESeq2 against per-plate matched vehicle and a mandatory null contrast under the same plate/edge adjustment terms. More than the pre-specified maximum of 5 apparent null DEGs hard-stops delivery. Evidence: `report_facts.json:null_contrast.n_deg`, `report_facts.json:null_contrast.maximum_acceptable_degs`, and `tables/de_model_specification.csv`.
- A wrong barcode whitelist or a transposed plate map collapses the barcode assignment rate; below threshold the run stops and asks you to reconcile the whitelist and plate map rather than mislabeling wells. Evidence: `report_facts.json:qc.barcode_assignment_rate`.
- Plate row/column/edge gradients can masquerade as dose effects; the run flags positional confounding when plate position explains an outsized share of well-to-well variance. Evidence: `report_facts.json:caveats_fired.positional_confounded`.
- Low-count genes yield unstable, inflated fold-changes; hits below the baseMean floor are flagged as low-expression rather than ranked as effects. Evidence: `report_facts.json:caveats_fired.low_expression_hits`.
- High-dose signatures can reflect cytotoxicity, global transcription/translation inhibition, or a general stress response rather than compound-specific pharmacology. The report quotes the artifact-derived limitation and discloses whether a viability measurement was available; this workflow does not silently claim a viability-adjusted analysis. Evidence: `report_facts.json:headline_sentences.cytotoxicity` and `report_facts.json:top_dose_signature.viability_measurement_available`.
- When a treatment condition is confounded with a whole plate the effect is not identifiable; the run stops rather than reporting a plate effect as compound biology. Evidence: `report_facts.json:design.identifiable`.
- The transcriptomic point of departure is method-sensitive by roughly an order of magnitude and requires at least four doses, a positive signed dose association, and a BMR crossing after lowest-dose referencing; the method and benchmark are recorded, and flat/nonresponsive data yield an explicit null. Evidence: `report_facts.json:dose_response.tpod_method` and `tables/dose_response.csv:response_status`.
- On targeted panels, off-panel genes are absent rather than zero; the run discloses whether values are measured or imputed so absent genes are not read as unchanged. Evidence: `report_facts.json:panel.measured_vs_imputed`.
- Connectivity/MoA against CMap or LINCS is not computed offline here. The exported signatures can be analyzed with `signature-reversal-lincs`, which reports at least two metrics under its own data-use terms. Evidence: `report_facts.json:connectivity.status`.
- The R interpreter version and the DESeq2 package version are different identifiers (for example R 4.4.3 versus DESeq2 1.46.0); when DESeq2 is used the run queries and persists the DESeq2 package version separately and never prints the R interpreter version or an installed boolean as the package version. Evidence: `report_facts.json:software.deseq2.version`.

Each caveat must name the artifact field or the number that says whether it fired. An unbound caveat is prose; a bound one is a gate.

## Evidence Tier

Contract maturity: `evidence_validated`. Run-specific support is limited to the hash-bound facts, provenance, figures, report, and receipt produced from the selected inputs.

## Data Sources & Licenses

- DESeq2 (Bioconductor 3.x): https://bioconductor.org/packages/DESeq2/ — license LGPL (>=3); commercial use `allowed` (LGPL permits commercial use with the usual copyleft obligations for modifications to the library itself.); included `true`; verify with `scripts/de_deseq2.R`.
- Python scientific stack (numpy, pandas, scipy, scikit-learn, matplotlib, seaborn) (Biomni environment): https://scipy.org/ — license BSD-3-Clause; commercial use `allowed` (BSD-3-Clause permits commercial use.); included `true`; verify with `scripts/run_analysis.py`.
- ReportLab (via pdf-report-generation) (Biomni environment): https://www.reportlab.com/ — license BSD-3-Clause; commercial use `allowed` (BSD-3-Clause permits commercial use.); included `true`; verify with `scripts/report_qc.py`.
- LINCS L1000 / CMap (GSE92742 / GSE70138): https://clue.io/ — license clue.io / CMap data-use terms; commercial use `no_prohibition_found` (clue.io terms permit research use; commercial use is not explicitly prohibited but must be verified before commercial deployment.); included `false`; verify with `references/connectivity_handoff.md`.
- MSigDB (2024.1): https://www.gsea-msigdb.org/ — license MSigDB registration terms (varies by collection); commercial use `not_checked` (Registration and per-collection terms not verified for commercial use.); included `false`; verify with `references/targeted_panel.md`.

## Resource Identity

## Common Issues

| Symptom                                                     | Cause                                                                                            | Fix                                                                                                                         |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| Run stops with "barcode assignment rate below threshold"    | Wrong barcode whitelist or a transposed plate map                                                | Reconcile `barcodes.txt` and `platemap.csv` against the wet-lab layout and confirm the barcode/UMI lengths              |
| Run stops with "design not identifiable"                    | A condition is confounded with a whole plate (batch = condition)                                 | Rebalance the layout so each condition spans at least two plates, or place matched vehicle on every plate                   |
| Run stops on mixed vehicle identities                       | More than one compound identity is flagged `is_control=true`                                   | Model separate matched strata in an appropriate workflow; this package intentionally does not pool distinct vehicles        |
| Null (vehicle-vs-vehicle) contrast returns more than 5 DEGs | Library-size or positional artifact, or a mis-specified design                                   | The run hard-stops; inspect the positional diagnostic/model specification and correct the design before retrying            |
| No tPOD reported for a compound                             | Fewer than four doses, no top-dose DEGs, flat/non-associated signed response, or no BMR crossing | Expected null behavior — inspect `response_reason`; extend or redesign the dose range only when scientifically justified |
| DESeq2 step fails to run                                    | DESeq2 is not installed in the R environment                                                     | Install DESeq2 from Bioconductor; it is a declared dependency, not optional                                                 |
| Connectivity / tau is empty                                 | No CMap/LINCS reference was supplied to this run                                                 | Expected — continue with `signature-reversal-lincs` using `tables/query_signatures.json`                               |

## Suggested Next Steps

- Analyze `tables/query_signatures.json` with `signature-reversal-lincs` for connectivity/MoA scoring against CMap/LINCS, reporting at least two metrics under its data-use terms.
- Extend the dose range to at least four doses per compound to estimate a tPOD where this run reported none.
- Confirm hits with orthogonal evidence (independent compounds, CRISPR concordance, or dose extension) before moving past hypothesis-generating strength.

## Related Skills

- `signature-reversal-lincs` — consumes the exported query signatures for connectivity/MoA (CMap/LINCS) scoring.
- `bulk-rnaseq-counts-to-de-deseq2` — general bulk RNA-seq DESeq2 differential expression; this skill adds the DRUG-seq plate/well front end, null contrast, and dose-response.
- `functional-enrichment-from-degs` — downstream pathway enrichment on the differential-expression gene lists.
- `pdf-report-generation` — used by the terminal step to render the branded PDF report.
