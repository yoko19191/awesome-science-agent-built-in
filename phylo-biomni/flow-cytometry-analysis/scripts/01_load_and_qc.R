#!/usr/bin/env Rscript
# Load, transform, compensate, and run modality-aware QC before clustering.
# Default proposal mode writes review artifacts and stops; apply mode consumes a fingerprinted
# external decision receipt. See SKILL.md and references/qc_gating.md for the full contract.

suppressPackageStartupMessages({
  library(optparse); library(flowCore); library(CATALYST)
  library(SingleCellExperiment); library(ggplot2); library(matrixStats)
})

.args_all <- commandArgs(FALSE)
.this <- sub("^--file=", "", grep("^--file=", .args_all, value = TRUE))
SCRIPT_DIR <- if (length(.this)) dirname(normalizePath(.this[1])) else "scripts"
source(file.path(SCRIPT_DIR, "gating_engine.R"))
source(file.path(SCRIPT_DIR, "execution_contract.R"))
source(file.path(SCRIPT_DIR, "control_contract.R"))
source(file.path(SCRIPT_DIR, "review_contract.R"))
source(file.path(SCRIPT_DIR, "time_qc_stage.R"))

# ------------------------------------------------------------------ args
opt <- parse_args(OptionParser(option_list = list(
  make_option("--input", type = "character"),
  make_option("--metadata", type = "character", default = NA),
  make_option("--outdir", type = "character", default = "/mnt/results/cyto_run"),
  make_option("--modality", type = "character", default = "auto"),
  make_option("--spectral-state", type = "character", default = "auto", dest = "spectral_state",
              help = "auto | raw | unmixed  (override spectral-unmixing detection)"),
  make_option("--transform", type = "character", default = "auto"),
  make_option("--cofactor", type = "character", default = "auto"),
  make_option("--compensate", type = "character", default = "auto"),
  make_option("--qc", type = "character", default = "on"),
  make_option("--singlet-mad-k", type = "double", default = 4.0, dest = "singlet_mad_k",
              help = "PERMISSIVE-by-default singlet band width in MADs for the FSC-A/FSC-H ratio (default 4; larger = gentler). Tight singlet gates silently discard REAL cells and bias downstream populations -- err gentle."),
  make_option("--debris-pct", type = "double", default = 0.02, dest = "debris_pct",
              help = "Lower-percentile FSC/SSC debris floor (default 0.02 = gentle). Raise only with cause."),
  make_option("--overgate-alarm", type = "double", default = 0.30, dest = "overgate_alarm",
              help = "If scatter+singlet gates JOINTLY remove more than this fraction, raise a loud OVER-GATING alarm (default 0.30 = 30%)."),
  make_option("--viab-max-remove", type = "double", default = 0.50, dest = "viab_max_remove",
              help = "Safety cap on the live/dead gate: if the data-driven valley cutoff would remove more than this fraction of events, reject it and fall back to the conservative 95th-percentile cutoff (default 0.50 = 50%). Rationale: a viability valley that removes >50%% of events is almost certainly cutting into the live population (e.g. neutrophils with non-specific dye uptake), not separating live from dead. Set to 1.0 to disable. Override with --thresholds if the sample genuinely has >50%% dead cells."),
  # ---- data-driven / reviewable / multivariate gating (gating_engine.R) ----
  make_option("--gate-method", type = "character", default = "auto", dest = "gate_method",
              help = "Cutoff method for data-driven gates: valley|gmm|otsu|percentile|control|auto (default auto = valley with honest unimodal/shallow fallback to conservative percentile)."),
  make_option("--multivariate", type = "character", default = "on",
              help = "on|off: use 2D joint gates (debris FSC-A x SSC-A; live/dead viability x scatter or Pt x DNA) in addition to 1D cutoffs (default on)."),
  make_option("--gate-review", type = "character", default = "propose", dest = "gate_review",
              help = "propose|apply|auto. propose (default) writes template+figures then stops. apply requires edited --thresholds. auto continues only with no QC uncertainties; otherwise stops for reviewed apply. Both engines require a proposal and receipt for apply."),
  make_option("--thresholds", type = "character", default = NA,
              help = "Edited gating_thresholds CSV to APPLY (pass 2; final_cutoff/apply columns honored; sample_id=ALL broadcasts)."),
  make_option("--review-receipt", type = "character", default = NA, dest = "review_receipt",
              help = "JSON receipt binding an external user's decision to the proposed and applied thresholds."),
  make_option("--controls", type = "character", default = NA,
              help = "Controls CSV (channel,control_file,control_type{unstained|fmo},percentile) to anchor positive/negative cutoffs to unstained/FMO controls."),
  make_option("--valley-min-depth", type = "double", default = 0.10, dest = "valley_min_depth",
              help = "Minimum normalized valley depth (0-1) to TRUST a data-driven cutoff; below this -> REVIEW_shallow + conservative percentile (default 0.10)."),
  make_option("--dip-alpha", type = "double", default = 0.05, dest = "dip_alpha",
              help = "Hartigan dip-test alpha for the unimodality guard; unimodal -> no valley invented (default 0.05)."),
  make_option("--threshold-scope", type = "character", default = "per_sample", dest = "threshold_scope",
              help = "per_sample|pooled|batch. per_sample computes cutoffs per sample (correct for staining variation); pooled harmonizes to the across-sample median cutoff; batch harmonizes cutoffs WITHIN each metadata batch via confidence-weighted shrinkage (item 4). Default per_sample."),
  # ---- v2.2.0 item 4: batch-aware cutoff harmonization ----
  make_option("--harmonize-shrink", type = "double", default = 1.0, dest = "harmonize_shrink",
              help = "Max shrink weight toward the (gate,batch) consensus cutoff for --threshold-scope batch; effective weight = (1 - valley_confidence) * shrink. 0 = no harmonization, 1 = full (default 1.0)."),
  # ---- v2.2.0 item 1: time-based acquisition QC ----
  make_option("--time-qc", type = "character", default = "auto", dest = "time_qc",
              help = "off|report|remove|auto. Flow-rate + signal-stability + margin-event QC over the Time channel. auto = report when modality=flow, a Time channel exists and --qc on (diagnostics-on: flags + figures, removes 0 events); remove opts in to dropping flagged events (default auto)."),
  make_option("--time-qc-backend", type = "character", default = "native", dest = "time_qc_backend",
              help = "native. Built-in MAD time QC. Other backend names are rejected; no silent method substitution."),
  make_option("--time-qc-mad-k", type = "double", default = 5.0, dest = "time_qc_mad_k",
              help = "MAD multiplier for flagging unstable time-bins in native time-QC (default 5)."),
  # ---- v2.2.0 item 2: compensation / spillover diagnostics ----
  make_option("--spillover", type = "character", default = NA,
              help = "External compensation/spillover matrix CSV (channels x channels, header + rownames) to APPLY instead of the embedded matrix (flow). Aligned to data channels by intersection."),
  make_option("--spillover-kappa-max", type = "double", default = 1e3, dest = "spillover_kappa_max",
              help = "Condition-number (kappa) threshold above which a spillover matrix is flagged ILL-conditioned. Reported and STILL applied (diagnostics-on); default 1000."),
  # ---- v2.2.0 item 8: CyTOF bead normalization ----
  make_option("--cytof-norm", type = "character", default = "off", dest = "cytof_norm",
              help = "off|on|auto. CATALYST::normCytof bead-based signal normalization (CyTOF). auto = on when modality=cytof and a bead channel is present. When on, the threshold bead-removal gate is skipped so beads survive for normCytof (default off)."),
  make_option("--beads", type = "character", default = "dvs",
              help = "Bead type for normCytof: dvs (masses 140,151,153,165,175) or beta, or a comma-separated list of masses (default dvs)."),
  make_option("--cytof-norm-k", type = "integer", default = 500L, dest = "cytof_norm_k",
              help = "normCytof smoothing window k (default 500)."),
  # ---- v2.2.0 item 6: OpenCyto hierarchical gating (opt-in backend) ----
  make_option("--gate-engine", type = "character", default = "builtin", dest = "gate_engine",
              help = "builtin|opencyto. builtin = data-driven per-gate engine (default, v2.1.0 behavior). opencyto = openCyto/flowWorkspace GatingSet + gatingTemplate hierarchical gating (requires --gate-template or a shipped default)."),
  make_option("--gate-template", type = "character", default = NA, dest = "gate_template",
              help = "openCyto gatingTemplate CSV for --gate-engine opencyto (alias,pop,parent,dims,gating_method,gating_args,...). If omitted, a shipped default template for the detected modality is used."),
  make_option("--opencyto-terminal", type = "character", default = NA, dest = "opencyto_terminal",
              help = "Exact terminal population path or unique leaf name to retain when an openCyto template branches. Required for branched hierarchies."),
  # ---- viability x lineage-marker live/dead diagnostic ----
  make_option("--lineage-markers", type = "character", default = NA, dest = "lineage_markers",
              help = "Comma-separated exact or unambiguous $PnN/$PnS lineage markers. Flow/spectral runs use them to detect lineage-biased viability removal."),
  make_option("--lineage-bias-delta-pp", type = "double", default = 10, dest = "lineage_bias_delta_pp",
              help = "Review viability gating when removal in a confidently resolved lineage-positive group exceeds the negative group by this many percentage points (provisional default 10)."),
  make_option("--lineage-min-events", type = "integer", default = 100L, dest = "lineage_min_events",
              help = "Minimum events in each lineage-positive/negative group for the lineage-bias check (provisional default 100)."),
  make_option("--dna-channels", type = "character", default = NA, dest = "dna_channels",
              help = "One or two comma-separated exact $PnN/$PnS DNA channels for CyTOF QC. With two channels, the low bound requires both Ir signals and a separate DNA1 x DNA2 diagnostic is written."),
  make_option("--seed", type = "integer", default = 1234L)
)))
review_reject_output_as_input(opt$thresholds, file.path(opt$outdir, "gating_thresholds_applied.csv"))
set.seed(opt$seed)
dir.create(opt$outdir, recursive = TRUE, showWarnings = FALSE)
figdir <- file.path(opt$outdir, "figures"); dir.create(figdir, showWarnings = FALSE)
LOG <- file.path(opt$outdir, "qc_transform_log.txt")
logmsg <- function(...) { m <- sprintf(...); cat(m, "\n"); cat(m, "\n", file = LOG, append = TRUE) }
cat("", file = LOG)  # truncate
logmsg("=== 01_load_and_qc.R  |  seed=%d  |  %s ===", opt$seed, as.character(Sys.time()))
QC_UNCERTAINTIES <- list()
add_qc_uncertainty <- function(id, category, sample_id = "ALL", detail, consequence, recommendation) {
  QC_UNCERTAINTIES[[id]] <<- data.frame(
    uncertainty_id = id, category = category, sample_id = sample_id, detail = detail,
    consequence = consequence, recommendation = recommendation, stringsAsFactors = FALSE)
}

