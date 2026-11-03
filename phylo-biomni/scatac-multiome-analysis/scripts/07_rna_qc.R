#!/usr/bin/env Rscript
# ============================================================================
# STAGE 07 — (JOINT route only) modality-specific RNA QC + normalization.
# Reads the Gene Expression modality from the combined multiome matrix and the
# ATAC checkpoint (03_recalled.rds), QCs RNA on RNA-native metrics (NOT ATAC
# metrics), normalizes, selects HVGs. Writes checkpoints/07_rna.rds.
#
# WHY separate from ATAC QC: RNA and ATAC quality are captured by different
# metrics (genes/cell + %mito for RNA; TSS/FRiP/nucleosome for ATAC). Applying
# one modality's thresholds to the other silently drops good cells.
# ============================================================================
suppressPackageStartupMessages({ library(Seurat); library(ggplot2) })
source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(FALSE), value = TRUE))), "_common.R"))
cfg <- load_config(); set.seed(cfg$project$seed)

comb <- cfg$input$multiome_matrix_h5 %||% cfg$input$peak_matrix_h5
if (is.null(comb) || !file.exists(comb))
  stop("Stage 07 (joint) requires a combined multiome matrix (input.multiome_matrix_h5) with Gene Expression + Peaks.")
if (!requireNamespace("hdf5r", quietly = TRUE))
  stop("hdf5r required to read the combined multiome h5 (conda install -c conda-forge r-hdf5r).")

log_msg("Stage 07: reading Gene Expression modality from", basename(comb))
x <- Read10X_h5(comb)
if (!is.list(x) || is.null(x[["Gene Expression"]]))
  stop("Combined matrix has no 'Gene Expression' feature type; this is not a paired multiome bundle (router should have caught this).")
gex <- x[["Gene Expression"]]

rna <- CreateSeuratObject(counts = gex, assay = "RNA", project = cfg$project$name,
                          min.cells = 3, min.features = 1)
rq <- cfg$rna_qc %||% list()
mt_pat <- rq$mito_pattern %||% "^MT-"
rna[["percent.mt"]] <- PercentageFeatureSet(rna, pattern = mt_pat)

# pre-filter QC figure (RNA-native metrics)
save_fig(cfg, VlnPlot(rna, features = c("nFeature_RNA","nCount_RNA","percent.mt"),
                      pt.size = 0, ncol = 3) & plot_theme(),
         "16_rna_qc_violin_prefilter", 10, 4)

n_before <- ncol(rna)
min_genes <- rq$min_genes %||% 200
max_genes <- rq$max_genes %||% Inf; if (is.null(max_genes) || is.na(max_genes)) max_genes <- Inf
max_mt    <- rq$max_pct_mito %||% 20
min_counts<- rq$min_counts %||% 0
keep <- with(rna@meta.data,
             nFeature_RNA >= min_genes & nFeature_RNA <= max_genes &
             percent.mt <= max_mt & nCount_RNA >= min_counts)
rna <- rna[, which(keep)]
log_msg(sprintf("RNA QC filter: %d -> %d cells (genes[%s,%s] mt<=%s counts>=%s)",
                n_before, ncol(rna), min_genes, max_genes, max_mt, min_counts))

norm <- rq$normalization %||% "lognorm"
if (identical(norm, "sct")) {
  rna <- SCTransform(rna, assay = "RNA", verbose = FALSE)
} else {
  rna <- NormalizeData(rna, verbose = FALSE)
  rna <- FindVariableFeatures(rna, nfeatures = rq$n_hvg %||% 2000, verbose = FALSE)
  rna <- ScaleData(rna, verbose = FALSE)
}

rna_qc_summary <- data.frame(
  metric = c("rna_cells_before_QC","rna_cells_after_QC","rna_cells_removed",
             "median_nFeature_RNA","median_nCount_RNA","median_percent_mt",
             "min_genes","max_genes","max_pct_mito","normalization"),
  value = c(n_before, ncol(rna), n_before - ncol(rna),
            round(median(rna$nFeature_RNA)), round(median(rna$nCount_RNA)),
            round(median(rna$percent.mt), 2),
            min_genes, ifelse(is.finite(max_genes), max_genes, "Inf"), max_mt, norm))
write.csv(rna_qc_summary, tabp(cfg, "rna_qc_summary.csv"), row.names = FALSE)

saveRDS(list(rna = rna, norm = norm), ckpt(cfg, "07_rna.rds"))
log_msg("STAGE 07 done -> checkpoints/07_rna.rds"); cat("STAGE07_OK\n")
