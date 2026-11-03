# Interpreting assembly QC: QUAST vs BUSCO vs Merqury

Read on demand. These three tools answer **three different questions**, and the skill reports them
side by side so none is mistaken for another. The values are computed by `scripts/lra.py`
(`contiguity_metrics`, `genome_fraction`, `busco_proxy`, `merqury_qv`) on the explicitly selected
assembly; production tools below remain the reference for full-scale analysis.

| Question | Metric family | Tool | In-package field |
|---|---|---|---|
| How contiguous? | # contigs, largest, N50/NG50, total length | QUAST | `contig_n50`, `largest_contig_bp`, `n_contigs`, `assembly_length_bp` |
| How complete (sequence vs a reference)? | genome fraction | QUAST | `genome_fraction_pct` |
| How complete (gene content)? | single-copy ortholog recovery | BUSCO / compleasm | `busco_complete_pct` |
| How accurate (consensus base quality)? | reference-free QV, k-mer completeness | Merqury | `merqury_qv` |

## Contiguity is not correctness (QUAST)

- **N50 is a length-weighted median, not the mean contig length.** N50 is the contig length `L` at
  which contigs `>= L` sum to at least half the assembly. A few concatemeric or chimeric contigs
  inflate N50; the skill therefore reports `contig_n50` next to genome fraction and QV, and the
  the report therefore keeps `contig_n50` distinct from mean contig length.
- **NG50** uses the estimated genome size (not the assembly size) as the denominator, so it is
  comparable across assemblies of the same genome; report it when a size estimate exists.
- A single long "circular" contig can be a **collapsed repeat or a misassembly**. QUAST flags
  misassemblies only against a reference; without one, contiguity says nothing about correctness.
- **genome fraction** = the percentage of the supplied reference covered by aligned contigs. It is a
  reference-based completeness measure and is `0` when no reference is supplied (never assume 100%).

## Completeness of gene content is not correctness (BUSCO)

- BUSCO reports the fraction of a lineage's near-universal single-copy orthologs found **Complete**
  (Single or Duplicated), **Fragmented**, or **Missing**. `compleasm` is a faster, largely concordant
  reimplementation.
- **High "Duplicated"** can indicate uncollapsed haplotypes (under-purged diploid assembly) or
  contamination — not extra completeness. Read it together with the contamination screen.
- A contaminated assembly can still score "complete" if the target genome's genes are all present, so
  **BUSCO completeness does not certify a clean assembly.** The skill's `busco_proxy` counts supplied
  single-copy markers found intact; the lineage-database BUSCO/compleasm run is the production measure.

## QV is consensus quality, not reference accuracy (Merqury)

- Merqury derives a **reference-free** consensus quality value from the k-mer spectrum of the reads
  versus the assembly: `QV = -10 * log10(estimated per-base error rate)`. QV 30 ≈ 1 error / 1,000 bp;
  QV 40 ≈ 1 / 10,000; QV 50 ≈ 1 / 100,000.
- **QV is not genome fraction and not BUSCO completeness.** A tiny, highly-polished fragment can have
  a high QV while covering little of the genome; the skill reports QV and genome fraction separately
  as distinct fields.
- **Low read coverage inflates QV** (few read k-mers disagree with the assembly), so interpret QV in
  light of depth. Merqury also reports k-mer completeness (fraction of reliable read k-mers present in
  the assembly), a second, orthogonal completeness measure.
- The skill reports `merqury_qv` for the draft and the polished assembly and requires
  `qv_polished >= qv_draft`; a QV drop after polishing signals bad read placement or over-polishing
  (see `polishing_order.md`).

## How to read them together

An assembly is *good enough to annotate or deposit* only when contiguity, gene-content completeness,
consensus accuracy, and a contamination screen all agree. Any one alone is misleading: high N50 +
contaminated, "complete" BUSCO + fragmented consensus, or high QV over 30% of the genome are all
failure modes a single number hides.

## Production tools (not bundled; see DATA_SOURCES.md)

- QUAST / MetaQUAST — https://github.com/ablab/quast (GPL-2.0)
- BUSCO — https://gitlab.com/ezlab/busco (MIT); compleasm — https://github.com/huangnengCSU/compleasm
- Merqury — https://github.com/marbl/merqury (US-Gov public domain); needs `meryl` k-mer DBs

Run these on Biomni HPC or a user install for real data; see `real_data_and_hpc.md`.
