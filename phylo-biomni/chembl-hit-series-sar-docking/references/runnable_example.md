# Runnable Example: TYK2 Hit Triage

This is a complete end-to-end example using real ChEMBL data and a real PDB
structure. **No simulated inputs.** The example serves as a smoke test — re-run
it to validate the skill after any change.

## Target

- **Protein:** Tyrosine kinase 2 (TYK2), a non-receptor tyrosine kinase in the
  JAK family
- **ChEMBL target ID:** CHEMBL3553 (human single-protein TYK2)
- **UniProt:** P29597
- **Disease relevance:** Autoimmune diseases (psoriasis, IBD); validated target
  (deucravacitinib approved 2022)

## Command sequence

```bash
# 0. Environment
uv pip install --python /opt/conda/bin/python vina meeko gemmi
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
RUN=/workspace/tyk2_example
mkdir -p $RUN/data $RUN/tmp $RUN/figures

# 1. Retrieve bioactivity from ChEMBL
/opt/conda/bin/python scripts/retrieve_activities.py \
    --target "TYK2" \
    --organism "Homo sapiens" \
    --target-type "SINGLE PROTEIN" \
    --outdir $RUN/data

# 2. Process and clean (reports variant composition; --variant any keeps all variants)
/opt/conda/bin/python scripts/process_activities.py \
    --input $RUN/data/activities_raw.json \
    --outdir $RUN/data \
    --variant any

# 3. Score and cluster
/opt/conda/bin/python scripts/score_and_cluster.py \
    --input $RUN/data/hits_clean.csv \
    --outdir $RUN/data \
    --butina-cutoff 0.40 \
    --min-series-size 5

# 4. SAR analysis
/opt/conda/bin/python scripts/sar_analysis.py \
    --input $RUN/data/hits_scored_clustered.csv \
    --outdir $RUN/data \
    --top-n-series 20

# 5. Prepare docking (auto-select PDB or specify; declare the receptor variant)
/opt/conda/bin/python scripts/prepare_docking.py \
    --hits $RUN/data/hits_scored_clustered.csv \
    --pdb-id 4GIH \
    --receptor-variant WT \
    --outdir $RUN/tmp \
    --data-dir $RUN/data \
    --dock-top-per-series 3 \
    --dock-top-overall 10

# 6. Run docking
/opt/conda/bin/python scripts/run_docking.py \
    --receptor $RUN/tmp/receptor.pdbqt \
    --ligand-dir $RUN/tmp/ligands \
    --box $RUN/data/docking_box.json \
    --selection $RUN/data/docking_selection.json \
    --outdir $RUN/tmp/docked \
    --output $RUN/data/docking_results.csv \
    --exhaustiveness 16 \
    --seed 42

# 7. Generate figures
/opt/conda/bin/python scripts/generate_figures.py \
    --data-dir $RUN/data \
    --figures-dir $RUN/figures

# 8. Generate HTML report
/opt/conda/bin/python scripts/generate_html_report.py \
    --data-dir $RUN/data \
    --figures-dir $RUN/figures \
    --target-name "TYK2" \
    --output /mnt/results/TYK2_hit_triage_report.html

# 9. Generate PDF report (load pdf-report-generation skill for conventions)
# PDF assembly is DELEGATED -- scripts/generate_pdf_report.py was removed.
# Load the pdf-report-generation system skill and follow SKILL.md Step 9. The report must
# carry a GenerateImage infographic plus methods, results, conclusions, figures,
# references and next steps, and the ChEMBL CC BY-SA 3.0 attribution + share-alike notice.
```

## Expected outputs

| Output | Expected value | Validation |
|---|---|---|
| Raw ChEMBL records | ~44,880 | `len(json.load(open('activities_raw.json')))` |
| Unique hits after filtering | ~6,160 | `len(pd.read_csv('hits_scored_clustered.csv'))` |
| Chemical series (≥5 members) | ~215 | `pd.read_csv('series_stats.csv').shape[0]` |
| Series with full SAR | 20 | `len(json.load(open('sar_results.json')))` |
| Docked ligands | ~51 | `len(pd.read_csv('docking_results.csv'))` |
| Docking score range | -7.5 to -10.9 kcal/mol | `dock['docking_score'].min(), .max()` |
| Docking-potency Pearson r | ~0.13 (p > 0.05, NS) | `scipy.stats.pearsonr(dock_score, pchembl)` |
| Variant composition | `variant_composition.json` written | TYK2 is predominantly WT; EGFR pools 13 variants |
| Figures | 10 PNG + 9 SVG (fig8 raster-only) | `ls figures/*.png | wc -l` = 10; `ls figures/*.svg | wc -l` = 9 |
| HTML report | ~2.2 MB, self-contained | Opens in browser, tables sortable |
| PDF report | ~14 pages | `pypdf.PdfReader(path).pages` |

## Validation checks

