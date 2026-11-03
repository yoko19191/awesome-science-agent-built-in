#!/usr/bin/env python3
"""Fail-closed dependency preflight for a cytometry run."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess


R_CORE_PACKAGES = (
    "optparse", "flowCore", "CATALYST", "FlowSOM", "ConsensusClusterPlus", "MASS",
    "SingleCellExperiment", "SummarizedExperiment", "S4Vectors",
    "ggplot2", "svglite", "matrixStats", "ComplexHeatmap", "jsonlite",
)
R_FULL_PACKAGES = R_CORE_PACKAGES + ("scater", "uwot", "Rtsne", "ggrepel")
R_DIFFERENTIAL_PACKAGES = R_FULL_PACKAGES + ("diffcyt",)
R_OPENCYTO_PACKAGES = R_FULL_PACKAGES + ("openCyto", "flowWorkspace", "CytoML")
PYTHON_PACKAGES = ("reportlab", "pypdf")
STATE_FILE = "environment_state.csv"
CHECKPOINT_FILE = "environment_resume.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--profile", choices=("core", "full", "differential", "opencyto"), default="full")
    return parser.parse_args()


def missing_r_packages(packages: tuple[str, ...]) -> list[str]:
    rscript = shutil.which("Rscript")
    if not rscript:
        return ["Rscript"] + list(packages)
    expression = (
        "packages <- c(" + ",".join(json.dumps(package) for package in packages) + ");"
        "cat(packages[!vapply(packages, requireNamespace, logical(1), quietly=TRUE)], sep='\\n')"
    )
    result = subprocess.run(
        [rscript, "-e", expression], check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return ["Rscript_preflight_error"]
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def write_state(outdir: Path, state: str, profile: str, missing: list[str], rationale: str) -> None:
    with (outdir / STATE_FILE).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("state", "profile", "missing_dependencies", "rationale"))
        writer.writeheader()
        writer.writerow({
            "state": state,
            "profile": profile,
            "missing_dependencies": ";".join(missing),
            "rationale": rationale,
        })
    checkpoint = {
        "schema_version": 1,
        "state": state,
        "profile": profile,
        "missing_dependencies": missing,
        "next_action": (
            "Install the named dependencies in one bounded foreground step, then rerun this exact preflight. "
            "If the environment is prepared asynchronously, resume this task only after the job is terminal."
            if missing else
            "Run the analysis stages, build the report, then execute verify_run_complete.py."
        ),
    }
    (outdir / CHECKPOINT_FILE).write_text(json.dumps(checkpoint, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    r_packages = {
        "core": R_CORE_PACKAGES,
        "full": R_FULL_PACKAGES,
        "differential": R_DIFFERENTIAL_PACKAGES,
        "opencyto": R_OPENCYTO_PACKAGES,
    }[args.profile]
    missing = missing_r_packages(r_packages)
    missing.extend(package for package in PYTHON_PACKAGES if importlib.util.find_spec(package) is None)
    if missing:
        write_state(
            outdir, "INCOMPLETE_environment_dependencies", args.profile, missing,
            "The analysis environment is not ready; no analysis stage may start.",
        )
        return 3
    write_state(outdir, "PASS_ready", args.profile, [], "All required runtime dependencies are importable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
