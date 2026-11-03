#!/usr/bin/env Rscript
# =====================================================================================
# build_manifest.R  --  Emit <outdir>/run_manifest.json for 07_build_report.py.
#
# WHY THIS SCRIPT EXISTS
#   07_build_report.py is data-driven: it reads <outdir>/run_manifest.json as the single
#   source of truth for every dataset number in the PDF. The R pipeline (01..08), however,
#   never writes that manifest -- so without this step 07 renders "n/a" for every field.
#   This script closes that gap HONESTLY: it derives each field from the pipeline's own
#   saved artifacts (the SCE .rds objects, the tables/*.csv, and qc_transform_log.txt).
#   It NEVER invents a value -- anything that cannot be derived is written as "n/a", which
#   is exactly what 07 shows when a field is missing.
#
# DESIGN CONSTRAINTS (do not violate)
#   * Does NOT edit or re-run 01..08 or gating_engine.R -- read-only over their outputs.
#   * Runs AFTER 06/08 (or after whatever the last stage that ran was), BEFORE 07.
#   * Every field is wrapped so a missing/partial run degrades to "n/a" instead of crashing
#     (e.g. ORIGINAL runs that stop early, or runs with no diff-abundance).
#   * diff_abundance{} keys are added ONLY when 06 actually produced
#     their .rds -- 07 emits those report sections conditionally on the key's presence.
#
# Manifest schema consumed by 07 (verified against 07_build_report.py):
#   scalars: modality, n_cells, n_samples, n_markers, n_clusters, n_populations, chosen_k,
#            transform, compensation, qc_removed, qc_summary, qc_detail, som_grid,
#            n_som_nodes, maxK, resolution_method, provenance, top_population,
#            top_population_pct
#   diff_abundance{}: mode, reason (descriptive_only), design (tested)
#
# Usage:
#   Rscript build_manifest.R --outdir <outdir>
# =====================================================================================

suppressPackageStartupMessages({
  library(optparse); library(jsonlite)
})

script_arg <- commandArgs(trailingOnly = FALSE)
script_file <- sub("^--file=", "", script_arg[grepl("^--file=", script_arg)])
script_dir <- if (length(script_file)) dirname(normalizePath(script_file[[1]])) else getwd()
source(file.path(script_dir, "review_contract.R"))

opt <- parse_args(OptionParser(option_list = list(
  make_option("--outdir", type = "character", default = "/mnt/results/cyto_run"),
  make_option("--resolution_method", type = "character", default = "multi-metric SOM-code assessment")
)))
OUT <- opt$outdir
if (!dir.exists(OUT)) stop(sprintf("outdir does not exist: %s", OUT))
TAB <- file.path(OUT, "tables")

# ---- helpers -------------------------------------------------------------------------
# safe(): evaluate an expression; any error/NULL/empty/NA collapses to NA (naify -> "n/a").
safe <- function(expr) tryCatch({
  v <- expr
  if (is.null(v) || length(v) == 0) return(NA)
  v
}, error = function(e) NA)
# naify(): coerce NA / NULL / empty to the literal string "n/a" (what 07 shows for missing).
naify <- function(x) {
  if (is.null(x) || length(x) == 0) return("n/a")
  if (length(x) == 1 && (is.na(x) || (is.character(x) && !nzchar(x)))) return("n/a")
  x
}
# num(): round a numeric to d places, else NA (kept numeric so JSON stays a number).
num <- function(x, d = 3) { x <- suppressWarnings(as.numeric(x)); if (length(x) != 1 || is.na(x)) NA else round(x, d) }
loginfo <- function(...) cat(sprintf(...), "\n")

loginfo("=== build_manifest.R | outdir=%s ===", OUT)

