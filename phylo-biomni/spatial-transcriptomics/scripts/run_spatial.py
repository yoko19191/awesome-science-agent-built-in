#!/usr/bin/env python3
"""
============================================================================
PACKAGED SCIENCE AND FIGURE-PREPARATION ENTRYPOINT
============================================================================

ONE packaged entrypoint for the complete scientific route, so a run never needs an
unrecorded custom driver (Finding 2). It uses only packaged modules:

    route (platform_router) -> platform-specific QC branch (spatial_workflow /
    xenium_workflow) -> standardize (spatial_common) -> shared downstream
    (shared_downstream) -> DERIVED provenance (spatial_provenance) -> facts
    payload -> results-root outputs manifest (outputs_manifest).

Every reported QC threshold is copied from the exact applied parameter object and
cross-checked before export (Finding 3). The authoritative SVG headline column is
carried into the facts payload (Finding 4). Provenance records the platform (so a
source assertion is evaluated platform-conditionally, Finding 1), the executed
packaged-module hashes (Finding 2) and the executed third-party imports
(Finding 6). A two-platform run is bound by a results-root manifest (Finding 5).

Usage
-----
    # scientific results + four required figures and a conditional DE volcano
    python scripts/run_spatial.py prepare <bundle_dir> <out_dir>
                                          [--platform auto|visium|xenium]
                                          [--min-genes N] [--min-transcripts N]

    # the DATA route only (no figures/PDF)
    python scripts/run_spatial.py route <bundle_dir> <out_dir> [...same flags...]

    # results-root index binding a two-platform layout (after both routes ran)
    python scripts/run_spatial.py manifest <results_root>
                                        --run Visium=visium_run --run Xenium=xenium_run

The command bound into the run receipt is a ``prepare`` invocation. Presentation is a separate,
provider-conditional step in ``finalize_report.py``. ``all`` remains a compatibility alias for
``prepare`` but no longer accepts report arguments. ``route`` remains available for deterministic
data-only evals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import numpy as np
import pandas as pd

import outputs_manifest
import spatial_provenance
import input_selection
from platform_router import route_platform
from spatial_common import standardize_spatial_object


DATA_OUTPUTS = (
    "platform_decision.json", "qc.csv", "niches.csv", "spatially_variable_genes.csv",
    "differential_expression.csv", "cci.csv", "provenance.json", "adata_processed.h5ad",
    "facts_payload.json",
)

# The four figures produced for every completed route plus their manifest. Replicate-aware DE adds
# a fifth figure only when the contrast is estimable; blocked DE is recorded as a reason in the
# manifest instead of a placeholder image.
FIGURE_OUTPUTS = (
    "figures/figure_3_run_the_platform.png",
    "figures/figure_4_standardize_to_the.png",
    "figures/figure_5_detect_spatially_variable.png",
    "figures/figure_7_infer_hypothesis_level.png",
    "figures/manifest.json",
)
# Presentation-neutral figure lineage. The finalizer verifies this manifest before it accepts either
# renderer branch, then writes the separate final ``visual_manifest.json`` that also pins the PDF.
SCIENTIFIC_VISUAL_MANIFEST = "scientific_visual_manifest.json"
COMMAND_OUTPUTS = tuple(DATA_OUTPUTS) + FIGURE_OUTPUTS + (SCIENTIFIC_VISUAL_MANIFEST,)


def _write_h5ad(adata, dest: Path) -> None:
    """Write .h5ad via a local temp file, then copy to dest (results mount is object-backed)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="spatial-h5ad-") as tmp:
        scratch = Path(tmp) / "adata_processed.h5ad"
        save = adata.copy()
        _clean_uns(save)
        save.write_h5ad(scratch, compression="gzip")
        shutil.copyfile(scratch, dest)


def _clean_uns(adata) -> None:
    for key in list(adata.uns.keys()):
        val = adata.uns[key]
        if isinstance(val, (np.integer,)):
            adata.uns[key] = int(val)
        elif isinstance(val, (np.floating,)):
            adata.uns[key] = float(val)


