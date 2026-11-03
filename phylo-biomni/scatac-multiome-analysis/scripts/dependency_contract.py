#!/usr/bin/env python3
"""Derive fail-closed R dependencies from the selected analysis branch."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

import yaml


def _r_package(*components: str) -> str:
    return ".".join(components)


ROUTE_DISCOVERY = "route_discovery"
SUPPORTED_ROUTES = (ROUTE_DISCOVERY, "atac_only", "multiome_annotate", "joint")
BARCODE_COLUMNS = ("barcode", "cell", "cell_id", "cb")
DISCOVERY_PACKAGES = ("yaml", "jsonlite")
ANALYSIS_PACKAGES = (
    "yaml",
    "jsonlite",
    "Matrix",
    "Signac",
    "Seurat",
    "GenomicRanges",
    "GenomeInfoDb",
    "ggplot2",
    "dplyr",
    "biovizBase",
)
GENOME_PACKAGES = {
    "hg38": ("EnsDb.Hsapiens.v86", _r_package("BSgenome", "Hsapiens", "UCSC", "hg38")),
    "hg19": ("EnsDb.Hsapiens.v75", _r_package("BSgenome", "Hsapiens", "UCSC", "hg19")),
    "mm10": ("EnsDb.Mmusculus.v79", _r_package("BSgenome", "Mmusculus", "UCSC", "mm10")),
}
REPLICATE_INFERENCE_PACKAGES = ("miloR", "SingleCellExperiment")
DEFAULT_MIN_REPLICATES = 2
MIN_CONDITIONS = 2


class DependencyContractError(RuntimeError):
    """Raised when a selected branch cannot be checked safely."""


def _configured_h5(config: Mapping[str, object]) -> bool:
    inputs = config.get("input") or {}
    if not isinstance(inputs, Mapping):
        raise DependencyContractError("config.input must be a mapping")
    return any(
        isinstance(inputs.get(key), str) and bool(str(inputs[key]).strip())
        for key in ("multiome_matrix_h5", "peak_matrix_h5")
    )


def _replicate_design(
    rows: Sequence[Mapping[str, str]] | None,
    replicate_col: str,
    condition_col: str,
    minimum: int,
) -> dict[str, object]:
    if not rows:
        return {
            "sufficient": False,
            "reason": "metadata unavailable or empty",
            "per_condition": {},
            "min_required": minimum,
        }
    columns = set(rows[0])
    missing = [column for column in (replicate_col, condition_col) if column not in columns]
    if missing:
        return {
            "sufficient": False,
            "reason": f"metadata missing required column(s): {', '.join(missing)}",
            "per_condition": {},
            "min_required": minimum,
        }
    if not any(column in columns for column in BARCODE_COLUMNS):
        return {
            "sufficient": False,
            "reason": "metadata has no barcode identifier for the joint-cell join",
            "per_condition": {},
            "min_required": minimum,
        }
    unique_units = {
        (str(row.get(replicate_col, "")).strip(), str(row.get(condition_col, "")).strip())
        for row in rows
        if str(row.get(replicate_col, "")).strip() and str(row.get(condition_col, "")).strip()
    }
    per_condition = Counter(condition for _, condition in unique_units)
    sufficient = len(per_condition) >= MIN_CONDITIONS and all(count >= minimum for count in per_condition.values())
    reason = (
        "at least two biological replicates are present in each of at least two conditions"
        if sufficient
        else f"need at least {minimum} biological replicates in each of at least two conditions"
    )
    return {
        "sufficient": sufficient,
        "reason": reason,
        "per_condition": dict(sorted(per_condition.items())),
        "min_required": minimum,
    }


def load_metadata(config: Mapping[str, object]) -> list[dict[str, str]] | None:
    inputs = config.get("input") or {}
    if not isinstance(inputs, Mapping):
        raise DependencyContractError("config.input must be a mapping")
    configured_path = inputs.get("metadata_csv")
    if not isinstance(configured_path, str) or not configured_path.strip():
        return None
    path = Path(configured_path)
    if not path.is_file():
        return None
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def derive_dependency_contract(
    config: Mapping[str, object],
    selected_route: str,
    metadata_rows: Sequence[Mapping[str, str]] | None = None,
) -> dict[str, object]:
    if selected_route not in SUPPORTED_ROUTES:
        raise DependencyContractError(
            f"unsupported selected route {selected_route!r}; expected one of {', '.join(SUPPORTED_ROUTES)}"
        )

    required = list(DISCOVERY_PACKAGES if selected_route == ROUTE_DISCOVERY else ANALYSIS_PACKAGES)
    branches: dict[str, dict[str, object]] = {
        "route_discovery": {
            "selected": selected_route == ROUTE_DISCOVERY,
            "reason": "resolve the input regime before selecting analysis dependencies",
        }
    }
    if selected_route == ROUTE_DISCOVERY:
        if _configured_h5(config):
            required.append("hdf5r")
        return {
            "selected_route": selected_route,
            "branches": branches,
            "required_r_packages": required,
        }

    genome = config.get("genome") or {}
    if not isinstance(genome, Mapping):
        raise DependencyContractError("config.genome must be a mapping")
    build = genome.get("build")
    if build not in GENOME_PACKAGES:
        raise DependencyContractError(f"unsupported genome build in dependency preflight: {build}")
    required.extend(GENOME_PACKAGES[str(build)])
    if selected_route in ("multiome_annotate", "joint") or _configured_h5(config):
        required.append("hdf5r")
    if selected_route in ("atac_only", "multiome_annotate"):
        required.append("tidyr")

    branches["selected_pipeline"] = {
        "selected": True,
        "route": selected_route,
        "reason": f"route_decision.json selected {selected_route}",
    }
    diffabund = config.get("diffabund") or {}
    if not isinstance(diffabund, Mapping):
        raise DependencyContractError("config.diffabund must be a mapping")
    configured_replicate_col = diffabund.get("replicate_col")
    configured_condition_col = diffabund.get("condition_col")
    replicate_col = "replicate" if configured_replicate_col is None else str(configured_replicate_col)
    condition_col = "condition" if configured_condition_col is None else str(configured_condition_col)
    configured_minimum = diffabund.get("min_replicates_per_group")
    minimum = DEFAULT_MIN_REPLICATES if configured_minimum is None else int(configured_minimum)
    replicate_design = _replicate_design(metadata_rows, replicate_col, condition_col, minimum)
    inference_selected = selected_route == "joint" and bool(replicate_design["sufficient"])
    branches["replicate_aware_inference"] = {
        "selected": inference_selected,
        "reason": (
            replicate_design["reason"] if selected_route == "joint" else f"not part of the {selected_route} route"
        ),
        "replicate_design": replicate_design,
    }
    if inference_selected:
        required.extend(REPLICATE_INFERENCE_PACKAGES)

    return {
        "selected_route": selected_route,
        "branches": branches,
        "required_r_packages": list(dict.fromkeys(required)),
    }


def assert_required_packages(
    contract: Mapping[str, object],
    available: Callable[[str], bool],
) -> None:
    packages = contract.get("required_r_packages")
    if not isinstance(packages, Iterable) or isinstance(packages, (str, bytes)):
        raise DependencyContractError("dependency contract lacks required_r_packages")
    missing = [str(package) for package in packages if not available(str(package))]
    if missing:
        route = contract.get("selected_route", "unknown")
        raise DependencyContractError(f"missing dependencies for selected route {route}: {', '.join(missing)}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--selected-route", choices=SUPPORTED_ROUTES, required=True)
    args = parser.parse_args(argv)
    with Path(args.config).open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, Mapping):
        raise DependencyContractError("config root must be a mapping")
    metadata = load_metadata(config) if args.selected_route != ROUTE_DISCOVERY else None
    print(json.dumps(derive_dependency_contract(config, args.selected_route, metadata), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