# ---- locate the most complete SCE available (partial runs degrade gracefully) --------
sce_path <- NULL
for (cand in c("sce_dr.rds", "sce_annotated.rds", "sce_rare_refined.rds",
               "sce_clustered.rds", "sce_prepped.rds")) {
  p <- file.path(OUT, cand)
  if (file.exists(p)) { sce_path <- p; break }
}
sce <- NULL
if (!is.null(sce_path)) {
  loginfo("Loading SCE: %s", basename(sce_path))
  sce <- safe(readRDS(sce_path))
  if (isTRUE(is.na(sce))) sce <- NULL
} else {
  loginfo("WARNING: no SCE .rds found in %s -- manifest will be mostly n/a.", OUT)
}

md  <- if (!is.null(sce)) safe(S4Vectors::metadata(sce)) else NA
qc  <- if (is.list(md)) md$qc else NULL
cc  <- if (is.list(md)) md$cluster_codes else NULL

# ---- parse qc_transform_log.txt for QC retention (only place pre-QC counts survive) ---
logpath <- file.path(OUT, "qc_transform_log.txt")
total_in <- NA; total_kept <- NA; log_lines <- character(0)
if (file.exists(logpath)) {
  log_lines <- readLines(logpath, warn = FALSE)
  n0s <- c(); kepts <- c()
  for (ln in log_lines) {
    m <- regmatches(ln, regexec("([0-9]+) -> ([0-9]+) cells kept", ln))[[1]]
    if (length(m) == 3) { n0s <- c(n0s, as.numeric(m[2])); kepts <- c(kepts, as.numeric(m[3])) }
  }
  if (length(n0s)) { total_in <- sum(n0s); total_kept <- sum(kepts) }
}
# fallback totals parsed from the post-QC summary line "cells=%d | type_markers=%d | samples=%d"
log_cells <- NA; log_tm <- NA; log_samples <- NA
if (length(log_lines)) {
  ln <- grep("cells=[0-9]+ \\| type_markers=", log_lines, value = TRUE)
  if (length(ln)) {
    mm <- regmatches(ln[length(ln)], regexec("cells=([0-9]+) \\| type_markers=([0-9]+) \\| samples=([0-9]+)", ln[length(ln)]))[[1]]
    if (length(mm) == 4) { log_cells <- as.numeric(mm[2]); log_tm <- as.numeric(mm[3]); log_samples <- as.numeric(mm[4]) }
  }
}
# was CellMarker2 actually used for annotation? 03_annotate.R logs this explicitly. Only
# then may 07 claim/cite CellMarker2 -- otherwise citing it would be a citation-integrity bug.
annotation_path <- file.path(OUT, "annotation_applied.csv")
cellmarker_used <- FALSE
if (file.exists(annotation_path)) {
  annotation_applied <- safe(read.csv(annotation_path, stringsAsFactors = FALSE))
  cellmarker_used <- is.data.frame(annotation_applied) && nrow(annotation_applied) &&
    "proposal_source" %in% names(annotation_applied) &&
    any(grepl("cellmarker", annotation_applied$proposal_source, ignore.case = TRUE))
}

# ---- core scalars (SCE first, log as fallback) ---------------------------------------
n_cells   <- safe({ if (!is.null(sce)) ncol(sce) else log_cells })
n_samples <- safe({ if (!is.null(sce)) length(unique(as.character(sce$sample_id))) else log_samples })
n_markers <- safe({
  if (!is.null(sce)) sum(SummarizedExperiment::rowData(sce)$marker_class == "type", na.rm = TRUE) else log_tm
})
modality     <- safe(qc$modality)
transform    <- safe(qc$transform)
if (is.list(transform)) {
  transform_method <- safe(transform$method)
  transform_cofactor <- safe(transform$cofactor)
  transform <- if (!is.na(transform_method) && !is.na(transform_cofactor))
    sprintf("%s (cofactor %s)", transform_method, transform_cofactor) else naify(transform_method)
}
compensation <- safe(qc$compensation)
qc_gating    <- safe(qc$qc_gating)

