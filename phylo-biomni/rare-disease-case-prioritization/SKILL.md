---
name: rare-disease-case-prioritization
description: Use to prioritize established rare-disease disease-gene hypotheses for
  one case by running a deterministic, fail-closed evidence-gating cascade that assigns
  each hypothesis an explicit evidence state with contradictions, missing-evidence
  codes, abstentions, provenance, and an optional reanalysis diff.
category: genomics_genetics
visibility: public
starting-prompt: Prioritize the established disease-gene hypotheses for this rare-disease
  case and explain each evidence state?
---
<!-- spec: phylo-analysis-spec/1 -->

# Rare Disease Case Prioritization

## When to use

Use to prioritize established rare-disease disease-gene hypotheses for one case by running a deterministic, fail-closed evidence-gating cascade that assigns each hypothesis an explicit evidence state with contradictions, missing-evidence codes, abstentions, provenance, and an optional reanalysis diff.

## Scope

Assign every candidate disease-gene hypothesis a transparent, reproducible evidence state (concordant, incomplete, conflicting, or abstained) with explicit reason codes, ranking, and provenance, so a human expert can decide where to focus follow-up -- without emitting a diagnosis, a diagnostic probability, or a formal variant classification.

## Inputs and prerequisites

One case-bundle directory: a GA4GH Phenopacket (phenopacket.json), a candidate disease-gene hypotheses table (candidates.tsv), and an evidence manifest (evidence_manifest.tsv); with optional family.tsv, phenotype-state-companion.json, and a prior evidence dossier for reanalysis.

Requirements:

- Provide phenopacket.json using GA4GH Phenopacket schema major version 2, whose top-level id is the case identifier and whose subject has a resolvable id.
- Provide candidates.tsv with the full controlled-vocabulary column set, one row per allele, every row's case_id equal to the phenopacket id, and a transcript_id on every consequence-bearing row.
- Provide evidence_manifest.tsv that declares every cited evidence_id exactly once (no duplicate rows) with its source_type, version_or_date, and verification_status.
- Assert an absent phenotype only with excluded:true; a term that is simply not listed is unknown, never absent.

Reject the input without success artifacts when:

- A required file is missing or unreadable, the phenopacket is malformed, its metaData.phenopacketSchemaVersion is missing/empty/not schema major version 2, or its subject id is unresolved.
- A candidates.tsv row uses an out-of-vocabulary value, disagrees with the phenopacket case_id, or omits a transcript_id on a consequence-bearing row.
- The evidence manifest has a row with an empty evidence_id or a duplicate evidence_id.
- A supplied prior evidence dossier does not match this case by schema or case_id.

Use the printed **Skill Directory** as `SKILL_DIR`. Do not edit the installed package.
Run once in a clean results directory; do not repair or retry in place.

## Analysis workflow

The canonical runner invokes one entrypoint, which performs these scientific steps in order:

1. Validate the case bundle fail-closed: check required files, parse the phenopacket, enforce controlled vocabularies, case_id agreement, and transcript presence on consequence-bearing rows, and reject with a named field before writing any output.
2. Resolve every cited evidence identifier against the manifest, separating resolved supporting/contradicting evidence from absent or unresolvable identifiers.
3. Evaluate each disease-gene hypothesis through the deterministic gate cascade (hard-stop abstentions, then contradictions, then missing-evidence blockers, else concordant), counting independent evidence lines with correlated predictors collapsed by predictor group.
4. Rank hypotheses by evidence state then independent evidence support, compute source-version provenance, and compute the optional reanalysis diff against a supplied prior dossier.
5. Write the ranked hypotheses table, the evidence dossier, the reanalysis diff, and the per-hypothesis evidence-completeness figure.

## Runtime interface

The only executable analysis entrypoint is `scripts/prioritize_cases.py`. Invoke it only through
`scripts/run_skill.py`; smoke validation and applications use this same path.
Outside the domain process, treat packaged Python modules as CLI-only implementation files.
Do not import them for inspection or presentation: imports can create cache files and
invalidate the frozen package. Use canonical finalization or independent standard tools.

