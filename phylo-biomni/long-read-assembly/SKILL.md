---
name: long-read-assembly
description: De novo assemble explicitly selected long reads with a production assembler
  or assess a supplied draft; unavailable production assemblers or polishers produce
  an honest planned-command external-tool stop without substitution.
category: genomics_genetics
visibility: public
starting-prompt: Assemble or assess long-read sequencing data for a microbial isolate
  or bounded metagenome, reporting contiguity, completeness, consensus quality, and
  contamination with explicit tool limitations.
---
<!-- archetype: analysis-workflow -->

<!-- contract: evidence-v1 -->

# Long Read Assembly

## When to Use This Skill

Use for long-read (ONT or PacBio) de novo assembly, polishing, and assembly QC with contamination screening, for a microbial isolate or bounded metagenome. Not read QC/basecalling, variant/SV, isoform, or methylation calling.

## Why This, Not The Obvious Thing (READ FIRST)

The obvious move is to run one assembler with default settings regardless of the declared read type — hifiasm on noisy ONT, or Flye's HiFi mode on ONT — which silently produces a wrong or fragmented assembly. This skill derives the read type and rejects an incompatible assembler before assembling.

## Inputs

- `reads.fastq.gz` — ONT or PacBio long reads (gzipped FASTQ). Required; the assembly source.
- `run.json` and the exact completed upstream `long_read_qc/identity_check.json` — both are explicit selected inputs. The selected run.json bytes must match `run_json_sha256`; missing, malformed, failed, or mismatched identity evidence stops before assembly. Read type is derived from this artifact, not re-inferred by a copied chemistry gate.
- `reference.fasta` — reference genome for QUAST-style genome fraction (optional; genome fraction is 0 without it).
- `markers.fasta` — single-copy ortholog marker sequences for the BUSCO-style completeness proxy (optional).
- `contaminant_ref.fasta` — contaminant reference for the contig-level screen (optional; without it the screen reports not-run, never clean).
- `--draft F.fasta` — a pre-computed draft assembly, required only for `--assembler provided_draft`.
- If an input is missing or run.json lacks a required key, stop and report the missing field rather than guessing; never fabricate an assembly.
- `execution_mode` must be `analysis`, and `--backend` must be `hpc` or `local`. Inputs must be explicitly selected. Unavailable or unsupported production-tool execution produces the documented external-tool stop; no bundled substitute assembler or polisher is used.

## Outputs

- `report_long_read_assembly.pdf` — the single canonical PDF, rendered by the provider selected from immutable user messages; defaults to `pdf-report-generation`. For the default path only: Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), task context, methods or sources, results, conclusions, figures where applicable, references, and next steps
- `long_read_assembly/assembly_qc.tsv` — primary machine-readable result table; one row per QC metric (`metric`, `value`, `unit`) covering contiguity (n_contigs, contig N50, largest contig, total length, mean contig length, circular count, GC%), reference genome fraction, BUSCO-style completeness, and Merqury-style consensus QV (draft and polished)
- `long_read_assembly/qv_kmer_spectrum.tsv` — the observed read k-mer multiplicity spectrum used to derive the solid-k-mer threshold; provenance records its hash and every QV input hash.
- `long_read_assembly/assembly.fasta` — polished assembly (FASTA; `assembly_draft.fasta` is the pre-polish draft); `long_read_assembly/assembly_graph.gfa` — assembly graph (one `S` line per contig)
- `long_read_assembly/contamination.tsv` — contig-level contamination screen; one row per screen field (method, status, and flagged/retained contig counts)
- `long_read_assembly/provenance.json`, `identity_check.json`, `assembly_readtype_check.json` — provenance echo, the inherited model/chemistry gate, and the assembler↔read-type gate
- `report_facts.json` — evidence-bearing claims, operational definitions, provenance, and validated denominator/completion partitions
- `long_read_assembly/input_selection.json` — sealed logical-input identities and canonical manifest hash echoed by facts, provenance, and stop receipts.

