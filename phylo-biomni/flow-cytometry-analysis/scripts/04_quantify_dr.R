#!/usr/bin/env Rscript
# =====================================================================================
# 04_quantify_dr.R  --  Cross-sample abundance quantification + dimensionality reduction.
#
#   - UMAP (+ tSNE cross-check) on a BALANCED subsample (default 10,000 cells/sample, capped at the
#     smallest sample's size) for speed/clarity. This affects the DR *visualization only* — clustering
#     and abundance/% use ALL cells.
#   - Population counts and % per sample.
#   - Cluster-by-sample relative-frequency heatmap.
#
# Usage:
#   Rscript 04_quantify_dr.R --sce <outdir>/sce_annotated.rds --outdir <outdir> \
#     --cells-per-sample 10000 --dr both --seed 1601
# Output: abundance_by_sample.csv, UMAP/tSNE figures, freq heatmap, sce_dr.rds
# =====================================================================================

suppressPackageStartupMessages({
  library(optparse); library(CATALYST); library(SingleCellExperiment)
  library(ggplot2); library(ggrepel); library(ComplexHeatmap); library(grid); library(svglite)
})

FREQUENCY_DISPLAY_LIMIT <- 20L

opt <- parse_args(OptionParser(option_list = list(
  make_option("--sce", type = "character"),
  make_option("--outdir", type = "character", default = "/mnt/results/cyto_run"),
  make_option("--cells_per_sample", type = "integer", default = 10000L, dest = "cps"),
  make_option("--dr", type = "character", default = "both"),   # umap | tsne | both
  make_option("--seed", type = "integer", default = 1601L)
)))
set.seed(opt$seed)
figdir <- file.path(opt$outdir, "figures"); dir.create(figdir, recursive = TRUE, showWarnings = FALSE)
tabdir <- file.path(opt$outdir, "tables"); dir.create(tabdir, recursive = TRUE, showWarnings = FALSE)
LOG <- file.path(opt$outdir, "qc_transform_log.txt")
logmsg <- function(...) { m <- sprintf(...); cat(m, "\n"); cat(m, "\n", file = LOG, append = TRUE) }
logmsg("=== 04_quantify_dr.R  |  seed=%d ===", opt$seed)

sce <- readRDS(opt$sce)
ann <- CATALYST::cluster_ids(sce, "annotation")
samp <- sce$sample_id

# ------------------------------------------------------------------ abundance per sample
tab <- table(population = ann, sample = samp)
props <- sweep(tab, 2, colSums(tab), "/") * 100
ab <- data.frame(population = rownames(tab), stringsAsFactors = FALSE)
for (s in colnames(tab)) { ab[[paste0(s, "_n")]] <- as.integer(tab[, s]); ab[[paste0(s, "_pct")]] <- round(props[, s], 3) }
ab$Total_n <- rowSums(tab); ab$Overall_pct <- round(rowSums(tab) / sum(tab) * 100, 3)
ab <- ab[order(-ab$Total_n), ]
write.csv(ab, file.path(tabdir, "abundance_by_sample.csv"), row.names = FALSE)
logmsg("Wrote abundance_by_sample.csv (%d populations x %d samples)", nrow(ab), ncol(tab))
logmsg("Abundance counts/%% computed on ALL %d post-QC cells (no subsampling; every event assigned).", ncol(sce))

# Figures summarize the most abundant groups when the complete partition is too large to label
# legibly. The full, unchanged abundance table above remains the machine-readable source of truth.
display_populations <- head(ab$population, FREQUENCY_DISPLAY_LIMIT)
display_props <- props[display_populations, , drop = FALSE]
display_counts <- tab[display_populations, , drop = FALSE]
has_other <- nrow(ab) > FREQUENCY_DISPLAY_LIMIT
if (has_other) {
  other_populations <- setdiff(rownames(props), display_populations)
  display_props <- rbind(display_props, other = colSums(props[other_populations, , drop = FALSE]))
  display_counts <- rbind(display_counts, other = colSums(tab[other_populations, , drop = FALSE]))
}

# abundance barplot (% per sample)
long <- do.call(rbind, lapply(colnames(display_counts), function(s)
  data.frame(population = rownames(display_counts), sample = s, pct = display_props[, s])))
long$population <- factor(long$population, levels = rev(rownames(display_props)))
pal <- c("#E6194B","#3CB44B","#4363D8","#F58231","#911EB4","#42D4F4","#F032E6","#BFEF45",
         "#FABED4","#469990","#9A6324","#800000","#000075","#A9A9A9","#808000","#FFD8B1")
pbar <- ggplot(long, aes(population, pct, fill = sample)) +
  geom_col(position = "dodge") + coord_flip() +
  labs(x = NULL, y = "% of sample", title = "Population abundance per sample") +
  theme_bw(base_size = 11) + theme(text = element_text(family = "Liberation Sans"))
ggsave(file.path(figdir, "fig_abundance_barplot.png"), pbar, width = 7, height = 5, dpi = 150)
ggsave(file.path(figdir, "fig_abundance_barplot.svg"), pbar, width = 7, height = 5)