# provenance: collapse the metadata$qc$provenance list to a scalar string (never inferred)
provenance <- "n/a"
prov <- if (is.list(qc)) qc$provenance else NULL
if (is.list(prov) && length(prov)) {
  if (!is.null(prov$provenance) && length(prov$provenance) == 1 && !is.na(prov$provenance) && nzchar(as.character(prov$provenance))) {
    provenance <- as.character(prov$provenance)
  } else if (!is.null(prov$dataset) && nzchar(as.character(prov$dataset))) {
    provenance <- paste0("HDCytoData: ", as.character(prov$dataset))
  }
}

# ---- clustering resolution / SOM grid (from CATALYST cluster_codes) -------------------
chosen_k <- safe(md$chosen_k)
n_clusters <- safe({
  if (!is.null(sce) && !is.null(chosen_k) && !is.na(chosen_k))
    length(unique(as.character(CATALYST::cluster_ids(sce, chosen_k)))) else NA
})
n_populations <- safe({
  if (!is.null(sce)) length(unique(as.character(CATALYST::cluster_ids(sce, "annotation")))) else NA
})
n_som_nodes <- NA; som_grid <- "n/a"; maxK <- NA
if (is.data.frame(cc) || is.matrix(cc) || !is.null(cc)) {
  cn <- safe(colnames(cc))
  if (!isTRUE(is.na(cn)) && length(cn)) {
    som_col <- grep("^som[0-9]+$", cn, value = TRUE)
    if (length(som_col)) {
      n_som_nodes <- as.integer(sub("som", "", som_col[1]))
      s <- sqrt(n_som_nodes)
      if (is.finite(s) && s == floor(s)) som_grid <- sprintf("%dx%d", as.integer(s), as.integer(s))
    }
    meta_cols <- grep("^meta[0-9]+$", cn, value = TRUE)
    if (length(meta_cols)) maxK <- max(as.integer(sub("meta", "", meta_cols)))
  }
}

# ---- QC retention strings ------------------------------------------------------------
gating_txt <- if (isTRUE(qc_gating)) "modality-appropriate QC gating applied" else
              if (identical(qc_gating, FALSE)) "QC gating skipped by configuration" else "gating status n/a"
qc_removed <- "n/a"; qc_summary <- "n/a"; qc_detail <- "n/a"
if (is.finite(total_in) && is.finite(total_kept) && total_in > 0) {
  rem_n <- total_in - total_kept; pct <- 100 * (1 - total_kept / total_in)
  qc_removed <- sprintf("%s of %s events (%.1f%%)", format(rem_n, big.mark = ",", trim = TRUE),
                        format(total_in, big.mark = ",", trim = TRUE), pct)
  ns <- naify(n_samples)
  qc_summary <- sprintf("QC retained %s of %s input events (%.1f%% removed) across %s sample(s); %s.",
                        format(total_kept, big.mark = ",", trim = TRUE),
                        format(total_in, big.mark = ",", trim = TRUE), pct,
                        ifelse(ns == "n/a", "?", as.character(ns)), gating_txt)
  qc_detail <- sprintf("%s Transform: %s; compensation: %s.", qc_summary,
                       naify(transform), naify(compensation))
} else if (!is.null(sce)) {
  # no pre-QC totals in log, but we still know the post-QC size and gating mode
  qc_summary <- sprintf("Post-QC dataset: %s cells across %s sample(s); %s (pre-QC input count unavailable in log).",
                        naify(n_cells), naify(n_samples), gating_txt)
  qc_detail <- qc_summary
}

# ---- top population (from abundance_by_sample.csv; sorted desc by Total_n) ------------
top_population <- "n/a"; top_population_pct <- "n/a"
ab_path <- file.path(TAB, "abundance_by_sample.csv")
if (file.exists(ab_path)) {
  ab <- safe(read.csv(ab_path, stringsAsFactors = FALSE, check.names = FALSE))
  if (is.data.frame(ab) && nrow(ab) && "Total_n" %in% names(ab) && "population" %in% names(ab)) {
    i <- which.max(ab$Total_n)
    top_population <- as.character(ab$population[i])
    if ("Overall_pct" %in% names(ab)) top_population_pct <- num(ab$Overall_pct[i], 2)
  }
}

