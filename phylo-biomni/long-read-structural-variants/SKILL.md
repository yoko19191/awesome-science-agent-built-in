---
name: long-read-structural-variants
description: Call and normalize structural variants from a user-selected aligned ONT
  or PacBio long-read BAM with Sniffles2 plus cuteSV (or compare an explicitly selected
  second callset), validate BAM/reference identity, and report descriptive counts,
  sizes, and caller concordance. Not for truth-set benchmarking, clinical interpretation,
  read QC/basecalling, assembly, isoform quantification, methylation, or SNV/indel
  calling.
category: genomics_genetics
visibility: public
starting-prompt: Call and normalize structural variants from aligned long reads with
  two independent caller routes, then summarize callset quality, size distributions,
  and descriptive concordance.
---
# Long Read Structural Variants

## When to Use This Skill

Use this skill for dual-caller descriptive analysis from an already aligned, sorted, indexed ONT
or PacBio BAM. It runs Sniffles2 plus installed cuteSV, or compares Sniffles2 with an explicitly
selected user VCF. Alignment and read QC are upstream. Caller agreement is descriptive robustness
evidence, not accuracy or clinical interpretation.

## Inputs

Before running `scripts/run_sv.py --execution-mode analysis --second-caller cutesv|user_vcf`, seal
schema-v2 `input_selection.json` with `scripts/input_selection.py`. Explicitly select the BAM,
reference FASTA, upstream `run.json`, completed QC `identity_check.json`, and any user-provided
second callset. Validate and hash-bind their identities and indexes. Partial acquisition, inferred
or package data, symlink escapes, missing/failed identity evidence, and reused non-empty results
roots are hard stops.

- `sample.bam` and adjacent `.bai`: long reads aligned to the exact reference, coordinate-sorted and indexed.
- `reference.fasta` and adjacent `.fai`: exact reference used for alignment.
- `run.json`: upstream platform, chemistry, basecaller, model, molecule type, and non-empty `reference_build`.
- Completed upstream `identity_check.json`: normalized platform/read type, successful identity verdict, QC hashes, and `coverage_estimate_status=computed` with positive coverage.
- Either installed cuteSV selected with `--second-caller cutesv`, or an explicitly selected VCF sealed as `second_caller_vcf` and passed with `--second-caller user_vcf --second-caller-vcf ...`.
- The public entrypoint is intentionally two-stage within one Biomni task. First run it without an infographic; it computes science, freezes every science output and imported scientific support-source hash in `sv/science_stage_receipt.json`, writes the exact facts-derived claim ledger/prompt, and exits successfully with explicit state `awaiting_infographic`. Create the image with Biomni GenerateImage from that exact prompt. Then rerun the same command with `--resume --infographic-file FILE`; resume revalidates selected inputs, support-source identity, and every frozen science-output hash before finalization.
- Keep optional benchmark scratch outside the results root. If a `benchmark_addon/` evidence tree is present at finalization, the entrypoint preserves every file and hash in a deterministic `benchmark_addon_evidence.zip` plus manifest, then removes the expanded duplicate so the published inventory remains fully enumerable by deterministic QA.

  Do not paraphrase or extend `sv/infographic_prompt.txt`; it is derived from the exact current-run facts and contains the only allowed claim texts.

The BAM header sequence dictionary must match the reference lengths. If an assembly tag is present it must agree with `run.json.reference_build`; otherwise the run stops before calling.

## Report Styling Provider Policy

The contract default is `pdf-report-generation`. Resolve an override only from an affirmative,
immutable end-user message naming an installed compatible report-styling provider. Customer, tenant,
project, account, folder, dataset, assistant, or caller context is not selection evidence. A missing,
malformed, conflicting, revoked, incompatible, or unavailable requested provider stops report
completion; never fall back or approximate the requested style.

The selected provider owns report presentation only: typography, layout, page chrome, palette, logo,
and PDF metadata. It must consume the same `report_content.json`, figures, infographic, facts,
references, limitations, provenance, and non-PDF outputs. Render `report_content.json` through the
selected provider and pass its completed PDF with `--provider-rendered-report`.

## Workflow

1. Seal and verify input selection; require a fresh external results root.
2. Audit the installed commercial-use license evidence for required Python/runtime dependencies.
3. Validate and hash-bind upstream QC identity; cross-check `--platform` (`ont|hifi`) against its normalized platform/read type, then check BAM/reference/run-metadata identity.
4. Derive effective support from validated upstream coverage (Sniffles2: ceil 10%, minimum 2; cuteSV: ceil 10%, minimum 10), record it, and execute the selected caller routes.
5. Normalize, sort, bgzip, and tabix-index both VCFs through pysam/bcftools bindings.
6. Validate both indexed VCFs and summarize PASS calls by SV class, non-breakend size, and class-aware breakpoint concordance.
7. Derive `report_facts.json`, `report_content.json`, and `report_structure.json` from the current facts, provenance, validated figure inventory, and complete user-facing `references/report_references.json` catalog. Do not restate remembered values in report prose, omit catalog entries, or substitute internal review identifiers.
8. Pause after the science stage, generate the infographic from exact `sv/infographic_prompt.txt`, then resume. Resolve the report provider before creating PDF bytes. Render `report_content.json` through the selected provider and pass its completed PDF with `--provider-rendered-report`, with the unchanged infographic as the first substantive visual on page 1. Extract text and render every PDF page beneath `report_review/`.
9. Call the strict receipt gate last. A missing, undersized, stale, unstyled, structurally incomplete, or hash-inconsistent PDF/report artifact is a failed run. Standard execution records visual quality honestly as `not_evaluable` and leaves visual acceptance to post-run review; an explicit visual defect remains blocking. GenerateImage lineage is evaluated only from a platform-provided, read-only trace for the current task. `execution_trace/transcript.jsonl` under the results root is agent-writable and never satisfies lineage, regardless of its IDs or contents. When the native trace is unavailable, incomplete, or cannot prove one exact call/result pair, the receipt records `not_evaluable_platform_trace` and keeps `infographic_lineage_verified` release-blocking. Do not report task success unless `run_receipt.json` exists and has an empty `release_blocking_gates` array.

