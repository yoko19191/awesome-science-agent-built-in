---
name: chembl-hit-series-sar-docking
description: 'Triage the known ChEMBL hits for any protein target: score by potency
  and efficiency, cluster into chemical series, run within-series SAR (MCS, R-groups),
  dock representative ligands with Vina, emit a PDF report plus an interactive HTML
  data appendix. Starts from experimental ChEMBL bioactivity, not a compound library
  and not an assay-interference screen. Triggers: hit triage, hit expansion, screening
  hits, chemical series, SAR, target-based triage.'
category: drug_discovery
visibility: public
starting-prompt: 'Triage small-molecule screening hits against EGFR from ChEMBL: score
  by potency, cluster into series, run SAR, dock representative ligands, and tell
  me which series are worth expanding.'
---
# ChEMBL Hit Triage, SAR & Docking

Turn a protein target name or ChEMBL target ID into a prioritized, series-aware
hit-expansion report. Retrieve bioactivity from ChEMBL, score hits by potency
and efficiency, cluster into chemical series, run SAR within each series, dock
representative ligands into a relevant PDB structure, and deliver an interactive
Phylo-branded PDF report (the primary deliverable) plus an interactive HTML data appendix.

## When to Use This Skill

**Use this skill when** you have a **protein target** (a name/synonym, a UniProt accession, or a
ChEMBL target ID) and want to triage its **known ChEMBL hits**: score them by potency and efficiency,
cluster them into chemical series, run within-series SAR, dock representative ligands into a relevant
PDB structure, and get a ranked, series-aware "which series are worth expanding" answer.

**Supported inputs (routes):**

- A **target identifier** → the skill resolves it against ChEMBL and retrieves experimental bioactivity.
- Your **own bioactivity CSV** (`smiles`, `standard_value`, `standard_type`, `molecule_id`) → the
  skill skips ChEMBL retrieval and proceeds from scoring; it fails loudly on missing columns and never
  fabricates input data.
- Optional: PDB override (`--pdb-id`/`--pdb-file`), assay-variant filter, and scoring/clustering
  thresholds (see Inputs).

**Outputs:** a Phylo-branded PDF report (primary), an interactive HTML data appendix, and the
underlying CSV/JSON artifacts — scored/clustered hits with variant provenance, docking results,
variant composition, SAR results, and docking-box provenance (see Outputs).

**Do NOT use this skill when** (use the named skill instead):

- you have a **single compound** and want its potency/selectivity across targets → `drug-bioactivity-chembl`
- you have a **compound library** (SMILES/CSV) to dock or screen at scale → `large-scale-virtual-screening`
- you need to judge whether a hit is a **real signal or an assay artefact** → `screen-hit-triage-interference`
- you need **quantitative activity-cliff / matched-molecular-pair** statistics → `matched-molecular-pairs`
- you want to **predict potency**, run **MD or free-energy** calculations, confirm binding
  experimentally, profile selectivity across many targets, or synthesize compounds — all out of scope.
  This skill uses only experimental ChEMBL potency and treats docking as structural plausibility, not
  affinity prediction.

## Scope

**Does:** resolve a ChEMBL target from a name/UniProt ID → pull bioactivity
records (IC50/Ki/Kd/EC50) → standardize to pChEMBL → score hits (potency +
ligand/lipophilic efficiency + drug-likeness) → tier hits → Butina cluster into
chemical series → SAR per series (Murcko scaffold, MCS, property-potency
correlations, R-group decomposition, plus a **coarse activity-cliff flag**) →
select representative ligands → dock with AutoDock Vina against a user-specified
or auto-selected PDB structure → generate 10 figures → produce an interactive
HTML report with sortable tables and embedded figures → produce a PDF report via
the `pdf-report-generation` skill.

**Does NOT:** predict potency (all potency is experimental ChEMBL data); run
free-energy perturbation or MD; confirm binding experimentally; perform
selectivity profiling across targets (use `drug-bioactivity-chembl` for that);
run large-scale virtual screening of external libraries (use
`large-scale-virtual-screening` for that); screen hits for assay interference or
promiscuity (use `screen-hit-triage-interference`); perform quantitative
activity-cliff or matched-molecular-pair analysis (use
`matched-molecular-pairs`); or synthesize compounds.

**Activity cliffs — this skill is not the authority.** The per-series cliff flag
here is a coarse orientation aid only. Cliff counts are extremely sensitive to the
similarity criterion — on one dataset the same pairs yielded 0.13 % under a
Tanimoto-only rule versus 6.04 % under a consensus rule. Quantitative cliff work
belongs in `matched-molecular-pairs`, which reports counts under every criterion
with the criterion named. **Never present this skill's cliff flag as a cliff
statistic**, and never let it contradict an MMP report.

## Boundary with existing skills

| Existing skill                     | What it does                                                                                                                   | Boundary                                                                                                                                                                                                                                                                                                       |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `drug-bioactivity-chembl`        | Compound-centric potency + selectivity profiling from ChEMBL                                                                   | **Compound → many targets.** This skill is **target → many compounds** with series clustering, SAR, and docking.                                                                                                                                                                                 |
| `large-scale-virtual-screening`  | Dock a library (ChEMBL/DUD-E/user SMILES) with Vina, enrichment benchmarking, triage + SAR                                     | **Library → docking scores → triage.** This skill starts from ChEMBL bioactivity (experimental potency), adds series-level SAR with MCS/R-groups, and docks only representative ligands -- not a full library screen. Reuse its docking scripts for the docking step if a full-scale screen is needed. |
| `screen-hit-triage-interference` | Flags whether an apparent hit is an assay artefact -- frequent-hitter/promiscuity prediction and demerit-scored reactivity     | **Is this hit real?** vs **which of these real hits should I expand?** That skill judges trustworthiness of the signal; this one assumes the potency is real and organises it into series. They compose: run interference triage first, then bring the surviving hits here.                        |
| `matched-molecular-pairs`        | Quantitative MMP transformations and activity cliffs, with bootstrap CIs, support thresholds and per-criterion cliff reporting | **Authority for cliffs and transformation statistics.** This skill's per-series cliff flag is a coarse orientation aid only and must never be reported as a cliff statistic. Hand any quantitative SAR-transformation or cliff question to that skill.                                                   |

**When to use this skill vs. others:**

- User has a **target** and wants to triage known hits → **this skill**
- User has a **compound** and wants its potency/selectivity profile → `drug-bioactivity-chembl`
- User has a **library** (SMILES/CSV) and wants to dock it → `large-scale-virtual-screening`
- User asks whether hits are **real or assay artefacts** → `screen-hit-triage-interference`
- User wants **how a structural change shifts potency**, or real cliff numbers → `matched-molecular-pairs`

## Inputs

### Required

- **Target identifier** — one of:
  - ChEMBL target ID (`CHEMBLxxxx`)
  - Protein name or synonym (e.g. "TYK2", "EGFR", "BRAF")
  - UniProt accession (e.g. "P29597") — used for both ChEMBL target resolution and PDB structure search

### Optional (with defaults)

