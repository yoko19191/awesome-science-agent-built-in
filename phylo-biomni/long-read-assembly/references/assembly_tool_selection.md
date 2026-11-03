# Choosing an assembler by read type

Read on demand; adapt to the data. The one rule the skill enforces in code is: **an assembler must
match the declared read type** (`scripts/lra.py:check_assembler_readtype`). Everything else here is
guidance for choosing among compatible tools. Tool facts are from the R04 lane
`tool_comparison_matrix.csv`; verify licensing in `DATA_SOURCES.md`.

## Read type is decisive

`read_type` comes from `run.json` (explicit `read_type`, else derived from platform + kit/model +
basecaller_mode): one of `ont-simplex`, `ont-duplex`, `ont-hq`, `pacbio-hifi`, `pacbio-clr`.

| Read type | Recommended assembler(s) | Avoid |
|---|---|---|
| PacBio HiFi | **hifiasm** (flagship diploid/phased); LJA; Verkko (with ONT-UL for T2T) | noisy-read assemblers tuned for high error |
| Noisy ONT (simplex/hq) or PacBio CLR | **Flye** (best overall for CLR/ONT in the eukaryote benchmark); Raven/wtdbg2 (fast); NextDenovo; Canu | **hifiasm** (HiFi-only), Verkko without HiFi |
| HiFi + ONT ultra-long | **Verkko** or hifiasm(UL) for near-T2T | HiFi-only or noisy-only single-tech runs |
| Small bacterial (isolate, optional short reads) | **Unicycler** (hybrid), Flye, Canu | — |

## Why the guard exists (the wrong-but-obvious move)

Running one assembler regardless of read type is the classic silent failure: hifiasm expects
HiFi-accuracy reads and mis-assembles noisy ONT; Flye's `--pacbio-hifi` mode on ONT reads, or its
`--nano-raw` mode on HiFi, degrades the result. The skill derives `read_type`, checks the requested
assembler's supported set, and finalizes `not_computable` (writing
`long_read_assembly/assembly_readtype_check.json` with `incompatible_violations = 1`) **before**
assembling, rather than producing a confidently wrong assembly. The Flye mode flag that must match the
read type is recorded in `scripts/lra.py:FLYE_MODE`.

## Execution

- **Biomni HPC-native (verified in R04):** Flye/metaFlye, hifiasm, Canu, Verkko, NextDenovo, Raven,
  wtdbg2, Unicycler, QUAST. For real data, run these on Biomni HPC (see `real_data_and_hpc.md`).
- **`--assembler provided_draft --draft F.fasta`:** QC/polish a draft you assembled elsewhere (e.g. a
  Flye/hifiasm run from HPC).

Compute note (from the matrix): hifiasm/Canu need 100s of GB RAM for large genomes; Verkko is
multi-node Snakemake. Right-size the machine or downsample.