**Write the report to the results root under the name above.** Data tables, figures and intermediates go in `data/`, `figures/`, `tables/`. Note that `GenerateImage` strips directory components, so schematics always land at the root regardless of the path you pass it.

## Clarification Questions

1. **Which reads should I assemble?** (select one)

   - `own_data` — Explicitly selected reads.fastq.gz + run.json (+ optional reference/markers/contaminant refs); runtime branch ID `input_source:own_data`
2. **Which assembler (must match the declared read type)?** (select one)

   - `auto` — Auto by read type (Flye for noisy ONT/CLR, hifiasm for HiFi); runtime branch ID `assembler:auto`
   - `flye` — Flye/metaFlye (ONT/CLR/HiFi) via HPC/user binary; runtime branch ID `assembler:flye`
   - `hifiasm` — hifiasm (PacBio HiFi) via HPC/user binary; runtime branch ID `assembler:hifiasm`
   - `verkko` — Verkko (HiFi + ONT-UL) via HPC/user binary; runtime branch ID `assembler:verkko`
   - `provided_draft` — QC/polish a draft assembly I already have; runtime branch ID `assembler:provided_draft`
3. **How should the draft be polished (accuracy-improving order)?** (select one)

   - `racon` — Racon (MIT) — the only offered long-read polisher (rough consensus; accuracy limits disclosed); runtime branch ID `polish:racon`
   - `none` — No polishing (QC the draft as-is); runtime branch ID `polish:none`

   Research-use-only polishers (e.g. Medaka, Oxford Nanopore Technologies Public License — commercial use prohibited) are **not offered** and are hard-refused (never run, never bundled); a request for one falls back to Racon/none.
4. **How should contamination be screened?** (select one)

   - `kmer` — k-mer containment vs a contaminant reference (default; no external tool); runtime branch ID `contamination_method:kmer`
   - `reference_align` — request reference alignment; the bundled adapter discloses effective `kmer_containment` triage and never claims minimap2 ran; runtime branch ID `contamination_method:reference_align`
   - `taxonomic_db` — Kraken2/FCS-GX DB (requires DB; else stop); runtime branch ID `contamination_method:taxonomic_db`
5. **Assemble as a single isolate or a metagenome?** (select one)

   - `isolate` — Single isolate/genome (BUSCO completeness); runtime branch ID `mode:isolate`
   - `metagenome` — Metagenome/MAGs (CheckM2/MIMAG; binning is a gated HPC step); runtime branch ID `mode:metagenome`

## Standard Workflow

**Step 1 — Load and validate the input.** Enforce every item in `## Inputs`; stop with `not_computable` on a mismatch.

**Step 2 — Load reads and upstream identity.** `python scripts/run_assembly.py --execution-mode analysis --requested-dataset NAME --selection-evidence EVIDENCE --reads READS.fastq.gz --reads-origin uploaded --reads-requested-source SOURCE --run-json run.json --run-json-origin uploaded --run-json-requested-source SOURCE --identity-check identity_check.json --identity-check-origin uploaded --identity-check-requested-source SOURCE --assembler auto --backend hpc --polish none --out-root /mnt/results` seals the exact inputs. It opens the upstream artifact, validates schema/tool/verdict/applicability/hash semantics, and requires its run hash to match the selected run.json. Optional references require their own origin and requested-source fields.

**Step 3 — Guard the assembler against the declared read type.** Resolve the requested assembler (auto -> Flye for noisy ONT/CLR, hifiasm for HiFi) and check it against the derived read_type; write assembly_readtype_check.json. Success: incompatible_violations = 0. An incompatible assembler (e.g. hifiasm on ONT) finalizes not_computable before assembling.

**Step 4 — Assemble the genome.** Require `--backend hpc|local`. If Flye/hifiasm/Verkko is unavailable or not wired, stop cleanly in `assembly_status.json` with the exact planned command and no contigs. `--assembler provided_draft` loads an explicitly selected draft. No in-package assembler or substitute is available.

