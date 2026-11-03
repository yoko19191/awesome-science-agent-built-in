#!/usr/bin/env Rscript
# DRUG-seq well-pseudobulk differential expression with DESeq2.
#
# Wells (not cells) are the experimental units. Each treated well-group is contrasted against the
# vehicle rows selected by is_control. A mandatory vehicle-vs-vehicle null contrast is always
# emitted; it must return approximately zero DEGs or the normalization/design is suspect.
#
# Usage:
#   Rscript de_deseq2.R --counts counts.tsv --platemap platemap.csv --pass-wells pass_wells.txt \
#       --outdir /mnt/results [--covariate none|edge] [--padj 0.05] [--lfc 1.0] [--min-count 10]
#
# Writes (under <outdir>/tables):
#   de_all_contrasts.csv   contrast, gene_id, baseMean, log2FoldChange, lfcSE, pvalue, padj, is_sig
#   de_summary.csv         contrast, n_sig, n_up, n_down, n_low_expr_hits
# Deterministic: DESeq2 is deterministic; the vehicle-null split is by sorted well_id.

suppressWarnings(suppressMessages({
  ok <- requireNamespace("DESeq2", quietly = TRUE)
}))
args <- commandArgs(trailingOnly = TRUE)
get_arg <- function(flag, default = NULL) {
  i <- match(flag, args); if (is.na(i)) return(default); args[[i + 1]]
}
counts_path <- get_arg("--counts")
platemap_path <- get_arg("--platemap")
pass_path <- get_arg("--pass-wells")
outdir <- get_arg("--outdir")
covariate <- get_arg("--covariate", "none")
padj_thr <- as.numeric(get_arg("--padj", "0.05"))
lfc_thr <- as.numeric(get_arg("--lfc", "1.0"))
min_count <- as.numeric(get_arg("--min-count", "10"))

if (is.null(counts_path) || is.null(platemap_path) || is.null(outdir)) {
  stop("required: --counts --platemap --outdir")
}
if (!ok) {
  # Documented fallback path: fail loudly with a not_computable signal rather than a wrong result.
  stop("DESeq2 is not installed. Install it, or use the edgeR/limma-voom fallback documented in references/. This run is not_computable for the DESeq2 engine.")
}
suppressWarnings(suppressMessages(library(DESeq2)))

tables <- file.path(outdir, "tables")
dir.create(tables, recursive = TRUE, showWarnings = FALSE)

# Persist runtime-component versions with identity separate from version capture. Query the DESeq2
# package version where it is used; never substitute the R interpreter version or an installed flag.
software_versions <- data.frame(
  component = c("R", "DESeq2"),
  version = c(as.character(getRversion()), as.character(packageVersion("DESeq2"))),
  installed = c(TRUE, TRUE),
  stringsAsFactors = FALSE
)
write.csv(software_versions, file.path(tables, "software_versions.csv"), row.names = FALSE)

cts <- as.matrix(read.delim(counts_path, row.names = 1, check.names = FALSE))
meta <- read.csv(platemap_path, stringsAsFactors = FALSE)
required_meta <- c("well_id", "compound", "is_control")
missing_meta <- setdiff(required_meta, colnames(meta))
if (length(missing_meta)) {
  stop("platemap is missing required control columns: ", paste(missing_meta, collapse = ", "))
}
control_values <- tolower(trimws(as.character(meta$is_control)))
control_true <- c("1", "1.0", "true", "yes", "y", "vehicle", "vehicle_control", "control",
                  "negative", "negative_control", "negative control", "negcon")
control_false <- c("0", "0.0", "false", "no", "n", "treatment", "test", "sample")
unsupported <- is.na(control_values) | !control_values %in% c(control_true, control_false)
if (any(unsupported)) {
  stop("is_control contains unsupported semantic values: ",
       paste(sort(unique(control_values[unsupported])), collapse = ", "))
}
meta$analysis_role <- ifelse(control_values %in% control_true, "vehicle", "treatment")
control_identities <- trimws(as.character(meta$compound[meta$analysis_role == "vehicle"]))
if (!length(control_identities) || any(is.na(control_identities)) || any(control_identities == "")) {
  stop("every vehicle-control row must have a nonempty compound identity")
}
if (length(unique(tolower(control_identities))) != 1) {
  stop("platemap contains mixed vehicle-control identities; define one global matched vehicle")
}
vehicle_identity <- control_identities[[1]]
rownames(meta) <- meta$well_id
if (!is.null(pass_path) && file.exists(pass_path)) {
  keep <- readLines(pass_path)
  keep <- keep[keep %in% colnames(cts) & keep %in% rownames(meta)]
} else {
  keep <- intersect(colnames(cts), rownames(meta))
}
cts <- cts[, keep, drop = FALSE]
meta <- meta[keep, , drop = FALSE]
storage.mode(cts) <- "integer"

