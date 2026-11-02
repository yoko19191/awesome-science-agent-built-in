# Normalized-ADT interchange contract shared by the R producer and Python H5MU builder.
canonical_adt_feature_names <- function(features, context = "ADT feature panel") {
  canonical <- gsub("_", "-", trimws(as.character(features)), fixed = TRUE)
  if (any(!nzchar(canonical))) stop(sprintf("%s contains an empty ADT feature name", context))
  collisions <- unique(canonical[duplicated(canonical) | duplicated(canonical, fromLast = TRUE)])
  if (length(collisions)) {
    stop(sprintf("canonical ADT feature collision in %s: %s",
                 context, paste(head(sort(collisions), 5), collapse = ", ")))
  }
  canonical
}

write_normalized_adt_csv <- function(norm, path) {
  if (is.null(rownames(norm)) || anyDuplicated(rownames(norm))) {
    stop("normalized ADT matrix needs unique feature names")
  }
  rownames(norm) <- canonical_adt_feature_names(
    rownames(norm), "normalized ADT export"
  )
  if (is.null(colnames(norm)) || anyDuplicated(colnames(norm))) {
    stop("normalized ADT matrix needs unique barcodes")
  }
  if (any(!is.finite(norm))) {
    stop("normalized ADT matrix contains non-finite values")
  }
  if (!any(abs(norm) > .Machine$double.eps)) {
    stop("normalized ADT matrix is entirely zero; refusing to export an empty protein layer")
  }
  adt_export <- data.frame(
    barcode = colnames(norm),
    as.data.frame(t(norm), check.names = FALSE),
    check.names = FALSE
  )
  utils::write.csv(adt_export, path, row.names = FALSE)
  invisible(path)
}

write_adt_counts_csv <- function(counts, path) {
  if (is.null(rownames(counts)) || anyDuplicated(rownames(counts))) {
    stop("raw ADT count matrix needs unique feature names")
  }
  rownames(counts) <- canonical_adt_feature_names(rownames(counts), "raw ADT count export")
  if (is.null(colnames(counts)) || anyDuplicated(colnames(counts))) {
    stop("raw ADT count matrix needs unique barcodes")
  }
  if (any(!is.finite(counts)) || any(counts < 0)) {
    stop("raw ADT count matrix contains invalid counts")
  }
  export <- data.frame(
    barcode = colnames(counts),
    as.data.frame(t(counts), check.names = FALSE),
    check.names = FALSE
  )
  utils::write.csv(export, path, row.names = FALSE)
  invisible(path)
}