**Step 5 — Assess assembly contiguity.** Compute QUAST-style contiguity from the contigs: n_contigs, largest contig, contig N50 (bp-weighted, not the mean contig length), total length, circular count, GC. Write to assembly_qc.tsv. Success: contig N50 is reported and is not the mean contig length.

**Step 6 — Polish and track consensus QV.** Request a production Racon route or explicitly choose `none`. The bundled adapter validates polisher order but does not execute a production polisher: when polishing is requested it records the exact planned command and stops `stopped_external_tool` without substitution. With `--assembler provided_draft`, provenance states `assembly_source=user_supplied_draft` and `assembler_executed=false`; it never attributes that draft to a named assembler. For QV, derive the trusted-k-mer cutoff from the observed multiplicity-spectrum valley, write `qv_kmer_spectrum.tsv`, and hash-bind the reads, draft/polished assemblies, spectrum, threshold, and solid-k-mer set. An ambiguous spectrum stops QV rather than using a fixed cutoff. A Q60 display ceiling is a **saturated lower bound**, never an exact estimate.

**Step 7 — Assess genome completeness.** Compute BUSCO-style completeness: the fraction of supplied single-copy markers found complete in the assembly, and QUAST-style genome fraction vs the supplied reference. Write to assembly_qc.tsv. Success: busco_complete_pct and genome_fraction_pct are reported (0 when the reference/markers are absent).

**Step 8 — Screen contigs for contamination.** Screen each contig against the supplied contaminant reference using deterministic k-mer containment triage. This package records `ok_kmer_triage`; it never calls that state plain `ok` or implies the anchor's reference-alignment evidence strength. Write contamination.tsv and contamination_per_contig.json. Success: flagged + retained contigs sum to n_contigs, or the status records why the screen did not run.

**Step 9 — Generate and validate figures.** Render only results produced by the preceding validated steps, write `figures/manifest.json`, then validate every declared artifact before facts or the receipt can use it. Generate the draft-versus-polished QV figure only when a polisher produced a distinct assembly. If no polishing occurred, record `file: null` plus the reason for step 6 and report the single assembly QV in text and tables. If polishing produced a distinct assembly but the QV is unchanged, retain the comparison figure because the no-change result is meaningful.

```python
from report_qc import assert_figures
figures = assert_figures("figures/manifest.json")
```

## Figures

One representative figure per result-producing step, showing **this run's actual result** — not a schematic, not an illustrative example. Every figure needs a caption and an adjacent claim-evidence-interpretation paragraph stating what the result means and its main limitation. If a step genuinely has nothing to plot, replace its row with a one-line reason. Both fields are required at run time; a detached caption-only figure gallery is not acceptable.

| Step | File                                                                                   | What it must make visible                                                                                                                                                                                                                                                                          |
| ---- | -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 4    | `figures/figure_4_assemble_the_genome.png`                                           | The assembled structure this run produced: each contig's length as a horizontal bar, coloured circular vs linear, annotated with contig count, circular count, largest contig, and total assembly length.                                                                                          |
| 5    | `figures/figure_5_assess_assembly_contiguity.png`                                    | The contiguity (Nx) curve — contig length vs cumulative assembly percentage — with the contig N50 marked as a reference line, showing N50 as a bp-weighted quantity distinct from the mean contig length.                                                                                        |
| 6    | `figures/figure_6_polish_and_track.png`, when polishing produced a distinct assembly | Reference-free Merqury-style consensus QV of the draft vs the polished assembly, making the polishing effect visible (expected qv_polished ≥ qv_draft). Without a distinct polished assembly, the manifest records `file: null` and explains that the single QV is reported in text and tables. |
| 7    | `figures/figure_7_assess_genome_completeness.png`                                    | Genome completeness: complete vs missing single-copy marker counts (BUSCO-style %) shown alongside the reference genome fraction, keeping gene-content completeness distinct from sequence coverage.                                                                                               |
| 8    | `figures/figure_8_screen_contigs_for.png`                                            | Contamination screen outcome: contaminant-flagged vs retained contig counts, annotated with the screen method and status so a not-run screen is never read as clean.                                                                                                                               |

