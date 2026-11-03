# Dual-caller structural-variant analysis

The production route runs Sniffles2 and cuteSV on the same explicitly selected aligned long-read BAM and reference, or compares Sniffles2 with an explicitly selected second VCF. Both callsets are normalized, sorted, bgzip-compressed, indexed, and validated before summary.

Caller support is derived from validated upstream coverage and recorded in provenance: Sniffles2 uses
`max(2, ceil(0.10 * coverage))`; cuteSV uses `max(10, ceil(0.10 * coverage))`. Missing upstream
coverage stops before calling. The operator `--platform` must agree with the upstream normalized
platform/read type before choosing cuteSV presets.

`sv/sv_calls.tsv` retains each normalized call's sample ID, FILTER, QUAL, genotype, genotype quality,
and caller read support when present. Missing sample fields remain empty. Multi-sample input fails
closed until an explicit sample-selection contract exists; the parser never silently chooses the
first sample.

Concordance uses the package's pre-existing class-aware matching rule: variants must share SV type and chromosome, and their breakpoints must fall within the configured distance tolerance; insertions and other length-bearing calls also require compatible lengths. Shared and caller-unique counts are descriptive robustness measures. They are never labelled sensitivity, precision, truth, or accuracy.

- Sniffles2: https://github.com/fritzsedlazeck/Sniffles (MIT)
- cuteSV: https://github.com/tjiangHIT/cuteSV (GPL-3.0; invoked as an independently installed executable, not bundled)

Low depth, repeats, alignment artifacts, platform characteristics, caller parameters, and representational differences can all alter the results. Review important calls in read-level evidence and confirm orthogonally.
