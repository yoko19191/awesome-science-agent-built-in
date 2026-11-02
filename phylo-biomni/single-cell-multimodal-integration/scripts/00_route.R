#!/usr/bin/env Rscript
# ============================================================================
# 00_route.R — establish the design regime FIRST, then select the route.
# Writes checkpoints/route_decision.json. Exit codes let run_all.sh halt:
#   0 = proceed (citeseq only)
#   2 = STOP     (scRNA-only, trimodal, unsupported)
#   3 = ROUTE  (paired RNA+ATAC multiome -> scatac-multiome-analysis)
# The route is resolved from the ACTUAL feature types/pairing, never assumed.
# ============================================================================
here <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]))
suppressMessages({
  source(file.path(here, "_common.R"))
  source(file.path(here, "_route.R"))
})

selection_path <- Sys.getenv("SCMI_INPUT_SELECTION", "")
if (!nzchar(selection_path) || !file.exists(selection_path)) stop("sealed SCMI_INPUT_SELECTION is required")
selection <- jsonlite::fromJSON(selection_path)
if (!identical(selection$execution_mode, "analysis") || !identical(selection$package_data_used, FALSE)) stop("input selection does not satisfy schema v2")

cfg <- load_config()
cfg$input$dir <- input_dir(cfg)                 # resolve to an absolute bundle path
ensure_dirs(cfg)
Sys.setenv(PYTHON = py_bin())

log_msg("Probing input bundle:", cfg$input$dir)
sig <- extract_route_signals(cfg)
dec <- resolve_route(sig)

dec_out <- list(
  route = dec$route, reason = dec$reason, stop = isTRUE(dec$stop),
  handoff_target = dec$handoff_target,
  feature_types = as.character(sig$feature_types),
  declared_regime = sig$declared_regime %||% "",
  isotype_controls_present = isTRUE(sig$isotype_controls_present),
  barcode_overlap = sig$barcode_overlap %||% NA,
  detail = dec$detail
)
outp <- ckpt(cfg, "route_decision.json")
write_json(dec_out, outp)
log_msg("Route:", dec$route, "|", dec$reason)
log_msg("Wrote", outp)

if (identical(dec$route, ROUTE_HANDOFF)) {
  cat("\nROUTE ->", dec$handoff_target, "\n", dec$reason, "\n"); quit(status = 3)
}
if (isTRUE(dec$stop)) { cat("\nSTOP:", dec$reason, "\n"); quit(status = 2) }
quit(status = 0)
