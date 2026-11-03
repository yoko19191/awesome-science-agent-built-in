# Resolution diagnostics and deterministic selection helpers.
# Functions in this file operate on SOM codes and consensus partitions only; they never retrain FlowSOM.

RESOLUTION_PAC_LOWER <- 0.10
RESOLUTION_PAC_UPPER <- 0.90
RESOLUTION_MIN_CLUSTER_CONSENSUS <- 0.50
RESOLUTION_CONSENSUS_SUBSAMPLE_FRACTION <- 0.80
RESOLUTION_BROAD_UPPER_QUANTILE <- 1 / 3
RESOLUTION_FINE_LOWER_QUANTILE <- 0.50
RESOLUTION_RARE_LOWER_QUANTILE <- 0.75

resolution_adaptive_max_k <- function(som_nodes, occupied_nodes = som_nodes,
                                      explicit_max_k = NA_integer_,
                                      subsample_fraction = RESOLUTION_CONSENSUS_SUBSAMPLE_FRACTION) {
  som_nodes <- as.integer(som_nodes)
  occupied_nodes <- as.integer(occupied_nodes)
  if (!is.finite(som_nodes) || som_nodes < 2L)
    stop("At least two SOM nodes are required for resolution assessment.")
  if (!is.finite(occupied_nodes) || occupied_nodes < 2L || occupied_nodes > som_nodes)
    stop("occupied_nodes must be between 2 and the number of SOM nodes.")
  if (!is.finite(subsample_fraction) || subsample_fraction <= 0 || subsample_fraction > 1)
    stop("Consensus subsample fraction must be in (0, 1].")
  safe_max <- min(occupied_nodes, floor(som_nodes * subsample_fraction))
  if (safe_max < 2L)
    stop("Consensus subsampling leaves fewer than two nodes for resolution assessment.")
  if (is.finite(explicit_max_k)) {
    explicit_max_k <- as.integer(explicit_max_k)
    if (explicit_max_k < 2L) stop("Explicit maxK must be at least 2.")
    if (explicit_max_k > safe_max)
      stop(sprintf("Explicit maxK=%d exceeds the data-specific safe maximum of %d.",
                   explicit_max_k, safe_max))
    return(explicit_max_k)
  }
  as.integer(safe_max)
}

# Reconstruct the core ConsensusClusterPlus result without invoking its exported
# report wrapper. ConsensusClusterPlus() renders a legend, one heatmap per k,
# CDF, and tracking plot even when plot=NULL; those plots are discarded here and
# can dominate wall time for small cytometry datasets. The fixed hc/euclidean
# calculation below follows ccRun with the exported wrapper's exact defaults and
# RNG sequence. Its only optimization is updating sampled connectivity submatrices
# directly instead of allocating a full outer-product matrix for every cluster.
resolution_cc_run_hc_euclidean <- function(d, max_k, reps) {
  n <- ncol(d)
  full_distance <- as.matrix(stats::dist(t(d), method = "euclidean"))
  sample_cols <- getFromNamespace("sampleCols", "ConsensusClusterPlus")
  upper_mask <- upper.tri(matrix(FALSE, nrow = n, ncol = n), diag = TRUE)
  upper_pairs <- which(upper_mask, arr.ind = TRUE)
  pair_position <- matrix(NA_integer_, nrow = n, ncol = n)
  pair_position[upper_pairs] <- seq_len(nrow(upper_pairs))
  pair_position[cbind(upper_pairs[, 2], upper_pairs[, 1])] <- seq_len(nrow(upper_pairs))
  sampled_pair_count <- integer(nrow(upper_pairs))
  connectivity_count <- matrix(0L, nrow = nrow(upper_pairs), ncol = max_k - 1L)

  for (iteration in seq_len(reps)) {
    sampled <- sample_cols(d, RESOLUTION_CONSENSUS_SUBSAMPLE_FRACTION, 1, NULL, NULL)
    sample_key <- as.integer(sampled[[3]])
    sampled_distance <- stats::as.dist(full_distance[sampled$subcols, sampled$subcols, drop = FALSE])
    attr(sampled_distance, "method") <- "euclidean"
    tree <- stats::hclust(sampled_distance, method = "average")
    assignments <- stats::cutree(tree, k = seq.int(2L, max_k))
    if (max_k == 2L) assignments <- matrix(assignments, ncol = 1L)
    local_pairs <- which(upper.tri(matrix(FALSE, nrow = length(sample_key),
                                           ncol = length(sample_key)), diag = TRUE),
                         arr.ind = TRUE)
    global_positions <- pair_position[cbind(sample_key[local_pairs[, 1]],
                                            sample_key[local_pairs[, 2]])]
    sampled_pair_count[global_positions] <- sampled_pair_count[global_positions] + 1L
    same_cluster <- assignments[local_pairs[, 1], , drop = FALSE] ==
      assignments[local_pairs[, 2], , drop = FALSE]
    connectivity_count[global_positions, ] <-
      connectivity_count[global_positions, , drop = FALSE] + same_cluster
  }

  ml <- vector("list", max_k)
  ml[[1]] <- 0
  for (k in seq.int(2L, max_k)) {
    values <- connectivity_count[, k - 1L] / sampled_pair_count
    values[sampled_pair_count == 0L] <- 0
    consensus <- matrix(0, nrow = n, ncol = n)
    consensus[upper_mask] <- values
    consensus[lower.tri(consensus)] <- t(consensus)[lower.tri(consensus)]
    ml[[k]] <- consensus
  }
  ml
}