# ---- assemble scalar manifest --------------------------------------------------------
man <- list(
  modality          = naify(modality),
  n_cells           = naify(safe(as.integer(n_cells))),
  n_samples         = naify(safe(as.integer(n_samples))),
  n_markers         = naify(safe(as.integer(n_markers))),
  n_clusters        = naify(safe(as.integer(n_clusters))),
  n_populations     = naify(safe(as.integer(n_populations))),
  chosen_k          = naify(chosen_k),
  transform         = naify(transform),
  compensation      = naify(compensation),
  qc_removed        = naify(qc_removed),
  qc_summary        = naify(qc_summary),
  qc_detail         = naify(qc_detail),
  som_grid          = naify(som_grid),
  n_som_nodes       = naify(safe(as.integer(n_som_nodes))),
  maxK              = naify(safe(as.integer(maxK))),
  resolution_method = naify(opt$resolution_method),
  provenance        = naify(provenance),
  top_population     = naify(top_population),
  top_population_pct = naify(top_population_pct)
)
man$cellmarker_used <- isTRUE(cellmarker_used)  # boolean; drives conditional CellMarker2 claim/citation in 07

# Machine-readable execution states. Reports consume these records instead of inferring completion
# from the mere presence of a figure or SCE file.
read_state <- function(name, column = "state") {
  path <- file.path(OUT, name)
  if (!file.exists(path)) return("NOT_EVALUATED")
  tab <- safe(read.csv(path, stringsAsFactors = FALSE))
  if (!is.data.frame(tab) || !nrow(tab) || !column %in% names(tab)) return("INCOMPLETE_malformed_state")
  values <- unique(as.character(tab[[column]]))
  values <- values[!is.na(values) & nzchar(trimws(values))]
  if (!length(values)) return("INCOMPLETE_malformed_state")
  if (any(grepl("^INCOMPLETE", values))) return(values[grepl("^INCOMPLETE", values)][1])
  if (any(grepl("^REVIEW", values))) return(values[grepl("^REVIEW", values)][1])
  if (all(grepl("^(PASS|USED|APPLIED)", values))) return("PASS")
  values[1]
}
gate_state <- read_state("gating_state.csv")
resolution_state_table <- if (file.exists(file.path(OUT, "resolution_state.csv")))
  safe(read.csv(file.path(OUT, "resolution_state.csv"), stringsAsFactors = FALSE)) else NULL
rare_goal <- is.data.frame(resolution_state_table) && nrow(resolution_state_table) &&
  "resolution_goal" %in% names(resolution_state_table) &&
  identical(as.character(resolution_state_table$resolution_goal[[1]]), "rare")
rare_artifact_names <- c(
  "rare_population_candidates.csv", "rare_population_shortlist.csv",
  "rare_population_review.md", "rare_population_review_proposal.json",
  "rare_population_decision.csv", "rare_population_review_receipt_state.csv",
  "rare_population_state.csv", "rare_population_assignments.csv",
  "sce_rare_candidates.rds", "sce_rare_refined.rds")
rare_artifacts_present <- rare_artifact_names[file.exists(file.path(OUT, rare_artifact_names))]
rare_required <- rare_goal || length(rare_artifacts_present) > 0L
rare_state_path <- file.path(OUT, "rare_population_state.csv")
rare_state_table <- if (file.exists(rare_state_path))
  safe(read.csv(rare_state_path, stringsAsFactors = FALSE)) else NULL
