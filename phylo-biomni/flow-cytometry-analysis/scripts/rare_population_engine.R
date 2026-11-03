# Marker-aware rare-population proposals over stored FlowSOM partitions.
# Reference labels are deliberately absent from every interface in this file.

RARE_MIN_DEFINING_MARKERS <- 2L
RARE_MIN_DIRECTION_FRACTION <- 0.75
RARE_MIN_PHENOTYPE_SCORE <- 0
RARE_MIN_MEAN_JACCARD <- 0.60
RARE_MIN_WORST_JACCARD <- 0.50
RARE_STABILITY_CANDIDATES <- 50L
RARE_SHORTLIST_SIZE <- 10L
RARE_SEED_OFFSETS <- c(0L, 104729L, 130363L)
RARE_SEPARATION_SCALE_FLOOR <- 0.05

rare_split_values <- function(value) {
  if (length(value) != 1L || is.na(value) || !nzchar(trimws(value))) return(character())
  out <- trimws(strsplit(value, ",", fixed = TRUE)[[1]])
  unique(out[nzchar(out)])
}

rare_marker_aliases <- function(sce, code_names) {
  row_data <- as.data.frame(SummarizedExperiment::rowData(sce), stringsAsFactors = FALSE)
  row_names <- rownames(sce)
  type_rows <- if ("used_for_clustering" %in% names(row_data))
    which(row_data$used_for_clustering %in% TRUE) else
    if ("marker_class" %in% names(row_data)) which(row_data$marker_class == "type") else integer()
  alias_columns <- intersect(c("marker_name", "antigen", "channel_name", "fcs_colname"),
                             names(row_data))
  rows <- lapply(seq_along(code_names), function(index) {
    code <- code_names[[index]]
    exact <- match(code, row_names)
    candidate_rows <- if (length(type_rows) == length(code_names)) type_rows[[index]] else exact
    if (!is.finite(candidate_rows))
      stop(sprintf("SOM code column '%s' cannot be mapped to SCE marker metadata.", code))
    aliases <- unique(c(code, row_names[candidate_rows],
      unlist(lapply(alias_columns, function(column) as.character(row_data[[column]][candidate_rows])))))
    aliases <- aliases[!is.na(aliases) & nzchar(trimws(aliases))]
    data.frame(code_column = code, alias = aliases,
               normalized = annotation_normalize_marker(aliases), stringsAsFactors = FALSE)
  })
  unique(do.call(rbind, rows))
}

rare_resolve_markers <- function(sce, codes, requested, role) {
  requested <- unique(requested[nzchar(trimws(requested))])
  if (!length(requested)) return(character())
  aliases <- rare_marker_aliases(sce, colnames(codes))
  resolved <- vapply(requested, function(marker) {
    normalized <- annotation_normalize_marker(marker)
    hits <- unique(aliases$code_column[aliases$normalized == normalized])
    if (!length(hits))
      stop(sprintf("The %s marker '%s' is not available among the clustering markers.", role, marker))
    if (length(hits) > 1L)
      stop(sprintf("The %s marker '%s' is ambiguous across: %s.", role, marker,
                   paste(hits, collapse = ", ")))
    hits[[1]]
  }, character(1))
  unname(resolved)
}

rare_robust_scale <- function(values) {
  values <- as.numeric(values)
  centre <- stats::median(values, na.rm = TRUE)
  spread <- stats::mad(values, center = centre, constant = 1, na.rm = TRUE)
  if (!is.finite(spread) || spread <= sqrt(.Machine$double.eps))
    spread <- stats::sd(values, na.rm = TRUE)
  if (!is.finite(spread) || spread <= sqrt(.Machine$double.eps)) return(rep(0, length(values)))
  (values - centre) / spread
}