resolution_rebuild_consensus <- function(codes, max_k, reps = 100L,
                                         distance = "euclidean", seed = 1234L) {
  d <- t(as.matrix(codes))
  if (max_k < 2L) stop("max_k must be at least 2.")
  if (!identical(distance, "euclidean"))
    stop("The verified reconstruction currently supports Euclidean distance only.")
  set.seed(seed)
  ml <- resolution_cc_run_hc_euclidean(d, as.integer(max_k), as.integer(reps))
  rebuilt <- vector("list", as.integer(max_k))
  for (k in seq.int(2L, as.integer(max_k))) {
    consensus <- ml[[k]]
    tree <- stats::hclust(stats::as.dist(1 - consensus), method = "average")
    partition <- stats::cutree(tree, k)
    names(partition) <- colnames(d)
    rebuilt[[k]] <- list(
      consensusMatrix = consensus,
      consensusTree = tree,
      consensusClass = partition
    )
  }
  rebuilt
}

resolution_partition_equivalent <- function(a, b) {
  a <- as.character(a); b <- as.character(b)
  length(a) == length(b) && identical(outer(a, a, `==`), outer(b, b, `==`))
}

resolution_pac <- function(consensus, lower = RESOLUTION_PAC_LOWER,
                           upper = RESOLUTION_PAC_UPPER) {
  consensus <- as.matrix(consensus)
  values <- consensus[upper.tri(consensus)]
  values <- values[is.finite(values)]
  if (!length(values)) return(NA_real_)
  mean(values > lower & values < upper)
}

resolution_cluster_consensus <- function(consensus, labels) {
  consensus <- as.matrix(consensus)
  labels <- as.character(labels)
  out <- vapply(sort(unique(labels)), function(label) {
    idx <- which(labels == label)
    if (length(idx) < 2L) return(NA_real_)
    values <- consensus[idx, idx, drop = FALSE]
    mean(values[upper.tri(values)], na.rm = TRUE)
  }, numeric(1))
  out
}

