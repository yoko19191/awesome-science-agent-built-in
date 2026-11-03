---
name: clinicaltrials-landscape
description: Use to map a bounded, disease-centric set of registered ClinicalTrials.gov
  records by condition, study purpose, intervention type, phase, status, or sponsor
  through the ClinicalTrials.gov API v2, with auditable coverage provenance. Not for
  exhaustive target-development, asset, or company-pipeline inventories.
category: literature
visibility: public
starting-prompt: What is the current ClinicalTrials.gov landscape for PSMA in prostate
  cancer?
---
# Clinicaltrials Landscape

## When to Use This Skill

Use this skill to map a **bounded** set of registered ClinicalTrials.gov records for a declared query
(one or more conditions, plus optional intervention/status/phase/sponsor filters), classified by study
purpose and record-grounded intervention type, with auditable coverage provenance.

Not for exhaustive target-development, asset, or company-pipeline inventories. If the request is an
exhaustive asset/program inventory, route to a target-oriented capability (for example a target- or
pipeline-inventory skill) and deliver only the bounded ClinicalTrials.gov component here. The
`scope_fit` clarification question makes this explicit and returns `not_computable` for the
out-of-scope branch.

## Interpretation boundary

Classification is generic (no disease config ships); generic buckets are not a target-development taxonomy.

## Inputs

- One or more ClinicalTrials.gov condition terms (required).
- Optional intervention term, status list, phase list, and sponsor term.
- Alternatively, a compiled trials CSV for offline/deterministic classification.
- For a simple PSMA-in-prostate-cancer prompt, use the bundled `psma-prostate-cancer` profile; it
  supplies the reviewed three-condition, 59-alias, all-14-status query without further questions.

## Outputs

- `report_clinicaltrials-landscape.pdf` — Generate the PDF report with `pdf-report-generation` by default. When the user explicitly selects a compatible report-styling skill, use that provider instead for presentation only; keep every report, evidence, artifact, infographic, and review requirement unchanged. Include a Biomni GenerateImage infographic when required, task context, methods or sources, results, conclusions, figures where applicable, references, and next steps
- `trials_all.csv` — the compiled per-record dataset; **one row per retrieved ClinicalTrials.gov record**, including expanded-access records when selected (identity, sponsor, phase, study type, `study_purpose`, `intervention_category`, geography, enrollment).
- `trials_by_category.csv` — intervention-category x phase counts; one row per intervention category, with explicit `Not Applicable`, `Phase-applicable subtotal`, and `All retrieved` denominator columns (no bare "Total").
- `trials_by_sponsor.csv` — one row per lead sponsor: registered-record count, distinct categories, and industry/academic class (full sponsor names).
- `trials_match_evidence.csv` — one row per registered record recording how it matched the declared query and which source fields were available (absent fields never implied present).
- `coverage_scope.json` — the auditable coverage fingerprint (declared query, denominators, dataset SHA-256, coverage/limitation and record-versus-asset boundary statements) binding the report to the exported dataset.
- `figures/manifest.json` — one entry per analysis step naming the figure file and its data-driven caption, or a recorded reason when a figure is not applicable.
- `report_facts.json` — evidence-bearing claims, operational definitions, provenance, and validated denominator/completion partitions
- `report_spec.json` — deterministic report section, wording, figure-order, and pagination contract derived from the exact `report_facts.json` bytes.

**Write the report to the results root under the name above.** Data tables, figures and intermediates go in `data/`, `figures/`, `tables/`. Note that `GenerateImage` strips directory components, so schematics always land at the root regardless of the path you pass it.

## Clarification Questions

Resolve unambiguous requests without asking. A plain landscape/report request with no uploaded CSV
uses `scope_fit:bounded_landscape` and `data_source:live_api`. Record those branch IDs and proceed. Ask
only when the user explicitly requests exhaustive/global asset coverage, supplies a compiled table,
or gives inputs that genuinely fit more than one branch.

1. **Is this a bounded registered-record landscape for a disease/mechanism, or an exhaustive target/asset/company-pipeline inventory?** (select one)
   - `bounded_landscape` — Bounded registered-record landscape (proceed); runtime branch ID `scope_fit:bounded_landscape`
   - `exhaustive_inventory` — Exhaustive asset/program inventory (out of scope; deliver only the bounded ClinicalTrials.gov component); runtime branch ID `scope_fit:exhaustive_inventory`
2. **Query the live ClinicalTrials.gov API v2, or classify an existing compiled trials table?** (select one)
   - `live_api` — Live ClinicalTrials.gov API v2 query (recommended for real use); runtime branch ID `data_source:live_api`
   - `compiled_csv` — An existing compiled trials CSV (offline / deterministic); runtime branch ID `data_source:compiled_csv`

## Standard workflow

This is a general retrieval and synthesis workflow. Resolve scope and source first, execute the production helpers, then assemble and inspect a report. Do not replace it with an unattended file-in analysis contract.

