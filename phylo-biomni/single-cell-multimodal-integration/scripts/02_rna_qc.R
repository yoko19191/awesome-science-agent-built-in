#!/usr/bin/env Rscript
# ============================================================================
# 02_rna_qc.R — modality-specific QC. RNA: counts, genes, percent-mito.
# ADT: total protein counts. A cell is retained only if it passes BOTH
# modalities (CITE-seq cells are paired by construction, so the joint set is
# the intersection). Records the cell-accounting partition.
# ============================================================================
here <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]))
suppressMessages({ source(file.path(here, "_common.R")); library(Seurat); library(ggplot2) })

cfg <- load_config(); ensure_dirs(cfg)
obj <- readRDS(ckpt(cfg, "01_ingest.rds"))
n_input <- ncol(obj)

q <- cfg$qc
obj[["percent.mt"]] <- PercentageFeatureSet(obj, pattern = q$rna$mt_pattern %||% "^MT-")
obj[["nCount_ADT"]]  <- Matrix::colSums(GetAssayData(obj, assay = "ADT", layer = "counts"))
obj[["nFeature_ADT"]] <- Matrix::colSums(GetAssayData(obj, assay = "ADT", layer = "counts") > 0)

md <- obj[[]]
rna_ok <- md$nFeature_RNA >= (q$rna$min_genes %||% 0) &
          md$nFeature_RNA <= (q$rna$max_genes %||% Inf) &
          md$nCount_RNA   >= (q$rna$min_counts %||% 0) &
          md$percent.mt   <= (q$rna$max_pct_mt %||% 100)
adt_ok <- md$nCount_ADT >= (q$adt$min_counts %||% 0) &
          md$nCount_ADT <= (q$adt$max_counts %||% Inf)
keep <- rna_ok & adt_ok
n_keep <- sum(keep); n_drop <- n_input - n_keep
log_msg(sprintf("QC: retained %d / %d cells (%d dropped: RNA-fail %d, ADT-fail %d)",
                n_keep, n_input, n_drop, sum(!rna_ok), sum(!adt_ok)))

# ---- figure 3: per-modality QC with thresholds ----
mdp <- md; mdp$stage <- "pre-filter"
long <- rbind(
  data.frame(metric = "RNA counts",     value = mdp$nCount_RNA),
  data.frame(metric = "RNA genes",      value = mdp$nFeature_RNA),
  data.frame(metric = "percent mito",   value = mdp$percent.mt),
  data.frame(metric = "ADT total counts", value = mdp$nCount_ADT))
thr <- data.frame(metric = c("RNA counts", "RNA genes", "ADT total counts"),
                  y = c(q$rna$min_counts, q$rna$min_genes, q$adt$min_counts))
p3 <- ggplot(long, aes(x = metric, y = value, fill = metric)) +
  geom_violin(scale = "width", trim = TRUE, alpha = 0.85) +
  geom_jitter(width = 0.15, size = 0.2, alpha = 0.25) +
  geom_hline(data = thr, aes(yintercept = y), linetype = "dashed", color = "#D55E00") +
  facet_wrap(~metric, scales = "free", nrow = 1) +
  scale_fill_manual(values = clpal(4)) + plot_theme() +
  theme(legend.position = "none", axis.text.x = element_blank(), axis.title.x = element_blank()) +
  labs(y = "value", title = NULL)
save_fig(cfg, p3, "figure_3_qc_each_modality.png", w = 9, h = 4)

# ---- QC summary table ----
qc_tab <- data.frame(
  metric = c("cells_input", "cells_retained", "cells_dropped",
             "median_nCount_RNA", "median_nFeature_RNA", "median_percent_mt", "median_nCount_ADT"),
  value = c(n_input, n_keep, n_drop,
            round(median(md$nCount_RNA), 1), round(median(md$nFeature_RNA), 1),
            round(median(md$percent.mt), 3), round(median(md$nCount_ADT), 1)))
utils::write.csv(qc_tab, tabp(cfg, "rna_adt_qc_summary.csv"), row.names = FALSE)

obj <- subset(obj, cells = colnames(obj)[keep])
saveRDS(obj, ckpt(cfg, "02_qc.rds"))

# CITE-seq is paired by construction: no unpaired cells in this route.
write_facts_fragment(cfg, "facts_02.json", list(
  n_joint_cells = n_keep, n_cells_dropped_qc = n_drop, n_cells_unpaired = 0L))
log_msg("Wrote QC summary + figure_3; saved filtered object.")