rare_applied <- (is.data.frame(rare_state_table) && nrow(rare_state_table) &&
                   "apply" %in% names(rare_state_table) &&
                   isTRUE(safe(as.logical(rare_state_table$apply[[1]])))) ||
  file.exists(file.path(OUT, "rare_population_assignments.csv")) ||
  file.exists(file.path(OUT, "sce_rare_refined.rds"))
rare_workflow_state <- if (!rare_required) "NOT_REQUIRED" else
  if (!is.data.frame(rare_state_table) || !nrow(rare_state_table)) "INCOMPLETE_state_contradiction" else
    read_state("rare_population_state.csv")
rare_selection_state <- if (!rare_required) "NOT_REQUIRED" else
  read_state("rare_population_state.csv", "selection_state")
rare_identity_state <- if (!rare_required) "NOT_REQUIRED" else
  read_state("rare_population_state.csv", "identity_validation_state")
if (rare_applied && (!grepl("^PASS", rare_workflow_state) ||
                     !grepl("^REVIEW_rare_", rare_selection_state) ||
                     !grepl("^(NOT_EVALUABLE|PASS)_rare_identity", rare_identity_state)))
  rare_workflow_state <- "INCOMPLETE_state_contradiction"
analysis_state <- list(
  environment = read_state("environment_state.csv"),
  gating = gate_state,
  gating_receipt = read_state("gating_review_receipt_state.csv"),
  qc_uncertainty = if (file.exists(file.path(OUT, "qc_uncertainty.csv"))) {
    uncertainty <- safe(read.csv(file.path(OUT, "qc_uncertainty.csv"), stringsAsFactors = FALSE))
    if (is.data.frame(uncertainty) && nrow(uncertainty)) {
      if (identical(read_state("gating_review_receipt_state.csv"), "PASS")) "PASS_acknowledged" else
        "REVIEW_qc_uncertainty_recorded"
    } else "PASS"
  } else "NOT_EVALUATED",
  controls = read_state("control_usage_state.csv"),
  transform = read_state("transform_contract.csv"),
  lineage_bias = read_state("lineage_bias_state.csv"),
  resolution = read_state("resolution_state.csv"),
  resolution_receipt = read_state("resolution_review_receipt_state.csv"),
  rare_population = rare_workflow_state,
  rare_population_selection = rare_selection_state,
  rare_population_identity = rare_identity_state,
  rare_population_receipt = if (rare_required) read_state("rare_population_review_receipt_state.csv") else "NOT_REQUIRED",
  annotation = read_state("annotation_state.csv"),
  annotation_receipt = read_state("annotation_review_receipt_state.csv"),
  manual_reconciliation = read_state("manual_reconciliation_state.csv")
)
# Manual reconciliation is external agreement evidence, reported separately from workflow completeness.
state_values <- unlist(analysis_state[setdiff(names(analysis_state), "manual_reconciliation")], use.names = FALSE)
required_stages <- c("environment", "gating", "transform", "resolution", "annotation")
if (rare_required) required_stages <- c(required_stages, "rare_population")
required_values <- unlist(analysis_state[required_stages], use.names = FALSE)
receipt_stages <- c("gating", "resolution", "annotation")
for (stage in receipt_stages)
  if (grepl("^PASS", analysis_state[[stage]]) && !identical(analysis_state[[paste0(stage, "_receipt")]], "PASS"))
    required_values <- c(required_values, "NOT_EVALUATED")
if (rare_required && grepl("^REVIEW_rare_user_", rare_selection_state) &&
    !identical(analysis_state$rare_population_receipt, "PASS"))
  required_values <- c(required_values, "NOT_EVALUATED")
workflow_state <- if (any(grepl("^INCOMPLETE", required_values)) || any(required_values == "NOT_EVALUATED"))
  "INCOMPLETE" else if (any(grepl("^REVIEW", required_values))) "REVIEW" else "PASS"
