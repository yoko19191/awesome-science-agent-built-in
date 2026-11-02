#!/usr/bin/env Rscript
# ============================================================================
# 07_annotate_markers.R — annotate joint clusters from SURFACE PROTEIN (ADT)
# programs (a built-in PBMC panel; CellMarker 2.0 withheld) and rank per-cluster
# RNA and ADT markers. Every assignment carries a confidence/margin flag.
# Writes joint_cluster_celltype_assignment.csv, rna_markers.csv,
# protein_markers.csv, cell_annotations.csv (for the h5mu) and figure_7.
# ============================================================================
here <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]))
suppressMessages({
  source(file.path(here, "_common.R"))
  source(file.path(here, "adt_contract.R"))
  source(file.path(here, "marker_programs.R"))
  library(Seurat)
  library(ggplot2)
})

cfg <- load_config(); ensure_dirs(cfg)
obj <- readRDS(ckpt(cfg, "04_wnn.rds"))
im <- readRDS(ckpt(cfg, "01_ingest_meta.rds"))
Idents(obj) <- "seurat_clusters"
clusters <- levels(obj$seurat_clusters)

# ---- built-in PBMC surface-protein programs (positive markers) ----
programs <- pbmc_surface_programs()

adt_data <- as.matrix(GetAssayData(obj, assay = "ADT", layer = "data"))
biological_adt <- setdiff(rownames(adt_data), im$isotypes %||% character(0))
if (!length(biological_adt)) stop("ADT panel contains no biological markers after isotype exclusion")
# per-cluster mean, then z-score each feature across clusters
cl_mean <- sapply(clusters, function(cl) rowMeans(adt_data[biological_adt, obj$seurat_clusters == cl, drop = FALSE]))
z <- t(scale(t(cl_mean)))                     # features x clusters, z per feature
z[is.na(z)] <- 0

# The package-owned Python contract performs TotalSeq-aware marker matching and refuses to turn
# an entirely unmatched panel into an arbitrary first-program assignment with a -Inf score.
score_path <- ckpt(cfg, "annotation_program_scores.csv")
program_path <- ckpt(cfg, "annotation_programs.json")
assignment_path <- ckpt(cfg, "annotation_program_assignments.csv")
utils::write.csv(data.frame(feature = rownames(z), z, check.names = FALSE),
                 score_path, row.names = FALSE, quote = TRUE)
jsonlite::write_json(programs, program_path, auto_unbox = FALSE, pretty = TRUE)
annotation_args <- c(
  file.path(here, "annotation_contract.py"),
  "--scores", score_path,
  "--programs", program_path,
  "--output", assignment_path
)
annotation_status <- system2(py_bin(), shQuote(annotation_args), stdout = TRUE, stderr = TRUE)
if (!identical(attr(annotation_status, "status") %||% 0L, 0L) || !file.exists(assignment_path)) {
  stop(sprintf("surface-marker annotation contract failed: %s",
               paste(annotation_status, collapse = "\n")))
}
assign_tab <- utils::read.csv(assignment_path, stringsAsFactors = FALSE, check.names = FALSE)
assign_tab$cluster <- as.character(assign_tab$cluster)

# ---- RNA log-fold changes + ADT normalized-score differences per cluster ----
rna_m <- tryCatch({
  DefaultAssay(obj) <- "RNA"
  FindAllMarkers(obj, assay = "RNA", only.pos = TRUE, min.pct = 0.1,
                 logfc.threshold = 0.1, verbose = FALSE)
}, error = function(e) { log_msg("RNA markers skipped:", conditionMessage(e)); NULL })

adt_status <- jsonlite::read_json(tabp(cfg, "adt_normalization_status.json"), simplifyVector = TRUE)
adt_marker_path <- tabp(cfg, "protein_markers.csv")
adt_marker_args <- c(
  file.path(here, "adt_marker_contract.py"),
  "--normalized", ckpt(cfg, "adt_normalized.csv"),
  "--raw-counts", ckpt(cfg, "adt_counts.csv"),
  "--clusters", ckpt(cfg, "wnn_cells.csv"),
  "--isotypes", paste(im$isotypes %||% character(0), collapse = ","),
  "--normalization-method", adt_status$method,
  "--output", adt_marker_path,
  "--min-pct", "0.1", "--min-effect", "0.1", "--top-n", "8"
)
adt_marker_status <- system2(py_bin(), shQuote(adt_marker_args), stdout = TRUE, stderr = TRUE)
if (!identical(attr(adt_marker_status, "status") %||% 0L, 0L) || !file.exists(adt_marker_path)) {
  stop(sprintf("ADT marker contract failed: %s", paste(adt_marker_status, collapse = "\n")))
}
adt_m <- utils::read.csv(adt_marker_path, stringsAsFactors = FALSE, check.names = FALSE)

