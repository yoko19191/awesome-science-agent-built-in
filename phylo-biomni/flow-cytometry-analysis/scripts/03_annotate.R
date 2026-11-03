#!/usr/bin/env Rscript
# =====================================================================================
# 03_annotate.R  --  Annotate metaclusters to cell types (semi-automated) + merge.
#
# Strategy:
#   1) Compute per-cluster median marker expression; z-score lineage markers -> heatmap.
#   2) Score clusters against reference marker sets (CellMarker2 data lake, if available) to
#      PROPOSE cell-type labels. This is semi-automated: the analyst confirms/edits.
#   3) mergeClusters() to consolidate redundant clusters into named populations.
#   4) Emit an editable annotation template (CSV) so labels are explicit and reproducible.
#
# Two-tier note: finer analysis requires measured-marker evidence and explicit review;
# a chosen resolution alone does not establish biological identity.
#
# Usage:
#   Rscript 03_annotate.R --sce <outdir>/sce_clustered.rds --outdir <outdir> \
#     --annotation <optional annotation.csv: cluster,population> \
#     --cellmarker <optional CellMarker2 tsv/csv> --tissue "bone marrow" --seed 1234
# Output: <outdir>/sce_annotated.rds, cluster_medians.rds, annotation_template.csv, marker heatmap.
# =====================================================================================

suppressPackageStartupMessages({
  library(optparse); library(CATALYST); library(SingleCellExperiment)
  library(ComplexHeatmap); library(matrixStats); library(grid)
})

script_arg <- commandArgs(trailingOnly = FALSE)
script_file <- sub("^--file=", "", script_arg[grepl("^--file=", script_arg)])
script_dir <- if (length(script_file)) dirname(normalizePath(script_file[[1]])) else getwd()
source(file.path(script_dir, "annotation_engine.R"))
source(file.path(script_dir, "review_contract.R"))

opt <- parse_args(OptionParser(option_list = list(
  make_option("--sce", type = "character"),
  make_option("--outdir", type = "character", default = "/mnt/results/cyto_run"),
  make_option("--annotation", type = "character", default = NA),  # user-provided cluster->population map
  make_option("--annotation-review", type = "character", default = "propose", dest = "annotation_review",
              help = "propose|apply|auto. apply requires a complete --annotation map; auto uses generic cluster IDs and remains REVIEW."),
  make_option("--cellmarker", type = "character", default = NA),  # CellMarker2 reference file
  make_option("--tissue", type = "character", default = NA),
  make_option("--review-receipt", type = "character", default = NA_character_, dest = "review_receipt",
              help = "JSON receipt binding an external user's decision to this proposal and annotation map."),
  make_option("--seed", type = "integer", default = 1234L)
)))
review_preserve_auto_proposal(opt$annotation_review, "annotation", opt$outdir)
review_reject_output_as_input(opt$annotation, file.path(opt$outdir, "annotation_applied.csv"))
set.seed(opt$seed)
figdir <- file.path(opt$outdir, "figures"); dir.create(figdir, recursive = TRUE, showWarnings = FALSE)
LOG <- file.path(opt$outdir, "qc_transform_log.txt")
logmsg <- function(...) { m <- sprintf(...); cat(m, "\n"); cat(m, "\n", file = LOG, append = TRUE) }
logmsg("=== 03_annotate.R  |  seed=%d ===", opt$seed)

annotation_input_fingerprint <- review_fingerprint_object(list(
  sce_sha256 = review_sha256_file(opt$sce),
  cellmarker_sha256 = if (!is.na(opt$cellmarker) && file.exists(opt$cellmarker))
    review_sha256_file(opt$cellmarker) else NA_character_,
  tissue = opt$tissue, seed = opt$seed))
sce <- readRDS(opt$sce)
chosen_k <- S4Vectors::metadata(sce)$chosen_k
cl <- CATALYST::cluster_ids(sce, chosen_k)
tm <- rownames(sce)[SummarizedExperiment::rowData(sce)$marker_class == "type"]
ex <- SummarizedExperiment::assay(sce, "exprs")

# ------------------------------------------------------------------ per-cluster medians + z
clusters <- levels(cl)
med <- sapply(clusters, function(cc) matrixStats::rowMedians(ex[tm, cl == cc, drop = FALSE]))
rownames(med) <- tm; colnames(med) <- clusters
z <- t(scale(t(med)))  # z-score each marker across clusters
z[!is.finite(z)] <- 0
saveRDS(list(median = med, zscore = z, chosen_k = chosen_k, clusters = clusters),
        file.path(opt$outdir, "cluster_medians.rds"))

