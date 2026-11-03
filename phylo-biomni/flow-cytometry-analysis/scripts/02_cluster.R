#!/usr/bin/env Rscript
# FlowSOM once, then evidence-rich, data-adaptive resolution assessment on the stored SOM codes.

suppressPackageStartupMessages({
  library(optparse); library(CATALYST); library(ConsensusClusterPlus)
  library(SingleCellExperiment); library(ggplot2); library(svglite)
})

script_arg <- commandArgs(trailingOnly = FALSE)
script_file <- sub("^--file=", "", script_arg[grepl("^--file=", script_arg)])
script_dir <- if (length(script_file)) dirname(normalizePath(script_file[[1]])) else getwd()
source(file.path(script_dir, "resolution_engine.R"))
source(file.path(script_dir, "review_contract.R"))

opt <- parse_args(OptionParser(option_list = list(
  make_option("--sce", type = "character"),
  make_option("--outdir", type = "character", default = "/mnt/results/cyto_run"),
  make_option("--xdim", type = "integer", default = 10L),
  make_option("--ydim", type = "integer", default = 10L),
  make_option("--maxK", type = "integer", default = NA_integer_),
  make_option("--k", type = "character", default = "auto"),
  make_option("--resolution-goal", type = "character", default = NA_character_, dest = "resolution_goal",
              help = "broad|standard|fine|rare; fresh runs default to standard, resumes inherit the stored goal."),
  make_option("--resolution-review", type = "character", default = "propose", dest = "resolution_review",
              help = "propose|apply|auto. apply requires an explicit --k; auto always remains REVIEW."),
  make_option("--resume-candidates", type = "character", default = NA_character_, dest = "resume_candidates",
              help = "Resume from sce_cluster_candidates.rds without retraining FlowSOM."),
  make_option("--decision-reason", type = "character", default = NA_character_, dest = "decision_reason",
              help = "Required when overriding a weak-structure resolution."),
  make_option("--review-receipt", type = "character", default = NA_character_, dest = "review_receipt",
              help = "JSON receipt binding an external user's decision to this exact candidate table and selected k."),
  make_option("--seed", type = "integer", default = 1234L)
)))

goal_was_explicit <- !is.na(opt$resolution_goal) && nzchar(trimws(opt$resolution_goal))
resumed <- !is.na(opt$resume_candidates) && nzchar(opt$resume_candidates)
if (!resumed && !goal_was_explicit) opt$resolution_goal <- "standard"
if (goal_was_explicit && !opt$resolution_goal %in% c("broad", "standard", "fine", "rare"))
  stop("--resolution-goal must be broad, standard, fine, or rare.")
if (!opt$resolution_review %in% c("propose", "apply", "auto"))
  stop("--resolution-review must be propose, apply, or auto.")
if (opt$resolution_review == "apply" && opt$k == "auto")
  stop("--resolution-review apply requires an exact --k selected after reviewing the candidates.")

dir.create(opt$outdir, recursive = TRUE, showWarnings = FALSE)
figdir <- file.path(opt$outdir, "figures"); dir.create(figdir, recursive = TRUE, showWarnings = FALSE)
LOG <- file.path(opt$outdir, "qc_transform_log.txt")
logmsg <- function(...) { m <- sprintf(...); cat(m, "\n"); cat(m, "\n", file = LOG, append = TRUE) }
parse_k <- function(value) {
  value <- sub("^meta", "", value)
  if (!grepl("^[0-9]+$", value)) stop("--k must be auto, an integer, or meta<integer>.")
  as.integer(value)
}

write_resolution_state <- function(k, n_clusters, state, apply, rationale,
                                   effective_max_k, selection_class, resumed = FALSE) {
  decision_reason <- if (is.na(opt$decision_reason)) "" else opt$decision_reason
  row <- data.frame(
    proposed_k = if (is.finite(k)) paste0("meta", k) else "",
    n_clusters = if (is.finite(n_clusters)) n_clusters else NA_integer_,
    state = state, apply = apply, rationale = rationale,
    resolution_goal = opt$resolution_goal, effective_maxK = effective_max_k,
    selection_class = selection_class, decision_reason = decision_reason,
    resumed = resumed, seed = opt$seed, stringsAsFactors = FALSE)
  write.csv(row, file.path(opt$outdir, "resolution_state.csv"), row.names = FALSE)
  row
}