rare_candidate_rows <- function(sce, positive_markers, negative_markers,
                                minimum_frequency_pct = NA_real_, recurrence = "majority") {
  if (!recurrence %in% c("any", "majority", "all"))
    stop("Rare-population recurrence must be any, majority, or all.")
  codes <- as.matrix(S4Vectors::metadata(sce)$SOM_codes)
  cluster_codes <- as.data.frame(S4Vectors::metadata(sce)$cluster_codes)
  positive_columns <- rare_resolve_markers(sce, codes, positive_markers, "positive")
  negative_columns <- rare_resolve_markers(sce, codes, negative_markers, "negative")
  defining_columns <- c(positive_columns, negative_columns)
  if (length(defining_columns) < RARE_MIN_DEFINING_MARKERS)
    stop(sprintf("Rare-population proposals require at least %d measured defining markers.",
                 RARE_MIN_DEFINING_MARKERS))

  node_ids <- suppressWarnings(as.integer(as.character(sce$cluster_id)))
  if (any(!is.finite(node_ids)) || any(node_ids < 1L) || any(node_ids > nrow(codes)))
    stop("SOM node identifiers cannot be matched to the stored SOM codes.")
  node_weights <- tabulate(node_ids, nbins = nrow(codes))
  occupied <- node_weights > 0
  sample_ids <- if ("sample_id" %in% colnames(SummarizedExperiment::colData(sce)))
    as.character(sce$sample_id) else rep("sample1", ncol(sce))
  sample_levels <- unique(sample_ids)
  scaled_codes <- apply(codes, 2, rare_robust_scale)
  if (is.null(dim(scaled_codes))) scaled_codes <- matrix(scaled_codes, ncol = 1L)
  dimnames(scaled_codes) <- dimnames(codes)
  code_distances <- as.matrix(stats::dist(codes))
  distance_scale <- stats::median(code_distances[upper.tri(code_distances)], na.rm = TRUE)

  meta_columns <- grep("^meta[0-9]+$", names(cluster_codes), value = TRUE)
  rows <- list()
  for (meta_column in meta_columns) {
    k <- as.integer(sub("^meta", "", meta_column))
    node_labels <- as.character(cluster_codes[[meta_column]])
    event_labels <- node_labels[node_ids]
    for (label in sort(unique(event_labels))) {
      group_nodes <- occupied & node_labels == label
      background_nodes <- occupied & node_labels != label
      if (!any(group_nodes) || !any(background_nodes)) next
      event_member <- event_labels == label
      event_count <- sum(event_member)
      event_pct <- 100 * event_count / length(event_member)
      centroid <- colSums(codes[group_nodes, , drop = FALSE] * node_weights[group_nodes]) /
        sum(node_weights[group_nodes])
      background_indices <- which(background_nodes)
      background_distance <- sqrt(rowSums(sweep(codes[background_indices, , drop = FALSE],
                                                 2, centroid, `-`)^2))
      background_order <- background_indices[order(background_distance)]
      local_count <- which(cumsum(node_weights[background_order]) >= sum(node_weights[group_nodes]))[1]
      if (!is.finite(local_count)) local_count <- length(background_order)
      local_background <- background_order[seq_len(max(1L, local_count))]
      signed_contrasts <- c(
        vapply(positive_columns, function(marker)
          weighted.mean(scaled_codes[group_nodes, marker], node_weights[group_nodes]) -
            weighted.mean(scaled_codes[local_background, marker], node_weights[local_background]), numeric(1)),
        vapply(negative_columns, function(marker)
          weighted.mean(scaled_codes[local_background, marker], node_weights[local_background]) -
            weighted.mean(scaled_codes[group_nodes, marker], node_weights[group_nodes]), numeric(1)))
      marker_labels <- c(paste0(positive_markers, "+"), paste0(negative_markers, "-"))
      names(signed_contrasts) <- marker_labels
      direction_conflicts <- names(signed_contrasts)[!is.finite(signed_contrasts) |
                                                       signed_contrasts <= 0]
      sample_counts <- table(factor(sample_ids[event_member], levels = sample_levels))
      sample_present <- sample_counts > 0
      sample_coverage <- mean(sample_present)
      # majority = strict majority of samples; one of two samples is not a majority.
      recurrence_ok <- switch(recurrence, any = any(sample_present),
                              majority = sample_coverage > 0.5, all = all(sample_present))
      frequency_ok <- !is.finite(minimum_frequency_pct) || event_pct >= minimum_frequency_pct
      within_distance <- sqrt(rowSums(sweep(codes[group_nodes, , drop = FALSE],
                                            2, centroid, `-`)^2))
      separation <- min(background_distance) /
        max(weighted.mean(within_distance, node_weights[group_nodes]),
            RARE_SEPARATION_SCALE_FLOOR * distance_scale, sqrt(.Machine$double.eps))
      rows[[length(rows) + 1L]] <- data.frame(
        candidate_id = sprintf("meta%d_cluster%s", k, label), k = k, cluster = label,
        node_membership = paste(which(group_nodes), collapse = ";"),
        event_count = event_count, event_pct = event_pct,
        sample_coverage = sample_coverage, recurrence = recurrence,
        recurrence_ok = recurrence_ok, frequency_ok = frequency_ok,
        marker_direction_fraction = mean(signed_contrasts > 0),
        phenotype_score = stats::median(signed_contrasts),
        local_separation = separation,
        local_background_nodes = paste(local_background, collapse = ";"),
        marker_contrasts = paste(sprintf("%s=%.3f", names(signed_contrasts), signed_contrasts),
                                 collapse = ";"),
        marker_direction_conflicts = if (length(direction_conflicts))
          paste(direction_conflicts, collapse = ";") else "none",
        stringsAsFactors = FALSE)
    }
  }
  if (!length(rows)) return(data.frame())
  do.call(rbind, rows)
}