| Parameter               | Default                                        | Accepted values                                                         | Notes                                                                                                                                                                                                                                                               |
| ----------------------- | ---------------------------------------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `potency_types`       | `IC50,Ki,Kd,EC50,AC50,Potency`               | Comma-separated ChEMBL `standard_type` values                         | pIC50/pKi/pKd are auto-converted                                                                                                                                                                                                                                    |
| `relation_filter`     | `exact_only`                                 | `exact_only`, `include_bounds`                                      | `exact_only` keeps `=` records only; `include_bounds` also keeps `>`/`<` as censored bounds                                                                                                                                                               |
| `pchembl_min`         | `5.0` (10 µM)                               | float                                                                   | Minimum pChEMBL to include as a hit                                                                                                                                                                                                                                 |
| `pchembl_max`         | `11.0` (0.001 nM)                            | float                                                                   | Maximum pChEMBL (sanity ceiling)                                                                                                                                                                                                                                    |
| `variant`             | `any`                                        | `any`, `WT`, or a mutation string (e.g. `L858R`, `L858R,T790M`) | Restrict scoring to one assay variant (`process_activities.py`). Default keeps all variants but reports the composition and warns about pooling; it is never silent. A named/WT selector fails loudly, listing available variants, if nothing matches.            |
| `butina_cutoff`       | `0.40` (Tanimoto distance = 0.60 similarity) | float 0–1                                                              | Butina clustering distance cutoff                                                                                                                                                                                                                                   |
| `min_series_size`     | `5`                                          | int                                                                     | Minimum cluster size to label as a named series                                                                                                                                                                                                                     |
| `top_n_series_sar`    | `20`                                         | int                                                                     | Number of top series to run full SAR on                                                                                                                                                                                                                             |
| `dock_top_per_series` | `3`                                          | int                                                                     | Representative ligands per series for docking                                                                                                                                                                                                                       |
| `dock_top_overall`    | `10`                                         | int                                                                     | Additional top hits by potency for docking                                                                                                                                                                                                                          |
| `pdb_id`              | auto-select                                    | PDB ID string                                                           | Override auto-selection; must contain a co-crystal ligand                                                                                                                                                                                                           |
| `receptor_variant`    | auto                                           | string (e.g. `WT`, `L858R`)                                         | Declares which variant the docked structure represents (`prepare_docking.py --receptor-variant`); recorded in `docking_box.json` and surfaced in reports. Auto = RCSB annotation (`pdbx_mutation`, or `mutation_count==0` → `WT`), else `unspecified`. |
| `vina_exhaustiveness` | `16`                                         | int                                                                     | Vina exhaustiveness parameter                                                                                                                                                                                                                                       |
| `vina_seed`           | `42`                                         | int                                                                     | Random seed for reproducibility                                                                                                                                                                                                                                     |
| `organism`            | `Homo sapiens`                               | string                                                                  | Filter ChEMBL targets by organism                                                                                                                                                                                                                                   |
| `target_type`         | `SINGLE PROTEIN`                             | string                                                                  | Filter ChEMBL target type                                                                                                                                                                                                                                           |

### User-supplied data (alternative to ChEMBL retrieval)

A caller may supply their own bioactivity table as a CSV with at minimum:

- `smiles` (canonical SMILES)
- `standard_value` (numeric potency in nM)
- `standard_type` (IC50, Ki, Kd, etc.)
- `molecule_id` (any identifier)

When user data is supplied, the skill **skips ChEMBL retrieval** and proceeds
from scoring onward. The skill **fails loudly** if required columns are missing
-- it never fabricates input data or silently falls back to a built-in example.

## Outputs (all to `/mnt/results/`)

| File                                                                     | Description                                                                                                                                                                                         |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `report_<target>_hit_triage.pdf` (e.g. `report_EGFR_hit_triage.pdf`) | **Primary deliverable.** Phylo-branded PDF assembled by `pdf-report-generation` (intro, methods, results, conclusions, figures, references, next steps)                                     |
| `<target>_hit_triage_report.html`                                      | **Interactive data appendix.** Self-contained HTML with embedded figures and sortable/filterable hit tables — for exploring the full hit list, which a PDF cannot do. Not the primary report |
| `<target>_hits_scored_clustered.csv`                                   | Full hit table with scores, tiers, cluster IDs, series labels, and variant columns (`variant_best`, `variant_summary`, `n_variants`, `assay_variant_accession`)                             |
| `<target>_docking_results.csv`                                         | Per-ligand docking scores with experimental potency **and the assay variant** each potency came from                                                                                          |
| `variant_composition.json`                                             | Per-variant record/molecule counts and percentages (WT/unspecified as its own bucket), the pooling flag, and the applied `--variant` filter                                                       |
| `<target>_sar_results.json`                                            | Per-series SAR: MCS, Murcko scaffold, activity cliffs, correlations, R-groups, recommendations                                                                                                      |
| `<target>_series_stats.csv`                                            | Per-series summary statistics, including the per-series variant mix (`n_variants_in_series`, `dominant_variant`)                                                                                |
| `docking_box.json`                                                     | Docking box + `pdb_selection` provenance + `receptor_variant`                                                                                                                                   |
| `figures/`                                                             | 10 figures as PNG; 9 also as SVG (fig8 structure grid is raster-only). All 10 are embedded in the HTML report.                                                                                      |

## Figures

All 10 figures are produced by `scripts/generate_figures.py` (step 7), embedded in the interactive
HTML appendix (step 8), and passed to the delegated PDF (step 9). Both reports read their figure list
from the run's `'figures'` directory (the files below); 9 are also written as SVG (fig8 is
raster-only). Captions state what each figure shows, not what it is called.

| Step | File                                         | What it shows                                                                                                     |
| ---- | -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| 7    | `figures/fig1_potency_distribution.png`    | pChEMBL potency distribution and hit-tier counts                                                                  |
| 7    | `figures/fig2_series_overview.png`         | chemical series ranked by mean hit score                                                                          |
| 7    | `figures/fig3_chemical_space.png`          | 2D chemical space of the hit set                                                                                  |
| 7    | `figures/fig4_docking_vs_potency.png`      | docking score vs experimental pChEMBL, with the honest Pearson r/p (labelled not-significant when it is)          |
| 7    | `figures/fig5_series_potency_boxplots.png` | potency distribution across the top series                                                                        |
| 7    | `figures/fig6_sar_correlations.png`        | within-series property-potency correlations                                                                       |
| 7    | `figures/fig7_le_lipe.png`                 | ligand efficiency vs lipophilic efficiency                                                                        |
| 7    | `figures/fig8_top_hit_structures.png`      | 2D structure grid of the top hits (raster-only)                                                                   |
| 7    | `figures/fig9_series_recommendations.png`  | series EXPAND/EXPLORE/MONITOR/DROP recommendations                                                                |
| 7    | `figures/fig10_docking_by_series.png`      | docking-score distribution across the top series (subset to the top-N so it stays legible on high-series targets) |

## Workflow

Run the scripts in `scripts/` in order. Each script is self-contained and
writes its output to a data directory. Use `/opt/conda/bin/python` for all
script invocations (see Environment Setup).

### 0. Environment setup (once per run)

```bash
uv pip install --python /opt/conda/bin/python vina meeko gemmi
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
```

RDKit, pandas, numpy, scipy, matplotlib, seaborn, reportlab, and pypdf are
pre-installed. Vina CLI, Meeko, and gemmi must be installed per run. See
`references/environment_and_troubleshooting.md` for install quirks.

### 1. Retrieve and resolve target from ChEMBL

```bash
/opt/conda/bin/python scripts/retrieve_activities.py \
    --target "TYK2" \
    --organism "Homo sapiens" \
    --target-type "SINGLE PROTEIN" \
    --outdir <run_dir>/data
```

