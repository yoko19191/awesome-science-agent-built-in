#!/usr/bin/env Rscript
# Reconcile pipeline counts and percentages against an explicit manual-gating reference.

parse_cli <- function(args) {
  out <- list(manual = NA_character_, sce = NA_character_, abundance = NA_character_, outdir = ".",
              sample_col = "auto", population_col = "auto", count_col = "auto", pct_col = "auto",
              parent_population = "auto", map = "auto", tol_pp = 5, tol_rel = 0.20)
  names_map <- c("manual" = "manual", "sce" = "sce", "abundance" = "abundance", "outdir" = "outdir",
                 "sample-col" = "sample_col", "population-col" = "population_col",
                 "count-col" = "count_col", "pct-col" = "pct_col",
                 "parent-population" = "parent_population", "map" = "map",
                 "tol-pp" = "tol_pp", "tol-rel" = "tol_rel")
  i <- 1L
  while (i <= length(args)) {
    key <- sub("^--", "", args[i])
    if (!key %in% names(names_map) || i == length(args)) stop(sprintf("Unknown option or missing value: %s", args[i]))
    dest <- names_map[[key]]
    value <- args[i + 1L]
    out[[dest]] <- if (dest %in% c("tol_pp", "tol_rel")) as.numeric(value) else value
    i <- i + 2L
  }
  out
}
opt <- parse_cli(commandArgs(trailingOnly = TRUE))

dir.create(opt$outdir, recursive = TRUE, showWarnings = FALSE)
LOG <- file.path(opt$outdir, "validation_vs_manual_log.txt")
cat("", file = LOG)
logmsg <- function(...) {
  msg <- sprintf(...)
  cat(msg, "\n")
  cat(msg, "\n", file = LOG, append = TRUE)
}
norm_id <- function(x) tolower(trimws(sub("\\.fcs$", "", as.character(x), ignore.case = TRUE)))
norm_pop <- function(x) tolower(trimws(as.character(x)))

read_any <- function(path) {
  ext <- tolower(tools::file_ext(path))
  if (ext == "csv") return(read.csv(path, check.names = FALSE, stringsAsFactors = FALSE))
  if (ext %in% c("tsv", "txt")) return(read.delim(path, check.names = FALSE, stringsAsFactors = FALSE))
  if (ext %in% c("xlsx", "xls")) {
    if (!requireNamespace("readxl", quietly = TRUE)) stop("Reading Excel requires the readxl package.")
    return(as.data.frame(readxl::read_excel(path), check.names = FALSE, stringsAsFactors = FALSE))
  }
  stop("Unsupported manual export format: .", ext)
}

choose_col <- function(df, override, pattern, label, required = TRUE) {
  if (!identical(override, "auto")) {
    if (!override %in% names(df)) stop(sprintf("--%s-col '%s' is absent.", label, override))
    return(override)
  }
  hits <- grep(pattern, names(df), ignore.case = TRUE, value = TRUE)
  if (length(hits) == 1L) return(hits)
  if (!required && !length(hits)) return(NA_character_)
  if (!length(hits)) stop(sprintf("Could not identify %s column; supply --%s-col.", label, label))
  stop(sprintf("Ambiguous %s columns (%s); supply --%s-col.", label, paste(hits, collapse = ", "), label))
}

classify_header <- function(header) {
  is_pct <- grepl("freq|%|percent|proportion", header, ignore.case = TRUE)
  is_count <- grepl("count|#|events|(^|[^a-z])n([^a-z]|$)", header, ignore.case = TRUE)
  base <- trimws(sub("\\|.*$", "", header))
  population <- trimws(sub(".*/", "", base))
  population <- trimws(gsub("(freq\\.? of parent.*|freq\\.? of.*|count|percent|%|\\(%\\)|events|\\|)",
                              "", population, ignore.case = TRUE))
  list(population = population, stat = if (is_pct) "pct" else if (is_count) "count" else NA_character_)
}

