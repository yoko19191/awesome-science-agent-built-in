# Shared execution contract for preprocessing, controls, and channel metadata.

CYTOMETRY_LINEAR_FLOW <- "FSC|SSC|Time"
CYTOMETRY_LINEAR_CYTOF <- "Time|Event_length"
DEFAULT_LINEAGE_MARKERS <- c("CD15", "CD66B", "CD16", "CD11B")
VIABILITY_PATTERNS <- c("VIABILITY", "LIVE", "DEAD", "FVS", "ZOMBIE", "7-?AAD",
                        "L/D", "AQUA", "GHOST", "EFLUOR ?506", "DAPI", "(^|[^A-Z])PI([^A-Z]|$)",
                        "CISPLATIN", "PT195", "195PT")
DNA_PATTERNS <- c("DNA1", "DNA2", "IR191", "IR193", "191IR", "193IR")

detect_modality <- function(ff) {
  kw <- flowCore::keyword(ff)
  kw1 <- function(x) {
    v <- kw[[x]]
    if (is.null(v) || all(is.na(v))) "" else as.character(v)[1]
  }
  cyt_raw <- trimws(paste(kw1("$CYT"), kw1("$CYTSN")))
  cyt <- toupper(cyt_raw)
  pd <- flowCore::pData(flowCore::parameters(ff))
  chans <- as.character(pd$name)
  descs <- as.character(pd$desc)
  descs[is.na(descs)] <- ""
  mass_like <- grepl("Di$|_Di|[0-9]{2,3}(Nd|Sm|Eu|Gd|Tb|Dy|Ho|Er|Tm|Yb|Lu|La|Ce|Pr|Pt|Ir|Rh|In|Cd|Xe|Ba|Te|Sn|Y|Cs)", chans)
  has_scatter <- any(grepl("FSC|SSC", chans, ignore.case = TRUE))
  is_cytof <- grepl("CYTOF|HELIOS|MASS|DVS|FLUIDIGM", cyt) || mean(mass_like) > 0.3
  is_flow <- has_scatter || grepl("FACS|LSR|FORTESSA|CANTO|SYMPHONY|AURORA|CYTEK|ATTUNE|CALIBUR|NAVIOS|GALLIOS|INFLUX|MOFLO", cyt)
  instrument_spectral <- grepl("AURORA|CYTEK|SPECTRAL|SP6800|ID7000", cyt)
  non_aux <- !grepl("FSC|SSC|Time|Event_length|Center|Offset|Width|Residual", chans, ignore.case = TRUE)
  non_aux_chans <- chans[non_aux]
  non_aux_descs <- descs[non_aux]
  fluor_re <- "FITC|PE|APC|BV|BUV|PE-Cy|APC-Cy|PerCP|Vio|Spark|NovaFluor|eFluor|Brilliant|CD[0-9]|HLA|Ig|TCR|Ki-?67|Granzyme|IFN|IL-?[0-9]|TNF|GM-CSF|CXCR|CCR"
  named_n <- sum(grepl(fluor_re, non_aux_descs, ignore.case = TRUE) |
                 grepl(fluor_re, non_aux_chans, ignore.case = TRUE))
  named_frac <- if (length(non_aux_chans)) named_n / length(non_aux_chans) else 0
  raw_re <- "^(UV|V|B|YG|R|CH|FL)?[0-9]+-?A?$"
  raw_frac <- if (length(non_aux_chans)) mean(grepl(raw_re, non_aux_chans, ignore.case = TRUE)) else 0
  raw_detector <- instrument_spectral && raw_frac > 0.5 && named_frac < 0.2 && length(chans) > 30
  spectral_state <- if (!instrument_spectral) "none" else if (named_frac >= 0.5) "unmixed" else if (raw_detector) "raw" else "unmixed"
  list(
    modality = if (is_cytof && !is_flow) "cytof" else if (is_flow) "flow" else if (is_cytof) "cytof" else "unknown",
    spectral = spectral_state == "raw", spectral_state = spectral_state,
    instrument_spectral = isTRUE(instrument_spectral), named_fluor_frac = named_frac,
    raw_detector = isTRUE(raw_detector), cyt = cyt, cyt_empty = !nzchar(cyt_raw),
    has_scatter = has_scatter, mass_like_n = sum(mass_like), n_chan = length(chans))
}