1. Record the verbatim request, approved query conditions/filters, source choice and scope boundary in `run_context.json`. Use a fresh results directory for each run. Stop on out-of-scope requests, ambiguous conditions, partial pagination, or missing offline provenance; do not silently relax filters or assert completeness.
2. For a recognized PSMA-in-prostate-cancer prompt, run `python3 "$SKILL_DIR/scripts/run_pipeline.py" --live --prompt "<verbatim user prompt>" --results-dir /mnt/results/<run>`. Other live requests use `--conditions` and optional `--intervention`/`--statuses`. For offline input use `--input <supplied.csv> --coverage <supplied-coverage.json>`. The coverage JSON must supply `conditions` and `statuses` as nonempty arrays of nonblank strings; malformed values stop processing before data export. `$SKILL_DIR` is the mounted directory printed when the skill loads. Record the command, exit status and full logs outside the package.
3. The pipeline queries or loads, classifies study purpose and intervention category, compiles records, exports all tables and coverage provenance, creates analytical figures, validates accounting and writes `report_facts.json` and `report_spec.json`. Do not repeat these stages with independently reconstructed inputs. Failed retrieval or accounting blocks report claims.
4. `scripts/landscape_evidence.py` binds the facts to the source payload and nonblank, captioned figures, checking the production definitions in `assets/fact_definitions.json`. If `report_spec.json` has a stale `facts_sha256`, rerun the pipeline; never patch facts or the report specification by hand.
5. Follow the report assembly and final inspection instructions below. Keep runtime data, reports, logs, receipts and evidence outside the extracted package.

Retries are bounded and apply only to the same idempotent retrieval after transient failure. Disclose package defects or required changes to scope/method, and stop for the appropriate decision; a modified package or fresh task is not the original run.

## Figures

One representative figure per result-producing step, showing **this run's actual result** — not a schematic, not an illustrative example. Every figure needs a caption stating what it shows. If a step genuinely has nothing to plot, replace its row with a one-line reason. A caption is required at run time, so fill these in before the first run.

| Step | File                                           | What it must make visible                                                                                                                                               |
| ---- | ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | `figures/figure_1_study_composition.png`     | Study-purpose composition (Therapeutic / Diagnostic-Imaging / Observational / Other-Supportive / Unresolved) with per-bar counts over all retrieved records.            |
| 2    | `figures/figure_2_intervention_category.png` | Record-grounded intervention-category distribution, including the count labeled `Unclassified` rather than assigned an invented modality.                             |
| 3    | `figures/figure_3_phase_distribution.png`    | Phase distribution with `Not Applicable` shown explicitly; phase-applicable and all-retrieved denominators kept distinct.                                             |
| 4    | `figures/figure_4_category_by_phase.png`     | Intervention category x phase for the phase-applicable subset (Not Applicable records excluded by definition).                                                          |
| 5    | `figures/figure_5_top_sponsors.png`          | Top lead sponsors by registered-record count with full (wrapped, non-truncated) names and industry vs academic/other class.                                             |
| 6    | `figures/figure_6_geography.png`             | Top countries by number of retrieved records with a site there (multi-site studies counted once per country).                                                           |
| 7    | `figures/figure_7_enrollment.png`            | Registry-reported enrollment (actual or estimated; log scale) by study purpose for interventional trials with usable enrollment; registry mega-study outliers excluded. |

The report reads this inventory from `report_facts.json`'s `figures` array rather than restating it, so it cannot claim a figure that was never produced.

## Report assembly and final inspection

**The run is not complete until this step has produced `report_clinicaltrials-landscape.pdf` at the results root.**
Generate the PDF report with `pdf-report-generation` by default. When the user explicitly selects a compatible report-styling skill, use that provider instead for presentation only; keep every report, evidence, artifact, infographic, and review requirement unchanged. Include a Biomni GenerateImage infographic when required, task context, methods or sources, results, conclusions, figures where applicable, references, and next steps

When the user explicitly selects a compatible report-styling skill, use that provider for presentation only; keep every report, evidence, artifact, infographic, and review requirement unchanged.

Produce one combined PDF with a short narrative. Follow `report_spec.json` exactly: place the qualitative GenerateImage infographic on page 1 after Task Context as the first substantive visual and caption it as `Figure 1`; number any later analytical figures after it. Set `keepWithNext=1` on protected heading styles. When a heading precedes a `KeepTogether` figure or callout, put the heading inside that same `KeepTogether` block because `keepWithNext` alone does not reliably cross the boundary. Keep every figure with its caption. Rendered page images are inspection evidence only, never substitutes for the PDF. End the task with a concise conclusion and links to the PDF and supporting artifacts, not a bare file listing.

Registered-record activity is not an asset-development pipeline. Quote
`record_asset_boundary_statement` verbatim and keep phase/status conclusions at the record level.
Never turn record counts into a pipeline, emerging/early-stage agents, unique assets, or development
programs in the PDF or final answer. The post-render gate rejects those substitutions.

The PDF must use these visible top-level sections in this order, adapting their contents to this skill rather than adding generic filler:

1. `Task Context` — the research or practitioner question, supplied inputs, scope, and decision the output informs.
2. `Methods & Sources` — Methods & Sources covers data provenance, filtering, parameters, software, and the analysis design; Results reports validated quantitative findings and figures.
3. `Results` — the run's actual outputs and evidence, never a description of what the skill could do.
4. `Conclusions & Interpretation` — supported takeaways, their practical meaning, and appropriate next steps.
5. `Limitations` — run-specific uncertainty, missing coverage, failed or unavailable checks, and claims the evidence does not support.

Include references and a compact output-artifact table where applicable. Empty boilerplate does not satisfy a section.

Use `report_spec.json` as the assembly contract and `report_facts.json` as the sole quantitative report source. In both `Methods & Sources` and
`Results`, quote `match_evidence_availability_sentence` verbatim instead of reconstructing field-
availability counts from nested keys. The flat availability fields are also present for tables; treat
any missing value, `None`, `null`, or `NaN` as a report-build failure. Escape literal artifact text
before inserting it into XML/HTML-aware layout components (especially intervention aliases containing
`&`), then confirm the extracted PDF text preserves the displayed query terms and contains no
unresolved placeholders.

Quote `sponsor_composition_sentence` verbatim wherever sponsor class is summarized. `n_industry` and
`n_academic` count **records by lead-sponsor class**, while `n_sponsors` counts unique lead sponsors;
never label record counts as sponsor counts. An offline `--input` CSV must carry `sponsor_class`
(registry lead-sponsor class) or `is_industry`; without either the pipeline stops rather than
reporting an unknown split as "0 industry" records. After extracting the actual PDF text, run `validate_report_output()` before recording completion. It rejects unsupported strategic or
commercial inference, record-to-asset/pipeline inference, sponsor-denominator mistakes, unresolved placeholders, corrupted ampersand
aliases, missing canonical fact sentences, an infographic that is not Figure 1/the first substantive
visual, `registered-trial` wording for a dataset containing expanded-access records, and effectively
blank/orphaned PDF pages or headings stranded at a page bottom.

Generate `infographic.png` with Biomni `GenerateImage` before assembly. Retain the original tool-returned file and the actual task/tool reference in `run_context.json`; never fabricate transcript IDs or claim independently verified lineage from a filename. Keep unsupported interpretations out of the illustration as well as prose. The independent campaign reviewer checks the task trace and the delivered PDF.

Read the complete installed `pdf-report-generation` instructions and assets, or the compatible provider explicitly selected by the user. Record provider identity and the user message authorizing an override. Do not infer styling from customer, account or project context. Missing or conflicting provider instructions require clarification. Construct the PDF in a fresh local workspace path, close it, then copy the completed file to the results directory; do not truncate an existing PDF on the object-backed results mount.

Use `landscape_evidence.prepare_report_review(results_dir)` to render every PDF page and extract text from the final PDF. It returns the exact text and page paths to inspect; retain its snapshot. Actually inspect every rendered image at readable resolution, including figures, labels, captions, page breaks and the first-page infographic. Text extraction, pixel statistics and automated layout checks do not prove visual inspection. If images cannot be inspected, record `fail` with the reason. Repair and rerender visual defects before recording `pass`; limitations cannot turn a failed visual review into approval.

Run the domain checks using the actual extracted text and your truthful inspection record:

```python
import os, sys
os.environ["BIOMNI_RESULTS"] = results_dir
sys.path.insert(0, f"{SKILL_DIR}/scripts")
from validate_report_output import validate_report_output
from landscape_evidence import write_report_review
checks = validate_report_output(
    extracted_text_file,
    review_attestation=review_attestation,
    review_verdict=review_verdict,
    review_performed=review_performed,
    review_issues=review_issues,
)
write_report_review(
    results_dir, report_file="report_clinicaltrials-landscape.pdf",
    text_file=extracted_text_file, rendered_page_files=rendered_page_files,
    reviewed_page_numbers=reviewed_page_numbers, checks=checks,
)
```

Set `review_performed` to the explicit boolean `True` only after actual image inspection, otherwise `False`; prose cannot authorize a pass. Supply actual paths and page numbers; never generate an affirmative attestation before inspection. `report_review.json` binds the PDF, extracted text, rendered pages, facts and report spec by hash. It distinguishes mechanical checks from the reviewer's attestation and refuses completion for incomplete page coverage or a failed review. These checks are not scientific acceptance.

Before the final answer, apply the same record-versus-asset and denominator restrictions to its prose. Deliver a concise supported conclusion with links to the PDF, tables, query provenance and review record. State unresolved limitations. Do not call the skill deployed or independently accepted based on completing this run.

## References

- [Registry API parameters](references/api-parameters.md) for query filters and pagination.
- [Classification rules](references/classification.md) for record-grounded purpose and modality labels.
- [Output schema](references/output-schema.md) for accounting, provenance and field availability.

Offline boundary requirements: `is_industry`, when supplied, must contain boolean `True`/`False` values (CSV boolean columns are accepted); use `sponsor_class` for registry labels rather than Y/N or INDUSTRY/OTHER flag strings. Coverage `phases` must be an array of nonblank strings; use `[]` for no phase filter.