resolution_weighted_silhouette <- function(codes, labels, weights) {
  codes <- as.matrix(codes)
  labels <- as.character(labels)
  weights <- as.numeric(weights)
  stopifnot(nrow(codes) == length(labels), length(labels) == length(weights))
  occupied <- is.finite(weights) & weights > 0
  codes <- codes[occupied, , drop = FALSE]
  labels <- labels[occupied]
  weights <- weights[occupied]
  if (length(unique(labels)) < 2L || any(!is.finite(weights)) || any(weights < 0))
    return(list(mean = NA_real_, macro_mean = NA_real_, minimum = NA_real_,
                minimum_cluster_mean = NA_real_, negative_fraction = NA_real_))
  distances <- as.matrix(stats::dist(codes))
  silhouettes <- numeric(length(labels))
  for (i in seq_along(labels)) {
    own <- which(labels == labels[i])
    own_denominator <- sum(weights[own]) - 1
    within <- if (own_denominator > 0) sum(distances[i, own] * weights[own]) / own_denominator else 0
    other_labels <- setdiff(unique(labels), labels[i])
    between <- min(vapply(other_labels, function(label) {
      idx <- which(labels == label)
      sum(distances[i, idx] * weights[idx]) / sum(weights[idx])
    }, numeric(1)))
    denominator <- max(within, between)
    silhouettes[i] <- if (denominator > 0) (between - within) / denominator else 0
  }
  total_weight <- sum(weights)
  cluster_means <- vapply(sort(unique(labels)), function(label) {
    idx <- which(labels == label)
    sum(silhouettes[idx] * weights[idx]) / sum(weights[idx])
  }, numeric(1))
  list(
    mean = sum(silhouettes * weights) / total_weight,
    macro_mean = mean(cluster_means),
    minimum = min(silhouettes),
    minimum_cluster_mean = min(cluster_means),
    negative_fraction = sum(weights[silhouettes < 0]) / total_weight
  )
}

resolution_weighted_dbi <- function(codes, labels, weights) {
  codes <- as.matrix(codes)
  labels <- as.character(labels)
  weights <- as.numeric(weights)
  stopifnot(nrow(codes) == length(labels), length(labels) == length(weights))
  occupied <- is.finite(weights) & weights > 0
  codes <- codes[occupied, , drop = FALSE]
  labels <- labels[occupied]
  weights <- weights[occupied]
  groups <- sort(unique(labels))
  if (length(groups) < 2L) return(NA_real_)
  centroids <- matrix(NA_real_, nrow = length(groups), ncol = ncol(codes),
                      dimnames = list(groups, colnames(codes)))
  scatter <- numeric(length(groups))
  for (g in seq_along(groups)) {
    idx <- which(labels == groups[g])
    centroids[g, ] <- colSums(codes[idx, , drop = FALSE] * weights[idx]) / sum(weights[idx])
    delta <- sweep(codes[idx, , drop = FALSE], 2, centroids[g, ], `-`)
    distances <- sqrt(rowSums(delta^2))
    scatter[g] <- sum(distances * weights[idx]) / sum(weights[idx])
  }
  centroid_distances <- as.matrix(stats::dist(centroids))
  ratios <- matrix(-Inf, nrow = length(groups), ncol = length(groups))
  for (i in seq_along(groups)) for (j in seq_along(groups)) if (i != j) {
    ratios[i, j] <- if (centroid_distances[i, j] > 0)
      (scatter[i] + scatter[j]) / centroid_distances[i, j] else Inf
  }
  mean(apply(ratios, 1, max))
}

resolution_normalized_rank <- function(values, higher_is_better = FALSE) {
  values <- as.numeric(values)
  finite <- is.finite(values)
  if (!any(finite)) return(rep(1, length(values)))
  if (length(unique(values[finite])) == 1L) {
    out <- rep(1, length(values)); out[finite] <- 0
    return(out)
  }
  ranked_values <- if (higher_is_better) -values else values
  ranks <- rank(ranked_values, na.last = "keep", ties.method = "average")
  out <- (ranks - 1) / max(1, sum(finite) - 1)
  out[!finite] <- 1
  out
}

resolution_pareto_front <- function(candidates) {
  macro_silhouette <- if ("macro_silhouette" %in% names(candidates))
    candidates$macro_silhouette else candidates$weighted_silhouette
  losses <- cbind(
    pac = candidates$pac,
    median_consensus = -candidates$median_cluster_consensus,
    minimum_consensus = -candidates$minimum_cluster_consensus,
    silhouette = -candidates$weighted_silhouette,
    macro_silhouette = -macro_silhouette,
    dbi = candidates$weighted_dbi
  )
  losses[!is.finite(losses)] <- Inf
  keep <- rep(TRUE, nrow(losses))
  for (i in seq_len(nrow(losses))) {
    for (j in seq_len(nrow(losses))) {
      if (i == j) next
      no_worse <- all(losses[j, ] <= losses[i, ])
      strictly_better <- any(losses[j, ] < losses[i, ])
      if (no_worse && strictly_better) { keep[i] <- FALSE; break }
    }
  }
  keep
}

