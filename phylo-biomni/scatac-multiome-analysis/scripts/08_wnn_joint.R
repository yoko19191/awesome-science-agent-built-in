#!/usr/bin/env Rscript
# ============================================================================
# STAGE 08 — (JOINT route) Seurat WNN joint embedding of paired RNA + ATAC.
# Loads 03_recalled.rds (ATAC recalled peaks) + 07_rna.rds (QC'd RNA), keeps the
# intersection of cells, recomputes per-modality reductions on that shared set,
# then FindMultiModalNeighbors (WNN) -> joint UMAP + clusters + per-cell modality
# weights. Annotates joint clusters from RNA markers (confidence-gated). Exports
# raw matrices + embeddings for the Python bridge (h5mu / metrics / MultiVI).
#
# WHY WNN as default: it learns a per-cell weight for each modality and needs no
# GPU; MultiVI is offered as a probabilistic sensitivity route in stage 09.
# ============================================================================
suppressPackageStartupMessages({ library(Signac); library(Seurat); library(Matrix); library(ggplot2) })
source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(FALSE), value = TRUE))), "_common.R"))
source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(FALSE), value = TRUE))), "wnn_export_contract.R"))
cfg <- load_config(); set.seed(cfg$project$seed)

atac <- readRDS(ckpt(cfg, "03_recalled.rds"))$obj
rna  <- readRDS(ckpt(cfg, "07_rna.rds"))$rna
shared <- intersect(colnames(atac), colnames(rna))
if (length(shared) < 50)
  stop(sprintf("Only %d shared cells between RNA and ATAC after QC; too few for a joint embedding. Check pairing.", length(shared)))
log_msg(sprintf("Joint cells (RNA n=%d, ATAC n=%d) -> shared n=%d", ncol(rna), ncol(atac), length(shared)))

obj <- subset(atac, cells = shared)
rna <- subset(rna,  cells = shared)
rna <- rna[, colnames(obj)]            # enforce identical cell order before assay transfer (Seurat v5)
obj[["RNA"]] <- rna[["RNA"]]

# ---- per-modality reductions on the shared set -----------------------------
DefaultAssay(obj) <- "RNA"
obj <- NormalizeData(obj, verbose = FALSE) |>
  FindVariableFeatures(nfeatures = cfg$rna_qc$n_hvg %||% 2000, verbose = FALSE) |>
  ScaleData(verbose = FALSE) |>
  RunPCA(npcs = 50, verbose = FALSE, reduction.name = "pca")

DefaultAssay(obj) <- "recalled"
obj <- RunTFIDF(obj, verbose = FALSE) |>
  FindTopFeatures(min.cutoff = cfg$dimreduc$min_cutoff %||% "q0") |>
  RunSVD(reduction.name = "lsi")

rna_dims  <- 1:(cfg$wnn$rna_dims_n %||% 30)
atac_dims <- (cfg$wnn$atac_first_dim %||% 2):(cfg$wnn$atac_dims_n %||% 30)  # drop LSI comp 1

# ---- WNN -------------------------------------------------------------------
log_msg("FindMultiModalNeighbors (WNN) ...")
obj <- FindMultiModalNeighbors(obj, reduction.list = list("pca", "lsi"),
                               dims.list = list(rna_dims, atac_dims),
                               modality.weight.name = c("RNA.weight", "ATAC.weight"))
obj <- RunUMAP(obj, nn.name = "weighted.nn", reduction.name = "wnn.umap",
               reduction.key = "wnnUMAP_", verbose = FALSE)
obj <- FindClusters(obj, graph.name = "wsnn", algorithm = 3,
                    resolution = cfg$wnn$resolution %||% 1.0, verbose = FALSE)
obj$wnn_clusters <- obj$seurat_clusters

# ---- modality weights (assert in [0,1]) ------------------------------------
wcol <- grep("\\.weight$", colnames(obj@meta.data), value = TRUE)
wsum <- do.call(rbind, lapply(wcol, function(c) data.frame(
  modality = sub("\\.weight$", "", c),
  mean_weight = round(mean(obj@meta.data[[c]]), 4),
  median_weight = round(median(obj@meta.data[[c]]), 4),
  min = round(min(obj@meta.data[[c]]), 4), max = round(max(obj@meta.data[[c]]), 4))))
