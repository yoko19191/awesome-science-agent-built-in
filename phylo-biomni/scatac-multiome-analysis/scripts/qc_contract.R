# Pure ATAC QC calculations. Keep numerator and denominator sources explicit.
FRIP_PERCENT_SCALE <- 100

FRIP_METRIC_SCHEMAS <- list(
  cell_ranger_arc = list(
    peak = "atac_peak_region_fragments",
    total = "atac_fragments",
    blacklist = "atac_blacklist_region_fragments",
    source = paste(
      "Cell Ranger ARC per-barcode metrics:",
      "atac_peak_region_fragments / atac_fragments"
    )
  ),
  cell_ranger_atac = list(
    peak = "peak_region_fragments",
    total = "passed_filters",
    blacklist = "blacklist_region_fragments",
    source = paste(
      "Cell Ranger ATAC singlecell.csv:",
      "peak_region_fragments / passed_filters"
    )
  )
)

compute_frip_percent <- function(peak_fragments, total_fragments) {
  peak_fragments <- as.numeric(peak_fragments)
  total_fragments <- as.numeric(total_fragments)
  if (length(peak_fragments) != length(total_fragments)) {
    stop("FRiP numerator and denominator lengths differ")
  }
  if (any(!is.finite(peak_fragments)) || any(!is.finite(total_fragments))) {
    stop("FRiP inputs contain missing or non-finite values")
  }
  if (any(peak_fragments < 0) || any(total_fragments <= 0)) {
    stop("FRiP requires nonnegative peak fragments and positive total fragments")
  }
  if (any(peak_fragments > total_fragments)) {
    stop("peak-fragment count exceeds the fragment-file total; incompatible count units")
  }
  FRIP_PERCENT_SCALE * peak_fragments / total_fragments
}

resolve_authoritative_frip_metrics <- function(metadata) {
  if (is.null(metadata)) return(NULL)
  if (!is.data.frame(metadata)) stop("FRiP metadata must be a data frame")

  resolved <- list()
  for (schema_name in names(FRIP_METRIC_SCHEMAS)) {
    schema <- FRIP_METRIC_SCHEMAS[[schema_name]]
    pair <- c(schema$peak, schema$total)
    present <- pair %in% colnames(metadata)
    if (any(present) && !all(present)) {
      stop(
        "incomplete authoritative FRiP metric pair for ", schema_name, ": expected ",
        paste(pair, collapse = " and ")
      )
    }
    if (all(present)) {
      percent <- compute_frip_percent(metadata[[schema$peak]], metadata[[schema$total]])
      resolved[[schema_name]] <- list(
        percent = percent,
        peak_fragments = as.numeric(metadata[[schema$peak]]),
        total_fragments = as.numeric(metadata[[schema$total]]),
        source = schema$source,
        schema = schema_name,
        blacklist_column = if (schema$blacklist %in% colnames(metadata)) schema$blacklist else NULL
      )
    }
  }

  if (!length(resolved)) return(NULL)
  if (length(resolved) > 1) {
    reference <- resolved[[1]]$percent
    agrees <- vapply(
      resolved[-1],
      function(item) isTRUE(all.equal(reference, item$percent, tolerance = 1e-8)),
      logical(1)
    )
    if (!all(agrees)) {
      stop("conflicting authoritative FRiP metric schemas in per-barcode metadata")
    }
  }

  # Prefer the ARC-native names when both equivalent schemas are supplied.
  if ("cell_ranger_arc" %in% names(resolved)) {
    return(resolved$cell_ranger_arc)
  }
  resolved[[1]]
}

resolve_frip_for_input <- function(
  metadata,
  peak_matrix_counts = NULL,
  fragment_totals = NULL,
  cell_ranger_arc_combined = FALSE
) {
  authoritative <- resolve_authoritative_frip_metrics(metadata)
  if (!is.null(authoritative)) return(authoritative)

  if (isTRUE(cell_ranger_arc_combined)) {
    stop(
      "Cell Ranger ARC combined matrices require authoritative per-barcode ",
      "atac_peak_region_fragments and atac_fragments metrics; Peaks assay counts are ",
      "cut-site counts and cannot be divided by fragment totals"
    )
  }
  if (is.null(peak_matrix_counts) || is.null(fragment_totals)) {
    stop("FRiP is not computable without an authoritative metric pair or compatible fallback counts")
  }

  list(
    percent = compute_frip_percent(peak_matrix_counts, fragment_totals),
    peak_fragments = as.numeric(peak_matrix_counts),
    total_fragments = as.numeric(fragment_totals),
    source = "peak-matrix fragment counts / fragment-file frequency_count",
    schema = "compatible_peak_matrix_fallback",
    blacklist_column = NULL
  )
}