rare_rank_candidates <- function(candidates) {
  if (!nrow(candidates)) return(candidates)
  candidates$rank_phenotype <- resolution_normalized_rank(candidates$phenotype_score, TRUE)
  candidates$rank_direction <- resolution_normalized_rank(candidates$marker_direction_fraction, TRUE)
  candidates$rank_separation <- resolution_normalized_rank(candidates$local_separation, TRUE)
  candidates$rank_coverage <- resolution_normalized_rank(candidates$sample_coverage, TRUE)
  candidates$evidence_rank <- apply(candidates[, c(
    "rank_phenotype", "rank_direction", "rank_separation", "rank_coverage")],
    1, stats::median)
  candidates$marker_evidence_ok <- candidates$marker_direction_fraction >= RARE_MIN_DIRECTION_FRACTION &
    candidates$phenotype_score > RARE_MIN_PHENOTYPE_SCORE & candidates$frequency_ok &
    candidates$recurrence_ok
  candidates <- candidates[order(candidates$evidence_rank, candidates$k, -candidates$event_count),
                           , drop = FALSE]
  candidates <- candidates[!duplicated(candidates$node_membership), , drop = FALSE]
  rownames(candidates) <- NULL
  candidates
}

rare_candidate_membership <- function(sce, candidate_id) {
  match <- regexec("^meta([0-9]+)_cluster(.+)$", candidate_id)
  parts <- regmatches(candidate_id, match)[[1]]
  if (length(parts) != 3L) stop(sprintf("Malformed rare candidate id: %s.", candidate_id))
  column <- paste0("meta", as.integer(parts[[2]]))
  cluster_codes <- as.data.frame(S4Vectors::metadata(sce)$cluster_codes)
  if (!column %in% names(cluster_codes)) stop(sprintf("Rare candidate resolution is absent: %s.", column))
  node_ids <- suppressWarnings(as.integer(as.character(sce$cluster_id)))
  as.character(cluster_codes[[column]])[node_ids] == parts[[3]]
}

rare_best_partition_jaccard <- function(reference_membership, comparison_sce) {
  cluster_codes <- as.data.frame(S4Vectors::metadata(comparison_sce)$cluster_codes)
  node_ids <- suppressWarnings(as.integer(as.character(comparison_sce$cluster_id)))
  meta_columns <- grep("^meta[0-9]+$", names(cluster_codes), value = TRUE)
  best <- list(jaccard = 0, candidate_id = "")
  reference_count <- sum(reference_membership)
  for (column in meta_columns) {
    labels <- as.character(cluster_codes[[column]])[node_ids]
    intersections <- table(factor(labels[reference_membership], levels = unique(labels)))
    comparison_counts <- table(factor(labels, levels = unique(labels)))
    union <- reference_count + comparison_counts - intersections
    scores <- as.numeric(intersections / union)
    index <- which.max(scores)
    if (length(index) && is.finite(scores[[index]]) && scores[[index]] > best$jaccard) {
      best$jaccard <- scores[[index]]
      best$candidate_id <- sprintf("%s_cluster%s", column, names(comparison_counts)[[index]])
    }
  }
  best
}

