---
name: open-targets
description: Rank disease-associated targets by overall association score from the
  Open Targets Platform API Retrieve bounded evidence rows with discriminated datasource
  and datatype semantics Paginate API results within configurable budgets with completeness
  labels Generate a provider-styled canonical PDF report with deterministic figures
  and a GenerateImage infographic Use when prioritizing therapeutic targets for a
  disease using the Open Targets Platform GraphQL API.
category: drug_discovery
visibility: public
starting-prompt: Which top 10 therapeutic targets are most strongly associated with
  Alzheimer disease (MONDO_0004975), and what does GWAS Catalog study GCST005194 with
  credible sets add to the evidence?
---
<!-- archetype: analysis-workflow -->

<!-- contract: evidence-v1 -->

## When to Use This Skill

Use this skill to prioritize therapeutic targets for a disease by querying the Open Targets Platform GraphQL API for target-disease associations, evidence rows, GWAS study metadata, and credible sets. It is suited to early target identification when you have an EFO disease identifier and want an integrated, datasource-aware ranking backed by public evidence.

Do not use this skill for causal inference, clinical recommendation, participant-level analysis, or raw GWAS summary-statistics retrieval. It retrieves public integrated association evidence only.

## Why X, not Y (READ FIRST)

- **API queries, not bulk download.** The Open Targets Platform GraphQL API returns bounded, structured records with release metadata. Bulk data dumps (e.g. FTP parquet) are a different access mode with different provenance; this skill stays in the API zone and labels any over-budget need explicitly rather than silently switching modes.
- **Bounded samples, not exhaustive sweeps.** Every paginated call is capped by configurable budgets (`max_entities`, `max_pages`, `max_rows`, `max_bytes`, `max_elapsed`). A retrieved target set is labelled `complete` or `bounded_sample`; it is never labelled exhaustive unless the API confirms it.
- **Discriminated semantics, not flat rows.** Each evidence record carries a `record_type`, `datasource_id`, `datatype`, `metric`, `scale`, and an interpretation boundary. Association scores are integrated prioritization metrics on a 0-1 scale, not effect estimates, replication, or causal proof.
- **Immutable operation ledger, not ad-hoc calls.** Every API operation is recorded in an append-only ledger with request hash, response hash, counts, and budget status. Bare HTTP POST calls bypass the ledger and are rejected by the shipped client helper.

## Inputs

- An EFO disease ID (e.g. `MONDO_0004975` for Alzheimer disease).
- Optionally a GWAS Catalog study ID (e.g. `GCST005194`).
- Optionally the number of top targets to retrieve (default `10`).

## Outputs

- `target_ranking.csv` — bounded top-N targets with overall and per-datatype association scores.
- `evidence_rows.csv` — bounded evidence rows for the top target, with discriminated datasource and datatype semantics.
- `operation_ledger.json` — immutable record of every API attempt with query, canonical request-body,
  and exact response-body hashes; byte counts, terminal state, and explicit retry/recovery links.
- `provenance.json` — API release label, version, access date, declared source mode, operation-ledger
  hash, and infographic prompt hash.
- `report_facts.json` — gated facts artifact feeding the report and source-witness checks.
- `report_content.json` — presentation-independent content shared by every compatible report-style provider.
- `report_structure.json` — provider-independent section and figure-placement contract.
- `root_bundle.json` — authoritative source mode, input identity, operation lineage, and member hashes
  for the complete root deliverable set.
- `report_open-targets.pdf` — the single canonical, provider-styled PDF report with task context, methods, results, deterministic figures, a GenerateImage infographic, references, and next steps.
- Use the report-style provider selected for this run to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references (cited inline with [N] markers), and next steps from all of the analyses. If no provider was explicitly selected, use the default `pdf-report-generation` provider. Never infer a provider from customer, tenant, project, or account context. If an explicitly selected provider is unavailable or incompatible, stop and report a missing-provider error rather than falling back. Regenerate any figure whenever its underlying data changes.

## Figures

| Step                  | File                                      | Description                                                                                                                      |
| --------------------- | ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Target ranking        | `figures/figure_1_target_ranking.png`   | Top targets ranked by overall association score (deterministic bar chart from `report_facts.json`).                            |
| Datatype contribution | `figures/figure_2_datatype_heatmap.png` | Per-datatype contribution scores for top targets (deterministic heatmap from `report_facts.json`).                             |
| Workflow infographic  | `infographic_open_targets_workflow.png` | Qualitative, non-data-bearing infographic of the API workflow and API/bulk boundary, generated by the Biomni GenerateImage tool. |