def _attach_samples(adata, bundle: Path, sample_key: str, condition_key: str) -> None:
    """Attach samples.csv identity to obs when the branch has not already done so.

    A single Visium section is one biological sample: the single samples.csv row is applied to every
    spot. When obs already carries per-observation sample/condition (Xenium cells table), nothing is
    overwritten.
    """
    samples = bundle / "samples.csv"
    if sample_key in adata.obs and condition_key in adata.obs:
        return
    if not samples.is_file():
        return
    df = pd.read_csv(samples)
    if df.empty:
        return
    if len(df) == 1:
        if sample_key not in adata.obs and sample_key in df.columns:
            adata.obs[sample_key] = str(df[sample_key].iloc[0])
        if condition_key not in adata.obs and condition_key in df.columns:
            adata.obs[condition_key] = str(df[condition_key].iloc[0])


def _run_visium(bundle: Path, out: Path, *, min_genes: int, min_cells: int, max_pct_mito: float,
                n_top_genes: int, n_pcs: int, svgs_n_perms: int, sample_key: str,
                condition_key: str):
    from spatial_input import load_visium_data
    from spatial_workflow import run_spatial_analysis, compute_visium_qc, visium_provenance_params

    decision = route_platform(str(bundle), output_dir=str(out))
    load_path = decision.get("load", {}).get("path")
    adata = load_visium_data(load_path if load_path else str(bundle))
    _attach_samples(adata, bundle, sample_key, condition_key)

    # Real per-spot QC (pre-filter) -> qc.csv with a genuine pass/fail flag.
    qc = compute_visium_qc(adata, min_genes=min_genes, max_pct_mito=max_pct_mito)
    mitochondrial_qc = dict(qc.attrs["mitochondrial_qc"])
    qc["platform"] = "Visium"
    qc["mitochondrial_matching_rule"] = mitochondrial_qc["matching_rule"]
    qc["mitochondrial_gene_name_source"] = mitochondrial_qc["gene_name_source"]
    qc["mitochondrial_matched_prefixes"] = "|".join(mitochondrial_qc["matched_prefixes"])
    qc["n_mito_features"] = mitochondrial_qc["n_mito_features"]
    qc["n_spots_with_mito_counts"] = mitochondrial_qc["n_spots_with_mito_counts"]
    out.mkdir(parents=True, exist_ok=True)
    qc.to_csv(out / "qc.csv", index=False)
    n_total = int(len(qc))
    n_pass = int(qc["passes_filter"].sum())

    adata = run_spatial_analysis(adata, min_genes=min_genes, min_cells=min_cells,
                                 max_pct_mito=max_pct_mito, n_top_genes=n_top_genes, n_pcs=n_pcs,
                                 svgs_n_perms=svgs_n_perms, output_dir=str(out))
    if adata.uns.get("mitochondrial_qc") != mitochondrial_qc:
        raise RuntimeError(
            "pre-filter and workflow mitochondrial feature resolution disagree; aborting export"
        )
    adata.obs["spatial_cluster"] = (
        "cluster_" + adata.obs["leiden"].astype(str)
    ).astype("category")
    adata = standardize_spatial_object(adata, platform="Visium", sample_key=sample_key,
                                       condition_key=condition_key)
    prov_params = visium_provenance_params(adata)
    exec_params = dict(prov_params["thresholds"])
    features = {"n_total": int(adata.n_vars), "n_genes": int(adata.n_vars), "n_control": 0}
    qc_facts = {"n_cells_total": n_total, "n_cells_pass": n_pass,
                "n_cells_filtered": n_total - n_pass}
    thresholds_facts = {"min_genes": exec_params.get("min_genes")}
    return adata, {
        "platform": "Visium", "provenance_thresholds": prov_params["thresholds"],
        "provenance_parameters": prov_params["parameters"], "xenium_block": None,
        "mitochondrial_qc": mitochondrial_qc,
        "exec_params": exec_params, "consistency_keys": ("min_genes",),
        "features": features, "qc_facts": qc_facts, "thresholds_facts": thresholds_facts,
    }


