#!/usr/bin/env Rscript
# =====================================================================================
# gating_opencyto.R  --  item 6 (v2.2.0): openCyto / flowWorkspace hierarchical gating
#                        backend for 01_load_and_qc.R (OPT-IN via --gate-engine opencyto).
#
# WHY: the builtin engine is a flat, data-driven per-gate template (one editable cutoff per
# gate, composed by intersection). Some labs prefer a REPRODUCIBLE, SHAREABLE gating
# HIERARCHY expressed as an openCyto gatingTemplate CSV (a real GatingSet with parent/child
# populations, flowClust/mindensity/singletGate methods, etc.). This module runs that stack
# and hands the terminal population back to the pipeline unchanged.
#
# DESIGN CONTRACT (must match the builtin `fs <- pass$fs` handoff exactly):
#   input  fs : per-sample flowSet, already COMPENSATED (flow) as the pipeline does before QC,
#               RAW (untransformed) values.
#   output fs : SAME sampleNames, subset to the terminal (leaf) population, RAW exprs,
#               prepData owns the real transform, so we never hand
#               transformed data back.
#
# HOW: the GatingSet is used ONLY to COMPUTE a per-sample, root-relative (absolute) logical
# index for the terminal population (gh_pop_get_indices returns length == total events). That
# index is applied POSITIONALLY to the ORIGINAL raw flowFrame,
# so the transform used for gating never leaks into the returned data.
#
# This file is sourced LAZILY by 01_load_and_qc.R only when --gate-engine opencyto is set, so
# v2.1.0 (builtin) users never need the openCyto/flowWorkspace Bioconductor stack installed.
# =====================================================================================

# --- locate a shipped default gatingTemplate for the modality --------------------------------
# assets/ is a sibling of scripts/ (script_dir): <pkg>/scripts/gating_opencyto.R + <pkg>/assets/.
opencyto_default_template <- function(modality, script_dir) {
  fn <- if (identical(modality, "cytof")) "gating_template_cytof.csv" else "gating_template_flow.csv"
  file.path(dirname(script_dir), "assets", fn)
}

# Resolve the template file that a run would use (explicit --gate-template wins; else the shipped
# default). Pure: no openCyto/flowWorkspace needed, so the proposal checkpoint can fingerprint and
# persist the template before the backend runs.
opencyto_resolve_template_path <- function(template_path, modality, script_dir) {
  path <- if (!is.null(template_path) && !is.na(template_path) && nzchar(template_path))
    template_path else opencyto_default_template(modality, script_dir)
  if (!file.exists(path)) stop(sprintf("openCyto gatingTemplate not found: %s", path))
  normalizePath(path)
}