# ------------------------------------------------------------------ evidence-gated proposal
cellmarker_sets <- NULL
if (!is.na(opt$cellmarker) && file.exists(opt$cellmarker)) {
  cm <- tryCatch({
    if (grepl("\\.csv$", opt$cellmarker, ignore.case = TRUE))
      read.csv(opt$cellmarker, stringsAsFactors = FALSE)
    else
      read.delim(opt$cellmarker, stringsAsFactors = FALSE)
  }, error = function(e) NULL)
  if (!is.null(cm)) {
    # expect columns: cell_name/cell_type and marker/Symbol; filter by tissue if given
    ctcol <- intersect(c("cell_name", "cell_type", "celltype"), tolower(names(cm)))
    mkcol <- intersect(c("marker", "symbol", "gene"), tolower(names(cm)))
    names(cm) <- tolower(names(cm))
    if (length(ctcol) && length(mkcol)) {
      if (!is.na(opt$tissue) && "tissue_type" %in% names(cm))
        cm <- cm[grepl(opt$tissue, cm$tissue_type, ignore.case = TRUE), , drop = FALSE]
      cellmarker_sets <- split(as.character(cm[[mkcol[1]]]), as.character(cm[[ctcol[1]]]))
    }
  }
} else {
  logmsg("No CellMarker2 file supplied; conservative measured-marker signatures will be used.")
}
proposal_evidence <- annotation_propose(z, tm, cellmarker_sets)
logmsg("Evidence-gated annotation retained %d supported proposals; %d clusters remain unresolved.",
       sum(proposal_evidence$proposal_status == "SUPPORTED"),
       sum(proposal_evidence$proposal_status != "SUPPORTED"))

# top-3 markers per cluster as an annotation aid
top_markers <- sapply(clusters, function(cc) paste(names(sort(z[, cc], decreasing = TRUE))[1:3], collapse = ", "))
event_count <- as.integer(table(factor(cl, levels = clusters)))
event_pct <- 100 * event_count / length(cl)
sample_ids <- if ("sample_id" %in% colnames(SummarizedExperiment::colData(sce)))
  as.character(sce$sample_id) else rep("sample1", ncol(sce))
sample_coverage <- vapply(clusters, function(cc)
  length(unique(sample_ids[cl == cc])) / length(unique(sample_ids)), numeric(1))

# ------------------------------------------------------------------ annotation map
if (!opt$annotation_review %in% c("propose", "apply", "auto"))
  stop("--annotation-review must be propose, apply, or auto.")
proposal_table <- merge(proposal_evidence, data.frame(cluster = clusters, top_markers = top_markers,
                             event_count = event_count, event_pct = event_pct,
                             sample_coverage = sample_coverage,
                             population = "", merge_decision = "", decision_reason = "",
                             stringsAsFactors = FALSE), by = "cluster", sort = FALSE)
proposal_table <- proposal_table[match(clusters, proposal_table$cluster), , drop = FALSE]
tmpl <- file.path(opt$outdir, "annotation_template.csv")
proposal_record_path <- file.path(opt$outdir, "annotation_review_proposal.json")
if (opt$annotation_review != "apply") {
  write.csv(proposal_table, tmpl, row.names = FALSE)
  review_bind_proposal_input(tmpl, annotation_input_fingerprint)
  review_write_proposal("annotation", tmpl, opt$outdir,
                        list(supported_proposals = sum(proposal_table$proposal_status == "SUPPORTED"),
                             unresolved_proposals = sum(proposal_table$proposal_status != "SUPPORTED")))
}