parse_manual <- function(df) {
  sample_col <- choose_col(df, opt$sample_col, "name|sample|file|specimen|well|tube", "sample")
  population_col <- if (!identical(opt$population_col, "auto"))
    choose_col(df, opt$population_col, "", "population") else {
      hits <- grep("population|gate|cell.?type|subset", names(df), ignore.case = TRUE, value = TRUE)
      if (length(hits) == 1L) hits else NA_character_
    }
  if (!is.na(population_col)) {
    count_col <- choose_col(df, opt$count_col, "count|events|^n$|n_cell|cells", "count", required = FALSE)
    pct_col <- choose_col(df[, setdiff(names(df), "pct_denominator"), drop = FALSE], opt$pct_col, "pct|percent|freq|prop", "pct", required = FALSE)
    if (is.na(count_col) && is.na(pct_col)) stop("Tidy manual table has neither a count nor percentage column.")
    parts <- list()
    if (!is.na(count_col)) parts[[length(parts) + 1L]] <- data.frame(
      sample = norm_id(df[[sample_col]]), population = as.character(df[[population_col]]), stat = "count",
      value = suppressWarnings(as.numeric(gsub(",", "", as.character(df[[count_col]])))), source = count_col, denominator = NA_character_)
    if (!is.na(pct_col)) parts[[length(parts) + 1L]] <- data.frame(
      sample = norm_id(df[[sample_col]]), population = as.character(df[[population_col]]), stat = "pct",
      value = suppressWarnings(as.numeric(gsub(",", "", as.character(df[[pct_col]])))), source = pct_col,
      denominator = if ("pct_denominator" %in% names(df)) norm_pop(df$pct_denominator) else NA_character_)
    return(do.call(rbind, parts))
  }
  if (!identical(opt$count_col, "auto") || !identical(opt$pct_col, "auto"))
    stop("--count-col/--pct-col overrides require a tidy table with --population-col.")
  parts <- lapply(setdiff(names(df), sample_col), function(header) {
    parsed <- classify_header(header)
    if (is.na(parsed$stat)) return(NULL)
    data.frame(sample = norm_id(df[[sample_col]]), population = parsed$population, stat = parsed$stat,
               value = suppressWarnings(as.numeric(gsub(",", "", as.character(df[[header]])))), source = header, denominator = NA_character_)
  })
  parts <- Filter(Negate(is.null), parts)
  if (!length(parts)) stop("No count/percentage columns were recognized in the wide manual export.")
  do.call(rbind, parts)
}

read_population_map <- function(spec) {
  if (identical(spec, "auto") || !nzchar(spec)) return(data.frame(pipeline = character(), manual = character()))
  if (file.exists(spec)) {
    map <- read.csv(spec, stringsAsFactors = FALSE)
    if (!all(c("pipeline", "manual") %in% names(map))) stop("Population map CSV needs pipeline and manual columns.")
  } else {
    pairs <- trimws(strsplit(spec, ";", fixed = TRUE)[[1]])
    split_pairs <- strsplit(pairs[nzchar(pairs)], "=", fixed = TRUE)
    if (!length(split_pairs) || any(lengths(split_pairs) != 2L)) stop("--map must be pipeline=manual;... or a CSV.")
    map <- data.frame(pipeline = vapply(split_pairs, `[`, character(1), 1),
                      manual = vapply(split_pairs, `[`, character(1), 2), stringsAsFactors = FALSE)
  }
  map$pipeline <- norm_pop(map$pipeline)
  map$manual <- norm_pop(map$manual)
  if (anyDuplicated(map$pipeline)) stop("Population map contains duplicate pipeline names.")
  map
}

