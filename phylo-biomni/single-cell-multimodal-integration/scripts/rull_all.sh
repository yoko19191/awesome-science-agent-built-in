#!/usr/bin/env bash
# ============================================================================
# run_all.sh — one-shot driver. Establishes the design regime FIRST (00_route.R),
# then runs the branch it selects. Genuinely paired RNA+ATAC exits 3 (route to
# scatac-multiome-analysis); unsupported bundles exit 2 (stop). Deliverables land at the results root.
#   bash run_all.sh [config.yaml]
# ============================================================================
set -euo pipefail
CONFIG="${1:-$(dirname "$0")/config.yaml}"
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$CONFIG" ] || { echo "Config not found: $CONFIG (copy config.example.yaml)"; exit 1; }
[ -n "${SCMI_INPUT_SELECTION:-}" ] || { echo "SCMI_INPUT_SELECTION is required"; exit 1; }

# conda libstdc++ is needed for pyarrow/anndata in this environment; harmless elsewhere.
for cl in "${CONDA_PREFIX:-}/lib" /opt/conda/lib; do
  [ -d "$cl" ] && export LD_LIBRARY_PATH="$cl:${LD_LIBRARY_PATH:-}" && break
done
export R_LIBS_USER="${R_LIBS_USER:-/workspace/.Rlib}"
export PYTHONDONTWRITEBYTECODE=1
PY="${PYTHON:-python3}"
export PYTHON="$PY"
"$PY" "$HERE/input_selection.py" verify "$SCMI_INPUT_SELECTION" >/dev/null
echo "==> config: $CONFIG | python: $PY"

echo "==================== STAGE 00 route ===================="
Rscript "$HERE/00_route.R" "$CONFIG"          # exits 2 (stop) / 3 (handoff) to halt here

OUTDIR="$("$PY" -c "import yaml;print(yaml.safe_load(open('$CONFIG'))['project']['outdir'])")"
RESULTS="$("$PY" -c "import yaml;print(yaml.safe_load(open('$CONFIG'))['project']['results_dir'])")"
ROUTE="$("$PY" -c "import json;print(json.load(open('$OUTDIR/checkpoints/route_decision.json'))['route'])")"
echo "==> route: $ROUTE"
mkdir -p "$RESULTS"
run() { echo ""; echo "==================== STAGE $1 ===================="; }

case "$ROUTE" in
  citeseq)
    run 01_ingest;        Rscript "$HERE/01_ingest.R"        "$CONFIG"
    run 02_rna_qc;        Rscript "$HERE/02_rna_qc.R"        "$CONFIG"
    run 03_adt_qc_dsb;    Rscript "$HERE/03_adt_qc_dsb.R"    "$CONFIG"
    run 04_wnn_joint;     Rscript "$HERE/04_wnn_joint.R"     "$CONFIG"
    run 05_totalvi;       "$PY"   "$HERE/05_totalvi.py"       --config "$CONFIG"
    run 07_annotate;      Rscript "$HERE/07_annotate_markers.R" "$CONFIG"
    run 06_integrate;     "$PY"   "$HERE/06_integrate_metrics.py" --config "$CONFIG"
    run 08_diff_abund;    Rscript "$HERE/08_diff_abundance.R" "$CONFIG"
    run finalize;         "$PY"   "$HERE/finalize.py"          --config "$CONFIG"
    ;;
  unpaired)
    run 09_unpaired_glue; "$PY" "$HERE/09_unpaired_glue.py" --config "$CONFIG"
    [ -f "$OUTDIR/tables/joint_cluster_celltype_assignment.csv" ] && cp "$OUTDIR/tables/joint_cluster_celltype_assignment.csv" "$RESULTS/" || true
    [ -f "$OUTDIR/tables/glue_status.json" ] && cp "$OUTDIR/tables/glue_status.json" "$RESULTS/" || true
    ;;
  mosaic)
    run 10_mosaic_stabmap; Rscript "$HERE/10_mosaic_stabmap.R" "$CONFIG"
    [ -f "$OUTDIR/tables/joint_cluster_celltype_assignment.csv" ] && cp "$OUTDIR/tables/joint_cluster_celltype_assignment.csv" "$RESULTS/" || true
    [ -f "$OUTDIR/tables/stabmap_status.json" ] && cp "$OUTDIR/tables/stabmap_status.json" "$RESULTS/" || true
    ;;
  *) echo "Unexpected route '$ROUTE'; see $OUTDIR/checkpoints/route_decision.json"; exit 2 ;;
esac

echo ""; echo "==================== REPORT (terminal step) ===================="
echo "Scientific pipeline complete. The package-owned run_once.py caller will validate facts and seal"
echo "these artifacts before the infographic/PDF terminal phase. Do not delete or rerun outputs."
echo "==> route=$ROUTE. Scientific deliverables in $RESULTS."