The report reads this inventory from `report_facts.json`'s `figures` array rather than restating it, so it cannot claim a figure that was never produced.

**Step 10 — Write `report_facts.json` from the runtime payload.** The workflow must produce `long_read_assembly/facts_payload.json` with every headline, denominator, and partition-member field declared in `skill_contract.json`. Semantic gates run before the facts artifact is written, so a failing run never produces claims a report could quote.

```python
from report_qc import write_facts_from_artifact
write_facts_from_artifact('report_facts.json', source='long_read_assembly/facts_payload.json', figures=figures, contract="skill_contract.json")
```

### Step 11 — Final report (MANDATORY TERMINAL STEP)

**The run is not complete until this step has produced `report_long_read_assembly.pdf` at the results root.**
Resolve the provider with `report_qc.report_render_plan()` before creating any PDF bytes. The resolver reads immutable user-message evidence only; a provider name in an assistant message, CLI argument, customer name, tenant, project, account, folder, or dataset is not selection evidence.

```python
from report_qc import report_render_plan
render_plan = report_render_plan()
```

- With no explicit user style request, follow the default path. Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), task context, methods or sources, results, conclusions, figures where applicable, references, and next steps
- With an explicit compatible provider, load that provider's complete instructions and required assets and render the same canonical filename.
- Missing, malformed, conflicting, revoked, or incompatible provider selection blocks completion. Never fall back, approximate a requested style, or produce an unverified companion PDF.
- The selected provider owns presentation only. Preserve every fact, table, figure, caption, threshold, claim, reference, limitation, provenance record, artifact path, and non-PDF output.

Produce one combined PDF with a short narrative. rendered page images are inspection evidence only, never substitutes for the PDF. End the task with a concise conclusion and links to the PDF and supporting artifacts, not a bare file listing.

The PDF must use these visible top-level sections in this order, adapting their contents to this skill rather than adding generic filler:

1. `Task Context` — the research or practitioner question, supplied inputs, scope, and decision the output informs.
2. `Methods & Sources` — Methods & Sources covers data provenance, filtering, parameters, software, and the analysis design; Results reports validated quantitative findings and figures.
3. `Results` — the run's actual outputs and evidence, never a description of what the skill could do.
4. `Conclusions & Interpretation` — supported takeaways, their practical meaning, and appropriate next steps.
5. `Limitations` — run-specific uncertainty, missing coverage, failed or unavailable checks, and claims the evidence does not support.

Include references and a compact output-artifact table where applicable. Empty boilerplate does not satisfy a section.

**Report grounding (MANDATORY).** Every run-specific value in the PDF — contiguity metrics, contamination status/counts, completeness, gate details, and QV — must be read from the machine-readable artifacts and never hardcoded. Assembly attribution comes from `provenance.json:assembly`: `user_supplied_draft` with `assembler_executed=false` must never name an assembler as having run. QV claims must bind to `qv_inputs` and `qv_kmer_spectrum.tsv`. The evidence tier comes verbatim from `report_facts.json:evidence_tier`.

Produce exactly one GenerateImage summary infographic named `infographic_assembly_qc_overview.png`. Preserve the accepted five-stage pipeline design, in this order: **Assemble → Contiguity → Polish → Completeness → Contamination**. Summarize contig count and total length; contig N50 and largest contig; draft versus polished consensus QV; BUSCO-style marker completeness and reference genome fraction; and contamination flagged versus retained. Every identifier, value, and run label in the image must be read from the current run artifacts; never hardcode the analysis values. Embed this unchanged artifact as the first substantive visual in the report; introductory text-only pages are allowed. It complements, rather than replaces, the data-driven per-step figures in `figures/`.

Immediately after GenerateImage returns, and before rendering the report, freeze its native lineage and exact pixels at the tool boundary:

```python
from report_qc import record_generated_infographic
infographic_filename = "infographic_assembly_qc_overview.png"
record_generated_infographic(infographic_filename, log_path="qc_run_log.json")
```

