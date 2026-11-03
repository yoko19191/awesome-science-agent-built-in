#!/usr/bin/env Rscript
# Pathway / TF activity by the decoupleR univariate linear model (ULM), implemented in base R.
#
# For each compound's top-dose log2FC vector x (over all tested genes) and each gene set S with 0/1
# membership indicator m, ULM fits lm(x ~ m) and reports the t-statistic of the membership slope as
# the activity score. It is deterministic (closed-form; no permutation). decoupleR/decoupler are
# recommended in production for the full ensemble + consensus; see references and DATA_SOURCES.md.
#
# Usage:
#   Rscript activity_ulm.R --de tables/de_all_contrasts.csv --genesets genesets.gmt \
#       --outdir /mnt/results [--min-size 5]
# Writes <outdir>/tables/activity_scores.csv  (rows = gene sets, cols = compounds; ULM t-values).

args <- commandArgs(trailingOnly = TRUE)
get_arg <- function(flag, default = NULL) {
  i <- match(flag, args); if (is.na(i)) return(default); args[[i + 1]]
}
de_path <- get_arg("--de")
gmt_path <- get_arg("--genesets")
outdir <- get_arg("--outdir")
min_size <- as.integer(get_arg("--min-size", "5"))
if (is.null(de_path) || is.null(gmt_path) || is.null(outdir)) {
  stop("required: --de --genesets --outdir")
}
tables <- file.path(outdir, "tables")
dir.create(tables, recursive = TRUE, showWarnings = FALSE)

de <- read.csv(de_path, stringsAsFactors = FALSE)
if (!"contrast_family" %in% colnames(de)) stop("DE table is missing semantic contrast_family")
de <- de[!is.na(de$compound) & de$contrast_family == "treatment_vs_vehicle", ]

# Per-compound top-dose log2FC over all genes.
compounds <- sort(unique(de$compound))
all_genes <- sort(unique(de$gene_id))
sig_mat <- matrix(0.0, nrow = length(all_genes), ncol = length(compounds),
                  dimnames = list(all_genes, compounds))
for (comp in compounds) {
  sc <- de[de$compound == comp, ]
  top <- max(sc$dose_uM)
  sc <- sc[sc$dose_uM == top, ]
  sig_mat[sc$gene_id, comp] <- sc$log2FoldChange
}

# Parse GMT.
gmt_lines <- readLines(gmt_path)
sets <- list()
for (ln in gmt_lines) {
  parts <- strsplit(ln, "\t")[[1]]
  if (length(parts) < 3) next
  genes <- parts[3:length(parts)]
  genes <- genes[genes %in% all_genes]
  if (length(genes) >= min_size) sets[[parts[1]]] <- genes
}
if (length(sets) == 0) stop("no gene sets passed the minimum-size filter against the tested genes")

set_names <- names(sets)
act <- matrix(NA_real_, nrow = length(set_names), ncol = length(compounds),
              dimnames = list(set_names, compounds))
for (sn in set_names) {
  m <- as.numeric(all_genes %in% sets[[sn]])
  for (comp in compounds) {
    x <- sig_mat[, comp]
    fit <- summary(lm(x ~ m))
    act[sn, comp] <- if ("m" %in% rownames(fit$coefficients)) fit$coefficients["m", "t value"] else 0.0
  }
}

out <- data.frame(gene_set = rownames(act), act, check.names = FALSE)
write.csv(out, file.path(tables, "activity_scores.csv"), row.names = FALSE)
cat(sprintf("activity_ulm: %d gene sets x %d compounds (ULM t-values)\n",
            length(set_names), length(compounds)))