# Filter very low-count genes (stabilises dispersion; speeds the fit).
cts <- cts[rowSums(cts) >= min_count, , drop = FALSE]

treatment_key <- paste(meta$compound, meta$dose_uM, sep = "\x1f")
treatment_keys <- sort(unique(treatment_key[meta$analysis_role == "treatment"]))
condition_map <- setNames(sprintf("treatment_%04d", seq_along(treatment_keys)), treatment_keys)
meta$condition <- ifelse(
  meta$analysis_role == "vehicle", "__vehicle__", unname(condition_map[treatment_key])
)
meta$condition <- relevel(factor(meta$condition), ref = "__vehicle__")
edge_cols <- range(meta$col)
meta$is_edge <- factor(ifelse(meta$col %in% edge_cols, "edge", "interior"))
if (length(unique(meta$plate_id)) > 1) meta$plate_id <- factor(meta$plate_id)

# Design: optionally add a positional (edge) covariate; add plate when >1 plate.
terms <- c()
if (length(unique(meta$plate_id)) > 1) terms <- c(terms, "plate_id")
if (covariate == "edge" && nlevels(meta$is_edge) > 1) terms <- c(terms, "is_edge")
terms <- c(terms, "condition")
design_formula <- as.formula(paste("~", paste(terms, collapse = " + ")))

dds <- DESeqDataSetFromMatrix(countData = cts, colData = meta, design = design_formula)
dds <- DESeq(dds, quiet = TRUE)
base_mean_floor <- as.numeric(quantile(rowMeans(counts(dds, normalized = TRUE)), 0.10))

all_rows <- list()
summ <- list()
levs <- setdiff(levels(meta$condition), "__vehicle__")
cond_map <- unique(meta[, c("condition", "compound", "dose_uM")])
for (lv in levs) {
  res <- as.data.frame(results(dds, contrast = c("condition", lv, "__vehicle__")))
  res$gene_id <- rownames(res)
  mrow <- cond_map[cond_map$condition == lv, ][1, ]
  res$contrast <- paste0(as.character(mrow$compound), "_", as.character(mrow$dose_uM))
  res$contrast_family <- "treatment_vs_vehicle"
  res$control_identity <- vehicle_identity
  res$compound <- as.character(mrow$compound)
  res$dose_uM <- as.numeric(mrow$dose_uM)
  res$is_sig <- !is.na(res$padj) & res$padj < padj_thr & abs(res$log2FoldChange) > lfc_thr
  res$low_expr_hit <- res$is_sig & (res$baseMean < base_mean_floor)
  all_rows[[lv]] <- res[, c("contrast", "contrast_family", "control_identity", "compound",
                            "dose_uM", "gene_id", "baseMean",
                            "log2FoldChange", "lfcSE", "pvalue", "padj", "is_sig", "low_expr_hit")]
  summ[[lv]] <- data.frame(
    contrast = res$contrast[[1]],
    contrast_family = "treatment_vs_vehicle",
    control_identity = vehicle_identity,
    n_sig = sum(res$is_sig),
    n_up = sum(res$is_sig & res$log2FoldChange > 0),
    n_down = sum(res$is_sig & res$log2FoldChange < 0),
    n_low_expr_hits = sum(res$low_expr_hit)
  )
}

