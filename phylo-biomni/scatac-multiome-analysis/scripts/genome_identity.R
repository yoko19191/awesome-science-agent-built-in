# Executed genome-identity comparison between independent input declarations and
# the configured annotation build. Seqlevel style is reported separately because
# chr* cannot distinguish hg19 from hg38.
GENOME_BUILD_ALIASES <- list(
  hg38 = c("hg38", "grch38"),
  hg19 = c("hg19", "grch37"),
  mm10 = c("mm10", "grcm38")
)

.genome_text <- function(values) {
  values <- unique(tolower(trimws(as.character(values))))
  values[nzchar(values) & !is.na(values) & values != "auto"]
}

.canonical_build <- function(value) {
  hits <- names(GENOME_BUILD_ALIASES)[vapply(
    GENOME_BUILD_ALIASES, function(aliases) value %in% aliases, logical(1)
  )]
  if (length(hits) == 1L) hits else NA_character_
}

evaluate_genome_identity <- function(configured_build, registry_resources,
                                     input_build_evidence, fragment_seqlevels) {
  violations <- list()
  add_violation <- function(field, expected, observed, reason) {
    violations[[length(violations) + 1L]] <<- list(
      field = field,
      expected = paste(expected, collapse = ";"),
      observed = paste(observed, collapse = ";"),
      reason = reason
    )
  }

  evidence <- lapply(input_build_evidence, .genome_text)
  evidence <- evidence[vapply(evidence, length, integer(1)) > 0L]
  observed_values <- unique(unlist(evidence, use.names = FALSE))
  canonical_by_value <- vapply(observed_values, .canonical_build, character(1))
  canonical_values <- unique(canonical_by_value[!is.na(canonical_by_value)])
  unknown_values <- observed_values[is.na(canonical_by_value)]

  status <- "passed"
  if (!length(observed_values)) {
    status <- "not_evaluable"
    add_violation(
      "input_build_identity", configured_build, "<missing>",
      "no independent matrix, metadata, or explicit input genome declaration is available"
    )
  } else if (length(unknown_values)) {
    status <- "not_evaluable"
    add_violation(
      "input_build_identity", names(GENOME_BUILD_ALIASES), unknown_values,
      "input genome declaration is not a recognized canonical build alias"
    )
  } else if (length(canonical_values) != 1L) {
    status <- "failed"
    add_violation(
      "input_build_identity", configured_build, canonical_values,
      "independent input declarations are ambiguous or disagree"
    )
  } else if (!identical(canonical_values[[1]], configured_build)) {
    status <- "failed"
    add_violation(
      "input_build_identity", configured_build, canonical_values[[1]],
      "independent input genome identity disagrees with the configured annotation build"
    )
  }

  seqlevels <- unique(as.character(fragment_seqlevels))
  seqlevels <- seqlevels[nzchar(seqlevels) & !is.na(seqlevels)]
  seqlevel_style_status <- if (!length(seqlevels)) {
    "not_evaluable"
  } else if (all(startsWith(seqlevels, "chr"))) {
    "passed"
  } else {
    "failed"
  }
  if (!identical(seqlevel_style_status, "passed")) {
    if (identical(status, "passed")) status <- seqlevel_style_status
    add_violation(
      "fragment_seqlevel_style", "UCSC chr* style",
      if (length(seqlevels)) seqlevels else "<missing>",
      "fragment seqlevel style could not be shown compatible with the resolved UCSC resources"
    )
  }

  list(
    configured_build = configured_build,
    registry_resources = registry_resources,
    input_build_evidence = evidence,
    canonical_input_build = if (length(canonical_values) == 1L) canonical_values[[1]] else NA_character_,
    fragment_seqlevels = seqlevels,
    seqlevel_style_status = seqlevel_style_status,
    comparison_status = status,
    identity_violations = length(violations),
    violations = violations
  )
}
