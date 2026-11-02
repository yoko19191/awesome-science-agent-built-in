# Citations — methods used by single-cell-multimodal-integration

1. **10x Genomics.** Feature-barcode matrices (Cell Ranger HDF5 outputs). Product documentation —
   feature types `Gene Expression`, `Antibody Capture`, `Peaks`.
   https://www.10xgenomics.com/support/software/cell-ranger/latest/analysis/outputs/cr-outputs-h5-matrices

2. **Mulè MP, Martins AJ, Tsang JS.** Normalizing and denoising protein expression data from
   droplet-based single-cell profiling. *Nature Communications* 13, 2099 (2022).
   doi:10.1038/s41467-022-29356-8  — **dsb** ADT background/technical denoising.

3. **Hao Y, Hao S, Andersen-Nissen E, et al.** Integrated analysis of multimodal single-cell data.
   *Cell* 184, 3573–3587 (2021). doi:10.1016/j.cell.2021.04.048  — **Seurat WNN** joint embedding and
   per-cell modality weights.

4. **Gayoso A, Steier Z, Lopez R, et al.** Joint probabilistic modeling of single-cell multi-omic
   data with totalVI. *Nature Methods* 18, 272–282 (2021). doi:10.1038/s41592-020-01050-x  —
   **totalVI** probabilistic RNA+ADT latent space.

5. **Dann E, Henderson NC, Teichmann SA, Morgan MD, Marioni JC.** Differential abundance testing on
   single-cell data using k-nearest neighbor graphs. *Nature Biotechnology* 40, 245–253 (2022).
   doi:10.1038/s41587-021-01033-z  — **Milo** replicate-aware differential abundance.

6. **Cao ZJ, Gao G.** Multi-omics single-cell data integration and regulatory inference with
   graph-linked embedding. *Nature Biotechnology* 40, 1458–1466 (2022). doi:10.1038/s41587-022-01284-4
   — **GLUE** method context only; the unpaired route is unavailable in this package.

7. **Ghazanfar S, Guibentif C, Marioni JC.** Stabilized mosaic single-cell data integration using
   unshared features (StabMap). *Nature Biotechnology* 42, 284–292 (2024). doi:10.1038/s41587-023-01766-z
   — **StabMap** method context only; the mosaic route is unavailable in this package.

8. **Korsunsky I, et al. / Immunogenomics.** presto: fast Wilcoxon rank-sum and auROC for marker
   detection. https://github.com/immunogenomics/presto  — Seurat marker-test accelerator.

9. **Luecken MD, et al.** Benchmarking atlas-level data integration in single-cell genomics
   (scib metrics: silhouette, kBET/iLISI concepts). *Nature Methods* 19, 41–50 (2022).
   doi:10.1038/s41592-021-01336-8  — integration-metric definitions (this skill's scorecard uses
   scikit-learn implementations of silhouette, ARI/NMI, and a kNN batch-entropy).

10. **Bredikhin D, Kats I, Stegle O.** MUON: multimodal omics analysis framework (`.h5mu`).
    *Genome Biology* 23, 42 (2022). doi:10.1186/s13059-021-02577-8  — multimodal container.

## Related / reused
- **scrnaseq-seurat-core-analysis** (origin/main `66048e24`) — RNA QC/normalization conventions.
- **`scatac-multiome-analysis`** — the paired RNA+ATAC route target.