| Parameter                         | Type   | Allowed values                                                         | Required | Default                   | Scientific effect                                                                                                                                         |
| --------------------------------- | ------ | ---------------------------------------------------------------------- | -------- | ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `entry-mode` / `--entry-mode` | string | `"phenotype_first"`, `"genotype_first"`, `"incomplete_evidence"` | false    | `"incomplete_evidence"` | Label-only entry context recorded on every hypothesis for one identical workflow; it never changes gate precedence, evidence states, ranking, or outputs. |

Map user-requested variations only to these declared parameters. A bounded method or tool
choice remains an internal implementation detail of the one entrypoint. If the requested
deviation is not declared here or would change inputs, workflow semantics, or output meaning,
stop and request a new package revision rather than bypassing the runner or editing the package.

## Scientific caveats and limitations

- **Assumption:** Inputs describe an ESTABLISHED disease-gene relationship with pre-annotated nuclear germline SNVs/indels; the skill never establishes a novel gene-disease association from a single case or publication.
- **Limitation:** Research decision support only: it emits evidence states, contradictions, missing-evidence codes, and abstentions, never a diagnosis, a diagnostic probability, or an ACMG/AMP classification.
- **Limitation:** Variant classes beyond snv/indel, and any hypothesis whose cited evidence cannot be resolved, are abstained rather than forced through SNV/indel logic.
- **Watch Out:** A concordant evidence state is not a clinical result. Canonical runtime checks establish artifact integrity and accounting; report interpretation, citation compliance and visual quality require external inspection and acceptance.

Source, software, and licensing details are recorded in `DATA_SOURCES.md`.

## Additional guidance

- Read [`references/evidence-gating-model.md`](references/evidence-gating-model.md) when you need the exact gate precedence, evidence-state definitions, and reason-code vocabulary behind each ranked hypothesis.
- Read [`references/report-policy.md`](references/report-policy.md) when before preparing report facts, citations, the infographic, or any PDF narrative; its exact bibliography and research-only rules are mandatory.
- Read [`references/execution-interface.md`](references/execution-interface.md) when migrating an existing invocation or interpreting which checks the canonical runner owns.

## Outputs

| Output                                          | Kind  | Stage        |
| ----------------------------------------------- | ----- | ------------ |
| `hypotheses.tsv`                              | file  | analysis     |
| `evidence_dossier.json`                       | json  | analysis     |
| `reanalysis_diff.json`                        | json  | analysis     |
| `figures/evidence_completeness.png`           | image | analysis     |
| `report_facts.json`                           | json  | presentation |
| `infographic.png`                             | image | presentation |
| `report_rare-disease-case-prioritization.pdf` | pdf   | presentation |

## Execute

Resolve the actual user input. Then run the canonical runner, adding one
`--param name=<JSON value>` argument for each non-default parameter:

```bash
python3 "$SKILL_DIR/scripts/run_skill.py" execute --input "$INPUT"
```

The runner verifies the frozen package, constructs the entrypoint command, binds the actual
input and parameter values, rejects dirty output paths, and hashes every analysis output.

## Build the scientific report

Read the selected report provider. Default to `pdf-report-generation`; use another
compatible provider only when the user explicitly requested it. Create:

- `report_facts.json`, including `source_outputs` copied exactly from the canonical
  runner's analysis-output path/hash map;
- `infographic.png` as the opening visual;
- `report_rare-disease-case-prioritization.pdf` as the reader-facing report.

Styling may change presentation but never the canonical analysis artifacts or facts.
Subjective scientific and visual quality remains an external review responsibility.

## Finalize and verify

```bash
python3 "$SKILL_DIR/scripts/run_skill.py" finalize
python3 "$SKILL_DIR/scripts/run_skill.py" verify-receipt
```

A terminal pass is valid only while the frozen package, entrypoint, input, and every declared
output retain the hashes recorded in the single `run_receipt.json`.

Return the requested scientific artifacts and a concise result summary. Keep package and
receipt internals out of the normal user-facing response.