if (is.na(opt$manual) || !file.exists(opt$manual)) stop("--manual export not found: ", opt$manual)
manual <- read_any(opt$manual)
long <- parse_manual(manual)
long$population_key <- norm_pop(long$population)
manual_records <- long
long <- long[is.finite(long$value), , drop = FALSE]
pop_map <- read_population_map(opt$map)
logmsg("Manual export: %d rows; %d normalized records; %d explicit population mapping(s).",
       nrow(manual), nrow(long), nrow(pop_map))

rows <- list()
parent_key <- NA_character_
valid_pct_samples <- character()
incomplete <- FALSE
disagreement <- FALSE
add_row <- function(sample, population, stat, pipeline, manual, coverage, reason = "") {
  delta <- if (coverage == "MATCHED" && is.finite(pipeline) && is.finite(manual)) pipeline - manual else NA_real_
  rel <- if (stat == "count" && is.finite(manual) && manual > 0 && is.finite(pipeline)) delta / manual else NA_real_
  # Relative error is undefined at zero; any nonzero pipeline count disagrees.
  flagged <- if (coverage != "MATCHED") FALSE else if (stat == "count") {
    if (is.finite(manual) && manual == 0 && is.finite(pipeline)) pipeline != 0
    else is.finite(rel) && abs(rel) > opt$tol_rel
  } else is.finite(delta) && abs(delta) > opt$tol_pp
  rows[[length(rows) + 1L]] <<- data.frame(sample = sample, population = population, stat = stat,
    pipeline = pipeline, manual = manual, delta = delta, rel_diff = rel, coverage = coverage,
    flagged = flagged, reason = reason, stringsAsFactors = FALSE)
  disagreement <<- disagreement || flagged
  incomplete <<- incomplete || coverage != "MATCHED"
}

if (!is.na(opt$sce) && file.exists(opt$sce)) {
  sce <- readRDS(opt$sce)
  if (!requireNamespace("SummarizedExperiment", quietly = TRUE))
    stop("Reading a SingleCellExperiment requires SummarizedExperiment.")
  if (!methods::is(sce, "SummarizedExperiment"))
    stop("--sce must contain a SingleCellExperiment or SummarizedExperiment with sample_id metadata.")
  sample_ids <- as.character(SummarizedExperiment::colData(sce)$sample_id)
  if (length(sample_ids) != ncol(sce) || anyNA(sample_ids) || any(!nzchar(trimws(sample_ids))))
    stop("Every SCE event requires a nonempty sample_id.")
  pipeline_cells <- as.data.frame(table(sample = norm_id(sample_ids)), stringsAsFactors = FALSE)
  names(pipeline_cells) <- c("sample", "pipeline")
  candidates <- unique(long$population_key[grepl("(^cells$|singlet|single cells|^live$|intact)", long$population_key)])
  parent_key <- if (!identical(opt$parent_population, "auto")) norm_pop(opt$parent_population) else {
    if (length(candidates) == 1L) candidates else NA_character_
  }
  if (is.na(parent_key)) {
    incomplete <- TRUE
    logmsg("INCOMPLETE: manual parent population is ambiguous/absent (%s); supply --parent-population.", paste(candidates, collapse = ", "))
  } else {
    manual_parent <- manual_records[manual_records$population_key == parent_key & manual_records$stat == "count", c("sample", "value")]
    names(manual_parent)[2] <- "manual"
    dup_parent <- unique(manual_parent$sample[duplicated(manual_parent$sample) | duplicated(manual_parent$sample, fromLast = TRUE)])
    if (length(dup_parent)) {
      incomplete <- TRUE
      logmsg("INCOMPLETE: duplicate manual parent counts for sample(s): %s", paste(dup_parent, collapse = ", "))
      manual_parent <- manual_parent[!manual_parent$sample %in% dup_parent, , drop = FALSE]
    }
    manual_parent <- manual_parent[is.finite(manual_parent$manual), , drop = FALSE]
    joined <- merge(pipeline_cells, manual_parent, by = "sample", all = TRUE)
    valid_pct_samples <- joined$sample[is.finite(joined$pipeline) & joined$pipeline > 0 &
                                       is.finite(joined$manual) & joined$manual > 0]
    for (i in seq_len(nrow(joined))) {
      coverage <- if (is.na(joined$pipeline[i])) "MISSING_PIPELINE_SAMPLE" else if (is.na(joined$manual[i])) "MISSING_MANUAL_SAMPLE" else "MATCHED"
      add_row(joined$sample[i], "(parent cells)", "count", joined$pipeline[i], joined$manual[i], coverage,
              if (coverage == "MATCHED") "" else "sample coverage incomplete")
    }
  }
} else {
  incomplete <- TRUE
  logmsg("INCOMPLETE: --sce was not supplied; total-cell reconciliation is required.")
}