resolution_rank_candidates <- function(candidates) {
  required <- c("k", "pac", "median_cluster_consensus", "minimum_cluster_consensus",
                "weighted_silhouette", "weighted_dbi")
  if (!all(required %in% names(candidates)))
    stop("Resolution candidates are missing required metric columns.")
  candidates$rank_pac <- resolution_normalized_rank(candidates$pac)
  candidates$rank_consensus <- resolution_normalized_rank(candidates$median_cluster_consensus, TRUE)
  candidates$rank_minimum_consensus <- resolution_normalized_rank(candidates$minimum_cluster_consensus, TRUE)
  candidates$rank_silhouette <- resolution_normalized_rank(candidates$weighted_silhouette, TRUE)
  if (!"macro_silhouette" %in% names(candidates))
    candidates$macro_silhouette <- candidates$weighted_silhouette
  candidates$rank_macro_silhouette <- resolution_normalized_rank(candidates$macro_silhouette, TRUE)
  candidates$rank_dbi <- resolution_normalized_rank(candidates$weighted_dbi)
  candidates$combined_rank <- apply(candidates[, c(
    "rank_pac", "rank_consensus", "rank_minimum_consensus", "rank_silhouette",
    "rank_macro_silhouette", "rank_dbi")], 1, stats::median)
  candidates$pareto <- resolution_pareto_front(candidates)
  candidates$stable <- is.finite(candidates$weighted_silhouette) &
    candidates$weighted_silhouette > 0 &
    is.finite(candidates$macro_silhouette) & candidates$macro_silhouette > 0 &
    is.finite(candidates$median_cluster_consensus) &
    candidates$median_cluster_consensus > RESOLUTION_MIN_CLUSTER_CONSENSUS &
    candidates$pareto
  candidates$rare_stable <- candidates$stable &
    is.finite(candidates$minimum_cluster_consensus) &
    candidates$minimum_cluster_consensus > RESOLUTION_MIN_CLUSTER_CONSENSUS
  better_half <- candidates$combined_rank <= stats::median(candidates$combined_rank, na.rm = TRUE)
  candidates$finest_defensible <- is.finite(candidates$weighted_silhouette) &
    candidates$weighted_silhouette > 0 & is.finite(candidates$macro_silhouette) &
    candidates$macro_silhouette > 0 & better_half
  candidates
}

resolution_role_bands <- function(candidates) {
  k <- sort(unique(as.integer(candidates$k)))
  broad_upper <- as.integer(stats::quantile(k, RESOLUTION_BROAD_UPPER_QUANTILE,
                                             names = FALSE, type = 1))
  fine_lower <- as.integer(stats::quantile(k, RESOLUTION_FINE_LOWER_QUANTILE,
                                            names = FALSE, type = 1))
  rare_lower <- as.integer(stats::quantile(k, RESOLUTION_RARE_LOWER_QUANTILE,
                                            names = FALSE, type = 1))
  list(
    broad = candidates$k <= broad_upper,
    balanced = rep(TRUE, nrow(candidates)),
    fine = candidates$k >= fine_lower,
    rare_preserving = candidates$k >= rare_lower,
    broad_upper_k = broad_upper,
    fine_lower_k = fine_lower,
    rare_lower_k = rare_lower
  )
}