# ------------------------------------------------------------------ load
is_hd <- grepl("^HDCytoData:", opt$input, ignore.case = TRUE)
prov <- list()  # provenance read from metadata

if (is_hd) {
  ds <- sub("^HDCytoData:", "", opt$input, ignore.case = TRUE)
  loaded <- load_hdcyto_input(ds, opt$modality, logmsg)
  fs <- loaded$fs; md <- loaded$md; panel <- loaded$panel
  modality <- loaded$modality; prov <- loaded$provenance
  spectral <- FALSE
  raw_ff <- fs[[1]]
  det <- loaded$modality_detection
  if (det$cyt_empty)
    logmsg("NOTE: $CYT/$CYTSN keyword empty/missing; HDCytoData modality classified from channel/marker structure only (assumption logged).")
  logmsg("HDCytoData modality detection: %s | has_scatter=%s, mass_like_channels=%s, n_chan=%d, $CYT='%s'",
         modality, det$has_scatter, det$mass_like_n, det$n_chan, det$cyt)
} else {
  # ---- real FCS ----
  if (dir.exists(opt$input)) {
    fcs_files <- list.files(opt$input, pattern = "\\.fcs$", full.names = TRUE, ignore.case = TRUE)
  } else fcs_files <- opt$input
  if (length(fcs_files) == 0) stop("No .fcs files found at --input")
  logmsg("Loading %d FCS file(s)", length(fcs_files))
  fs <- flowCore::read.flowSet(files = fcs_files, transformation = FALSE, truncate_max_range = FALSE)
  raw_ff <- fs[[1]]
  # metadata
  if (!is.na(opt$metadata) && file.exists(opt$metadata)) {
    md_in <- read.csv(opt$metadata, stringsAsFactors = FALSE)
    md <- data.frame(file_name = md_in$file, sample_id = md_in$sample_id,
                     condition = if ("group" %in% names(md_in)) md_in$group else "sample",
                     batch = if ("batch" %in% names(md_in)) md_in$batch else NA,
                     patient = if ("patient" %in% names(md_in)) md_in$patient else NA,
                     stringsAsFactors = FALSE)
    prov$provenance <- "user FCS; provenance from metadata.csv / FCS keywords"
  } else {
    bn <- basename(flowCore::sampleNames(fs))
    md <- data.frame(file_name = bn, sample_id = tools::file_path_sans_ext(bn),
                     condition = "sample", batch = NA, patient = NA, stringsAsFactors = FALSE)
    prov$provenance <- "user FCS; no metadata.csv supplied"
  }
  # panel from FCS parameters
  pn <- flowCore::pData(flowCore::parameters(raw_ff))
  ag <- as.character(pn$desc); ag[is.na(ag) | ag == ""] <- as.character(pn$name)[is.na(ag) | ag == ""]
  # mark scatter/time/bead/viability/autofluorescence as non-type ("none"); everything else "type"
  # also check the antigen ($PnS) field, not just the channel name ($PnN),
  # so fluorophore-named viability channels (FVS780-A / "Viability") and autofluorescence
  # channels (AF-A) are correctly excluded from clustering features.
  nm <- as.character(pn$name)
  is_none_ch  <- grepl("FSC|SSC|Time|Event_length|Center|Offset|Width|Residual|bead|DNA|Ir19|Pt19|Live|Dead|Viability", nm, ignore.case = TRUE)
  is_none_ag  <- grepl("FSC|SSC|Time|Event_length|Center|Offset|Width|Residual|bead|DNA|Ir19|Pt19|Live|Dead|Viability|FVS|Zombie|7AAD|7-AAD|Aqua|Ghost|eFluor.?506|^PI$|L/D|LiveDead|Autofluorescence", ag, ignore.case = TRUE)
  is_none_af  <- grepl("^AF(-A)?$", nm, ignore.case = TRUE)  # autofluorescence channel (Cytek Aurora)
  is_none <- is_none_ch | is_none_ag | is_none_af
  panel <- data.frame(fcs_colname = nm, antigen = ag,
                      marker_class = ifelse(is_none, "none", "type"), stringsAsFactors = FALSE)
  # modality detection
  det <- detect_modality(raw_ff)
  modality <- if (opt$modality != "auto") opt$modality else det$modality
  # spectral state: explicit user override ALWAYS wins over auto-detection
  spectral_state <- switch(opt$spectral_state,
    raw = "raw", unmixed = "unmixed", auto = det$spectral_state)
  spectral <- (spectral_state == "raw")   # only raw triggers the guard
  # already-unmixed spectral IS fluorescence flow: force modality=flow unless user overrode it
  if (spectral_state == "unmixed" && opt$modality == "auto") modality <- "flow"
  if (det$cyt_empty)
    logmsg("NOTE: $CYT/$CYTSN keyword empty/missing; modality classified from channel/marker structure only (assumption logged).")
  logmsg("Modality detection: %s | spectral_state=%s (instrument_spectral=%s, named_fluor_frac=%.2f, raw_detector=%s, n_chan=%d, $CYT='%s')",
         modality, spectral_state, det$instrument_spectral, det$named_fluor_frac, det$raw_detector, det$n_chan, det$cyt)
  logmsg("Spectral decision: %s -- %s",
         spectral_state,
         if (spectral_state == "unmixed") "already-unmixed spectral treated as fluorescence flow"
         else if (spectral_state == "raw") "raw spectral (unmixing pending) -> will refuse"
         else "not spectral")
  if (opt$spectral_state != "auto")
    logmsg("  (user override --spectral-state=%s applied; auto-detection was %s)", opt$spectral_state, det$spectral_state)
}

# ------------------------------------------------------------------ spectral guard (raw only)
if (isTRUE(spectral)) {
  logmsg("!! RAW SPECTRAL data detected (unmixing pending). This skill does NOT perform spectral unmixing.")
  logmsg("!! If this data is ALREADY UNMIXED (per-fluorophore abundances), rerun with --spectral-state unmixed.")
  logmsg("!! To force processing as-is, rerun with --spectral-state unmixed --modality flow.")
  stop("Raw spectral data requires upstream unmixing; aborting per skill scope. Override with --spectral-state unmixed.")
}
if (modality == "unknown") {
  logmsg("Modality could not be auto-detected; defaulting to 'flow'. Override with --modality.")
  modality <- "flow"
}

# ------------------------------------------------------------------ compensation (flow) + spillover diagnostics (item 2)
# v2.2.0 item 2: ALWAYS compute a condition-number diagnostic (kappa/rcond/verdict) on the
# spillover matrix and RECORD it (diagnostics-on). An ILL-conditioned matrix (kappa >
# --spillover-kappa-max) is REPORTED but STILL applied; only malformed/singular matrices fall
# back to UNCOMPENSATED (preserves v2.1.0 apply/skip logic -> no drift). An external --spillover
# CSV takes precedence over the embedded matrix (aligned to data channels by intersection).
comp_applied <- "none"
comp_matrix <- NULL
comp_info <- list(source = "none", dim = NA_integer_, rcond = NA_real_, kappa = NA_real_,
                  verdict = "none", applied = FALSE)
