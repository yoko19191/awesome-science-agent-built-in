#!/usr/bin/env Rscript
# ============================================================================
# STAGE 06 — Provenance manifest + optional slimmed RDS + copy to results.
# Route-aware: loads 05_final.rds (atac_only/multiome_annotate) or
# 10_diffabund.rds (joint). Writes provenance_manifest.json (incl. the genome
# identity-violations witness) and, in the joint route, folds in modality
# weights / integration metrics / MultiVI status.
# ============================================================================
suppressPackageStartupMessages({ library(Signac); library(Seurat); library(jsonlite) })
source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(FALSE), value = TRUE))), "_common.R"))
source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(FALSE), value = TRUE))), "integration_metrics_figure.R"))
cfg <- load_config(); set.seed(cfg$project$seed)

route_decision <- tryCatch(fromJSON(ckpt(cfg, "route_decision.json"), simplifyVector = FALSE),
                           error = function(e) NULL)
if (is.null(route_decision)) stop("route_decision.json is required for provenance")
route <- route_decision$route
joint <- identical(route, "joint")
st <- if (joint) readRDS(ckpt(cfg, "10_diffabund.rds")) else readRDS(ckpt(cfg, "05_final.rds"))
obj <- st$obj
gen <- GENOME_REGISTRY[[cfg$genome$build]]
genome_identity <- route_decision$signals$genome_identity
if (is.null(genome_identity) || is.null(genome_identity$identity_violations)) {
  stop("route decision lacks an executed genome-identity comparison")
}
identity_violations <- as.integer(genome_identity$identity_violations)
if (!identical(identity_violations, 0L)) {
  stop("genome-identity comparison has violations; refusing provenance/report finalization")
}

pkg_ver <- function(p) tryCatch(as.character(packageVersion(p)), error = function(e) NA)
manifest <- list(
  project = cfg$project$name,
  generated = format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z"),
  route = route,
  genome = list(build = cfg$genome$build,
                ensdb = gen$ensdb, bsgenome = gen$bsgenome, blacklist = gen$blacklist,
                comparison_status = genome_identity$comparison_status,
                canonical_input_build = genome_identity$canonical_input_build,
                input_build_evidence = genome_identity$input_build_evidence,
                observed_fragment_seqlevels = genome_identity$fragment_seqlevels,
                seqlevel_style_status = genome_identity$seqlevel_style_status,
                identity_violations = identity_violations,
                violations = genome_identity$violations),
  seed = cfg$project$seed,
  input = list(fragments = cfg$input$fragments, peak_matrix_h5 = cfg$input$peak_matrix_h5,
               multiome = cfg$input$multiome, multiome_joint = isTRUE(cfg$multiome$joint),
               metadata_csv = cfg$input$metadata_csv),
  cells_after_QC = ncol(obj),
  package_versions = list(
    R = paste(R.version$major, R.version$minor, sep = "."),
    Signac = pkg_ver("Signac"), Seurat = pkg_ver("Seurat"),
    GenomicRanges = pkg_ver("GenomicRanges"), miloR = pkg_ver("miloR")),
  R_platform = sessionInfo()$platform
)

if (joint) {
  sc <- tryCatch(fromJSON(tabp(cfg, "joint_qc_scorecard.json")), error = function(e) NULL)
  im <- if (!is.null(sc)) sc$integration_metrics else NULL
  if (!is.null(im) && integration_metrics_have_numeric_result(im)) {
    p <- build_integration_metrics_plot(im, clpal(length(INTEGRATION_METRIC_KEYS)), plot_theme())
    save_fig(cfg, p, "19_integration_metrics", 6.5, 4.5)
  } else if (!is.null(im)) {
    log_msg("Skipped 19_integration_metrics:", INTEGRATION_METRICS_ABSENCE_REASON)
  }
  manifest$joint <- list(
    n_joint_cells = ncol(obj),
    modality_weights = tryCatch(st$weights, error = function(e) NA),
    integration_metrics = if (!is.null(sc)) sc$integration_metrics else NA,
    multivi = if (!is.null(sc)) sc$multivi else NA,
    diffabund = tryCatch(st$diffabund_status, error = function(e) NA),
    parameters = list(pairing = cfg$pairing, wnn = cfg$wnn,
                      integration_metrics = cfg$integration_metrics,
                      multivi = cfg$multivi, diffabund = cfg$diffabund, rna_qc = cfg$rna_qc))
} else {
  manifest$n_recalled_peaks <- tryCatch(st$n_recalled, error = function(e) NA)
  manifest$n_final_clusters <- tryCatch(st$n_final, error = function(e) NA)
  manifest$n_cell_types <- tryCatch(nlevels(obj$cell_type), error = function(e) NA)
  manifest$parameters <- list(qc = cfg$qc, dimreduc = cfg$dimreduc, peaks = cfg$peaks,
                              annotation = cfg$annotation, diffaccess = cfg$diffaccess)
}
write_json(manifest, tabp(cfg, "provenance_manifest.json"), auto_unbox = TRUE, pretty = TRUE)
# also place the witness at the results root for report_qc/source-assertion checks
dir.create(cfg$project$results_dir, recursive = TRUE, showWarnings = FALSE)
write_json(manifest, file.path(cfg$project$results_dir, "provenance_manifest.json"),
           auto_unbox = TRUE, pretty = TRUE)
log_msg("Wrote provenance_manifest.json (route =", route, ")")

if (isTRUE(cfg$output$save_rds)) {
  out <- obj
  if (isTRUE(cfg$output$slim_rds)) {
    tryCatch({ Fragments(out[["recalled"]]) <- NULL }, error = function(e) NULL)
    tryCatch({ Fragments(out[["peaks"]]) <- NULL }, error = function(e) NULL)
    if ("peaks" %in% Assays(out)) out[["peaks"]] <- NULL
  }
  local_rds <- file.path(cfg$project$outdir, "final_object.rds"); saveRDS(out, local_rds)
  system2("cp", c(local_rds, file.path(cfg$project$results_dir, "final_object.rds")))
  log_msg("Saved RDS ->", cfg$project$results_dir)
}

copy_to_results(cfg)
log_msg("STAGE 06 done"); cat("STAGE06_OK\n")