# Mandatory vehicle-vs-vehicle null contrast: split flagged vehicle wells by sorted well_id.
vehicle_wells <- sort(rownames(meta)[meta$analysis_role == "vehicle"])
if (length(vehicle_wells) < 4) {
  stop("vehicle null requires at least four QC-passing vehicle wells; null anchor is not computable")
}
md <- droplevels(meta[vehicle_wells, , drop = FALSE])
null_covariates <- setdiff(terms, "condition")
for (term in null_covariates) {
  if (length(unique(md[[term]])) < 2) {
    stop("vehicle null cannot fit the same ", term,
         " adjustment as treatment contrasts; null anchor is not computable")
  }
}
# Balance the deterministic g1/g2 split within the same plate/edge strata used
# for treatment contrasts. Alternating the first label across ordered strata avoids
# assigning every singleton stratum to g1; the rank check below rejects any residual
# confounding rather than silently fitting a different null model.
strata <- if (length(null_covariates)) {
  do.call(
    interaction,
    c(unname(as.list(md[, null_covariates, drop = FALSE])), list(drop = TRUE, lex.order = TRUE))
  )
} else {
  factor(rep("all", nrow(md)))
}
grp <- character(nrow(md))
stratum_names <- sort(unique(as.character(strata)))
for (stratum_index in seq_along(stratum_names)) {
  indices <- which(as.character(strata) == stratum_names[[stratum_index]])
  ordered <- indices[order(rownames(md)[indices])]
  first <- if (stratum_index %% 2 == 1) c("g1", "g2") else c("g2", "g1")
  grp[ordered] <- rep(first, length.out = length(ordered))
}
if (!setequal(unique(grp), c("g1", "g2"))) {
  stop("vehicle null split did not produce both g1 and g2; null anchor is not computable")
}
md$nullgroup <- factor(grp, levels = c("g1", "g2"))
ctsd <- cts[, vehicle_wells, drop = FALSE]
null_terms <- c(null_covariates, "nullgroup")
null_design_formula <- as.formula(paste("~", paste(null_terms, collapse = " + ")))
null_design_matrix <- model.matrix(null_design_formula, data = md)
if (qr(null_design_matrix)$rank < ncol(null_design_matrix)) {
  stop("vehicle null design is rank deficient with the treatment-model adjustment terms; ",
       "null anchor is not computable")
}
dds0 <- DESeqDataSetFromMatrix(ctsd, md, null_design_formula)
dds0 <- DESeq(dds0, quiet = TRUE)
res0 <- as.data.frame(results(dds0, contrast = c("nullgroup", "g2", "g1")))
res0$gene_id <- rownames(res0)
res0$contrast <- "NULL_VEHICLE_vs_VEHICLE"
res0$contrast_family <- "vehicle_vs_vehicle_null"
res0$control_identity <- vehicle_identity
res0$compound <- vehicle_identity
res0$dose_uM <- NA_real_
res0$is_sig <- !is.na(res0$padj) & res0$padj < padj_thr & abs(res0$log2FoldChange) > lfc_thr
res0$low_expr_hit <- res0$is_sig & (res0$baseMean < base_mean_floor)
all_rows[["NULL"]] <- res0[, c("contrast", "contrast_family", "control_identity", "compound",
                               "dose_uM", "gene_id", "baseMean",
                               "log2FoldChange", "lfcSE", "pvalue", "padj", "is_sig", "low_expr_hit")]
summ[["NULL"]] <- data.frame(
  contrast = "NULL_VEHICLE_vs_VEHICLE",
  contrast_family = "vehicle_vs_vehicle_null",
  control_identity = vehicle_identity,
  n_sig = sum(res0$is_sig),
  n_up = sum(res0$is_sig & res0$log2FoldChange > 0),
  n_down = sum(res0$is_sig & res0$log2FoldChange < 0),
  n_low_expr_hits = sum(res0$low_expr_hit)
)

model_specification <- data.frame(
  contrast_family = c("treatment_vs_vehicle", "vehicle_vs_vehicle_null"),
  control_identity = c(vehicle_identity, vehicle_identity),
  formula = c(deparse(design_formula), deparse(null_design_formula)),
  shared_adjustment_terms = paste(setdiff(terms, "condition"), collapse = ";"),
  stringsAsFactors = FALSE
)
write.csv(model_specification, file.path(tables, "de_model_specification.csv"), row.names = FALSE)

de <- do.call(rbind, all_rows)
de_summary <- do.call(rbind, summ)
write.csv(de, file.path(tables, "de_all_contrasts.csv"), row.names = FALSE)
write.csv(de_summary, file.path(tables, "de_summary.csv"), row.names = FALSE)
cat(sprintf("de_deseq2: %d contrasts, %d genes tested, base_mean_floor=%.3f, covariate=%s\n",
            length(levs) + 1, nrow(cts), base_mean_floor, covariate))