# Acquisition-scale events retained for margin/saturation QC. Compensation below rewrites `fs`
# in place (same events, same order); the FCS $PnR bounds describe only these values.
fs_acquisition <- fs
do_comp <- switch(opt$compensate, on = TRUE, off = FALSE, auto = (modality == "flow"), FALSE)
if (do_comp) {
  chans <- flowCore::colnames(raw_ff)
  mat <- NULL; src <- "none"; align_note <- ""
  # (a) external spillover CSV takes precedence over the embedded matrix
  if (!is.na(opt$spillover) && nzchar(opt$spillover)) {
    if (!file.exists(opt$spillover)) {
      logmsg("WARNING: --spillover '%s' not found; using embedded spillover.", opt$spillover)
    } else {
      ext <- tryCatch(read_spillover_csv(opt$spillover), error = function(e) NULL)
      if (is.null(ext)) {
        logmsg("WARNING: --spillover CSV '%s' unreadable; using embedded spillover.", opt$spillover)
      } else {
        al <- align_spillover(ext, chans)
        if (isTRUE(al$ok)) {
          mat <- al$matrix; src <- "external"
          align_note <- sprintf(" [aligned %d ch; %d missing, %d extra]", al$info$n_matched,
                                length(al$info$missing_in_matrix), length(al$info$extra_in_matrix))
          logmsg("Using EXTERNAL spillover from %s%s", opt$spillover, align_note)
        } else logmsg("WARNING: external spillover from %s could not be aligned to data channels (%d matched); using embedded.",
                      opt$spillover, al$info$n_matched)
      }
    }
  }
  # (b) embedded spillover fallback
  if (is.null(mat)) {
    sp <- tryCatch(flowCore::spillover(raw_ff), error = function(e) NULL)
    sp <- Filter(Negate(is.null), sp)
    if (length(sp) >= 1 && !is.null(sp[[1]])) { mat <- sp[[1]]; src <- "embedded" }
  }
  if (!is.null(mat)) {
    # ALWAYS compute the condition-number diagnostic (report even when applied)
    dg <- spillover_diagnostics(mat, kappa_max = opt$spillover_kappa_max)
    comp_info$source <- src; comp_info$dim <- dg$n; comp_info$rcond <- dg$rcond
    comp_info$kappa <- dg$kappa; comp_info$verdict <- dg$verdict
    logmsg("Spillover diagnostics [%s]: kappa=%s rcond=%s verdict=%s (%s)", src,
           ifelse(is.finite(dg$kappa), sprintf("%.4g", dg$kappa), "NA"),
           ifelse(is.finite(dg$rcond), sprintf("%.4g", dg$rcond), "NA"), dg$verdict, dg$notes)
    if (identical(dg$verdict, "ill"))
      logmsg("  !! ILL-CONDITIONED spillover (kappa=%.4g > --spillover-kappa-max=%.4g): the inverse AMPLIFIES noise and can destabilize positivity gates. REPORTED and STILL APPLIED (diagnostics-on / removal-opt-in). Inspect the spillover matrix / single-stain controls.",
             dg$kappa, opt$spillover_kappa_max)
    if (dg$verdict %in% c("ill", "malformed", "singular"))
      add_qc_uncertainty(
        sprintf("compensation_%s", dg$verdict), "compensation", detail = dg$notes,
        consequence = "Compensation may distort fluorescence intensities and downstream positivity gates.",
        recommendation = "Inspect the spillover matrix and single-stain controls before continuing.")
    # apply defensively: malformed/singular -> UNCOMPENSATED; well/ill -> apply
    comp_try <- if (dg$verdict %in% c("malformed", "singular")) {
                  simpleError(sprintf("spillover matrix %s (%s)", dg$verdict, dg$notes))
                } else tryCatch(flowCore::compensate(fs, mat), error = function(e) e)
    if (!inherits(comp_try, "error")) {
      fs <- comp_try; comp_info$applied <- TRUE
      comp_matrix <- mat
      comp_applied <- sprintf("%s spillover (%d x %d, verdict=%s)%s", src, dg$n, dg$n, dg$verdict, align_note)
      logmsg("Compensation applied: %s", comp_applied)
    } else {
      comp_applied <- sprintf("none (%s spillover not applied: %s - proceeding UNCOMPENSATED)",
                              src, conditionMessage(comp_try))
      logmsg("WARNING: %s", comp_applied)
    }
  } else {
    comp_applied <- "none (no spillover found - proceeding UNCOMPENSATED)"
    logmsg("WARNING: %s", comp_applied)
  }
} else logmsg("Compensation: skipped (modality=%s, --compensate=%s)", modality, opt$compensate)

# ------------------------------------------------------------------ decide transform + cofactor
if (opt$transform == "auto") transform <- if (modality == "cytof") "arcsinh" else "logicle" else transform <- opt$transform
if (opt$cofactor == "auto") {
  cofactor <- if (modality == "cytof") 5 else NA  # flow: prefer logicle; arcsinh cofactor estimated if chosen
} else cofactor <- as.numeric(opt$cofactor)
logmsg("Transform decision: %s | cofactor: %s | modality: %s",
       transform, ifelse(is.na(cofactor), "n/a (logicle) / estimated", cofactor), modality)
TRANSFORM_DEF <- build_transform_definition(fs, modality, transform, cofactor)
cofactor <- TRANSFORM_DEF$cofactor
write_transform_contract(TRANSFORM_DEF, file.path(opt$outdir, "transform_contract.csv"))
logmsg("Transform contract: %s applied once to %d channel(s); reused for samples, controls, gating diagnostics, and final expression assay.",
       TRANSFORM_DEF$method, length(TRANSFORM_DEF$channels))

# ------------------------------------------------------------------ v2.2.0 feature setup (items 4/6/8)
# Initialize v2.2.0 summaries unconditionally so downstream metadata is always well-formed,
# regardless of modality / which features run. Defaults preserve v2.1.0 behavior (no drift).
harmonization <- list(scope = opt$threshold_scope, n_batches = 0L,
                      n_groups_harmonized = 0L, shrink = opt$harmonize_shrink)
gate_hierarchy <- NULL   # populated by the opencyto backend (item 6); NULL for builtin
cytof_norm_summary <- list(applied = FALSE, beads = opt$beads, k = opt$cytof_norm_k,
                           n_removed = 0L, n_beads = NA_integer_)
# item 8: decide whether CyTOF bead normalization is active (opt-in). auto = cytof + bead channel.
cytof_bead_ch <- if (modality == "cytof") {
  cn <- flowCore::colnames(raw_ff); h <- cn[grepl("Ce140|140Ce|bead|EQ", cn, ignore.case = TRUE)]
  if (length(h)) h[1] else NA_character_
} else NA_character_
cytof_norm_on <- switch(opt$cytof_norm,
  on   = (modality == "cytof"),
  auto = (modality == "cytof" && !is.na(cytof_bead_ch)),
  off  = FALSE, FALSE)
if (opt$cytof_norm != "off" && modality != "cytof")
  logmsg("NOTE: --cytof-norm=%s ignored (modality=%s, not cytof).", opt$cytof_norm, modality)
if (cytof_norm_on)
  logmsg("CyTOF bead normalization ENABLED (normCytof beads=%s k=%d): the threshold bead-removal gate will be SKIPPED so bead events survive for normalization.",
         opt$beads, opt$cytof_norm_k)
if (!is.na(opt$gate_template) && nzchar(opt$gate_template) && opt$gate_engine == "builtin") {
  logmsg("NOTE: --gate-template supplied; switching --gate-engine to 'opencyto'.")
  opt$gate_engine <- "opencyto"
}
if (opt$gate_engine == "opencyto") {
  logmsg("Gating engine: openCyto/flowWorkspace hierarchical backend (item 6, opt-in).")
  source(file.path(SCRIPT_DIR, "gating_opencyto.R"))
}

# ------------------------------------------------------------------ QC gating (BEFORE clustering)
# Data-driven (density valley), reviewable (editable template + per-gate figures), and
# multivariate (2D joint gates) QC gating via gating_engine.R. Gates compose by INTERSECTION;
# each 1D gate row carries ONE editable cutoff; 2D gates are accept/reject (region2d).
# Data-driven cutoffs are found on the (locally arcsinh-transformed) channel so valleys are
# visible; when a channel is unimodal (Hartigan dip test) or its valley is too shallow, NO
# cutoff is invented -> conservative percentile + REVIEW flag (honesty rule; matches legacy
# behavior on already-clean data). scatter/singlet gates stay PERMISSIVE; the per-gate WARNING
# and OVER-GATING alarm are preserved. See references/qc_gating.md + threshold_selection.md.
qc_on <- (opt$qc == "on")
PER_GATE_WARN <- 0.20  # per-gate warn threshold for a single scatter/singlet gate
CONTROLS <- NULL       # populated by load_controls() when --controls supplied
LINEAGE_BIAS_ROWS <- list()
thresholds_supplied <- !is.na(opt$thresholds) && file.exists(opt$thresholds)
if (!opt$gate_review %in% c("propose", "apply", "auto"))
  stop("--gate-review must be propose, apply, or auto.")
write_gating_state <- function(state, applied, reason, reviewed_thresholds = FALSE) {
  row <- data.frame(
    state = state, applied = isTRUE(applied), gate_review = opt$gate_review,
    gate_engine = opt$gate_engine, thresholds_supplied = thresholds_supplied,
    reviewed_thresholds = isTRUE(reviewed_thresholds), reason = reason,
    stringsAsFactors = FALSE)
  utils::write.csv(row, file.path(opt$outdir, "gating_state.csv"), row.names = FALSE)
  row
}

# $PnN/$PnS resolution is centralized and fails on ambiguous matches.
available_channels <- as.character(flowCore::pData(flowCore::parameters(raw_ff))$name)
LINEAGE_CH <- resolve_lineage_markers(panel, opt$lineage_markers, available_channels)
VIAB_CH <- resolve_role_channel(panel, available_channels, VIABILITY_PATTERNS,
                                role = "viability", required = FALSE)
if (is.na(VIAB_CH)) {
  logmsg("Viability channel: not present in unambiguous $PnN/$PnS metadata.")
} else {
  logmsg("Viability channel resolved through shared $PnN/$PnS contract: %s", VIAB_CH)
}

time_qc_result <- run_time_qc_stage(fs, opt$time_qc, opt$time_qc_backend, opt$time_qc_mad_k,
                                    modality, qc_on, figdir, logmsg, acquisition_fs = fs_acquisition)
fs <- time_qc_result$fs; time_qc_summary <- time_qc_result$summary
utils::write.csv(time_qc_result$diagnostics, file.path(opt$outdir, "time_qc_diagnostics.csv"), row.names = FALSE)
if (time_qc_summary$mode != "off") {
  for (metric in c("rate", "signal", "margin")) {
    value <- time_qc_summary[[paste0("pct_", metric)]]
    if (is.finite(value) && value > 0)
      add_qc_uncertainty(
        sprintf("time_%s", metric), "acquisition", detail = sprintf("%.2f%% of events were flagged for %s.", value, metric),
        consequence = "Acquisition instability can create artificial populations or biased frequencies.",
        recommendation = "Review the time-QC plot; keep report-only mode unless the affected interval is clearly technical.")
  }
}

