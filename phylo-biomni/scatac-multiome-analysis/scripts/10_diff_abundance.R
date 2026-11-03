#!/usr/bin/env Rscript
# ============================================================================
# STAGE 10 — (JOINT route) replicate-aware differential abundance + DE/DA.
#  (1) Milo neighborhood differential ABUNDANCE on the WNN joint embedding
#      (spatial-FDR; requires >=2 biological replicates per condition).
#  (2) cross-modality pseudobulk RNA differential EXPRESSION per cell type.
#  (3) per-cell-type ATAC differential ACCESSIBILITY (preserves the standard
#      top_DA_peaks_per_celltype.csv artifact in the joint route).
#
# Each block STOPS/SKIPS with an explicit recorded reason when replicates are
# insufficient — it never fabricates an FDR from technical pseudoreplication.
# ============================================================================
suppressPackageStartupMessages({ library(Signac); library(Seurat); library(ggplot2) })
source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(FALSE), value = TRUE))), "_common.R"))
cfg <- load_config(); set.seed(cfg$project$seed)

st <- readRDS(ckpt(cfg, "08_joint.rds")); obj <- st$obj
da_cfg <- cfg$diffabund %||% list()
rep_col  <- da_cfg$replicate_col %||% "replicate"
cond_col <- da_cfg$condition_col %||% "condition"
min_rep  <- da_cfg$min_replicates_per_group %||% 2
status <- list()

rs <- replicates_sufficient(obj@meta.data, rep_col, cond_col, min_rep)
reps_ok <- isTRUE(rs$sufficient)
status$replicate_design <- rs

# ---- (1) Milo differential abundance ---------------------------------------
if (reps_ok) {
  inference_packages <- c("miloR", "SingleCellExperiment")
  missing_inference <- inference_packages[
    !vapply(inference_packages, requireNamespace, logical(1), quietly = TRUE)
  ]
  if (length(missing_inference)) {
    stop(
      "replicate-aware inference is selected but required dependencies are missing: ",
      paste(missing_inference, collapse = ", ")
    )
  }
  milo_res <- tryCatch({
    suppressPackageStartupMessages({ library(miloR); library(SingleCellExperiment) })
    je <- cbind(Embeddings(obj, "pca")[, st$rna_dims, drop = FALSE],
                Embeddings(obj, "lsi")[, st$atac_dims, drop = FALSE])
    colnames(je) <- paste0("joint_", seq_len(ncol(je)))
    obj[["joint"]] <- CreateDimReducObject(embeddings = je, key = "joint_", assay = "RNA")
    sce <- as.SingleCellExperiment(obj, assay = "RNA")
    reducedDim(sce, "JOINT") <- je
    milo <- Milo(sce)
    d <- ncol(je); k <- da_cfg$k %||% 30
    milo <- buildGraph(milo, k = k, d = d, reduced.dim = "JOINT")
    milo <- makeNhoods(milo, prop = da_cfg$prop %||% 0.1, k = k, d = d, refined = TRUE, reduced_dims = "JOINT")
    milo <- countCells(milo, meta.data = as.data.frame(colData(milo)), sample = rep_col)
    design <- unique(as.data.frame(colData(milo))[, c(rep_col, cond_col)])
    rownames(design) <- design[[rep_col]]
    design[[cond_col]] <- factor(design[[cond_col]])
    milo <- calcNhoodDistance(milo, d = d, reduced.dim = "JOINT")
    res <- testNhoods(milo, design = as.formula(paste("~", cond_col)), design.df = design)
    res <- annotateNhoods(milo, res, coldata_col = "cell_type")
    write.csv(res[order(res$SpatialFDR), ], tabp(cfg, "milo_da_neighborhoods.csv"), row.names = FALSE)
    n_sig <- sum(res$SpatialFDR < (da_cfg$fdr %||% 0.1), na.rm = TRUE)
    p <- tryCatch(plotDAbeeswarm(res, group.by = "cell_type") + plot_theme(), error = function(e) NULL)
    if (!is.null(p)) save_fig(cfg, p, "19_milo_da_beeswarm", 7.5, 5)
    list(status = "ran", n_neighborhoods = nrow(res), n_sig_spatialFDR = n_sig,
         fdr_threshold = da_cfg$fdr %||% 0.1)
  }, error = function(e) list(status = "failed", reason = conditionMessage(e)))
  status$milo <- milo_res
} else {
  status$milo <- list(status = "skipped",
    reason = "insufficient biological replicates (need >=2 per condition); no fabricated FDR.")
}
log_msg("Milo:", status$milo$status)