write_options <- function(shortlist, recommendation) {
  write.csv(shortlist, file.path(opt$outdir, "resolution_shortlist.csv"), row.names = FALSE)
  write.csv(recommendation$role_recommendations,
            file.path(opt$outdir, "resolution_role_recommendations.csv"), row.names = FALSE)
  lines <- c(
    "# Resolution review checkpoint", "",
    sprintf("The automatic assessment is **%s**.", recommendation$selection_class), "",
    "The run reports separate broad, balanced, fine, and rare-preserving candidates because",
    "one cluster count cannot represent every biological objective. Review the labelled diagnostic:",
    "[resolution evidence](figures/fig_resolution_diagnostics.png).", "",
    "Choose one continuation:", "",
    "1. Select an exact `k` and resume with `--resolution-review apply --k <k>`.",
    "2. Accept the finest defensible fallback with `--resolution-review auto --k auto`.",
    "3. Stop and revise the marker panel or requested granularity.", "",
    "The shortlist is in `resolution_shortlist.csv`; the role summary is in",
    "`resolution_role_recommendations.csv`. Higher `k` can protect finer or rare subsets,",
    "but increases the burden of reviewing biologically justified merges. The requested granularity",
    "selects one role-specific recommendation; exact reviewed choices may use any evaluated k.",
    "UMAP is visual evidence only.")
  writeLines(lines, file.path(opt$outdir, "resolution_review.md"))
}

plot_resolution_evidence <- function(candidates, selected_k = NA_integer_, selected_label = "proposed k") {
  metrics <- c("delta_area", "pac", "median_cluster_consensus", "minimum_cluster_consensus",
               "weighted_silhouette", "macro_silhouette", "weighted_dbi")
  metric_long <- do.call(rbind, lapply(metrics, function(metric)
    data.frame(k = candidates$k, metric = metric, value = candidates[[metric]], stable = candidates$stable)))
  p <- ggplot(metric_long, aes(k, value)) + geom_line(colour = "#276FBF") +
    geom_point(aes(shape = stable), size = 2, colour = "#183153") +
    facet_wrap(~metric, scales = "free_y", ncol = 2) +
    labs(title = "Resolution evidence from stored SOM codes", shape = "Stable", x = "Metaclusters (k)") +
    theme_minimal(base_family = "Liberation Sans")
  if (is.finite(selected_k)) p <- p +
    geom_vline(xintercept = selected_k, colour = "#D62728", linewidth = 0.65, linetype = "dashed") +
    geom_text(data = data.frame(k = selected_k, metric = metrics, value = Inf),
              aes(k, value, label = sprintf("%s = %d", selected_label, selected_k)),
              inherit.aes = FALSE, vjust = 1.4,
              hjust = if (selected_k > stats::median(candidates$k)) 1.05 else -0.05,
              colour = "#D62728", size = 2.7)
  ggsave(file.path(figdir, "fig_resolution_diagnostics.png"), p, width = 9, height = 9, dpi = 160)
  ggsave(file.path(figdir, "fig_resolution_diagnostics.svg"), p, width = 9, height = 9)
  delta_plot <- ggplot(candidates, aes(k, delta_area)) + geom_line(colour = "#276FBF") +
    geom_point(colour = "#183153") + labs(title = "Consensus delta area", x = "Metaclusters (k)", y = "Delta area") +
    theme_minimal(base_family = "Liberation Sans")
  if (is.finite(selected_k)) delta_plot <- delta_plot +
    geom_vline(xintercept = selected_k, colour = "#D62728", linewidth = 0.65, linetype = "dashed") +
    annotate("text", x = selected_k, y = Inf, label = sprintf("%s = %d", selected_label, selected_k),
             vjust = 1.4, hjust = if (selected_k > stats::median(candidates$k)) 1.05 else -0.05,
             colour = "#D62728", size = 3)
  ggsave(file.path(figdir, "fig_delta_area.png"), delta_plot, width = 6, height = 4, dpi = 150)
  ggsave(file.path(figdir, "fig_delta_area.svg"), delta_plot, width = 6, height = 4)
}