# ---- per-frame gating -> list(frame = gated flowFrame, rows = template rows) ----
gate_frame <- function(ff, ff_gating, sid, template) {
  ex <- flowCore::exprs(ff_gating); n0 <- nrow(ex); ch <- colnames(ex)
  marker_ch <- ch
  keep <- rep(TRUE, n0); rows <- list(); stage <- list()
  getc <- function(pats) {
    for (pattern in pats) {
      hit <- resolve_role_channel(panel, ch, pattern, role = sprintf("gate dimension '%s'", pattern), required = FALSE)
      if (!is.na(hit)) return(hit)
    }
    NA_character_
  }
  tf <- function(v, nm) v
  ctrl_for <- function(nm) {
    if (is.null(CONTROLS)) return(NULL)
    keys <- ls(CONTROLS, all.names = TRUE)
    key <- keys[toupper(keys) == toupper(nm)]
    if (!length(key)) return(NULL)
    item <- get(key[1], envir = CONTROLS); item$used <- TRUE; assign(key[1], item, envir = CONTROLS)
    item
  }
  record <- function(before, after, name, scatter = FALSE) {
    frac <- if (before > 0) 1 - after / before else 0
    stage[[name]] <<- c(in_ = before, out = after, frac = frac, scatter = as.numeric(scatter))
    flag <- if (scatter && frac > PER_GATE_WARN)
      sprintf("  <-- WARNING: removed %.1f%% of entering events; a tight scatter/singlet gate discards REAL cells and biases downstream. Loosen and inspect this gate FIRST if results disagree with manual gating.", 100 * frac) else ""
    logmsg("    [%s] %s: %d -> %d (%.1f%% removed)%s", sid, name, before, after, 100 * frac, flag)
    if (scatter && frac > PER_GATE_WARN)
      add_qc_uncertainty(
        sprintf("%s_%s_high_removal", sid, name), "gating", sid,
        sprintf("The %s gate removed %.1f%% of entering events.", name, 100 * frac),
        "A tight scatter or singlet gate can remove legitimate cell populations.",
        "Inspect the matching gate diagnostic and loosen the threshold if real cells are excluded.")
  }
  # 1D data-driven gate: engine estimate (or template override) -> row + figure -> apply
  do_1d <- function(gate, chname, direction, fallback_pct = NULL, method = opt$gate_method,
                    scatter = FALSE, use_control = FALSE, max_remove_frac = NA_real_, vec_all = NULL) {
    if (is.na(chname)) return(invisible())
    before <- sum(keep); idx <- which(keep)
    xv_all <- if (is.null(vec_all)) tf(ex[, chname], chname) else vec_all
    xv <- xv_all[idx]
    lu <- if (!is.null(template)) lookup_threshold(template, sid, gate) else NULL
    user_override <- !is.null(lu) && is.finite(lu$cutoff)
    control_item <- if (use_control) ctrl_for(chname) else NULL
    control <- if (is.null(control_item)) NULL else control_item$values
    if (user_override) {
      est <- list(cutoff = lu$cutoff, method_used = "user", status = "user_edit", valley_confidence = NA,
                  unimodal = NA, dip_p = NA, valley_x = NA, notes = if (is.null(control_item)) "from template" else sprintf(
                    "from reviewed template; control reference cutoff=%.6g at percentile=%.6g; reviewed cutoff preserved",
                    control_quantile(control, control_item$pct), control_item$pct)); applyit <- lu$apply
    } else {
      est <- estimate_threshold_1d(xv, direction = direction, method = method, control = control,
                                   fallback_pct = fallback_pct,
                                   control_pct = if (is.null(control_item)) 0.99 else control_item$pct,
                                   valley_min_depth = opt$valley_min_depth, dip_alpha = opt$dip_alpha,
                                   max_remove_frac = max_remove_frac)
      applyit <- if (!is.null(lu)) lu$apply else TRUE
    }
    if (identical(gate, "live_dead") && identical(modality, "flow") && !identical(est$method_used, "user") && length(LINEAGE_CH)) {
      candidate_keep <- apply_threshold_1d(xv, est$cutoff, direction)
      bias_review <- FALSE
      for (ag in names(LINEAGE_CH)) {
        lch <- LINEAGE_CH[[ag]]
        if (!lch %in% ch) next
        br <- lineage_bias_review(candidate_keep, ex[idx, lch], min_events = opt$lineage_min_events,
                                  delta_pp = opt$lineage_bias_delta_pp)
        LINEAGE_BIAS_ROWS[[length(LINEAGE_BIAS_ROWS) + 1L]] <<- data.frame(
          sample_id = sid, lineage = ag, channel = lch, evaluable = isTRUE(br$evaluable),
          state = if (isTRUE(br$review)) "REVIEW_lineage_bias" else if (isTRUE(br$evaluable)) "PASS" else "NOT_EVALUABLE",
          reason = br$reason, n_positive = ifelse(is.null(br$n_positive), NA, br$n_positive),
          n_negative = ifelse(is.null(br$n_negative), NA, br$n_negative),
          positive_removed_pct = ifelse(is.null(br$positive_removed_pct), NA, br$positive_removed_pct),
          negative_removed_pct = ifelse(is.null(br$negative_removed_pct), NA, br$negative_removed_pct),
          removal_delta_pp = ifelse(is.null(br$delta_pp), NA, br$delta_pp), stringsAsFactors = FALSE)
        bias_review <- bias_review || isTRUE(br$review)
      }
      if (bias_review) {
        applyit <- FALSE
        est$status <- "REVIEW_lineage_bias"
        est$notes <- paste(est$notes, "viability application blocked: lineage-biased removal requires reviewed threshold")
      }
    }
    pr <- pct_removed_1d(xv, est$cutoff, direction)
    row <- make_gate_row(sid, gate, chname, NA, est, direction, pr,
                         diagnostic_valley_pct_removed = if (is.finite(est$valley_x))
                           pct_removed_1d(xv, est$valley_x, direction) else NA_real_); row$apply <- if (applyit) "Y" else "N"
    rows[[length(rows) + 1]] <<- row
    plot_gate_1d(xv, est$cutoff, direction, sprintf("%s | %s (%s)", sid, gate, chname),
                 file.path(figdir, sprintf("gate_%s_%s.png", sid, gate)), control = control, status = est$status,
                 depth = est$valley_confidence, unimodal = est$unimodal, dip_p = est$dip_p,
                 valley_x = est$valley_x, method_label = est$method_used)
    if (isTRUE(applyit)) { keep <<- keep & apply_threshold_1d(xv_all, est$cutoff, direction); record(before, sum(keep), gate, scatter = scatter) }
    else logmsg("    [%s] %s: proposed (apply=N) -- not applied", sid, gate)
  }
  # fixed-cutoff row (MAD-band bounds / percentile bounds); template-overridable; optional figure
  do_fixed <- function(gate, chname, cutoff, direction, scatter = FALSE, note = "", vec_all = NULL, fig = FALSE) {
    before <- sum(keep); idx <- which(keep)
    xv_all <- if (is.null(vec_all)) tf(ex[, chname], chname) else vec_all; xv <- xv_all[idx]
    lu <- if (!is.null(template)) lookup_threshold(template, sid, gate) else NULL; applyit <- TRUE; status <- "fixed"
    if (!is.null(lu)) { applyit <- lu$apply; if (is.finite(lu$cutoff)) { cutoff <- lu$cutoff; status <- "user_edit"; note <- "from template" } }
    diagnostic_valley <- if (fig) find_valley(xv) else NULL
    est <- list(cutoff = cutoff, method_used = if (status == "user_edit") "user" else "fixed", status = status,
                valley_confidence = if (is.null(diagnostic_valley)) NA else diagnostic_valley$depth,
                unimodal = NA, dip_p = NA,
                valley_x = if (is.null(diagnostic_valley)) NA else diagnostic_valley$cutoff,
                notes = note)
    row <- make_gate_row(sid, gate, chname, NA, est, direction, pct_removed_1d(xv, cutoff, direction))
    if (!is.null(diagnostic_valley) && is.finite(diagnostic_valley$cutoff))
      row$diagnostic_valley_pct_removed <- pct_removed_1d(xv, diagnostic_valley$cutoff, direction)
    row$apply <- if (applyit) "Y" else "N"; rows[[length(rows) + 1]] <<- row
    if (fig) plot_gate_1d(xv, cutoff, direction, sprintf("%s | %s (%s)", sid, gate, chname),
                          file.path(figdir, sprintf("gate_%s_%s.png", sid, gate)), status = status,
                          depth = diagnostic_valley$depth, valley_x = diagnostic_valley$cutoff,
                          method_label = note)
    if (isTRUE(applyit)) { keep <<- keep & apply_threshold_1d(xv_all, cutoff, direction); record(before, sum(keep), gate, scatter = scatter) }
    invisible(list(cutoff = cutoff, apply = applyit))
  }
  # MAD band -> two editable rows (low keep_above + high keep_below), computed on current survivors
  do_madband <- function(gate, chname, k, scatter = TRUE) {
    if (is.na(chname)) return(invisible())
    v_all <- tf(ex[, chname], chname); v <- v_all[which(keep)]
    lo <- stats::median(v, na.rm = TRUE) - k * stats::mad(v, na.rm = TRUE)
    hi <- stats::median(v, na.rm = TRUE) + k * stats::mad(v, na.rm = TRUE)
    do_fixed(sprintf("%s_low", gate), chname, lo, "keep_above", scatter = scatter, note = sprintf("median-%g*MAD", k), vec_all = v_all)
    do_fixed(sprintf("%s_high", gate), chname, hi, "keep_below", scatter = scatter, note = sprintf("median+%g*MAD", k), vec_all = v_all)
  }
  # 2D joint gate: retain all fitted components plus a robust envelope. Reviewable via CSV:
  #   apply=Y/N            -> accept/reject the gate (as any gate)
  #   final_cutoff (0,1)   -> density-envelope COVERAGE. Blank keeps the automatic gate;
  #                           a finite value switches to that density coverage. Raise toward 0.999
  #                           to LOOSEN (keep more); lower toward 0.95 to TIGHTEN (keep less).
  # Blank-by-default final_cutoff means an unedited template reproduces the auto gate exactly,
  # while a wet-lab reviewer can still LOOSEN an over-aggressive 2D gate per sample from the CSV.
  do_2d <- function(gate, chx, chy, apply_default = TRUE, scatter = FALSE,
                    x_all = NULL, y_all = NULL) {
    if (is.na(chx) || is.na(chy)) return(invisible())
    before <- sum(keep); idx <- which(keep)
    xa <- if (is.null(x_all)) tf(ex[, chx], chx) else x_all
    ya <- if (is.null(y_all)) tf(ex[, chy], chy) else y_all
    lu <- if (!is.null(template)) lookup_threshold(template, sid, gate) else NULL
    applyit <- if (!is.null(lu)) lu$apply else apply_default
    level_default <- 0.99
    user_level <- if (!is.null(lu) && is.finite(lu$cutoff)) lu$cutoff else NA_real_
    if (!is.na(user_level) && (user_level <= 0 || user_level >= 1)) {
      logmsg("    [%s] %s: final_cutoff=%.4g outside (0,1); ignoring, using automatic gate", sid, gate, user_level)
      user_level <- NA_real_
    }
    if (!is.na(user_level)) {
      g2 <- estimate_gate_2d(xa[idx], ya[idx], method = "density", level = user_level)
      lvl_show <- user_level; status2 <- "user_edit"
      note2 <- sprintf("2D %s x %s; robust density coverage=%.4g (user)", chx, chy, user_level)
    } else {                                                    # automatic permissive envelope
      gate_method <- if (isTRUE(applyit)) "auto" else "density"
      g2 <- estimate_gate_2d(xa[idx], ya[idx], method = gate_method, level = level_default)
      lvl_show <- level_default; status2 <- if (!is.null(lu)) lu$status else "auto_ok"
      note2 <- sprintf("2D %s x %s (all components + density envelope); final_cutoff = density coverage in (0,1), blank=auto, raise->looser", chx, chy)
    }
    keepvec <- rep(FALSE, n0); keepvec[idx] <- g2$keep
    pr <- if (before > 0) 1 - sum(g2$keep) / before else 0
    est <- list(cutoff = lvl_show, method_used = g2$method, status = status2, valley_confidence = NA,
                unimodal = NA, dip_p = NA, valley_x = NA, notes = note2)
    row <- make_gate_row(sid, gate, chx, chy, est, "region2d", pr); row$apply <- if (applyit) "Y" else "N"
    # proposed_cutoff shows the default level knob (0.99); final_cutoff stays BLANK unless the
    # reviewer set one, so re-running an unedited template keeps the automatic gate.
    if (is.na(user_level)) row$final_cutoff <- NA
    rows[[length(rows) + 1]] <<- row
    plot_gate_2d(xa[idx], ya[idx], g2$keep, chx, chy,
                 sprintf("%s | %s%s", sid, gate, if (applyit) "" else " (diagnostic, apply=N)"),
                 file.path(figdir, sprintf("gate_%s_%s.png", sid, gate)), density_gate = g2)
    if (isTRUE(applyit)) { keep <<- keep & keepvec; record(before, sum(keep), gate, scatter = scatter) }
    else logmsg("    [%s] %s: proposed (apply=N) -- diagnostic only", sid, gate)
  }

  MULTI <- (opt$multivariate == "on")
  if (modality == "flow") {
    fscA <- getc(c("FSC-A", "FSC.A", "^FSC$")); fscH <- getc(c("FSC-H", "FSC.H")); sscA <- getc(c("SSC-A", "SSC.A", "^SSC$")); sscH <- getc(c("SSC-H", "SSC.H"))
    # debris gate prefers AREA, but falls back to HEIGHT when area is absent (older FACSCalibur / height-only sets).
    fsc_deb <- if (!is.na(fscA)) fscA else fscH; ssc_deb <- if (!is.na(sscA)) sscA else sscH
    # 1) debris: permissive one-dimensional floors are always applied. The robust 2D envelope
    # is a review diagnostic and cannot remove cells unless a reviewer explicitly applies it.
    debris_fsc_cut <- if (!is.na(fsc_deb)) as.numeric(stats::quantile(ex[keep, fsc_deb], opt$debris_pct, na.rm = TRUE)) else NA_real_
    debris_ssc_cut <- if (!is.na(ssc_deb)) as.numeric(stats::quantile(ex[keep, ssc_deb], opt$debris_pct, na.rm = TRUE)) else NA_real_
    if (is.finite(debris_fsc_cut)) do_fixed("debris_fsc", fsc_deb, debris_fsc_cut, "keep_above", scatter = TRUE,
                                            note = sprintf("permissive %.3g lower-tail floor", opt$debris_pct), fig = TRUE)
    if (is.finite(debris_ssc_cut)) do_fixed("debris_ssc", ssc_deb, debris_ssc_cut, "keep_above", scatter = TRUE,
                                            note = sprintf("permissive %.3g lower-tail floor", opt$debris_pct), fig = TRUE)
    if (MULTI && !is.na(fsc_deb) && !is.na(ssc_deb))
      do_2d("debris_2d", fsc_deb, ssc_deb, apply_default = FALSE, scatter = TRUE)
    # 2) singlet: FSC-A/FSC-H ratio MAD band (two editable rows, applied on the ratio)
    if (!is.na(fscA) && !is.na(fscH)) {
      before <- sum(keep); r <- ex[, fscA] / (ex[, fscH] + 1e-6); rv <- r[which(keep)]
      lo <- stats::median(rv, na.rm = TRUE) - opt$singlet_mad_k * stats::mad(rv, na.rm = TRUE)
      hi <- stats::median(rv, na.rm = TRUE) + opt$singlet_mad_k * stats::mad(rv, na.rm = TRUE)
      low_result <- do_fixed("singlet_low", "FSC-A/FSC-H", lo, "keep_above", scatter = TRUE,
                             note = sprintf("median-%g*MAD ratio", opt$singlet_mad_k), vec_all = r)
      high_result <- do_fixed("singlet_high", "FSC-A/FSC-H", hi, "keep_below", scatter = TRUE,
                              note = sprintf("median+%g*MAD ratio", opt$singlet_mad_k), vec_all = r)
      plot_singlet_gate_2d(
        ex[, fscA], ex[, fscH], low_result$cutoff, high_result$cutoff,
        sprintf("%s | singlets (%s vs %s)", sid, fscA, fscH),
        file.path(figdir, sprintf("gate_%s_singlet_2d.png", sid)))
    }
    # 3) live/dead: 1D viability valley (editable; control-anchored if provided) + 2D diagnostic
    # The global viability channel is resolved once through the shared $PnN/$PnS contract.
    vch <- VIAB_CH
    if (is.na(vch)) vch <- getc(c("Live", "Dead", "Viability", "Zombie", "7AAD", "DAPI", "^PI$", "L/D", "LiveDead"))
    if (!is.na(vch)) {
      do_1d("live_dead", vch, "keep_below", fallback_pct = 0.95, use_control = TRUE, max_remove_frac = opt$viab_max_remove)
      if (MULTI && !is.na(fscA)) do_2d("live_dead_2d", vch, fscA, apply_default = FALSE)
      # viability x lineage-marker diagnostic. Neutrophils take up viability dye
      # without being dead, so viability x FSC alone can misgate live neutrophils as dead.
      # Plotting viability against a granulocyte lineage marker (CD15/CD66b/CD16/CD11b) makes
      # the dye-bright-but-live population obvious. Diagnostic only (apply=N); gating unchanged.
      # Decoupled from --multivariate (always emits when vch + a resolved lineage marker exist).
      for (ag in names(LINEAGE_CH)) {
        lch <- LINEAGE_CH[[ag]]
        if (lch %in% ch) do_2d(sprintf("live_dead_lineage_%s", ag), vch, lch, apply_default = FALSE)
      }
    }
  } else if (modality == "cytof") {
    dna <- resolve_dna_channels(panel, ch, opt$dna_channels)
    pt <- VIAB_CH
    bd  <- getc(c("Ce140", "140Ce", "bead", "EQ")); el <- getc(c("Event_length", "Event-length", "length"))
    # 1) DNA/intact: valley low bound (keep_above) + 99.5th-pct high bound (keep_below).
    #    v2.2.0 item 8: normalization beads are DNA-NEGATIVE, so dna_intact_low would remove
    #    them. When cytof bead normalization is enabled, skip this low bound so beads survive
    #    into the SCE for CATALYST::normCytof (which then removes them). The high bound (trim
    #    high-DNA doublets) is kept -- DNA-low beads pass it.
    if (length(dna)) {
      dna_values <- ex[, dna, drop = FALSE]
      dna_low <- if (length(dna) == 2L) apply(dna_values, 1L, min) else dna_values[, 1L]
      dna_high <- if (length(dna) == 2L) apply(dna_values, 1L, max) else dna_values[, 1L]
      dna_low_label <- if (length(dna) == 2L) sprintf("DNA_both_min(%s)", paste(dna, collapse = ",")) else dna[1]
      dna_high_label <- if (length(dna) == 2L) sprintf("DNA_either_max(%s)", paste(dna, collapse = ",")) else dna[1]
      if (length(dna) == 2L)
        do_2d("dna_joint_2d", dna[1], dna[2], apply_default = FALSE, scatter = TRUE)
      if (cytof_norm_on) logmsg("    [%s] dna_intact_low: SKIPPED (item 8 cytof-norm on; DNA-negative beads retained for normCytof).", sid)
      else do_1d("dna_intact_low", dna_low_label, "keep_above", fallback_pct = 0.05,
                 scatter = TRUE, vec_all = dna_low)
      dv <- dna_high[which(keep)]
      do_fixed("dna_intact_high", dna_high_label,
               as.numeric(stats::quantile(dv, 0.995, na.rm = TRUE)), "keep_below",
               scatter = TRUE, note = "99.5th pct (trim events high in either DNA isotope)",
               vec_all = dna_high)
    }
    # 2) Gaussian-parameter doublet: event length MAD band
    if (!is.na(el)) do_madband("gaussian_doublet", el, opt$singlet_mad_k, scatter = TRUE)
    # 3) live/dead (Pt/cisplatin): 1D valley (editable; control-anchored) + 2D Pt x DNA diagnostic
    if (!is.na(pt)) {
      do_1d("live_dead", pt, "keep_below", fallback_pct = 0.95, use_control = TRUE, max_remove_frac = opt$viab_max_remove)
      if (MULTI && length(dna))
        do_2d("live_dead_2d", pt, dna_low_label, apply_default = FALSE, y_all = dna_low)
      # Pt viability x lineage-marker diagnostic (analogous to the flow branch).
      # Diagnostic only (apply=N); decoupled from --multivariate.
      for (ag in names(LINEAGE_CH)) {
        lch <- LINEAGE_CH[[ag]]
        if (lch %in% ch) do_2d(sprintf("live_dead_lineage_%s", ag), pt, lch, apply_default = FALSE)
      }
    }
    # 4) bead removal (normalization beads): valley (keep_below) or 99th-pct fallback.
    #    v2.2.0 item 8: when CyTOF bead normalization is enabled, RETAIN beads so normCytof
    #    can normalize to them (it removes them afterward) -- skip the threshold bead gate.
    if (!is.na(bd)) {
      if (cytof_norm_on) logmsg("    [%s] beads: RETAINED for normCytof (item 8); threshold bead-removal gate skipped.", sid)
      else do_1d("beads", bd, "keep_below", fallback_pct = 0.99)
    }
  }
  # always: drop non-finite marker events
  before <- sum(keep); keep <- keep & rowSums(!is.finite(ex[, marker_ch, drop = FALSE])) == 0
  if (sum(keep) < before) logmsg("    [%s] non-finite drop: %d -> %d", sid, before, sum(keep))
  # OVER-GATING alarm: scatter/singlet must stay permissive
  scat <- do.call(rbind, stage[vapply(stage, function(s) unname(s["scatter"]) == 1, logical(1))])
  if (!is.null(scat) && nrow(scat) > 0) {
    scat_frac <- 1 - min(scat[, "out"]) / max(scat[, "in_"])
    if (scat_frac > opt$overgate_alarm)
      logmsg("  !! OVER-GATING ALARM [%s]: scatter/singlet gates jointly removed %.1f%% of events (> %.0f%%). Determine the discrepancy direction from external reconciliation and inspect these gate figures before attributing the result to biology.",
             sid, 100 * scat_frac, 100 * opt$overgate_alarm)
    if (scat_frac > opt$overgate_alarm)
      add_qc_uncertainty(
        sprintf("%s_joint_scatter_overgate", sid), "gating", sid,
        sprintf("Scatter and singlet gates jointly removed %.1f%% of events.", 100 * scat_frac),
        "Legitimate cells, including minority granulocytes, may have been removed.",
        "Review every scatter/singlet diagnostic and compare retained population shapes before continuing.")
  }
  # VIABILITY over-removal alarm: if the live/dead gate removed > viab_max_remove, warn loudly.
  # Neutrophils take up viability dye non-specifically; a valley cutoff can gate out live
  # granulocytes. The live_dead_lineage_* diagnostic figures make this visible.
  if (!is.null(stage$live_dead)) {
    viab_frac <- as.numeric(stage$live_dead["frac"])
    if (is.finite(viab_frac) && viab_frac > opt$viab_max_remove)
      logmsg("  !! VIABILITY OVER-REMOVAL [%s]: live/dead gate removed %.1f%% of events (> %.0f%%). Neutrophils take up viability dye without being dead -- a valley cutoff can gate out live granulocytes. Inspect the live_dead_lineage_* diagnostic figures. If the sample genuinely has high dead-cell fraction, override with --thresholds or raise --viab-max-remove.",
             sid, 100 * viab_frac, 100 * opt$viab_max_remove)
    if (is.finite(viab_frac) && viab_frac > opt$viab_max_remove)
      add_qc_uncertainty(
        sprintf("%s_viability_overremove", sid), "viability", sid,
        sprintf("The viability gate removed %.1f%% of events.", 100 * viab_frac),
        "The threshold may remove viable dye-bright granulocytes or reflect a genuinely poor sample.",
        "Inspect viability density and lineage diagnostics; confirm the cutoff or revise it with a reason.")
  }
  logmsg("  %s: %d -> %d cells kept (%.1f%% removed total)", sid, n0, sum(keep), 100 * (1 - sum(keep) / n0))
  list(frame = ff[keep, ], rows = rows)
}