if (opt$annotation_review == "apply") {
  if (is.na(opt$annotation) || !file.exists(opt$annotation))
    stop("--annotation-review apply requires --annotation <reviewed CSV>.")
  amap <- read.csv(opt$annotation, stringsAsFactors = FALSE)
  if (!all(c("cluster", "population") %in% names(amap))) stop("Annotation CSV requires cluster and population columns.")
  if (anyDuplicated(as.character(amap$cluster))) stop("Annotation CSV contains duplicate cluster rows.")
  extra_clusters <- setdiff(as.character(amap$cluster), clusters)
  if (length(extra_clusters)) stop(sprintf("Annotation map contains unknown clusters: %s.", paste(extra_clusters, collapse = ",")))
  supplied <- amap
  supplied <- supplied[, intersect(c("cluster", "population", "merge_decision", "decision_reason"), names(supplied)), drop = FALSE]
  amap <- merge(proposal_table[, setdiff(names(proposal_table), c("population", "merge_decision", "decision_reason"))],
                supplied, by = "cluster", all.x = TRUE, sort = FALSE)
  amap <- amap[match(clusters, amap$cluster), , drop = FALSE]
  for (column in c("population", "merge_decision", "decision_reason"))
    if (!column %in% names(amap)) amap[[column]] <- ""
  amap$population[is.na(amap$population) | !nzchar(trimws(amap$population))] <-
    paste0("cluster_", amap$cluster[is.na(amap$population) | !nzchar(trimws(amap$population))])
  amap$merge_decision <- tolower(trimws(ifelse(is.na(amap$merge_decision), "", amap$merge_decision)))
  amap$decision_reason <- trimws(ifelse(is.na(amap$decision_reason), "", amap$decision_reason))
  explicit_decisions <- all(amap$merge_decision %in% c("merge", "keep")) &&
    all(nzchar(amap$decision_reason)) && !anyNA(match(clusters, supplied$cluster))
  decision_consistent <- FALSE
  if (explicit_decisions) {
    groups <- split(seq_len(nrow(amap)), amap$population)
    decision_consistent <- all(vapply(groups, function(idx) {
      if (length(idx) > 1L) all(amap$merge_decision[idx] == "merge") else amap$merge_decision[idx] == "keep"
    }, logical(1)))
  }
  annotation_state <- if (explicit_decisions && decision_consistent) "PASS_reviewed" else "REVIEW_merge_pending"
  proposal_record <- review_read_proposal(proposal_record_path, "annotation")
  review_validate_proposal_input(proposal_record$proposal_path, annotation_input_fingerprint)
  receipt_validation <- review_validate_receipt(
    opt$review_receipt, "annotation", proposal_path = proposal_record$proposal_path,
    proposal_sha256 = proposal_record$proposal_sha256,
    decision_artifact = opt$annotation)
  if (!isTRUE(receipt_validation$valid)) stop(receipt_validation$reason)
  review_write_receipt_state("annotation", receipt_validation, opt$outdir)
  if (annotation_state == "PASS_reviewed" && !isTRUE(receipt_validation$valid))
    annotation_state <- "REVIEW_applied_unverified"
  logmsg("Applied annotation map: %s (%d/%d cluster decisions explicit; state=%s).",
         opt$annotation, sum(amap$merge_decision %in% c("merge", "keep")), length(clusters), annotation_state)
} else if (opt$annotation_review == "auto") {
  amap <- proposal_table
  amap$population <- paste0("cluster_", amap$cluster)
  annotation_state <- "REVIEW_merge_pending"
  logmsg("Annotation auto mode continues provisionally with generic cluster IDs; merge/keep review remains pending.")
} else {
  state <- data.frame(state = "INCOMPLETE_proposal", clusters = length(clusters), confirmed = 0L,
                      reason = "Fill population labels and rerun with --annotation-review apply --annotation <csv>",
                      stringsAsFactors = FALSE)
  write.csv(state, file.path(opt$outdir, "annotation_state.csv"), row.names = FALSE)
  logmsg("ANNOTATION CHECKPOINT: proposals written to %s; no CellMarker2 label was confirmed.", tmpl)
  quit(save = "no", status = 3L)
}
write.csv(data.frame(state = annotation_state, clusters = length(clusters),
                     confirmed = if (annotation_state == "PASS_reviewed") length(clusters) else 0L,
                     merge_decisions_complete = annotation_state == "PASS_reviewed",
                     reason = if (annotation_state == "PASS_reviewed") "Every cluster has a consistent merge/keep decision and reason." else
                       "Continue provisionally, then complete merge_decision and decision_reason for every cluster.",
                     stringsAsFactors = FALSE), file.path(opt$outdir, "annotation_state.csv"), row.names = FALSE)

# ------------------------------------------------------------------ merge to populations
# Build a merging table for CATALYST::mergeClusters (old_cluster -> new_population).
duplicate_population <- duplicated(amap$population) | duplicated(amap$population, fromLast = TRUE)
keep_separate <- duplicate_population & amap$merge_decision != "merge"
applied_population <- amap$population
applied_population[keep_separate] <- paste0(applied_population[keep_separate], "__cluster_", amap$cluster[keep_separate])
amap$applied_population <- applied_population
write.csv(amap, file.path(opt$outdir, "annotation_applied.csv"), row.names = FALSE)
merge_tbl <- data.frame(old_cluster = as.character(amap$cluster),
                        new_cluster = applied_population,
                        stringsAsFactors = FALSE)
sce <- CATALYST::mergeClusters(sce, k = chosen_k, table = merge_tbl, id = "annotation", overwrite = TRUE)
n_pop <- length(unique(CATALYST::cluster_ids(sce, "annotation")))
logmsg("Merged %d metaclusters -> %d annotated populations (id='annotation').", length(clusters), n_pop)

# ------------------------------------------------------------------ marker z-score heatmap
ann_ids <- CATALYST::cluster_ids(sce, "annotation")
pops <- levels(ann_ids)
medp <- sapply(pops, function(pp) matrixStats::rowMedians(ex[tm, ann_ids == pp, drop = FALSE]))
rownames(medp) <- tm; colnames(medp) <- pops
zp <- t(scale(t(medp)))
zp[!is.finite(zp)] <- 0
counts <- table(ann_ids)
ht <- ComplexHeatmap::Heatmap(
  zp, name = "z-score",
  column_title = "Median marker expression by annotated population",
  row_names_gp = grid::gpar(fontsize = 8), column_names_gp = grid::gpar(fontsize = 8),
  top_annotation = ComplexHeatmap::HeatmapAnnotation(
    n = ComplexHeatmap::anno_barplot(as.integer(counts[pops])), annotation_name_gp = grid::gpar(fontsize = 8)))
png(file.path(figdir, "fig_marker_heatmap.png"), width = 1600, height = 1400, res = 180)
ComplexHeatmap::draw(ht); dev.off()

S4Vectors::metadata(sce)$annotation_map <- amap
S4Vectors::metadata(sce)$annotation_state <- annotation_state
saveRDS(sce, file.path(opt$outdir, "sce_annotated.rds"))
logmsg("Saved: %s", file.path(opt$outdir, "sce_annotated.rds"))
logmsg("=== 03 complete ===")
