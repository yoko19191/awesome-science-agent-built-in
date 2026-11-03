# Connectivity / MoA continuation

Connectivity scoring against an external compendium (CMap/LINCS) is **composed, not rebuilt** by this
skill. `signatures_moa.py` exports `tables/query_signatures.json` (top up/down genes per compound at
the top dose). This step makes those signatures available to a connectivity skill; it does **not** compute tau
in-package, and it stops cleanly when no reference is supplied.

## How to continue the analysis

1. Take `tables/query_signatures.json` (`{compound: {up: [...], down: [...]}}`).
2. Pass it to `signature-reversal-lincs` (or an equivalent CMap/LINCS connectivity workflow).
3. **Report at least two connectivity metrics** (e.g., weighted connectivity score / tau plus XSum or
   a rank-based score) and reconcile them — single-score reliance biases MoA calls, and published
   benchmarks show the metrics disagree.
4. Treat connectivity as a hypothesis-generating interpretation step, confirmed with orthogonal
   evidence (compound-vs-CRISPR concordance, dose extension), not as validation on its own.

## Data-use terms (must be checked before ingesting a reference)

- **LINCS L1000 / CMap (clue.io):** the reference compendium and its query service have their own
  terms of use. A free download does not imply redistribution rights. Record the version and terms in
  the run manifest; do not vendor the reference into this package.
- This package ships **no** connectivity reference. `connectivity.status = "ready_for_signature_reversal_lincs"` in
  `report_facts.json` records that connectivity was not computed in-run.

## Why not compute tau here

The reference is large, licensed, and lives in L1000 gene space (with imputation caveats). Rebuilding
it would duplicate `signature-reversal-lincs` and couple this skill to an external service inside a
deterministic front-end run. The boundary is deliberate.

Evidence: review §4.7, §9, §12; L1000/CMap tau [1], connectivity-metric disagreement [83, 88, 85].
