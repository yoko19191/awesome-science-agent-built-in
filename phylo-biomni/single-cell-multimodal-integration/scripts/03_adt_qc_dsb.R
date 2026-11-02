#!/usr/bin/env Rscript
# ============================================================================
# 03_adt_qc_dsb.R — denoise antibody-derived tags. Preferred path: dsb using
# empty droplets (from raw_feature_bc_matrix.h5) + named isotype controls.
# Explicit CLR remains available, but a requested DSB run fails when no validated
# background is available. Writes tables/adt_normalization_status.json and
# figure_4 with the measured post-normalization isotype median when evaluable.
# ============================================================================
here <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]))
suppressMessages({
  source(file.path(here, "_common.R"))
  source(file.path(here, "marker_programs.R"))
  library(Seurat)
  library(ggplot2)
})
source(file.path(here, "adt_contract.R"))

cfg <- load_config(); ensure_dirs(cfg)
DEFAULT_BACKGROUND_MIN_DROPLETS <- 10L
obj  <- readRDS(ckpt(cfg, "02_qc.rds"))
im   <- readRDS(ckpt(cfg, "01_ingest_meta.rds"))
ig   <- im$ingest_dir
isotypes <- intersect(im$isotypes, rownames(obj[["ADT"]]))

cell_counts <- as.matrix(GetAssayData(obj, assay = "ADT", layer = "counts"))
requested <- tolower(cfg$adt$normalization %||% "dsb")

# The ingest boundary preselects captured low-depth background droplets after excluding all
# filtered/cell-called and metadata-declared cell barcodes. Revalidate that semantic separation here.
empty_mat <- NULL; n_empty <- 0L
raw_mtx <- file.path(ig, "adt_raw.mtx.gz")
if (file.exists(raw_mtx)) {
  raw_adt <- as.matrix(read_mtx_gz(raw_mtx, file.path(ig, "adt_raw_features.tsv.gz"),
                                   file.path(ig, "adt_raw_barcodes.tsv.gz")))
  rownames(raw_adt) <- canonical_adt_feature_names(
    rownames(raw_adt), "raw empty-droplet ADT panel"
  )
  missing_raw <- setdiff(rownames(cell_counts), rownames(raw_adt))
  if (length(missing_raw)) {
    stop(sprintf("raw ADT panel is missing filtered feature(s): %s",
                 paste(head(missing_raw, 5), collapse = ", ")))
  }
  raw_adt <- raw_adt[rownames(cell_counts), , drop = FALSE]
  background_bc <- colnames(raw_adt)
  cell_overlap <- intersect(background_bc, im$filtered_barcodes %||% character(0))
  if (length(cell_overlap)) {
    stop(sprintf("validated DSB background overlaps %d filtered/cell-called barcode(s)",
                 length(cell_overlap)))
  }
  background_depth <- Matrix::colSums(raw_adt)
  if (any(background_depth <= 0)) {
    stop("validated DSB background contains a barcode without ADT capture")
  }
  empty_mat <- raw_adt
  n_empty <- ncol(raw_adt)
}

