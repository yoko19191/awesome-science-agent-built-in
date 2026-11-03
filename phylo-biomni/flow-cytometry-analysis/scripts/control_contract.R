# Load and validate fluorescence controls against the sample preprocessing contract.

load_controls_contract <- function(path, panel, available_channels, modality,
                                   compensation_matrix, transform_definition,
                                   compensation_applied, log_fn) {
  if (is.na(path) || !nzchar(path)) return(NULL)
  if (!file.exists(path)) stop(sprintf("Supplied controls CSV not found: %s", path))
  controls <- utils::read.csv(path, stringsAsFactors = FALSE)
  required <- c("channel", "control_file", "control_type")
  missing <- setdiff(required, names(controls))
  if (length(missing))
    stop(sprintf("Controls CSV missing required column(s): %s", paste(missing, collapse = ", ")))

  out <- new.env(parent = emptyenv())
  ledger <- list()
  for (i in seq_len(nrow(controls))) {
    file <- controls$control_file[i]
    if (is.null(file) || is.na(file) || !file.exists(file))
      stop(sprintf("Supplied control file not found: %s", as.character(file)))
    frame <- tryCatch(
      flowCore::read.FCS(file, transformation = FALSE, truncate_max_range = FALSE),
      error = function(e) e)
    if (inherits(frame, "error"))
      stop(sprintf("Supplied control is unreadable (%s): %s", basename(file), conditionMessage(frame)))

    main_hit <- resolve_channel(panel, controls$channel[i], available_channels,
                                context = "control channel")
    parameter_data <- flowCore::pData(flowCore::parameters(frame))
    antigens <- as.character(parameter_data$desc)
    absent <- is.na(antigens) | !nzchar(antigens)
    antigens[absent] <- as.character(parameter_data$name)[absent]
    control_panel <- data.frame(fcs_colname = as.character(parameter_data$name),
                                antigen = antigens, stringsAsFactors = FALSE)
    control_hit <- resolve_channel(control_panel, main_hit$channel,
                                   colnames(flowCore::exprs(frame)),
                                   context = "control FCS channel")
    control_type <- tolower(trimws(as.character(controls$control_type[i])))
    if (!control_type %in% c("unstained", "fmo"))
      stop(sprintf("Unsupported control_type '%s' for %s.", control_type, basename(file)))
    if (exists(main_hit$channel, envir = out, inherits = FALSE))
      stop(sprintf("Multiple supplied controls resolve to channel %s; make the control contract explicit.",
                   main_hit$channel))

    frame <- tryCatch(
      prepare_control_frame(frame, modality, compensation_matrix, transform_definition),
      error = function(e) e)
    if (inherits(frame, "error"))
      stop(sprintf("Control %s cannot follow sample compensation/transform contract: %s",
                   basename(file), conditionMessage(frame)))
    expression <- flowCore::exprs(frame)
    channel <- control_hit$channel
    percentile <- if ("percentile" %in% names(controls) && !is.na(controls$percentile[i]))
      as.numeric(controls$percentile[i]) else 0.99
    tryCatch(control_quantile(expression[, channel], percentile), error = function(e)
      stop(sprintf("Invalid control percentile for %s: %s", main_hit$channel, conditionMessage(e))))
    assign(main_hit$channel,
           list(values = expression[, channel], type = control_type,
                pct = percentile, used = FALSE), envir = out)
    ledger[[i]] <- data.frame(
      requested = controls$channel[i], channel = main_hit$channel,
      control_type = control_type, compensation = compensation_applied,
      transform = transform_definition$method, used = FALSE, state = "READY",
      stringsAsFactors = FALSE)
    log_fn("  control ready: '%s' -> %s (%s, pct=%.3g, n=%d)",
           controls$channel[i], main_hit$channel, control_type, percentile,
           nrow(expression))
  }
  attr(out, "ledger") <- do.call(rbind, ledger)
  out
}

validate_builtin_control_channels <- function(controls, viability_channel, outdir) {
  if (is.null(controls)) return(invisible(TRUE))
  unsupported <- setdiff(ls(controls), viability_channel)
  if (length(unsupported)) {
    ledger <- attr(controls, "ledger")
    ledger$state[ledger$channel %in% unsupported] <- "INCOMPLETE_unsupported_marker_control"
    utils::write.csv(ledger, file.path(outdir, "control_usage_state.csv"), row.names = FALSE)
    stop(sprintf(paste0("Builtin QC supports control anchoring only for the viability channel. ",
      "Marker controls (%s) need an explicit marker-positivity workflow and review; ",
      "do not omit supplied controls or infer positivity from cluster z-scores."),
      paste(unsupported, collapse = ", ")))
  }
  invisible(TRUE)
}
