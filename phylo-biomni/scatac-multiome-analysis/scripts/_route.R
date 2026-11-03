# ============================================================================
# _route.R  —  deterministic input-regime router for the multiome extension.
#
# WHY THIS EXISTS (see SKILL.md "Why X, not Y"): choosing an integration method
# before establishing the design regime is single-cell multimodal analysis's most
# common analytical error. This router refuses to run a joint integration until it
# has *established* that the bundle is genuinely paired (same cells), on one
# genome, from consistent samples. Unpaired RNA+ATAC and CITE-seq are directed
# to single-cell-multimodal-integration; anything uncertain becomes an explicit STOP.
#
# Two layers, deliberately separated so the decision logic is testable without
# any heavy I/O dependency:
#   * resolve_route(sig)          -- PURE base-R decision function (unit-tested)
#   * extract_route_signals(cfg)  -- reads .h5 / fragments / metadata (needs hdf5r)
# ============================================================================

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0 || (length(a)==1 && is.na(a))) b else a
.g <- function(sig, key, default) { v <- sig[[key]]; if (is.null(v)) default else v }

# Canonical route/handoff strings (asserted verbatim by the eval suite).
ROUTE_ATAC_ONLY        <- "atac_only"
ROUTE_MULTIOME_ANNOT   <- "multiome_annotate"
ROUTE_JOINT            <- "joint"
ROUTE_HANDOFF          <- "handoff"
ROUTE_STOP             <- "stop"
HANDOFF_TARGET         <- "single-cell-multimodal-integration"
HANDOFF_MESSAGE        <- "Use single-cell-multimodal-integration: this bundle is not paired 10x RNA-ATAC multiome."

.decision <- function(route, reason, stop = FALSE, handoff_target = NA_character_, detail = list()) {
  list(route = route, reason = reason, stop = stop, handoff_target = handoff_target, detail = detail)
}

.genome_gate_decision <- function(sig) {
  if (isTRUE(.g(sig, "genome_agree", FALSE))) return(NULL)
  genome_identity <- .g(sig, "genome_identity", list(comparison_status = "not_evaluable"))
  reason <- if (identical(genome_identity$comparison_status, "not_evaluable")) {
    "Genome identity is not evaluable from an independent matrix, metadata, or explicit input-build declaration; refusing to infer hg19/hg38 from shared chr* style."
  } else {
    "Independent input genome identity disagrees with the configured annotation build or fragment seqlevel style."
  }
  .decision(ROUTE_STOP, reason, stop = TRUE,
            detail = list(genome_identity = genome_identity))
}