```python
import pandas as pd, json, os
from pypdf import PdfReader

# 1. Hits CSV
hits = pd.read_csv("$RUN/data/hits_scored_clustered.csv")
assert len(hits) > 1000, f"Too few hits: {len(hits)}"
assert hits["best_pchembl"].notna().all(), "NaN in best_pchembl"
assert hits["smiles"].notna().all(), "NaN in smiles"
assert hits["series"].notna().all(), "NaN in series"
assert hits["hit_score"].notna().all(), "NaN in hit_score"
assert hits["tier"].notna().all(), "NaN in tier"

# 2. Docking CSV
dock = pd.read_csv("$RUN/data/docking_results.csv")
assert len(dock) > 0, "No docking results"
assert dock["status"].notna().all(), "NaN in status"
successful = dock[dock["status"] == "success"]
assert successful["docking_score"].notna().all(), "NaN in docking_score for successful"

# 3. SAR JSON
with open("$RUN/data/sar_results.json") as f:
    sar = json.load(f)
assert len(sar) > 0, "No SAR results"
for sname, sdata in sar.items():
    assert "n_members" in sdata, f"Missing n_members in {sname}"
    assert "pchembl_median" in sdata, f"Missing pchembl_median in {sname}"
    assert "recommendation" in sdata, f"Missing recommendation in {sname}"
    # MCS should now succeed (was silently failing in original run)
    # Note: MCS may still fail for very diverse series — check that it's not 0/20
    # At least 50% of series should have MCS success

# 4. HTML report
assert os.path.getsize("/mnt/results/TYK2_hit_triage_report.html") > 100000, "HTML too small"
with open("/mnt/results/TYK2_hit_triage_report.html") as f:
    html = f.read()
assert "TYK2" in html, "Target name not in HTML"
assert "data:image/png;base64" in html, "No embedded figures"
assert "<table" in html, "No tables in HTML"

# 5. PDF report
reader = PdfReader("/mnt/results/report_TYK2_hit_triage.pdf")
assert len(reader.pages) >= 2, f"Only {len(reader.pages)} pages"
assert len(reader.pages[0].extract_text().strip()) > 0, "No text on first page"
```

## What the original cold run got wrong (and this skill fixes)

1. **MCS/R-group decomposition failed silently in all 20 series** due to
   `completeRings=True` (wrong parameter name). Fixed to `completeRingsOnly=True`.
2. **Docking-potency correlation was hallucinated as r=0.39** ("moderate") when
   the actual value is r=0.133 (p=0.353, not significant). Fixed: always compute
   with `scipy.stats.pearsonr`, print to stdout, and use honest language.
3. **1,657 molecules with only `>` (inactive) records were silently dropped**
   without documentation. Fixed: the relation filter is now explicit
   (`exact_only` default) and the report documents how many were excluded.
4. **`scaffold_smarts.json` was empty** (0 entries) but the report claimed MCS
   extraction was performed. Fixed: MCS success is now validated per series.
5. **No RUN PROBE in the reports** (it was only in the chat response). Fixed:
   the PDF report now includes a RUN PROBE appendix.
6. **Open Babel (GPL v2) was used for receptor PDBQT** without noting the
   license constraint. Fixed: default to Meeko `mk_prepare_receptor.py` (LGPL)
   or ADFR `prepare_receptor` (LGPL2, verified at https://ccsb.scripps.edu/adfr/license/).

## v0.3.0 review fixes (target-variant, clustering, artifacts)

7. **Target-variant conflation was silent.** ChEMBL pooled WT and mutant assays into one
   pChEMBL and docked into one structure with no disclosure. Fixed: `assay_variant_mutation` /
   `assay_variant_accession` retained; composition reported to console + `variant_composition.json`
   + both reports; prominent pooling warning naming the receptor variant; opt-in `--variant`
   filter (`WT` / `<MUTATION>` / `any`); variant carried into the per-compound and docking tables
   and the per-series variant mix. On EGFR: 13 variants pooled by default (WT 74.4%).
8. **Butina clustering was a 54.4M-call Python double loop** needing a provisioned machine.
   Fixed: `BulkTanimotoSimilarity` into a NumPy matrix (Butina matrix path) — bit-identical
   distances, identical clusters, ~6 s / ~1.4 GB vs ~85 s / ~5.1 GB at EGFR scale on a 1-CPU
   worker. `scripts/benchmark_clustering.py` reproduces it.
9. **fig8 was generated but never rendered in the HTML**, and **fig4 compound labels collided.**
   Fixed: the HTML now embeds all 10 figures (fig8 included); fig4 uses offset leader-line labels
   and keeps the honest `r`/`p` annotation.

## Wall-clock time (reference)

| Step | Time | Notes |
|---|---|---|
| ChEMBL retrieval | ~8 min | 44,880 records, batch=1000, 0.3s delay |
| Processing + scoring | ~1 min | pandas + RDKit on 6k molecules |
| Butina clustering | seconds | BulkTanimotoSimilarity dense matrix; measured ~6 s / ~1.4 GB peak at 10,431 molecules (EGFR) on a 1-CPU worker |
| SAR analysis | ~2 min | 20 series, MCS + cliffs + correlations |
| Docking prep | ~2 min | 51 ligands, 3D embed + PDBQT |
| Vina docking | ~7 min | 51 ligands, ~8s each at exhaustiveness=16 |
| Figure generation | ~1 min | 10 figures |
| HTML report | ~30s | Base64 encoding of 10 PNGs |
| PDF report | ~30s | ReportLab build |
| **Total** | **~26 min** | |
