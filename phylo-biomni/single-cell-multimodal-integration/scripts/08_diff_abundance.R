#!/usr/bin/env Rscript
# ============================================================================
# 08_diff_abundance.R — replicate-aware differential abundance with Milo on the
# joint (RNA PCA + ADT PCA) embedding. Runs ONLY with >=2 biological replicates
# per condition AND >1 condition (inference-readiness gate); otherwise it is
# skipped and flagged in diffabund_status.json — never a fabricated FDR. Figure 8.
# ============================================================================
here <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]))
suppressMessages({ source(file.path(here, "_common.R")); library(Seurat); library(ggplot2) })
set.seed(1)

cfg <- load_config(); ensure_dirs(cfg)
obj <- readRDS(ckpt(cfg, "04_wnn.rds"))
ann <- utils::read.csv(ckpt(cfg, "cell_annotations.csv"), stringsAsFactors = FALSE)
obj$cell_type <- ann$cell_type[match(colnames(obj), ann$barcode)]
d <- cfg$diffabund
meta <- obj[[]]

gate <- replicates_sufficient(meta, d$replicate_col %||% "replicate",
                              d$condition_col %||% "condition", min_rep = d$min_rep %||% 2)
status <- list(ran = FALSE, method = "miloR", reason = gate$reason,
               per_condition = gate$per_condition, n_neighborhoods = 0L, n_significant = 0L,
               fdr_threshold = 0.1)

if (isTRUE(gate$sufficient)) {
  upd <- tryCatch({
    suppressMessages({ library(miloR); library(SingleCellExperiment); library(SummarizedExperiment) })
    cond <- d$condition_col %||% "condition"
    if (!"sample" %in% colnames(meta)) {
      meta$sample <- paste(meta[[cond]], meta[[d$replicate_col %||% "replicate"]], sep = "_")
      obj$sample <- meta$sample
    }
    joint <- cbind(Embeddings(obj, "pca"), Embeddings(obj, "apca")); colnames(joint) <- paste0("J", seq_len(ncol(joint)))
    sce <- SingleCellExperiment(assays = list(logcounts = GetAssayData(obj, assay = "RNA", layer = "data")),
                                colData = S4Vectors::DataFrame(obj[[]]))
    SingleCellExperiment::reducedDim(sce, "JOINT") <- joint
    dd <- ncol(joint); kk <- d$k %||% 20
    milo <- Milo(sce)
    milo <- buildGraph(milo, k = kk, d = dd, reduced.dim = "JOINT")
    milo <- makeNhoods(milo, prop = d$prop %||% 0.2, k = kk, d = dd, refined = TRUE, reduced_dims = "JOINT")
    milo <- countCells(milo, meta.data = data.frame(colData(milo)), sample = "sample")
    milo <- calcNhoodDistance(milo, d = dd, reduced.dim = "JOINT")
    des <- data.frame(colData(milo))[, c("sample", cond)]; des <- des[!duplicated(des$sample), ]
    rownames(des) <- des$sample; des <- des[colnames(nhoodCounts(milo)), , drop = FALSE]
    res <- testNhoods(milo, design = stats::as.formula(paste("~", cond)), design.df = des, reduced.dim = "JOINT")
    res <- annotateNhoods(milo, res, coldata_col = "cell_type")
    utils::write.csv(res, tabp(cfg, "milo_da_neighborhoods.csv"), row.names = FALSE)
    n_sig <- sum(res$SpatialFDR < 0.1, na.rm = TRUE)
    res$sig <- ifelse(res$SpatialFDR < 0.1, "SpatialFDR<0.1", "n.s.")
    contrast_lab <- paste(cond, paste(gate$conditions, collapse = " vs "))
    p8 <- ggplot(res, aes(x = cell_type, y = logFC, color = sig)) +
      geom_hline(yintercept = 0, color = "#8A8378") +
      geom_jitter(width = 0.2, height = 0, size = 1.6, alpha = 0.85) +
      scale_color_manual(values = c("SpatialFDR<0.1" = "#D55E00", "n.s." = "#8A8378")) +
      coord_flip() + plot_theme() +
      labs(x = NULL, y = sprintf("Milo log-fold change (%s)", contrast_lab),
           color = NULL, title = "Neighborhood differential abundance")
    save_fig(cfg, p8, "figure_8_test_replicate_aware.png", w = 8, h = 4.8)
    list(ran = TRUE, n_neighborhoods = nrow(res), n_significant = as.integer(n_sig),
         reason = sprintf("Milo ran on %d neighborhoods; %d significant at SpatialFDR<0.1", nrow(res), n_sig),
         contrast = contrast_lab)
  }, error = function(e) list(ran = FALSE, reason = paste("Milo error -> skipped:", conditionMessage(e))))
  status <- modifyList(status, upd)
}

if (!isTRUE(status$ran)) {
  log_msg("Differential abundance skipped:", status$reason)
  comp <- as.data.frame(table(condition = meta[[d$condition_col %||% "condition"]], cell_type = obj$cell_type))
  p8 <- ggplot(comp, aes(cell_type, Freq, fill = condition)) +
    geom_col(position = "dodge") + scale_fill_manual(values = clpal(max(2, length(unique(comp$condition))))) +
    coord_flip() + plot_theme() +
    labs(x = NULL, y = "cells", fill = "condition",
         title = "Differential abundance SKIPPED (insufficient replication)",
         subtitle = strwrap(status$reason, 80)[1])
  save_fig(cfg, p8, "figure_8_test_replicate_aware.png", w = 8, h = 4.8)
}

write_status(cfg, "diffabund_status.json", status)
log_msg("Wrote diffabund_status.json + figure_8. ran =", status$ran)
