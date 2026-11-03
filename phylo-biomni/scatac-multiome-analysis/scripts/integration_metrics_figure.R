# Presentation-only derivation for the WNN integration-metrics figure.

INTEGRATION_METRIC_KEYS <- c(
  "label_purity",
  "batch_mixing_entropy",
  "silhouette_label",
  "silhouette_batch"
)

INTEGRATION_STATUS_LABELS <- c(
  not_evaluable_independent_labels = "NE-label",
  structurally_degenerate = "Degenerate"
)

INTEGRATION_METRICS_ABSENCE_REASON <- paste(
  "All integration metrics are non-evaluable or structurally degenerate;",
  "no finite numeric score is available to plot."
)

integration_status_label <- function(status) {
  status <- as.character(status)
  known <- unname(INTEGRATION_STATUS_LABELS[status])
  fallback <- vapply(status, function(value) {
    paste(strwrap(gsub("_", " ", value, fixed = TRUE), width = 12L), collapse = "\n")
  }, character(1))
  ifelse(is.na(known), fallback, known)
}

integration_status_caption <- function(statuses) {
  statuses <- unique(as.character(statuses))
  entries <- vapply(statuses, function(status) {
    label <- integration_status_label(status)
    description <- switch(
      status,
      not_evaluable_independent_labels = "not evaluable: independent labels unavailable",
      structurally_degenerate = "structurally degenerate",
      gsub("_", " ", status, fixed = TRUE)
    )
    sprintf("%s = %s", label, description)
  }, character(1))
  paste(entries, collapse = "; ")
}

integration_metric_values <- function(integration_metrics) {
  vapply(INTEGRATION_METRIC_KEYS, function(key) {
    value <- integration_metrics[[key]]
    is_finite_scalar <- !is.null(value) && is.numeric(value) &&
      length(value) == 1L && is.finite(value)
    if (is_finite_scalar) {
      as.numeric(value)
    } else {
      NA_real_
    }
  }, numeric(1))
}

integration_metrics_have_numeric_result <- function(integration_metrics) {
  any(!is.na(integration_metric_values(integration_metrics)))
}

integration_metric_plot_data <- function(integration_metrics) {
  values <- integration_metric_values(integration_metrics)
  statuses <- vapply(INTEGRATION_METRIC_KEYS, function(key) {
    integration_metrics[[paste0(key, "_status")]] %||% "not_evaluable"
  }, character(1))
  data.frame(
    metric = factor(INTEGRATION_METRIC_KEYS, levels = INTEGRATION_METRIC_KEYS),
    value = values,
    status = statuses,
    display_label = ifelse(
      is.na(values), integration_status_label(statuses), sprintf("%.3f", values)
    ),
    label_y = ifelse(is.na(values), 0.06, values),
    stringsAsFactors = FALSE
  )
}

build_integration_metrics_plot <- function(integration_metrics, palette, theme) {
  if (!integration_metrics_have_numeric_result(integration_metrics)) {
    stop(INTEGRATION_METRICS_ABSENCE_REASON, call. = FALSE)
  }
  dfm <- integration_metric_plot_data(integration_metrics)
  non_numeric_statuses <- dfm$status[is.na(dfm$value)]
  ggplot2::ggplot(dfm, ggplot2::aes(x = metric)) +
    ggplot2::geom_col(
      data = dfm[!is.na(dfm$value), , drop = FALSE],
      ggplot2::aes(y = value, fill = metric),
      width = 0.7
    ) +
    ggplot2::geom_text(
      ggplot2::aes(y = label_y, label = display_label),
      vjust = -0.4,
      size = 3
    ) +
    ggplot2::scale_fill_manual(values = palette) +
    ggplot2::coord_cartesian(ylim = c(0, 1.08)) +
    ggplot2::labs(
      x = NULL,
      y = "Computed score (0-1)",
      title = "WNN integration metrics and evaluability states",
      caption = if (length(non_numeric_statuses)) {
        integration_status_caption(non_numeric_statuses)
      } else {
        NULL
      }
    ) +
    theme +
    ggplot2::theme(
      legend.position = "none",
      axis.text.x = ggplot2::element_text(angle = 20, hjust = 1),
      plot.caption = ggplot2::element_text(hjust = 0, size = 8)
    )
}
