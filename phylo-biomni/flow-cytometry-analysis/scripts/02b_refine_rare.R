#!/usr/bin/env Rscript
# Marker-aware rare-population refinement that preserves the primary global clustering.

suppressPackageStartupMessages({
  library(optparse); library(CATALYST); library(SingleCellExperiment)
  library(ggplot2); library(svglite)
})

script_arg <- commandArgs(trailingOnly = FALSE)
script_file <- sub("^--file=", "", script_arg[grepl("^--file=", script_arg)])
script_dir <- if (length(script_file)) dirname(normalizePath(script_file[[1]])) else getwd()
source(file.path(script_dir, "resolution_engine.R"))
source(file.path(script_dir, "annotation_engine.R"))
source(file.path(script_dir, "review_contract.R"))
source(file.path(script_dir, "rare_population_engine.R"))

opt <- parse_args(OptionParser(option_list = list(
  make_option("--prepared-sce", type = "character", default = NA_character_, dest = "prepared_sce",
              help = "Prepared pre-clustering SCE used for the two additional stability seeds."),
  make_option("--clustered-sce", type = "character", default = NA_character_, dest = "clustered_sce",
              help = "Primary clustered SCE containing the reviewed or provisional global clustering."),
  make_option("--resume-candidates", type = "character", default = NA_character_, dest = "resume_candidates",
              help = "Resume from sce_rare_candidates.rds without retraining FlowSOM."),
  make_option("--outdir", type = "character", default = "/mnt/results/cyto_run"),
  make_option("--population", type = "character", default = NA_character_),
  make_option("--positive-markers", type = "character", default = NA_character_, dest = "positive_markers"),
  make_option("--negative-markers", type = "character", default = NA_character_, dest = "negative_markers"),
  make_option("--minimum-frequency-pct", type = "double", default = NA_real_, dest = "minimum_frequency_pct"),
  make_option("--recurrence", type = "character", default = "majority",
              help = "any|majority|all sample recurrence required for a proposal."),
  make_option("--rare-review", type = "character", default = "propose", dest = "rare_review",
              help = "propose|auto|apply|reject. Selection never establishes biological identity."),
  make_option("--candidate-id", type = "character", default = NA_character_, dest = "candidate_id"),
  make_option("--review-receipt", type = "character", default = NA_character_, dest = "review_receipt"),
  make_option("--decision-reason", type = "character", default = NA_character_, dest = "decision_reason",
              help = "Required for a reasoned user override of a partially supported alternative."),
  make_option("--xdim", type = "integer", default = 10L),
  make_option("--ydim", type = "integer", default = 10L),
  make_option("--seed", type = "integer", default = 1234L)
)))

if (!opt$rare_review %in% c("propose", "auto", "apply", "reject"))
  stop("--rare-review must be propose, auto, apply, or reject.")
if (!opt$recurrence %in% c("any", "majority", "all"))
  stop("--recurrence must be any, majority, or all.")
if (is.finite(opt$minimum_frequency_pct) &&
    (opt$minimum_frequency_pct < 0 || opt$minimum_frequency_pct >= 100))
  stop("--minimum-frequency-pct must be in [0, 100).")

dir.create(opt$outdir, recursive = TRUE, showWarnings = FALSE)
figdir <- file.path(opt$outdir, "figures")
dir.create(figdir, recursive = TRUE, showWarnings = FALSE)
log_path <- file.path(opt$outdir, "qc_transform_log.txt")
logmsg <- function(...) {
  message <- sprintf(...)
  cat(message, "\n")
  cat(message, "\n", file = log_path, append = TRUE)
}

state_path <- file.path(opt$outdir, "rare_population_state.csv")
write_state <- function(state, selection_state = "NOT_SELECTED",
                        identity_validation_state = "NOT_EVALUABLE_rare_identity",
                        apply = FALSE, candidate_id = "", event_count = NA_integer_,
                        event_pct = NA_real_, rationale = "", resumed = FALSE) {
  row <- data.frame(
    population = ifelse(is.na(opt$population), "", opt$population), state = state,
    selection_state = selection_state, identity_validation_state = identity_validation_state,
    apply = apply, candidate_id = candidate_id,
    event_count = event_count, event_pct = event_pct,
    recurrence = opt$recurrence,
    minimum_frequency_pct = opt$minimum_frequency_pct,
    decision_reason = ifelse(is.na(opt$decision_reason), "", opt$decision_reason),
    rationale = rationale, resumed = resumed, seed = opt$seed,
    stringsAsFactors = FALSE)
  write.csv(row, state_path, row.names = FALSE)
  row
}

