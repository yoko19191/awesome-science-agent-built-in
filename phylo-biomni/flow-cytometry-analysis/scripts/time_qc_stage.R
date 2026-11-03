# Acquisition-time QC orchestration. Pure gate calculations and plotting live in gating_engine.R.
#
# Two invariants this stage owns:
#  * Margin/saturation QC sees acquisition-scale values. Compensation is a linear re-mix that
#    legitimately yields negative and out-of-range intensities, so the FCS $PnR bounds describe
#    only the uncompensated matrix. `acquisition_fs` is the pre-compensation flowSet with 1:1
#    event correspondence to `fs`; rate and signal QC run on the (compensated) `fs` that
#    continues downstream, and any removal is applied to `fs`.
#  * Cohort percentages are pooled over events, sum(flagged)/sum(total), never as the unweighted
#    mean of per-sample percentages.

TIME_QC_BINS <- 100L
TIME_QC_MODES <- c("off", "report", "remove", "auto")

time_qc_pooled_pct <- function(flagged, total) {
  flagged <- suppressWarnings(as.numeric(flagged)); total <- suppressWarnings(as.numeric(total))
  use <- is.finite(flagged) & is.finite(total)
  denominator <- sum(total[use])
  if (!any(use) || !is.finite(denominator) || denominator <= 0) return(NA_real_)
  100 * sum(flagged[use]) / denominator
}

