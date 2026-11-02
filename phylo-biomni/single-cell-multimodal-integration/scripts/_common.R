# ============================================================================
# _common.R — shared helpers for single-cell-multimodal-integration.
# Config loading, replicate-sufficiency gate, figure saving, provenance.
# ============================================================================
suppressWarnings({
  .libpath <- Sys.getenv("R_LIBS_USER", unset = "")
  if (nzchar(.libpath)) .libPaths(c(.libpath, .libPaths()))
})

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0 || (length(a) == 1 && is.na(a))) b else a
log_msg <- function(...) cat(sprintf("[%s] ", format(Sys.time(), "%H:%M:%S")), ..., "\n")

load_config <- function(path = NULL) {
  path <- path %||% Sys.getenv("CONFIG", unset = "")
  if (!nzchar(path)) {
    args <- commandArgs(trailingOnly = TRUE)
    if (length(args) >= 1 && file.exists(args[1])) path <- args[1]
  }
  if (!nzchar(path) || !file.exists(path))
    stop("config not found; pass a path or set CONFIG=/path/to/config.yaml")
  if (!requireNamespace("yaml", quietly = TRUE))
    stop("R package 'yaml' is required to read the config")
  yaml::read_yaml(path)
}

ensure_dirs <- function(cfg) {
  for (d in c(cfg$project$outdir,
              file.path(cfg$project$outdir, c("ingest", "figures", "tables", "checkpoints")),
              cfg$project$results_dir))
    if (!is.null(d) && !dir.exists(d)) dir.create(d, recursive = TRUE, showWarnings = FALSE)
}

figp <- function(cfg, name) file.path(cfg$project$outdir, "figures", name)
tabp <- function(cfg, name) file.path(cfg$project$outdir, "tables", name)

# ---- replicate-sufficiency gate (inference readiness) ----------------------
# >=2 biological replicates per condition AND >1 condition, or DA is skipped.
replicates_sufficient <- function(meta, rep_col, cond_col, min_rep = 2) {
  if (!all(c(rep_col, cond_col) %in% colnames(meta)))
    return(list(sufficient = FALSE,
                reason = sprintf("metadata missing '%s'/'%s' column(s)", rep_col, cond_col),
                per_condition = list()))
  u <- unique(meta[, c(rep_col, cond_col), drop = FALSE])
  pc <- table(u[[cond_col]])
  suff <- (length(pc) >= 2 && all(pc >= min_rep))
  list(sufficient = suff,
       reason = if (suff) "sufficient" else
                sprintf("need >=%d biological replicates in each of >=2 conditions; observed %s",
                        min_rep, paste(sprintf("%s=%d", names(pc), as.integer(pc)), collapse = ", ")),
       per_condition = as.list(stats::setNames(as.integer(pc), names(pc))),
       conditions = names(pc), min_required = min_rep)
}

# ---- Okabe-Ito colorblind-safe palette + theme -----------------------------
okabe <- c("#000000", "#E69F00", "#56B4E9", "#009E73", "#F0E442",
           "#0072B2", "#D55E00", "#CC79A7")
clpal <- function(n) if (n <= length(okabe)) okabe[seq_len(n)] else
  grDevices::colorRampPalette(okabe)(n)
plot_theme <- function() {
  th <- ggplot2::theme_bw(base_size = 11)
  th + ggplot2::theme(text = ggplot2::element_text(family = "Liberation Sans"),
                      panel.grid.minor = ggplot2::element_blank())
}

# PNG + editable-text SVG (svglite if available).
save_fig <- function(cfg, p, name, w = 7, h = 5) {
  png_path <- figp(cfg, name)
  ggplot2::ggsave(png_path, p, width = w, height = h, dpi = 150)
  svg_path <- sub("\\.png$", ".svg", png_path)
  tryCatch({
    if (requireNamespace("svglite", quietly = TRUE))
      ggplot2::ggsave(svg_path, p, width = w, height = h, device = svglite::svglite)
    else ggplot2::ggsave(svg_path, p, width = w, height = h)
  }, error = function(e) log_msg("SVG save skipped for", name, ":", conditionMessage(e)))
  png_path
}

write_json <- function(obj, path) {
  jsonlite::write_json(obj, path, auto_unbox = TRUE, pretty = TRUE, digits = 8, null = "null")
  path
}

# Read gzipped MatrixMarket produced by scripts/_io.py into a Seurat-ready matrix.
read_mtx_gz <- function(mtx_gz, features_gz, barcodes_gz) {
  if (!requireNamespace("Matrix", quietly = TRUE)) stop("Matrix package required")
  m <- Matrix::readMM(gzfile(mtx_gz))              # features x cells
  feats <- readLines(gzfile(features_gz))
  cells <- readLines(gzfile(barcodes_gz))
  rownames(m) <- feats; colnames(m) <- cells
  methods::as(m, "CsparseMatrix")
}

# ---- python bridge, path resolution, provenance, facts fragments -----------
py_bin <- function() Sys.getenv("PYTHON", unset = "python3")

# package root = scripts/.. (works whether sourced or run with --file=)
skill_root <- function() {
  f <- grep("^--file=", commandArgs(FALSE), value = TRUE)
  here <- if (length(f)) dirname(sub("^--file=", "", f[1])) else "."
  normalizePath(file.path(here, ".."), mustWork = FALSE)
}

# absolute -> as-is; else CWD if it exists; else relative to the package root.
resolve_path <- function(p, base = NULL) {
  if (is.null(p) || !nzchar(p)) return(p)
  if (grepl("^(/|[A-Za-z]:)", p)) return(p)
  if (file.exists(p)) return(normalizePath(p, mustWork = FALSE))
  cand <- file.path(base %||% skill_root(), p)
  if (file.exists(cand)) return(normalizePath(cand, mustWork = FALSE))
  p
}

input_dir <- function(cfg) resolve_path(cfg$input$dir %||% cfg$input$data_dir %||% ".")
ckpt <- function(cfg, name) file.path(cfg$project$outdir, "checkpoints", name)

# read-modify-write nested provenance_manifest.json at the results root.
provenance_path <- function(cfg) file.path(cfg$project$results_dir, "provenance_manifest.json")
update_provenance <- function(cfg, patch) {
  p <- provenance_path(cfg)
  cur <- if (file.exists(p))
    tryCatch(jsonlite::read_json(p, simplifyVector = TRUE), error = function(e) list()) else list()
  if (!is.list(cur)) cur <- list()
  for (grp in names(patch)) {
    if (is.null(cur[[grp]]) || !is.list(cur[[grp]])) cur[[grp]] <- list()
    for (k in names(patch[[grp]])) cur[[grp]][[k]] <- patch[[grp]][[k]]
  }
  dir.create(dirname(p), recursive = TRUE, showWarnings = FALSE)
  write_json(cur, p)
  invisible(p)
}

# per-step facts fragment (aggregated into facts_payload.json by finalize.py)
write_facts_fragment <- function(cfg, name, obj) {
  d <- file.path(cfg$project$outdir, "checkpoints")
  dir.create(d, recursive = TRUE, showWarnings = FALSE)
  write_json(obj, file.path(d, name))
}

# status JSON that binds a caveat to a field (dsb/totalvi/diffabund)
write_status <- function(cfg, name, obj) {
  d <- file.path(cfg$project$outdir, "tables")
  dir.create(d, recursive = TRUE, showWarnings = FALSE)
  write_json(obj, file.path(d, name))
}