scientific_state <- if (identical(workflow_state, "INCOMPLETE") || any(grepl("^INCOMPLETE", state_values)))
  "INCOMPLETE" else if (any(grepl("^REVIEW", state_values)) ||
                         any(grepl("^NOT_EVALUABLE", state_values))) "REVIEW" else "PASS"
man$analysis_state <- analysis_state
man$workflow_state <- workflow_state
man$scientific_state <- scientific_state
man$overall_state <- scientific_state

rare_assignment_path <- file.path(OUT, "rare_population_assignments.csv")
if (file.exists(rare_state_path)) {
  rare_state <- safe(read.csv(rare_state_path, stringsAsFactors = FALSE))
  rare_assignment <- if (file.exists(rare_assignment_path))
    safe(read.csv(rare_assignment_path, stringsAsFactors = FALSE)) else NULL
  if (is.data.frame(rare_state) && nrow(rare_state)) {
    man$rare_population <- list(
      population = naify(safe(rare_state$population[[1]])),
      workflow_state = naify(safe(rare_state$state[[1]])),
      selection_state = naify(safe(rare_state$selection_state[[1]])),
      identity_validation_state = naify(safe(rare_state$identity_validation_state[[1]])),
      applied = isTRUE(safe(as.logical(rare_state$apply[[1]]))),
      candidate_id = naify(safe(rare_state$candidate_id[[1]])),
      event_count = naify(safe(as.integer(rare_state$event_count[[1]]))),
      event_pct = naify(num(safe(rare_state$event_pct[[1]]), 4)),
      recurrence = naify(safe(rare_state$recurrence[[1]])),
      sample_rows = if (is.data.frame(rare_assignment)) nrow(rare_assignment) else 0L)
  }
}

# ---- v2.2.0 QC diagnostics (items 1/2/4/6/8): READ-ONLY from metadata(sce)$qc ---------
# Every field degrades to "n/a" (scalar) or an omitted key (nested) exactly like the rest of
# the manifest, so ORIGINAL/v2.1.0 runs and partial runs never crash. 07 renders each section
# only when its key is present. NOTHING here is inferred -- it is a straight read of what 01 wrote.
man$gate_engine <- naify(safe(qc$gate_engine))  # "builtin" | "opencyto"

tq <- if (is.list(qc)) qc$time_qc else NULL      # item 1: time-based acquisition QC
tq_path <- file.path(OUT, "time_qc_diagnostics.csv")
if (file.exists(tq_path)) {
  tq_table <- safe(read.csv(tq_path, stringsAsFactors = FALSE))
  if (is.data.frame(tq_table) && nrow(tq_table)) {
    tq_row <- tq_table[tq_table$sample_id == "ALL", , drop = FALSE]
    if (!nrow(tq_row)) tq_row <- tq_table[1, , drop = FALSE]
    tq <- list(
      mode = tq_row$mode[1], backend = tq_row$backend_used[1],
      requested_backend = tq_row$requested_backend[1], pct_rate = tq_row$pct_rate[1],
      pct_signal = tq_row$pct_signal[1], pct_margin = tq_row$pct_margin[1],
      removed = tq_row$removed_events[1])
  }
}
if (is.list(tq) && length(tq)) man$time_qc <- list(
  mode = naify(safe(tq$mode)), backend = naify(safe(tq$backend)),
  requested_backend = naify(safe(tq$requested_backend)),
  pct_rate = naify(num(tq$pct_rate, 3)), pct_signal = naify(num(tq$pct_signal, 3)),
  pct_margin = naify(num(tq$pct_margin, 3)), removed = naify(safe(as.integer(tq$removed))))

cd <- if (is.list(qc)) qc$compensation_diag else NULL   # item 2: spillover conditioning
if (is.list(cd) && length(cd)) man$compensation_diag <- list(
  source = naify(safe(cd$source)), dim = naify(safe(as.integer(cd$dim))),
  rcond = naify(num(cd$rcond, 4)), kappa = naify(num(cd$kappa, 3)),
  verdict = naify(safe(cd$verdict)), applied = isTRUE(safe(cd$applied)))

