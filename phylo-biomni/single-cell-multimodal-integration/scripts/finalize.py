#!/usr/bin/env python3
"""
finalize.py — stage run outputs to the results root, write the figure manifest
and the facts runtime payload.

Copies figures and deliverable tables from the working dir to the results root,
writes figures/manifest.json (one entry per result-producing step, with the
caption the report reads), and aggregates the per-step facts fragments into
facts_payload.json (headline + cell-accounting partition fields). The branded
PDF, infographic lineage, and receipt are produced by the agent terminal step.

Usage: python finalize.py --config config.yaml
"""
import argparse, csv, glob, json, os, shutil, subprocess

from report_language import adt_figure_caption, validate_adt_normalization_status

ap = argparse.ArgumentParser(); ap.add_argument("--config", required=True)
CFG = __import__("yaml").safe_load(open(ap.parse_args().config))
OUT = CFG["project"]["outdir"]; RES = CFG["project"]["results_dir"]
os.makedirs(os.path.join(RES, "figures"), exist_ok=True)

CAPTIONS = {
    3: ("figure_3_qc_each_modality.png",
        "Per-modality QC after filtering: RNA counts, genes per cell, and percent mitochondrial, "
        "plus ADT total protein counts, with the retained-cell thresholds marked."),
    4: ("figure_4_denoise_antibody_derived.png", None),
    5: ("figure_5_build_the_wnn.png",
        "WNN joint UMAP colored by joint cluster, alongside the per-cell RNA modality-weight "
        "distribution with weights shown to lie in the closed unit interval [0,1]."),
    6: ("figure_6_compute_the_totalvi.png",
        "Joint integration metrics (cluster silhouette, batch-mixing entropy, and label purity scored "
        "only against an independent reference label — otherwise flagged not-evaluable), beside the "
        "annotation-vs-reference confusion that exposes any novel/spurious label and cluster split/merge."),
    7: ("figure_7_annotate_cells_and.png",
        "Per-cluster surface-protein (ADT) signal, row z-scored, with each cluster's supported cell "
        "type or explicit unassigned state and a confidence/margin flag."),
    8: ("figure_8_test_replicate_aware.png",
        "Milo neighborhood differential-abundance across conditions with spatial-FDR significance, "
        "or the insufficient-replication skip flag when fewer than two replicates per condition."),
}


def main():
    # 1) copy figures to the results root
    for f in glob.glob(os.path.join(OUT, "figures", "*")):
        shutil.copy(f, os.path.join(RES, "figures", os.path.basename(f)))

    # 2) figure manifest (results-root-relative paths)
    adt_status_path = os.path.join(OUT, "tables", "adt_normalization_status.json")
    if not os.path.isfile(adt_status_path):
        raise FileNotFoundError("adt_normalization_status.json is required before figure finalization")
    with open(adt_status_path) as handle:
        adt_status = json.load(handle)
    validate_adt_normalization_status(adt_status)
    captions = dict(CAPTIONS)
    captions[4] = (captions[4][0], adt_figure_caption(adt_status))

    manifest = []
    for step in sorted(captions):
        fn, cap = captions[step]
        p = os.path.join(RES, "figures", fn)
        if os.path.exists(p) and os.path.getsize(p) >= 5000:
            manifest.append({"step": step, "file": f"figures/{fn}", "caption": cap})
        else:
            manifest.append({"step": step, "file": None,
                             "reason": f"step {step} produced no figure at {fn}"})
    json.dump(manifest, open(os.path.join(RES, "figures", "manifest.json"), "w"), indent=2)

    # 3) facts payload (aggregate per-step fragments -> headline + partition)
    frags = {}
    for name in ("facts_01.json", "facts_02.json", "facts_04.json"):
        p = os.path.join(OUT, "checkpoints", name)
        if os.path.exists(p):
            frags.update(json.load(open(p)))
    payload = {k: frags[k] for k in (
        "n_input_cells", "n_joint_cells", "n_cells_dropped_qc", "n_cells_unpaired",
        "n_joint_clusters", "median_rna_modality_weight", "n_adt_features") if k in frags}
    # Execution mode is sealed before the scientific route begins.
    payload["execution_mode"] = (CFG.get("input") or {}).get("execution_mode", "analysis")
    _in_dir = CFG.get("input", {}).get("dir", ".")
    if not os.path.isabs(_in_dir) and not os.path.exists(_in_dir):
        _in_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), _in_dir)
    _meta_path = os.path.join(_in_dir, "metadata.csv")
    if os.path.exists(_meta_path):
        with open(_meta_path) as _fh:
            _reader = csv.DictReader(_fh)
            _cols = _reader.fieldnames or []
            _sample_col = next((c for c in ("sample", "donor", "replicate") if c in _cols), None)
            payload["n_samples"] = len({row[_sample_col] for row in _reader if row.get(_sample_col)}) if _sample_col else 1
    else:
        payload["n_samples"] = 1
    json.dump(payload, open(os.path.join(RES, "facts_payload.json"), "w"), indent=2, sort_keys=True)

    # 4) stage deliverable tables + status jsons to the results root
    for fn in ("joint_qc_scorecard.csv", "joint_cluster_celltype_assignment.csv",
               "rna_markers.csv", "protein_markers.csv", "modality_weights_summary.csv",
               "milo_da_neighborhoods.csv", "adt_normalization_status.json",
               "protein_marker_status.json",
               "totalvi_status.json", "diffabund_status.json",
               "label_purity_status.json", "annotation_concordance.csv"):
        src = os.path.join(OUT, "tables", fn)
        if os.path.exists(src):
            subprocess.run(["cp", src, os.path.join(RES, fn)], check=True)

    print("finalize: staged figures/tables, wrote figures/manifest.json + facts_payload.json")
    print("facts_payload:", json.dumps(payload))


if __name__ == "__main__":
    main()