positive_markers <- rare_split_values(opt$positive_markers)
negative_markers <- rare_split_values(opt$negative_markers)
# optparse defaults cannot tell an omitted flag from an explicit one; on resume only explicitly
# supplied values are compared with the contract (the goal_was_explicit pattern of 02_cluster.R).
explicit_arguments <- rare_explicit_arguments(
  commandArgs(trailingOnly = TRUE),
  c("population", "positive-markers", "negative-markers", "recurrence", "minimum-frequency-pct"))
resumed <- !is.na(opt$resume_candidates) && nzchar(opt$resume_candidates)
candidates_path <- file.path(opt$outdir, "rare_population_candidates.csv")
candidate_sce_path <- file.path(opt$outdir, "sce_rare_candidates.rds")
proposal_path <- file.path(opt$outdir, "rare_population_review_proposal.json")
decision_path <- file.path(opt$outdir, "rare_population_decision.csv")

if (resumed) {
  if (!file.exists(opt$resume_candidates)) stop("--resume-candidates does not exist.")
  sce <- readRDS(opt$resume_candidates)
  stored_candidates <- file.path(dirname(opt$resume_candidates), "rare_population_candidates.csv")
  if (!file.exists(stored_candidates))
    stop("rare_population_candidates.csv must be beside the candidate SCE.")
  contract <- S4Vectors::metadata(sce)$rare_population_resume_contract
  if (!is.list(contract)) stop("Rare-population candidate SCE has no resume contract.")
  rare_check_resume_overrides(contract, explicit_arguments, list(
    population = opt$population, positive_markers = positive_markers,
    negative_markers = negative_markers, recurrence = opt$recurrence,
    minimum_frequency_pct = opt$minimum_frequency_pct))
  positive_markers <- as.character(contract$positive_markers)
  negative_markers <- as.character(contract$negative_markers)
  rare_validate_contract(sce, contract, stored_candidates,
                         positive_markers, negative_markers)
  candidates <- read.csv(stored_candidates, stringsAsFactors = FALSE, check.names = FALSE)
  embedded <- S4Vectors::metadata(sce)$rare_population_candidates
  for (column in intersect(names(embedded), names(candidates))) {
    if (is.character(embedded[[column]])) candidates[[column]] <- as.character(candidates[[column]])
  }
  embedded_match <- all.equal(as.data.frame(embedded), as.data.frame(candidates),
                              check.attributes = FALSE, tolerance = 1e-12)
  if (!isTRUE(embedded_match))
    stop(sprintf("Embedded rare-population candidates differ from the saved table: %s",
                 paste(embedded_match, collapse = "; ")))
  if (!identical(normalizePath(stored_candidates), normalizePath(candidates_path, mustWork = FALSE)))
    file.copy(stored_candidates, candidates_path, overwrite = TRUE)
  opt$population <- as.character(contract$population)
  opt$minimum_frequency_pct <- as.numeric(contract$minimum_frequency_pct)
  opt$recurrence <- as.character(contract$recurrence)
  seeds <- as.integer(contract$seeds)
  logmsg("Validated rare-population resume contract; no FlowSOM fit was repeated.")
} else {
  if (is.na(opt$population) || !nzchar(trimws(opt$population))) {
    write_state("INCOMPLETE_rare_population_unspecified",
                rationale = "Name the rare population before marker-aware refinement.")
    quit(save = "no", status = 3L)
  }
  if (length(c(positive_markers, negative_markers)) < RARE_MIN_DEFINING_MARKERS) {
    write_state("INCOMPLETE_rare_markers",
                rationale = sprintf("At least %d measured positive/negative defining markers are required.",
                                    RARE_MIN_DEFINING_MARKERS))
    quit(save = "no", status = 3L)
  }
  if (is.na(opt$clustered_sce) || !file.exists(opt$clustered_sce))
    stop("--clustered-sce must name the primary clustered SCE.")
  if (is.na(opt$prepared_sce) || !file.exists(opt$prepared_sce))
    stop("--prepared-sce is required for cross-seed rare-population stability.")
  sce <- readRDS(opt$clustered_sce)
  prepared <- readRDS(opt$prepared_sce)
  if (!identical(colnames(sce), colnames(prepared)))
    stop("Prepared and clustered SCE event identities or order differ.")
  if (!identical(nrow(S4Vectors::metadata(sce)$SOM_codes), as.integer(opt$xdim * opt$ydim)))
    stop("--xdim/--ydim do not match the primary SOM grid.")
  resolution_state_path <- file.path(dirname(opt$clustered_sce), "resolution_state.csv")
  if (!file.exists(resolution_state_path))
    stop("resolution_state.csv must be beside the primary clustered SCE.")
  resolution_state <- read.csv(resolution_state_path, stringsAsFactors = FALSE)
  if (!identical(as.integer(resolution_state$seed[[1]]), as.integer(opt$seed)))
    stop("Rare-population seed differs from the primary resolution seed.")

  initial <- tryCatch(
    rare_candidate_rows(sce, positive_markers, negative_markers,
                        opt$minimum_frequency_pct, opt$recurrence),
    error = function(error) error)
  if (inherits(initial, "error")) {
    write_state("INCOMPLETE_rare_markers", rationale = conditionMessage(initial))
    logmsg("RARE CHECKPOINT: %s", conditionMessage(initial))
    quit(save = "no", status = 3L)
  }
  candidates <- rare_rank_candidates(initial)
  seeds <- rare_seed_values(opt$seed)
  max_k <- max(as.integer(sub("^meta", "", grep(
    "^meta[0-9]+$", names(S4Vectors::metadata(sce)$cluster_codes), value = TRUE))))
  comparison_sces <- lapply(seeds[-1], function(seed) {
    logmsg("Running rare-population stability FlowSOM seed %d of %s.",
           seed, paste(seeds, collapse = ", "))
    set.seed(seed)
    CATALYST::cluster(prepared, features = "type", xdim = opt$xdim, ydim = opt$ydim,
                      maxK = max_k, verbose = FALSE, seed = seed)
  })
  if (any(!vapply(comparison_sces, function(item) identical(colnames(item), colnames(sce)), logical(1))))
    stop("A rare-population stability fit changed event identity or order.")
  candidates <- rare_add_stability(candidates, sce, comparison_sces)
  write.csv(candidates, candidates_path, row.names = FALSE)
  S4Vectors::metadata(sce)$rare_population_candidates <- candidates
  S4Vectors::metadata(sce)$rare_population_resume_contract <- rare_make_contract(
    sce, candidates_path, opt$population, positive_markers, negative_markers,
    opt$minimum_frequency_pct, opt$recurrence, seeds)
  saveRDS(sce, candidate_sce_path)
}