All quantitative figures are deterministic and derived from `report_facts.json`. The infographic is qualitative only and carries no run-specific numbers, scores, rankings, variant IDs, or p-values.

## Clarification Questions

Resolve the complete user prompt with `scripts/prompt_contract.py` before asking anything. If its
status is `ready`, execute immediately with the returned inputs and record its
`selected_branch_ids`; **do not ask for confirmation, alternative inputs, validation depth, or
preferences already fixed by the prompt**. Apply the documented default `top_n=10` and the
evidence-only branch without asking when no GWAS/credible-set work was requested.

Ask one concise question naming only the unresolved field when the resolver returns `clarify`.
That state is reserved for a missing required disease identifier, conflicting disease/top-N/study
values, conflicting scope instructions, or a credible-set request with no GCST study identifier.

**scope** (single-select):

- `evidence_only` — Retrieve evidence for the top target only.
- `with_credible_sets` — Retrieve evidence plus GWAS credible sets and L2G predictions for the given study ID.

Derive `with_credible_sets` when the prompt supplies a GCST identifier or explicitly requests GWAS
or credible-set analysis. Otherwise derive `evidence_only`. Record the resulting
`<question_id>:<choice_id>` value in `selected_branch_ids` for the run receipt.

## Standard Workflow

### Step 0 — Resolve supplied inputs; ask only when required

Pass the full user prompt to `prompt_contract.resolve_prompt`. A `ready` result is authorization to
run the declared workflow with those values immediately. A `clarify` result identifies the exact
missing or ambiguous field; do not expand that into optional preference questions.

### Step 1 — Capture API release metadata

Run the `meta` query to capture `dataVersion` and `apiVersion`, binding them to the operation ledger and provenance.

```python
from open_targets_client import OpenTargetsClient
client = OpenTargetsClient()
meta = client.capture_release()
```

### Step 2 — Resolve disease identity and rank targets

Query `associatedTargets` for the given EFO ID, retrieving a bounded top-N set with datatype scores.

```python
from queries import REGISTRY
result = client.request(
    "meta_and_associated_targets",
    REGISTRY["meta_and_associated_targets"]["query"],
    {"efoId": disease_id, "size": top_n},
)
```

### Step 3 — Retrieve evidence for the top target

Query `evidences` for the top-ranked target with cursor pagination, bounded by configured budgets.

```python
evidence = client.request(
    "evidence", REGISTRY["evidence"]["query"],
    {"efoId": disease_id, "ensemblId": top_target_id, "size": 25, "cursor": None},
)
```

### Step 4 — Inspect GWAS study and credible sets

Query `study` metadata and `credible_sets` with L2G and colocalisation for the given study ID. Before integrating the study into the disease context, `run_analysis.py` checks concordance: the study's returned `traitFromSource` and `diseases` list are compared to the queried EFO ID. If concordance cannot be established, the study is labeled `independent_study_context` and excluded from target-support conclusions.

```python
study = client.request("study", REGISTRY["study"]["query"], {"studyId": study_id})
cred = client.request(
    "credible_sets", REGISTRY["credible_sets"]["query"],
    {"studyIds": [study_id], "size": 10},
)
```

### Step 5 — Serialize records through the discriminated schema

Pass all records through `semantics.py` serializers with `record_type`, `metric`, `scale`, `datasource_id`, and interpretation boundary. L2G and colocalisation records carry a null `datasource_id` with an explicit not-applicable reason.

### Step 6 — Build deterministic figures

Run `visualize.py` to produce the target-ranking bar chart and datatype heatmap from `report_facts.json`.

### Step 7 — Generate the qualitative infographic

Call the Biomni GenerateImage tool with the fixed prompt from `assets/infographic_prompt.txt` to produce `infographic_open_targets_workflow.png`. The prompt is qualitative and non-data-bearing; it is built from fixed package text, not from run results. Read the prompt file immediately before calling GenerateImage; do not edit it afterward. The prompt SHA256 and delivered image SHA256 are derived into `provenance.json` and the run receipt, pairing the tool-use id, exact prompt, requested filename, and successful result id.

### Step 8 — Build presentation-independent report content