if (!is.na(opt$abundance) && file.exists(opt$abundance)) {
  abundance <- read.csv(opt$abundance, check.names = FALSE, stringsAsFactors = FALSE)
  population_col <- choose_col(abundance, "auto", "pop|cluster|cell.?type|label", "population")
  sample_hits <- grep("sample", names(abundance), ignore.case = TRUE, value = TRUE)
  pipeline_long <- list()
  if (length(sample_hits) == 1L) {
    count_col <- choose_col(abundance, "auto", "count|^n$|n_cell|cells|total_n", "count", required = FALSE)
    pct_col <- choose_col(abundance, "auto", "pct|percent|freq|prop|overall_pct", "pct", required = FALSE)
    if (!is.na(count_col)) pipeline_long[[length(pipeline_long) + 1L]] <- data.frame(
      sample = norm_id(abundance[[sample_hits]]), population = norm_pop(abundance[[population_col]]),
      stat = "count", value = suppressWarnings(as.numeric(abundance[[count_col]])))
    if (!is.na(pct_col)) pipeline_long[[length(pipeline_long) + 1L]] <- data.frame(
      sample = norm_id(abundance[[sample_hits]]), population = norm_pop(abundance[[population_col]]),
      stat = "pct", value = suppressWarnings(as.numeric(abundance[[pct_col]])))
  } else {
    count_cols <- grep("_n$", names(abundance), ignore.case = TRUE, value = TRUE)
    pct_cols <- grep("_pct$", names(abundance), ignore.case = TRUE, value = TRUE)
    count_cols <- setdiff(count_cols, "Total_n")
    pct_cols <- setdiff(pct_cols, "Overall_pct")
    for (column in count_cols) pipeline_long[[length(pipeline_long) + 1L]] <- data.frame(
      sample = norm_id(sub("_n$", "", column, ignore.case = TRUE)),
      population = norm_pop(abundance[[population_col]]), stat = "count",
      value = suppressWarnings(as.numeric(abundance[[column]])))
    for (column in pct_cols) pipeline_long[[length(pipeline_long) + 1L]] <- data.frame(
      sample = norm_id(sub("_pct$", "", column, ignore.case = TRUE)),
      population = norm_pop(abundance[[population_col]]), stat = "pct",
      value = suppressWarnings(as.numeric(abundance[[column]])))
  }
  if (!length(pipeline_long)) stop("Abundance table has no recognizable tidy or <sample>_n/<sample>_pct values.")
  pipeline_long <- do.call(rbind, pipeline_long)
  pipeline_long$manual_population <- pipeline_long$population
  if (nrow(pop_map)) {
    idx <- match(pipeline_long$population, pop_map$pipeline)
    pipeline_long$manual_population[!is.na(idx)] <- pop_map$manual[idx[!is.na(idx)]]
  }
  for (i in seq_len(nrow(pipeline_long))) {
    match_rows <- long$sample == pipeline_long$sample[i] &
                  long$population_key == pipeline_long$manual_population[i] & long$stat == pipeline_long$stat[i]
    manual_value <- long$value[match_rows]
    coverage <- if (!is.finite(pipeline_long$value[i])) "MISSING_PIPELINE_VALUE" else if (!length(manual_value)) "MISSING_MANUAL_POPULATION" else if (length(manual_value) > 1L) "AMBIGUOUS_MANUAL_POPULATION" else "MATCHED"
    reason <- if (coverage == "MATCHED") "" else sprintf("mapped manual population: %s", pipeline_long$manual_population[i])
    if (coverage == "MATCHED" && pipeline_long$stat[i] == "pct") {
      denominator <- long$denominator[match_rows]
      # Step 04 percentages use all retained SCE cells. The selected manual
      # parent is their reference counterpart, not an arbitrary immediate parent.
      if (is.na(parent_key) || is.na(denominator) || !nzchar(denominator) || denominator != parent_key ||
          !pipeline_long$sample[i] %in% valid_pct_samples) {
        coverage <- "NOT_COMPUTABLE_DENOMINATOR"
        reason <- sprintf(paste0("Percentage denominator is unknown or incompatible: manual=%s; ",
                                 "required manual counterpart of retained SCE cells=%s. ",
                                 "A unique positive manual parent count and retained SCE sample are required. ",
                                 "No percentage difference computed; supply a verified pct_denominator per tidy row."),
                          if (is.na(denominator) || !nzchar(denominator)) "unknown" else denominator,
                          if (is.na(parent_key)) "unknown" else parent_key)
      }
    }
    add_row(pipeline_long$sample[i], pipeline_long$population[i], pipeline_long$stat[i], pipeline_long$value[i],
            if (length(manual_value) == 1L) manual_value else NA_real_, coverage, reason)
  }
  # Parent counts are reconciled against the SCE above. Every other manual
  # sample/population/stat must also have a pipeline counterpart.
  manual_keys <- unique(long[, c("sample", "population_key", "stat")])
  if (!is.na(parent_key)) manual_keys <- manual_keys[manual_keys$population_key != parent_key, , drop = FALSE]
  for (i in seq_len(nrow(manual_keys))) {
    row <- manual_keys[i, ]
    matching <- pipeline_long$sample == row$sample &
      pipeline_long$manual_population == row$population_key & pipeline_long$stat == row$stat
    if (!any(matching)) {
      values <- long$value[long$sample == row$sample & long$population_key == row$population_key & long$stat == row$stat]
      add_row(row$sample, row$population_key, row$stat, NA_real_,
              if (length(values) == 1L) values else NA_real_, "MISSING_PIPELINE_POPULATION",
              "Manual population/stat has no pipeline counterpart.")
    }
  }
} else {
  logmsg("Population reconciliation not requested because --abundance was absent.")
}

