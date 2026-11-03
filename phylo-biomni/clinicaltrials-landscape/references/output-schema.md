# Output Schema Reference

All paths are relative to the results root. Each run writes one machine-readable dataset per concern,
the coverage fingerprint, the facts payload, and the figure manifest.

## `coverage_scope.json` — coverage provenance

Machine-readable provenance binding the report to the exact exported dataset:

| Field | Type | Description |
|-------|------|-------------|
| `schema` | str | `clinicaltrials-landscape-coverage/v1` |
| `source` | str | `ClinicalTrials.gov API v2` |
| `query_date` | str | Date of the API retrieval |
| `conditions`, `statuses` | list | Exact required condition terms and status filter |
| `intervention_filter`, `phases`, `sponsor_filter` | str/list/null | Optional filters exactly as submitted |
| `input_resolution` | str | `declared_query` or `versioned_profile` |
| `query_profile` | object/null | Profile id, version, review date, alias count, and canonical profile SHA-256 when a versioned profile resolved the prompt |
| `pages_retrieved` | int | API pages successfully retrieved |
| `records_retrieved` | int | Records returned after exhausted pagination |
| `api_total_count` | int | API `totalCount` for the declared query |
| `pagination_exhausted` | bool | Must be `true` before export |
| `retrieval_state` | str | Must be `complete_for_declared_query` before export |
| `dataset_file`, `dataset_rows`, `dataset_sha256` | str/int/str | Exact exported CSV name, row count, SHA-256 |
| `boundary_statement`, `limitation_statement`, `record_asset_boundary_statement` | str | Verbatim coverage and record-versus-asset boundary statements the report must carry |

This artifact supports only a **bounded** claim about the declared registry query. It does not
establish a complete target-development or all-program inventory.

## `trials_all.csv` — one row per retrieved registered record

Identity/sponsor/phase/geography columns plus the classification and denominator columns:

| Column | Type | Description |
|--------|------|-------------|
| `nct_id`, `brief_title`, `official_title` | str | Trial identity |
| `lead_sponsor`, `sponsor_normalized`, `sponsor_class`, `is_industry` | str/bool | Sponsor identity and class; missing or blank sponsor names normalize to `Unknown`. An offline input that carries neither `is_industry` nor `sponsor_class` is rejected (fail closed) because the industry/academic split cannot be derived |
| `study_type` | str | `INTERVENTIONAL`, `OBSERVATIONAL`, or `EXPANDED_ACCESS` when availability statuses are requested |
| `study_purpose` | str | Observational / Diagnostic-Imaging / Therapeutic / Other-Supportive / Unresolved |
| `intervention_category` | str | One of the nine generic modality buckets (`classification.md`) |
| `classification_resolved` | bool | True when both purpose and modality resolved |
| `mechanism` | str | Equals `intervention_category` unless an optional disease config supplies a label; always exported, also for a label-complete offline CSV |
| `drug_names_str`, `intervention_names_str`, `intervention_descriptions_str` | str | Intervention names / descriptions (descriptions raw-API only) |
| `brief_summary`, `detailed_description` | str | Present on raw API records; often blank in an offline CSV |
| `phase_normalized` | str | Display phase (e.g., `Phase 2`, `Phase 2/3`, `Not Applicable`); a label outside the canonical set counts as Not Applicable in both the category-by-phase table and the phase figure, so figure totals equal `records_retrieved` |
| `phase_numeric` | float | Numeric phase for sorting (0 for Not Applicable) |
| `phase_applicable` | bool | True iff phase ∈ Phase 1..4 (incl. combined) — the denominator flag |
| `is_therapeutic` | bool | `study_purpose == Therapeutic` |
| `is_pharmacological` | bool | Single consistent definition: `INTERVENTIONAL` **and** `Therapeutic` |
| `overall_status` | str | Registry status |
| `conditions_str` | str | Semicolon-separated conditions |
| `enrollment_type` | str | Registry ACTUAL/ESTIMATED type when supplied; missing or unrecognized values remain unknown in summaries |
| `enrollment`, `enrollment_clean`, `enrollment_outlier` | num/num/bool | Raw enrollment; capped (>50,000 → NaN) for aggregate views; outlier flag |
| `countries_str`, `n_countries`, `regions_str` | str/int/str | Geography |
| `study_design_category` | str | Derived design (RCT Double-Blind, Single-Arm, Observational, …) |
| `is_fda_regulated_drug` | bool/null | Regulatory signal when present |