After scientific lineage is finalized, run `report_content.py` to derive `report_content.json` and `report_structure.json` from `report_facts.json` and `provenance.json`. These files contain no palette, typography, logo, or customer styling. Every provider must render this same content and the existing deterministic figures; styling must not change, omit, or reinterpret scientific content.

### Step 9 — Final report (MANDATORY TERMINAL STEP)

**The run is not complete until this step has produced `report_open-targets.pdf` at the results root.**

Use the report-style provider selected for this run to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references (cited inline with [N] markers), and next steps from all of the analyses. If no provider was explicitly selected, use the default `pdf-report-generation` provider. Never infer a provider from customer, tenant, project, or account context. If an explicitly selected provider is unavailable or incompatible, stop and report a missing-provider error rather than falling back. Regenerate any figure whenever its underlying data changes.

Resolve the provider only from immutable user-message evidence using `report_render_plan`. The bundled `build_report.py` is the renderer for the default provider only. When an explicit compatible provider is selected, render `report_content.json` through that provider and pass its completed PDF with `--provider-rendered-report`. That path is an artifact input, never provider-selection evidence.

For an explicit provider, run `run_analysis.py --content-only` first. This completes the unchanged scientific analysis and leaves the style-free content without publishing a PDF. After the selected provider renders that content, run `finalize_report.py --provider-rendered-report <workspace-pdf>` with genuine visual-review evidence. This second phase does not repeat API queries or scientific calculations. The default provider may complete both phases in the ordinary `run_analysis.py` invocation.

Publish exactly one file as `report_open-targets.pdf`. Do not preserve a Phylo canonical report and create an enterprise-styled companion. Do not overwrite the canonical PDF after finalization; render and inspect the selected provider's completed file first, stage it once, and then call `finalize_report_artifacts`.

Render the PDF to a fresh workspace file named by `workspace_report_file`; do not open or truncate an existing PDF on the object-backed results mount. The `staged_copy` call below publishes the completed file under its declared results-root name.

Then verify the run and write its receipt. `write_receipt` runs every gate — the report exists at the results root and is big enough, each declared figure is present and non-blank, the infographic lineage resolves to a fail-closed three-state outcome (`verified` when there is a unique same-id `GenerateImage` call/result pair with the exact prompt/filename and a delivered-image hash; `not_evaluable_platform_trace` when a genuine event is present but truncated/id-less/agent-writable/hashless; `failed` for contradictory or fabricated evidence), its decoded pixels match an embedded image on page 1, and the finished PDF carries the selected provider's required markers — then records what each one returned. A `not_evaluable_platform_trace` lineage is release-blocking unless `report_facts.json`, `provenance.json`, and the report all disclose that provenance is not independently verified. Run bundled commands through `run_bundled`, which writes the QC-owned `qc_run_log.json` from the subprocess result and output hashes. Do not author execution events or copy transcript identifiers. Record PDF visual review with an explicit state: only `pass` may complete the mandatory gate; `not_evaluable` remains false and blocks a successful receipt. Produce every source-witness artifact declared in `skill_contract.json`. Receipt v3 rejects unmatched hashes, unavailable provider evidence, partial page coverage, or source-value disagreement. It raises if any mandatory gate is not `pass`, **after** writing the receipt, so a failed run leaves the diagnostic behind.