topn <- function(m, effect, n = 10) if (is.null(m) || !nrow(m)) NULL else
  do.call(rbind, by(m, m$cluster, function(d) head(d[order(-d[[effect]]), ], n)))
rna_top <- topn(rna_m, "avg_log2FC")
adt_top <- topn(adt_m, "avg_normalized_adt_difference", 8)
if (!is.null(rna_top)) utils::write.csv(rna_top[, c("cluster","gene","avg_log2FC","p_val_adj","pct.1","pct.2")],
                                        tabp(cfg, "rna_markers.csv"), row.names = FALSE)

top_str <- function(m, cl, effect, n = 4) { if (is.null(m) || !nrow(m)) return(""); d <- m[m$cluster == cl, ]
  paste(head(d$gene[order(-d[[effect]])], n), collapse = ";") }
wc <- utils::read.csv(ckpt(cfg, "wnn_cells.csv"), stringsAsFactors = FALSE)
wc$seurat_clusters <- as.character(wc$seurat_clusters)   # name-based lookup (cluster '0' must not index positionally)

assign_tab$top_rna_markers <- sapply(assign_tab$cluster, top_str, m = rna_top, effect = "avg_log2FC")
assign_tab$top_adt_markers <- sapply(assign_tab$cluster, top_str, m = adt_top,
                                    effect = "avg_normalized_adt_difference")
assign_tab$mean_rna_weight <- round(sapply(assign_tab$cluster, function(cl) mean(wc$rna_weight[wc$seurat_clusters == cl])), 3)
assign_tab$mean_adt_weight <- round(1 - assign_tab$mean_rna_weight, 3)
assign_tab$n_cells <- as.integer(sapply(assign_tab$cluster, function(cl) sum(wc$seurat_clusters == cl)))
assign_tab <- assign_tab[, c("cluster","cell_type","top_rna_markers","top_adt_markers",
                             "mean_rna_weight","mean_adt_weight","n_cells","confidence","margin","top_program_score")]
utils::write.csv(assign_tab, tabp(cfg, "joint_cluster_celltype_assignment.csv"), row.names = FALSE)

# per-cell annotation for the h5mu / DA
ct_map <- stats::setNames(assign_tab$cell_type, assign_tab$cluster)
cell_ann <- data.frame(barcode = wc$barcode, cluster = wc$seurat_clusters,
                       cell_type = ct_map[wc$seurat_clusters])
utils::write.csv(cell_ann, ckpt(cfg, "cell_annotations.csv"), row.names = FALSE)

# ---- figure 7: per-cluster surface-protein heatmap + assigned type ----
zl <- as.data.frame(as.table(z)); colnames(zl) <- c("feature", "cluster", "z")
lab <- stats::setNames(sprintf("%s: %s%s", assign_tab$cluster, assign_tab$cell_type,
                               ifelse(assign_tab$confidence == "confident", "", paste0(" (", assign_tab$confidence, ")"))),
                       assign_tab$cluster)
zl$cluster_lab <- lab[as.character(zl$cluster)]
p7 <- ggplot(zl, aes(cluster_lab, feature, fill = z)) + geom_tile(color = "white", linewidth = 0.3) +
  scale_fill_gradient2(low = "#0072B2", mid = "#F9F7F3", high = "#D55E00", midpoint = 0) +
  plot_theme() + theme(axis.text.x = element_text(angle = 35, hjust = 1)) +
  labs(x = NULL, y = "surface protein (ADT)", fill = "z-score",
       title = "Per-cluster surface-protein signal (row z-scored)")
save_fig(cfg, p7, "figure_7_annotate_cells_and.png", w = 8.5, h = 5)

log_msg("Annotation done:", paste(sprintf("%s=%s", assign_tab$cluster, assign_tab$cell_type), collapse = ", "))