resolve_channel <- function(panel, query, available = panel$fcs_colname, context = "channel") {
  query <- trimws(as.character(query)[1])
  if (!nzchar(query)) stop(sprintf("Empty %s query.", context))
  pnn <- as.character(panel$fcs_colname)
  pns <- as.character(panel$antigen)
  exact <- unique(c(available[toupper(available) == toupper(query)],
                    pnn[toupper(pns) == toupper(query)]))
  exact <- intersect(exact, available)
  if (length(exact) == 1L) return(list(channel = exact, source = "exact_PnN_or_PnS", query = query))
  if (length(exact) > 1L)
    stop(sprintf("Ambiguous %s '%s': exact metadata match resolves to %s.", context, query, paste(exact, collapse = ", ")))
  matched <- tryCatch(grepl(query, pnn, ignore.case = TRUE, perl = TRUE) |
                      grepl(query, pns, ignore.case = TRUE, perl = TRUE),
                      error = function(e) stop(sprintf("Invalid %s pattern '%s': %s", context, query, conditionMessage(e))))
  hits <- unique(intersect(pnn[matched], available))
  if (!length(hits)) stop(sprintf("Unresolved %s '%s' in $PnN/$PnS metadata.", context, query))
  if (length(hits) > 1L)
    stop(sprintf("Ambiguous %s '%s': metadata pattern matches %s. Use an exact $PnN or $PnS value.",
                 context, query, paste(hits, collapse = ", ")))
  list(channel = hits, source = "unique_PnN_or_PnS_pattern", query = query)
}

resolve_role_channel <- function(panel, available, patterns, role, required = FALSE) {
  pnn <- as.character(panel$fcs_colname)
  pns <- as.character(panel$antigen)
  pat <- paste(patterns, collapse = "|")
  pns_hits <- unique(intersect(pnn[grepl(pat, pns, ignore.case = TRUE, perl = TRUE)], available))
  if (length(pns_hits) == 1L) return(pns_hits)
  if (length(pns_hits) > 1L)
    stop(sprintf("Ambiguous %s channel in $PnS metadata: %s. Supply an exact channel through the relevant option.",
                 role, paste(pns_hits, collapse = ", ")))
  hits <- unique(intersect(pnn[grepl(pat, pnn, ignore.case = TRUE, perl = TRUE)], available))
  if (!length(hits)) {
    if (required) stop(sprintf("Required %s channel is absent from $PnN/$PnS metadata.", role))
    return(NA_character_)
  }
  if (length(hits) > 1L)
    stop(sprintf("Ambiguous %s channel: %s. Supply an exact channel through the relevant option.",
                 role, paste(hits, collapse = ", ")))
  hits
}

resolve_dna_channels <- function(panel, available = panel$fcs_colname, user_arg = NA_character_) {
  if (!is.na(user_arg) && nzchar(trimws(user_arg))) {
    requested <- trimws(strsplit(user_arg, ",", fixed = TRUE)[[1]])
    requested <- requested[nzchar(requested)]
    if (!length(requested) || length(requested) > 2L)
      stop("--dna-channels requires one or two comma-separated exact $PnN or $PnS values.")
    resolved <- vapply(requested, function(query)
      resolve_channel(panel, query, available, context = "DNA channel")$channel, character(1))
    if (anyDuplicated(resolved)) stop("--dna-channels resolves to the same detector more than once.")
    return(unname(resolved))
  }
  pnn <- as.character(panel$fcs_colname)
  pns <- as.character(panel$antigen)
  pattern <- paste(DNA_PATTERNS, collapse = "|")
  matched <- grepl(pattern, pnn, ignore.case = TRUE, perl = TRUE) |
    grepl(pattern, pns, ignore.case = TRUE, perl = TRUE)
  hits <- unique(intersect(pnn[matched], available))
  if (length(hits) > 2L)
    stop(sprintf("More than two DNA channels were detected (%s). Supply one or two exact values with --dna-channels.",
                 paste(hits, collapse = ", ")))
  hits
}

prepare_hdcyto_channels <- function(expression, channel_data) {
  required <- c("channel_name", "marker_name", "marker_class")
  missing <- setdiff(required, colnames(channel_data))
  if (length(missing))
    stop(sprintf("HDCytoData channel metadata is missing: %s.", paste(missing, collapse = ", ")))
  detector_names <- trimws(as.character(channel_data$channel_name))
  marker_names <- trimws(as.character(channel_data$marker_name))
  if (ncol(expression) != length(detector_names))
    stop("HDCytoData expression columns do not match channel metadata rows.")
  if (anyNA(detector_names) || any(!nzchar(detector_names)) || anyDuplicated(detector_names))
    stop("HDCytoData detector channel_name values must be complete and unique.")
  if (anyNA(marker_names) || any(!nzchar(marker_names)))
    stop("HDCytoData marker_name values must be complete.")
  colnames(expression) <- detector_names
  panel <- data.frame(
    fcs_colname = detector_names,
    antigen = marker_names,
    marker_class = as.character(channel_data$marker_class),
    stringsAsFactors = FALSE)
  list(expression = expression, panel = panel)
}