hz <- if (is.list(qc)) qc$harmonization else NULL       # item 4: batch-aware harmonization
if (is.list(hz) && length(hz)) man$harmonization <- list(
  scope = naify(safe(hz$scope)), n_batches = naify(safe(as.integer(hz$n_batches))),
  n_groups_harmonized = naify(safe(as.integer(hz$n_groups_harmonized))),
  shrink = naify(num(hz$shrink, 2)))

cnrm <- if (is.list(qc)) qc$cytof_norm else NULL        # item 8: CyTOF bead normalization
if (is.list(cnrm) && length(cnrm)) man$cytof_norm <- list(
  applied = isTRUE(safe(cnrm$applied)), beads = naify(safe(cnrm$beads)),
  k = naify(safe(as.integer(cnrm$k))), n_removed = naify(safe(as.integer(cnrm$n_removed))),
  n_beads = naify(safe(as.integer(cnrm$n_beads))))

gh <- if (is.list(qc)) qc$gate_hierarchy else NULL      # item 6: openCyto hierarchy (opt-in)
if (is.list(gh) && length(gh)) {
  per_pop <- safe({
    cnts <- gh$counts
    if (is.data.frame(cnts) && nrow(cnts)) {
      agg <- aggregate(cbind(count = cnts$count, pct_of_parent = cnts$pct_of_parent),
                       by = list(population = cnts$population),
                       FUN = function(z) mean(z, na.rm = TRUE))
      agg <- agg[order(nchar(gsub("[^/]", "", agg$population))), ]
      lapply(seq_len(nrow(agg)), function(i) list(
        population = agg$population[i], mean_count = naify(num(agg$count[i], 1)),
        mean_pct_parent = naify(num(agg$pct_of_parent[i], 1))))
    } else NULL
  })
  man$gate_hierarchy <- list(
    engine = naify(safe(gh$engine)), template = naify(safe(basename(as.character(gh$template)))),
    populations = naify(safe(paste(gh$populations, collapse = " -> "))),
    terminal = naify(safe(gh$terminal)), n_samples = naify(safe(as.integer(gh$n_samples))),
    total_input = naify(safe(as.integer(gh$total_input))),
    total_retained = naify(safe(as.integer(gh$total_retained))),
    pct_removed = naify(num(gh$pct_removed, 1)),
    hierarchy_file = naify(safe(basename(as.character(gh$hierarchy_file)))))
  if (!is.null(per_pop) && length(per_pop)) man$gate_hierarchy$per_population <- per_pop
}
loginfo("v2.2.0 QC fields: gate_engine=%s | time_qc=%s | comp_diag=%s | harmonize=%s | cytof_norm=%s | gate_hierarchy=%s",
        man$gate_engine,
        ifelse(!is.null(man$time_qc), man$time_qc$mode, "n/a"),
        ifelse(!is.null(man$compensation_diag), man$compensation_diag$verdict, "n/a"),
        ifelse(!is.null(man$harmonization), man$harmonization$scope, "n/a"),
        ifelse(!is.null(man$cytof_norm), as.character(man$cytof_norm$applied), "n/a"),
        ifelse(!is.null(man$gate_hierarchy), man$gate_hierarchy$engine, "n/a"))

# ---- diff_abundance{} (only if 06 produced diff_abundance.rds) ------------------------
dpath <- file.path(OUT, "diff_abundance.rds")
if (file.exists(dpath)) {
  d <- safe(readRDS(dpath))
  if (is.list(d) && !is.null(d$mode)) {
    da <- list(mode = naify(as.character(d$mode)))
    if (!is.null(d$reason)) da$reason <- naify(as.character(d$reason))   # descriptive_only mode
    if (!is.null(d$design)) da$design <- naify(as.character(d$design))   # tested mode
    man$diff_abundance <- da
    loginfo("diff_abundance{}: mode=%s", da$mode)
  }
} else loginfo("No diff_abundance.rds -> differential-abundance section omitted (honest).")