def _run_xenium(bundle: Path, out: Path, *, min_transcripts: int, sample_key: str,
                condition_key: str):
    from xenium_workflow import run_xenium_analysis

    decision = route_platform(str(bundle), output_dir=str(out))
    load = decision.get("load", {})
    adata, qc, seg = run_xenium_analysis(
        load.get("cell_feature_matrix"), load.get("cells"), output_dir=str(out),
        transcripts=load.get("transcripts"), sample_key=sample_key, condition_key=condition_key,
        min_transcripts=min_transcripts,
    )
    # qc.csv already written by run_xenium_qc; add a platform column deterministically.
    qc_path = out / "qc.csv"
    if qc_path.is_file():
        df = pd.read_csv(qc_path)
        if "platform" not in df.columns:
            df["platform"] = "Xenium"
            df.to_csv(qc_path, index=False)
    n_total = int(qc["n_cells_initial"])
    n_pass = int(qc["n_cells_pass"])
    n_control = int(qc.get("n_control_features", 0))
    exec_params = {"min_transcripts": int(qc["min_transcripts"])}
    prov_thresholds = {"min_transcripts": int(qc["min_transcripts"]),
                       "negative_control_rate_max": float(qc["negative_control_rate_max"])}
    xenium_block = {"min_transcript_qv": int(qc["min_transcript_qv"]),
                    "min_transcripts": int(qc["min_transcripts"]),
                    "negative_control_rate": float(qc["negative_control_rate"]),
                    "resegmentation_recommended": bool(seg["resegmentation_recommended"]),
                    "normalization": "library_size_100_log1p"}
    features = {"n_total": int(adata.n_vars) + n_control, "n_genes": int(adata.n_vars),
                "n_control": n_control}
    qc_facts = {"n_cells_total": n_total, "n_cells_pass": n_pass,
                "n_cells_filtered": n_total - n_pass}
    thresholds_facts = {"min_transcripts": exec_params["min_transcripts"]}
    return adata, {
        "platform": "Xenium", "provenance_thresholds": prov_thresholds,
        "provenance_parameters": exec_params, "xenium_block": xenium_block,
        "mitochondrial_qc": None,
        "exec_params": exec_params, "consistency_keys": ("min_transcripts",),
        "features": features, "qc_facts": qc_facts, "thresholds_facts": thresholds_facts,
    }


