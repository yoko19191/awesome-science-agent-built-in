#!/usr/bin/env python3
"""Seal explicit scATAC/multiome inputs and run the configured production pipeline."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import input_selection


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-mode", choices=["analysis"], required=True)
    parser.add_argument("--requested-dataset", required=True)
    parser.add_argument("--selection-evidence", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--config-origin", choices=sorted(input_selection.ORIGINS), required=True)
    parser.add_argument("--config-source", required=True)
    parser.add_argument("--config-acquisition-json")
    parser.add_argument("--input-declarations", required=True,
                        help="JSON array of explicit role/path/origin/requested_source/acquisition objects")
    parser.add_argument("--declarations-origin", choices=sorted(input_selection.ORIGINS), required=True)
    parser.add_argument("--declarations-source", required=True)
    parser.add_argument("--declarations-acquisition-json")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    declaration_path = Path(args.input_declarations).resolve()
    declarations = json.loads(declaration_path.read_text(encoding="utf-8"))
    if not isinstance(declarations, list) or not declarations:
        parser.error("input declarations must be a non-empty JSON array")
    selected = [
        input_selection.select_input(
            role="analysis_config", path=config_path, origin=args.config_origin,
            requested_source=args.config_source,
            acquisition=input_selection.parse_acquisition(
                args.config_acquisition_json, args.config_origin),
        ),
        input_selection.select_input(
            role="input_declarations", path=declaration_path,
            origin=args.declarations_origin, requested_source=args.declarations_source,
            acquisition=input_selection.parse_acquisition(
                args.declarations_acquisition_json, args.declarations_origin),
        ),
    ]
    for declaration in declarations:
        if set(declaration) != {"role", "path", "origin", "requested_source", "acquisition"}:
            parser.error("each declaration must contain role, path, origin, requested_source, acquisition")
        selected.append(input_selection.select_input(
            role=declaration["role"], path=declaration["path"],
            origin=declaration["origin"], requested_source=declaration["requested_source"],
            acquisition=declaration["acquisition"],
        ))
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    result_root = Path(config["project"]["results_dir"]).resolve()
    work_root = Path(config["project"]["outdir"]).resolve()
    if input_selection._inside(result_root, input_selection.PACKAGE_ROOT) or input_selection._inside(
        work_root, input_selection.PACKAGE_ROOT
    ):
        parser.error("configured output roots must be outside the package")
    sealed = input_selection.seal_manifest(
        requested_dataset=args.requested_dataset, inputs=selected,
        result_root=result_root, selection_evidence=args.selection_evidence,
    )
    manifest = result_root / "input_selection.json"
    input_selection.verify_manifest(manifest)
    env = dict(os.environ)
    env["BIOMNI_INPUT_SELECTION_MANIFEST"] = str(manifest)
    completed = subprocess.run(["bash", str(HERE / "run_all.sh"), str(config_path)], env=env)
    if completed.returncode == 0:
        (result_root / "analysis_provenance.json").write_text(json.dumps({
            "execution_mode": "analysis",
            "input_selection_manifest_sha256": sealed["manifest_sha256"],
            "package_data_used": False,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