shortlist <- rare_shortlist(candidates)
write.csv(shortlist, file.path(opt$outdir, "rare_population_shortlist.csv"), row.names = FALSE)
supported_shortlist <- shortlist[shortlist$proposal_status == "SUPPORTED", , drop = FALSE]

plot_data <- candidates[candidates$frequency_ok & candidates$recurrence_ok &
                          is.finite(candidates$mean_stability_jaccard), , drop = FALSE]
if (nrow(plot_data)) {
  plot_data$stability <- ifelse(is.finite(plot_data$mean_stability_jaccard),
                                plot_data$mean_stability_jaccard, 0)
  plot_data$shortlisted <- plot_data$candidate_id %in% shortlist$candidate_id
  figure <- ggplot(plot_data, aes(event_pct, phenotype_score)) +
    geom_point(aes(colour = stability, shape = supported), size = 2.2, alpha = 0.75) +
    geom_text(data = plot_data[plot_data$shortlisted, , drop = FALSE],
              aes(label = candidate_id), hjust = -0.05, vjust = -0.4, size = 2.5,
              check_overlap = TRUE) +
    scale_colour_viridis_c(limits = c(0, 1)) +
    labs(title = sprintf("Rare-population candidates: %s", opt$population),
         subtitle = "Marker evidence and cross-seed event-set stability; reference labels were not used",
         x = "Candidate frequency (%)", y = "Signed marker-contrast score",
         colour = "Mean Jaccard", shape = "Supported") +
    theme_minimal(base_family = "Liberation Sans")
  ggsave(file.path(figdir, "fig_rare_population_candidates.png"), figure,
         width = 8, height = 5, dpi = 160)
  ggsave(file.path(figdir, "fig_rare_population_candidates.svg"), figure,
         width = 8, height = 5)
}

