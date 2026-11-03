# Conservative marker normalization and evidence-gated population proposals.

annotation_strip_fluorophore <- function(x) {
  fluorophore <- paste(c(
    "FITC", "PE", "APC", "PCP", "PCPCY5\\.?5", "PERCP(?:-?CY5\\.?5)?",
    "APC-?H7", "APC-?R700", "APC-?CY7", "PE-?CY5", "PE-?CY5\\.?5", "PE-?CY7",
    "V450", "V500", "BV[0-9]{3}", "BUV[0-9]{3}", "BB[0-9]{3}",
    "ALEXA[ -]?FLUOR[ -]?[0-9]{3}", "AF[0-9]{3}", "EFLUOR[ -]?[0-9]{3}",
    "PACIFIC[ -]?(?:BLUE|ORANGE)", "AMCYAN", "QDOT[ -]?[0-9]{3}",
    "VIO(?:BLUE|GREEN|BRIGHT)[ -]?[0-9]*", "SPARK[ -]?[0-9]{3}", "NOVAFLUOR[ -]?[0-9]{3}"
  ), collapse = "|")
  pattern <- paste0("[[:space:]_/-]+(?:", fluorophore, ")(?:[-_. ]?(?:A|H|W))?$")
  gsub(pattern, "", trimws(as.character(x)), ignore.case = TRUE, perl = TRUE)
}

annotation_normalize_marker <- function(x) {
  x <- toupper(annotation_strip_fluorophore(x))
  x <- gsub("Μ|Μ", "U", x)
  x <- gsub("^[0-9]+", "", x)
  x <- gsub("(\\.FCS)?[-_. ](A|H|W)$", "", x)
  x <- gsub("[^A-Z0-9]", "", x)
  aliases <- c("HLA-DR" = "HLADR", "HLADR" = "HLADR", "NCAM1" = "CD56",
               "FCGR3A" = "CD16", "MS4A1" = "CD20", "PTPRC" = "CD45")
  hit <- match(x, gsub("[^A-Z0-9]", "", names(aliases)))
  x[!is.na(hit)] <- unname(aliases[hit[!is.na(hit)]])
  x
}

annotation_signatures <- function() list(
  `CD4 T cell` = list(required = c("CD3", "CD4"), any = character(), exclude = c("CD8", "CD19", "CD14")),
  `CD8 T cell` = list(required = c("CD3", "CD8"), any = character(), exclude = c("CD4", "CD19", "CD14")),
  `B cell` = list(required = character(), any = c("CD19", "CD20", "CD22", "CD79A"), min_any = 2L,
                  exclude = c("CD3", "CD14")),
  `NK cell` = list(required = character(), any = c("CD56", "CD16"), min_any = 1L,
                   exclude = c("CD3", "CD19", "CD14")),
  `Monocyte` = list(required = character(), any = c("CD14", "CD33", "CD64", "CD11B"), min_any = 2L,
                    exclude = c("CD3", "CD19", "CD56")),
  `Dendritic cell` = list(required = c("HLADR", "CD11C"), any = character(),
                          exclude = c("CD3", "CD19", "CD14")),
  `HSPC` = list(required = c("CD34"), any = c("CD117", "CD123", "CD38"), min_any = 1L,
                exclude = c("CD3", "CD19", "CD14"))
)

