# Model / chemistry compatibility (inherited provenance gate)

Read on demand. This skill does not implement the chemistry gate. It accepts the exact completed
`long-read-qc-basecalling` identity artifact, validates it with `scripts/lra.py:load_upstream_identity`,
and requires its `run_json_sha256` to match the selected `run.json` bytes. The exact artifact SHA-256
is recorded in assembly provenance and facts.

## The ONT basecaller model-name / chemistry convention (recognized as data provenance)

Oxford Nanopore basecalling-model names encode the flow-cell chemistry they were trained for. This is a
**property of the reads the user supplies** (declared in `run.json`), not a tool this skill uses: the
skill only *reads* the model-name token and compares it to the declared chemistry. **No ONT basecalling
software (Dorado, Medaka, etc.) is used, bundled, invoked, or depended on** — basecalling happened
upstream by `long-read-qc-basecalling`, and this skill consumes its output.

```
basecaller_model: dna_r10.4.1_e8.2_400bps_sup@v4.3.0   ->  encoded chemistry: r10.4.1
flowcell_chemistry: R10.4.1                             ->  declared chemistry: r10.4.1
=> match, model_chemistry_mismatch_violations = 0
```

A model trained for one chemistry run against a flow cell of another (e.g. an `r9.4.1` model with an
`R10.4.1` flow cell) produces systematically wrong base qualities and consensus — a silent error the
downstream assembly and QV would inherit. The gate catches it up front.

## Behaviour (returns 0 or 1 violation)

| Situation | `applicable` | `model_chemistry_mismatch_violations` | Outcome |
|---|---|---|---|
| ONT reads, encoded token == declared | true | 0 | proceed |
| ONT reads, encoded token != declared | true | 1 | `not_computable` before facts |
| ONT reads, model has no parseable token | true | 1 | `not_computable` (cannot verify) |
| PacBio, or a non-ONT model-name convention | false | 0 | not applicable — proceed |

PacBio HiFi/CLR do not use the ONT model-name convention, so the gate marks itself `not_applicable`
and does not block. The comparison is case-insensitive and token-based (`normalize_chemistry`,
`parse_model_chemistry`).

## Source of the convention

The chemistry-token convention (R9.4.1, R10.4.1, …) and its relevance to near-finished microbial
assembly are documented in the peer-reviewed literature — e.g. Sereika et al., *Nature Methods* 2022,
"Oxford Nanopore R10.4 long-read sequencing enables the generation of near-finished bacterial genomes
from pure cultures and metagenomes without short-read or reference polishing"
(https://doi.org/10.1038/s41592-022-01539-7). The source assertion `ont_model_chemistry_convention`
witnesses the gate output (`identity_check.json:model_chemistry_mismatch_violations = 0`) against that
reference. Read-level QC and basecalling themselves are never performed here.

## Why long-read-assembly consumes rather than re-implements it

Read-level QC, basecalling, and the chemistry verdict are upstream responsibilities. This package
opens the upstream artifact as shared data and never re-derives an equivalent verdict from raw
`run.json`; that prevents the two packages from silently drifting.