load_hdcyto_input <- function(dataset, modality_override = "auto", logmsg) {
  if (!requireNamespace("HDCytoData", quietly = TRUE))
    stop("HDCytoData not installed. Install via BiocManager into /workspace/.Rlib.")
  logmsg("Loading requested public HDCytoData dataset: %s", dataset)
  se <- do.call(paste0(dataset, "_SE"), list(), envir = asNamespace("HDCytoData"))
  package_version <- as.character(utils::packageVersion("HDCytoData"))
  logmsg("Source package: HDCytoData %s. Descriptive benchmark metadata and reference labels were not inspected.",
         package_version)

  expression <- SummarizedExperiment::assay(se)
  channel_data <- SummarizedExperiment::colData(se)
  cell_data <- SummarizedExperiment::rowData(se)
  sample_column <- intersect(c("patient_id", "sample_id", "group_id"), colnames(cell_data))[1]
  inferred_single_sample <- is.na(sample_column)
  samples <- if (inferred_single_sample) rep(dataset, nrow(cell_data)) else
    as.character(cell_data[[sample_column]])
  if (inferred_single_sample)
    logmsg("HDCytoData row metadata has no sample identifier; treating the dataset as one sample named %s.", dataset)
  prepared <- prepare_hdcyto_channels(expression, channel_data)

  frames <- lapply(unique(samples), function(sample_id) {
    matrix <- as.matrix(prepared$expression[samples == sample_id, , drop = FALSE])
    flowCore::flowFrame(matrix)
  })
  names(frames) <- unique(samples)
  flow_set <- as(frames, "flowSet")
  modality_detection <- detect_modality(flow_set[[1]])
  metadata <- data.frame(
    file_name = unique(samples), sample_id = unique(samples), condition = "unspecified",
    stringsAsFactors = FALSE)

  list(
    fs = flow_set,
    md = metadata,
    panel = prepared$panel,
    modality = if (modality_override == "auto") modality_detection$modality else modality_override,
    modality_detection = modality_detection,
    inferred_single_sample = inferred_single_sample,
    provenance = list(dataset = dataset, package = "HDCytoData", package_version = package_version,
                      reference_labels_loaded = FALSE))
}

resolve_lineage_markers <- function(panel, user_arg, available = panel$fcs_colname) {
  requested <- if (is.na(user_arg) || !nzchar(user_arg)) DEFAULT_LINEAGE_MARKERS else
    toupper(trimws(strsplit(user_arg, ",", fixed = TRUE)[[1]]))
  out <- character(0)
  for (marker in requested[nzchar(requested)]) {
    hit <- tryCatch(resolve_channel(panel, paste0("\\b", marker, "\\b"), available,
                                    context = sprintf("lineage marker %s", marker)),
                    error = function(e) {
                      if (startsWith(conditionMessage(e), "Unresolved")) return(NULL)
                      stop(e)
                    })
    if (!is.null(hit)) out[marker] <- hit$channel
  }
  out
}

est_arcsinh_cofactor <- function(mat) {
  q <- apply(mat, 2, function(x) stats::quantile(abs(x[is.finite(x)]), 0.2, na.rm = TRUE))
  stats::median(pmax(q, 1), na.rm = TRUE)
}

build_transform_definition <- function(fs, modality, transform, cofactor) {
  channels <- flowCore::colnames(fs)
  linear_re <- if (identical(modality, "cytof")) CYTOMETRY_LINEAR_CYTOF else CYTOMETRY_LINEAR_FLOW
  transformed <- channels[!grepl(linear_re, channels, ignore.case = TRUE)]
  if (identical(transform, "arcsinh")) {
    cf <- if (is.finite(cofactor)) cofactor else est_arcsinh_cofactor(flowCore::exprs(fs[[1]])[, transformed, drop = FALSE])
    tl <- if (length(transformed)) flowCore::transformList(transformed, flowCore::arcsinhTransform(a = 0, b = 1 / cf, c = 0)) else NULL
    return(list(method = "arcsinh", cofactor = cf, channels = transformed, flowcore = tl))
  }
  if (!identical(transform, "logicle")) stop(sprintf("Unsupported transform '%s'.", transform))
  tl <- if (length(transformed)) flowCore::estimateLogicle(fs[[1]], channels = transformed) else NULL
  list(method = "logicle", cofactor = NA_real_, channels = transformed, flowcore = tl)
}

