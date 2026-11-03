# Evidence-gating model

This reference defines the deterministic evidence-gating cascade behind every ranked
hypothesis produced by `scripts/prioritize_cases.py`. It is research decision support
only: the skill assigns each *established* disease-gene hypothesis an explicit **evidence
state** with reason codes, provenance, and an optional reanalysis diff. It never emits a
diagnosis, a diagnostic probability, or an ACMG/AMP variant classification.

The engine reads one case bundle (a GA4GH Phenopacket, a `candidates.tsv` allele table,
and an `evidence_manifest.tsv`), validates it fail-closed, groups candidate rows by
`hypothesis_id`, evaluates each hypothesis through the cascade below, ranks the
hypotheses, and writes `hypotheses.tsv`, `evidence_dossier.json`, `reanalysis_diff.json`,
and `figures/evidence_completeness.png`.

## Scope and hard boundaries

- **In scope:** pre-annotated nuclear germline **SNV/indel** alleles for an **already
  established** disease-gene relationship, evaluated to help a human expert decide where
  to focus follow-up.
- **Out of scope (abstain or reject, never force through SNV/indel logic):** novel
  gene-disease discovery from a single case or a single publication; variant classes
  other than `snv`/`indel`; any hypothesis whose cited evidence cannot be resolved.
- **Never emitted:** diagnosis, diagnostic probability, ACMG/AMP classification, or any
  patient-facing result. Report/data consistency is asserted only at presentation time by
  the canonical report-QC validator, not by this analysis step (the dossier records
  `checks.report_data_consistency = "pending_presentation"`).

## Entry mode

`--entry-mode` (`phenotype_first`, `genotype_first`, `incomplete_evidence`; default
`incomplete_evidence`) is a **label-only** context recorded on every hypothesis and in the
TSV `entry_mode` column. It never changes gate precedence, evidence states, ranking, or
outputs. It exists so a caller can annotate how the case entered review.

## Input validation (fail-closed)

Before any hypothesis is evaluated or any output is written, the case bundle is validated
fail-closed; a rejected input produces a single named error on stderr, exit status 2, and
**no** output artifacts:

- **Required files** `phenopacket.json`, `candidates.tsv`, and `evidence_manifest.tsv` must
  exist and be readable.
- **Phenopacket v2 contract:** `metaData.phenopacketSchemaVersion` must exist, be a
  non-empty string, and have major version `2`. A missing, empty, non-string, or non-v2
  value is rejected (the error names `phenopacketSchemaVersion`).
- **Controlled vocabularies, `case_id` agreement, and transcript presence** on
  consequence-bearing rows are enforced on `candidates.tsv`.
- **Evidence manifest integrity:** every row needs a non-empty `evidence_id`, and a
  **duplicate `evidence_id` is rejected** rather than silently overwritten.
- **Prior dossier match:** a supplied `prior-evidence-dossier.json` must match this case by
  schema and `case_id`.

## Evidence states

Each hypothesis is assigned exactly one state:

| State | Meaning |
|-------|---------|
| `concordant_hypothesis` | Every hard gate, contradiction check, and concordance blocker is satisfied. Not a clinical result. |
| `incomplete_hypothesis` | No hard stop and no contradiction, but at least one concordance blocker (missing-evidence code) is present. |
| `conflicting_hypothesis` | At least one resolvable material contradiction is present (and no higher-precedence hard stop). |
| `abstained` | A hard-stop gate fired; the hypothesis is set aside rather than forced through SNV/indel logic. |

Ranking is deterministic: by state (`concordant` < `incomplete` < `conflicting` <
`abstained`), then by descending independent-evidence-line count, then by `hypothesis_id`.

## Gate precedence (highest first)

Evaluation is a strict cascade. The first gate that fires determines the abstention; only
if no hard stop fires are contradictions considered, then concordance blockers, else the
hypothesis is concordant.

### 1. Hard-stop abstentions (`abstention_code`)

Evaluated in this exact order; the first match wins:

1. `BIOLOGICAL_RELATEDNESS_INCONSISTENT` — a supplied `family.tsv` declares
   `relatedness_status = inconsistent`. This is case-level and forces abstention for
   **every** hypothesis in the case.
2. `EVIDENCE_UNRESOLVABLE` — at least one **cited** evidence identifier is absent from the
   manifest or has `verification_status = unresolvable`. Cited identifiers are the union of
   `supporting_evidence_refs`, `contradicting_evidence_refs`, `classification_source`, and
   `gene_disease_source`. The unresolved identifiers are retained per hypothesis and at the
   top level (see *Evidence resolution*).
3. `UNSUPPORTED_VARIANT_CLASS` — any allele's `variant_class` is outside `{snv, indel}`.
4. `BUILD_OR_TRANSCRIPT_MISMATCH` — a resolvable contradicting reference of source type
   `build_contract` applies, **or** the hypothesis mixes more than one `genome_assembly`,
   **or** it mixes more than one `transcript_id`.
