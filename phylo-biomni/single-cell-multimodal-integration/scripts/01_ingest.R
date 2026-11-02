#!/usr/bin/env Rscript
# ============================================================================
# 01_ingest.R — assemble a paired RNA+ADT Seurat object from the CITE-seq
# bundle. HDF5 reading is delegated to scripts/_io.py (writes MatrixMarket +
# ingest_meta.json), so R never needs a fragile HDF5 reader. Starts
# provenance_manifest.json with the assay-identity witness (must be 0 violations).
# ============================================================================
here <- dirname(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1]))
suppressMessages({
  source(file.path(here, "_common.R"))
  source(file.path(here, "adt_contract.R"))
  library(Seurat)
  library(SeuratObject)
})

cfg <- load_config(); cfg$input$dir <- input_dir(cfg); ensure_dirs(cfg)
iso <- paste(cfg$adt$isotype_controls %||% character(0), collapse = ",")
background <- cfg$adt$dsb_background %||% list()
DEFAULT_BACKGROUND_MIN_ADT_COUNTS <- 10L
DEFAULT_BACKGROUND_MAX_ADT_COUNTS <- 200L
DEFAULT_BACKGROUND_MAX_RNA_COUNTS <- 200L
DEFAULT_BACKGROUND_MIN_DROPLETS <- 10L

# ---- run the Python ingest (writes <outdir>/ingest/*) ----------------------
log_msg("Ingesting", cfg$input$dir, "via _io.py")
args <- c(file.path(here, "_io.py"), "--ingest", cfg$input$dir, "--out", cfg$project$outdir)
if (nzchar(iso)) args <- c(args, "--isotypes", iso)
args <- c(
  args,
  "--adt-normalization", tolower(cfg$adt$normalization %||% "dsb"),
  "--background-min-adt-counts", as.integer(background$min_adt_counts %||% DEFAULT_BACKGROUND_MIN_ADT_COUNTS),
  "--background-max-adt-counts", as.integer(background$max_adt_counts %||% DEFAULT_BACKGROUND_MAX_ADT_COUNTS),
  "--background-max-rna-counts", as.integer(background$max_rna_counts %||% DEFAULT_BACKGROUND_MAX_RNA_COUNTS),
  "--background-min-droplets", as.integer(background$min_droplets %||% DEFAULT_BACKGROUND_MIN_DROPLETS)
)
st <- system2(py_bin(), shQuote(args), stdout = TRUE, stderr = TRUE)
cat(paste(st, collapse = "\n"), "\n")
if (!identical(attr(st, "status") %||% 0L, 0L)) {
  stop(sprintf("CITE-seq ingest failed: %s", paste(st, collapse = "\n")))
}
ig <- file.path(cfg$project$outdir, "ingest")
meta_json <- jsonlite::read_json(file.path(ig, "ingest_meta.json"), simplifyVector = TRUE)

if (as.integer(meta_json$assay_identity_violations) != 0L) {
  update_provenance(cfg, list(assay = list(
    identity_violations = as.integer(meta_json$assay_identity_violations),
    declared_regime = meta_json$declared_regime,
    observed_feature_types = meta_json$observed_feature_types)))
  stop(sprintf("assay-identity violation (declared=%s, observed=%s): not CITE-seq; exclude/handoff, do not integrate.",
               meta_json$declared_regime, paste(meta_json$observed_feature_types, collapse = "+")))
}

# ---- build the Seurat object (RNA + ADT) -----------------------------------
rna <- read_mtx_gz(file.path(ig, "rna.mtx.gz"), file.path(ig, "rna_features.tsv.gz"), file.path(ig, "barcodes.tsv.gz"))
adt <- read_mtx_gz(file.path(ig, "adt.mtx.gz"), file.path(ig, "adt_features.tsv.gz"), file.path(ig, "barcodes.tsv.gz"))
# Seurat rewrites '_' to '-' in feature names. RNA retains its established make.unique behavior;
# ADT uses the strict shared R/Python contract so aliases cannot silently misalign matrices.
rownames(rna) <- make.unique(gsub("_", "-", rownames(rna)))
rownames(adt) <- canonical_adt_feature_names(rownames(adt), "Seurat ADT panel")

obj <- CreateSeuratObject(counts = rna, assay = "RNA", project = "s38")
obj[["ADT"]] <- CreateAssayObject(counts = adt)
log_msg("Seurat:", ncol(obj), "cells |", nrow(rna), "genes |", nrow(adt), "ADT features")

# metadata (barcode-keyed)
mpath <- file.path(cfg$input$dir, "metadata.csv")
if (file.exists(mpath)) {
  md <- utils::read.csv(mpath, stringsAsFactors = FALSE)
  rownames(md) <- md$barcode
  md <- md[colnames(obj), , drop = FALSE]
  for (cc in setdiff(colnames(md), "barcode")) obj[[cc]] <- md[[cc]]
}

# isotype controls actually present in the panel (names already dash-sanitized)
iso_req <- canonical_adt_feature_names(
  cfg$adt$isotype_controls %||% character(0), "configured isotype controls"
)
iso_present <- intersect(iso_req, rownames(adt))
if (length(iso_present) == 0) iso_present <- grep("(?i)isotype|^igg", rownames(adt), value = TRUE, perl = TRUE)

saveRDS(obj, ckpt(cfg, "01_ingest.rds"))
saveRDS(list(isotypes = iso_present, ingest_dir = ig, adt_features = rownames(adt),
             filtered_barcodes = colnames(obj),
             dsb_background_selection = meta_json$dsb_background_selection),
        ckpt(cfg, "01_ingest_meta.rds"))

# ---- provenance + facts fragment -------------------------------------------
update_provenance(cfg, list(assay = list(
  identity_violations = 0L,
  declared_regime = meta_json$declared_regime %||% "cite_seq",
  observed_feature_types = meta_json$observed_feature_types,
  isotype_controls_present = length(iso_present) > 0,
  n_adt_features = nrow(adt), empty_droplet_count = as.integer(meta_json$empty_droplet_count %||% 0))))
write_facts_fragment(cfg, "facts_01.json", list(
  n_input_cells = ncol(obj), n_adt_features = nrow(adt),
  n_isotype_controls = length(iso_present)))
log_msg("Ingest complete. identity_violations=0. n_input_cells =", ncol(obj))
