# Dependency, license & commercial-use inventory

Each shipped or invoked dependency was verified **independently** against its authoritative source
(package `LICENSE`/`DESCRIPTION` or upstream terms), not copied from any third-party comparison matrix.
Where a package license and the underlying data/assembly terms differ, they are recorded **separately**.

## Software

| Component | Version | License (source) | Commercial use | Authoritative source | Notes |
|---|---|---|---|---|---|
| Signac | >=1.12 | MIT | allowed | github.com/stuart-lab/signac `LICENSE` | Core scATAC/multiome. |
| Seurat | >=5.0 | MIT | allowed | github.com/satijalab/seurat `LICENSE` | WNN joint embedding. |
| MACS3 | >=3.0 | BSD-3-Clause | allowed | github.com/macs3-project/MACS `LICENSE` | Peak recalling. Verified BSD-3, not GPL. |
| presto | 1.0.0 | GPL-3.0-only | allowed (copyleft) | github.com/immunogenomics/presto; bioconda `r-presto` | Fast Wilcoxon markers. |
| scvi-tools | >=1.1 | BSD-3-Clause | allowed | github.com/scverse/scvi-tools `LICENSE` | Optional MultiVI. |
| mudata / muon | >=0.2 | BSD-3-Clause | allowed | github.com/scverse/mudata `LICENSE` | `.h5mu` container. |
| scanpy | >=1.9 | BSD-3-Clause | allowed | github.com/scverse/scanpy `LICENSE` | Python bridge utilities. |
| scib-metrics | >=0.5 | BSD-3-Clause | allowed | github.com/YosefLab/scib-metrics (`pyproject`/`LICENSE`) | Optional metric backend. |
| miloR | >=1.10 | GPL-3 | allowed (copyleft) | Bioconductor `miloR` `DESCRIPTION` | Differential abundance. |
| reportlab | >=4 | BSD | allowed | reportlab source `LICENSE` | PDF rendering (delegated report skill). |

## Reference data (package vs. underlying data recorded separately)

| Component | Version | Package license | Data/assembly terms | Commercial use | Notes |
|---|---|---|---|---|---|
| EnsDb.Hsapiens.v86 | 2.99.0 | Artistic-2.0 | Ensembl gene models (Ensembl terms; open) | allowed | hg38 gene models; installed at runtime. |
| BSgenome.Hsapiens.UCSC.hg38 | 1.4.5 | Artistic-2.0 (package code) | UCSC hg38/GRCh38 assembly (UCSC data-use policy; freely usable incl. commercial) | allowed | **Package Artistic-2.0 ≠ assembly data terms**; recorded separately. |

## Ambiguous / withheld

| Component | Signal | Disposition |
|---|---|---|
| **CellMarker 2.0** | Source NAR paper is **CC-BY-NC-4.0** (non-commercial); some aggregators list **CC0**; the CellMarker2 website shows a Harbin Medical University copyright with **no explicit data-reuse license**. | Commercial reuse could **not** be unambiguously verified → `commercial_status: not_checked`, `included: false`. Optional; annotation uses the package's documented PBMC marker-panel fallback without importing CellMarker data. |

## Evidence-reconciliation notes

- Licenses above were checked at the upstream source; the campaign review's tool-comparison-matrix license
  column and unverified corpus counts (e.g. "167 records") were **not** reused as evidence.
- MACS3 was independently confirmed **BSD-3-Clause** (a common mislabel is GPL).
- presto and miloR are copyleft (GPL); commercial use is permitted under GPL terms and recorded as `allowed`.
