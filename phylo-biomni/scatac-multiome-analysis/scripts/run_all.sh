#!/usr/bin/env bash
# One-shot driver. Runs the router FIRST, then the branch it selects.
#   bash run_all.sh [config.yaml]
# Exit 2/3 from stage 00 means STOP / HANDOFF (see route_decision.json) — the
# bundle is not a paired 10x RNA-ATAC multiome and nothing downstream runs.
set -euo pipefail
CONFIG="${1:-$(dirname "$0")/config.yaml}"
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ ! -f "$CONFIG" ]; then
  echo "Config not found: $CONFIG"; echo "Copy scripts/config.example.yaml to config.yaml first."; exit 1
fi
echo "==> Using config: $CONFIG"
if [ -z "${BIOMNI_INPUT_SELECTION_MANIFEST:-}" ]; then
  echo "Input selection is not sealed; launch through run_sealed_analysis.py"; exit 2
fi
python3 "$HERE/verify_analysis_selection.py" \
  --manifest "$BIOMNI_INPUT_SELECTION_MANIFEST" --config "$CONFIG"

# Dependency installation is a setup step, never an in-run retry. First check
# only route-discovery dependencies; the resolved branch is checked below.
echo "==================== ROUTE DEPENDENCY PREFLIGHT ===================="
Rscript "$HERE/preflight_dependencies.R" "$CONFIG" route_discovery

# ---- Stage 00: establish the regime / route (halts on stop/handoff) ----
echo "==================== STAGE 00 route ===================="
Rscript "$HERE/00_route.R" "$CONFIG"     # non-zero exit halts here on stop/handoff

# ---- read the resolved route ----
OUTDIR="$(python3 -c "import yaml;print(yaml.safe_load(open('$CONFIG'))['project']['outdir'])")"
ROUTE="$(python3 -c "import json;print(json.load(open('$OUTDIR/checkpoints/route_decision.json'))['route'])")"
echo "==> Route: $ROUTE"

echo "==================== SELECTED-BRANCH DEPENDENCY PREFLIGHT ===================="
Rscript "$HERE/preflight_dependencies.R" "$CONFIG" "$ROUTE"

run() { echo ""; echo "==================== STAGE $1 ===================="; }

case "$ROUTE" in
  atac_only|multiome_annotate)
    for s in 01_ingest_qc 02_lsi_cluster 03_recall_peaks 04_annotate 05_diff_access 06_figures_tables; do
      run "$s"; Rscript "$HERE/${s}.R" "$CONFIG"
    done
    ;;
  joint)
    # ATAC backbone (fragments-first, from the multiome bundle's Peaks + fragments)
    for s in 01_ingest_qc 02_lsi_cluster 03_recall_peaks; do run "$s"; Rscript "$HERE/${s}.R" "$CONFIG"; done
    # RNA modality QC + WNN joint embedding
    run 07_rna_qc;     Rscript "$HERE/07_rna_qc.R"     "$CONFIG"
    run 08_wnn_joint;  Rscript "$HERE/08_wnn_joint.R"  "$CONFIG"
    # Python bridge: h5mu + integration metrics + MultiVI sensitivity route
    run 09_integrate_bridge; python3 "$HERE/09_integrate_bridge.py" --config "$CONFIG"
    # Replicate-aware DA/DE (Milo + pseudobulk + preserved ATAC DA)
    run 10_diff_abundance;   Rscript "$HERE/10_diff_abundance.R" "$CONFIG"
    # Shared figures/tables/provenance
    run 06_figures_tables;   Rscript "$HERE/06_figures_tables.R"  "$CONFIG"
    ;;
  *)
    echo "Unexpected route '$ROUTE'; see $OUTDIR/checkpoints/route_decision.json"; exit 2 ;;
esac

# ---- stage declared deliverables to the results root (downstream + run receipts) ----
RESULTS_DIR="$(python3 -c "import yaml;print(yaml.safe_load(open('$CONFIG'))['project']['results_dir'])")"
mkdir -p "$RESULTS_DIR"
for f in joint_cluster_celltype_assignment.csv joint_qc_scorecard.csv modality_weights_summary.csv \
         milo_da_neighborhoods.csv pseudobulk_rna_de_by_celltype.csv top_DA_peaks_per_celltype.csv \
         atac_initial_cluster_summary.json atac_recalled_cluster_summary.json; do
  [ -f "$OUTDIR/tables/$f" ] && cp "$OUTDIR/tables/$f" "$RESULTS_DIR/$f" || true
done
echo "==> Staged declared deliverables to $RESULTS_DIR"

echo ""; echo "==================== REPORT ===================="
echo "Build the branded PDF via the pdf-report-generation skill per SKILL.md's terminal step"
echo "(the packaged make_report.py is a fallback renderer):"
echo "  python3 $HERE/make_report.py --config $CONFIG   # fallback only"
echo ""
echo "==> Pipeline complete. Route=$ROUTE. See results_dir in $CONFIG."