```python
from report_content import write_report_content
from report_qc import (finalize_report_artifacts, finalize_root_bundle,
                       record_pdf_review, report_render_plan, run_bundled,
                       staged_copy, write_receipt)

# Build figures and scientific lineage before presentation.
run_bundled(
    ["python3", "scripts/visualize.py", "--facts", "report_facts.json", "--outdir", str(outdir)],
    "scripts/visualize.py",
    ["figures/figure_1_target_ranking.png", "figures/figure_2_datatype_heatmap.png"],
)
scientific_artifacts = [
    "target_ranking.csv", "evidence_rows.csv", "operation_ledger.json",
    "provenance.json", "report_facts.json", "figure_payloads.json",
    "figures/figure_1_target_ranking.png", "figures/figure_2_datatype_heatmap.png",
    "infographic_open_targets_workflow.png",
]
finalize_root_bundle(scientific_artifacts)
report_content = write_report_content(outdir)
render_plan = report_render_plan()
bundled_files = ["scripts/visualize.py"]
if render_plan["render_with_bundled"]:
    run_bundled(
        ["python3", "scripts/build_report.py", "--content", str(report_content),
         "--output", str(workspace_report_file)],
        "scripts/build_report.py",
        [str(workspace_report_file)],
    )
    bundled_files.append("scripts/build_report.py")
else:
    workspace_report_file = provider_rendered_report
staged_copy(workspace_report_file, "report_open-targets.pdf")
finalize_report_artifacts("report_open-targets.pdf")
root_artifacts = [
    *scientific_artifacts, "report_content.json", "report_structure.json",
    "report_open-targets.pdf",
]

# OT-R2-05: bind every review event to the current final-PDF SHA256 and artifact SHA256s
record_pdf_review(
    report_name="report_open-targets.pdf",
    text_artifact=extracted_text_file,
    rendered_page_files=rendered_page_files,
    reviewed_page_numbers=reviewed_page_numbers,
    review_attestation=visual_review_notes,
    review_state="pass",  # only after inspecting every rendered page
    pdf_sha256=current_pdf_sha256,
    artifact_sha256s=artifact_sha256s,
)
# OT-R2-02: pass infographic_prompt so the receipt pairs prompt+image hashes with the tool-use
write_receipt(
    report_name="report_open-targets.pdf",
    figures=figures,
    bundled_files=tuple(bundled_files),
    outputs=("figures/figure_1_target_ranking.png", "figures/figure_2_datatype_heatmap.png"),
    infographics=("infographic_open_targets_workflow.png",),
    infographic_prompt="assets/infographic_prompt.txt",
    qc_run_log="qc_run_log.json",
    narrative_facts="report_facts.json",
    narrative_provenance="provenance.json",
    root_artifacts=root_artifacts,
)
```

The v3 receipt is `run_receipt.json` at the **results root**, not beside this SKILL.md. It records
`report_content_complete` and `root_lineage_verified` in addition to the execution, output, figure,
provider-style, rendering, visual-review, source, and infographic gates. **Do not write this file by hand.**
`check_skill.py --require-run-receipt` requires the schema marker and per-outcome evidence, so a
hand-written block of `true`s fails.

## Scientific caveats

- Association scores are integrated prioritization metrics on a 0-1 scale, not effect estimates, replication, causal proof, or clinical validation. The `overall_association_score` field in `report_facts.json` records the value; the `INTERPRETATION_BOUNDARIES` in `semantics.py` record the boundary.
- The target sample is bounded, not exhaustive. The `completeness` field in `report_facts.json` is `complete` or `bounded_sample`; it is never labelled exhaustive unless the API confirms it. The `max_entities` budget in `open_targets_client.py` is `50`.
- L2G and colocalisation records carry a null `datasource_id` with an explicit not-applicable reason. The `FORBIDDEN_DATASOURCE_IDS` set in `semantics.py` is `{"ot_genetics_l2g", "ot_genetics_colocalisation"}`.
- GWAS Catalog study IDs are study-specific. `GCST005194` is coronary artery disease, not Alzheimer disease; do not conflate study disease with the queried EFO disease. The `studies` field in `report_facts.json` records the study trait. The `study_concordance` field records whether the study's returned trait/ontology matches the queried disease; non-concordant studies are labeled `independent_study_context` and excluded from target-support conclusions.
- The report derives executed-operation counts from `operation_ledger.json`. Five operations are executed in the representative run (meta, meta_and_associated_targets, evidence, study, credible_sets); nine operation families are registered and separately validated by live schema smoke. The report states these counts separately; no hard-coded executed count remains.
- Machine-readable JSON artifacts (`figure_payloads.json`) are serialized with `allow_nan=False`; non-finite floats are converted to `null` and remain visually distinct from numeric zero in the heatmap.
- PDF review evidence (text extraction, page renders, visual review) is hash-bound to the current final-PDF SHA256 and every artifact SHA256. A PDF overwrite, text substitution, or stale event fails the receipt gate.
- Visual review of rendered PDF pages is `not_evaluable` when inspection is unavailable. The receipt
  records that typed state as a false mandatory outcome; it never converts unavailable evidence to
  success. A separate reviewer must inspect every rendered page and record `review_state="pass"`.
