#!/usr/bin/env Rscript
# ============================================================================
# Stage 00 — input-regime router (runs FIRST, gates everything downstream).
# Establishes the design regime BEFORE any integration method is chosen, writes
# route_decision.json, and STOPS the pipeline (non-zero exit) on a stop/handoff
# so no fabricated joint result is ever produced on non-paired data.
#   proceed -> exit 0     stop -> exit 2     alternate supported skill -> exit 3
# ============================================================================
here <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]))
if (is.na(here) || !nzchar(here)) here <- "."
source(file.path(here, "_common.R"))
source(file.path(here, "genome_identity.R"))
source(file.path(here, "_route.R"))

cfg <- load_config()
ensure_dirs(cfg)

log_msg("Stage 00: resolving input regime / route ...")
sig <- tryCatch(extract_route_signals(cfg), error = function(e) {
  log_msg("Signal extraction error:", conditionMessage(e)); NULL
})
if (is.null(sig)) {
  dec <- list(route = "stop", stop = TRUE, handoff_target = NA,
              reason = "Could not read inputs to establish the regime; refusing to guess. Check paths in config.yaml.")
} else {
  dec <- resolve_route(sig)
}

# Persist the decision (machine-readable) to both working + results dirs.
decision_out <- list(
  route = dec$route, stop = isTRUE(dec$stop), handoff_target = dec$handoff_target,
  reason = dec$reason, detail = dec$detail,
  signals = if (is.null(sig)) list() else sig[c("route_override","multiome_flag","joint_flag",
    "has_fragments","has_tbi","has_combined_matrix","feature_types","separate_rna_atac",
    "barcode_overlap","min_barcode_overlap","genome_agree","genome_identity",
    "sample_ids_agree","require_same_sample")],
  seed = cfg$project$seed %||% 1234
)
writeLines(jsonlite::toJSON(decision_out, auto_unbox = TRUE, pretty = TRUE, null = "null"),
           ckpt(cfg, "route_decision.json"))
dir.create(cfg$project$results_dir, recursive = TRUE, showWarnings = FALSE)
writeLines(jsonlite::toJSON(decision_out, auto_unbox = TRUE, pretty = TRUE, null = "null"),
           file.path(cfg$project$results_dir, "route_decision.json"))

cat("\n=====================================================================\n")
cat("ROUTE:", dec$route, "\n")
cat("REASON:", dec$reason, "\n")
if (!is.na(dec$handoff_target)) cat("HANDOFF TARGET:", dec$handoff_target, "\n")
cat("=====================================================================\n\n")

if (identical(dec$route, "handoff")) {
  log_msg("STOP + HANDOFF. Not a paired 10x RNA-ATAC multiome bundle. See route_decision.json.")
  quit(status = 3)
}
if (isTRUE(dec$stop)) {
  log_msg("STOP. The bundle did not satisfy the paired-multiome preconditions. See route_decision.json.")
  quit(status = 2)
}
log_msg("Route resolved:", dec$route, "- proceeding.")