resolution_role_recommendations <- function(candidates) {
  bands <- resolution_role_bands(candidates)
  choose <- function(role, eligible, require_rare_stable = FALSE, prefer_small = FALSE) {
    stable_flag <- if (require_rare_stable) candidates$rare_stable else candidates$stable
    pool <- candidates[eligible & stable_flag, , drop = FALSE]
    if (!nrow(pool)) return(data.frame(
      role = role, k = NA_integer_, status = "NO_STABLE_CANDIDATE",
      combined_rank = NA_real_, stringsAsFactors = FALSE))
    tie <- if (prefer_small) pool$k else -pool$k
    selected <- pool[order(pool$combined_rank, tie), , drop = FALSE][1, , drop = FALSE]
    data.frame(role = role, k = as.integer(selected$k), status = "STABLE",
               combined_rank = selected$combined_rank, stringsAsFactors = FALSE)
  }
  do.call(rbind, list(
    choose("broad", bands$broad, prefer_small = TRUE),
    choose("balanced", bands$balanced),
    choose("fine", bands$fine),
    choose("rare_preserving", bands$rare_preserving, require_rare_stable = TRUE)
  ))
}

resolution_attach_roles <- function(candidates, recommendations) {
  candidates$candidate_roles <- ""
  for (i in seq_len(nrow(recommendations))) {
    k <- recommendations$k[[i]]
    if (!is.finite(k)) next
    hit <- which(candidates$k == k)
    existing <- candidates$candidate_roles[hit]
    candidates$candidate_roles[hit] <- ifelse(
      nzchar(existing), paste(existing, recommendations$role[[i]], sep = ";"),
      recommendations$role[[i]])
  }
  candidates
}

resolution_recommend <- function(candidates, goal = "standard", unattended = FALSE) {
  candidates <- resolution_rank_candidates(candidates)
  if (!goal %in% c("broad", "standard", "fine", "rare"))
    stop("Resolution goal must be broad, standard, fine, or rare.")
  recommendations <- resolution_role_recommendations(candidates)
  candidates <- resolution_attach_roles(candidates, recommendations)
  role <- switch(goal, broad = "broad", standard = "balanced", fine = "fine",
                 rare = "rare_preserving")
  role_row <- recommendations[recommendations$role == role, , drop = FALSE]
  bands <- resolution_role_bands(candidates)
  eligible <- switch(role, broad = bands$broad, balanced = bands$balanced,
                     fine = bands$fine, rare_preserving = bands$rare_preserving)
  goal_min_k <- if (role == "broad") min(candidates$k) else min(candidates$k[eligible])
  candidates$goal_eligible <- eligible
  if (is.finite(role_row$k[[1]])) {
    return(list(candidates = candidates, chosen_k = role_row$k[[1]],
                selection_class = paste0("role_", role), ambiguous = FALSE, weak = FALSE,
                goal_min_k = goal_min_k, role_recommendations = recommendations))
  }
  defensible <- candidates[candidates$finest_defensible & candidates$goal_eligible, , drop = FALSE]
  if (nrow(defensible)) {
    chosen <- defensible[which.max(defensible$k), , drop = FALSE]
    return(list(candidates = candidates, chosen_k = chosen$k[[1]],
                selection_class = "finest_defensible", ambiguous = TRUE, weak = FALSE,
                goal_min_k = goal_min_k, role_recommendations = recommendations))
  }
  list(candidates = candidates, chosen_k = NA_integer_,
       selection_class = "weak_structure", ambiguous = TRUE, weak = TRUE,
       goal_min_k = goal_min_k, role_recommendations = recommendations)
}

resolution_shortlist <- function(candidates, finest_k = NA_integer_) {
  ord <- order(candidates$combined_rank, -candidates$k)
  keep <- head(ord, 3L)
  if ("candidate_roles" %in% names(candidates))
    keep <- unique(c(keep, which(nzchar(candidates$candidate_roles))))
  if (is.finite(finest_k)) keep <- unique(c(keep, which(candidates$k == finest_k)))
  out <- candidates[keep, , drop = FALSE]
  out <- out[order(out$combined_rank, -out$k), , drop = FALSE]
  out$separation <- sprintf("weighted silhouette %.3f; DBI %.3f",
                            out$weighted_silhouette, out$weighted_dbi)
  out$consensus_summary <- sprintf("PAC %.3f; median/minimum consensus %.3f/%.3f",
                                   out$pac, out$median_cluster_consensus, out$minimum_cluster_consensus)
  out$rare_population_tradeoff <- ifelse(out$k == min(out$k),
    "higher risk of merging rare/fine subsets", ifelse(out$k == max(out$k),
    "best protection against merging rare/fine subsets", "intermediate rare-subset protection"))
  out$expected_merging_burden <- ifelse(out$k == min(out$k), "lower",
                                        ifelse(out$k == max(out$k), "higher", "moderate"))
  out
}