candidate_path <- file.path(opt$outdir, "resolution_candidates.csv")
resume_path <- opt$resume_candidates

if (resumed) {
  if (!file.exists(resume_path)) stop("--resume-candidates file does not exist.")
  sce <- readRDS(resume_path)
  embedded <- S4Vectors::metadata(sce)$resolution_candidates
  contract <- S4Vectors::metadata(sce)$resolution_resume_contract
  stored_goal <- as.character(contract$resolution_goal)
  if (length(stored_goal) != 1L || is.na(stored_goal) ||
      !stored_goal %in% c("broad", "standard", "fine", "rare"))
    stop("Candidate SCE has no valid resolution goal in its resume contract.")
  if (goal_was_explicit && !identical(as.character(opt$resolution_goal), stored_goal))
    stop("Explicit --resolution-goal conflicts with the goal stored in the candidate contract.")
  opt$resolution_goal <- stored_goal
  logmsg("=== 02_cluster.R | seed=%d | goal=%s | review=%s ===",
         opt$seed, opt$resolution_goal, opt$resolution_review)
  stored_csv <- file.path(dirname(resume_path), "resolution_candidates.csv")
  if (!file.exists(stored_csv)) stop("resolution_candidates.csv must be beside the candidate SCE.")
  if (!identical(as.integer(opt$seed), as.integer(contract$seed)))
    stop("Resume seed differs from the seed stored in the candidate contract.")
  if (is.finite(opt$maxK) && !identical(as.integer(opt$maxK), as.integer(contract$max_k)))
    stop("Explicit --maxK differs from the effective maxK stored in the candidate contract.")
  resolution_validate_resume_contract(sce, contract, stored_csv)
  candidates <- utils::read.csv(stored_csv, stringsAsFactors = FALSE, check.names = FALSE)
  if (!isTRUE(all.equal(as.data.frame(embedded), as.data.frame(candidates),
                        check.attributes = FALSE, tolerance = 1e-12)))
    stop("Candidate table embedded in the SCE does not match resolution_candidates.csv.")
  effective_max_k <- as.integer(contract$max_k)
  if (!identical(normalizePath(stored_csv), normalizePath(candidate_path, mustWork = FALSE)))
    file.copy(stored_csv, candidate_path, overwrite = TRUE)
  logmsg("Validated resume contract; FlowSOM and ConsensusClusterPlus were not rerun.")
} else {
  logmsg("=== 02_cluster.R | seed=%d | goal=%s | review=%s ===",
         opt$seed, opt$resolution_goal, opt$resolution_review)
  if (is.na(opt$sce) || !file.exists(opt$sce)) stop("--sce must name an existing prepared SCE.")
  sce <- readRDS(opt$sce)
  som_node_limit <- as.integer(opt$xdim * opt$ydim)
  requested_max_k <- resolution_adaptive_max_k(
    som_nodes = som_node_limit, occupied_nodes = som_node_limit,
    explicit_max_k = opt$maxK)

  set.seed(opt$seed)
  t0 <- Sys.time()
  sce <- CATALYST::cluster(sce, features = "type", xdim = opt$xdim, ydim = opt$ydim,
                           maxK = requested_max_k, verbose = FALSE, seed = opt$seed)
  clustering_seconds <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
  occupied_nodes <- length(unique(as.character(sce$cluster_id)))
  effective_max_k <- min(requested_max_k, occupied_nodes)
  if (effective_max_k < 2L) stop("Fewer than two occupied SOM nodes; resolution cannot be assessed.")
  logmsg("One FlowSOM %dx%d run + CATALYST metaclustering maxK=%d completed in %.1f min.",
         opt$xdim, opt$ydim, requested_max_k, clustering_seconds / 60)
  logmsg("Data-adaptive resolution range is k=2..%d (%d occupied of %d SOM nodes; %.0f%% consensus resampling).",
         effective_max_k, occupied_nodes, som_node_limit,
         100 * RESOLUTION_CONSENSUS_SUBSAMPLE_FRACTION)
  logmsg("Clustering used all %d post-QC cells; no analysis-path subsampling.", ncol(sce))

  da_plot <- tryCatch(CATALYST::delta_area(sce), error = function(e) NULL)
  delta <- data.frame(k = integer(), delta_area = numeric())
  if (!is.null(da_plot)) {
    da_plot <- da_plot + ggplot2::theme(text = ggplot2::element_text(family = "Liberation Sans"))
    ggsave(file.path(figdir, "fig_delta_area.png"), da_plot, width = 6, height = 4, dpi = 150)
    ggsave(file.path(figdir, "fig_delta_area.svg"), da_plot, width = 6, height = 4)
    d <- tryCatch(ggplot2::ggplot_build(da_plot)$data[[1]], error = function(e) NULL)
    if (!is.null(d) && all(c("x", "y") %in% names(d)))
      delta <- data.frame(k = as.integer(d$x), delta_area = as.numeric(d$y))
  }

  som_codes <- as.matrix(S4Vectors::metadata(sce)$SOM_codes)
  cluster_codes <- as.data.frame(S4Vectors::metadata(sce)$cluster_codes)
  t_diag <- Sys.time()
  rebuilt <- suppressMessages(resolution_rebuild_consensus(
    som_codes, max_k = requested_max_k, reps = 100L,
    distance = "euclidean", seed = opt$seed))
  diagnostic_seconds <- as.numeric(difftime(Sys.time(), t_diag, units = "secs"))
  logmsg("Consensus diagnostics added %.1f s (%.1f%% of clustering wall time).",
         diagnostic_seconds, 100 * diagnostic_seconds / max(clustering_seconds, 0.001))

  k_values <- seq.int(2L, effective_max_k)
  mismatch <- vapply(k_values, function(k) {
    stored <- cluster_codes[[paste0("meta", k)]]
    !resolution_partition_equivalent(stored, rebuilt[[k]]$consensusClass)
  }, logical(1))
  if (any(mismatch)) {
    bad <- paste(k_values[mismatch], collapse = ", ")
    write_resolution_state(NA_integer_, NA_integer_, "INCOMPLETE_resolution_diagnostic_mismatch",
                           FALSE, paste("Reconstructed partitions differ at k:", bad),
                           effective_max_k, "diagnostic_mismatch")
    saveRDS(sce, file.path(opt$outdir, "sce_cluster_candidates.rds"))
    stop(sprintf("ConsensusClusterPlus reconstruction did not match CATALYST at k=%s.", bad))
  }

  node_ids <- suppressWarnings(as.integer(as.character(sce$cluster_id)))
  if (any(!is.finite(node_ids)) || any(node_ids < 1L) || any(node_ids > nrow(som_codes)))
    stop("SOM node identifiers cannot be matched to stored SOM codes.")
  node_weights <- tabulate(node_ids, nbins = nrow(som_codes))
  sample_ids <- if ("sample_id" %in% colnames(SummarizedExperiment::colData(sce)))
    as.character(sce$sample_id) else rep("sample1", ncol(sce))

  rows <- lapply(k_values, function(k) {
    labels <- as.character(cluster_codes[[paste0("meta", k)]])
    consensus <- as.matrix(rebuilt[[k]]$consensusMatrix)
    cc <- resolution_cluster_consensus(consensus, labels)
    sil <- resolution_weighted_silhouette(som_codes, labels, node_weights)
    dbi <- resolution_weighted_dbi(som_codes, labels, node_weights)
    event_labels <- labels[node_ids]
    event_counts <- table(event_labels)
    coverage <- vapply(names(event_counts), function(label) {
      length(unique(sample_ids[event_labels == label])) / length(unique(sample_ids))
    }, numeric(1))
    data.frame(
      k = k,
      delta_area = if (k %in% delta$k) delta$delta_area[match(k, delta$k)] else NA_real_,
      pac = resolution_pac(consensus),
      median_cluster_consensus = if (any(is.finite(cc))) stats::median(cc, na.rm = TRUE) else NA_real_,
      minimum_cluster_consensus = if (any(is.finite(cc))) min(cc, na.rm = TRUE) else NA_real_,
      weighted_silhouette = sil$mean,
      macro_silhouette = sil$macro_mean,
      minimum_node_silhouette = sil$minimum,
      minimum_cluster_silhouette = sil$minimum_cluster_mean,
      negative_event_fraction = sil$negative_fraction,
      weighted_dbi = dbi,
      total_events = length(event_labels),
      minimum_cluster_events = min(event_counts),
      minimum_cluster_pct = 100 * min(event_counts) / length(event_labels),
      minimum_sample_coverage = min(coverage),
      stringsAsFactors = FALSE)
  })
  candidates <- do.call(rbind, rows)
  recommendation <- resolution_recommend(candidates, opt$resolution_goal,
                                         unattended = opt$resolution_review == "auto")
  candidates <- recommendation$candidates
  write.csv(candidates, candidate_path, row.names = FALSE)
  write.csv(recommendation$role_recommendations,
            file.path(opt$outdir, "resolution_role_recommendations.csv"), row.names = FALSE)

  S4Vectors::metadata(sce)$resolution_candidates <- candidates
  S4Vectors::metadata(sce)$resolution_diagnostic_seconds <- diagnostic_seconds
  S4Vectors::metadata(sce)$resolution_clustering_seconds <- clustering_seconds
  candidate_md5 <- unname(tools::md5sum(candidate_path))
  S4Vectors::metadata(sce)$resolution_resume_contract <-
    resolution_make_resume_contract(sce, opt$seed, effective_max_k, candidate_md5,
                                    opt$resolution_goal)
  saveRDS(sce, file.path(opt$outdir, "sce_cluster_candidates.rds"))
}