# ---- write ----------------------------------------------------------------------------
out_json <- file.path(OUT, "run_manifest.json")
writeLines(jsonlite::toJSON(man, auto_unbox = TRUE, pretty = TRUE, null = "null"), out_json)

# Every reportable scalar and execution state is exported with its source artifact. This table is
# the reconciliation ledger used by the report builder and by acceptance evaluation.
claim_rows <- list()
add_claim <- function(name, value, source_artifact, source_field) {
  claim_rows[[length(claim_rows) + 1L]] <<- data.frame(
    claim = name, value = paste(value, collapse = ";"), source_artifact = source_artifact,
    source_field = source_field, stringsAsFactors = FALSE)
}
for (field in setdiff(names(man), c("analysis_state", "diff_abundance", "rare_population",
                                    "time_qc", "compensation_diag", "harmonization", "cytof_norm", "gate_hierarchy")))
  add_claim(field, man[[field]], "run_manifest.json", field)
for (stage in names(analysis_state)) {
  source_artifact <- if (grepl("receipt$", stage))
    paste0(sub("_receipt$", "", stage), "_review_receipt_state.csv") else
    switch(stage, gating = "gating_state.csv", qc_uncertainty = "qc_uncertainty.csv",
           environment = "environment_state.csv", controls = "control_usage_state.csv",
           transform = "transform_contract.csv", lineage_bias = "lineage_bias_state.csv",
           resolution = "resolution_state.csv", rare_population = "rare_population_state.csv",
           rare_population_selection = "rare_population_state.csv",
           rare_population_identity = "rare_population_state.csv",
           annotation = "annotation_state.csv", manual_reconciliation = "manual_reconciliation_state.csv")
  source_field <- switch(stage, rare_population_selection = "selection_state",
                         rare_population_identity = "identity_validation_state", "state")
  if (is.null(source_artifact) || !file.exists(file.path(OUT, source_artifact))) {
    source_artifact <- "run_manifest.json"
    source_field <- paste0("analysis_state.", stage)
  }
  add_claim(paste0("state.", stage), analysis_state[[stage]], source_artifact, source_field)
}
if (is.list(man$rare_population)) for (field in names(man$rare_population))
  add_claim(paste0("rare_population.", field), man$rare_population[[field]],
            if (field == "sample_rows") "rare_population_assignments.csv" else "rare_population_state.csv",
            field)
if (is.list(man$time_qc)) for (field in names(man$time_qc))
  add_claim(paste0("time_qc.", field), man$time_qc[[field]], "time_qc_diagnostics.csv",
            switch(field, backend = "backend_used", requested_backend = "requested_backend",
                   removed = "removed_events", field))
write.csv(do.call(rbind, claim_rows), file.path(OUT, "report_claims.csv"), row.names = FALSE)

artifact_files <- list.files(OUT, recursive = TRUE, full.names = TRUE)
artifact_files <- artifact_files[file.info(artifact_files)$isdir %in% FALSE]
artifact_files <- artifact_files[basename(artifact_files) != "artifact_manifest.csv"]
artifact_manifest <- data.frame(
  artifact = substring(artifact_files, nchar(normalizePath(OUT)) + 2L),
  bytes = as.numeric(file.info(artifact_files)$size),
  sha256 = vapply(artifact_files, review_sha256_file, character(1)), stringsAsFactors = FALSE)
write.csv(artifact_manifest, file.path(OUT, "artifact_manifest.csv"), row.names = FALSE)
loginfo("Wrote %s (%d top-level keys; diff_abundance=%s)",
        out_json, length(man),
        ifelse(!is.null(man$diff_abundance), "yes", "no"))
loginfo("=== build_manifest.R complete ===")