rare_add_stability <- function(candidates, primary_sce, comparison_sces,
                               maximum_candidates = RARE_STABILITY_CANDIDATES) {
  candidates <- rare_rank_candidates(candidates)
  candidates$mean_stability_jaccard <- NA_real_
  candidates$minimum_stability_jaccard <- NA_real_
  candidates$stability_matches <- ""
  eligible <- which(candidates$frequency_ok & candidates$recurrence_ok)
  if (!length(eligible)) {
    candidates$supported <- FALSE
    candidates$rank_stability <- resolution_normalized_rank(candidates$mean_stability_jaccard, TRUE)
    candidates$combined_rank <- apply(candidates[, c(
      "rank_phenotype", "rank_direction", "rank_separation", "rank_coverage", "rank_stability")],
      1, stats::median)
    return(candidates)
  }
  eligible <- head(eligible[order(candidates$evidence_rank[eligible],
                                  -candidates$event_count[eligible])], maximum_candidates)
  for (index in eligible) {
    membership <- rare_candidate_membership(primary_sce, candidates$candidate_id[[index]])
    matches <- lapply(comparison_sces, function(sce)
      rare_best_partition_jaccard(membership, sce))
    values <- vapply(matches, `[[`, numeric(1), "jaccard")
    candidates$mean_stability_jaccard[[index]] <- mean(values)
    candidates$minimum_stability_jaccard[[index]] <- min(values)
    candidates$stability_matches[[index]] <- paste(vapply(matches, function(item)
      sprintf("%s=%.3f", item$candidate_id, item$jaccard), character(1)), collapse = ";")
  }
  candidates$supported <- candidates$marker_evidence_ok &
    is.finite(candidates$mean_stability_jaccard) &
    candidates$mean_stability_jaccard >= RARE_MIN_MEAN_JACCARD &
    candidates$minimum_stability_jaccard >= RARE_MIN_WORST_JACCARD
  candidates$rank_stability <- resolution_normalized_rank(candidates$mean_stability_jaccard, TRUE)
  candidates$combined_rank <- apply(candidates[, c(
    "rank_phenotype", "rank_direction", "rank_separation", "rank_coverage", "rank_stability")],
    1, stats::median)
  candidates
}

rare_shortlist <- function(candidates, limit = RARE_SHORTLIST_SIZE) {
  if (!nrow(candidates)) return(candidates)
  supported <- candidates[candidates$supported %in% TRUE, , drop = FALSE]
  supported <- supported[order(supported$combined_rank, -supported$event_count,
                               supported$k, supported$candidate_id), , drop = FALSE]
  supported$proposal_status <- rep("SUPPORTED", nrow(supported))
  supported$shortlist_basis <- rep("", nrow(supported))
  alternatives <- candidates[!candidates$supported & is.finite(candidates$mean_stability_jaccard) &
                               candidates$frequency_ok & candidates$recurrence_ok, , drop = FALSE]
  alternatives <- alternatives[order(alternatives$combined_rank, -alternatives$event_count,
                                     alternatives$k, alternatives$candidate_id), , drop = FALSE]
  alternatives$proposal_status <- rep("ALTERNATIVE_PARTIAL_MARKER_SUPPORT", nrow(alternatives))
  alternatives$shortlist_basis <- rep("partial-marker alternative", nrow(alternatives))
  # Keep the shortlist biologically diverse rather than showing only one
  # aggregate-score optimum. Distinct views of phenotype strength, local
  # separation, cross-seed persistence, and population size make nested rare
  # branches visible without consulting reference labels.
  supported_limit <- if (nrow(alternatives)) max(1L, limit - min(2L, nrow(alternatives))) else limit
  if (nrow(supported)) {
    supported_all <- supported
    top_combined_ids <- head(supported_all$candidate_id, 3L)
    priority <- unique(c(
      head(seq_len(nrow(supported_all)), 3L),
      which.max(supported_all$phenotype_score),
      which.max(supported_all$local_separation),
      which.max(supported_all$mean_stability_jaccard),
      which.min(supported_all$event_pct),
      seq_len(nrow(supported_all))))
    supported <- supported_all[head(priority, supported_limit), , drop = FALSE]
    for (index in seq_len(nrow(supported))) {
      basis <- character()
      if (supported$candidate_id[[index]] %in% top_combined_ids)
        basis <- c(basis, "best combined evidence")
      if (supported$phenotype_score[[index]] == max(supported_all$phenotype_score, na.rm = TRUE))
        basis <- c(basis, "strongest marker contrast")
      if (supported$local_separation[[index]] == max(supported_all$local_separation, na.rm = TRUE))
        basis <- c(basis, "strongest local separation")
      if (supported$mean_stability_jaccard[[index]] == max(supported_all$mean_stability_jaccard, na.rm = TRUE))
        basis <- c(basis, "highest cross-seed stability")
      if (supported$event_pct[[index]] == min(supported_all$event_pct, na.rm = TRUE))
        basis <- c(basis, "smallest supported branch")
      supported$shortlist_basis[[index]] <- paste(
        if (length(basis)) basis else "next combined evidence", collapse = "; ")
    }
  }
  alternatives <- head(alternatives, max(0L, limit - nrow(supported)))
  combined <- rbind(supported, alternatives)
  head(combined, limit)
}

