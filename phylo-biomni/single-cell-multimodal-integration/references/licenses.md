# Dependency licenses — single-cell-multimodal-integration

Software licenses re-verified at the authoritative source (package DESCRIPTION / repository
LICENSE / PyPI) for this package. **Software license is recorded separately from any underlying
data/assembly terms.** "Commercial use" refers to the software license only; copyleft (GPL/LGPL)
permits commercial use but imposes source-availability obligations on distribution of derivatives.
Re-verify before any redistribution.

## Runtime software (used by the executed pipeline)

| Dependency | Role | License | Commercial use | Source of truth |
|---|---|---|---|---|
| Seurat (>=5.0) | RNA/ADT QC, WNN joint embedding, clustering, markers | MIT | allowed | GitHub `satijalab/seurat` LICENSE.md |
| SeuratObject (>=5.0) | assay/object model | MIT | allowed | GitHub `satijalab/seurat-object` |
| dsb (>=2.0) | ADT ambient/background denoising | CC0-1.0 (public domain) | allowed | CRAN `dsb` DESCRIPTION |
| presto (>=1.0) | fast Wilcoxon markers (Seurat accelerator; optional) | GPL-3.0-only | allowed (copyleft) | GitHub `immunogenomics/presto` |
| miloR (>=2.0) | replicate-aware differential abundance | GPL-3 | allowed (copyleft) | Bioconductor `miloR` DESCRIPTION |
| edgeR (transitive via miloR) | NB GLM / TMM inside Milo testNhoods | GPL (>=2) | allowed (copyleft) | Bioconductor `edgeR` DESCRIPTION |
| SingleCellExperiment / SummarizedExperiment | Milo containers | Artistic-2.0 / GPL-3 | allowed (copyleft) | Bioconductor DESCRIPTIONs |
| scvi-tools / totalVI (>=1.1) | probabilistic RNA+ADT latent space | BSD-3-Clause | allowed | GitHub `scverse/scvi-tools` LICENSE |
| scanpy / anndata (>=1.9 / >=0.10) | Python single-cell I/O + preprocessing | BSD-3-Clause | allowed | GitHub `scverse` LICENSEs |
| mudata / muon (>=0.3 / >=0.1) | `.h5mu` multimodal container | BSD-3-Clause | allowed | GitHub `scverse/mudata`, `scverse/muon` |
| scikit-learn (>=1.3) | integration metrics (silhouette, ARI/NMI, kNN entropy) | BSD-3-Clause | allowed | GitHub `scikit-learn/scikit-learn` |
| reportlab (>=4) | branded PDF rendering | BSD-3-Clause | allowed | PyPI `reportlab` LICENSE |
| pypdf (>=4) | PDF page/embedding checks | BSD-3-Clause | allowed | GitHub `py-pdf/pypdf` |
| h5py (>=3) | HDF5 reading (Python `_io.py`) | BSD-3-Clause | allowed | GitHub `h5py/h5py` |
| matplotlib (>=3.7) | figure/infographic rendering | Matplotlib License (BSD-style, PSF-based) | allowed | matplotlib.org/stable/users/project/license |
| PyYAML | config parsing | MIT | allowed | PyPI `PyYAML` |

## Optional / route-specific backends

| Dependency | Role | License | Commercial use | Notes |
|---|---|---|---|---|
| scglue / GLUE (>=0.3) | method inventory only; unavailable in this package | MIT | allowed | Installing it does not enable the unsupported unpaired route; GitHub `gao-lab/GLUE` LICENSE |
| StabMap (>=0.1) | method inventory only; unavailable in this package | GPL-2 | allowed (copyleft) | Installing it does not enable the unsupported mosaic route; Bioconductor/GitHub `MarioniLab/StabMap` |
| hdf5r (>=1.3) | optional R HDF5 reader (runtime avoids it via `_io.py`) | Apache-2.0 | allowed | CRAN `hdf5r` DESCRIPTION |
| scib-metrics (>=0.5) | optional integration-metric cross-check | BSD-3-Clause | allowed | The shipped scorecard uses scikit-learn; scib-metrics is not required |
| MOFA2 | optional multi-omics factor cross-check (not in the primary path) | Apache-2.0 (R wrapper) | allowed | Not invoked by the executed workflow |

## Withheld reference resources

| Resource | Reason | Disposition |
|---|---|---|
| CellMarker 2.0 | Conflicting reuse signals (paper CC-BY-NC-4.0; site copyright; no explicit reuse grant). Commercial reuse could not be unambiguously verified. | **Withheld.** A built-in PBMC surface-protein marker panel ships as the annotation fallback; CellMarker 2.0 is never fetched or bundled. |

## Bundled data

| Artifact | License | Notes |
|---|---|---|

## Notes

- GPL/GPL-2/GPL-3 and Artistic-2.0 are copyleft: commercial *use* is permitted; distributing modified
  versions triggers source-availability obligations. Permissive licenses (MIT, BSD-3, Apache-2.0,
  CC0) impose no such obligation.
- No dependency in the executed path is AGPL or non-commercial-restricted.
- This inventory covers the software this skill calls; transitive dependencies inherit their own
  licenses (predominantly BSD/MIT/Apache across the scverse and CRAN/Bioconductor stacks).