Do not edit, recompress, resize, rename, or replace the image after this snapshot. Pass the same `infographic_filename` to the report renderer and to `write_receipt` below.

If the joined standard-runtime trace explicitly marks the GenerateImage arguments as truncated, the package still requires the same-ID successful result naming this exact filename and still binds the returned file bytes, decoded pixels, first substantive PDF image, and final PDF hash. Only the omitted submitted prompt/request fields become `not_evaluable_platform_trace`, with `independent_post_run_campaign_audit_required=true`; this is not a positive provenance claim. Missing, malformed, ambiguous, mismatched, or contradictory call/result evidence still fails.

Render the PDF to a fresh workspace file named by `workspace_report_file`; do not open or truncate an existing PDF on the object-backed results mount. The `staged_copy` call below publishes the completed file under its declared results-root name.

**Visually inspect the rendered report.** Use ordinary `Read` to load the PDF when
native PDF input is supported, or load every rendered page PNG with an image-capable
model. Inspect the returned media for blank pages, clipped labels, and incorrect
figures, and fix defects before delivery. Loading media or extracting text alone is
not a passing review. If visual input is unavailable, state that limitation. The
receipt checks text, rendering, hashes, and scientific provenance; it does not
attest that a visual review occurred.

Then verify and authorize the run through the package-owned terminal boundary. `write_receipt` runs every gate — the report exists at the results root and is big enough, each declared figure is present and non-blank, the finished PDF carries the selected provider's verified PDF markers, and the infographic has one same-ID native GenerateImage call/result pair whose immediate file/pixel snapshot is unchanged and appears exactly once as the first substantive visual in the final hash-bound PDF — then records what each one returned. Introductory text-only pages may precede it, but no other image may. Run bundled commands through `run_bundled`, which writes the QC-owned `qc_run_log.json` from the subprocess result and output hashes. Do not author execution events or copy transcript identifiers. Produce every source-witness artifact declared in `skill_contract.json`. The receipt writer rejects unmatched hashes, partial page coverage, source-value disagreement, infographic mutation, or report-binding mismatch. It raises if any gate failed, **after** writing the receipt, so a failed run leaves the diagnostic behind. Do not catch or ignore that failure. Never announce or link a report unless `finalize_delivery` returns its delivery authorization.

Record each answer from `## Clarification Questions` in `selected_branch_ids` using the displayed `<question_id>:<choice_id>` value. The receipt derives its required outputs only from those selected branches; do not union mutually exclusive branch artifacts.

```python
from report_qc import (finalize_delivery, outputs_for_selected_branches, record_pdf_review,
                       staged_copy, write_receipt)
selected_outputs = outputs_for_selected_branches(selected_branch_ids)
# Both input-source branches include provenance.json, identity_check.json, and
# assembly_readtype_check.json, so every completed run verifies these common outputs.
staged_copy(workspace_report_file, "report_long_read_assembly.pdf")
# Render/extract the PDF and store advisory notes. Inspect the returned page images
# with ordinary Read; this programmatic receipt does not attest visual quality.
record_pdf_review(
    report_name="report_long_read_assembly.pdf", text_artifact=extracted_text_file,
    rendered_page_files=rendered_page_files,
    reviewed_page_numbers=reviewed_page_numbers,
    review_attestation=visual_review_notes,  # optional supplementary notes; never the verdict
)
delivery = finalize_delivery(
    report_name="report_long_read_assembly.pdf",
    figures=figures,
    receipt_factory=lambda: write_receipt(
        report_name="report_long_read_assembly.pdf",
        figures=figures,
        figure_not_applicable_reason=None,
        bundled_files=['scripts/run_assembly.py'],
        outputs=selected_outputs,
        infographics=[infographic_filename],
        qc_run_log="qc_run_log.json",
        strict=True,
    ),
)
final_response = delivery["terminal_message"]
```

