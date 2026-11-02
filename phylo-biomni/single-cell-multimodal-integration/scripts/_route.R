# ============================================================================
# _route.R  —  deterministic input-regime router for single-cell multimodal
# integration. Reciprocal route partner: scatac-multiome-analysis.
#
# WHY THIS EXISTS (see SKILL.md "Why X, not Y"): choosing an integration method
# before establishing the design regime is single-cell multimodal analysis's most
# common analytical error. This router refuses to integrate until it has
# *established* the regime from the actual feature types and pairing structure.
# Genuinely paired RNA+ATAC multiome is routed to scatac-multiome-analysis; anything it cannot
# establish becomes an explicit STOP, never a guess.
#
# Two layers, deliberately separated so the decision logic is testable without
# any heavy I/O dependency:
#   * resolve_route(sig)          -- PURE base-R decision function (unit-tested)
#   * extract_route_signals(cfg)  -- reads .h5 / metadata via a Python probe
# ============================================================================

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0 || (length(a)==1 && is.na(a))) b else a
.g <- function(sig, key, default) { v <- sig[[key]]; if (is.null(v)) default else v }

# Canonical route and handoff strings used by every production entrypoint.
ROUTE_CITESEQ     <- "citeseq"
ROUTE_UNPAIRED    <- "unpaired"
ROUTE_MOSAIC      <- "mosaic"
ROUTE_HANDOFF     <- "handoff"
ROUTE_STOP        <- "stop"
ROUTE_TARGET_SCATAC <- "scatac-multiome-analysis"
ROUTE_MESSAGE_SCATAC <- paste(
  "Route to scatac-multiome-analysis: paired 10x RNA-ATAC multiome should use that",
  "skill's joint route, not this multimodal-integration skill.")

.decision <- function(route, reason, stop = FALSE, handoff_target = NA_character_, detail = list()) {
  list(route = route, reason = reason, stop = stop, handoff_target = handoff_target, detail = detail)
}