def run_route(bundle_dir, out_dir, *, platform: str = "auto", min_genes: int = 200,
              min_cells: int = 10, max_pct_mito: float = 50.0, n_top_genes: int = 2000,
              n_pcs: int = 30, svgs_n_perms: int = 100, min_transcripts: int = 10,
              sample_key: str = "sample_id", condition_key: str = "condition") -> dict:
    """Run the complete packaged route for one bundle; write every data output. Return a summary."""
    from shared_downstream import adjacency_spec, run_shared_downstream

    bundle = Path(bundle_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    decision = route_platform(str(bundle), output_dir=str(out),
                              manifest_platform=None if platform == "auto" else platform)
    if decision.get("status") != "routed":
        return {"status": "stop", "reason": decision.get("reason"), "platform": None,
                "outputs": ["platform_decision.json"]}
    resolved = decision["platform"]

    # Finding 2 (v5): a stage that removes every spot/cell stops with a structured, actionable
    # not_computable result instead of an opaque empty-matrix/scikit-learn error downstream.
    from spatial_workflow import SpatialNotComputable
    try:
        if resolved == "Visium":
            adata, meta = _run_visium(bundle, out, min_genes=min_genes, min_cells=min_cells,
                                      max_pct_mito=max_pct_mito, n_top_genes=n_top_genes, n_pcs=n_pcs,
                                      svgs_n_perms=svgs_n_perms, sample_key=sample_key,
                                      condition_key=condition_key)
        else:
            adata, meta = _run_xenium(bundle, out, min_transcripts=min_transcripts,
                                      sample_key=sample_key, condition_key=condition_key)
    except SpatialNotComputable as exc:
        payload = exc.as_dict(platform=resolved)
        (out / "not_computable.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        outputs = ["platform_decision.json", "not_computable.json"]
        if (out / "qc.csv").is_file():
            outputs.insert(1, "qc.csv")
        return {"status": "not_computable", **payload, "outputs": outputs}

    adjacency_key, adjacency_label = adjacency_spec(resolved)
    adata.uns["adjacency_relationship_label"] = adjacency_label
    shared = run_shared_downstream(adata, output_dir=str(out), cluster_key=adjacency_key,
                                   sample_key=sample_key, condition_key=condition_key,
                                   n_perms=svgs_n_perms)
    de_status = shared.get("de_status", {})
    de_n_sig = int(de_status.get("n_sig", 0)) if de_status.get("run") else 0
    svg_sig = shared.get("svg_significance", {})

    facts = {
        "platform": meta["platform"],
        "data_origin": "caller_selected",
        "n_cells_or_spots": int(meta["qc_facts"]["n_cells_pass"]),
        "n_svg_sig": int(shared.get("n_svg_sig", 0)),
        "n_niches": int(shared.get("n_niches", 0)),
        "niche_selection": shared.get("niche_selection", {}),
        "de_n_sig": de_n_sig,
        "de_blocked": not bool(de_status.get("run", False)),
        "qc": meta["qc_facts"],
        "features": meta["features"],
        "thresholds": meta["thresholds_facts"],
        "svg_significance": svg_sig,
        "adjacency_relationship_label": shared.get("adjacency_relationship_label"),
        "adjacency_label_basis": shared.get("adjacency_label_basis"),
    }
    if meta["platform"] == "Visium":
        facts["mitochondrial_qc"] = meta["mitochondrial_qc"]

    provenance = spatial_provenance.build_provenance(
        meta["platform"], thresholds=meta["provenance_thresholds"],
        parameters=meta["provenance_parameters"], xenium=meta["xenium_block"],
        svg_significance=svg_sig,
        extra={"mitochondrial_qc": meta["mitochondrial_qc"]}
        if meta["platform"] == "Visium" else None,
    )
    # Finding 3: refuse to emit when the applied threshold disagrees across sources.
    spatial_provenance.assert_threshold_consistency(
        meta["exec_params"], provenance, facts, keys=meta["consistency_keys"])
    spatial_provenance.write_provenance(out / "provenance.json", provenance)

    (out / "facts_payload.json").write_text(
        json.dumps(facts, indent=2, sort_keys=True), encoding="utf-8")
    _write_h5ad(adata, out / "adata_processed.h5ad")

    summary = {"status": "routed", "platform": meta["platform"], "out_dir": str(out),
               "facts": facts, "de_status": de_status, "svg_significance": svg_sig,
               "outputs": list(DATA_OUTPUTS)}
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True),
                                          encoding="utf-8")
    return summary