# ---------------------------------------------------------------------------
# resolve_route(sig): PURE. Given a normalized signal list, return the route.
# Recognised sig fields (all optional; safe defaults applied):
#   route_override      "auto"|"atac_only"|"multiome_annotate"|"joint"|"stop"
#   multiome_flag       logical  (config input.multiome)
#   joint_flag          logical  (config multiome.joint)
#   has_fragments       logical
#   has_tbi             logical
#   has_combined_matrix logical  (filtered_feature_bc_matrix.h5 present)
#   feature_types       character vector of unique 10x feature_type values
#   separate_rna_atac   logical  (independent RNA + ATAC inputs => diagonal)
#   barcode_overlap     numeric [0,1] shared-barcode fraction (matrix vs fragments)
#   min_barcode_overlap numeric threshold (default 0.95)
#   genome_agree        logical  (independent input build + seqlevel style vs configured resources)
#   sample_ids_agree    logical
#   require_same_sample logical  (default TRUE)
# ---------------------------------------------------------------------------
resolve_route <- function(sig) {
  ov  <- .g(sig, "route_override", "auto")
  ft  <- .g(sig, "feature_types", character(0))
  has_gex   <- "Gene Expression" %in% ft
  has_peaks <- "Peaks" %in% ft
  has_adt   <- "Antibody Capture" %in% ft
  joint_flag <- isTRUE(.g(sig, "joint_flag", FALSE))
  multiome_flag <- isTRUE(.g(sig, "multiome_flag", FALSE))

  # ---- explicit overrides (still safety-gated for joint) ----
  if (identical(ov, "stop"))
    return(.decision(ROUTE_STOP, "route_override=stop set in config.", stop = TRUE))
  if (identical(ov, "atac_only")) {
    genome_stop <- .genome_gate_decision(sig)
    if (!is.null(genome_stop)) return(genome_stop)
    return(.decision(ROUTE_ATAC_ONLY, "route_override=atac_only."))
  }
  if (identical(ov, "multiome_annotate")) {
    genome_stop <- .genome_gate_decision(sig)
    if (!is.null(genome_stop)) return(genome_stop)
    return(.decision(ROUTE_MULTIOME_ANNOT, "route_override=multiome_annotate."))
  }
  requested_joint <- identical(ov, "joint")

  # ---- auto-detection of the design regime ----
  if (!requested_joint) {
    # CITE-seq / trimodal: any Antibody Capture modality is out of scope here.
    if (has_adt)
      return(.decision(ROUTE_HANDOFF,
                       paste("Antibody Capture modality detected (CITE-seq / trimodal).", HANDOFF_MESSAGE),
                       stop = TRUE, handoff_target = HANDOFF_TARGET))
    # Two independent single-modality inputs => diagonal / unpaired.
    if (isTRUE(.g(sig, "separate_rna_atac", FALSE)))
      return(.decision(ROUTE_HANDOFF,
                       paste("Separate (unpaired) RNA and ATAC inputs => diagonal integration.", HANDOFF_MESSAGE),
                       stop = TRUE, handoff_target = HANDOFF_TARGET))
    if (isTRUE(.g(sig, "has_combined_matrix", FALSE)) && has_gex && !has_peaks && !has_adt)
      return(.decision(ROUTE_STOP,
                       "Combined matrix contains only Gene Expression (no Peaks) - this is scRNA-seq, not scATAC/multiome. Use a scRNA-seq skill.",
                       stop = TRUE))
    if (isTRUE(.g(sig, "has_combined_matrix", FALSE)) && has_gex && has_peaks) {
      # 10x multiome present. joint_flag opts in to joint analysis; otherwise
      # preserve the existing RNA-assisted ATAC annotation behavior.
      if (!joint_flag) {
        genome_stop <- .genome_gate_decision(sig)
        if (!is.null(genome_stop)) return(genome_stop)
        return(.decision(ROUTE_MULTIOME_ANNOT,
                         "10x multiome (GEX+Peaks) detected; multiome.joint not set -> existing RNA-assisted ATAC annotation route (preserved)."))
      }
      # else fall through to the joint safety gate below.
    } else {
      # No combined GEX+Peaks matrix: fall back to the ATAC primitive.
      if (isTRUE(.g(sig, "has_fragments", FALSE))) {
        genome_stop <- .genome_gate_decision(sig)
        if (!is.null(genome_stop)) return(genome_stop)
        return(.decision(ROUTE_ATAC_ONLY,
                         "Fragments present, no paired GEX+Peaks matrix -> scATAC-only route (preserved)."))
      }
      return(.decision(ROUTE_STOP,
                       "No ATAC fragments file found; cannot run. Provide atac_fragments.tsv.gz (+ .tbi).",
                       stop = TRUE))
    }
  }

  # ---- JOINT safety gate (auto-selected joint OR override=joint) ----
  # Every check below must PASS or the run STOPS with the specific reason.
  if (!isTRUE(.g(sig, "has_fragments", FALSE)))
    return(.decision(ROUTE_STOP, "Joint route requires an ATAC fragments file; none found.", stop = TRUE))
  if (!isTRUE(.g(sig, "has_tbi", FALSE)))
    return(.decision(ROUTE_STOP, "ATAC fragments Tabix index (.tbi) missing; required for fragment-based QC/quantification.", stop = TRUE))
  if (has_adt)
    return(.decision(ROUTE_HANDOFF,
                     paste("Antibody Capture modality present; joint RNA-ATAC route cannot process protein.", HANDOFF_MESSAGE),
                     stop = TRUE, handoff_target = HANDOFF_TARGET))
  if (!(has_gex && has_peaks))
    return(.decision(ROUTE_STOP,
                     "Joint route requires a combined matrix with both Gene Expression and Peaks feature types.", stop = TRUE))
  ov_bc  <- suppressWarnings(as.numeric(.g(sig, "barcode_overlap", NA_real_)))
  min_bc <- suppressWarnings(as.numeric(.g(sig, "min_barcode_overlap", 0.95)))
  if (is.na(ov_bc))
    return(.decision(ROUTE_STOP,
                     "Could not establish barcode pairing between the matrix and fragments; refusing to assume the bundle is paired.", stop = TRUE))
  if (ov_bc < min_bc)
    return(.decision(ROUTE_STOP,
                     sprintf("Bundle is not truly paired: matrix<->fragments barcode overlap %.3f < min_barcode_overlap %.3f.", ov_bc, min_bc),
                     stop = TRUE, detail = list(barcode_overlap = ov_bc, min_barcode_overlap = min_bc)))
  genome_stop <- .genome_gate_decision(sig)
  if (!is.null(genome_stop)) return(genome_stop)
  if (isTRUE(.g(sig, "require_same_sample", TRUE)) && !isTRUE(.g(sig, "sample_ids_agree", TRUE)))
    return(.decision(ROUTE_STOP,
                     "Sample identities disagree across modalities in metadata.csv; cannot treat as one paired sample.", stop = TRUE))

  .decision(ROUTE_JOINT,
            sprintf("Paired 10x RNA-ATAC multiome established (barcode overlap %.3f >= %.3f; genome + sample identity agree) -> joint WNN route.",
                    ov_bc, min_bc),
            detail = list(barcode_overlap = ov_bc, min_barcode_overlap = min_bc))
}