# ---------------------------------------------------------------------------
# resolve_route(sig): PURE. Given a normalized signal list, return the route.
# Recognised sig fields (all optional; safe defaults applied):
#   route_override         "auto"|"citeseq"|"unpaired"|"mosaic"|"handoff"|"stop"
#   feature_types          character vector of unique 10x feature_type values
#   has_combined_matrix    logical  (filtered_feature_bc_matrix.h5 present)
#   has_rna_only_matrix    logical  (separate rna_matrix.h5 present)
#   has_fragments          logical  (atac_fragments.tsv.gz present)
#   separate_rna_atac      logical  (independent RNA + ATAC inputs)
#   regime_hint            character ("mosaic" forces the mosaic route)
#   declared_regime        character (metadata 'assay'; used for identity reporting)
#   isotype_controls_present logical
#   barcode_overlap        numeric [0,1] shared-barcode fraction (separate inputs)
#   mosaic_min_overlap     numeric threshold (default 0.1)
# ---------------------------------------------------------------------------
resolve_route <- function(sig) {
  ov  <- .g(sig, "route_override", "auto")
  ft  <- as.character(.g(sig, "feature_types", character(0)))
  has_gex   <- "Gene Expression" %in% ft
  has_adt   <- "Antibody Capture" %in% ft
  has_peaks <- "Peaks" %in% ft
  has_comb  <- isTRUE(.g(sig, "has_combined_matrix", FALSE))
  separate  <- isTRUE(.g(sig, "separate_rna_atac", FALSE))
  hint      <- .g(sig, "regime_hint", "")

  # ---- explicit overrides ----
  if (identical(ov, "stop"))
    return(.decision(ROUTE_STOP, "route_override=stop set in config.", stop = TRUE))
  if (identical(ov, "handoff"))
    return(.decision(ROUTE_HANDOFF, paste("route_override=handoff.", ROUTE_MESSAGE_SCATAC),
                     stop = TRUE, handoff_target = ROUTE_TARGET_SCATAC))
  if (identical(ov, "mosaic"))
    return(.decision(ROUTE_STOP,
                     "route_override=mosaic requests StabMap, which is unsupported until a real shared-feature bridge is implemented.",
                     stop = TRUE))
  if (identical(ov, "unpaired"))
    return(.decision(ROUTE_STOP,
                     "route_override=unpaired requests GLUE, which is unsupported until a real guidance graph and label-transfer model are implemented.",
                     stop = TRUE))
  if (identical(ov, "citeseq")) {
    if (has_comb && has_gex && has_adt && !has_peaks)
      return(.decision(ROUTE_CITESEQ, "route_override=citeseq; GEX+Antibody Capture confirmed."))
    return(.decision(ROUTE_STOP,
                     "route_override=citeseq but the matrix is not a paired RNA+ADT (Gene Expression + Antibody Capture, no Peaks) bundle.",
                     stop = TRUE))
  }

  # ---- auto-detection of the design regime ----
  # Trimodal (RNA + protein + ATAC): out of scope for v1.
  if (has_gex && has_adt && has_peaks)
    return(.decision(ROUTE_STOP,
                     "Trimodal bundle (Gene Expression + Antibody Capture + Peaks) detected; trimodal integration is a documented out-of-scope future mode.",
                     stop = TRUE))
  if (has_comb) {
    # CITE-seq: RNA + ADT, no chromatin.
    if (has_gex && has_adt && !has_peaks)
      return(.decision(ROUTE_CITESEQ,
                       "Paired CITE-seq established (Gene Expression + Antibody Capture, no Peaks) -> WNN + totalVI route.",
                       detail = list(isotype_controls_present = isTRUE(.g(sig, "isotype_controls_present", FALSE)))))
    # Paired RNA+ATAC multiome: route to the dedicated skill.
    if (has_gex && has_peaks && !has_adt)
      return(.decision(ROUTE_HANDOFF,
                       paste("Combined Gene Expression + Peaks matrix (paired RNA+ATAC multiome).", ROUTE_MESSAGE_SCATAC),
                       stop = TRUE, handoff_target = ROUTE_TARGET_SCATAC))
    # scRNA-only combined matrix.
    if (has_gex && !has_adt && !has_peaks)
      return(.decision(ROUTE_STOP,
                       "Combined matrix contains only Gene Expression (no Antibody Capture, no Peaks) - this is scRNA-seq. Use a scRNA-seq skill.",
                       stop = TRUE))
    return(.decision(ROUTE_STOP,
                     "Combined matrix feature types are not a supported multimodal regime (need RNA+ADT for CITE-seq).",
                     stop = TRUE))
  }

  # ---- separate (unpaired/mosaic) inputs ----
  if (separate || isTRUE(.g(sig, "has_rna_only_matrix", FALSE))) {
    ov_bc  <- suppressWarnings(as.numeric(.g(sig, "barcode_overlap", 0)))
    min_bc <- suppressWarnings(as.numeric(.g(sig, "mosaic_min_overlap", 0.1)))
    if (identical(tolower(hint), "mosaic"))
      return(.decision(ROUTE_STOP,
                       "Mosaic regime detected, but StabMap is unsupported until a real shared-feature bridge is implemented.",
                       stop = TRUE, detail = list(barcode_overlap = ov_bc)))
    if (!is.na(ov_bc) && ov_bc > min_bc && ov_bc < 0.95)
      return(.decision(ROUTE_STOP,
                       sprintf("Partial barcode overlap %.3f indicates mosaic data, but StabMap is unsupported until a real shared-feature bridge is implemented.", ov_bc),
                       stop = TRUE, detail = list(barcode_overlap = ov_bc)))
    return(.decision(ROUTE_STOP,
                     "Separate RNA and ATAC inputs detected, but GLUE is unsupported until a real guidance graph and label-transfer model are implemented.",
                     stop = TRUE, detail = list(barcode_overlap = ov_bc)))
  }

  .decision(ROUTE_STOP,
            "No supported multimodal input found. Provide a CITE-seq filtered_feature_bc_matrix.h5 (Gene Expression + Antibody Capture), or separate RNA and ATAC inputs.",
            stop = TRUE)
}

# ---------------------------------------------------------------------------
# extract_route_signals(cfg): reads the actual inputs into a signal list via a
# bundled Python probe (scripts/_io.py --probe), so routing does not depend on
# an R HDF5 reader. Degrades to explicit safe defaults (never a fabricated
# value) when something cannot be read, so resolve_route() STOPs rather than guess.
# ---------------------------------------------------------------------------
extract_route_signals <- function(cfg) {
  here <- tryCatch(dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1])),
                   error = function(e) ".")
  probe <- file.path(here, "_io.py")
  indir <- cfg$input$dir %||% cfg$input$data_dir %||% "."
  py <- Sys.getenv("PYTHON", "python3")
  out <- tryCatch(system2(py, c(shQuote(probe), "--probe", shQuote(indir)), stdout = TRUE, stderr = FALSE),
                  error = function(e) NULL)
  sig <- list(route_override = cfg$route %||% cfg$multimodal$route %||% "auto")
  if (!is.null(out) && length(out)) {
    parsed <- tryCatch(jsonlite::fromJSON(paste(out, collapse = "\n"), simplifyVector = TRUE),
                       error = function(e) NULL)
    if (!is.null(parsed)) for (k in names(parsed)) sig[[k]] <- parsed[[k]]
  }
  if (is.null(sig$feature_types)) sig$feature_types <- character(0)
  sig$feature_types <- as.character(sig$feature_types)
  sig
}