apply_transform_definition <- function(x, definition) {
  if (is.null(definition$flowcore)) return(x)
  available <- flowCore::colnames(x)
  transform_names <- names(definition$flowcore@transforms)
  shared <- intersect(transform_names, available)
  if (!length(shared))
    stop("Input has none of the channels in the recorded transform definition.")
  transform_list <- if (length(shared) == length(transform_names)) definition$flowcore else
    methods::new("transformList", transforms = definition$flowcore@transforms[shared],
                 transformationId = definition$flowcore@transformationId)
  flowCore::transform(x, transform_list)
}

transform_sce_counts <- function(sce, definition) {
  counts <- SummarizedExperiment::assay(sce, "counts")
  row_metadata <- SummarizedExperiment::rowData(sce)
  if (!("channel_name" %in% colnames(row_metadata)))
    stop("SCE rowData is missing the required channel_name ($PnN) mapping.")
  channels <- trimws(as.character(row_metadata$channel_name))
  if (length(channels) != nrow(counts) || anyNA(channels) || any(!nzchar(channels)))
    stop("SCE channel_name ($PnN) mapping is incomplete.")
  if (anyDuplicated(channels))
    stop(sprintf("SCE channel_name ($PnN) mapping is ambiguous: %s.",
                 paste(unique(channels[duplicated(channels)]), collapse = ", ")))

  frame_values <- t(counts)
  colnames(frame_values) <- channels
  ff_counts <- flowCore::flowFrame(frame_values)
  ff_exprs <- apply_transform_definition(ff_counts, definition)
  transformed <- t(flowCore::exprs(ff_exprs))
  dimnames(transformed) <- dimnames(counts)
  transformed
}

prepare_control_frame <- function(ff, modality, compensation_matrix, transform_definition) {
  if (identical(modality, "flow") && !is.null(compensation_matrix))
    ff <- flowCore::compensate(ff, compensation_matrix)
  apply_transform_definition(ff, transform_definition)
}

control_quantile <- function(values, percentile) {
  if (!is.finite(percentile) || percentile <= 0 || percentile >= 1)
    stop("Control percentile must be between 0 and 1.")
  as.numeric(stats::quantile(values[is.finite(values)], percentile, na.rm = TRUE))
}

write_transform_contract <- function(definition, path) {
  channels <- if (length(definition$channels)) definition$channels else "(no transformed channels)"
  rows <- data.frame(channel = channels, method = definition$method,
                     cofactor = ifelse(is.finite(definition$cofactor), definition$cofactor, NA_real_),
                     state = "APPLIED", stringsAsFactors = FALSE)
  utils::write.csv(rows, path, row.names = FALSE)
  invisible(rows)
}

lineage_bias_review <- function(viability_keep, lineage_values, min_events = 100L, delta_pp = 10) {
  ok <- is.finite(lineage_values) & !is.na(viability_keep)
  if (sum(ok) < 2L * min_events) return(list(evaluable = FALSE, review = FALSE, reason = "too_few_events"))
  fv <- find_valley(lineage_values[ok])
  dip <- dip_unimodal(lineage_values[ok])
  if (!is.finite(fv$cutoff) || fv$depth < 0.10 || isTRUE(dip$unimodal))
    return(list(evaluable = FALSE, review = FALSE, reason = "lineage_not_confidently_resolved"))
  positive <- lineage_values > fv$cutoff
  n_pos <- sum(ok & positive)
  n_neg <- sum(ok & !positive)
  if (n_pos < min_events || n_neg < min_events)
    return(list(evaluable = FALSE, review = FALSE, reason = "lineage_group_below_minimum"))
  pos_removed <- mean(!viability_keep[ok & positive])
  neg_removed <- mean(!viability_keep[ok & !positive])
  delta <- 100 * (pos_removed - neg_removed)
  list(evaluable = TRUE, review = delta > delta_pp, reason = if (delta > delta_pp) "lineage_biased_removal" else "within_limit",
       cutoff = fv$cutoff, n_positive = n_pos, n_negative = n_neg,
       positive_removed_pct = 100 * pos_removed, negative_removed_pct = 100 * neg_removed,
       delta_pp = delta)
}