recommendation <- resolution_recommend(candidates, opt$resolution_goal,
                                       unattended = opt$resolution_review == "auto")
candidates <- recommendation$candidates
# The proposal receipt fingerprints this exact persisted table. Re-serializing a
# validated resume table can change byte-level numeric formatting even when the
# values are equal, so preserve the reviewed artifact verbatim on resume.
if (!resumed) write.csv(candidates, candidate_path, row.names = FALSE)
finest_k <- if (any(candidates$finest_defensible)) max(candidates$k[candidates$finest_defensible]) else NA_integer_
shortlist <- resolution_shortlist(candidates, finest_k)
write_options(shortlist, recommendation)
proposal_record_path <- file.path(opt$outdir, "resolution_review_proposal.json")
decision_path <- file.path(opt$outdir, "resolution_decision.csv")
if (opt$resolution_review != "apply") {
  review_write_proposal("resolution", candidate_path, opt$outdir,
                        list(recommended_k = recommendation$chosen_k,
                             selection_class = recommendation$selection_class,
                             resolution_goal = opt$resolution_goal,
                             goal_min_k = recommendation$goal_min_k,
                             adaptive_max_k = effective_max_k,
                             role_recommendations = recommendation$role_recommendations))
  review_write_decision_template("resolution", opt$outdir)
}
plot_resolution_evidence(candidates, recommendation$chosen_k, "proposed k")

