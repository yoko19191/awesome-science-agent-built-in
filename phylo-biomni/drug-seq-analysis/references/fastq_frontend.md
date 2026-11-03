# FASTQ front end: demultiplexing to a counts matrix

DRUG-seq FASTQ has R1 = well barcode (typically 8-10 nt) + UMI (typically 8-10 nt); R2 = cDNA.
Barcode/UMI geometry differs per protocol (PLATE-seq R1 = 8-nt barcode; BRB-seq early barcode + UMI),
so length and read position must be **parameterized**, never hard-coded.

## Production path (compose a real aligner)

For whole-transcriptome quantification, compose an established pipeline rather than aligning by hand:

- **DRAGoN** (Nextflow, DRUG-seq-native): demultiplex-first for per-well parallelism and per-well QC;
  STAR + featureCounts; UMI collapse within a 10-bp position tolerance and <=1 edit distance.
- **zUMIs**: flexible barcode/UMI geometry, exon+intron counting, adaptive downsampling (high memory
  at very large scale).
- **STARsolo**: fast, versatile cell-barcode/UMI handling; configure the barcode as the well barcode.
- **UMI-tools**: reference UMI collapse (directional adjacency recommended over naive identity to
  avoid over-collapsing at depth).

Steps: bcl2fastq -> R1/R2; assign reads to wells by matching R1's barcode to the whitelist at edit
distance 1; align R2 (STAR) to genome+annotation (3' assays) or a probe reference (TempO-Seq);
featureCounts to genes; collapse UMIs (directional / position-tolerant); output genes x wells matrix.

## Bundled offline micro path (`demux_umi.py`)

`demux_umi.py` is the deterministic, dependency-light path used for the demo/tests: it matches R1
barcodes to the whitelist (Hamming <=1; whitelist barcodes are >=2 apart so correction is
unambiguous), matches R2 to a small **probe reference by exact sequence**, and collapses UMIs by
identity per (well, gene). It is **not** a genome aligner and is not a substitute for STARsolo/DRAGoN
on real whole-transcriptome data — use it to exercise the barcode/UMI logic and QC, then move to the
production path for real quantification.

## Stop condition

If the barcode assignment rate is below `well_qc.MIN_ASSIGNMENT_RATE` (0.5), the whitelist or plate
map is almost certainly wrong (transposed map, wrong whitelist). Stop and reconcile — do not proceed
to contrasts on mislabeled wells.

Evidence: review §4.1-4.2, §3, §7; DRAGoN [26], zUMIs [128], STARsolo [138], UMI-tools [131],
PLATE-seq / BRB-seq geometry [35, 27].
