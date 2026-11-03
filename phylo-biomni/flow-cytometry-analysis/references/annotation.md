# Cluster annotation (marker-driven, expert-in-the-loop)

Clusters are numbers; annotation assigns them biological identities. This is **semi-automated**: the
pipeline proposes labels from marker signatures, then a human confirms. Never ship auto-labels as
final without review.

## Step 1 — per-cluster marker signature
Compute the **median** expression of each clustering (type) marker per cluster, then z-score across
clusters so high/low is relative:

```r
med <- sapply(clusters, function(cc)
         matrixStats::rowMedians(ex[type_markers, cl == cc, drop = FALSE]))
z <- t(scale(t(med)))                    # markers x clusters, z-scored across clusters
```

Median (not mean) resists outliers; z-scoring makes the heatmap readable and comparable across
markers on different scales.

## Step 2 — make only evidence-supported proposals

Normalize measured antigen names before matching. A label is proposed only when a conservative
lineage signature has its required positive markers, enough alternative defining markers, and no
measured conflicting lineage marker. A cluster that cannot satisfy that contract is `UNRESOLVED`,
with likely alternatives and guidance about missing defining markers.

CellMarker 2.0 may supplement the likely-alternative list when at least two of a cell type's markers
were actually measured in the panel. Its positive-only score has no negative-marker evidence and is
therefore never sufficient by itself to emit or confirm a population label. Restrict it to the known
tissue/context; if context is unknown, say so.

## Step 3 — human review via an editable template
Write `annotation_template.csv` with the proposal, status, confidence, source, supporting/conflicting
markers, missing-marker guidance, alternatives, top markers, abundance, coverage, and blank
`population`, `merge_decision`, and `decision_reason` fields.

Use `merge_decision=merge` on every member of a repeated population name to merge them reproducibly.
Use `keep` on singleton populations. A reviewed pass requires a non-empty reason, a consistent
decision for every cluster, and an external human receipt fingerprinted to both the proposal and
applied map. Legacy two-column maps remain readable, and incomplete or unverified decisions may
continue, but never pass. Duplicate names marked `keep` are preserved as separate applied populations
instead of merging accidentally.

## Step 4 — verify against the heatmap
The marker heatmap (populations × markers, z-scored) is the annotation audit trail: every label
should be defensible from the shown signature (e.g. CD3+CD4+ → CD4 T; CD19+CD20+ → B; CD14+ →
monocyte). If a label is not visible in the heatmap, fix the label.

## Two-tier annotation
Prefer **coarse-then-split**: annotate major lineages confidently, then re-cluster and sub-annotate
lineages that are internally heterogeneous, marker-ambiguous, or explicitly identified by the user as
rare-population targets. Hidden reference labels and later benchmark scores never choose the analysis
partition. This yields robust top-level labels without over-interpreting noise.

## References
- Hu et al., CellMarker 2.0. Nucleic Acids Research 2023. doi:10.1093/nar/gkac947.
- Crowell et al., CATALYST (`mergeClusters`, `plotExprHeatmap`).