# ---- (2) cross-modality pseudobulk RNA DE per cell type --------------------
if (reps_ok) {
  de <- tryCatch({
    DefaultAssay(obj) <- "RNA"
    md <- obj@meta.data
    cts <- levels(droplevels(factor(md$cell_type)))
    counts <- GetAssayData(obj, assay = "RNA", layer = "counts")
    conds <- levels(factor(md[[cond_col]]))[1:2]
    out <- list()
    for (ct in cts) {
      cells_ct <- rownames(md)[md$cell_type == ct]
      grp <- interaction(md[cells_ct, rep_col], md[cells_ct, cond_col], drop = TRUE)
      if (nlevels(grp) < 4) next
      pb <- sapply(levels(grp), function(g) Matrix::rowSums(counts[, cells_ct[grp == g], drop = FALSE]))
      cpm <- log2(sweep(pb, 2, colSums(pb) + 1, "/") * 1e6 + 1)
      cond_of <- sub("^.*\\.", "", colnames(pb))
      a <- cpm[, cond_of == conds[2], drop = FALSE]; b <- cpm[, cond_of == conds[1], drop = FALSE]
      if (ncol(a) < min_rep || ncol(b) < min_rep) next
      lfc <- rowMeans(a) - rowMeans(b)
      pv <- apply(cpm, 1, function(r) tryCatch(t.test(r[cond_of == conds[2]], r[cond_of == conds[1]])$p.value, error = function(e) NA))
      tb <- data.frame(cell_type = ct, gene = rownames(cpm), log2FC = round(lfc, 3),
                       p_value = pv, FDR = p.adjust(pv, "BH"))
      out[[ct]] <- tb[order(tb$FDR), ][1:min(50, nrow(tb)), ]
    }
    if (length(out)) {
      allde <- do.call(rbind, out)
      write.csv(allde, tabp(cfg, "pseudobulk_rna_de_by_celltype.csv"), row.names = FALSE)
      list(status = "ran", contrast = paste(conds[2], "vs", conds[1]),
           n_celltypes = length(out), note = "Pseudobulk across biological replicates; low replicate counts limit power.")
    } else list(status = "skipped", reason = "no cell type had >=2 pseudobulk replicates per condition.")
  }, error = function(e) list(status = "failed", reason = conditionMessage(e)))
  status$pseudobulk_de <- de
} else {
  status$pseudobulk_de <- list(status = "skipped", reason = "insufficient biological replicates.")
}
log_msg("Pseudobulk DE:", status$pseudobulk_de$status)

# ---- (3) per-cell-type ATAC differential accessibility (standard artifact) --
atac_da <- tryCatch({
  DefaultAssay(obj) <- "recalled"
  Idents(obj) <- "cell_type"
  obj <- FindTopFeatures(obj, min.cutoff = cfg$diffaccess$var_features_cutoff %||% "q25")
  vf <- VariableFeatures(obj)
  da <- FindAllMarkers(obj, features = vf, only.pos = isTRUE(cfg$diffaccess$only_pos %||% TRUE),
                       min.pct = cfg$diffaccess$min_pct %||% 0.15, test.use = "wilcox",
                       max.cells.per.ident = cfg$diffaccess$max_cells_per_ident %||% 200)
  if (nrow(da)) {
    cf <- ClosestFeature(obj, regions = da$gene)
    da$nearest_gene <- cf$gene_name[match(da$gene, cf$query_region)]
    write.csv(da, tabp(cfg, "top_DA_peaks_per_celltype.csv"), row.names = FALSE)
  }
  list(status = "ran", n_da_peaks = nrow(da))
}, error = function(e) list(status = "failed", reason = conditionMessage(e)))
status$atac_da <- atac_da
log_msg("ATAC DA:", status$atac_da$status)

writeLines(jsonlite::toJSON(status, auto_unbox = TRUE, pretty = TRUE, null = "null"),
           tabp(cfg, "diffabund_status.json"))
st$diffabund_status <- status
saveRDS(st, ckpt(cfg, "10_diffabund.rds"))
log_msg("STAGE 10 done -> checkpoints/10_diffabund.rds"); cat("STAGE10_OK\n")