## Outputs

- `sv/calls_sniffles2.norm.vcf.gz` and `.tbi`
- `sv/calls_second.norm.vcf.gz` and `.tbi`
- `sv/sv_counts.tsv` and `sv/concordance.tsv`
- `sv/provenance.json`, `sv/identity_check.json`, `sv/dependency_licenses.json`
- `sv/facts_payload.json`
- `sv/sv_calls.tsv` with one row per delivered call and its FILTER, QUAL, genotype, genotype quality, and read support when present in the caller VCF
- `sv/infographic_claims.json`, whose allowed claims are each bound to an exact facts field/value
- `sv/infographic_prompt.txt`, generated only from those bound claim texts and a fixed forbidden-claims list
- `sv/science_stage_receipt.json`, whose exact output inventory and SHA-256 values must all verify on resume
- `figures/figure_2_call_svs.{png,svg}`, `figures/figure_3_normalize_and_index.{png,svg}`, and `figures/figure_4_compare_callers_concordance.{png,svg}`
- `figures/manifest.json`
- `workflow_infographic_long_read_sv.png`
- `report_facts.json`, `report_content.json`, and `report_structure.json`
- `report_long-read-structural-variants.pdf`
- `report_review/report.txt` and every rendered page under `report_review/pages/`
- When optional benchmarking was retained, `benchmark_addon_evidence.zip` and its exact hash/inventory ledger `benchmark_addon_evidence_manifest.json`
- `qc_run_log.json` and `run_receipt.json`

Each data figure must be kept with its caption and adjacent claim-evidence-interpretation paragraph
from `figures/manifest.json`. The paragraph must state what the current plot means and its main
limitation; a detached caption-only gallery is not acceptable.

If reference identity fails, only the diagnostic `sv/not_computable.json` is emitted after the identity record and the public entrypoint exits nonzero without claiming report completion. Facts must not claim accuracy, sensitivity, precision, or external validation.

## Scientific Limitations

- Low depth, low variant-allele fraction, tandem repeats, segmental duplications, and alignment artifacts can reduce sensitivity or breakpoint precision.
- Counts and caller concordance are descriptive and depend on caller versions, the recorded coverage-scaled support thresholds, alignment, coverage, and reference build; agreement does not establish correctness.
- BND and complex rearrangement representations are not directly comparable to length-bearing INS/DEL/DUP/INV records.
- Review candidate calls with read-level evidence and orthogonal methods before biological or translational decisions.

## Common Issues

- **Reference identity failure:** BAM contigs or lengths, the alignment assembly tag, `run.json`,
  and the selected FASTA disagree. Realign to the declared reference or select the matching files;
  never rename contigs to force a pass.
- **Insufficient or missing coverage evidence:** support thresholds cannot be derived without the
  validated upstream coverage artifact. Complete long-read QC first.
- **Second caller unavailable:** install the selected cuteSV version or provide a separately sealed
  second VCF. Do not substitute another caller silently.
- **Large caller disagreement:** inspect support, FILTER, genotype, repeats, and breakpoint
  representation before interpreting unique calls. Agreement is not a truth label.
- **Breakend/complex representation mismatch:** BNDs and complex events are not directly comparable
  to simple length-bearing variants and require event-level review.
- **Infographic resume failure:** any changed science artifact invalidates the frozen receipt. Start
  a fresh science stage rather than modifying outputs between stages.

## Suggested Next Steps

- Review prioritized shared and caller-unique variants in a genome browser with read-level support.
- Confirm biologically important events with an orthogonal assay or an independently generated
  callset; use a matched truth set only in a separately declared benchmarking workflow.
- Annotate genes and regulatory elements only after reference build and breakpoint identity are
  resolved.
- Re-run at adequate depth when coverage-scaled support or low variant allele fraction limits
  detection.
- Use a dedicated small-variant workflow for SNVs/indels and retain this skill's SV scope.

## Related Skills

- `long-read-qc-basecalling` — establish read identity, quality, and coverage before SV calling.
- `long-read-assembly` — assemble long reads de novo rather than call variants from alignments.
- `variant-calling-from-sequencing` — call and benchmark SNVs/indels.
- `long-read-methylation` — analyze modified bases from compatible long-read data.

## Reference

See `references/sv_calling.md` for dual-caller scope, matching rules, and citations.