write.csv(wsum, tabp(cfg, "modality_weights_summary.csv"), row.names = FALSE)
stopifnot(all(vapply(wcol, function(c) all(obj@meta.data[[c]] >= 0 & obj@meta.data[[c]] <= 1), logical(1))))
log_msg("Modality weights in [0,1]. Means: ",
        paste(sprintf("%s=%.3f", wsum$modality, wsum$mean_weight), collapse = "  "))

# ---- joint annotation from RNA markers (confidence-gated) -------------------
PBMC_PANEL <- list(
  "CD14 Mono"=c("CD14","LYZ","S100A8","S100A9"), "CD16 Mono"=c("FCGR3A","MS4A7","CDKN1C"),
  "CD4 T"=c("IL7R","CD3D","CD3E","TCF7"), "CD8 T"=c("CD8A","CD8B","GZMK"),
  "NK"=c("GNLY","NKG7","KLRD1","NCAM1"), "B"=c("MS4A1","CD79A","CD79B","BANK1"),
  "DC"=c("FCER1A","CST3","CLEC9A","IRF8"), "Platelet"=c("PPBP","PF4","ITGA2B"))
panel <- if (!is.null(cfg$annotation$marker_panel) && file.exists(cfg$annotation$marker_panel %||% ""))
           yaml::read_yaml(cfg$annotation$marker_panel) else PBMC_PANEL
DefaultAssay(obj) <- "RNA"
avg <- as.matrix(AverageExpression(obj, assays = "RNA", group.by = "wnn_clusters")$RNA)
colnames(avg) <- sub("^g", "", colnames(avg))
z <- t(scale(t(avg))); z[is.na(z)] <- 0; rownames(z) <- toupper(rownames(z))
setU <- lapply(panel, toupper)
score_mat <- sapply(setU, function(g){ g <- intersect(g, rownames(z))
  if(!length(g)) rep(NA_real_, ncol(z)) else colMeans(z[g,,drop=FALSE]) })
rownames(score_mat) <- colnames(z)
assign_one <- function(v){ v[is.na(v)] <- -Inf; o <- order(v, decreasing=TRUE)
  top <- v[o[1]]; sec <- if(length(v)>1) v[o[2]] else -Inf
  flag <- if(top < (cfg$annotation$min_top_zscore %||% 1.0)) "low-confidence"
          else if((top-sec) < (cfg$annotation$min_margin %||% 0.25)) "ambiguous" else "ok"
  data.frame(cell_type=colnames(score_mat)[o[1]], top_score=round(top,2),
             margin=round(top-sec,2), confidence=flag) }
atab <- do.call(rbind, lapply(seq_len(nrow(score_mat)), function(i) assign_one(score_mat[i,])))
atab <- cbind(cluster=rownames(score_mat), atab)
write.csv(atab, tabp(cfg, "joint_cluster_celltype_assignment.csv"), row.names = FALSE)
map <- setNames(atab$cell_type, atab$cluster)
ct <- unname(map[as.character(obj$wnn_clusters)]); names(ct) <- colnames(obj); ct[is.na(ct)] <- "Unknown"
obj$cell_type <- factor(ct, levels = sort(unique(ct)))
obj$annotation_label <- obj$cell_type

# ---- attach experimental-unit metadata (replicate/condition/sample) --------
md_path <- cfg$input$metadata_csv
if (!is.null(md_path) && file.exists(md_path)) {
  md <- read.csv(md_path, stringsAsFactors = FALSE)
  bc_col <- names(md)[tolower(names(md)) %in% c("barcode","cell","cell_id","cb")][1]
  if (!is.na(bc_col)) {
    rownames(md) <- md[[bc_col]]
    metric_label_col <- cfg$integration_metrics$label_key %||% "cell_type"
    for (col in setdiff(names(md), bc_col)) {
      v <- md[colnames(obj), col]
      if (identical(col, metric_label_col)) {
        obj$reference_label <- v
      } else if (!identical(col, "cell_type")) {
        obj[[col]] <- v
      }
    }
    log_msg("Attached metadata columns:", paste(setdiff(names(md), bc_col), collapse=", "))
  } else log_msg("metadata.csv has no barcode column; skipping metadata join.")
}