if (qc_on) {
  fs_gating <- apply_transform_definition(fs, TRANSFORM_DEF)
  logmsg("--- QC gating (data-driven / reviewable / multivariate) ---")
  logmsg("PRINCIPLE: scatter/singlet gates default to PERMISSIVE. Over-gating removes REAL cells")
  logmsg("           silently and biases downstream populations/readouts -- it does NOT fail loudly.")
  logmsg("           HONESTY: unimodal channel or shallow valley -> NO cutoff invented -> conservative percentile + REVIEW flag.")
  logmsg("method=%s | multivariate=%s | review=%s | scope=%s | valley-min-depth=%.2f | dip-alpha=%.3g",
         opt$gate_method, opt$multivariate, opt$gate_review, opt$threshold_scope, opt$valley_min_depth, opt$dip_alpha)
  logmsg("           singlet band = median +/- %g*MAD | debris floor = %.3g pct | over-gate alarm = %.0f%% | per-gate warn = %.0f%% | viab-max-remove = %.0f%%",
         opt$singlet_mad_k, opt$debris_pct, 100 * opt$overgate_alarm, 100 * PER_GATE_WARN, 100 * opt$viab_max_remove)
  CONTROLS <- load_controls_contract(
    opt$controls, panel, available_channels, modality, comp_matrix,
    TRANSFORM_DEF, comp_info$applied, logmsg)
  if (!is.null(CONTROLS)) logmsg("Controls: %d channel(s) ready on the shared compensation/transform path.", length(ls(CONTROLS)))
  # log lineage-marker resolution for the viability x lineage live/dead diagnostic.
  if (length(LINEAGE_CH)) {
    logmsg("Lineage markers for viability x lineage diagnostic: %s",
           paste(sprintf("%s -> %s", names(LINEAGE_CH), unname(LINEAGE_CH)), collapse = ", "))
  } else {
    req <- if (is.na(opt$lineage_markers)) paste(DEFAULT_LINEAGE_MARKERS, collapse = ",")
           else opt$lineage_markers
    logmsg("live_dead_lineage: no lineage markers (%s) found in panel; viability x lineage diagnostic skipped (no error).", req)
  }

  # Bind reviewed cutoffs to the actual pre-gating event matrices, metadata,
  # controls, and preprocessing settings. Exclude only checkpoint/output controls.
  binding_options <- opt[setdiff(names(opt), c("outdir", "thresholds", "review_receipt", "gate_review", "gate_template"))]
  gating_input_fingerprint <- review_fingerprint_object(list(
    raw_fcs_sha256 = if (is_hd) NULL else setNames(
      vapply(fcs_files, review_sha256_file, character(1)), fcs_files),
    metadata_sha256 = if (!is.na(opt$metadata) && file.exists(opt$metadata))
      review_sha256_file(opt$metadata) else NA_character_,
    samples = flowCore::sampleNames(fs),
    events = lapply(seq_along(fs), function(i) flowCore::exprs(fs[[i]])),
    metadata = md, panel = panel, compensation = comp_matrix,
    controls = if (is.null(CONTROLS)) NULL else as.list(CONTROLS),
    settings = binding_options))
  review_preserve_auto_proposal(opt$gate_review, "gating", opt$outdir,
                                input_fingerprint = gating_input_fingerprint)
  proposal_record_path <- file.path(opt$outdir, "gating_review_proposal.json")
  uncertainty_path <- file.path(opt$outdir, "qc_uncertainty.csv")
  uncertainty_columns <- c("uncertainty_id", "category", "sample_id", "detail", "consequence", "recommendation")
  uncertainty_table <- if (length(QC_UNCERTAINTIES)) do.call(rbind, QC_UNCERTAINTIES) else
    as.data.frame(setNames(replicate(length(uncertainty_columns), character(0), simplify = FALSE), uncertainty_columns))

  if (opt$gate_engine == "opencyto") {
    if (!is.null(CONTROLS)) {
      write_gating_state("INCOMPLETE_unsupported_controls", FALSE,
                         "Supplied controls cannot be consumed by the openCyto backend.")
      stop("Supplied controls are unsupported by the openCyto backend; refusing to leave them unused.")
    }
    oc_template <- opencyto_resolve_template_path(opt$gate_template, modality, SCRIPT_DIR)
    utils::write.csv(uncertainty_table, uncertainty_path, row.names = FALSE)
    if (opt$gate_review == "apply") {
      receipt_validation <- review_validate_gating_apply(
        opt$review_receipt, proposal_record_path, oc_template,
        uncertainty_table$uncertainty_id, opt$outdir,
        input_fingerprint = gating_input_fingerprint)
      if (!isTRUE(receipt_validation$valid)) stop(receipt_validation$reason)
    } else {
      oc_proposed <- file.path(opt$outdir, "opencyto_template_proposed.csv")
      oc_rows <- utils::read.csv(oc_template, stringsAsFactors = FALSE, check.names = FALSE)
      oc_rows$review_input_sha256 <- NULL
      if (opt$gate_review == "auto" && file.exists(proposal_record_path)) {
        persisted <- review_read_proposal(proposal_record_path, "gating")
        previous <- utils::read.csv(persisted$proposal_path, stringsAsFactors = FALSE, check.names = FALSE)
        previous$review_input_sha256 <- NULL
        if (!identical(previous, oc_rows))
          stop("The openCyto template differs from the persisted proposal; use reviewed apply or a new output directory.")
      }
      utils::write.csv(oc_rows, oc_proposed, row.names = FALSE)
      review_bind_proposal_input(oc_proposed, gating_input_fingerprint)
      review_write_proposal("gating", oc_proposed, opt$outdir,
                            list(uncertainty_ids = uncertainty_table$uncertainty_id))
      if (opt$gate_review == "propose" || nrow(uncertainty_table) > 0L) {
        reason <- if (nrow(uncertainty_table)) "QC uncertainties require review; unattended gating stopped." else
          "Review the openCyto template before application."
        write_gating_state("INCOMPLETE_proposal", FALSE, reason)
        logmsg("GATING CHECKPOINT: %s Review %s and %s; resume with --gate-review apply --gate-template <reviewed.csv> --review-receipt <receipt.json>.",
               reason, oc_proposed, uncertainty_path)
        quit(save = "no", status = 3L)
      }
      proposal_record <- review_read_proposal(proposal_record_path, "gating")
      review_write_auto_receipt_state("gating", proposal_record, opt$outdir)
      oc_template <- oc_proposed
    }
    oc <- run_opencyto_gating(fs, modality = modality, template_path = oc_template,
                              transform_definition = TRANSFORM_DEF,
                              terminal_population = opt$opencyto_terminal,
                              panel = panel, script_dir = SCRIPT_DIR, outdir = opt$outdir, logmsg = logmsg)
    fs <- oc$fs
    gate_hierarchy <- oc$hierarchy
    write_gating_state(if (opt$gate_review == "auto") "REVIEW_auto_applied" else "PASS_reviewed", TRUE,
                       if (opt$gate_review == "auto") "no QC uncertainties" else receipt_validation$reason,
                       reviewed_thresholds = opt$gate_review == "apply")
    logmsg("openCyto gating complete: terminal population handed to clustering (see gating_hierarchy.txt).")
  } else {
  validate_builtin_control_channels(CONTROLS, VIAB_CH, opt$outdir)
  template <- NULL
  proposal_record_path <- file.path(opt$outdir, "gating_review_proposal.json")
  receipt_validation <- list(valid = FALSE, receipt = NULL, reason = "No review receipt was supplied.")
  if (opt$gate_review == "apply") {
    if (!thresholds_supplied) {
      write_gating_state("INCOMPLETE_missing_reviewed_thresholds", FALSE,
                         "--gate-review apply requires a complete reviewed threshold file.")
      stop("--gate-review apply requires --thresholds <edited csv>")
    }
    if (!file.exists(proposal_record_path)) {
      write_gating_state("INCOMPLETE_missing_proposal", FALSE,
                         "A gating proposal must be generated before reviewed thresholds can be applied.")
      stop("Missing gating_review_proposal.json. Run --gate-review propose first in the same output directory.")
    }
    proposal_record <- review_read_proposal(proposal_record_path, "gating")
    receipt_validation <- review_validate_gating_apply(
      opt$review_receipt, proposal_record, opt$thresholds, character(0), opt$outdir,
      input_fingerprint = gating_input_fingerprint)
    if (!isTRUE(receipt_validation$valid)) stop(receipt_validation$reason)
    template <- read_threshold_template(opt$thresholds)
    validate_reviewed_thresholds(template, read_threshold_template(proposal_record$proposal_path))
    logmsg("PASS 2 (apply): applying edited thresholds from %s (%d rows). Receipt: %s",
           opt$thresholds, nrow(template), receipt_validation$reason)
  } else if (thresholds_supplied && opt$gate_review == "auto") {
    stop("--gate-review auto applies proposed gates. Use --gate-review apply with a receipt for supplied --thresholds.")
  } else if (thresholds_supplied) {
    logmsg("--thresholds supplied with propose mode: not applied. Review state remains INCOMPLETE until --gate-review apply.")
  }

  run_pass <- function(tmpl) {
    sns <- flowCore::sampleNames(fs); frames <- list(); allrows <- list()
    for (s in sns) { res <- gate_frame(fs[[s]], fs_gating[[s]], s, tmpl); frames[[s]] <- res$frame; allrows <- c(allrows, res$rows) }
    fs_new <- as(frames, "flowSet"); flowCore::sampleNames(fs_new) <- sns
    list(fs = fs_new, rows = allrows)
  }
  pass <- run_pass(template)

  # pooled scope: harmonize each gate to the across-sample median cutoff, then re-apply
  if (opt$threshold_scope == "pooled" && is.null(template)) {
    df <- do.call(rbind, pass$rows); num <- suppressWarnings(as.numeric(df$final_cutoff))
    keeprow <- !is.na(num) & df$direction %in% c("keep_below", "keep_above")
    if (any(keeprow)) {
      agg <- aggregate(num[keeprow], by = list(gate = df$gate[keeprow], direction = df$direction[keeprow]), FUN = function(z) stats::median(z, na.rm = TRUE))
      pooled <- data.frame(sample_id = "ALL", gate = agg$gate, channel_x = "", channel_y = "", method = "pooled_median",
                           proposed_cutoff = agg$x, direction = agg$direction, pct_removed = NA, valley_confidence = NA,
                           unimodal = "NA", status = "pooled", final_cutoff = agg$x, apply = "Y", notes = "median across samples", stringsAsFactors = FALSE)
      logmsg("threshold-scope=pooled: harmonizing %d gate(s) to across-sample median cutoffs and re-applying.", nrow(pooled))
      pass <- run_pass(pooled)
    } else logmsg("threshold-scope=pooled requested but no editable 1D cutoffs to pool; using per-sample result.")
  }

  # batch scope (item 4): harmonize per-sample cutoffs WITHIN each metadata batch by
  # confidence-weighted shrinkage toward the (gate,batch) consensus, then re-apply. Falls back
  # to pooled harmonization (+ warning) when no usable batch column is present.
  if (opt$threshold_scope == "batch" && is.null(template)) {
    have_batch <- ("batch" %in% names(md)) && !all(is.na(md$batch))
    if (!have_batch) {
      logmsg("threshold-scope=batch requested but metadata has no usable 'batch' column; FALLING BACK to pooled harmonization.")
      df <- do.call(rbind, pass$rows); num <- suppressWarnings(as.numeric(df$final_cutoff))
      keeprow <- !is.na(num) & df$direction %in% c("keep_below", "keep_above")
      if (any(keeprow)) {
        agg <- aggregate(num[keeprow], by = list(gate = df$gate[keeprow], direction = df$direction[keeprow]), FUN = function(z) stats::median(z, na.rm = TRUE))
        pooled <- data.frame(sample_id = "ALL", gate = agg$gate, channel_x = "", channel_y = "", method = "pooled_median",
                             proposed_cutoff = agg$x, direction = agg$direction, pct_removed = NA, valley_confidence = NA,
                             unimodal = "NA", status = "pooled", final_cutoff = agg$x, apply = "Y", notes = "median across samples (batch fallback)", stringsAsFactors = FALSE)
        pass <- run_pass(pooled)
      }
      harmonization$scope <- "batch->pooled(fallback)"
    } else {
      # gate rows are keyed by sampleNames(fs); map each to its batch via md (file_name, then
      # sample_id, then extension-stripped sample_id) so the join is robust to .fcs extensions.
      sn <- flowCore::sampleNames(fs)
      idx <- match(sn, md$file_name)
      if (anyNA(idx)) idx <- match(sn, md$sample_id)
      if (anyNA(idx)) idx <- match(tools::file_path_sans_ext(sn), md$sample_id)
      batch_map <- data.frame(sample_id = sn, batch = as.character(md$batch)[idx], stringsAsFactors = FALSE)
      if (anyNA(batch_map$batch))
        logmsg("WARNING: %d/%d sample(s) could not be matched to a metadata batch (batch=NA); those stay per-sample.",
               sum(is.na(batch_map$batch)), length(sn))
      hz <- harmonize_cutoffs(do.call(rbind, pass$rows), batch_map,
                              shrink = opt$harmonize_shrink, cutoff_col = "final_cutoff")
      logmsg("threshold-scope=batch: harmonized %d (gate,batch) group(s) across %d batch(es) (shrink=%.2f) and re-applying.",
             hz$n_groups_harmonized, hz$n_batches, hz$shrink)
      pass <- run_pass(hz$rows)
      harmonization$scope <- "batch"; harmonization$n_batches <- hz$n_batches
      harmonization$n_groups_harmonized <- hz$n_groups_harmonized
    }
  }

  tmpl_path <- file.path(opt$outdir, if (opt$gate_review == "apply")
    "gating_thresholds_applied.csv" else "gating_thresholds_template.csv")
  write_threshold_template(pass$rows, tmpl_path)
  if (opt$gate_review != "apply") review_bind_proposal_input(tmpl_path, gating_input_fingerprint)
  review_figures <- list.files(figdir, pattern = "^(gate_|time_qc_).*[.]png$", full.names = TRUE)
  review_index <- data.frame(
    stage = ifelse(grepl("^time_qc_", basename(review_figures)), "acquisition_qc", "gating"),
    label = gsub("[_.]", " ", tools::file_path_sans_ext(basename(review_figures))),
    relative_path = file.path("figures", basename(review_figures)),
    absolute_path = normalizePath(review_figures, mustWork = TRUE),
    stringsAsFactors = FALSE)
  utils::write.csv(review_index, file.path(opt$outdir, "review_artifact_index.csv"), row.names = FALSE)
  logmsg("Wrote gating threshold artifact: %s (%d rows)", tmpl_path, length(pass$rows))
  logmsg("Wrote per-gate diagnostic figures to: %s (gate_<sample>_<gate>.png)", figdir)

  if (length(LINEAGE_BIAS_ROWS))
    utils::write.csv(do.call(rbind, LINEAGE_BIAS_ROWS), file.path(opt$outdir, "lineage_bias_state.csv"), row.names = FALSE)
  gate_rows <- if (length(pass$rows)) do.call(rbind, pass$rows) else data.frame(status = character(0))
  review_rows <- gate_rows[grepl("^REVIEW", gate_rows$status), , drop = FALSE]
  if (nrow(review_rows)) for (i in seq_len(nrow(review_rows))) {
    row <- review_rows[i, ]
    add_qc_uncertainty(
      sprintf("%s_%s_%s", row$sample_id, row$gate, row$status), "threshold", row$sample_id,
      sprintf("%s used %s (%s).", row$gate, row$method, row$status),
      "The data do not strongly support an unambiguous automatic threshold.",
      "Inspect the density plot, the applied cutoff, and any labelled alternative valley before continuing.")
  }
  if (length(LINEAGE_BIAS_ROWS)) {
    lineage_rows <- do.call(rbind, LINEAGE_BIAS_ROWS)
    for (i in which(lineage_rows$state == "REVIEW_lineage_bias"))
      add_qc_uncertainty(
        sprintf("%s_%s_lineage_bias", lineage_rows$sample_id[i], lineage_rows$lineage[i]),
        "viability", lineage_rows$sample_id[i], lineage_rows$reason[i],
        "Viability removal may be biased against a legitimate lineage-positive population.",
        "Review the viability-by-lineage plot and confirm or revise the viability threshold.")
  }
  uncertainty_path <- file.path(opt$outdir, "qc_uncertainty.csv")
  uncertainty_columns <- c("uncertainty_id", "category", "sample_id", "detail", "consequence", "recommendation")
  uncertainty_table <- if (length(QC_UNCERTAINTIES)) do.call(rbind, QC_UNCERTAINTIES) else
    as.data.frame(setNames(replicate(length(uncertainty_columns), character(0), simplify = FALSE), uncertainty_columns))
  utils::write.csv(uncertainty_table, uncertainty_path, row.names = FALSE)
  if (!is.null(CONTROLS)) {
    ledger <- attr(CONTROLS, "ledger")
    for (i in seq_len(nrow(ledger))) {
      item <- get(ledger$channel[i], envir = CONTROLS)
      ledger$used[i] <- isTRUE(item$used)
      ledger$state[i] <- if (isTRUE(item$used)) "USED" else "INCOMPLETE_unused"
    }
    utils::write.csv(ledger, file.path(opt$outdir, "control_usage_state.csv"), row.names = FALSE)
    if (any(!ledger$used))
      stop(sprintf("Supplied control(s) were unused: %s", paste(ledger$channel[!ledger$used], collapse = ", ")))
  }

  if (opt$gate_review != "apply") {
    review_write_proposal("gating", tmpl_path, opt$outdir,
                          list(uncertainty_ids = uncertainty_table$uncertainty_id))
    proposal_record <- review_read_proposal(proposal_record_path, "gating")
  }
  if (opt$gate_review == "apply") {
    receipt_validation <- review_validate_gating_apply(
      opt$review_receipt, proposal_record, opt$thresholds,
      uncertainty_table$uncertainty_id, opt$outdir,
      input_fingerprint = gating_input_fingerprint)
    if (!isTRUE(receipt_validation$valid)) stop(receipt_validation$reason)
  }
  unresolved_qc <- nrow(uncertainty_table) > 0L && !isTRUE(receipt_validation$valid)
  should_stop <- opt$gate_review == "propose" || unresolved_qc
  gate_state <- if (should_stop) "INCOMPLETE_proposal" else
    if (opt$gate_review == "apply") "PASS_reviewed" else "REVIEW_auto_applied"
  reason <- if (opt$gate_review == "apply") receipt_validation$reason else
    if (unresolved_qc) "QC uncertainties require review; unattended gating stopped." else
    if (opt$gate_review == "auto") "no QC uncertainties" else "Gate thresholds proposed but not applied."
  if (opt$gate_review == "auto" && !should_stop)
    review_write_auto_receipt_state("gating", proposal_record, opt$outdir)
  write_gating_state(gate_state, !should_stop, reason,
                     reviewed_thresholds = isTRUE(receipt_validation$valid))
  if (should_stop) {
    logmsg("=== GATING CHECKPOINT -- an external user decision is required ===")
    logmsg("ACTION: review %s, %s, and every directly linkable figure in %s.",
           tmpl_path, uncertainty_path, file.path(opt$outdir, "review_artifact_index.csv"))
    logmsg("Edit final_cutoff/apply, then rerun with --gate-review apply --thresholds <edited.csv> --review-receipt <receipt.json>.")
    logmsg("(No SCE is written while this checkpoint is incomplete.)")
    quit(save = "no", status = 3L)
  }
  fs <- pass$fs
  }
  logmsg("NOTE: CyTOF bead *normalization* (CATALYST::normCytof) is recommended when bead channels")
  logmsg("      are present; this script does threshold-based bead *removal*. See references/qc_gating.md.")
} else {
  review_record_skipped_gating(opt$outdir)
  write_gating_state("REVIEW_qc_skipped", FALSE,
                     "QC gating was skipped; input must already be clean and this remains non-pass.")
  logmsg("QC gating SKIPPED (--qc off). Use only for known pre-cleaned data (with documented upstream QC).")
}