def run_prepare(bundle_dir, out_dir, **kw) -> dict:
    """Run scientific computation and render the applicable data-bearing figures.

    This phase is presentation-neutral. It records matplotlib and ``spatial_figures.py`` in the
    scientific provenance and writes ``scientific_visual_manifest.json``. It never imports a PDF
    renderer or chooses a report-style provider.
    """
    import spatial_figures

    summary = run_route(bundle_dir, out_dir, **kw)
    if summary.get("status") != "routed":
        return summary

    out = Path(out_dir)
    figures = spatial_figures.generate_report_figures(out_dir)

    def _sha(p) -> str:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()

    vis_artifacts = [
        {"path": e["file"], "sha256": _sha(out / e["file"]), "role": "figure", "step": e["step"]}
        for e in figures if e.get("file")
    ]
    (out / SCIENTIFIC_VISUAL_MANIFEST).write_text(
        json.dumps(
            {
                "schema": "spatial-scientific-visual-manifest/1",
                "generated_by": "run_spatial.run_prepare",
                "artifacts": vis_artifacts,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    summary["figures"] = figures
    optional_figures = [
        e["file"] for e in figures
        if e.get("file") and e["file"] not in FIGURE_OUTPUTS
    ]
    summary["outputs"] = [*COMMAND_OUTPUTS, *optional_figures]
    (out / "run_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def run_all(bundle_dir, out_dir, *, report_out=None, infographic=None, **kw) -> dict:
    """Compatibility alias for the presentation-neutral preparation phase."""
    if report_out is not None or infographic is not None:
        raise ValueError(
            "run_spatial.py no longer renders reports; use report_content.py and "
            "finalize_report.py after scientific preparation"
        )
    return run_prepare(bundle_dir, out_dir, **kw)


def emit_manifest(results_root, platform_subdirs: dict) -> dict:
    """Write the results-root outputs manifest for a two-platform layout."""
    return outputs_manifest.build_outputs_manifest(results_root, platform_subdirs)


def _parse_runs(pairs):
    out = {}
    for item in pairs or []:
        if "=" not in item:
            raise SystemExit(f"--run expects Platform=subdir, got {item!r}")
        platform, subdir = item.split("=", 1)
        out[platform.strip()] = subdir.strip()
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Packaged spatial-transcriptomics orchestration")
    sub = parser.add_subparsers(dest="command", required=True)

    def _add_route_args(p):
        p.add_argument("--execution-mode", choices=["analysis"], required=True)
        p.add_argument("--requested-dataset", required=True)
        p.add_argument("--selection-evidence", required=True)
        p.add_argument("bundle_dir")
        p.add_argument("out_dir")
        p.add_argument("--input-origin", choices=sorted(input_selection.ORIGINS), required=True)
        p.add_argument("--input-source", required=True)
        p.add_argument("--input-acquisition-json")
        p.add_argument("--platform", default="auto", choices=["auto", "visium", "xenium"])
        p.add_argument("--min-genes", type=int, default=200)
        p.add_argument("--min-cells", type=int, default=10)
        p.add_argument("--max-pct-mito", type=float, default=50.0)
        p.add_argument("--n-top-genes", type=int, default=2000)
        p.add_argument("--n-pcs", type=int, default=30)
        p.add_argument("--svgs-n-perms", type=int, default=100)
        p.add_argument("--min-transcripts", type=int, default=10)

    _add_route_args(sub.add_parser("route", help="run the data route for one bundle"))
    _add_route_args(sub.add_parser(
        "prepare", help="run scientific computation and the applicable data-bearing figures"))
    _add_route_args(sub.add_parser(
        "all", help="compatibility alias for the presentation-neutral prepare phase"))

    m = sub.add_parser("manifest", help="write the results-root two-platform index")
    m.add_argument("results_root")
    m.add_argument("--run", action="append", metavar="PLATFORM=SUBDIR")

    args = parser.parse_args(argv)
    if args.command in ("route", "prepare", "all"):
        selected = input_selection.select_input(
            role="spatial_bundle", path=args.bundle_dir, origin=args.input_origin,
            requested_source=args.input_source,
            acquisition=input_selection.parse_acquisition(
                args.input_acquisition_json, args.input_origin),
        )
        selection = input_selection.seal_manifest(
            requested_dataset=args.requested_dataset, inputs=[selected],
            result_root=args.out_dir, selection_evidence=args.selection_evidence,
        )
        input_selection.verify_manifest(Path(args.out_dir) / "input_selection.json")
        common = dict(platform=args.platform, min_genes=args.min_genes, min_cells=args.min_cells,
                      max_pct_mito=args.max_pct_mito, n_top_genes=args.n_top_genes, n_pcs=args.n_pcs,
                      svgs_n_perms=args.svgs_n_perms, min_transcripts=args.min_transcripts)
        if args.command in ("prepare", "all"):
            summary = run_prepare(args.bundle_dir, args.out_dir, **common)
        else:
            summary = run_route(args.bundle_dir, args.out_dir, **common)
        (Path(args.out_dir) / "analysis_provenance.json").write_text(json.dumps({
            "execution_mode": "analysis",
            "input_selection_manifest_sha256": selection["manifest_sha256"],
            "package_data_used": False,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2, sort_keys=True))
        # not_computable is a controlled, structured stop (like an ambiguous-bundle stop), not a crash.
        return 0 if summary["status"] in ("routed", "stop", "not_computable") else 1
    if args.command == "manifest":
        manifest = emit_manifest(args.results_root, _parse_runs(args.run))
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