annotation_signature_evidence <- function(z_column, normalized_markers, threshold = 0.5) {
  names(z_column) <- normalized_markers
  signatures <- annotation_signatures()
  rows <- lapply(names(signatures), function(label) {
    signature <- signatures[[label]]
    required <- signature$required
    any_markers <- signature$any
    minimum_any <- if (is.null(signature$min_any)) 0L else signature$min_any
    measured_required <- intersect(required, normalized_markers)
    measured_any <- intersect(any_markers, normalized_markers)
    missing <- setdiff(c(required, any_markers), normalized_markers)
    required_ok <- length(measured_required) == length(required) &&
      all(z_column[measured_required] > threshold)
    any_positive <- measured_any[z_column[measured_any] > threshold]
    any_ok <- length(any_positive) >= minimum_any
    exclusion_measured <- intersect(signature$exclude, normalized_markers)
    conflicts <- exclusion_measured[z_column[exclusion_measured] > threshold]
    supported <- required_ok && any_ok && !length(conflicts)
    support <- unique(c(measured_required[z_column[measured_required] > threshold], any_positive))
    score <- if (length(support)) mean(z_column[support], na.rm = TRUE) else -Inf
    data.frame(label = label, supported = supported, score = score,
               supporting_markers = paste(support, collapse = ";"),
               conflicting_markers = paste(conflicts, collapse = ";"),
               missing_markers = paste(missing, collapse = ";"), stringsAsFactors = FALSE)
  })
  do.call(rbind, rows)
}

annotation_cellmarker_candidates <- function(z_column, normalized_markers, sets) {
  if (is.null(sets) || !length(sets)) return(data.frame())
  rows <- lapply(names(sets), function(label) {
    overlap <- intersect(unique(annotation_normalize_marker(sets[[label]])), normalized_markers)
    score <- if (length(overlap) >= 2L) mean(z_column[match(overlap, normalized_markers)], na.rm = TRUE) else NA_real_
    data.frame(label = label, overlap = paste(overlap, collapse = ";"), n_overlap = length(overlap),
               score = score, stringsAsFactors = FALSE)
  })
  do.call(rbind, rows)
}

annotation_propose <- function(z, measured_markers, cellmarker_sets = NULL) {
  normalized <- annotation_normalize_marker(measured_markers)
  rows <- lapply(colnames(z), function(cluster) {
    evidence <- annotation_signature_evidence(z[, cluster], normalized)
    supported <- evidence[evidence$supported, , drop = FALSE]
    supported <- supported[order(-supported$score, supported$label), , drop = FALSE]
    cm <- annotation_cellmarker_candidates(z[, cluster], normalized, cellmarker_sets)
    if (nrow(cm)) {
      cm <- cm[is.finite(cm$score) & cm$score > 0, , drop = FALSE]
      cm <- cm[order(-cm$score, cm$label), , drop = FALSE]
    }
    alternatives <- unique(c(supported$label, head(cm$label, 3L)))
    if (nrow(supported) == 1L) {
      best <- supported[1, ]
      status <- "SUPPORTED"; label <- best$label; confidence <- "moderate"
      supporting <- best$supporting_markers; conflicting <- best$conflicting_markers
      missing <- best$missing_markers
      source <- paste(c("conservative_lineage_signature", if (nrow(cm)) "cellmarker_context"), collapse = ";")
    } else {
      status <- "UNRESOLVED"; label <- ""; confidence <- "insufficient"
      supporting <- if (nrow(supported)) paste(unique(unlist(strsplit(supported$supporting_markers, ";", fixed = TRUE))), collapse = ";") else ""
      conflicting <- if (nrow(supported)) paste(unique(unlist(strsplit(supported$conflicting_markers, ";", fixed = TRUE))), collapse = ";") else ""
      missing <- paste(unique(unlist(strsplit(evidence$missing_markers, ";", fixed = TRUE))), collapse = ";")
      source <- paste(c(if (nrow(supported) > 1L) "conflicting_lineage_signatures" else
        "insufficient_defining_markers", if (nrow(cm)) "cellmarker_supplemental_alternatives"), collapse = ";")
    }
    data.frame(cluster = cluster, proposed_population = label, proposal_status = status,
               proposal_confidence = confidence, proposal_source = source,
               supporting_markers = supporting, conflicting_markers = conflicting,
               missing_defining_markers = missing,
               alternative_populations = paste(head(alternatives, 3L), collapse = ";"), stringsAsFactors = FALSE)
  })
  do.call(rbind, rows)
}