# --- main entry point ------------------------------------------------------------------------
# Returns list(fs = filtered flowSet, hierarchy = <list for SCE metadata>, terminal = <chr>,
#              template = <path>, hierarchy_file = <path or NA>).
run_opencyto_gating <- function(fs, modality, template_path = NA,
                                transform_definition, terminal_population = NA,
                                panel, script_dir = "scripts",
                                outdir = ".", logmsg = message) {

  # hard dependency check (opt-in feature -> fail clearly, never silently downgrade)
  need <- c("flowWorkspace", "openCyto")
  miss <- need[!vapply(need, requireNamespace, logical(1), quietly = TRUE)]
  if (length(miss))
    stop(sprintf(paste0("--gate-engine opencyto requires the Bioconductor package(s): %s. ",
                        "Install them (BiocManager::install) or use --gate-engine builtin."),
                 paste(miss, collapse = ", ")))
  suppressPackageStartupMessages({ library(flowWorkspace); library(openCyto) })

  # 1. resolve the gatingTemplate (explicit --gate-template wins; else shipped default) --------
  tmpl_path <- opencyto_resolve_template_path(template_path, modality, script_dir)
  logmsg("openCyto backend: modality=%s | template=%s", modality, basename(tmpl_path))

  tmpl_raw <- utils::read.csv(tmpl_path, stringsAsFactors = FALSE, check.names = FALSE)
  if (!"dims" %in% names(tmpl_raw)) stop("openCyto template is missing the required 'dims' column.")
  resolved_dims <- vapply(seq_len(nrow(tmpl_raw)), function(i) {
    raw_dims <- trimws(strsplit(as.character(tmpl_raw$dims[i]), ",", fixed = TRUE)[[1]])
    raw_dims <- raw_dims[nzchar(raw_dims)]
    if (!length(raw_dims)) stop(sprintf("openCyto template row %d has no gating dimension.", i))
    mapped <- vapply(raw_dims, function(dim_name)
      resolve_channel(panel, dim_name, flowCore::colnames(fs),
                      context = sprintf("openCyto template dimension in row %d", i))$channel,
      character(1))
    paste(mapped, collapse = ",")
  }, character(1))
  tmpl_raw$dims <- resolved_dims
  tmpl_raw$review_input_sha256 <- NULL
  resolved_template <- file.path(outdir, "opencyto_template_resolved.csv")
  utils::write.csv(tmpl_raw, resolved_template, row.names = FALSE, na = "")
  logmsg("openCyto dimensions resolved through $PnN/$PnS metadata: %s",
         paste(unique(resolved_dims), collapse = "; "))

  sns <- flowCore::sampleNames(fs)

  # 3. transform a COPY with the exact run-wide transform definition (never returned)
  fs_t <- apply_transform_definition(fs, transform_definition)

  # 4. build the GatingSet and run the template
  gs <- flowWorkspace::GatingSet(fs_t)
  gt <- openCyto::gatingTemplate(resolved_template)
  logmsg("openCyto: running gatingTemplate over %d sample(s) ...", length(sns))
  openCyto::gt_gating(gt, gs)

  # 5. resolve populations, parents and the terminal (leaf) population ------------------------
  pf      <- flowWorkspace::gs_get_pop_paths(gs, path = "full")   # "root","/A","/A/B",...
  nonroot <- setdiff(pf, "root")
  if (!length(nonroot)) stop("openCyto produced no populations (empty gatingTemplate?).")
  is_leaf <- !vapply(nonroot, function(nd) any(startsWith(nonroot, paste0(nd, "/"))), logical(1))
  leaves  <- nonroot[is_leaf]
  depth   <- function(p) nchar(gsub("[^/]", "", p))
  if (length(leaves) == 1L) {
    terminal <- leaves
    if (!is.na(terminal_population) && nzchar(terminal_population) &&
        !terminal_population %in% c(terminal, basename(terminal)))
      stop(sprintf("--opencyto-terminal '%s' does not match the only leaf '%s'.", terminal_population, terminal))
  } else {
    if (is.na(terminal_population) || !nzchar(terminal_population))
      stop(sprintf("openCyto hierarchy has %d terminal populations (%s). Supply --opencyto-terminal explicitly.",
                   length(leaves), paste(leaves, collapse = ", ")))
    exact <- leaves[leaves == terminal_population]
    if (!length(exact)) exact <- leaves[basename(leaves) == terminal_population]
    if (length(exact) != 1L)
      stop(sprintf("--opencyto-terminal '%s' does not identify exactly one leaf. Available leaves: %s",
                   terminal_population, paste(leaves, collapse = ", ")))
    terminal <- exact
  }
  parent_of <- setNames(
    vapply(nonroot, function(nd) tryCatch(flowWorkspace::gs_pop_get_parent(gs, nd),
                                          error = function(e) NA_character_), character(1)),
    nonroot)
  logmsg("openCyto populations: %s", paste(c("root", basename(nonroot)), collapse = " -> "))
  logmsg("openCyto terminal (retained) population: %s", basename(terminal))

  # 6. per-sample terminal index -> subset ORIGINAL raw fs; collect the
  #    full per-node absolute counts for the hierarchy record. ----------------------------------
  frames    <- vector("list", length(sns)); names(frames) <- sns
  hier_rows <- list(); ret_rows <- list()
  for (s in sns) {
    tot <- nrow(flowCore::exprs(fs[[s]]))
    cnts <- setNames(c(tot, vapply(nonroot, function(nd)
              sum(flowWorkspace::gh_pop_get_indices(gs[[s]], nd)), integer(1))), c("root", nonroot))
    for (nd in c("root", nonroot)) {
      par <- if (identical(nd, "root")) NA_character_ else parent_of[[nd]]
      pcp <- if (is.na(par)) NA_real_ else 100 * cnts[[nd]] / cnts[[par]]
      hier_rows[[length(hier_rows) + 1L]] <- data.frame(
        sample_id = s, population = nd, parent = par,
        count = as.integer(cnts[[nd]]), pct_of_parent = pcp, stringsAsFactors = FALSE)
    }
    idx <- flowWorkspace::gh_pop_get_indices(gs[[s]], terminal)
    if (length(idx) != tot)
      stop(sprintf("openCyto index length (%d) != events (%d) for %s.", length(idx), tot, s))
    frames[[s]] <- fs[[s]][idx, ]
    ret_rows[[length(ret_rows) + 1L]] <- data.frame(
      sample_id = s, input = tot, retained = sum(idx),
      pct_removed = 100 * (1 - sum(idx) / tot), stringsAsFactors = FALSE)
  }
  fs_out <- as(frames, "flowSet"); flowCore::sampleNames(fs_out) <- sns
  hier_df <- do.call(rbind, hier_rows); ret_df <- do.call(rbind, ret_rows)

  tot_in  <- sum(ret_df$input); tot_out <- sum(ret_df$retained)
  logmsg("openCyto gating retained %d / %d events (%.1f%% removed) across %d sample(s).",
         tot_out, tot_in, 100 * (1 - tot_out / tot_in), length(sns))

  # 7. human-readable hierarchy tree (mean across samples) + per-sample retained ---------------
  hfile <- file.path(outdir, "gating_hierarchy.txt")
  hier_written <- tryCatch({
    con <- file(hfile, "w"); on.exit(close(con), add = TRUE)
    writeLines(c(
      sprintf("openCyto gating hierarchy  (engine=opencyto, modality=%s)", modality),
      sprintf("template source   : %s", tmpl_path),
      sprintf("template resolved : %s", resolved_template),
      sprintf("samples  : %d", length(sns)),
      sprintf("terminal : %s  (this population is handed to clustering)", terminal),
      "",
      "Population tree  --  mean count across samples [mean % of parent]:"), con)
    ord <- c("root", nonroot[order(vapply(nonroot, depth, integer(1)))])
    for (nd in ord) {
      sub <- hier_df[hier_df$population == nd, ]
      mc  <- mean(sub$count); mp <- mean(sub$pct_of_parent)
      ind <- strrep("  ", depth(nd))
      lab <- if (identical(nd, "root")) "root" else nd
      tag <- if (identical(nd, terminal)) "   <- terminal (retained)" else ""
      line <- if (identical(nd, "root")) sprintf("%s%-42s %8.0f%s", ind, lab, mc, tag)
              else sprintf("%s%-42s %8.0f [%5.1f%%]%s", ind, lab, mc, mp, tag)
      writeLines(line, con)
    }
    writeLines(c("", "Per-sample retained (terminal population):"), con)
    for (i in seq_len(nrow(ret_df)))
      writeLines(sprintf("  %-20s input=%-8d retained=%-8d removed=%5.1f%%",
                         ret_df$sample_id[i], ret_df$input[i], ret_df$retained[i],
                         ret_df$pct_removed[i]), con)
    TRUE
  }, error = function(e) { logmsg("NOTE: could not write gating_hierarchy.txt (%s).", conditionMessage(e)); FALSE })
  if (isTRUE(hier_written)) logmsg("Wrote openCyto hierarchy: %s", hfile)

  # 8. structured record for SCE metadata (read-only, surfaced by build_manifest.R + 07) -------
  hierarchy <- list(
    engine       = "opencyto",
    template     = tmpl_path,
    resolved_template = resolved_template,
    populations  = basename(nonroot),
    paths        = nonroot,
    terminal     = terminal,
    n_samples    = length(sns),
    total_input  = as.integer(tot_in),
    total_retained = as.integer(tot_out),
    pct_removed  = 100 * (1 - tot_out / tot_in),
    counts       = hier_df,
    retained     = ret_df,
    hierarchy_file = if (isTRUE(hier_written)) hfile else NA_character_)

  list(fs = fs_out, hierarchy = hierarchy, terminal = terminal, template = resolved_template,
       hierarchy_file = hierarchy$hierarchy_file)
}
