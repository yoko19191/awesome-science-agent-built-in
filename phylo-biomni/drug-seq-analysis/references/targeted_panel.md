# Targeted-panel sub-mode (L1000 / TempO-Seq)

Whole-transcriptome 3' DRUG-seq (the default) sequences 3' ends and detects ~11-14k genes. Targeted
panels measure a fixed gene set and must **disclose measured vs imputed genes** — conflating them
over-states coverage.

## The two panel families

- **TempO-Seq / RASL (ligation panels):** two detector oligos ligate only when hybridized adjacently,
  then well-barcoded PCR. Panels range from ~1,500 sentinel genes (S1500+, ~2,753 genes) to whole
  transcriptome. There is **no** reverse-transcription length bias. Align R2 to a small **probe
  reference** (not a genome); a genome aligner is inappropriate. `panel.measured_vs_imputed` should
  record "all measured within the probe panel; genes outside the panel are absent, not zero".
- **L1000 (bead hybridization):** measures **978 landmark genes** and computationally **infers**
  ~11,350 more. About **17% of inferred transcripts are poorly inferred** and should be excluded from
  downstream claims. `panel.measured_vs_imputed` should separate measured (978) from imputed, and
  flag the excluded poorly-inferred set. "Depth" is per-bead fluorescence, not reads.

## Rules

- Never treat an unmeasured/undetected panel gene as a true zero — it was not assayed.
- Never rank or interpret L1000 imputed genes without marking them imputed; exclude the poorly-inferred
  ~17%.
- Record the panel, its version, and the measured/imputed partition in the run manifest and report.

The production engine supports the **whole-transcriptome 3'** regime. The targeted-panel path is a
documented sub-mode that requires a caller-supplied probe reference and measured-gene list.

Evidence: review §2, §3, §7, §12; L1000 [1], TempO-Seq / S1500+ [9, 10, 7], House concentration-
response pipeline (temposeqcount) [43].
