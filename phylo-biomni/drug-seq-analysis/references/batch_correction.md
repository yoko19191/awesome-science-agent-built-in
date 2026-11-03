# Plate, positional, and batch correction

Read this when deciding whether and how to correct plate/positional/batch structure before contrasts.
Edge/positional gradients (evaporation, temperature) are well documented in plate assays and inflate
false positives if ignored; the choice is regime-dependent, not universal.

## Decision guide

1. **Randomize the layout at design time.** Interleave controls and randomize compound/dose across
   plate positions so treatment is not confounded with edge/position.
2. **Single plate, randomized positions:** fit `~ condition` (no plate term) and
   *report* the positional diagnostic (`well_qc.positional_diagnostics`: variance of top-PC scores
   associated with plate column). If `positional_col_association` is high (> 0.30 flags
   `positional_confounded`), escalate to step 4.
3. **Multiple plates:** always include `plate_id` as a covariate in the DESeq2 design. `de_deseq2.R`
   adds it automatically when `plate_id` has more than one level.
4. **Strong positional gradient:** add a position covariate — run `de_deseq2.R --covariate edge`
   (adds an edge/interior term), or model row/column explicitly. Alternatives: **ComBat-seq**
   (negative-binomial batch adjustment that preserves integer counts; needs known, non-confounded
   batches; requires the `sva` package), or **RUVSeq** (control-gene / replicate-based removal of
   unwanted variation). Diagnose before and after with PCA / BatchQC.
5. **Batch = condition (non-identifiable):** if a whole plate is one treatment group, plate and
   effect cannot be separated. `design_preflight.py` / `well_qc.check_identifiability` flag this and
   the run stops (`not_computable`). Do not "correct" it — it is a design defect.

## What not to do

- Do not apply ComBat-seq when batch is confounded with condition; it will remove real signal.
- Do not skip the positional diagnostic just because the design looks balanced — report it either way.
- A correction that changes nothing is not evidence of no batch effect; check the diagnostic.

Evidence: review §4.4, §6, §7; ComBat-seq [93], RUVSeq [101], BatchQC [102], plate/spatial-bias
methods [110, 103, 111, 114].