- Infographic provenance is a fail-closed three-state outcome (`verified` /
  `not_evaluable_platform_trace` / `failed`) recorded identically in `report_facts.json`
  (`infographic_lineage`), `provenance.json`, the report, and the run receipt. When the platform
  trace truncates the GenerateImage args (or the event is id-less, agent-writable, or hashless) the
  state is `not_evaluable_platform_trace`: it is never reported as verified, and the run may complete
  only when every surface discloses that the image provenance is not independently verified and makes
  no verification claim. A `failed` state (contradictory, paraphrased, mismatched, duplicate, or
  fabricated evidence) is always release-blocking. The exact prompt is never inferred from a
  truncated string.

## Evidence Tier

Contract maturity: `evidence_validated`

This package carries a `skill_contract.json` (`phylo-skill-evidence/1`) with tested capabilities, source assertions with runtime witnesses, clarification branches, external-dependency budgets, and a PDF review specification. The auto validation matrix records `not_run` until the offline eval suite and a representative run have been executed; maturity upgrades to `evidence_validated` once they pass.

## Data Sources & Licenses

- **Open Targets Platform GraphQL API** — `https://api.platform.opentargets.org/api/v4/graphql`. Data under CC0 1.0 Universal (Public Domain Dedication); the API is free with no authentication. See `references/licensing-and-access.md` for access terms and verification.
- **GWAS Catalog** (integrated by Open Targets) — `https://www.ebi.ac.uk/gwas/`. CC0 1.0. Study metadata and credible sets are retrieved through the Open Targets API, not directly from the GWAS Catalog.
- **Ensembl / UniProt / ChEMBL** (integrated by Open Targets) — identifiers resolved through the API. See `DATA_SOURCES.md` for the full source list and licenses.

No participant-level data, raw GWAS summary statistics, or controlled-access data are retrieved.

## Common Issues

- **HTTP 200 with a body-level error.** The client rejects responses where `data` is null or `errors` is present, even with HTTP 200. See `failure_body_error.json`.
- **Empty semantic payload.** The client rejects responses that parse but carry no semantic content. See `failure_empty_semantic.json`.
- **Pagination overrun.** If a query exceeds `max_pages` or `max_rows`, the ledger records `bounded_sample` and the run continues; it does not silently loop. See `test_pagination.py`.
- **Missing infographic.** If `infographic_open_targets_workflow.png` is absent, blank, or corrupt, `build_report.py` raises `InfographicError` and the report is not published. The receipt's `infographics_generated_by_tool` gate also verifies prompt/tool/image lineage and content-identity embedding. See `test_infographic.py`.
- **Truncated GenerateImage args in the platform trace.** The platform execution trace can truncate the GenerateImage tool-call arguments (the fixed prompt is ~1124 chars; the trace caps args near 600 chars and appends a literal `[truncated N chars]` marker), so the exact prompt cannot be read. The `infographics_generated_by_tool` gate returns a fail-closed three-state outcome — `verified`, `not_evaluable_platform_trace`, or `failed` — computed by `report_qc.classify_infographic_lineage`. A truncated (or id-less, agent-writable, or hashless) but otherwise genuine event is `not_evaluable_platform_trace`: it never counts as verified, and it is release-blocking unless `report_facts.json`, `provenance.json`, and the report all disclose that provenance is not independently verified. Contradictory, paraphrased, mismatched, duplicate, or fabricated evidence is `failed` (always release-blocking). The truncated args string is never reconstructed into a prompt. See `test_repair_r4.py`.
- **Non-concordant GWAS study.** If the study's returned trait does not match the queried disease, it is labeled `independent_study_context` and excluded from target-support conclusions. See `test_repair_r2.py`.
- **Stale PDF review events.** If the PDF is overwritten after review events are recorded, the hash-binding check in `_pdf_review_evidence` fails the receipt. See `test_repair_r2.py`.

## Suggested Next Steps

- Run `schema_smoke.py` against the live API to verify all nine operations return schema-compatible records.
- Increase `--top-n` within budget to widen the target set, then re-run.
- Cross-reference top targets with druggability or tractability skills for modality assessment.
- Export `target_ranking.csv` into a downstream prioritization workflow.

## Related Skills

- `target-tractability-druggability` — assess druggability and modality tractability of ranked targets.
- `knowledge-graph-target-reasoning` — multi-hop target reasoning over integrated evidence.
- `gwas-to-function-twas` — identify causal genes from GWAS using transcriptome-wide association studies.
- `colocalization-susie` — test whether two association signals share causal variants.
