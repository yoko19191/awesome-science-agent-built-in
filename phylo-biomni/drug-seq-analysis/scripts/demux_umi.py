#!/usr/bin/env python3
"""Barcode + UMI demultiplexer for the DRUG-seq FASTQ front end.

R1 = well barcode (default 10 nt) + UMI (default 10 nt); R2 = cDNA. Each read pair is assigned to a
well by matching R1's barcode to the whitelist within edit distance 1 (Hamming; whitelist barcodes
are >= 2 apart so a 1-error correction is unambiguous), assigned to a gene by matching R2 to the
probe reference, then UMIs are collapsed per (well, gene). Output is a genes x wells UMI matrix plus
a barcode assignment rate. Low assignment rate signals a wrong whitelist or transposed plate map.

This is the deterministic, offline micro path. Production full-transcriptome alignment composes
STARsolo / DRAGoN / zUMIs (see references/fastq_frontend.md); this path is not a genome aligner.

CLI:
  python3 demux_umi.py --r1 fastq/R1.fastq --r2 fastq/R2.fastq --whitelist barcodes.txt \
      --reference reference.tsv --outdir /mnt/results [--bc-len 10] [--umi-len 10]
writes <outdir>/tables/fastq_counts.tsv and prints a JSON QC summary (barcode_assignment_rate).
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pandas as pd

BASES = "ACGT"


def _read_fastq_seqs(path):
    seqs = []
    with open(path) as fh:
        for i, line in enumerate(fh):
            if i % 4 == 1:
                seqs.append(line.strip())
    return seqs


def _hamming1_neighbors(bc: str):
    for i, ch in enumerate(bc):
        for b in BASES:
            if b != ch:
                yield bc[:i] + b + bc[i + 1:]


def build_matcher(whitelist: list[str]):
    wl = set(whitelist)
    return wl


def assign_barcode(bc: str, wl: set) -> str | None:
    if bc in wl:
        return bc
    hits = [n for n in _hamming1_neighbors(bc) if n in wl]
    return hits[0] if len(hits) == 1 else None


def run(r1_path, r2_path, whitelist_path, reference_path, outdir,
        bc_len: int = 10, umi_len: int = 10) -> dict:
    outdir = pathlib.Path(outdir)
    (outdir / "tables").mkdir(parents=True, exist_ok=True)
    whitelist = [b.strip() for b in open(whitelist_path) if b.strip()]
    wl = build_matcher(whitelist)
    ref = {}
    for line in open(reference_path):
        gid, seq = line.rstrip("\n").split("\t")
        ref[seq] = gid
    r1 = _read_fastq_seqs(r1_path)
    r2 = _read_fastq_seqs(r2_path)
    if len(r1) != len(r2):
        raise ValueError(f"R1 ({len(r1)}) and R2 ({len(r2)}) have different read counts")

    total = len(r1)
    assigned = 0
    gene_assigned = 0
    seen = set()                       # (well, gene, umi) for identity UMI collapse
    counts: dict[tuple[str, str], int] = {}
    for rr1, rr2 in zip(r1, r2):
        bc = rr1[:bc_len]
        umi = rr1[bc_len:bc_len + umi_len]
        well = assign_barcode(bc, wl)
        if well is None:
            continue
        assigned += 1
        gid = ref.get(rr2)             # exact probe match
        if gid is None:
            continue
        gene_assigned += 1
        key = (well, gid, umi)
        if key in seen:
            continue                   # UMI duplicate collapsed
        seen.add(key)
        counts[(gid, well)] = counts.get((gid, well), 0) + 1

    genes = sorted({g for g, _ in counts})
    wells = [w for w in whitelist if any((g, w) in counts for g in genes)]
    mat = pd.DataFrame(0, index=genes, columns=wells, dtype=int)
    for (g, w), c in counts.items():
        mat.at[g, w] = c
    mat.index.name = "gene_id"
    mat.to_csv(outdir / "tables" / "fastq_counts.tsv", sep="\t")

    rate = assigned / total if total else 0.0
    return {
        "n_read_pairs": total,
        "n_assigned_barcode": assigned,
        "barcode_assignment_rate": float(rate),
        "n_assigned_gene": gene_assigned,
        "n_umis_after_collapse": int(mat.values.sum()),
        "n_wells_with_reads": len(wells),
        "n_genes_detected": len(genes),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Barcode + UMI demultiplexer for DRUG-seq FASTQ.")
    ap.add_argument("--r1", required=True)
    ap.add_argument("--r2", required=True)
    ap.add_argument("--whitelist", required=True)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--bc-len", type=int, default=10)
    ap.add_argument("--umi-len", type=int, default=10)
    args = ap.parse_args()
    summary = run(args.r1, args.r2, args.whitelist, args.reference, args.outdir,
                  args.bc_len, args.umi_len)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