run_time_qc_stage <- function(fs, requested_mode, backend, mad_k, modality, qc_on, figdir, logmsg,
                              acquisition_fs = NULL) {
  if (backend != "native")
    stop("Unsupported time-QC backend: ", backend,
         ". This implementation supports native MAD only; choose native explicitly or use a separately validated backend workflow.")
  if (length(requested_mode) != 1L || is.na(requested_mode) ||
      !requested_mode %in% TIME_QC_MODES)
    stop("Unsupported --time-qc mode: '", paste(requested_mode, collapse = ","),
         "'. Choose one of ", paste(TIME_QC_MODES, collapse = ", "),
         "; an unknown mode is never silently treated as off.")
  mode <- switch(requested_mode, off = "off", report = "report", remove = "remove",
                 auto = if (modality == "flow" && qc_on) "report" else "off")
  backend_used <- "native"
  summary <- list(mode = mode, backend = backend_used, requested_backend = backend,
                  pct_rate = NA_real_, pct_signal = NA_real_,
                  pct_margin = NA_real_, removed = 0L, n_events = 0L,
                  margin_source = if (is.null(acquisition_fs)) "qc_matrix" else "acquisition")
  diagnostic_columns <- c("sample_id", "mode", "requested_backend", "backend_used", "time_channel",
                          "n_events", "pct_rate", "pct_signal", "pct_margin", "pct_total",
                          "removed_events", "state",
                          "n_rate_flagged", "n_signal_flagged", "n_margin_flagged", "n_total_flagged")
  empty_diagnostics <- as.data.frame(setNames(
    replicate(length(diagnostic_columns), character(0), simplify = FALSE), diagnostic_columns))
  if (mode == "off") return(list(fs = fs, summary = summary, diagnostics = empty_diagnostics, qc_details = list()))
  if (is.null(acquisition_fs)) acquisition_fs <- fs
  logmsg("--- Time-based acquisition QC | mode=%s | backend=%s | mad_k=%g | bins=%d | margin source=%s ---",
         mode, backend, mad_k, TIME_QC_BINS, summary$margin_source)
  sample_names <- flowCore::sampleNames(fs); frames <- list()
  if (!all(sample_names %in% flowCore::sampleNames(acquisition_fs)))
    stop("The acquisition-scale flowSet for margin QC does not contain every QC sample.")
  counts <- list(rate = integer(0), signal = integer(0), margin = integer(0), total_flagged = integer(0), total = integer(0))
  total_removed <- 0L
  diagnostic_rows <- list(); qc_details <- list()
  for (sample_id in sample_names) {
    frame <- fs[[sample_id]]; expression <- flowCore::exprs(frame); channels <- colnames(expression)
    acquisition_frame <- acquisition_fs[[sample_id]]; acquisition <- flowCore::exprs(acquisition_frame)
    if (nrow(acquisition) != nrow(expression))
      stop(sprintf("[%s] the acquisition-scale matrix has %d events but the QC matrix has %d; margin QC requires 1:1 event correspondence.",
                   sample_id, nrow(acquisition), nrow(expression)))
    time_channel <- channels[grepl("Time", channels, ignore.case = TRUE)][1]
    if (is.na(time_channel)) {
      logmsg("  [%s] no Time channel; time-QC skipped for this sample.", sample_id)
      diagnostic_rows[[length(diagnostic_rows) + 1L]] <- data.frame(
        sample_id = sample_id, mode = mode, requested_backend = backend, backend_used = backend_used,
        time_channel = "", n_events = nrow(expression), pct_rate = NA_real_, pct_signal = NA_real_,
        pct_margin = NA_real_, pct_total = NA_real_, removed_events = 0L, state = "NOT_EVALUABLE_no_time",
        n_rate_flagged = NA_integer_, n_signal_flagged = NA_integer_, n_margin_flagged = NA_integer_,
        n_total_flagged = NA_integer_, stringsAsFactors = FALSE)
      frames[[sample_id]] <- frame; next
    }
    marker_channels <- setdiff(channels, time_channel)
    if (!all(c(time_channel, marker_channels) %in% colnames(acquisition)))
      stop(sprintf("[%s] the acquisition-scale matrix lacks QC channels: %s.", sample_id,
                   paste(setdiff(c(time_channel, marker_channels), colnames(acquisition)), collapse = ", ")))
    # flowCore::compensate can round the (untouched) Time column to single precision; compare
    # with a float32-safe tolerance rather than exact equality.
    if (!isTRUE(all.equal(as.numeric(acquisition[, time_channel]), as.numeric(expression[, time_channel]),
                          tolerance = 1e-6, check.attributes = FALSE)))
      stop(sprintf("[%s] acquisition-scale events do not correspond to the QC events (Time channel differs).", sample_id))
    ranges <- tryCatch({
      parameters <- flowCore::pData(flowCore::parameters(acquisition_frame))
      minimum <- suppressWarnings(as.numeric(parameters$minRange))
      maximum <- suppressWarnings(as.numeric(parameters$maxRange))
      names(minimum) <- names(maximum) <- as.character(parameters$name)
      result <- lapply(marker_channels, function(channel)
        if (!is.na(minimum[channel]) && !is.na(maximum[channel])) c(minimum[channel], maximum[channel]) else NULL)
      if (all(vapply(result, is.null, logical(1)))) NULL else result
    }, error = function(error) NULL)
    qc <- time_acquisition_qc(expression[, time_channel], expression[, marker_channels, drop = FALSE],
                              ranges, c("rate", "signal", "margin"),
                              n_bins = TIME_QC_BINS, mad_k = mad_k,
                              margin_expr = acquisition[, marker_channels, drop = FALSE])
    qc_details[[sample_id]] <- qc
    plot_time_qc(qc, sample_id, file.path(figdir, sprintf("time_qc_%s.png", sample_id)))
    counts$rate <- c(counts$rate, qc$n_rate_flagged)
    counts$signal <- c(counts$signal, qc$n_signal_flagged)
    counts$margin <- c(counts$margin, qc$n_margin_flagged)
    counts$total_flagged <- c(counts$total_flagged, qc$n_total_flagged)
    counts$total <- c(counts$total, qc$n_total)
    logmsg("  [%s] time-QC: rate=%.2f%% signal=%.2f%% margin=%.2f%% total=%.2f%% (n=%d)",
           sample_id, ifelse(is.na(qc$pct_rate), 0, qc$pct_rate), ifelse(is.na(qc$pct_signal), 0, qc$pct_signal),
           ifelse(is.na(qc$pct_margin), 0, qc$pct_margin), ifelse(is.na(qc$pct_total), 0, qc$pct_total), qc$n)
    if (mode == "remove") {
      removed <- sum(!qc$keep); total_removed <- total_removed + removed
      frames[[sample_id]] <- frame[qc$keep, ]
      logmsg("  [%s] time-QC REMOVE: dropped %d flagged events (%.2f%%).",
             sample_id, removed, 100 * removed / max(qc$n, 1))
    } else { removed <- 0L; frames[[sample_id]] <- frame }
    diagnostic_rows[[length(diagnostic_rows) + 1L]] <- data.frame(
      sample_id = sample_id, mode = mode, requested_backend = backend, backend_used = backend_used,
      time_channel = time_channel, n_events = qc$n, pct_rate = qc$pct_rate,
      pct_signal = qc$pct_signal, pct_margin = qc$pct_margin, pct_total = qc$pct_total,
      removed_events = removed, state = "REVIEW_diagnostic",
      n_rate_flagged = qc$n_rate_flagged, n_signal_flagged = qc$n_signal_flagged,
      n_margin_flagged = qc$n_margin_flagged, n_total_flagged = qc$n_total_flagged,
      stringsAsFactors = FALSE)
  }
  if (mode == "remove") {
    fs <- as(frames, "flowSet"); flowCore::sampleNames(fs) <- sample_names
  }
  summary$pct_rate <- time_qc_pooled_pct(counts$rate, counts$total)
  summary$pct_signal <- time_qc_pooled_pct(counts$signal, counts$total)
  summary$pct_margin <- time_qc_pooled_pct(counts$margin, counts$total)
  summary$removed <- as.integer(total_removed)
  summary$n_events <- as.integer(sum(counts$total))
  diagnostics <- if (length(diagnostic_rows)) do.call(rbind, diagnostic_rows) else empty_diagnostics
  aggregate_row <- data.frame(
    sample_id = "ALL", mode = mode, requested_backend = backend, backend_used = backend_used,
    time_channel = "aggregate", n_events = sum(suppressWarnings(as.numeric(diagnostics$n_events)), na.rm = TRUE),
    pct_rate = summary$pct_rate, pct_signal = summary$pct_signal, pct_margin = summary$pct_margin,
    pct_total = time_qc_pooled_pct(counts$total_flagged, counts$total),
    removed_events = summary$removed, state = "REVIEW_diagnostic",
    n_rate_flagged = sum(counts$rate, na.rm = TRUE), n_signal_flagged = sum(counts$signal, na.rm = TRUE),
    n_margin_flagged = sum(counts$margin, na.rm = TRUE), n_total_flagged = sum(counts$total_flagged, na.rm = TRUE),
    stringsAsFactors = FALSE)
  diagnostics <- rbind(diagnostics, aggregate_row)
  logmsg("Time-QC summary (pooled over %d events): rate=%.2f%% signal=%.2f%% margin=%.2f%% | removed=%d (mode=%s).",
         summary$n_events,
         ifelse(is.na(summary$pct_rate), 0, summary$pct_rate),
         ifelse(is.na(summary$pct_signal), 0, summary$pct_signal),
         ifelse(is.na(summary$pct_margin), 0, summary$pct_margin), summary$removed, mode)
  if (mode == "report") logmsg("(REPORT mode: 0 events removed by design.)")
  list(fs = fs, summary = summary, diagnostics = diagnostics, qc_details = qc_details)
}