rare_seed_values <- function(seed) as.integer(seed + RARE_SEED_OFFSETS)

rare_marker_fingerprint <- function(positive_markers, negative_markers) {
  resolution_md5_text(c(paste0("positive:", sort(unique(as.character(positive_markers)))),
                        paste0("negative:", sort(unique(as.character(negative_markers))))))
}

rare_make_contract <- function(sce, candidates_path, population, positive_markers,
                               negative_markers, minimum_frequency_pct, recurrence, seeds) {
  list(
    cells = ncol(sce),
    event_fingerprint = resolution_md5_text(colnames(sce)),
    marker_fingerprint = rare_marker_fingerprint(positive_markers, negative_markers),
    candidate_sha256 = review_sha256_file(candidates_path),
    clustering_fingerprint = resolution_clustering_fingerprint(sce),
    population = population,
    positive_markers = positive_markers,
    negative_markers = negative_markers,
    minimum_frequency_pct = minimum_frequency_pct,
    recurrence = recurrence,
    seeds = as.integer(seeds)
  )
}

rare_validate_contract <- function(sce, contract, candidates_path, positive_markers, negative_markers) {
  if (!is.list(contract)) stop("Rare-population candidate SCE has no resume contract.")
  if (missing(positive_markers) || missing(negative_markers))
    stop("Rare-population resume validation requires the positive and negative markers to check marker_fingerprint.")
  checks <- c(
    cells = identical(as.integer(contract$cells), as.integer(ncol(sce))),
    events = identical(as.character(contract$event_fingerprint),
                       resolution_md5_text(colnames(sce))),
    # The plain marker fields and the fingerprint were written together; both the supplied markers
    # and the contract's own marker list must reproduce the stored fingerprint.
    markers = identical(as.character(contract$marker_fingerprint),
                        rare_marker_fingerprint(positive_markers, negative_markers)) &&
      identical(as.character(contract$marker_fingerprint),
                rare_marker_fingerprint(contract$positive_markers, contract$negative_markers)),
    candidates = identical(tolower(as.character(contract$candidate_sha256)),
                           review_sha256_file(candidates_path)),
    # Event names and the candidate table do not identify the clustering itself; the SOM codes,
    # metacluster table and per-event node assignments must match what produced the candidates.
    clustering = identical(as.character(contract$clustering_fingerprint),
                           resolution_clustering_fingerprint(sce)))
  if (!all(checks))
    stop(sprintf("Rare-population resume contract mismatch: %s.",
                 paste(names(checks)[!checks], collapse = ", ")))
  invisible(TRUE)
}

# Which resume-relevant options were supplied on the command line (both `--flag value` and
# `--flag=value` forms). optparse defaults cannot distinguish an omitted flag from an explicit one.
rare_explicit_arguments <- function(args, flags) {
  args <- as.character(args)
  setNames(vapply(flags, function(flag)
    any(args == paste0("--", flag) | startsWith(args, paste0("--", flag, "="))), logical(1)), flags)
}

# On resume, omitted options adopt the contract; an explicitly supplied option that differs from the
# contract is a conflict and stops (a resumed checkpoint cannot be silently re-targeted).
rare_check_resume_overrides <- function(contract, explicit, values) {
  same_set <- function(a, b) identical(sort(unique(as.character(a))), sort(unique(as.character(b))))
  same_number <- function(a, b) {
    a <- suppressWarnings(as.numeric(a)); b <- suppressWarnings(as.numeric(b))
    (length(a) == 1L && length(b) == 1L && is.na(a) && is.na(b)) ||
      (length(a) == 1L && length(b) == 1L && isTRUE(all.equal(a, b)))
  }
  comparisons <- list(
    "population" = function() identical(as.character(values$population), as.character(contract$population)),
    "positive-markers" = function() same_set(values$positive_markers, contract$positive_markers),
    "negative-markers" = function() same_set(values$negative_markers, contract$negative_markers),
    "recurrence" = function() identical(as.character(values$recurrence), as.character(contract$recurrence)),
    "minimum-frequency-pct" = function() same_number(values$minimum_frequency_pct, contract$minimum_frequency_pct))
  for (flag in names(comparisons)) {
    if (isTRUE(explicit[[flag]]) && !isTRUE(comparisons[[flag]]()))
      stop(sprintf("Explicit --%s conflicts with the rare-population resume contract; omit it to adopt the stored value or start a new refinement.", flag))
  }
  invisible(TRUE)
}
