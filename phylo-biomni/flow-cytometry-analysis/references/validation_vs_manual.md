# Validation against a manual-gating export (MANDATORY when one exists)

This is the check that, in the reference ADCC session, was the *only* thing that revealed a large
gating error the pipeline's own diagnostics had missed. **Whenever a manual-gating export exists
(a FlowJo "Export Statistics" file, or any per-sample population count / percentage table), reconcile
the automated pipeline against it BEFORE trusting any downstream fit, abundance comparison, or report.**
This is not optional polish — it is a gate. If the reconciliation flags large discrepancies, stop and
fix gating first.

## Why (the failure this prevents)

The automated pipeline reported ~40–50% dead target cells in no-antibody control wells; the scientist's
manual FlowJo gating showed ~10%. Every internal diagnostic (bimodal BV421, monotonic dose-response,
r≈0.81 correlations) looked *self-consistent*, so the pipeline "validated" itself and was wrong. The
manual export was external ground truth. The discrepancy localized immediately to the scatter/singlet
gate (kept 16,183 "cells" vs the manual 22,654 singlets; recovered 895 target cells vs 7,175 manual),
not to the viability threshold. **Self-consistency is not validation. An independent reference is.**

## What to reconcile

`scripts/08_validate_vs_manual.R` compares, per sample:

1. **Total post-QC cell count** (pipeline, from `sce_prepped.rds`) vs the manual top-level
   **singlets / "Cells"** count. *This is the primary over-gating detector* and needs only step-01
   output — run it as early as possible. A large deficit (pipeline ≪ manual) means the scatter/singlet
   gate is too tight (see `qc_gating.md` → "inspect the SCATTER gate FIRST").
2. **Per-population counts and percentages** (if an annotated abundance table from `04_quantify_dr.R`
   is available) vs the manual per-population counts / percentages with the same denominator definition. Matched by sample id and
   by population name (normalized, case-insensitive; supply a mapping for non-obvious names).

## Inputs

| Input | Notes |
|---|---|
| `--manual <file>` | FlowJo Export Statistics or any tidy/wide table. `.csv`/`.tsv` always supported; `.xlsx` if `readxl` is installed. |
| `--sce <sce_prepped.rds>` | For the total post-QC cell-count reconciliation. |
| `--abundance <abundance.csv>` | Optional. Per-sample per-population counts/percent from `04`. Enables population-level reconciliation. |
| `--sample-col`, `--population-col`, `--count-col`, `--pct-col` | Exact overrides for tidy manual tables. |
| `--parent-population` | Exact manual parent population. `auto` is allowed only when one unambiguous cells/singlets/live/intact parent exists. |
| `--tol-pp` (default 5) | Flag any `%` discrepancy larger than this many percentage points. |
| `--tol-rel` (default 0.20) | Flag any count discrepancy larger than this relative fraction. |
| `--map <auto\|name=Manual;...>` | Optional population-name mapping (pipeline→manual). |

## FlowJo "Export Statistics" shape (what the parser expects)

FlowJo exports are typically **wide**: one row per sample (file), and columns whose headers encode the
gate hierarchy with `/` separators plus a statistic, e.g.:

```
Name, Cells/Single Cells | Count, Cells/Single Cells/Target cells | Count, .../Target cells/DEAD+ | Freq. of Parent (%)
```

The parser:
- picks the **sample column** = first non-numeric/text column (or `--sample-col`);
- classifies each remaining column as a **count** (`Count`/`#` in header) or **percent**
  (`Freq`/`%`/`percent` in header), and takes the **leaf** gate name (after the last `/`, before `|`)
  as the population;
- melts to long `(sample, population, stat, value)`.

If your export is already tidy/long, pass `--sample-col`, `--count-col`, `--pct-col` explicitly.

## Percentage comparability

Step `04` percentages use all retained SCE cells per sample. Manual `%parent` may instead use an
immediate parent such as CD3+, so a matching population name alone does not make percentages comparable.
For tidy manual tables, supply `pct_denominator` on each percentage row with the exact manual population
name corresponding to the retained SCE cells (the selected `--parent-population`, or its unambiguous
`auto` match). Matching is case-insensitive. This declares the denominator definition, not equal event
membership or equal denominator counts between pipelines.

The script compares percentages only when that declaration matches and that sample has a unique,
finite positive count for the selected manual parent and retained SCE cells. Missing, duplicate, zero
or invalid parent counts cannot establish a percentage denominator. Unknown or different denominators
produce `NOT_COMPUTABLE_DENOMINATOR`, a specific reason, no delta and no tolerance flag; the overall
manual verdict stays `INCOMPLETE`. Count comparisons remain available. Mixed parent hierarchies may
have both comparable and non-computable rows. Wide exports have no verified denominator metadata and
therefore cannot establish percentage comparability: convert them to tidy rows and preserve each
actual denominator. Do not label an immediate-parent percentage as a singlet percentage. If an external
preparation derives percentages from counts, retain the original export, denominator counts and
transformation provenance; this script never guesses or normalizes them automatically.

## Verdict and behavior

- Writes `validation_vs_manual.csv` (per-sample, per-population: pipeline value, manual value, delta,
  flagged?) and logs a summary.
- Emits **PASS** only when every required sample/population matches and all comparisons are within
  `--tol-pp` / `--tol-rel`.
- Emits **REVIEW** for complete but disagreeing comparisons and **INCOMPLETE** for unmatched,
  missing, duplicate, ambiguous, or non-comparable reference data. Exit codes are 0/2/3 respectively.
- The report step (`07`) should surface the verdict; a REVIEW verdict must not be silently dropped.

## If no manual export exists

Say so explicitly, and fall back to the internal sanity checks in `qc_gating.md` (over-gating alarm,
implausible-baseline trip-wire). Do **not** treat internal self-consistency as if it were external
validation.

## References
- Weber & Robinson 2016 (population-matching conventions).
- FlowJo Export Statistics documentation (BD Life Sciences).