The receipt is `run_receipt.json` and its successful terminal authorization is `delivery_authorization.json`, both at the **results root**, not beside this SKILL.md — once this skill is installed its own directory is read-only, so per-run state written there cannot work. The receipt uses `phylo-run-receipt/3` and records `execution_contract_satisfied`, `outputs_appeared`, `report_at_results_root`, `figure_contract_satisfied`, `facts_artifact_verified`, `report_style_verified`, `text_extracted`, `pages_rendered`, `visual_review_attested`, `report_sections_present`, `source_assertions_verified`, `infographic_lineage_verified`, each with the path, byte count, selected-provider markers, source hashes, or transcript record the outcome was read from. `visual_review_attested` is `not_applicable`: this receipt does not attest visual inspection. Only `infographic_lineage_verified` may be `not_evaluable_platform_trace`, with explicit generation-trace limitation evidence plus `independent_post_run_campaign_audit_required=true`.

**Do not write this file by hand.** `check_skill.py --require-run-receipt` requires the schema marker and per-outcome evidence, so a hand-written block of `true`s fails. Whatever did not hold is recorded `false` with a `<key>_reason`; fix the run rather than the receipt.

## Scientific caveats

- The obvious move is to run one assembler with default settings regardless of the declared read type — hifiasm on noisy ONT, or Flye's HiFi mode on ONT — which silently produces a wrong or fragmented assembly. This skill derives the read type and rejects an incompatible assembler before assembling. Evidence: `report_facts.json:assembler_readtype_compatible`.
- Contiguity is not correctness: one long contig can be a misassembly or a collapsed repeat, and an N50 inflated by a few concatemeric contigs overstates quality. Contig N50 is reported next to genome fraction and QV, never as accuracy. Evidence: `report_facts.json:contig_n50`.
- Merqury-style QV is reference-free consensus quality from read k-mers; it is not reference-based accuracy or genome fraction, and low coverage can inflate it. QV and genome fraction are reported separately. Evidence: `report_facts.json:merqury_qv`.
- Contamination screening is only as good as the supplied reference/DB; with none, the screen reports not-run, never clean. A 0-flagged result with a not_run status means unscreened, not contaminant-free. Evidence: `report_facts.json:contamination_screen_status`.
- Polishing uses only the permissively-licensed (MIT) Racon route and never fabricates a result; QV is reported before and after. Racon is a rough long-read consensus polisher (no neural-network consensus step), so residual consensus errors can remain and accuracy on real ONT/PacBio data is not validated here. Research-use-only polishers (e.g. Medaka, ONT Public License — commercial use prohibited) are not offered and are hard-refused, never run. Evidence: `report_facts.json:polish_status`.
- A reference-free Merqury-style QV that reaches the Q60 display ceiling (100% of assembly k-mers supported) is a saturated lower bound, not an exact estimate; it is reported as `qv_polished_saturated`/`capped_at_60`, never as a measured Q60. Evidence: `report_facts.json:qv_polished_saturated`.
- `assembly.fasta` remains the all-contig assembly. Separate `*_post_screen` fields summarize contigs retained after k-mer triage; they are not a filtered deliverable and are never labeled clean or contaminant-free. Evidence: `report_facts.json:n_contigs_post_screen`, `report_facts.json:retained_after_triage_contigs`, `report_facts.json:contamination_effective_method`.
- No assembler is bundled. Real ONT/HiFi assembly uses the gated Flye/hifiasm/Verkko path or an explicitly selected provided draft; unavailable production execution stops with a planned command and no fabricated contigs. Evidence: `report_facts.json:assembly_backend`.
- This skill does not perform read-level QC or basecalling (inherited from long-read-qc-basecalling); a model/chemistry mismatch stops the run before assembly. Evidence: `report_facts.json:model_chemistry_compatible`.

Each caveat must name the artifact field or the number that says whether it fired. An unbound caveat is prose; a bound one is a gate.

## Evidence Tier

Report only the evidence tier established by the current run's `report_facts.json` and receipt. The PDF report's Limitations section must reproduce `report_facts.json:evidence_tier`; static package metadata does not establish result evidence.

## Data Sources & Licenses