candidate_lines <- if (nrow(shortlist)) unlist(lapply(seq_len(nrow(shortlist)), function(index) {
  row <- shortlist[index, , drop = FALSE]
  sprintf(paste0("- `%s` at resolution meta%d: %.3f%% of events; sample coverage %.0f%%; ",
                 "stability %.2f; SOM nodes `%s`; marker contrasts `%s`; direction conflicts `%s`. ",
                 "Included for %s."),
          row$candidate_id, row$k, row$event_pct, 100 * row$sample_coverage,
          row$mean_stability_jaccard, row$node_membership, row$marker_contrasts,
          row$marker_direction_conflicts, row$shortlist_basis)
})) else "- No defensible candidate was found."

review_lines <- c(
  "# Rare-population review checkpoint", "",
  sprintf("Target: **%s**", opt$population),
  sprintf("Positive markers: %s", if (length(positive_markers)) paste(positive_markers, collapse = ", ") else "none"),
  sprintf("Negative markers: %s", if (length(negative_markers)) paste(negative_markers, collapse = ", ") else "none"), "",
  "The global clustering is preserved. These are separate marker-supported candidate branches.",
  "Local marker contrasts compare each branch with nearby SOM nodes. They are diagnostics; they do not define biological marker positivity or prove population identity.",
  "Review their frequency, marker contrast, recurrence, and agreement across three FlowSOM seeds:",
  "[rare-population candidate figure](figures/fig_rare_population_candidates.png).", "",
  "Candidate options (larger marker/stability/separation values are stronger, but none proves identity):", "",
  candidate_lines, "",
  sprintf("Which one action should I take for %s?", opt$population), "",
  "1. Select any exact candidate ID persisted in `rare_population_candidates.csv` with a review receipt. A non-shortlisted choice requires a reason and remains a user override.",
  "2. Continue automatically with the best supported proposal. This remains a provisional REVIEW selection.",
  "3. Reject all candidates. The run finishes as `INCOMPLETE_rare_resolution` with follow-up options rather than forcing a label.")
writeLines(review_lines, file.path(opt$outdir, "rare_population_review.md"))

if (!file.exists(proposal_path) && file.exists(candidates_path))
  review_write_proposal("rare_population", candidates_path, opt$outdir,
                        list(population = opt$population,
                             positive_markers = positive_markers,
                             negative_markers = negative_markers,
                             seeds = seeds,
                             supported_candidates = nrow(supported_shortlist),
                             alternative_candidates = nrow(shortlist) - nrow(supported_shortlist)))
if (!file.exists(decision_path)) review_write_decision_template("rare_population", opt$outdir)

if (!nrow(supported_shortlist) && opt$rare_review %in% c("propose", "auto")) {
  write_state("INCOMPLETE_rare_resolution", selection_state = "REJECTED_no_supported_candidate",
              rationale = paste(
    "No candidate satisfied marker direction, frequency/recurrence, and cross-seed stability.",
    "Review any persisted alternative with a reason, revise the phenotype, use control-derived thresholds, or retain the global clustering without a rare-population claim."),
    resumed = resumed)
  logmsg("RARE CHECKPOINT: no supported marker-stable candidate.")
  quit(save = "no", status = 3L)
}

if (opt$rare_review == "propose") {
  write_state("INCOMPLETE_rare_review", selection_state = "REVIEW_rare_auto_proposed",
              candidate_id = supported_shortlist$candidate_id[[1]],
              event_count = supported_shortlist$event_count[[1]], event_pct = supported_shortlist$event_pct[[1]],
              rationale = "Review the rare-population shortlist before applying a candidate.",
              resumed = resumed)
  logmsg("RARE CHECKPOINT: %d supported candidates are ready for review.", nrow(supported_shortlist))
  quit(save = "no", status = 3L)
}

