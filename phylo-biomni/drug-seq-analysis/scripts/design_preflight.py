#!/usr/bin/env python3
"""Inference preflight for DRUG-seq contrasts: replication adequacy and design identifiability.

The contrast test is inferential, and the experimental unit is the WELL (not the cell). Before any
contrast is fitted this preflight checks that (1) each treated condition has at least
MIN_INDEPENDENT_UNITS QC-passing replicate wells, and (2) no condition is perfectly confounded with a
plate (which would make batch and effect non-separable — a design-stage stop). The vehicle-vs-vehicle
null contrast provides the empirical negative control / permutation reference downstream.

Files and technical read-outs never satisfy the biological-replication minimum by count alone: these
are replicate WELLS, which may be technical/plate-level rather than fully independent biological
replicates — a limitation the report states.

CLI:
  python3 design_preflight.py --platemap platemap.csv --pass-wells pass_wells.txt --outdir /mnt/results
writes <outdir>/tables/design_preflight.csv and prints a JSON summary (design_identifiable, min units).
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pandas as pd

import scientific_logic

MIN_INDEPENDENT_UNITS = 3


def run(platemap_path, outdir, pass_wells_path=None, min_units: int = MIN_INDEPENDENT_UNITS) -> dict:
    outdir = pathlib.Path(outdir)
    (outdir / "tables").mkdir(parents=True, exist_ok=True)
    meta = pd.read_csv(platemap_path)
    meta, vehicle_identity = scientific_logic.normalize_control_roles(meta)
    if pass_wells_path and pathlib.Path(pass_wells_path).exists():
        keep = {w.strip() for w in open(pass_wells_path) if w.strip()}
        meta = meta[meta["well_id"].isin(keep)]
    treated = scientific_logic.treatment_rows(meta)
    treated["condition"] = treated["compound"].astype(str) + "_" + treated["dose_uM"].astype(str)

    rows = []
    for cond, grp in treated.groupby("condition"):
        rows.append({"condition": cond, "n_replicate_wells": int(len(grp)),
                     "sufficient": bool(len(grp) >= min_units)})
    # vehicle replication too
    n_vehicle = int((meta["analysis_role"] == scientific_logic.CONTROL_ROLE).sum())
    pre = pd.DataFrame(rows).sort_values("condition")
    pre.to_csv(outdir / "tables" / "design_preflight.csv", index=False)

    # Identifiability: is any condition perfectly confounded with a single plate?
    n_plates = meta["plate_id"].nunique()
    confounded = []
    if n_plates > 1:
        for cond, grp in treated.groupby("condition"):
            plates_here = set(grp["plate_id"])
            if len(plates_here) == 1:
                only = next(iter(plates_here))
                if (meta.loc[meta["plate_id"] == only, "analysis_role"] !=
                        scientific_logic.CONTROL_ROLE).all() and \
                   (treated["condition"] == cond).sum() == (meta["plate_id"] == only).sum():
                    confounded.append(cond)
    identifiable = len(confounded) == 0

    summary = {
        "experimental_unit": "well (treated well-group vs matched vehicle wells on the plate)",
        "replicate_type": "replicate wells per condition (technical/plate-level unit)",
        "min_independent_units": int(min_units),
        "n_vehicle_wells": n_vehicle,
        "vehicle_identity": vehicle_identity,
        "conditions_insufficient": [r["condition"] for r in rows if not r["sufficient"]],
        "all_conditions_sufficient": bool(all(r["sufficient"] for r in rows)) if rows else False,
        "n_plates": int(n_plates),
        "design_identifiable": bool(identifiable),
        "confounded_conditions": confounded,
        "permutation_reference": "vehicle-vs-vehicle null contrast (must return ~0 DEGs)",
    }
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Inference preflight: replication + identifiability.")
    ap.add_argument("--platemap", required=True)
    ap.add_argument("--pass-wells", default=None)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--min-units", type=int, default=MIN_INDEPENDENT_UNITS)
    args = ap.parse_args()
    print(json.dumps(run(args.platemap, args.outdir, args.pass_wells, args.min_units), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