method <- "clr"; reason <- ""; use_iso <- FALSE
norm <- NULL
minimum_background <- as.integer(
  cfg$adt$dsb_background$min_droplets %||% DEFAULT_BACKGROUND_MIN_DROPLETS
)
can_dsb <- requested == "dsb" && !is.null(empty_mat) && n_empty >= minimum_background
if (can_dsb) {
  use_iso <- length(isotypes) > 0
  norm <- tryCatch(
    dsb::DSBNormalizeProtein(
      cell_protein_matrix = cell_counts, empty_drop_matrix = empty_mat,
      denoise.counts = use_iso, use.isotype.control = use_iso,
      isotype.control.name.vec = if (use_iso) isotypes else NULL),
    error = function(e) { reason <<- paste("dsb error:", conditionMessage(e)); NULL })
  if (!is.null(norm)) { method <- if (use_iso) "dsb" else "dsb_no_isotype"
    reason <- if (use_iso) sprintf("dsb with %d empty droplets and %d isotype control(s)", n_empty, length(isotypes))
              else sprintf("dsb with %d empty droplets, ambient-only (no isotype controls named/present)", n_empty) }
} else if (requested == "dsb") {
  stop(if (is.null(empty_mat))
    "DSB normalization requested but no validated background-droplet matrix is available"
  else sprintf("DSB normalization requested but only %d validated background droplets are available; require >=%d",
               n_empty, minimum_background))
}
if (requested == "dsb" && is.null(norm)) {
  stop(sprintf("DSB normalization failed; no CLR substitution was made: %s", reason))
}
if (is.null(norm)) {
  method <- "clr"; reason <- "CLR requested in config"
  tmp <- NormalizeData(obj, assay = "ADT", normalization.method = "CLR", margin = 2, verbose = FALSE)
  norm <- as.matrix(GetAssayData(tmp, assay = "ADT", layer = "data"))
}

norm <- norm[rownames(cell_counts), colnames(cell_counts), drop = FALSE]
obj <- SetAssayData(obj, assay = "ADT", layer = "data", new.data = as(norm, "CsparseMatrix"))

iso_bg_median <- if (length(isotypes) > 0) round(as.numeric(median(norm[isotypes, , drop = FALSE])), 4) else NA
iso_bg_interpretation <- if (length(isotypes) > 0)
  "measured_no_universal_zero_threshold" else "not_evaluable_no_isotype_controls"
log_msg(sprintf("ADT normalization: %s (%s); isotype background median = %s",
                method, reason, ifelse(is.na(iso_bg_median), "NA", iso_bg_median)))

# ---- figure 4: isotype vs a true marker, before (log1p counts) vs after ----
marker <- surface_marker_matches(
  rownames(norm), c("CD3", "CD4", "CD8", "CD19", "CD14")
)[1]
sel <- c(isotypes[1], marker); sel <- sel[!is.na(sel)]
mk_df <- function(mat, stage) do.call(rbind, lapply(sel, function(f)
  data.frame(feature = f, kind = ifelse(f %in% isotypes, "isotype", "marker"),
             stage = stage, value = as.numeric(mat[f, ]))))
before <- mk_df(log1p(cell_counts), "before (log1p counts)")
after  <- mk_df(norm, paste0("after (", method, ")"))
p4 <- ggplot(rbind(before, after), aes(x = feature, y = value, fill = kind)) +
  geom_violin(scale = "width", alpha = 0.85) +
  facet_wrap(~stage, scales = "free_y") +
  scale_fill_manual(values = c(isotype = "#8A8378", marker = "#0072B2")) +
  plot_theme() + labs(x = NULL, y = "ADT signal", fill = NULL)
save_fig(cfg, p4, "figure_4_denoise_antibody_derived.png", w = 8, h = 4.2)

write_status(cfg, "adt_normalization_status.json", list(
  method = method, reason = reason, use_isotype_control = use_iso,
  n_empty_droplets = n_empty, isotype_controls = as.list(isotypes),
  background_selection = im$dsb_background_selection,
  isotype_background_median = iso_bg_median,
  isotype_background_interpretation = iso_bg_interpretation,
  figure_marker = if (length(marker) && !is.na(marker)) marker else NULL,
  normalized_nonzero_values = sum(is.finite(norm) & abs(norm) > .Machine$double.eps),
  normalized_absolute_sum = sum(abs(norm[is.finite(norm)]))))
# Export the explicit cells-x-features contract used by the Python H5MU builder.
write_normalized_adt_csv(norm, ckpt(cfg, "adt_normalized.csv"))
write_adt_counts_csv(cell_counts, ckpt(cfg, "adt_counts.csv"))
saveRDS(obj, ckpt(cfg, "03_adt.rds"))
log_msg("Wrote adt_normalization_status.json + figure_4; saved denoised ADT.")