if (opt$rare_review == "reject") {
  if (is.na(opt$decision_reason) || !nzchar(trimws(opt$decision_reason)))
    stop("--rare-review reject requires a non-empty --decision-reason.")
  review_read_selection_decision(decision_path, "reject", "REJECT_ALL", opt$decision_reason)
  proposal <- review_read_proposal(proposal_path, "rare_population")
  receipt <- review_validate_receipt(
    opt$review_receipt, "rare_population", proposal_path = candidates_path,
    proposal_sha256 = proposal$proposal_sha256, decision_artifact = decision_path,
    selected_value = "REJECT_ALL", expected_decisions = "reject")
  review_write_receipt_state("rare_population", receipt, opt$outdir)
  if (!isTRUE(receipt$valid)) stop(receipt$reason)
  write_state("INCOMPLETE_rare_resolution", selection_state = "REJECTED_rare_candidates",
              rationale = paste(
                "The user rejected every persisted candidate.",
                "Follow up with revised defining markers or frequency expectations, control-derived thresholds, a manual event-level gate, orthogonal validation, or retain only the global clustering."),
              resumed = resumed)
  logmsg("RARE CHECKPOINT: the user rejected all persisted candidates; no rare label was applied.")
  quit(save = "no", status = 3L)
}

if (opt$rare_review == "apply") {
  if (is.na(opt$candidate_id) || !nzchar(trimws(opt$candidate_id)))
    stop("--rare-review apply requires --candidate-id.")
  if (!opt$candidate_id %in% candidates$candidate_id)
    stop("The selected candidate is absent from the persisted rare-population candidate table.")
  shortlisted <- opt$candidate_id %in% shortlist$candidate_id
  selected_supported <- isTRUE(candidates$supported[match(opt$candidate_id, candidates$candidate_id)])
  user_override <- !shortlisted || !selected_supported
  if (is.na(opt$decision_reason) || !nzchar(trimws(opt$decision_reason)))
    stop("A non-empty --decision-reason is required for a user-selected rare-population candidate.")
  review_read_selection_decision(decision_path, "select", opt$candidate_id, opt$decision_reason)
  proposal <- review_read_proposal(proposal_path, "rare_population")
  receipt <- review_validate_receipt(
    opt$review_receipt, "rare_population", proposal_path = candidates_path,
    proposal_sha256 = proposal$proposal_sha256, decision_artifact = decision_path,
    selected_value = opt$candidate_id)
  review_write_receipt_state("rare_population", receipt, opt$outdir)
  if (!isTRUE(receipt$valid)) stop(receipt$reason)
  selection_state <- if (user_override) "REVIEW_rare_user_override" else
    "REVIEW_rare_user_selected"
  selected_id <- opt$candidate_id
} else {
  selected_id <- supported_shortlist$candidate_id[[1]]
  selection_state <- "REVIEW_rare_auto_proposed"
}

selected <- candidates[candidates$candidate_id == selected_id, , drop = FALSE][1, , drop = FALSE]
membership <- rare_candidate_membership(sce, selected_id)
sce$rare_population <- ifelse(membership, opt$population, "")
sce$rare_population_candidate <- ifelse(membership, selected_id, "")
S4Vectors::metadata(sce)$rare_population_selection <- as.list(selected)
state_row <- write_state("PASS_rare_deliverable", selection_state = selection_state,
                         apply = TRUE, candidate_id = selected_id,
                         event_count = selected$event_count, event_pct = selected$event_pct,
                         rationale = paste(
                           "Marker-aware branch selected independently of reference labels;",
                           "the primary global clustering is unchanged."), resumed = resumed)
S4Vectors::metadata(sce)$rare_population_state <- state_row
saveRDS(sce, file.path(opt$outdir, "sce_rare_refined.rds"))

sample_ids <- if ("sample_id" %in% colnames(SummarizedExperiment::colData(sce)))
  as.character(sce$sample_id) else rep("sample1", ncol(sce))
assignment <- do.call(rbind, lapply(unique(sample_ids), function(sample_id) {
  in_sample <- sample_ids == sample_id
  data.frame(sample_id = sample_id, population = opt$population,
             candidate_id = selected_id, count = sum(in_sample & membership),
             percentage = 100 * sum(in_sample & membership) / sum(in_sample),
             workflow_state = "PASS_rare_deliverable", selection_state = selection_state,
             identity_validation_state = "NOT_EVALUABLE_rare_identity",
             stringsAsFactors = FALSE)
}))
write.csv(assignment, file.path(opt$outdir, "rare_population_assignments.csv"), row.names = FALSE)
logmsg("Rare-population candidate %s applied to %d events (%.4f%%); state=%s.",
       selected_id, selected$event_count, selected$event_pct, selection_state)
