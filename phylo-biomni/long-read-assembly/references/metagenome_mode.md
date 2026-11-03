# Bounded metagenome mode

Read on demand. `--mode metagenome` changes routing and QC interpretation, but **binning and the
MAG-completeness tools are gated to HPC / a user install** — they are not run in CI. What runs
in-package for a metagenome is the shared, deterministic layer: contiguity, contamination screening,
and provenance. Isolate mode (`--mode isolate`, default) is the fully in-package path.

## What changes in metagenome mode

| Step | Isolate mode | Metagenome mode |
|---|---|---|
| Assembler | Flye / hifiasm / Verkko | **metaFlye** (`flye --meta`) or hifiasm-meta | 
| Completeness | BUSCO single-copy orthologs (one genome) | **CheckM2** per MAG (many genomes) |
| Binning | n/a (one organism) | MetaBAT2 / MaxBin2 / SemiBin (contig binning) — **gated** |
| QC framing | one genome's contiguity/QV | per-MAG completeness/contamination vs **MIMAG** |

A metagenome is a mixture, so a single genome fraction / BUSCO / global N50 is not meaningful; quality
is judged **per recovered bin (MAG)**.

## MIMAG quality standards

The Minimum Information about a Metagenome-Assembled Genome (MIMAG) standard classifies each MAG:

| Tier | Completeness | Contamination | Additional |
|---|---|---|---|
| High-quality draft | > 90% | < 5% | 23S, 16S, 5S rRNA present + >= 18 tRNAs |
| Medium-quality draft | >= 50% | < 10% | — |
| Low-quality draft | < 50% | < 10% | — |

Completeness and contamination are estimated from lineage-specific single-copy marker genes.
**CheckM2** (machine-learning predictor) is the current default; CheckM1 (lineage-specific marker
sets) remains widely used. Both are external tools (see DATA_SOURCES.md).

## Why binning is gated, not run in CI

Reliable binning needs (a) multi-genome coverage structure and (b) tools/databases sized for
HPC. When those requirements are absent, forcing a binning step would fabricate structure that is
not in the data. So metagenome mode in this package:

- routes to the metaFlye/CheckM2/binning **plan** and records the intended commands;
- still runs the shared contiguity + contamination + provenance layer deterministically;
- stops cleanly (no fabricated MAGs) when the gated tools are unavailable, exactly like the assembler
  gate (`assembly_status.json`).

Run the full metagenome path — metaFlye assembly, MetaBAT2/SemiBin binning, CheckM2 per-MAG QC, and
MIMAG classification — on Biomni HPC or a user install; see `real_data_and_hpc.md`.

## Production tools (not bundled; see DATA_SOURCES.md)

- metaFlye — part of Flye, https://github.com/mikolmogorov/Flye (BSD-3-Clause)
- CheckM2 — https://github.com/chklovski/CheckM2 (GPL-3.0)
- Binning: MetaBAT2, MaxBin2, SemiBin — user/HPC install
