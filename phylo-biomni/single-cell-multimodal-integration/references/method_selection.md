# Method selection — regime → method decision tree

The governing rule: **establish the design regime before choosing an integration method.** Picking a
method first (e.g. "run WNN") is the field's most common error, because the right method is a function
of how the data were generated, not of what you hope to find. `00_route.R` resolves the regime from
the *actual* 10x feature types and pairing structure, never from the file name or a config assertion.

## Decision tree (implemented in `_route.R::resolve_route`)

```
feature types present in filtered_feature_bc_matrix.h5 / separate inputs
│
├─ Gene Expression + Antibody Capture (no Peaks)           → CITE-seq route (primary)
│     paired RNA + surface protein in the SAME cells       → Seurat WNN + totalVI
│
├─ Gene Expression + Peaks (no Antibody Capture)           → ROUTE to scatac-multiome-analysis
│     genuine paired RNA+ATAC multiome                        (scatac-multiome-analysis)
│
├─ Gene Expression + Antibody Capture + Peaks              → STOP (trimodal, out of scope v1)
│
├─ Gene Expression only                                    → STOP (use a scRNA-seq skill)
│
└─ separate RNA and ATAC inputs (disjoint or partial cells)
      ├─ barcode overlap ~0                                → STOP (GLUE unsupported here)
      └─ partial overlap (bridge cells) or regime=mosaic   → STOP (StabMap unsupported here)
```

A valid-looking declaration attached to mismatched feature types (e.g. metadata `assay=cite_seq`
over a Gene Expression + Peaks matrix) is a **resource-identity failure**: it is excluded / handed
off, not force-integrated (`assay.identity_violations > 0`).

## Why each method for its regime

- **CITE-seq → Seurat WNN (primary) + totalVI (alternative).** RNA and ADT are measured in the same
  cells but on very different scales and noise models. WNN [3] learns per-cell modality weights so
  neither modality is assumed dominant; the weights are a diagnostic (median weight, distribution),
  not a nuisance. totalVI [4] is a probabilistic alternative that models ADT background explicitly;
  it is reported as a *prediction*, and it runs only when its assumptions hold (enough cells,
  backend available) — otherwise a documented skip. Running both guards against a method-specific
  artifact being read as biology.
- **ADT normalization → explicit dsb or CLR.** Antibody-derived tags carry heavy ambient background.
  For dsb [2], the complete filtered-cell barcode set and any per-cell metadata barcodes are excluded
  before raw droplets are restricted to a declared low-depth RNA/ADT region with positive ADT capture.
  The selected population and rejection counts are recorded. An explicit dsb request fails when this
  population is absent or insufficient; it never treats every raw-minus-post-QC barcode as ambient and
  never silently substitutes CLR. CLR remains an explicit route. The post-normalization isotype median
  is a measured diagnostic without a universal near-zero acceptance threshold.

- **ADT marker effects follow the normalized scale.** DSB outputs are standardized denoised scores,
  not log-normalized counts. Protein markers therefore report an arithmetic normalized-score
  difference with raw-count detection fractions and adjusted Wilcoxon P values, not `avg_log2FC`.
  Declared isotype controls are excluded from biological marker ranking and heatmaps.
- **Paired RNA+ATAC → route to `scatac-multiome-analysis`.** That skill implements the extended WNN + peak-recall +
  chromVAR joint route for chromatin; duplicating it here would fork a validated workflow. The
  route destination is asserted by the production router.
- **Unpaired/diagonal GLUE → unsupported stop.** GLUE [6] requires a genuine genomic guidance
  graph, model training, and a validated label-transfer target. None is implemented in this package;
  installing `scglue` does not enable the route. The router stops before preprocessing.
- **Mosaic StabMap → unsupported stop.** StabMap [7] requires a genuine shared-feature bridge and
  fitted integration model. Neither is implemented in this package; installing StabMap does not
  enable the route. The router stops before preprocessing.

## No hard-coded winner / benchmark tie-breaker

The skill does not assert one method is universally best. WNN is the headline for CITE-seq because it
yields interpretable per-cell weights on a small, dependency-light stack; totalVI is provided as an
orthogonal probabilistic check. When both run, the WNN-vs-totalVI kNN agreement in the scorecard is
the tie-breaker signal — high agreement increases confidence in the joint structure; low agreement is
a flag to inspect ADT depth/denoising and totalVI convergence before interpretation. Method choice is
otherwise driven by the regime, the availability of empty droplets/isotypes/replicates, and the
integration metrics, not by a fixed ranking.

## Inference-readiness (differential abundance)

Milo differential abundance [5] runs only with **≥2 biological replicates per condition AND >1
condition** (`_common.R::replicates_sufficient`). Single-replicate or single-condition designs are
skipped and flagged in `diffabund_status.json` — a permutation over a technical partition would
manufacture significance, so it is refused rather than reported.