**Why:** The target must be resolved to a single ChEMBL target ID before pulling
bioactivity. The script searches by name/synonym, filters by organism and target
type, and prints all candidates. If multiple candidates remain, it picks the
`SINGLE PROTEIN` target for the specified organism and states the choice. If no
match is found, it fails loudly with the candidates it considered.

**What changes with user data:** If the caller supplies a CSV, skip this step
entirely. The skill proceeds from Step 2 with the user's table.

### 2. Process and clean bioactivity records

```bash
/opt/conda/bin/python scripts/process_activities.py \
    --input <run_dir>/data/activities_raw.json \
    --outdir <run_dir>/data \
    --variant any                     # or: WT | L858R | "L858R,T790M" | T790M ...
```

**Variant handling (see scientific caveat #1):** This step retains the structured
`assay_variant_mutation` / `assay_variant_accession` fields, reports the variant composition
(counts + percentages, WT/unspecified as its own bucket) to the console and to
`variant_composition.json`, and **warns prominently when more than one variant is pooled**.
`--variant any` (default) keeps all variants (backward compatible, never silent); `--variant WT`
or `--variant <MUTATION>` restricts scoring to one variant and fails loudly if none match. The
variant of each molecule's best measurement (`variant_best`), the set it was measured against
(`variant_summary`, `n_variants`), and `assay_variant_accession` are written into the hit table.

**Why:** Raw ChEMBL records contain heterogeneous endpoints, units, and
relations. This step:

- Filters to potency-type endpoints (IC50, Ki, Kd, EC50, etc.)
- Keeps numeric values > 0 in nM units (or already-log-scale p-types)
- Standardizes to pChEMBL = -log10(value in M)
- Validates SMILES with RDKit and computes physicochemical properties (MW, cLogP,
  TPSA, HBD, HBA, rotatable bonds, heavy atoms, rings, aromatic rings)
- Deduplicates per molecule: keeps the best (highest pChEMBL) exact-measurement
  record, aggregates median/count/std
- Computes ligand efficiency (LE = 1.37 × pChEMBL / heavy_atoms) and LipE
  (pChEMBL - cLogP)
- Computes Lipinski Rule of 5 pass/fail

**Relation handling decision:**

| Option                   | What it does                                                           | When to use                                                                                                                                    |
| ------------------------ | ---------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `exact_only` (default) | Keeps only `=` records; drops `>`/`<` censored values            | When you want clean potency measurements for scoring and SAR. Drops molecules whose only records are bounds (e.g. "IC50 > 10 µM" = inactive). |
| `include_bounds`       | Keeps `>`/`<` as censored bounds, flagged but not used for scoring | When you want to preserve inactive compounds for completeness or selectivity analysis.                                                         |

**Caveat:** The default `exact_only` filter dropped 1,657 molecules that had only
`>` (inactive) records in the TYK2 reference run. These molecules are genuinely
weak/inactive -- their exclusion is correct for hit triage but should be
documented in the report.

### 3. Score hits and cluster into chemical series

```bash
/opt/conda/bin/python scripts/score_and_cluster.py \
    --input <run_dir>/data/hits_clean.csv \
    --outdir <run_dir>/data \
    --butina-cutoff 0.40 \
    --min-series-size 5
```

**Why:** Scoring ranks hits by a composite metric; clustering groups
structurally similar hits into series for SAR analysis.

**Scoring formula:**

| Component     | Weight | Formula                                                         | Rationale                                                         |
| ------------- | ------ | --------------------------------------------------------------- | ----------------------------------------------------------------- |
| Potency       | 50%    | Normalized pChEMBL: clip((pChEMBL - 5) / (11 - 5), 0, 1)        | Primary driver of triage decisions                                |
| Efficacy      | 30%    | 0.5 × normalized LE + 0.5 × normalized LipE                   | Rewards efficient binding, penalizes lipophilicity-driven potency |
| Drug-likeness | 20%    | 0.5 × Ro5 pass + 0.5 × normalized MW (300–500 Da sweet spot) | Favors developable compounds                                      |

**Tier thresholds:**

| Tier             | Score range | Action                           |
| ---------------- | ----------- | -------------------------------- |
| Tier 1 (Expand)  | ≥ 0.70     | Prioritize for hit expansion     |
| Tier 2 (Explore) | 0.50–0.70  | Explore with medicinal chemistry |
| Tier 3 (Monitor) | 0.30–0.50  | Monitor, limited upside          |
| Tier 4 (Drop)    | < 0.30      | Deprioritize                     |

**Clustering decision:**

| Method                                      | Default | Why                                                                                                                         | When to switch                                                                                                                                                                                                             |
| ------------------------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Butina on ECFP4 (Morgan r=2, 2048 bits)     | ✓      | Standard for medicinal chemistry; deterministic; bulk-Tanimoto distances make ~10k molecules trivial (~6 s, ~1.4 GB, 1 CPU) | Dense distance matrix is O(n²) **memory** (~0.9 GB at 10k, ~3.5 GB at 20k, ~14 GB at 40k); for >~25k molecules on a 16 GB worker switch to Taylor–Butina/sphere-exclusion with fingerprint bucketing, or subsample |
| Tanimoto distance cutoff 0.40 (sim ≥ 0.60) | ✓      | Balanced: groups compounds with shared scaffold but allows R-group variation                                                | Use 0.35 (sim ≥ 0.65) for tighter series; 0.50 (sim ≥ 0.50) for broader groupings                                                                                                                                        |
| Min series size 5                           | ✓      | Need ≥5 members for meaningful SAR statistics                                                                              | Lower to 3 for small datasets; raise to 10 for very large datasets                                                                                                                                                         |

### 4. Run SAR analysis within each series

```bash
/opt/conda/bin/python scripts/sar_analysis.py \
    --input <run_dir>/data/hits_scored_clustered.csv \
    --outdir <run_dir>/data \
    --top-n-series 20
```

**Why:** Series-level SAR reveals which structural features drive potency and
where activity cliffs signal sensitive optimization points.

**SAR components per series:**

| Component                         | Method                                                                        | Default                      | When it fails                                                                                                                                                         |
| --------------------------------- | ----------------------------------------------------------------------------- | ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Maximum Common Substructure (MCS) | `rdFMCS.FindMCS` with `completeRingsOnly=True`                            | timeout=10s, threshold=0.6   | Falls back to Murcko scaffold. **Critical:** use `completeRingsOnly`, not `completeRings` -- the latter raises a Boost.Python.ArgumentError in RDKit 2025.x |
| Murcko scaffold                   | `MurckoScaffold.GetScaffoldForMol`                                          | Always runs                  | Robust; rarely fails                                                                                                                                                  |
| Activity cliffs                   | Pairs with Tanimoto ≥ 0.85 and ΔpChEMBL ≥ 2.0                              | Top 20 examples stored       | O(n²) per series; skip if series > 500 members                                                                                                                       |
| Property-potency correlations     | Pearson r for MW, cLogP, TPSA, HBD, HBA, rot_bonds, LE, LipE vs pChEMBL       | Requires ≥5 non-null values | Reports None if insufficient data                                                                                                                                     |
| R-group decomposition             | `rdRGroupDecomposition.RGroupDecompose` with `asSmiles=True, asRows=True` | Requires MCS success         | Skipped if MCS fails; Murcko scaffold provides a fallback scaffold view                                                                                               |

**Series recommendation logic:**

| Recommendation           | Condition                                              | Rationale                                                                                                                                                     |
| ------------------------ | ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| EXPAND                   | median pChEMBL ≥ 8.0 AND n ≥ 50 AND median LE ≥ 0.3 | High potency, good efficiency, sufficient SAR coverage                                                                                                        |
| EXPLORE                  | median pChEMBL ≥ 7.0 AND n ≥ 30                      | Moderate potency with SAR headroom                                                                                                                            |
| MONITOR                  | median pChEMBL ≥ 6.0 AND n ≥ 20                      | Marginal potency, limited upside                                                                                                                              |
| DROP                     | median pChEMBL < 6.0                                   | Low potency series                                                                                                                                            |
| + discontinuous-SAR flag | n_activity_cliffs > 10                                 | Appended to any recommendation as a **coarse orientation aid**, never as a cliff statistic; points at `matched-molecular-pairs` for quantitative work |

### 5. Select PDB structure and prepare docking

```bash
/opt/conda/bin/python scripts/prepare_docking.py \
    --hits <run_dir>/data/hits_scored_clustered.csv \
    --pdb-id 4GIH \
    --receptor-variant WT \
    --outdir <run_dir>/tmp \
    --data-dir <run_dir>/data \
    --dock-top-per-series 3 \
    --dock-top-overall 10
```

**Receptor variant:** `--receptor-variant <label>` declares which variant the docked structure
represents; it is recorded in `docking_box.json` (`receptor_variant`) and surfaced in the reports
so the mismatch against pooled activity variants is explicit. If omitted, the script makes a
best-effort read of RCSB `pdbx_mutation` (and treats `mutation_count == 0` as `WT`), else records
`unspecified`. On the EGFR run, auto-selected 8A27 resolved to `WT`.

**PDB structure selection decision:**

| Criterion         | Default                                                 | Why                                                                                                         | When to override                                                                    |
| ----------------- | ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Domain relevance  | Match the domain that ChEMBL bioactivity assays measure | Most kinase inhibitors target the catalytic domain; docking to the wrong domain produces meaningless scores | User specifies a different domain (e.g. JH2 pseudokinase for allosteric inhibitors) |
| Co-crystal ligand | Required                                                | Defines the binding pocket and docking box; enables box validation                                          | If no co-crystal structure exists, user must supply box coordinates                 |
| Resolution        | ≤ 2.5 Å preferred                                     | Higher resolution = more accurate receptor                                                                  | Accept lower if no better structure exists                                          |
| Method            | X-ray preferred over cryo-EM                            | X-ray generally has more accurate coordinates for docking                                                   | Use cryo-EM if no X-ray structure exists                                            |

**Auto-selection procedure** (used when neither `--pdb-id` nor `--pdb-file` is supplied):

1. Search RCSB by UniProt accession, restricted to entries with `nonpolymer_entity_count > 0`
   and sorted by resolution ascending
2. For each candidate in resolution order, reject HETATMs that are buffers, cryoprotectants,
   sugars or ions (explicit ignore list) and require a ligand of **≥ 150 Da**
3. Take the first candidate that has both a resolution and a drug-like ligand
4. Print the candidates examined and the selection rationale
5. Record the choice in `docking_box.json` under `pdb_selection`, so the report can state
   whether the receptor was user-specified or auto-selected and on what basis

The UniProt accession comes from `target_metadata.json` (written by `retrieve_activities.py`),
or can be passed explicitly with `--uniprot`. If no accession is available, or if no candidate
qualifies, the script **fails with an explicit message** telling you to supply `--pdb-id` —
it never silently docks into an arbitrary structure.

```bash
# auto-select (no --pdb-id): reads the accession from target_metadata.json
/opt/conda/bin/python scripts/prepare_docking.py \
    --hits <run_dir>/data/hits_scored_clustered.csv \
    --outdir <run_dir>/tmp --data-dir <run_dir>/data
```

Verified live against RCSB: **EGFR (P00533) → 8A27** (1.07 Å X-ray, ligand KY9, 675 Da);
**CDK2 (P24941) → 6Q4G** (0.98 Å X-ray, ligand HJK, 269 Da).

> Auto-selection picks the *best-resolution ligand-bound* structure, which is not always the
> *most relevant* one. It cannot reason about which domain or conformational state your series
> targets — see the domain-relevance row in the table above. For allosteric series, pseudokinase
> domains, or any case where the binding site matters, pass `--pdb-id` explicitly.

**Receptor preparation:**

- Extract ATOM records (protein only), remove HETATM (ligands, waters, ions)
- Convert to PDBQT using **Meeko `mk_prepare_receptor.py`** (LGPL-2.1, commercial-use safe) or ADFR `prepare_receptor` (**LGPL2**, commercial-use safe — verified at https://ccsb.scripps.edu/adfr/license/, **not** Apache 2.0)
- **Prefer Meeko/ADFR over Open Babel** for receptor PDBQT conversion — Open Babel is GPL-2.0 (copyleft: a source-distribution obligation on derivative works, **not** a commercial-use ban). Meeko/ADFR are chosen to sidestep the copyleft obligation. See the dependency inventory (`scripts/commercial_inventory.py`) and License note below.

**Ligand preparation:**

- RDKit 3D embedding (ETKDG, seed=42) + MMFF optimization (500 iterations)
- Meeko `MoleculePreparation` + `PDBQTWriterLegacy.write_string()`
- **Critical:** `PDBQTWriterLegacy.write_string()` returns a **3-tuple** in Meeko 0.7.x, not a 2-tuple. Use `result[0]` to get the PDBQT string. See `references/environment_and_troubleshooting.md`.

**Docking box:**

- Center on co-crystal ligand centroid
- Size = ligand extents + 16 Å padding, minimum 24 Å per axis
- Saved to `docking_box.json` for reproducibility
- **Permissiveness is derived, not assumed (F10).** `prepare_docking.box_diagnostics` computes
  `box_volume_A3`, `box_max_dim_A` and a `box_permissive` flag from the box geometry (focused-pocket
  guide: max axis ≤ 30 Å, volume ≤ ~27,000 Å³). When the box is permissive — e.g. it was defined by
  a large co-crystal ligand, or an allosteric-inhibitor conformation used to dock ATP-competitive
  actives — a `docking_caveats` entry is written into `docking_box.json` explaining that a wide
  search box weakens the docking-vs-potency correlation and may not match the docked ligand's binding
  mode. `report_qc.assert_docking_box_diagnostics` fails the run if a permissive box ships without
  that surfaced caveat, so this driver of a near-zero correlation is enforced, not left to prose.

### 6. Run AutoDock Vina docking

```bash
/opt/conda/bin/python scripts/run_docking.py \
    --receptor <run_dir>/tmp/receptor.pdbqt \
    --ligand-dir <run_dir>/tmp/ligands \
    --box <run_dir>/data/docking_box.json \
    --selection <run_dir>/data/docking_selection.json \
    --outdir <run_dir>/tmp/docked \
    --output <run_dir>/data/docking_results.csv \
    --exhaustiveness 16 \
    --seed 42
```

**Why:** Docking provides an independent structural assessment of whether
representative hits fit the binding pocket. It is **not** a potency predictor --
the docking-potency correlation is typically weak (r ≈ 0.1–0.3 for congeneric
series with narrow potency ranges).

**Vina score parsing:** Vina 1.2.5 has **no `--log` flag**. Parse scores from
stdout by detecting the `mode | affinity` table header and extracting
`mode, affinity, rmsd_lb, rmsd_ub` per line. See
`references/environment_and_troubleshooting.md`.

**Docking-potency correlation reporting:**

- Always compute Pearson r and p-value (scipy.stats.pearsonr)
- Report the correlation with its p-value and an explicit significance statement
- **Never describe r < 0.3 as "moderate" or "good"** — use "weak" or "not significant"
- State the caveat: docking scores are unreliable as absolute affinities (±2–3
  kcal/mol error); weak correlation with experimental potency is expected for
  congeneric series with narrow potency ranges

### 7. Generate figures

```bash
/opt/conda/bin/python scripts/generate_figures.py \
    --data-dir <run_dir>/data \
    --figures-dir <run_dir>/figures
```

Produces 10 figures (PNG; 9 also SVG -- fig8 structure grid is raster-only):

| Figure | Content                                            | Type            |
| ------ | -------------------------------------------------- | --------------- |
| fig1   | pChEMBL distribution + tier distribution           | Data plot       |
| fig2   | Top 20 series ranked by mean hit score             | Data plot       |
| fig3   | Chemical space (MW vs cLogP, colored by pChEMBL)   | Data plot       |
| fig4   | Docking score vs experimental pChEMBL (with r, p)  | Data plot       |
| fig5   | Potency boxplots across top 15 series              | Data plot       |
| fig6   | Property-potency correlation heatmap across series | Data plot       |
| fig7   | Ligand efficiency vs LipE                          | Data plot       |
| fig8   | Top 12 hit structures (molecule grid)              | RDKit rendering |
| fig9   | Series recommendations by potency                  | Data plot       |
| fig10  | Docking score distribution by series               | Data plot       |

After saving each figure, run `Read(file_path=...)` and
regenerate if blank, clipped, or unreadable.

### 8. Generate the interactive HTML data appendix

```bash
/opt/conda/bin/python scripts/generate_html_report.py \
    --data-dir <run_dir>/data \
    --figures-dir <run_dir>/figures \
    --target-name "TYK2" \
    --output /mnt/results/<target>_hit_triage_report.html
```

**Why:** The HTML is the interactive data appendix, not the report. It is self-contained (all
figures embedded as base64 PNG), interactive (sortable/filterable tables with
JavaScript), and navigable (sticky nav bar with section anchors).

**HTML report structure:**

1. Executive overview (KPI cards) + **assay-variant composition table and pooling warning**
2. Potency & hit scoring (fig1, fig3, fig7, **fig8 top-hit structures**)
3. Chemical series (fig2, fig5)
4. SAR analysis (fig6, series cards with recommendations)
5. Structure-based docking (fig4, fig10, docking-potency correlation with caveat, receptor variant)
6. Hit-expansion recommendations (fig9, recommendation cards)
7. Data tables (top 50 hits with **Variant** column, series summary, docking results with **Variant** column — all sortable/filterable)

**Figures in HTML:** All **10** figures (fig1–fig10, including the fig8 top-hit
structure grid) are embedded as base64-encoded PNG images inside `<img>` tags.
This makes the HTML fully self-contained (no external file dependencies) but
increases file size (~1.7 MB for 10 figures). SVG figures are not embedded (they
would require inline SVG, which complicates the template); 9 SVGs are written to
`figures/` (fig8 is raster-only).

### 9. Generate the PDF report (delegated)

**Do not generate the PDF from a script in this package.** Hand report assembly to the
`pdf-report-generation` system skill, which owns Phylo brand conventions and produces a
complete, non-sparse report. A hardcoded report script was removed from this skill because
it emitted only Methods / Results / Conclusions -- no introduction, no literature
grounding, no infographic, no references, no next steps, and no ChEMBL share-alike notice --
which is exactly what the skill review flagged as missing.

**Step 9a -- render citations through the single-source mechanism BEFORE assembling the report
(MANDATORY).** The introduction is grounded with `LiteratureSearch`, whose results carry
session-global `references.jsonl` indices. **Those indices are not the report's citation labels.**
Treating them as labels -- or hand-maintaining a `label_map` and renumbering the bibliography by
hand -- is exactly what shipped a bibliography renumbered to 7 entries while the inline text still
read `[8] [9] [16]` (unresolved) and `[5]`/`[6]` pointed to the wrong papers, all while every gate
passed. Do not do that. Run the packaged transform, which is the single source of truth for
numbering:

```python
import sys; sys.path.insert(0, "scripts")
from render_citations import render_citations, write_citation_map

# cited_records: one dict per cited LiteratureSearch source. `key` = how the draft intro refers to
# it (the references.jsonl index). Each record needs a DOI, a PMID, or first-author + year so its
# identity can be bound; `citation` is the formatted line for the References section.
result = render_citations(intro_draft_text, cited_records)   # raises CitationError, failing closed
intro_final     = result["rewritten_text"]        # hand THIS to pdf-report-generation as the intro
references_block = result["bibliography_text"]     # ... and THIS as the References section, verbatim
write_citation_map("citation_map.json", result)    # the single source the report gate re-checks
```

`render_citations` selects only the cited records, assigns final labels `1..N` **once** (citation
order), rewrites every inline `[N]` through that one map, and builds the bibliography from the same
map -- so the inline text and the References section cannot diverge. It fails closed on a citation
with no source, a source with no resolvable identity, a duplicated identity, or any label that does
not resolve. Pass `intro_final` and `references_block` to the delegated report **unchanged**: do not
re-number, re-order, or re-key them downstream. The `assert_citations_resolve` gate (below) then
re-validates the finished PDF against `citation_map.json`.

Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses

**Brand the infographic explicitly, and fix its geometry.** `pdf-report-generation` governs the
ReportLab document, not the image -- it says nothing about `GenerateImage`, so an undirected prompt
returns a generic blue/teal technical diagram that does not match the report it sits in. Put both
the shape and the palette into the `GenerateImage` prompt itself.

**Geometry -- not optional.** The infographic is a *banner*, not a poster:

- Pass **`aspect_ratio="3:2"`** (1536x1024). Never `2:3` or any portrait ratio. A portrait render
  produces a tall narrow column with dead margins either side of the page; that is a defect, not a
  style choice.
- Lay the content out as a **horizontal, left-to-right flow**. If there are more stages than fit in
  one row, use two rows of equal length -- never a single vertical stack.
- It sits as a **captioned banner** directly under the page-1 title block, at close to full text
  width. Do **not** repeat the report title, subtitle or attribution inside the image -- the page
  already carries them.
- **Check before embedding:** confirm the returned PNG is wider than it is tall. If it came back
  portrait, regenerate with the ratio restated. Place it with the same captioned-figure helper used
  for every other figure so it receives a `Figure N.` caption; an uncaptioned banner is a defect.

**Palette:**

- **Primary accent `#D4A04A` (Phylo gold)** for numbered step markers, box borders, dividers,
  rules and emphasis. Gold is used as line work and small fills, not large blocks.
- **Warm off-white `#F9F7F3`** for panel and callout fills, on a warm off-white `#FAF9F3` ground.
- **Near-black `#111111`** headings, **warm dark `#2C2A26`** body text,
  **warm grey `#8A8378`** for captions and secondary text.
- Additional data colours only from the house sequence, and only when a second colour is needed:
  `#0279EE`, `#75A025`, `#FF9400`, `#FD9BED`.
- Clean sans-serif type, to sit with the report's Helvetica.
- **Do not** use a stock blue/teal diagram palette, gradients, drop shadows, or 3-D effects.

A shipped worked example of this prompt pattern is
`biomni_skills/skills/off-target-safety-pharmacology/references/infographic_prompt.txt`.

**Render the PDF to a workspace file, then publish it to the results root.** The report's final
location is `/mnt/results/report_<subject>.pdf` -- the results root, NOT a run subdirectory, so a
reader opens it without navigating into folders. But the object-backed results mount does not support
ordinary in-place truncation, so render the ReportLab document to a fresh `/workspace` file first and
publish it with `report_qc.staged_copy(...)` (in the gate block below), which copies via the shell
and fails on a 0-byte result. Intermediate data, per-figure PNG/SVG files and the infographic source
may still live in subdirectories; the published PDF may not.

**Wire the gates. This block is mandatory, not illustrative.** A gate is only a gate when something
calls it. Run it immediately after the report is assembled, from the package's `scripts/` directory,
so a wrong-shaped or unbranded report fails the run instead of passing silently:

```python
import sys; sys.path.insert(0, "scripts")
from report_qc import (TRANSCRIPT, assert_citations_resolve, assert_delivery_receipt,
                       assert_docking_box_diagnostics, assert_generated_by_tool,
                       assert_infographic_landscape, assert_output_contract, assert_report_branded,
                       assert_report_exists, staged_copy)

# Both names must match what you actually passed to GenerateImage / wrote the PDF as.
TARGET = "EGFR"                          # the target symbol used in every <target>_* artifact name
INFOGRAPHIC = "workflow_infographic.png"
REPORT = "report_EGFR_hit_triage.pdf"

# Publish the workspace-rendered PDF to the results root: the FUSE-backed mount cannot truncate in
# place, so render to /workspace first, then staged_copy (shell cp; fails on a 0-byte result).
workspace_pdf = "/workspace/report_EGFR_hit_triage.pdf"   # where pdf-report-generation rendered it
staged_copy(workspace_pdf, "report_EGFR_hit_triage.pdf")  # -> /mnt/results/report_EGFR_hit_triage.pdf

# Ordered deliberately: the file-only gates come first so that a gate with an environmental
# dependency can never stop them running.
# Confirm EVERY declared data output actually reached the results root with the declared content
# (variant columns, WT/unspecified composition bucket, docking-box provenance incl. excluded
# additives). This is the fix for "expected output artifacts were not confirmed" -- run it before
# write_facts so a dropped/mis-shaped artifact stops the run instead of shipping a report about it.
print(assert_output_contract(target=TARGET))      # assets/output_contract.json; fails on any gap
print(assert_docking_box_diagnostics())           # F10: derived box permissiveness + surfaced caveat
print(assert_infographic_landscape(INFOGRAPHIC))  # 3:2 landscape, never portrait
assert_report_exists(REPORT)                      # produced, at the RESULTS ROOT, declared name
assert_report_branded(REPORT)                     # palette proves pdf-report-generation was used
print(assert_citations_resolve(REPORT, "citation_map.json"))  # every inline [N] resolves, identity-bound

# Provenance needs the session transcript, which not every runtime writes. Its absence is a
# property of the environment, not a defect in the run -- report it, do not fail on it.
if TRANSCRIPT.exists():
    assert_generated_by_tool(INFOGRAPHIC)         # really from GenerateImage, not matplotlib
else:
    print(f"NOTE: {TRANSCRIPT} absent -- GenerateImage provenance unverifiable on this runtime")

# FINAL finalization (family F04). Run this LAST, after EVERY deliverable is staged -- report, all
# tables, figures/, provenance/, the deterministic-test receipt (test_receipt.json), and the
# per-file manifest (provenance/package_manifest.json). It reconciles the staged result inventory
# against BOTH the declared contract and the packaged delivery receipt
# (assets/output_contract.json -> "delivery_receipt") and fails closed unless every required
# report/table/figure/provenance/test/manifest path resolves. Finalizing the contract only after
# staging is the fix for final_delivery_and_output_manifest_drift: it cannot pass while any
# receipt-named artifact is missing or renamed.
print(assert_delivery_receipt(target=TARGET))
```

If a gate raises, fix the artifact and re-run it. Do not delete the gate, and do not report success
with a gate failing.

This report is **always produced** -- there is no configuration in which it is skipped.

The contract, restated verbatim:

> Use the pdf-report-generation skill to generate a pdf report with infographics (use the Biomni GenerateImage tool), methods, results, conclusions, figures, references, and next steps from all of the analyses

Required content, in addition to the delegation sentence in the report step above:

- **Introduction** grounded via `LiteratureSearch` -- target biology and disease relevance, with
  inline `[N]` citations resolving to the References section. The inline citations and the
  bibliography are produced by the single-source `render_citations` transform (step 9a) from one
  label map -- never a hand-maintained remap of the session-global `references.jsonl` indices
- **Infographic** from the Biomni `GenerateImage` tool -- a **landscape captioned banner
  directly under the page-1 title block**, at close to full text width. NOT a full-page
  image, and never portrait (see the geometry rules in the report step)
- **Scientific caveats**, stated plainly and not softened:
  - docking scores rank poses, they do not predict potency; report the docking-vs-pChEMBL
    correlation honestly even when it is poor or absent
  - the receptor is one structure and one conformational state; state how it was chosen
    (`docking_box.json` -> `pdf_selection`) and whether it was auto-selected
  - series membership is a clustering result at a chosen Tanimoto cutoff, not a chemotype truth
  - the per-series activity-cliff flag is a coarse orientation aid; quantitative cliff and
    transformation statistics belong to `matched-molecular-pairs` (see Boundary above)
- **ChEMBL attribution**: ChEMBL is CC BY-SA 3.0, so the report must carry both an
  attribution line naming ChEMBL and its release, **and** an explicit share-alike notice.
  A licence string in a provenance table is not sufficient -- the review rejected exactly that.
- **References** section built verbatim from `render_citations()` output (step 9a): every entry is
  cited in text, every inline `[N]` resolves to exactly one entry, and each entry is bound to its
  intended source identity. The `assert_citations_resolve` gate enforces this against
  `citation_map.json` and fails the run on any missing, stale, sparse, duplicated, or misbound label
- **Next steps**
- **Run probe appendix**: tool versions, substitutions, data provenance, wall-clock time

The interactive HTML data appendix (step 8) is still built by
`scripts/generate_html_report.py`; only PDF assembly is delegated.

## Decision tables: significant choice points

### ChEMBL target resolution

| Alternatives considered                | Default  | Why                                                                                                             | Switch when                                              |
| -------------------------------------- | -------- | --------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| Search by name/synonym                 | ✓       | Most user-friendly; handles common names                                                                        | User provides a ChEMBL ID directly                       |
| Search by UniProt accession            | Fallback | More precise; avoids homonym confusion (e.g. "TYK2" vs PTK2B)                                                   | Name search returns ambiguous results                    |
| Filter by organism (Homo sapiens)      | ✓       | Most drug discovery targets human proteins                                                                      | User specifies a different organism                      |
| Filter by target_type (SINGLE PROTEIN) | ✓       | Avoids pooling SINGLE PROTEIN with PROTEIN FAMILY or PROTEIN COMPLEX — these have heterogeneous assay contexts | User explicitly wants family-level or complex-level data |

### Potency standardization

| Alternatives considered                 | Default             | Why                                                      | Switch when                                                                                         |
| --------------------------------------- | ------------------- | -------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Compute pChEMBL from standard_value     | ✓                  | Transparent; works for all nM endpoints                  | —                                                                                                  |
| Use ChEMBL's pre-computed pchembl_value | Alternative         | Faster; but not available for all records                | Only if you want to include records without standard_value (not recommended — loses assay context) |
| Include pIC50/pKi/pKd directly          | ✓ (auto-converted) | These are already log-scale; treat as pChEMBL equivalent | —                                                                                                  |

### Docking receptor preparation

| Alternatives considered          | Default                      | Why                                                                                                                                       | Switch when                                   |
| -------------------------------- | ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| Meeko `mk_prepare_receptor.py` | ✓                           | LGPL-2.1 (commercial-use safe); handles PDB input                                                                                         | Meeko unavailable                             |
| ADFR `prepare_receptor`        | Fallback                     | LGPL2 (commercial-use safe — https://ccsb.scripps.edu/adfr/license/, not Apache 2.0); widely used                                        | ADFR unavailable                              |
| Open Babel `obabel -xr -xd`    | **Avoided by default** | GPL-2.0 (copyleft: source-distribution obligation on derivatives, not a commercial-use ban); Meeko/ADFR chosen to sidestep the obligation | Only when Meeko and ADFR are both unavailable |

### Docking score parsing

| Alternatives considered  | Default     | Why                                                               | Switch when                                  |
| ------------------------ | ----------- | ----------------------------------------------------------------- | -------------------------------------------- |
| Parse Vina stdout table  | ✓          | Vina 1.2.5 has no `--log` flag; stdout is the only score output | —                                           |
| Use Vina Python bindings | Alternative | More robust parsing; but requires `uv pip install vina`         | Python bindings are installed and importable |

## Degradation and honesty

### Long-running jobs

- **ChEMBL retrieval:** Paginate in batches of 1,000 with 0.3s delays. For
  targets with >50k records, warn the user and offer to limit to potency-type
  endpoints only (reduces volume ~60%). Timeout: 10 minutes per batch; if a
  batch fails, retry once, then report the failure.
- **Butina clustering:** distances via `BulkTanimotoSimilarity` into a dense NumPy
  matrix — ~6 s / ~1.4 GB at 10k molecules on 1 CPU. The limiter is now O(n²)
  **memory** (~0.9 GB at 10k, ~3.5 GB at 20k, ~14 GB at 40k). For >~25k molecules
  on a 16 GB worker, subsample or switch to Taylor–Butina/sphere-exclusion with
  fingerprint bucketing.
- **Docking:** ~8s per ligand at exhaustiveness=16. For >100 ligands, warn the
  user and offer to reduce `dock_top_per_series` or `dock_top_overall`. Timeout:
  300s per ligand; if a ligand times out, mark it as "timeout" and continue.

### Inputs below the size the method requires

- **<50 unique hits:** The skill runs but adds a caveat to the report: "Small
  dataset (n<50) — series statistics and SAR correlations may be unstable."
- **<5 hits in any series:** Series is labeled "Singleton/Other" and excluded
  from SAR analysis. This is by design, not a failure.
- **0 hits after filtering:** The skill fails loudly: "No hits met the
  inclusion criteria (pChEMBL ≥ {min}, exact measurements). Consider lowering
  pchembl_min or including censored values."

### Missing comparator data

- **No PDB structure available for the target:** The skill reports SAR and
  scoring results without docking. The HTML report states: "No suitable PDB
  structure was identified for {target}. Docking was skipped." The PDF report
  omits the docking section.
- **No co-crystal ligand in any PDB structure:** The skill asks the user to
  supply docking box coordinates. If none are supplied, docking is skipped with
  a clear statement.
- **Docking-potency correlation not significant (p > 0.05):** The report states
  this explicitly and does not claim docking validates the hits. The docking
  section is framed as "structural plausibility assessment" not "potency
  confirmation."

### Conditions under which the skill must decline to claim a result is validated

- **Docking alone never validates a hit.** The report must state: "Docking
  scores assess structural plausibility, not binding affinity. Experimental
  validation is required."
- **SAR based on <10 series members** must be labeled "preliminary" in the
  report.
- **Activity cliff counts based on <20 similar pairs** must be labeled
  "limited statistical power."
- **Any synthesized/simulated input data** must be labeled as such throughout
  the outputs. The skill does not synthesize input data — if the caller supplies
  simulated data, the report must carry a "SIMULATED DATA" banner.

## Data sources and licensing

| Source                              | Used for                                                                                            | License                                                            | Commercial use                          | Obligations                                                                                                                                                                  |
| ----------------------------------- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ | --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **ChEMBL** (EMBL-EBI)         | All bioactivity data, target/molecule metadata                                                      | **CC BY-SA 3.0**                                             | **Yes**                           | **Attribution + share-alike**: derived datasets must be redistributed under the same CC BY-SA 3.0 terms. Cite the ChEMBL release used.                                 |
| **RCSB PDB**                  | Protein structures for docking                                                                      | **CC0 1.0** (public domain)                                  | **Yes**                           | None required (citation appreciated)                                                                                                                                         |
| **RDKit**                     | Cheminformatics (SMILES, fingerprints, clustering, MCS, scaffolds, R-groups, properties, rendering) | **BSD-3-Clause**                                             | **Yes**                           | Attribution                                                                                                                                                                  |
| **AutoDock Vina**             | Molecular docking                                                                                   | **Apache 2.0**                                               | **Yes**                           | Attribution; note license in report                                                                                                                                          |
| **Meeko**                     | Ligand and receptor PDBQT preparation                                                               | **LGPL-2.1**                                                 | **Yes** (dynamic linking)         | Attribution; LGPL allows commercial use with dynamic linking                                                                                                                 |
| **ADFR** `prepare_receptor` | Receptor PDBQT preparation (fallback; exercised on the EGFR run)                                    | **LGPL2** (verified: https://ccsb.scripps.edu/adfr/license/) | **Yes**                           | Used as an unmodified standalone CLI tool; commercial use permitted                                                                                                          |
| **gemmi**                     | Required by Meeko for PDB parsing                                                                   | **MPL-2.0**                                                  | **Yes**                           | Attribution; MPL allows commercial use                                                                                                                                       |
| **Open Babel**                | Receptor PDBQT conversion (last-resort fallback; avoided by default)                                | **GPL-2.0**                                                  | **Yes**, with copyleft obligation | GPL-2.0 copyleft is a source-distribution obligation on derivative works, **not** a commercial-use ban. Avoided by default; use Meeko/ADFR to sidestep the obligation. |
| **ReportLab**                 | PDF generation                                                                                      | **BSD** (ReportLab license)                                  | **Yes**                           | Attribution                                                                                                                                                                  |
| **scipy**                     | Pearson correlation, p-values                                                                       | **BSD-3-Clause**                                             | **Yes**                           | Attribution                                                                                                                                                                  |

**License gate (HOLD — not self-clearing):** Run `scripts/commercial_inventory.py --assert` to
derive the dependency/commercial-use inventory from the declared dependencies **and** the
actually-executed imports/tools, each bound to its own licence page. It fails closed on a licence
claim that conflicts with the authoritative evidence, a material dependency with no evidence URL, or
a self-clear while a hold is open. **It never declares commercial clearance.** The overall status
stays **HOLD**: ChEMBL's CC BY-SA 3.0 share-alike obligation on derived datasets and the open
human-decision commercial findings (possible hard veto / prohibition) require Phylo/legal review.
Open Babel (GPL-2.0, copyleft) is avoided by default; if it is ever used, the report must state the
copyleft source-distribution obligation.

**ChEMBL share-alike obligation:** The redistributed CSVs (hits, docking
results, SAR) are derived from ChEMBL data and inherit the CC BY-SA 3.0
share-alike obligation. The report must include a data-source line attributing
ChEMBL with the release version and URL.

## Environment notes

- **Python interpreter:** Use `/opt/conda/bin/python` for all script
  invocations. The base image has a `/workspace/.venv` whose `bin/python` can be
  a broken symlink; bare `python` may import from the wrong environment.
- **Install per run:** `uv pip install --python /opt/conda/bin/python vina meeko gemmi`
- **PATH:** `export PATH=/opt/conda/bin:/usr/local/bin:$PATH` so `vina`,
  `prepare_receptor`, and `mk_prepare_receptor.py` resolve.
- **Compute:** No GPU needed. **Clustering is now viable on the default 1-CPU / 16 GB worker.**
  - Butina distances use `DataStructs.BulkTanimotoSimilarity` accumulated into a NumPy matrix
    (`score_and_cluster.bulk_distance_matrix`, Butina matrix path). **Measured on the default
    1-CPU / 16 GB worker** at EGFR scale (10,431 molecules, 54.4M pairs): **~6 s (5.9 s distances
    + 0.1 s Butina), ~1.4 GB peak RSS** — vs the old Python double loop at **~85 s, ~5.1 GB**
      (14x faster distances, 3.7x lower peak). Distances are **bit-identical** (max abs diff 0.0;
      full-vector SHA256 matches) and clusters are identical (1888). `scripts/benchmark_clustering.py`
      reproduces these numbers.
  - The remaining wall-clock at a well-studied target is **ChEMBL retrieval** (~8–10 min for
    58,847 EGFR records) and **docking** (~8 min per ~50 ligands; Vina is multi-threaded, so a
    multi-core machine speeds docking — but clustering no longer requires it).
  - **Do not background the clustering step.** With auto-progress off, a task can settle while
    waiting on its own callback — this truncated one validation run at step 3 of 9. It now runs in
    seconds; keep it in the foreground.
- See `references/environment_and_troubleshooting.md` for all known quirks and
  fixes.

## Scientific caveats (MUST appear in every report)

1. **Target-variant conflation — reported, warned, and filterable (default still pools).**
   ChEMBL activity for a target pools assays run against the **wild type and against mutants**
   (for EGFR: L858R, T790M, exon-19 deletions, C797S and double/triple mutants). The structured
   fields `assay_variant_mutation` and `assay_variant_accession` are retained end to end. By
   default (`--variant any`) the skill still **aggregates all variants into a single pChEMBL per
   molecule** and docks into a **single** structure — so a compound optimised against a mutant is
   ranked against whichever receptor was docked (e.g. on EGFR, 20,183 scored records span **13
   assay variants**: WT 74.4%, L858R+T790M 9.6%, L858R 4.3%, T790M 1.4%, plus C797S combinations).
   This is **no longer silent**:

   - `process_activities.py` reports the full variant composition (counts + percentages, WT/
     unspecified as its own bucket) to the console and to `variant_composition.json`, and both
     reports render it as a table.
   - A **prominent warning** fires whenever more than one variant is pooled, naming the variants
     and the variant the docked structure represents (`receptor_variant` in `docking_box.json`).
   - The per-compound and docking tables carry the variant each potency came from
     (`variant_best`, plus `variant_summary`/`n_variants`), and `series_stats.csv` reports the
     per-series variant mix so cross-variant merging is visible.

   Consequences that remain and must still be disclosed under the default:

   - Potency tiers mix protein variants and are **not** variant-specific.
   - Docking scores are computed against one conformer of one variant; series that target a
     different variant are scored against the wrong receptor.
   - Series clustering can merge chemotypes developed against different variants.

   When variant resolution matters, restrict scoring with `process_activities.py --variant WT|<MUTATION>` (fails loudly and lists available variants if none match) and dock a
   matching structure with `prepare_docking.py --pdb-id <variant PDB> --receptor-variant <label>`.
2. **ChEMBL bias:** ChEMBL over-represents published active molecules; the hit
   distribution is skewed toward potent compounds and is not a random screen.
3. **pChEMBL standardization:** Different endpoint types (IC50, Ki, Kd) measure
   different things; pooling them into a single pChEMBL is an approximation.
4. **Butina clustering is deterministic but cutoff-dependent:** Different
   cutoffs produce different series; the choice of 0.60 similarity is
   conventional but not universal.
5. **Activity cliffs are pairwise:** They identify sensitive SAR points but do
   not explain the mechanism; follow-up with matched molecular pair analysis is
   recommended.
6. **Docking scores are not binding affinities:** Vina scores carry ±2–3
   kcal/mol error; weak correlation with experimental potency is expected and
   does not invalidate the docking assessment.
7. **Single receptor conformation and box permissiveness:** No induced fit or side-chain
   flexibility; docking assesses fit to one static structure. When the box is oversized/permissive
   (derived by `box_diagnostics` into `docking_box.json`; enforced by
   `assert_docking_box_diagnostics`), the search scores poses across a wide region rather than one
   focused pocket — a driver of weak docking-vs-potency correlation, and a signal the box may be
   defined by a co-crystal ligand whose binding mode/conformation differs from the docked ligand
   class. Report the derived caveat and state the receptor conformation and binding-site relevance.
8. **MCS may fail for diverse series:** If `rdFMCS.FindMCS` times out or returns
   no common substructure, Murcko scaffolds provide a fallback. R-group
   decomposition requires MCS success.
9. **Series recommendations are heuristics:** The EXPAND/EXPLORE/MONITOR/DROP
   labels are based on potency, series size, and ligand efficiency thresholds
   that are conventional but not universally validated.

## Error handling

- **Target not found in ChEMBL:** Report it; list candidates considered; suggest
  searching by UniProt accession. Do not guess.
- **No bioactivity records after filtering:** Report the filter criteria and the
  raw record count; suggest relaxing filters (lower pchembl_min, include bounds).
- **No PDB structure with co-crystal ligand:** Report it; suggest user-supplied
  box coordinates; skip docking with a clear statement.
- **MCS failure:** Log the error; fall back to Murcko scaffold; continue SAR
  analysis without R-group decomposition. Do not silently skip the series.
- **Docking timeout:** Mark the ligand as "timeout"; continue with remaining
  ligands; report the failure count.
- **ChEMBL API unreachable:** Retry once after 30s; if still down, report the
  failure rather than fabricating data.

## Runnable example

See `references/runnable_example.md` for a complete end-to-end example using
TYK2 (CHEMBL3553) as the target, including expected outputs and validation
checks. The example uses real ChEMBL data and a real PDB structure (4GIH) — no
simulated inputs.

**Expected outputs for TYK2 example:**

- ~6,160 unique hits after filtering
- ~215 chemical series (≥5 members)
- 20 series with full SAR
- 51 ligands docked
- 10 figures
- Interactive HTML report (~2.2 MB)
- PDF report (~14 pages)

**Validation checks:**

- Hits CSV: ≥1,000 rows, no NaN in best_pchembl/smiles/series
- Docking CSV: all ligands have a numeric docking_score or status="timeout"
- SAR JSON: each series has n_members, pchembl_median, recommendation
- HTML report: opens in browser, tables are sortable, figures render
- PDF report: ≥2 pages, extractable text, figures embedded