result <- if (length(rows)) do.call(rbind, rows) else data.frame(
  sample = character(), population = character(), stat = character(), pipeline = numeric(), manual = numeric(),
  delta = numeric(), rel_diff = numeric(), coverage = character(), flagged = logical(), reason = character())
verdict <- if (incomplete || !nrow(result)) "INCOMPLETE" else if (disagreement) "REVIEW" else "PASS"
write.csv(result, file.path(opt$outdir, "validation_vs_manual.csv"), row.names = FALSE)
writeLines(verdict, file.path(opt$outdir, "validation_verdict.txt"))
state <- data.frame(state = verdict, comparisons = nrow(result), matched = sum(result$coverage == "MATCHED"),
                    flagged = sum(result$flagged), stringsAsFactors = FALSE)
write.csv(state, file.path(opt$outdir, "manual_reconciliation_state.csv"), row.names = FALSE)
logmsg("=== VERDICT: %s | matched=%d/%d | flagged=%d ===", verdict, state$matched, nrow(result), state$flagged)
if (verdict == "REVIEW") logmsg("Downstream claims remain provisional until disagreements are resolved.")
if (verdict == "INCOMPLETE") logmsg("PASS is impossible until all required samples/populations have matched manual data.")
quit(status = switch(verdict, PASS = 0L, REVIEW = 2L, INCOMPLETE = 3L))
