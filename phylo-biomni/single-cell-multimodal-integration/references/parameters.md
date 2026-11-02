# Parameters — single-cell-multimodal-integration

Every tunable lives in `scripts/config.example.yaml` (copy to `config.yaml`). No numbers are
hard-coded in the scripts; defaults below are read via `_common.R::load_config` / PyYAML.

## project
| Key | Default | Meaning |
|---|---|---|
| `project.outdir` | `/workspace/s38_run` | working dir: `ingest/`, `figures/`, `tables/`, `checkpoints/` |
| `project.results_dir` | `/mnt/results` | results root: deliverables + branded report (must equal `report_qc` RESULTS for the terminal step) |

## input / route
| Key | Default | Meaning |
|---|---|---|
| `route` | `auto` | `auto` \| `citeseq` \| `unpaired` \| `mosaic` \| `handoff` \| `stop`; `unpaired` and `mosaic` are explicit unsupported hard-stops |

## adt (antibody-derived tags)
| Key | Default | Meaning |
|---|---|---|
| `adt.isotype_controls` | `[IgG1_isotype, IgG2a_isotype]` | isotype antibody names (matched after `_`→`-` sanitization); enable dsb denoising and the isotype-background assertion |
| `adt.normalization` | `dsb` | `dsb` (needs a valid captured low-depth non-cell background) \| `clr`; an explicit dsb request fails rather than silently substituting CLR when its background is invalid |
| `adt.dsb_background.min_adt_counts` / `max_adt_counts` | 10 / 200 | raw ADT library-size capture/depth bounds for non-cell background droplets |
| `adt.dsb_background.max_rna_counts` | 200 | maximum raw RNA count for a background droplet |
| `adt.dsb_background.min_droplets` | 10 | minimum selected background droplets required to run dsb |

## qc
| Key | Default | Meaning |
|---|---|---|
| `qc.rna.min_genes` / `max_genes` | 30 / 100000 | retained-cell gene bounds |
| `qc.rna.min_counts` | 60 | minimum RNA UMIs |
| `qc.rna.max_pct_mt` | 25 | maximum percent mitochondrial (`mt_pattern`, default `^MT-`) |
| `qc.adt.min_counts` / `max_counts` | 10 / 100000 | ADT total-count bounds; a cell is kept only if it passes RNA **and** ADT QC |

## wnn
| Key | Default | Meaning |
|---|---|---|
| `wnn.rna_dims` | 20 | RNA PCA dimensions |
| `wnn.adt_dims` | 10 | ADT PCA dimensions (capped at n_ADT_markers − 1; isotypes excluded from the embedding) |
| `wnn.k_nn` | 20 | neighbors for `FindMultiModalNeighbors` |
| `wnn.cluster_resolution` | 0.8 | Louvain/SLM resolution on the `wsnn` graph |

## totalvi
| Key | Default | Meaning |
|---|---|---|
| `totalvi.enabled` | true | run the totalVI alternative |
| `totalvi.min_cells` | 200 | documented skip below this joint-cell count |
| `totalvi.max_epochs` | 40 | bounded for CPU micro-runs; raise for production |
| `totalvi.n_latent` | 15 | latent dimensionality |

## integration / diffabund / annotation
| Key | Default | Meaning |
|---|---|---|
| `integration.batch_key` | `replicate` | batch label for the kNN batch-mixing metric |
| `integration.label_key` | `cell_type` | optional independent reference label for ARI/NMI purity (omit if unknown) |
| `diffabund.condition_col` / `replicate_col` | `condition` / `replicate` | design columns for the replicate-sufficiency gate |
| `diffabund.min_rep` | 2 | ≥ this many biological replicates per condition (and >1 condition) required for Milo |
| `diffabund.k` / `prop` | 20 / 0.2 | Milo graph neighbors / neighborhood sampling proportion |
| `annotation.panel` | `builtin_pbmc` | built-in surface-protein + gene panel (CellMarker 2.0 withheld) |

`protein_markers.csv` reports `avg_normalized_adt_difference`, the arithmetic mean normalized ADT
score inside a cluster minus outside it, together with raw-count detection fractions and adjusted
Wilcoxon P values. Declared isotype controls are excluded from biological marker ranking and remain
normalization diagnostics only. It does not report a log2 fold change for DSB standardized scores.

## report
| Key | Default | Meaning |
|---|---|---|
| `report.pdf_name` | `report_single_cell_multimodal_integration.pdf` | branded PDF at the results root |
| `report.infographic_name` | `..._infographic.png` | GenerateImage infographic embedded on page 1 |
| `report.title` / `subtitle` / `sample_description` | see example | report cover text |

## Tuning guidance
- **10x data:** raise `qc.rna.min_counts`/`min_genes` to the knee of the selected input's count distribution
  and set `qc.rna.max_pct_mt` from its mitochondrial-fraction distribution.
- **Degenerate modality weights** (median ≈ 0 or 1): one modality dominates — check ADT depth and
  dsb denoising before interpreting the joint embedding.
- **totalVI** on production data: raise `max_epochs` (e.g. 200–400) and prefer a GPU; record the
  chosen training budget and convergence evidence.