5. `IMPOSSIBLE_INHERITANCE` — two or more alleles are all asserted `phase = trans` while
   sharing a single non-`de_novo`, non-`unknown` parental origin (a demonstrably impossible
   segregation).
6. `ALLELES_IN_CIS` — a non-AD hypothesis is biallelic (>= 2 alleles, or a homozygous call) and
   at least one allele is `phase = cis`, so the alleles cannot jointly satisfy a biallelic
   model.
7. `CALL_QUALITY_INADEQUATE` — any allele has `call_quality_status` in `{fail, unknown}`.

### 2. Material contradictions (`contradiction_codes`)

If no hard stop fires, the hypothesis is `conflicting_hypothesis` when any of the following
hold (each contributes a `MATERIAL_CONTRADICTION`; `contradiction_count` records how many
distinct signals fired):

- a resolvable, non-`build_contract` `contradicting_evidence_refs` entry;
- `phenotype_match = contradictory`;
- `gene_disease_validity` in `{disputed, refuted}`;
- `population_frequency_status = too_common`;
- input `classification` in `{LB, B}`.

### 3. Concordance blockers (`missing_evidence_codes`)

If neither a hard stop nor a contradiction fires, the hypothesis is
`incomplete_hypothesis` when any blocker applies (all applicable codes are recorded):

| Code | Trigger |
|------|---------|
| `SECOND_ALLELE_OR_COVERAGE_REQUIRED` | `AR` model but not biallelic (one P/LP allele, no demonstrated second allele/coverage). |
| `PHASE_REQUIRED` | `AR` with at least two allele rows, no homozygous call, and phases not all `trans`; hard-stop and contradiction precedence still applies. |
| `INHERITANCE_CONTEXT_REQUIRED` | Inheritance model `UNKNOWN`, unknown zygosity under any inheritance model, or `X_LINKED` heterozygous with unknown phase. |
| `GENE_DISEASE_VALIDITY_INSUFFICIENT` | Validity `limited`/`unknown`, or a strong-tier validity (`definitive`/`strong`/`moderate`) with an empty `gene_disease_source` (an absent/unresolvable cited source is instead the `EVIDENCE_UNRESOLVABLE` hard stop). |
| `GENE_DISEASE_SOURCE_NOT_CURATED` | A strong-tier validity's resolved, **verified** `gene_disease_source` is not a curated gene-disease assertion/curation type (e.g. a lone `publication`). One publication alone cannot establish a disease-gene association; publication quality is never inferred. See *Cited-source verification and curated gene-disease source types*. |
| `POPULATION_CONTEXT_INSUFFICIENT` | `population_frequency_status = insufficient_context`. |
| `SUPPORTING_EVIDENCE_UNVERIFIED` | A `supporting_evidence_refs` reference resolves but is `verification_status = unverified`. |
| `CITED_SOURCE_UNVERIFIED` | A cited **source** role (`classification_source` or `gene_disease_source`) resolves but is `verification_status = unverified`. No unverified cited evidence in any role may support concordance; affected ids are preserved in `unverified_evidence_ids`. |
| `VARIANT_CLASSIFICATION_UNRESOLVED` | Input `classification` in `{VUS, UNCLASSIFIED}`. |
| `PHENOTYPE_CORRELATION_INSUFFICIENT` | `phenotype_match` `nonspecific`/`unknown` and never `specific`/`supportive`. |

### 4. Concordant

If none of the above fire, the hypothesis is `concordant_hypothesis`.

## Cited-source verification and curated gene-disease source types

Concordance requires that **every cited evidence identifier is resolved and verified**,
across all cited roles — `supporting_evidence_refs`, `contradicting_evidence_refs`,
`classification_source`, and `gene_disease_source`:

- A cited identifier that is **absent** from the manifest or `verification_status =
  unresolvable` is the `EVIDENCE_UNRESOLVABLE` hard stop (abstention).
- A cited identifier that resolves but is `verification_status = unverified` is a
  concordance blocker, never a silent pass: `SUPPORTING_EVIDENCE_UNVERIFIED` for a
  supporting reference and `CITED_SOURCE_UNVERIFIED` for a `classification_source`/
  `gene_disease_source`. The union of unverified cited ids is preserved per hypothesis in
  `unverified_evidence_ids` (and unioned at the top level).

A **strong-tier** `gene_disease_validity` (`definitive`/`strong`/`moderate`) may support
concordance **only** when its resolved, verified `gene_disease_source` has a **curated
gene-disease** source type. This encodes the rule that *one publication alone does
not establish a disease-gene association*: a verified source whose type is merely a
publication is treated as insufficient validity (`GENE_DISEASE_SOURCE_NOT_CURATED`, with the
offending ids preserved in `noncurated_gene_disease_source_ids`). Publication quality is
never inferred from the record.