- numpy (2.x): https://github.com/numpy/numpy — license BSD-3-Clause; commercial use `allowed` (BSD-3-Clause permissive (standard scientific stack).); included `true`; verify with `scripts/lra.py`.
- matplotlib (3.x): https://github.com/matplotlib/matplotlib — license Matplotlib (PSF-based, BSD-compatible); commercial use `allowed` (PSF-based license permits commercial use (standard scientific stack).); included `true`; verify with `scripts/lra.py`.
- reportlab (4.x+): https://github.com/MrBitBucket/reportlab-mirror — license BSD; commercial use `allowed` (BSD permissive; underlies the pdf-report-generation report.); included `true`; verify with `scripts/run_assembly.py`.
- mappy (minimap2) (2.x): https://github.com/lh3/minimap2 — license MIT; commercial use `allowed`; included `false`; the bundled adapter does not invoke it and records k-mer containment as the effective method.
- Flye / metaFlye (external): https://github.com/mikolmogorov/Flye — license BSD-3-Clause; commercial use `allowed` (BSD-3-Clause permits commercial use; invoked as an external HPC/user binary, not bundled.); included `false`; verify with `references/assembly_tool_selection.md`.
- hifiasm (external): https://github.com/chhylp123/hifiasm — license MIT; commercial use `allowed` (MIT permits commercial use; invoked as an external HPC/user binary, not bundled.); included `false`; verify with `references/assembly_tool_selection.md`.
- Verkko (external): https://github.com/marbl/verkko — license Public Domain (US Gov); commercial use `allowed` (US-Gov public domain; external HPC/user tool, not bundled.); included `false`; verify with `references/assembly_tool_selection.md`.
- Canu (external): https://github.com/marbl/canu — license GPL-2.0; commercial use `allowed` (GPL permits commercial USE of the unmodified binary; not bundled or linked, invoked as an external tool. Source vs binary/use licensing recorded separately.); included `false`; verify with `references/assembly_tool_selection.md`.
- Raven (external): https://github.com/lbcb-sci/raven — license MIT; commercial use `allowed` (MIT permits commercial use; external tool, not bundled.); included `false`; verify with `references/assembly_tool_selection.md`.
- wtdbg2 (external): https://github.com/ruanjue/wtdbg2 — license GPL-3.0; commercial use `allowed` (GPL permits commercial USE of the unmodified binary; not bundled or linked, invoked as an external tool. Source vs binary/use licensing recorded separately.); included `false`; verify with `references/assembly_tool_selection.md`.
- Unicycler (external): https://github.com/rrwick/Unicycler — license GPL-3.0; commercial use `allowed` (GPL permits commercial USE of the unmodified binary; not bundled or linked, invoked as an external tool. Source vs binary/use licensing recorded separately.); included `false`; verify with `references/assembly_tool_selection.md`.
- QUAST / MetaQUAST (external): https://github.com/ablab/quast — license GPL-2.0; commercial use `allowed` (GPL permits commercial USE of the unmodified binary; not bundled or linked, invoked as an external tool. Source vs binary/use licensing recorded separately.); included `false`; verify with `references/assembly_qc_interpretation.md`.
- Merqury (external): https://github.com/marbl/merqury — license Public Domain (US Gov); commercial use `allowed` (US-Gov public domain; external tool. The bundled command computes a Merqury-STYLE reference-free QV natively.); included `false`; verify with `references/assembly_qc_interpretation.md`.
- BUSCO / compleasm (external): https://gitlab.com/ezlab/busco — license MIT; commercial use `allowed` (MIT permits commercial use; external tool with lineage DBs. The bundled command computes a BUSCO-STYLE marker proxy natively.); included `false`; verify with `references/assembly_qc_interpretation.md`.
- CheckM2 (external): https://github.com/chklovski/CheckM2 — license GPL-3.0; commercial use `allowed` (GPL permits commercial USE of the unmodified binary; not bundled or linked, invoked as an external tool. Source vs binary/use licensing recorded separately.); included `false`; verify with `references/metagenome_mode.md`.
- Racon (external): https://github.com/lbcb-sci/racon — license MIT; commercial use `allowed` (MIT permits commercial use; the analysis path performs a Racon-STYLE consensus polish natively. Sole polishing route after removal of research-use-only ONT polishers.); included `false`; verify with `references/polishing_order.md`.
- NextPolish (external): https://github.com/Nextomics/NextPolish — license GPL/BSD; commercial use `allowed` (GPL permits commercial USE of the unmodified binary; not bundled or linked, invoked as an external tool. Source vs binary/use licensing recorded separately.); included `false`; verify with `references/polishing_order.md`.
- Kraken2 (external): https://github.com/DerrickWood/kraken2 — license MIT; commercial use `allowed` (MIT permits commercial use; requires a user-provided DB. Not bundled.); included `false`; verify with `references/contamination_screening.md`.
- FCS-GX (external): https://github.com/ncbi/fcs — license Public Domain (US Gov); commercial use `allowed` (NCBI public domain; ~470 GB RAM for the GX database. Not bundled.); included `false`; verify with `references/contamination_screening.md`.