**Field availability.** Raw API records also carry intervention descriptions, brief summaries, and detailed descriptions; an
offline compiled CSV frequently does not. Columns unavailable in the source are left blank and are
never implied present. `is_pharmacological` uses one definition everywhere (fixing the prior
package's two contradictory definitions that reported "0 pharmacological mechanism classes").

## `trials_by_category.csv` — one row per intervention_category

`intervention_category` × phase counts with **explicit denominators**:

- one column per phase present (`Phase 1` … `Phase 4`),
- `Not Applicable`,
- `Phase-applicable subtotal` (sum of the phase columns),
- `All retrieved` (the true grand total per category).

There is deliberately **no bare `Total` column**: a phase-only subtotal must never be presented as the
all-records total (the prior package labeled the phase-only subtotal "Total" and dropped Not
Applicable records).

## `trials_by_sponsor.csv` — one row per lead sponsor

| Column | Type | Description |
|--------|------|-------------|
| `sponsor_normalized` | str | Index: normalized sponsor name (full, not truncated) |
| `trials` | int | Trial count |
| `intervention_categories` | int | Distinct generic categories for the sponsor |
| `industry` | bool | Industry-sponsored (from registry lead-sponsor class) |

## `trials_match_evidence.csv` — one row per registered record (query-match provenance)

| Column | Description |
|--------|-------------|
| `nct_id`, `brief_title`, `official_title`, `conditions_str` | Identity and conditions |
| `intervention_names`, `intervention_descriptions`, `brief_summary`, `detailed_description` | Match text (descriptions blank when the source lacked them) |
| `study_purpose`, `intervention_category` | Assigned labels |
| `intervention_term` | The declared intervention filter expression (if any) |
| `matched_intervention_terms` | Which positive leaf terms/aliases from the declared expression were visible in exported fields |
| `matched_query_fields` | Which logical fields contained at least one declared positive term/alias |
| `matched_condition_terms` | Which declared condition terms matched |
| `match_basis` | `intervention_term_matched_in_exported_fields` \| `condition_matched_only` \| `registry_index_only` |
| `intervention_descriptions_available`, `brief_summary_available`, `detailed_description_available` | **Field-availability flags** (True only when the field was actually present) |
| `match_evidence_source` | `raw_api` (rich fields present) \| `compiled_csv` (offline; descriptions/summary absent) |

**Field availability is recorded, not assumed.** Absent source fields are left blank **and** flagged
`False`; they are never implied present.

**Recall limitation.** Each positive leaf term in a Boolean intervention expression is inspected
independently across the exported fields with one alias-aware rule (`scripts/alias_match.py`):
matching is case-insensitive; a short letters-only alias (at most 4 letters, e.g. `PSMA`, `CAR`)
must be bounded by the text edge, a non-letter, or a letter-case transition, so `PSMAxCD3`,
`177Lu-PSMA-617` and lowercase prose match while `CAR` inside `Carcinoma` does not; longer aliases
keep plain substring semantics. A multi-word alias is matched as a whole, and each edge token that
is itself a short letters-only alias must be bounded on its outer side (`PSMA CAR` matches
`PSMA CAR-T cells` but not `PSMA carcinoma`). Accepted recall cost: an all-caps run glued to a short
alias is indistinguishable from an ordinary word, so `PSMA` is not visible inside `PSMAI&T` or
`PSMAPET`; such records stay matched through their other exported fields or remain
`registry_index_only`. `match_basis` separates visible alias matches from records
included via a condition match or the registry's own query indexing. Registry query semantics can
still retrieve a record without exposing the indexed match in these fields, so use this table to
*review* evidence, not to claim completeness.

## `report_facts.json` — evidence-bearing claims

Promoted from `facts_payload.json` by `landscape_evidence.write_report_facts`, which attaches the
validated figure manifest and enforces the partition accounting declared in `assets/fact_definitions.json`.
Contains headline counts; the four partitions (`study_type`, `phase_applicability`, `study_purpose`,
`intervention_category`), **each summing to `records_retrieved`**; pre-formatted `headline_sentences`
the report quotes verbatim (including the coverage, limitation, and record-versus-asset boundary statements); `caveats_fired` (each
bound to a concrete number); and the `figures` array.

Presentation-critical match-evidence values are exposed both inside the structured `match_evidence`
object and as top-level aliases (`descriptions_available_count`, `brief_summary_available_count`,
`detailed_description_available_count`, plus the three match-basis counts).
`match_evidence_availability_sentence` is the canonical report-ready sentence. Report builders must
quote that sentence instead of manually traversing nested keys; a missing, `None`, `null`, or `NaN`
value is a hard report-build failure.

`sponsor_composition_sentence` is the canonical sponsor-class sentence. It keeps unique-sponsor count
(`n_sponsors`) distinct from record counts by lead-sponsor class (`n_industry`, `n_academic`). Both
counts are integers by contract (`assets/fact_definitions.json`); an input without a lead-sponsor class
is rejected by `compile_trials` and again by `build_facts_payload` rather than reported as
"0 records have an industry lead sponsor". The
post-render `validate_report_output()` gate checks these distinctions against extracted PDF text and
also rejects record-to-asset/pipeline inference, programmatic-only visual-pass attestations, and
effectively blank/orphaned pages.

## `figures/manifest.json`

One entry per analysis step: `{step, file, caption}` when a figure was produced, or
`{step, file: null, reason}` when a figure is inapplicable for this run (e.g., no phase-applicable
records, or too few enrollment values). Figures render as PNG and editable-text SVG.

## `report_spec.json`

Derived from the exact bytes of `report_facts.json`. Schema v2 records the facts SHA-256, canonical top-level
section order, required verbatim fact sentences, Figure 1 infographic placement, analytical figure
numbering, protected headings, keep-together rules, and the minimum normalized text per page. The
post-render validator rejects a missing or stale spec before evaluating the PDF text.

Enrollment plots use registry-reported counts, which may mix actual and estimated values. Captions derive type counts from the plotted subset and retain fractional medians. Never describe mixed or unknown-type counts as exclusively planned enrollment.