# ---------------------------------------------------------------------------
# extract_route_signals(cfg): reads the actual inputs into a signal list.
# Uses hdf5r to read 10x feature types + barcodes; scans a bounded prefix of the
# fragments file for barcodes; reads metadata.csv for sample identity. Degrades
# to explicit NA (never a fabricated value) when something cannot be read, so
# resolve_route() will STOP rather than guess.
# ---------------------------------------------------------------------------
.read_10x_h5_meta <- function(h5_path, frag_sample_max = 2e6L) {
  if (!requireNamespace("hdf5r", quietly = TRUE))
    stop("hdf5r required to read ", h5_path, " (conda install -c conda-forge r-hdf5r).")
  f <- hdf5r::H5File$new(h5_path, mode = "r")
  on.exit(f$close_all(), add = TRUE)
  # 10x CellRanger v3 layout: /matrix/features/feature_type , /matrix/barcodes
  ft <- tryCatch(unique(as.character(f[["matrix/features/feature_type"]][])), error = function(e) character(0))
  bc <- tryCatch(as.character(f[["matrix/barcodes"]][]), error = function(e) character(0))
  genomes <- tryCatch(unique(as.character(f[["matrix/features/genome"]][])),
                      error = function(e) character(0))
  list(feature_types = ft, barcodes = bc, genomes = genomes)
}

.scan_fragment_barcodes <- function(frag_path, max_lines = 3e6L) {
  # Read a bounded prefix; fragments are sorted but a prefix still covers many
  # barcodes. Returns unique barcodes seen (column 4). NULL on failure.
  con <- tryCatch(gzfile(frag_path, "r"), error = function(e) NULL)
  if (is.null(con)) return(NULL)
  on.exit(close(con), add = TRUE)
  seen <- new.env(parent = emptyenv()); n <- 0L
  repeat {
    ln <- readLines(con, n = 1e5L)
    if (length(ln) == 0L) break
    ln <- ln[!startsWith(ln, "#")]
    if (length(ln)) {
      parts <- strsplit(ln, "\t", fixed = TRUE)
      bcs <- vapply(parts, function(p) if (length(p) >= 4) p[[4]] else NA_character_, character(1))
      for (b in bcs[!is.na(bcs)]) assign(b, TRUE, envir = seen)
    }
    n <- n + length(ln)
    if (n >= max_lines) break
  }
  ls(seen)
}

.scan_fragment_seqlevels <- function(frag_path, max_lines = 10000L) {
  con <- tryCatch(gzfile(frag_path, "r"), error = function(e) NULL)
  if (is.null(con)) return(character(0))
  on.exit(close(con), add = TRUE)
  observed <- character(0)
  n <- 0L
  repeat {
    ln <- readLines(con, n = min(1000L, max_lines - n))
    if (!length(ln)) break
    body <- ln[!startsWith(ln, "#")]
    if (length(body)) observed <- union(observed, sub("\t.*$", "", body))
    n <- n + length(ln)
    if (n >= max_lines) break
  }
  observed
}