# ------------------------------------------------------------------ build CATALYST SCE + transform
md$file_name <- as.character(md$file_name)
factors <- intersect(c("condition", "batch", "patient"), colnames(md))
sce <- CATALYST::prepData(
  fs, panel = panel, md = md,
  features = panel$fcs_colname,
  panel_cols = list(channel = "fcs_colname", antigen = "antigen", class = "marker_class"),
  md_cols   = list(file = "file_name", id = "sample_id", factors = factors),
  transform = FALSE,
  cofactor  = 5,
  FACS = (modality == "flow"))

# Reuse the exact transform object used for samples, controls, and gate estimation.
# CATALYST exposes antigen names as SCE row names and preserves detector names in
# rowData$channel_name; the transform contract is keyed by those detector names.
SummarizedExperiment::assay(sce, "exprs") <- transform_sce_counts(sce, TRANSFORM_DEF)
logmsg("Final expression assay uses the recorded %s transform contract without re-estimation.", TRANSFORM_DEF$method)

# ------------------------------------------------------------------ CyTOF bead normalization (item 8)
# Opt-in (default off). CATALYST::normCytof normalizes signal to bead intensity over acquisition
# time and removes bead (and bead-bead doublet / low-signal bead) events.
# When off (default), the
# SCE is identical to v2.1.0 (no drift).
if (cytof_norm_on) {
  beads_arg <- if (opt$beads %in% c("dvs", "beta")) opt$beads else suppressWarnings(as.numeric(strsplit(opt$beads, ",")[[1]]))
  logmsg("--- CyTOF bead normalization (item 8): CATALYST::normCytof (beads=%s, k=%d) ---", opt$beads, opt$cytof_norm_k)
  n_before <- ncol(sce)
  nc <- tryCatch(
    CATALYST::normCytof(sce, beads = beads_arg, k = opt$cytof_norm_k,
                        assays = c("counts", "exprs"), overwrite = TRUE,
                        transform = TRUE, plot = FALSE, verbose = FALSE),
    error = function(e) e)
  if (inherits(nc, "error")) {
    logmsg("WARNING: normCytof failed (%s); proceeding WITHOUT bead normalization.", conditionMessage(nc))
  } else {
    sce_norm <- if (is.list(nc) && !is.null(nc$data)) nc$data else nc
    n_removed <- if (is.list(nc) && !is.null(nc$removed)) ncol(nc$removed) else (n_before - ncol(sce_norm))
    n_beads   <- if (is.list(nc) && !is.null(nc$beads)) ncol(nc$beads) else NA_integer_
    sce <- sce_norm
    cytof_norm_summary$applied  <- TRUE
    cytof_norm_summary$n_removed <- as.integer(n_removed)
    cytof_norm_summary$n_beads   <- if (is.na(n_beads)) NA_integer_ else as.integer(n_beads)
    logmsg("normCytof applied: %d -> %d cells (%d removed; %s bead events identified).",
           n_before, ncol(sce), n_removed, ifelse(is.na(n_beads), "NA", as.character(n_beads)))
  }
}

