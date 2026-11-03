# Contamination screening (contig level)

Read on demand. The rule enforced in code (`scripts/lra.py:screen_contamination_contigs`, inherited
the `long-read-qc-basecalling` contamination contract applied at the contig level): **a screen that did not run is reported
`not_run_*`, never "clean".** A 0-flagged result is only meaningful when a reference or database was
actually supplied and used.

## Contig-level, after assembly

`long-read-qc-basecalling` screened reads; `long-read-assembly` screens **assembled contigs**, so a whole contig can be flagged as
contaminant-derived and excluded before QC and deposition. Each contig gets a disposition, and the
counts partition exactly:

```
contaminant_flagged_contigs + retained_after_triage_contigs = screened_contig_count
```

This partition is a contract fact; the receipt asserts that the members sum to the denominator.

## Methods (in order of increasing infrastructure)

| Method | `--contam-method` | Needs | In-package? | Notes |
|---|---|---|---|---|
| k-mer containment vs a contaminant reference | `kmer` | contaminant FASTA | **yes** (default) | No external tool; fraction of a contig's k-mers found in the contaminant reference. |
| requested reference alignment | `reference_align` | contaminant FASTA | no production alignment in this adapter | Executes k-mer containment triage and records `ok_kmer_triage`; never claims alignment evidence. |
| Taxonomic classification (Kraken2) | `taxonomic_db` | Kraken2 DB | no | Per-contig taxonomy; requires a user-provided DB, else `not_run_no_db`. |
| NCBI FCS-GX | `taxonomic_db` | FCS-GX GX database | no | Authoritative for genome submission screening; **~470 GB RAM** for the GX database — an HPC step. |
| GC-coverage-taxonomy (BlobTools) | — | multiple inputs | no | Visual/diagnostic; documented, not automated here. |

The k-mer route is dependency-free but requires an explicitly selected contaminant reference;
Production reference alignment, Kraken2, and FCS-GX require an external route; this bundled adapter
does not silently substitute their evidence strength.

## not-run is not clean (the guarded defect)

Skipping the screen, or running it with no reference/DB, and then reporting "0 contaminants" implies a
clean assembly that was never tested. The skill instead writes an explicit status
(`contamination_screen_status`) — one of `ok_kmer_triage`, `not_run_no_reference`,
`not_run_no_db`, or `not_run_unknown_method` — and the report must surface that status next to the flagged count. The
When no reference or database is supplied, the run must report a `not_run_*` status and never
`clean`.

## What a flag means (and does not)

- A flagged contig matched the supplied contaminant reference/DB; it does **not** prove biological
  contamination without review (adapters, index hopping, and true endosymbionts all look similar).
- An unflagged contig only means "no match to what was screened against" — coverage of the screen is
  bounded by the reference/DB supplied. Host/target references reduce false positives; a broad
  contaminant DB reduces false negatives.
- Contamination and BUSCO "Duplicated"/high genome size often co-occur — cross-read the QC
  (`assembly_qc_interpretation.md`).

## Production tools (not bundled; see DATA_SOURCES.md)

- minimap2 / mappy — https://github.com/lh3/minimap2 (MIT)
- Kraken2 — https://github.com/DerrickWood/kraken2 (MIT); user-provided DB
- FCS-GX — https://github.com/ncbi/fcs (US-Gov public domain); ~470 GB RAM GX database
