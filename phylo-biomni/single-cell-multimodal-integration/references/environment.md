# Environment — single-cell-multimodal-integration

The supported CITE-seq route spans an R stack (Seurat/dsb/miloR) and a Python stack
(scvi-tools/scanpy/mudata). HDF5 reading is kept on the Python side (`scripts/_io.py`) so the
R steps never depend on a fragile R HDF5 reader at runtime.

## Interpreters
- **R** ≥ 4.4 with `R_LIBS_USER` pointing at a writable library (e.g. `/workspace/.Rlib`).
- **Python** ≥ 3.11. Point the pipeline at it with the `PYTHON` env var (falls back to `python3`).

## R packages
Seurat (≥5.0), SeuratObject (≥5.0), dsb (≥2.0), presto (≥1.0), miloR (≥2.0) and its Bioconductor
deps (SingleCellExperiment, SummarizedExperiment, edgeR, S4Vectors), Matrix, jsonlite, yaml,
ggplot2, patchwork, svglite. Optional: hdf5r. StabMap is inventoried but does not enable the
unsupported mosaic route.

Install notes (observed on this platform):
- miloR / RcppArmadillo need C++17 — set in `~/.R/Makevars`:
  `CXX_STD=CXX17`, `CXX11STD=-std=gnu++17`, `CXX14STD=-std=gnu++17`, `CXX17STD=-std=gnu++17`.
- hdf5r links `-lsz`; provide libaec (`libsz`) in the conda env if it is missing.
- Do **not** `conda/mamba install` R packages that would pull a new `r-base` — it churns the
  Seurat stack. Install R packages from source into `R_LIBS_USER` instead.

## Python packages
scvi-tools (totalVI), torch (CPU build sufficient), scanpy, anndata, mudata, muon, scikit-learn,
h5py, scipy, numpy, pandas, matplotlib, reportlab, pypdf, PyYAML. Optional: scib-metrics. scglue is
inventoried but does not enable the unsupported unpaired route.

Install notes (observed on this platform):
- **torch/torchvision must be a matched pair** (e.g. `torch==2.7.1` + `torchvision==0.22.1`, CPU
  builds). A CUDA torch on a CPU host, or a mismatched torchvision, breaks the
  `scvi → lightning → torchmetrics → torchvision` import chain.
- **numpyro must match the installed jax** (e.g. `numpyro>=0.21` for `jax>=0.10`); `scvi.model.TOTALVI`
  imports numpyro eagerly, so an incompatible pair blocks totalVI import.
- **`LD_LIBRARY_PATH` must include the conda `lib`** (e.g. `/opt/conda/lib`) so `pyarrow`/`anndata`
  find a `libstdc++` new enough for `GLIBCXX_3.4.32`. `scripts/run_all.sh` sets this automatically
  when a conda prefix is present; it is harmless elsewhere.
- Use `uv pip install` into a project venv under `/workspace/.venv`.

## Running
```bash
export PYTHON=/path/to/python           # optional; defaults to python3
bash scripts/run_all.sh config.yaml     # router first; then the selected branch
```
Exit 2 = STOP (unsupported/scRNA-only/trimodal); exit 3 = ROUTE (paired RNA+ATAC → `scatac-multiome-analysis`). The
branded PDF terminal step (GenerateImage infographic + `make_report.py` + `report_qc` receipt) is run
after the pipeline per `SKILL.md`.

## Compute
CITE-seq data with 10k–100k+ cells benefits from substantial RAM and, for totalVI, a GPU with a
recorded training budget sufficient for convergence.