# ------------------------------------------------------------------ QC summary + save
tm <- rownames(sce)[SummarizedExperiment::rowData(sce)$marker_class == "type"]
ex <- SummarizedExperiment::assay(sce, "exprs")
logmsg("--- Post-QC SCE ---")
logmsg("cells=%d | type_markers=%d | samples=%d", ncol(sce), length(tm), length(unique(sce$sample_id)))
logmsg("non-finite events in exprs: %d", sum(!is.finite(ex)))
logmsg("marker range: min=%.3f max=%.3f", min(ex[tm, ], na.rm = TRUE), max(ex[tm, ], na.rm = TRUE))
S4Vectors::metadata(sce)$qc <- list(modality = modality, transform = transform, cofactor = cofactor,
                                    compensation = comp_applied, qc_gating = qc_on, provenance = prov,
                                    gate_params = list(singlet_mad_k = opt$singlet_mad_k,
                                                       debris_pct = opt$debris_pct,
                                                       overgate_alarm = opt$overgate_alarm),
                                    # ---- v2.2.0 (diagnostics-on / removal-opt-in) ----
                                    time_qc = time_qc_summary,
                                    compensation_diag = comp_info,
                                    harmonization = harmonization,
                                    cytof_norm = cytof_norm_summary,
                                    gate_engine = opt$gate_engine,
                                    gate_hierarchy = gate_hierarchy)
saveRDS(sce, file.path(opt$outdir, "sce_prepped.rds"))
logmsg("Saved: %s", file.path(opt$outdir, "sce_prepped.rds"))
logmsg("=== 01 complete ===")
