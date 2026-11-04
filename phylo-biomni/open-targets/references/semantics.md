# Scientific semantics — Open Targets record types

This reference documents the discriminated `record_type` schema that every exported record passes
through (OT-07, OT-08). The schema prevents conflation of score types and forbids invented
datasource identity.

## Discriminated record types

Every serialized record carries a `record_type` discriminator, a `metric_name`, a `metric_value`,
a `scale` string, a `datasource_id` (only when the record is a real evidence datasource record),
a `datasource_not_applicable_reason` (when `datasource_id` is null), and an
`interpretation_boundary` string that constrains the strongest claim the report may make.

| record_type | metric_name | scale | datasource_id | interpretation boundary |
|---|---|---|---|---|
| `association` | `association_score` | 0–1 (higher = stronger integrated association) | null | Prioritization metric, NOT an effect estimate, replication, causal proof, or clinical validation |
| `datatype_score` | `datatype_score` | 0–1 (higher = stronger datatype contribution) | null | Prioritization metric, NOT an effect estimate or causal proof |
| `evidence` | `evidence_score` | datasource-specific (0–1 for most) | real datasource ID from API | NOT an effect estimate, causal proof, or clinical validation |
| `l2g_prediction` | `l2g_score` | 0–1 (higher = more likely causal gene) | null | Prioritizes the causal gene at a locus; NOT an effect estimate, replication, causal proof, or clinical validation |
| `colocalisation` | `colocalisation_h4` / `colocalisation_clpp` | 0–1 posterior probability | null | Supportive evidence that two signals share a causal variant; NOT causal proof or clinical validation |
| `credible_set` | `pvalue` | scientific notation (mantissa × 10^exponent) | null | Fine-mapped genetic-locus annotation, NOT an effect estimate or causal proof |
| `study` | null | null | null | GWAS study metadata; descriptive, NOT an effect estimate or causal proof |

## Datasource identity rules (OT-08)

`datasource_id` is populated **only** from the real `evidences.rows[].datasourceId` field returned
by the API. The following rules are enforced by `scripts/semantics.py`:

1. **Evidence records** keep their real `datasource_id` (e.g. `genetic_literature`, `clinical`,
   `affected_pathway`). An evidence record with a null `datasource_id` fails validation.
2. **L2G, colocalisation, and credible-set records** carry `datasource_id: null` with an explicit
   `datasource_not_applicable_reason`. These are nested fields on `CredibleSet`, not evidence
   datasource records.
3. **Association and datatype-score records** carry `datasource_id: null` with an explicit reason.
   They are integrated metrics, not single-datasource evidence.
4. **Forbidden invented IDs.** The strings `ot_genetics_l2g` and `ot_genetics_colocalisation`
   must never appear in a `datasource_id` field. Injecting either raises `ValueError` at
   serialization time and fails schema validation.

## Interpretation boundaries (OT-07)

No record type may be labeled as a raw effect estimate, replication, causal proof, or clinical
validation. The `interpretation_boundary` string is attached to every record and quoted verbatim
in the report's caveats. The report's prose is derived from `report_facts.json`, which carries
these boundaries, so the report cannot make a stronger claim than the schema allows.

## Validation

`validate_records()` checks every record for:
- Known `record_type` discriminator
- `datasource_id` is null for non-evidence types and non-null for evidence
- `datasource_not_applicable_reason` is present when `datasource_id` is null
- No forbidden invented datasource IDs

Any validation error is recorded in `report_facts.json.validation_errors` and surfaced in the
report's operation-ledger summary.