extract_route_signals <- function(cfg) {
  inp <- cfg$input
  frag <- inp$multiome_atac_fragments %||% inp$fragments
  comb <- inp$multiome_matrix_h5
  sig <- list(
    route_override      = (cfg$multiome$route %||% "auto"),
    multiome_flag       = isTRUE(inp$multiome),
    joint_flag          = isTRUE(cfg$multiome$joint),
    has_fragments       = !is.null(frag) && file.exists(frag),
    has_tbi             = !is.null(frag) && file.exists(paste0(frag, ".tbi")),
    has_combined_matrix = !is.null(comb) && file.exists(comb),
    separate_rna_atac   = (!is.null(inp$rna_matrix_h5) && file.exists(inp$rna_matrix_h5 %||% "")) &&
                          (!is.null(inp$fragments) && is.null(comb)),
    min_barcode_overlap = (cfg$pairing$min_barcode_overlap %||% 0.95),
    require_same_sample = (cfg$pairing$require_same_sample %||% TRUE),
    feature_types       = character(0),
    matrix_genomes      = character(0),
    barcode_overlap     = NA_real_,
    genome_agree        = TRUE,
    sample_ids_agree    = TRUE
  )
  if (sig$has_combined_matrix) {
    meta <- tryCatch(.read_10x_h5_meta(comb), error = function(e) { message(conditionMessage(e)); NULL })
    if (!is.null(meta)) {
      sig$feature_types <- meta$feature_types
      sig$matrix_genomes <- meta$genomes
      if (sig$has_fragments && length(meta$barcodes)) {
        fb <- .scan_fragment_barcodes(frag)
        if (!is.null(fb) && length(fb))
          sig$barcode_overlap <- mean(meta$barcodes %in% fb)
      }
    }
  }
  # Genome agreement is an executed, structured comparison. It checks the
  # configured registry triad, observed fragment seqlevels, and any metadata build.
  if (sig$has_fragments) {
    fragment_seqlevels <- .scan_fragment_seqlevels(frag)
    metadata_builds <- character(0)
    if (!is.null(cfg$input$metadata_csv) && file.exists(cfg$input$metadata_csv %||% "")) {
      md <- tryCatch(utils::read.csv(cfg$input$metadata_csv, stringsAsFactors = FALSE), error = function(e) NULL)
      if (!is.null(md) && "genome" %in% tolower(names(md))) {
        gcol <- names(md)[tolower(names(md)) == "genome"][1]
        metadata_builds <- unique(as.character(md[[gcol]]))
      }
      if (!is.null(md)) {
        # sample identity: if both rna_sample and atac_sample columns exist, compare
        lc <- tolower(names(md))
        if (all(c("rna_sample","atac_sample") %in% lc)) {
          rs <- md[[names(md)[lc=="rna_sample"][1]]]; as_ <- md[[names(md)[lc=="atac_sample"][1]]]
          sig$sample_ids_agree <- all(as.character(rs) == as.character(as_))
        }
      }
    }
    build <- cfg$genome$build %||% "hg38"
    registry_resources <- GENOME_REGISTRY[[build]] %||% list()
    sig$genome_identity <- evaluate_genome_identity(
      configured_build = build,
      registry_resources = registry_resources[c("ensdb", "bsgenome", "blacklist")],
      input_build_evidence = list(
        matrix_feature_genome = sig$matrix_genomes,
        metadata_genome = metadata_builds,
        explicit_input_build = cfg$genome$input_build %||% character(0)
      ),
      fragment_seqlevels = fragment_seqlevels
    )
    sig$genome_agree <- identical(sig$genome_identity$comparison_status, "passed") &&
      identical(sig$genome_identity$identity_violations, 0L)
  } else {
    sig$genome_identity <- list(
      configured_build = cfg$genome$build %||% "hg38",
      comparison_status = "not_evaluable",
      identity_violations = 1L,
      violations = list(list(
        field = "fragment_seqlevels", expected = "nonempty UCSC-style seqlevels",
        observed = "<missing>", reason = "ATAC fragments file is unavailable"
      ))
    )
    sig$genome_agree <- FALSE
  }
  sig
}