# ---- joint figures ---------------------------------------------------------
save_fig(cfg, DimPlot(obj, reduction="wnn.umap", group.by="cell_type", label=TRUE, repel=TRUE) +
           ggtitle("WNN joint UMAP (cell type)") & plot_theme(), "17_wnn_umap_celltype", 7.5, 5.5)
wlong <- data.frame(weight = obj@meta.data[[wcol[1]]], modality = sub("\\.weight$","",wcol[1]))
if (length(wcol) > 1) wlong <- rbind(wlong, data.frame(weight=obj@meta.data[[wcol[2]]], modality=sub("\\.weight$","",wcol[2])))
save_fig(cfg, ggplot(wlong, aes(modality, weight, fill=modality)) +
           geom_violin(scale="width") + scale_fill_manual(values=clpal(length(wcol))) +
           labs(x=NULL, y="Per-cell modality weight", title="WNN modality weights") +
           plot_theme() + theme(legend.position="none"), "18_modality_weights", 6, 4.5)

# ---- exports for the Python bridge -----------------------------------------
ex <- file.path(cfg$project$outdir, "joint_export"); dir.create(ex, showWarnings = FALSE, recursive = TRUE)
rna_counts <- GetAssayData(obj, assay = "RNA", layer = "counts")
atac_counts <- GetAssayData(obj, assay = "recalled", layer = "counts")
Matrix::writeMM(rna_counts,  file.path(ex, "rna_counts.mtx"))
Matrix::writeMM(atac_counts, file.path(ex, "atac_counts.mtx"))
writeLines(rownames(rna_counts),  file.path(ex, "rna_genes.txt"))
writeLines(rownames(atac_counts), file.path(ex, "atac_peaks.txt"))
writeLines(colnames(obj),         file.path(ex, "barcodes.txt"))
obs <- obj@meta.data
obs$barcode <- rownames(obs)
metric_batch_col <- cfg$integration_metrics$batch_key %||% "replicate"
keepcols <- intersect(c("barcode","cell_type","annotation_label","reference_label",
                        "confidence","wnn_clusters",wcol,
                        metric_batch_col,"replicate","condition","sample","donor"), colnames(obs))
write.csv(obs[, keepcols], file.path(ex, "obs.csv"), row.names = FALSE)
umap <- as.data.frame(Embeddings(obj, "wnn.umap")); umap$barcode <- rownames(umap)
write.csv(umap, file.path(ex, "wnn_umap.csv"), row.names = FALSE)
# Canonical metric-coordinate export: the WNN-derived UMAP, not raw PCA+LSI.
write.csv(umap, file.path(ex, "joint_embedding.csv"), row.names = FALSE)
# Export the actual WNN weighted-neighbor graph. The graph can include self as
# its first neighbor, so remove self before any purity/mixing calculation.
weighted_neighbor <- obj[["weighted.nn"]]
nn_index <- methods::slot(weighted_neighbor, "nn.idx")
nn_distance <- methods::slot(weighted_neighbor, "nn.dist")
assert_wnn_neighbor_shapes(nn_index, nn_distance, ncol(obj))
neighbor_rows <- do.call(rbind, lapply(seq_len(nrow(nn_index)), function(cell_index) {
  keep_neighbor <- nn_index[cell_index, ] != cell_index
  neighbor_index <- nn_index[cell_index, keep_neighbor]
  data.frame(
    barcode = colnames(obj)[cell_index],
    neighbor_barcode = colnames(obj)[neighbor_index],
    rank = seq_along(neighbor_index),
    distance = nn_distance[cell_index, keep_neighbor],
    stringsAsFactors = FALSE
  )
}))
write.csv(neighbor_rows, file.path(ex, "wnn_neighbors.csv"), row.names = FALSE)

saveRDS(list(obj = obj, weights = wsum, assign = atab, export_dir = ex,
             rna_dims = rna_dims, atac_dims = atac_dims), ckpt(cfg, "08_joint.rds"))
log_msg("STAGE 08 done -> checkpoints/08_joint.rds + joint_export/"); cat("STAGE08_OK\n")
