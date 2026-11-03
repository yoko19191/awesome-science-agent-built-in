#!/usr/bin/env python3
"""DRUG-seq analysis orchestrator for caller-selected counts or FASTQ inputs.

Runs the assay-specific front end and early analysis, composing the bundled scripts. The R steps
(DESeq2 contrasts, ULM activity) are executed through report_qc.run_bundled so the run receipt can
verify them. Produces every results table, the per-step figures + manifest, and the runtime facts
payload (facts_payload.json). The figures/facts/report/receipt terminal steps are driven by SKILL.md.

Modes:
  --mode counts  run on a user counts.tsv + platemap.csv
  --mode fastq   demultiplex barcoded FASTQ to a UMI matrix (front end only; stops for a real aligner)

CLI (counts):
  python3 run_analysis.py --mode counts --counts counts.tsv --platemap platemap.csv \
      --genesets genesets.gmt --outdir /mnt/results [--covariate none|edge]
Writes tables/*, figures/*, figures/manifest.json, facts_payload.json under --outdir. Prints JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
PKG = HERE.parent
sys.path.insert(0, str(HERE))

import pandas as pd  # noqa: E402

import well_qc          # noqa: E402
import design_preflight  # noqa: E402
import dose_response     # noqa: E402
import signatures_moa    # noqa: E402
import figures as figmod  # noqa: E402
import demux_umi          # noqa: E402
import env_provenance     # noqa: E402
import input_selection    # noqa: E402
import scientific_logic   # noqa: E402
from report_qc import positional_report_sentence, run_bundled  # noqa: E402

TIER = "hypothesis-generating"
RSCRIPT = os.environ.get("RSCRIPT_BIN", "Rscript")


def infer_mode(inputs: dict) -> str:
    """Route explicit inputs to counts or barcoded-FASTQ analysis.

    Wells are the unit in every mode; there is no single-cell path here (wells are not cells).
    """
    if inputs.get("counts") and inputs.get("platemap"):
        return "counts"
    fastq_keys = ("fastq_r1", "fastq_r2", "whitelist", "reference")
    if all(inputs.get(k) for k in fastq_keys):
        return "fastq"
    raise ValueError(
        "cannot route inputs: provide counts+platemap (counts mode), a barcoded FASTQ set "
        "(fastq mode: r1,r2,whitelist,reference)"
    )


def _write_payload(outdir: pathlib.Path, payload: dict) -> None:
    (outdir / "facts_payload.json").write_text(json.dumps(payload, indent=2, sort_keys=True),
                                               encoding="utf-8")


def run_counts(counts_path, platemap_path, genesets_path, outdir, covariate="none") -> dict:
    outdir = pathlib.Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    tables = outdir / "tables"
    data_dir = outdir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Resolve control semantics once and give every downstream consumer the same normalized map.
    meta, vehicle_identity = scientific_logic.normalize_control_roles(pd.read_csv(platemap_path))
    analysis_platemap = data_dir / "analysis_platemap.csv"
    meta.to_csv(analysis_platemap, index=False)

    # Step: per-well QC + plate/positional diagnostics + identifiability
    qc = well_qc.run(counts_path, analysis_platemap, str(outdir))
    if qc["assignment_stop"]:
        payload = {"status": "not_computable",
                   "reason": "barcode assignment rate below threshold; reconcile whitelist/plate map",
                   "qc": {"barcode_assignment_rate": qc["barcode_assignment_rate"]}}
        _write_payload(outdir, payload)
        return payload
    if not qc["identifiability"]["identifiable"]:
        payload = {"status": "not_computable",
                   "reason": "design not identifiable: " + qc["identifiability"]["reason"],
                   "design": {"identifiable": False}}
        _write_payload(outdir, payload)
        return payload
    pass_wells = qc["pass_wells"]
    (outdir / "pass_wells.txt").write_text("\n".join(pass_wells) + "\n", encoding="utf-8")

    # Step: inference preflight (replication + identifiability)
    pre = design_preflight.run(analysis_platemap, str(outdir), str(outdir / "pass_wells.txt"))

    # Step: DESeq2 contrasts + null (bundled R, verified by the receipt)
    run_bundled(
        [RSCRIPT, str(PKG / "scripts" / "de_deseq2.R"),
         "--counts", str(counts_path), "--platemap", str(analysis_platemap),
         "--pass-wells", str(outdir / "pass_wells.txt"), "--outdir", str(outdir),
         "--covariate", covariate],
        "scripts/de_deseq2.R",
        ["tables/de_all_contrasts.csv", "tables/de_summary.csv",
         "tables/de_model_specification.csv"],
        invocation_id="de",
    )

    # The negative-control anchor is a delivery gate, not report prose. A run with a missing,
    # duplicated, or over-threshold null stops before pathway, tPOD, figure, or report artifacts.
    de_summary = pd.read_csv(tables / "de_summary.csv")
    null_gate = scientific_logic.gate_null_contrast(de_summary)
    (tables / "null_contrast_gate.json").write_text(
        json.dumps(null_gate, indent=2, sort_keys=True), encoding="utf-8"
    )
    scientific_logic.require_null_contrast_passed(null_gate)

    # Step: ULM pathway/TF activity (bundled R)
    run_bundled(
        [RSCRIPT, str(PKG / "scripts" / "activity_ulm.R"),
         "--de", str(tables / "de_all_contrasts.csv"), "--genesets", str(genesets_path),
         "--outdir", str(outdir)],
        "scripts/activity_ulm.R", ["tables/activity_scores.csv"], invocation_id="activity",
    )

    # Step: dose-response / tPOD
    dr = dose_response.run(tables / "de_all_contrasts.csv", str(outdir))
    # Step: signatures + MOA + connectivity export
    sig = signatures_moa.run(tables / "de_all_contrasts.csv", str(outdir))
    # Step: figures + manifest
    figmod.run(
        counts_path, analysis_platemap, str(outdir),
        positional_confounded=bool(qc["positional"]["positional_confounded"]),
        covariate=covariate,
    )

    # Assemble the facts payload (no "figures" key: the facts step attaches the validated inventory).
    de = pd.read_csv(tables / "de_all_contrasts.csv")
    treated = scientific_logic.treatment_rows(meta)
    treated["condition"] = treated["compound"].astype(str) + "_" + treated["dose_uM"].astype(str)
    n_total = int(treated["condition"].nunique())
    non_null = de_summary[
        de_summary["contrast_family"] == scientific_logic.TREATMENT_CONTRAST_FAMILY
    ]
    n_computed = int(non_null["contrast"].nunique())
    null_n = int(null_gate["n_deg"])
    top_row = non_null.sort_values("n_sig", ascending=False).iloc[0]
    compound_de = de[
        de["compound"].notna()
        & (de["contrast_family"] == scientific_logic.TREATMENT_CONTRAST_FAMILY)
    ].copy()
    compound_de["dose_uM"] = pd.to_numeric(compound_de["dose_uM"], errors="coerce")
    top_doses = compound_de.groupby("compound")["dose_uM"].transform("max")
    top_dose_de = compound_de[compound_de["dose_uM"] == top_doses]
    top_dose_counts = top_dose_de.groupby("contrast")["is_sig"].sum().sort_values(ascending=False)
    top_dose_contrast = str(top_dose_counts.index[0])
    top_dose_rows = top_dose_de[top_dose_de["contrast"] == top_dose_contrast]
    top_dose_n_deg = int(top_dose_counts.iloc[0])
    top_dose_n_tested = int(len(top_dose_rows))
    top_dose_value = float(top_dose_rows["dose_uM"].iloc[0])
    normalized_columns = {str(column).strip().casefold(): column for column in meta.columns}
    viability_columns = [
        normalized_columns[name]
        for name in scientific_logic.VIABILITY_COLUMN_CANDIDATES
        if name in normalized_columns
    ]
    viability_available = any(meta[column].notna().any() for column in viability_columns)
    cytotoxicity_sentence = scientific_logic.cytotoxicity_report_sentence(
        top_dose_contrast, top_dose_n_deg, top_dose_n_tested, viability_available
    )
    finite = {c: v["tpod_uM"] for c, v in dr.items() if v.get("tpod_uM") is not None}
    best_comp = min(finite, key=finite.get) if finite else None
    best_tpod = float(finite[best_comp]) if best_comp else None
    tpod_method = next((v["tpod_method"] for v in dr.values()), "n/a")
    low_expr_hits = int(de["low_expr_hit"].sum()) if "low_expr_hit" in de else 0

    payload = {
        "status": "complete",
        "tier": TIER,
        "vehicle_control": {"identity": vehicle_identity, "role_source": "is_control"},
        "n_compounds": int(treated["compound"].nunique()),
        "well_qc": {
            "n_wells_total": int(qc["n_wells_total"]),
            "n_wells_pass_qc": int(qc["n_wells_pass_qc"]),
            "n_wells_dropped_qc": int(qc["n_wells_dropped_qc"]),
            "dropped_wells": qc["dropped_wells"],
        },
        "qc": {"barcode_assignment_rate": float(qc["barcode_assignment_rate"])},
        "contrasts": {"n_total": n_total, "n_computed": n_computed,
                      "n_skipped": n_total - n_computed},
        "null_contrast": {
            **null_gate,
            "model_specification_artifact": "tables/de_model_specification.csv",
            "gate_artifact": "tables/null_contrast_gate.json",
        },
        "top_contrast": {"name": str(top_row["contrast"]), "n_deg": int(top_row["n_sig"])},
        "top_dose_signature": {
            "name": top_dose_contrast,
            "dose_uM": top_dose_value,
            "n_deg": top_dose_n_deg,
            "n_tested_genes": top_dose_n_tested,
            "viability_measurement_available": viability_available,
        },
        "dose_response": {"best_tpod_uM": best_tpod, "best_tpod_compound": best_comp,
                          "tpod_method": tpod_method},
        "caveats_fired": {
            "positional_confounded": bool(qc["positional"]["positional_confounded"]),
            "low_expression_hits": low_expr_hits,
            "cytotoxicity_general_stress": True,
        },
        "design": {"identifiable": bool(pre["design_identifiable"]),
                   "min_independent_units": int(pre["min_independent_units"]),
                   "all_conditions_sufficient": bool(pre["all_conditions_sufficient"])},
        "panel": {"measured_vs_imputed": "all measured (3' whole-transcriptome); no gene imputation"},
        "connectivity": {"status": sig["connectivity_status"]},
    }
    payload["headline_sentences"] = {
        "qc": (f"{payload['well_qc']['n_wells_pass_qc']} of {payload['well_qc']['n_wells_total']} "
               f"wells passed QC; {payload['well_qc']['n_wells_dropped_qc']} dropped for low "
               f"depth/complexity."),
        "null": (f"The {vehicle_identity} vehicle-vs-vehicle null contrast returned {null_n} DEGs "
                 "and passed the "
                 f"pre-specified maximum of {scientific_logic.MAX_NULL_DEGS}."),
        "top": (f"The strongest contrast, {top_row['contrast']}, has {int(top_row['n_sig'])} DEGs "
                f"at padj<0.05 and |log2FC|>1."),
        "tpod": ((f"Transcriptomic point of departure for {best_comp} is {best_tpod:.3g} uM "
                  f"({tpod_method}).") if best_comp else
                 "No compound produced an estimable lowest-dose-referenced signed tPOD."),
        "positional": positional_report_sentence(
            payload["caveats_fired"]["positional_confounded"]
        ),
        "cytotoxicity": cytotoxicity_sentence,
    }
    # Persist the R interpreter and DESeq2 package as separate runtime components. The package
    # version is read from the file written where DESeq2 was actually used.
    prov = env_provenance.software_facts(tables)
    payload["software"] = prov["software"]
    payload["headline_sentences"]["environment"] = prov["sentence"]
    _write_payload(outdir, payload)
    return payload


def run_fastq(r1, r2, whitelist, reference, outdir) -> dict:
    outdir = pathlib.Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    dm = demux_umi.run(r1, r2, whitelist, reference, str(outdir))
    stop = dm["barcode_assignment_rate"] < demux_umi.__dict__.get("MIN_ASSIGNMENT_RATE", 0.5) \
        if False else dm["barcode_assignment_rate"] < well_qc.MIN_ASSIGNMENT_RATE
    payload = {
        "status": "not_computable" if stop else "front_end_complete",
        "mode": "fastq",
        "qc": {"barcode_assignment_rate": dm["barcode_assignment_rate"]},
        "n_wells_with_reads": dm["n_wells_with_reads"],
        "reason": ("barcode assignment rate below threshold; reconcile whitelist/plate map" if stop
                   else "UMI matrix produced; continue in counts mode with a full aligner "
                        "(STARsolo/DRAGoN) for whole-transcriptome quantification"),
    }
    _write_payload(outdir, payload)
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description="DRUG-seq analysis orchestrator.")
    ap.add_argument("--execution-mode", choices=["analysis"], required=True)
    ap.add_argument("--mode", choices=["counts", "fastq"], required=True)
    ap.add_argument("--requested-dataset", required=True)
    ap.add_argument("--selection-evidence", required=True)
    ap.add_argument("--counts")
    ap.add_argument("--platemap")
    ap.add_argument("--genesets")
    ap.add_argument("--fastq-r1")
    ap.add_argument("--fastq-r2")
    ap.add_argument("--whitelist")
    ap.add_argument("--reference")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--covariate", default="none", choices=["none", "edge"])
    for role in ("counts", "platemap", "genesets", "fastq-r1", "fastq-r2", "whitelist", "reference"):
        ap.add_argument(f"--{role}-origin", choices=sorted(input_selection.ORIGINS))
        ap.add_argument(f"--{role}-source")
        ap.add_argument(f"--{role}-acquisition-json")
    args = ap.parse_args()

    paths = (("counts", args.counts), ("platemap", args.platemap), ("genesets", args.genesets)) if args.mode == "counts" else (
        ("fastq_r1", args.fastq_r1), ("fastq_r2", args.fastq_r2),
        ("whitelist", args.whitelist), ("reference", args.reference),
    )
    selected = []
    for role, path in paths:
        option = role.replace("_", "-")
        origin = getattr(args, f"{role}_origin")
        source = getattr(args, f"{role}_source")
        if not path or not origin or not source:
            ap.error(f"{args.mode} mode requires --{option}, --{option}-origin, and --{option}-source")
        selected.append(input_selection.select_input(
            role=role, path=path, origin=origin, requested_source=source,
            acquisition=input_selection.parse_acquisition(
                getattr(args, f"{role}_acquisition_json"), origin
            ),
        ))
    selection = input_selection.seal_manifest(
        requested_dataset=args.requested_dataset,
        inputs=selected,
        result_root=args.outdir,
        selection_evidence=args.selection_evidence,
    )
    input_selection.verify_manifest(pathlib.Path(args.outdir) / "input_selection.json")

    if args.mode == "counts":
        if not (args.counts and args.platemap and args.genesets):
            ap.error("counts mode needs --counts --platemap --genesets")
        payload = run_counts(args.counts, args.platemap, args.genesets, args.outdir, args.covariate)
    else:
        for req in ("fastq_r1", "fastq_r2", "whitelist", "reference"):
            if not getattr(args, req):
                ap.error(f"fastq mode needs --{req.replace('_', '-')}")
        payload = run_fastq(args.fastq_r1, args.fastq_r2, args.whitelist, args.reference, args.outdir)
    payload["execution_mode"] = "analysis"
    payload["input_selection_manifest_sha256"] = selection["manifest_sha256"]
    _write_payload(pathlib.Path(args.outdir), payload)
    print(json.dumps({k: payload[k] for k in ("status",) if k in payload}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