if (opt$resolution_review == "propose") {
  state <- if (recommendation$weak) "INCOMPLETE_weak_structure" else
    if (recommendation$ambiguous) "INCOMPLETE_resolution_ambiguous" else "INCOMPLETE_proposal"
  write_resolution_state(recommendation$chosen_k, recommendation$chosen_k, state, FALSE,
                         "Review resolution_candidates.csv and resolution_review.md, then resume without retraining FlowSOM.",
                         effective_max_k, recommendation$selection_class, resumed)
  logmsg("RESOLUTION CHECKPOINT: %s. Candidate SCE and review shortlist are ready.", state)
  quit(save = "no", status = 3L)
}

if (opt$resolution_review == "apply") {
  chosen_k_number <- parse_k(opt$k)
  if (!chosen_k_number %in% candidates$k) stop("Reviewed --k is not present in the validated candidate table.")
  chosen <- candidates[candidates$k == chosen_k_number, , drop = FALSE]
  weak_override <- !is.finite(chosen$weighted_silhouette) || chosen$weighted_silhouette <= 0
  if (is.na(opt$decision_reason) || !nzchar(trimws(opt$decision_reason)))
    stop("A non-empty --decision-reason is required for a reviewed resolution selection.")
  review_read_selection_decision(decision_path, "select", chosen_k_number, opt$decision_reason)
  source_proposal <- if (file.exists(proposal_record_path)) proposal_record_path else
    if (resumed) file.path(dirname(resume_path), "resolution_review_proposal.json") else proposal_record_path
  proposal_record <- review_read_proposal(source_proposal, "resolution")
  receipt_validation <- review_validate_receipt(
    opt$review_receipt, "resolution", proposal_path = candidate_path,
    proposal_sha256 = proposal_record$proposal_sha256, decision_artifact = decision_path,
    selected_value = chosen_k_number)
  if (!isTRUE(receipt_validation$valid)) stop(receipt_validation$reason)
  review_write_receipt_state("resolution", receipt_validation, opt$outdir)
  resolution_state <- if (weak_override) "REVIEW_weak_structure_user_override" else
    review_receipt_state(receipt_validation, TRUE, "REVIEW_applied_unverified")
  selection_class <- if (weak_override) "weak_structure_user_override" else "exact_user_selected"
} else {
  if (recommendation$weak || !is.finite(recommendation$chosen_k)) {
    write_resolution_state(NA_integer_, NA_integer_, "INCOMPLETE_weak_structure", FALSE,
                           "No resolution has positive silhouette; exact user override with a recorded reason is required.",
                           effective_max_k, "weak_structure", resumed)
    logmsg("RESOLUTION CHECKPOINT: weak structure; no automatic resolution was applied.")
    quit(save = "no", status = 3L)
  }
  chosen_k_number <- as.integer(recommendation$chosen_k)
  resolution_state <- if (recommendation$ambiguous)
    "REVIEW_ambiguous_auto_applied" else "REVIEW_auto_applied"
  selection_class <- recommendation$selection_class
}

chosen_meta <- paste0("meta", chosen_k_number)
available <- colnames(S4Vectors::metadata(sce)$cluster_codes)
if (!chosen_meta %in% available) stop(sprintf("%s is absent from stored cluster codes.", chosen_meta))
n_clusters <- length(unique(S4Vectors::metadata(sce)$cluster_codes[[chosen_meta]]))
state_row <- write_resolution_state(chosen_k_number, n_clusters, resolution_state, TRUE,
                                    "Multi-metric SOM-code resolution assessment; UMAP was not used for selection.",
                                    effective_max_k, selection_class, resumed)
S4Vectors::metadata(sce)$chosen_k <- chosen_meta
S4Vectors::metadata(sce)$resolution_state <- state_row
saveRDS(sce, file.path(opt$outdir, "sce_clustered.rds"))
plot_resolution_evidence(candidates, chosen_k_number, "selected k")
logmsg("Selected %s -> %d metaclusters; state=%s; resumed=%s.",
       chosen_meta, n_clusters, resolution_state, resumed)
logmsg("Saved: %s", file.path(opt$outdir, "sce_clustered.rds"))
logmsg("=== 02 complete ===")