# Raw percentages are always the primary frequency view. A single sample has no meaningful
# between-sample heatmap, so it receives a ranked percentage chart. Z-scores are supplemental
# only when at least three samples make standardization interpretable.
if (ncol(props) == 1L) {
  ranked_names <- rownames(display_props)[order(display_props[, 1])]
  ranked <- data.frame(population = factor(rownames(display_props), levels = ranked_names),
                       percentage = as.numeric(display_props[, 1]))
  pfreq <- ggplot(ranked, aes(population, percentage)) + geom_col(fill = "#276FBF") + coord_flip() +
    geom_text(aes(label = sprintf("%.2f%%", percentage)), hjust = -0.1, size = 3) +
    scale_y_continuous(expand = expansion(mult = c(0, 0.12))) +
    labs(title = sprintf("Population frequencies — %s", colnames(props)[1]), x = NULL, y = "% of sample") +
    theme_bw(base_size = 10) + theme(text = element_text(family = "Liberation Sans"))
  ggsave(file.path(figdir, "fig_frequency_ranked.png"), pfreq, width = 7, height = 5.5, dpi = 160)
  frequency_mode <- "single_sample_ranked_raw_percentage"
} else {
  ht_raw <- ComplexHeatmap::Heatmap(as.matrix(display_props), name = "%",
    column_title = "Raw population percentage by sample",
    row_names_gp = grid::gpar(fontsize = 8), column_names_gp = grid::gpar(fontsize = 8),
    right_annotation = ComplexHeatmap::rowAnnotation(n = ComplexHeatmap::anno_barplot(rowSums(display_counts))))
  png(file.path(figdir, "fig_freq_heatmap.png"), width = 1400, height = 1200, res = 170)
  ComplexHeatmap::draw(ht_raw); dev.off()
  frequency_mode <- "multi_sample_raw_percentage_heatmap"
  if (ncol(props) >= 3L) {
    zf <- t(scale(t(display_props))); zf[!is.finite(zf)] <- 0
    ht_z <- ComplexHeatmap::Heatmap(zf, name = "z(freq)",
      column_title = "Supplemental standardized population frequency",
      row_names_gp = grid::gpar(fontsize = 8), column_names_gp = grid::gpar(fontsize = 8))
    png(file.path(figdir, "fig_freq_heatmap_zscore.png"), width = 1400, height = 1200, res = 170)
    ComplexHeatmap::draw(ht_z); dev.off()
    frequency_mode <- "multi_sample_raw_percentage_plus_zscore"
  }
}
write.csv(data.frame(mode = frequency_mode, samples = ncol(props), primary_units = "percent",
                     total_populations = nrow(props), displayed_populations = nrow(display_props),
                     other_collapsed = has_other, display_limit = FREQUENCY_DISPLAY_LIMIT,
                     zscore_available = ncol(props) >= 3L, stringsAsFactors = FALSE),
          file.path(opt$outdir, "frequency_figure_state.csv"), row.names = FALSE)
logmsg("Frequency figure mode: %s.", frequency_mode)

# ------------------------------------------------------------------ dimensionality reduction
# This is the ONLY subsampling stage in the pipeline. It sets EMBEDDING DENSITY for the UMAP/t-SNE
# figures and nothing else -- clustering (02), annotation (03), abundance/% (above)
# and differential abundance (06) all use ALL post-QC cells. Raising --cells_per_sample makes the
# plots denser; it does NOT change any count, frequency, cluster assignment, or statistic.
n_per <- min(opt$cps, min(table(samp)))
if (n_per < opt$cps)
  logmsg("DR embedding subsample capped at %d cells/sample (smallest sample has %d < requested %d).",
         n_per, min(table(samp)), opt$cps)
logmsg("Running DR (UMAP/tSNE) on %d cells/sample across %d samples -- VISUALIZATION ONLY (analysis stages used all cells).",
       n_per, length(unique(samp)))
sce <- CATALYST::runDR(sce, dr = "UMAP", cells = n_per, features = "type")
if (opt$dr %in% c("tsne", "both")) sce <- CATALYST::runDR(sce, dr = "TSNE", cells = n_per, features = "type")

annotation_display <- as.character(ann)
if (has_other) annotation_display[!annotation_display %in% display_populations] <- "other"
sce$annotation_display <- factor(annotation_display,
                                 levels = c(display_populations, if (has_other) "other" else character()))

plot_dr <- function(sce, dr, color_by) {
  p <- CATALYST::plotDR(sce, dr, color_by = color_by)
  # centroid labels so color is secondary (dense scatters are hard with many hues)
  d <- p$data
  # Current CATALYST returns coordinate columns named 'x'/'y'; older versions used
  # 'UMAP1'/'TSNE1'/'dim1'. Handle both conventions.
  if (all(c("x", "y") %in% names(d))) {
    xy <- c("x", "y")
  } else {
    xy <- grep("^(UMAP|TSNE|dim)", names(d), value = TRUE, ignore.case = TRUE)[1:2]
  }
  d$.col <- d[[color_by]]
  cent <- aggregate(d[, xy], list(.col = d$.col), median)
  p + ggrepel::geom_label_repel(data = cent, aes(x = .data[[xy[1]]], y = .data[[xy[2]]], label = .col),
        size = 2.6, fill = "white", color = "black", max.overlaps = Inf, seed = 1, inherit.aes = FALSE) +
    ggplot2::guides(color = "none") +
    ggplot2::theme(text = ggplot2::element_text(family = "Liberation Sans"))
}
pu <- plot_dr(sce, "UMAP", "annotation_display")
ggsave(file.path(figdir, "fig_umap_clusters.png"), pu, width = 6.5, height = 5.5, dpi = 150)
ggsave(file.path(figdir, "fig_umap_clusters.svg"), pu, width = 6.5, height = 5.5)
if (opt$dr %in% c("tsne", "both")) {
  pt <- plot_dr(sce, "TSNE", "annotation_display")
  ggsave(file.path(figdir, "fig_tsne_clusters.png"), pt, width = 6.5, height = 5.5, dpi = 150)
  ggsave(file.path(figdir, "fig_tsne_clusters.svg"), pt, width = 6.5, height = 5.5)
}

saveRDS(sce, file.path(opt$outdir, "sce_dr.rds"))
logmsg("Saved: %s", file.path(opt$outdir, "sce_dr.rds"))
logmsg("=== 04 complete ===")