Accepted curated gene-disease `source_type` values (the allowlist enforced by the engine):

| `source_type` | Accepted as a curated gene-disease source? |
|---------------|--------------------------------------------|
| `gene_disease_validity` | yes (e.g. a ClinGen-style gene-disease validity curation) |
| `gene_disease_curation` | yes |
| `gene_disease_assertion` | yes |
| `curated_gene_disease_assertion` | yes |
| `clingen_gene_validity` | yes |
| `expert_panel_curation` | yes |
| `publication` | **no** — a lone publication cannot establish the association |
| any other type | **no** |

## Missing vs. absent phenotype

Phenotype state follows GA4GH Phenopacket semantics strictly: a term listed with
`excluded: true` is **absent**; a term that is simply **not listed is unknown, never
absent**. The dossier reports `phenotype.present`, `phenotype.absent`, and their counts,
with an explicit note to that effect.

## Independent evidence lines and correlated predictors

`independent_evidence_line_count` counts distinct **resolved** supporting-evidence lines,
collapsing correlated in-silico predictors so that a family of correlated tools counts
once:

- A supporting reference whose manifest `source_type` is a predictor type
  (`in_silico`, `in_silico_predictor`, `computational_predictor`, `predictor`) collapses by
  its row's `predictor_group` label (or the reference id when no group is given).
- Every other supporting reference collapses by its distinct `source_type`.

For auditability the dossier **also** retains the sorted set of supplied non-empty
`predictor_group` labels for each hypothesis (`predictor_groups`) alongside the collapsed
count, so a reviewer can see *which* predictor group was collapsed rather than only the
resulting number. Absent/unresolvable references never contribute a line.

## Evidence resolution

`supporting_evidence_ids` and `contradicting_evidence_ids` in each hypothesis record list
**only** identifiers that resolve (`verified` or `unverified`). Every cited identifier that
is absent or `unresolvable` is retained separately in `unresolved_evidence_ids` (per
hypothesis and unioned at the top level). When any cited evidence is unresolved,
`checks.evidence_resolution = "hard_stop_detected"` (never `pass`) and
`checks.unresolved_evidence_id_count` records the size of the union; otherwise
`checks.evidence_resolution = "pass"`. Per-hypothesis, `gates.evidence_resolution` is
`"hard_stop_detected"` when that hypothesis has unresolved citations, else `"all_resolved"`.

Two further machine-readable sets are preserved for downstream review:
`unverified_evidence_ids` (cited ids that resolve but are `unverified`, in any role) and
`noncurated_gene_disease_source_ids` (verified `gene_disease_source` ids that back a
strong-tier validity but are not a curated type). Both are recorded per hypothesis and
unioned at the top level, with `checks.unverified_evidence_id_count` and
`checks.noncurated_gene_disease_source_id_count`.

## Provenance and reanalysis

- **`source_version_set_id`** identifies the supplied **evidence source/version set only**.
  It is the truncated SHA-256 of the sorted `(evidence_id, version_or_date)` pairs from
  `evidence_manifest.tsv`. It is never derived from analysis-input file bytes and never from
  a supplied prior dossier, so merely re-supplying a prior dossier cannot manufacture a
  false source-version change.
- **Per-file input hashes** are recorded separately in `checks.input_artifacts`
  (`sha256` and `bytes` for each analysis-input file).
- **Reanalysis** (`reanalysis_diff.json`) is emitted only when a
  `prior-evidence-dossier.json` matching this case (schema and `case_id`) is supplied. It
  reports two independent comparisons: (a) prior vs. current `source_version_set_id`
  (`source_version_changed`), and (b) prior vs. current analysis-input hashes
  (`analysis_inputs_changed`). The prior dossier itself is excluded from both sides of the
  input-hash comparison so that supplying it cannot register as an input change. Any
  per-hypothesis state/rank/contradiction/missing-evidence/abstention change, or any
  added/removed hypothesis, sets `review_recommended = true`. With no prior dossier the diff
  is `{"applicable": false, "review_recommended": false, ...}` and each hypothesis is
  `not_applicable`.

## Outputs

| File | Kind | Contents |
|------|------|----------|
| `hypotheses.tsv` | tab-delimited table | one ranked hypothesis per row with state, reason codes, provenance, and reanalysis status |
| `evidence_dossier.json` | JSON | full per-hypothesis records, phenotype summary, counts, checks, provenance, limitations |
| `reanalysis_diff.json` | JSON | reanalysis comparison against a supplied prior dossier (or an inapplicable stub) |
| `figures/evidence_completeness.png` | PNG | per-hypothesis evidence-completeness and hard-gate matrix |

All outputs are written only after successful validation and computation; a rejected input
produces a named error on stderr and no outputs (fail-closed).
