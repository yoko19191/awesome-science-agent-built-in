#!/usr/bin/env Rscript
# ============================================================================
# 04_wnn_joint.R — Seurat Weighted Nearest Neighbor joint embedding.
# RNA PCA + ADT PCA -> FindMultiModalNeighbors -> joint UMAP + joint clusters +
# per-cell modality weights. Asserts every weight lies in [0,1] (witnessed in
# provenance_manifest.json: wnn.modality_weight_out_of_unit_interval). Figure 5.
# ============================================================================
here <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]))
suppressMessages({ source(file.path(here, "_common.R")); library(Seurat); library(ggplot2); library(patchwork) })
set.seed(1)

cfg <- load_config(); ensure_dirs(cfg)
obj <- readRDS(ckpt(cfg, "03_adt.rds"))
im  <- readRDS(ckpt(cfg, "01_ingest_meta.rds"))
isotypes <- intersect(im$isotypes, rownames(obj[["ADT"]]))
w <- cfg$wnn

# ---- RNA branch ----
DefaultAssay(obj) <- "RNA"
obj <- NormalizeData(obj, verbose = FALSE)
obj <- FindVariableFeatures(obj, nfeatures = min(2000, nrow(obj)), verbose = FALSE)
obj <- ScaleData(obj, verbose = FALSE)
rna_dims <- min(w$rna_dims %||% 20, ncol(obj) - 1, nrow(obj) - 1)
obj <- RunPCA(obj, npcs = rna_dims, reduction.name = "pca", verbose = FALSE)

# ---- ADT branch (dsb/CLR data already in the ADT 'data' layer) ----
DefaultAssay(obj) <- "ADT"
adt_feats <- setdiff(rownames(obj[["ADT"]]), isotypes)            # exclude isotypes from the embedding
VariableFeatures(obj) <- adt_feats
obj <- ScaleData(obj, features = adt_feats, verbose = FALSE)
adt_dims <- min(w$adt_dims %||% 10, length(adt_feats) - 1)
obj <- RunPCA(obj, features = adt_feats, npcs = adt_dims, reduction.name = "apca",
              approx = FALSE, verbose = FALSE)

# ---- WNN ----
obj <- FindMultiModalNeighbors(obj, reduction.list = list("pca", "apca"),
                               dims.list = list(1:rna_dims, 1:adt_dims),
                               k.nn = w$k_nn %||% 20, verbose = FALSE)
obj <- RunUMAP(obj, nn.name = "weighted.nn", reduction.name = "wnn.umap",
               reduction.key = "wnnUMAP_", verbose = FALSE)
obj <- FindClusters(obj, graph.name = "wsnn", algorithm = 3,
                    resolution = w$cluster_resolution %||% 0.8, verbose = FALSE)
n_clusters <- nlevels(obj$seurat_clusters)

# ---- modality weights (assert in [0,1]) ----
rw <- obj$RNA.weight
aw <- if (!is.null(obj$ADT.weight)) obj$ADT.weight else 1 - rw
n_bad <- sum(rw < 0 | rw > 1 | aw < 0 | aw > 1, na.rm = TRUE)
med_rna_w <- round(as.numeric(median(rw, na.rm = TRUE)), 4)
log_msg(sprintf("WNN: %d clusters | median RNA weight = %.3f | out-of-[0,1] weights = %d",
                n_clusters, med_rna_w, n_bad))
update_provenance(cfg, list(wnn = list(
  modality_weight_out_of_unit_interval = as.integer(n_bad),
  median_rna_modality_weight = med_rna_w, n_joint_clusters = n_clusters)))

emb <- Embeddings(obj, "wnn.umap")
cells <- data.frame(barcode = colnames(obj), seurat_clusters = as.character(obj$seurat_clusters),
                    rna_weight = as.numeric(rw), adt_weight = as.numeric(aw),
                    wnnUMAP_1 = emb[, 1], wnnUMAP_2 = emb[, 2])
for (cc in intersect(c("condition", "replicate", "sample", "cell_type"), colnames(obj[[]])))
  cells[[cc]] <- obj[[]][[cc]]
utils::write.csv(cells, ckpt(cfg, "wnn_cells.csv"), row.names = FALSE)

# deliverable: modality-weight summary (aggregate, weights bounded)
mw <- data.frame(
  modality = c("RNA", "ADT"),
  mean = round(c(mean(rw), mean(aw)), 4), median = round(c(median(rw), median(aw)), 4),
  min = round(c(min(rw), min(aw)), 4), max = round(c(max(rw), max(aw)), 4),
  sd = round(c(sd(rw), sd(aw)), 4), in_unit_interval = c(n_bad == 0, n_bad == 0))
utils::write.csv(mw, tabp(cfg, "modality_weights_summary.csv"), row.names = FALSE)

# ---- figure 5: joint UMAP + modality-weight distribution ----
p_umap <- ggplot(cells, aes(wnnUMAP_1, wnnUMAP_2, color = seurat_clusters)) +
  geom_point(size = 0.7, alpha = 0.85) + scale_color_manual(values = clpal(n_clusters)) +
  plot_theme() + guides(color = guide_legend(override.aes = list(size = 2))) +
  labs(color = "joint\ncluster", title = "WNN joint UMAP")
p_w <- ggplot(data.frame(w = rw), aes(w)) +
  geom_histogram(bins = 40, fill = "#D4A04A", color = "white") +
  geom_vline(xintercept = med_rna_w, linetype = "dashed", color = "#2C2A26") +
  xlim(0, 1) + plot_theme() + labs(x = "per-cell RNA modality weight (ADT = 1 - RNA)",
                                   y = "cells", title = "Modality weights in [0,1]")
save_fig(cfg, p_umap + p_w + plot_layout(widths = c(1.25, 1)), "figure_5_build_the_wnn.png", w = 11, h = 4.6)

saveRDS(obj, ckpt(cfg, "04_wnn.rds"))
write_facts_fragment(cfg, "facts_04.json", list(
  n_joint_clusters = n_clusters, median_rna_modality_weight = med_rna_w))
if (n_bad > 0) stop(sprintf("%d modality weights fell outside [0,1] — refusing to proceed", n_bad))
log_msg("Wrote wnn_cells.csv, modality_weights_summary.csv, figure_5; saved WNN object.")
