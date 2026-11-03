# Study & Intervention Classification

This skill assigns two orthogonal, mutually exclusive labels to every retrieved trial, so each
partition sums exactly to the number of retrieved records:

- `study_purpose` — one of: **Observational, Diagnostic/Imaging, Therapeutic, Other/Supportive, Unresolved**
- `intervention_category` — one of nine generic, record-grounded modality buckets (below)

These are **structural, generic buckets — not a target-development or mechanism-of-action taxonomy.**
No disease-specific mechanism configuration ships with this skill. A record whose modality cannot be
established from its own fields is **labeled** `Unclassified (insufficient description)` (modality) or
`Unresolved` (purpose) — never assigned an invented modality. Implemented in
`scripts/classify_study.py`.

## Intervention categories (9)

1. Diagnostic radiotracer / imaging agent
2. Radioligand therapy (radiopharmaceutical)
3. Small molecule
4. Biologic (antibody/protein)
5. Cell or gene therapy
6. Radiation therapy (external/brachytherapy)
7. Device / Procedure
8. Behavioral / Supportive / Other
9. Unclassified (insufficient description)

## Evidence used

Classification reads only fields present on the record: every intervention `type` / `name` /
`description`, `briefSummary`, `detailedDescription`, titles, and conditions (raw API records), or `study_type`, titles,
`intervention_names_str`/`drug_names_str` and `intervention_descriptions_str` (compiled offline CSV). On compiled rows
the radioligand rules evaluate the intervention names first and then the names jointly with the retained
descriptions, so a generic name with a therapeutic-radioligand description classifies as on the raw-record
path; a description that is itself diagnostic imaging never supplies therapeutic evidence. The evidence bundle records **field availability**
(`intervention_types_available`, `intervention_descriptions_available`, `brief_summary_available`,
`detailed_description_available`) so
downstream match-evidence never implies an absent field was present.

## Priority order (deterministic, most specific first)

1. **Radioligand therapy** — fires on a therapeutic isotope (`177Lu`, `225Ac`, `223Ra`, `131I`,
   `90Y`, `212Pb`, and spelled variants) or a named therapeutic radioligand. Treatment intent
   dominates a theranostic pair, so this is checked **before** the diagnostic bucket.
2. **Diagnostic radiotracer / imaging agent** — fires only on explicit diagnostic evidence: a
   `DIAGNOSTIC_TEST` intervention type, a named diagnostic tracer, or a diagnostic isotope (`18F`,
   `68Ga`, `64Cu`, `89Zr`, `99mTc`, `11C`) **in an imaging/diagnostic context** (PET/SPECT/imaging/
   staging/detection words). It never fires on an isotope token alone.
3. **Cell or gene therapy** — `GENETIC` type or CAR-T / TIL / gene-therapy / vaccine / oncolytic cues.
4. **Biologic (antibody/protein)** — `BIOLOGICAL` type, antibody/monoclonal/bispecific cues, or a
   `*mab` suffix.
5. **Small molecule** — small-molecule cues (inhibitor/antagonist/agonist/kinase and `-inib` /
   `-parib` / `-lutamide` style stems) or a `DRUG` type.
6. **Radiation therapy** — `RADIATION` type or external-beam / SBRT / IMRT / brachytherapy cues (no
   radiopharmaceutical drug).
7. **Device / Procedure** — `DEVICE` / `PROCEDURE` type or surgery / ablation / biopsy cues.
8. **Behavioral / Supportive / Other** — `BEHAVIORAL` / `DIETARY_SUPPLEMENT` / `OTHER` type or
   behavioral / diet / exercise / supportive-care cues.
9. **Unclassified (insufficient description)** — nothing above resolved.

## study_purpose assignment

- `OBSERVATIONAL` study type → **Observational**.
- Diagnostic radiotracer category → **Diagnostic/Imaging**.
- Therapeutic-modality categories (radioligand therapy, small molecule, biologic, cell/gene,
  radiation) → **Therapeutic**.
- Device / Procedure → **Diagnostic/Imaging** if diagnostic cues are present, else **Other/Supportive**.
- Behavioral / Supportive / Other → **Other/Supportive**.
- Unclassified modality → **Therapeutic** if therapy words / interventions are present,
  **Diagnostic/Imaging** if diagnostic evidence is present, else **Unresolved**.

## Why the radioligand split is evidence-gated

A theranostic program can pair a diagnostic tracer with a therapeutic radioligand. The classifier
resolves this only from explicit record evidence: a therapeutic isotope or named therapeutic
radioligand routes to *Radioligand therapy*; a diagnostic isotope routes to *Diagnostic radiotracer*
**only when an imaging/diagnostic context is also present**. When the record shows neither, the trial
is left `Unclassified (insufficient description)` rather than being forced into a modality. This keeps
the split honest for records that carry only titles and drug names (the common offline case).

## Optional user-supplied disease configuration

`scripts/disease_config.py` accepts an **optional, user-supplied** mapping of mechanism labels to
name/regex patterns. **No configuration ships with this skill**, and the coherent generic mode above
is the validated default. When a user supplies a config, a config-driven `mechanism` label is attached
*in addition to* the generic `intervention_category`; otherwise `mechanism` defaults to the
`intervention_category` for backwards compatibility. Supplying a config never changes the generic
`study_purpose` / `intervention_category` axes and never turns the generic buckets into a
target-development taxonomy.

## Backwards compatibility

The former `scripts.classify_mechanisms` import path is preserved as a thin shim that re-exports
`classify_all`, `classify_record`, `classify_mechanism`, `classify_intervention_category`,
`classify_study_purpose`, `extract_evidence`, `INTERVENTION_CATEGORIES`, `STUDY_PURPOSES`,
`PHASE_MAP`, and `_normalize_phase`, delegating to `classify_study`. Its `classify_all` also restores
the five fields the historical function documented: `phase_normalized`, `drug_names`,
`drug_names_normalized`, `is_industry`, and `is_biosimilar` (raw-record and compiled-DataFrame paths;
on compiled rows `drug_names` holds every retained intervention name because intervention types are
not kept). The legacy `classify_mechanism(interventions, ...)` signature now returns
`(intervention_category, drug_names)` so old call sites keep working without inventing a
mechanism-of-action label. Prefer importing from `classify_study` in new code.