resolution_md5_text <- function(text) {
  path <- tempfile("resolution-contract-")
  on.exit(unlink(path), add = TRUE)
  writeLines(enc2utf8(as.character(text)), path, useBytes = TRUE)
  unname(tools::md5sum(path))
}

# SHA-256 over the clustering state a resumed checkpoint must reproduce exactly: the SOM codes,
# the metacluster code table, and every event's SOM node. Cell count, markers, samples and the
# candidate table cannot distinguish two different clusterings of the same events. The contract
# is stored inside the checkpoint RDS, so the RDS bytes cannot fingerprint themselves; the state
# components are serialized deterministically (uncompressed RDS v2) and hashed instead.
resolution_clustering_fingerprint <- function(sce) {
  som_codes <- S4Vectors::metadata(sce)$SOM_codes
  cluster_codes <- S4Vectors::metadata(sce)$cluster_codes
  cluster_id <- if ("cluster_id" %in% colnames(SummarizedExperiment::colData(sce)))
    as.character(sce$cluster_id) else character(0)
  state <- list(
    som_codes = if (is.null(som_codes)) NULL else {
      codes <- as.matrix(som_codes)
      list(dim = dim(codes), colnames = colnames(codes), values = as.numeric(codes))
    },
    cluster_codes = if (is.null(cluster_codes)) NULL else {
      table <- as.data.frame(cluster_codes, stringsAsFactors = FALSE)
      lapply(setNames(as.list(table), names(table)), as.character)
    },
    cluster_id = cluster_id)
  path <- tempfile("clustering-state-")
  on.exit(unlink(path), add = TRUE)
  saveRDS(state, path, version = 2, compress = FALSE)
  if (exists("review_sha256_file", mode = "function")) return(review_sha256_file(path))
  if (!requireNamespace("digest", quietly = TRUE))
    stop("Clustering-state fingerprints require review_contract.R or the digest package.")
  tolower(digest::digest(file = path, algo = "sha256", serialize = FALSE))
}

resolution_make_resume_contract <- function(sce, seed, max_k, candidate_md5, resolution_goal) {
  marker_names <- rownames(sce)[SummarizedExperiment::rowData(sce)$used_for_clustering %in% TRUE]
  sample_ids <- if ("sample_id" %in% colnames(SummarizedExperiment::colData(sce)))
    as.character(sce$sample_id) else rep("unknown", ncol(sce))
  qc <- S4Vectors::metadata(sce)$qc
  transform_text <- capture.output(dput(if (is.list(qc)) qc$transform else NULL))
  list(
    cells = ncol(sce),
    marker_fingerprint = resolution_md5_text(sort(marker_names)),
    sample_fingerprint = resolution_md5_text(sample_ids),
    transform_fingerprint = resolution_md5_text(transform_text),
    clustering_fingerprint = resolution_clustering_fingerprint(sce),
    resolution_goal = as.character(resolution_goal),
    seed = as.integer(seed),
    max_k = as.integer(max_k),
    candidate_md5 = as.character(candidate_md5)
  )
}

resolution_validate_resume_contract <- function(sce, contract, candidate_path) {
  if (!is.list(contract)) stop("Candidate SCE has no resolution resume contract.")
  observed_md5 <- unname(tools::md5sum(candidate_path))
  observed <- resolution_make_resume_contract(sce, contract$seed, contract$max_k, observed_md5,
                                              contract$resolution_goal)
  fields <- c("cells", "marker_fingerprint", "sample_fingerprint", "transform_fingerprint",
              "clustering_fingerprint", "resolution_goal", "seed", "max_k", "candidate_md5")
  mismatched <- fields[!vapply(fields, function(field) identical(observed[[field]], contract[[field]]), logical(1))]
  if (length(mismatched))
    stop(sprintf("Resolution resume contract mismatch: %s", paste(mismatched, collapse = ", ")))
  invisible(TRUE)
}
