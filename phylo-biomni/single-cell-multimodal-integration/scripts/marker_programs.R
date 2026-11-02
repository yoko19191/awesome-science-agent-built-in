# Canonical built-in PBMC surface-protein programs.
# Lineage panels deliberately exclude naive/memory differentiation markers.
pbmc_surface_programs <- function() list(
  "CD4 T"    = c("CD3", "CD4"),
  "CD8 T"    = c("CD3", "CD8"),
  "B"        = c("CD19"),
  "Monocyte" = c("CD14", "CD16"),
  "NK"       = c("CD56", "CD16")
)

canonical_surface_marker <- function(features) {
  marker <- toupper(canonical_adt_feature_names(features, "surface-marker panel"))
  marker <- sub("[-. ]*TOTALSEQ[A-Z0-9]*$", "", marker, perl = TRUE)
  marker[marker == "CD8A"] <- "CD8"
  marker
}

surface_marker_matches <- function(features, markers) {
  feature_markers <- canonical_surface_marker(features)
  wanted <- canonical_surface_marker(markers)
  features[feature_markers %in% wanted]
}