The inherited ONT basecaller model-name / chemistry convention is recognized only as declared data provenance (run.json `basecaller_model`/`flowcell_chemistry`); no ONT basecalling software (Dorado/Medaka) is used, bundled, invoked, or depended on. See `references/model_chemistry_compatibility.md`.

## Resource Identity

Resolve `assembler` against https://github.com/chhylp123/hifiasm and compare `read_type`. Write decisions to `long_read_assembly/assembly_readtype_check.json` and require `incompatible_violations = 0` through a source-assertion witness. A valid identifier attached to mismatched metadata is a failure, not a resolved resource; exclude it or finalize `not_computable` before facts or the report are written.

## Common Issues

| Symptom                                                        | Cause                                                                                                | Fix                                                                                                                 |
| -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Assembly stops before producing contigs                        | The selected assembler is unavailable or incompatible with the upstream read type                    | Install/wire the declared production assembler or choose a compatible one; never substitute an in-package assembler |
| Very high N50 but weak completeness or QV                      | Collapsed repeats, concatemeric contigs, low coverage, or misassembly can inflate contiguity         | Review the assembly graph, coverage, genome fraction, marker completeness, and QV together                          |
| QV is not computable                                           | The read k-mer spectrum is too shallow or has no defensible valley                                   | Increase usable coverage or provide more suitable reads; do not impose a fixed solid-k-mer cutoff                   |
| Completeness is zero or absent                                 | No compatible marker/reference input was supplied, or the supplied markers do not match the organism | Supply an appropriate reference and lineage marker set; report unavailable checks explicitly                        |
| Contamination appears clean although no reference was supplied | A not-run screen was mistaken for zero flagged contigs                                               | Read the screen status and effective method; `not_run` means unevaluated, not contaminant-free                    |
| Polishing requested but workflow stops                         | The production Racon route is external and not wired in the current runtime                          | Execute the recorded command in an approved environment, then rerun from the frozen draft                           |

## Suggested Next Steps

- Resolve any upstream identity or assembler-read-type mismatch before assembly.
- Inspect graph topology and read coverage around unusually long, circular, or low-support contigs.
- Repeat consensus polishing with the declared production route, then recompute QV from the same
  provenance-bound inputs.
- Use lineage-appropriate BUSCO/Compleasm markers and a suitable reference when completeness or
  genome fraction is needed for a release decision.
- Confirm contaminant flags with a broader taxonomic database or reference alignment before
  removing contigs from the all-contig assembly.
- For metagenomes, move to a dedicated binning and CheckM2/MIMAG workflow after assembly QC.

## Related Skills

- `long-read-qc-basecalling` — validate reads, yield, quality, and model/chemistry identity before
  assembly.
- `long-read-structural-variants` — call structural variants from aligned long reads rather than
  assemble them de novo.
- `long-read-transcript-isoforms` — quantify and compare long-read transcript isoforms.
- `long-read-methylation` — analyze modified bases from compatible long-read evidence.
