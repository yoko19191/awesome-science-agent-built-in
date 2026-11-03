"""
============================================================================
SPATIAL PLATFORM ROUTER  (Visium vs Xenium)
============================================================================

Detects the assay platform of a spatial bundle from an EXPLICIT manifest and/or
the INPUT-FILE SIGNATURE, then routes to the Visium branch or the Xenium branch.

Critical guards (from the R01 lane evidence):
  - The router NEVER infers platform from a filename alone. A file called
    ``xenium_sample.h5ad`` with no Xenium content signature does not route to
    Xenium.
  - Ambiguous / mixed / incomplete bundles STOP and request an explicit
    platform/input manifest — they never guess.
  - The decision, the evidence behind it, and the platform label are written to
    ``platform_decision.json`` and travel into every downstream output.

Signatures (by file PRESENCE, not by loading):
  Visium : a Space Ranger matrix  AND  a spatial/ folder with tissue positions
           and scalefactors.
  Xenium : transcripts.parquet  AND  a cell_feature_matrix  AND  cells.parquet
           (morphology image and negative-control features strengthen it).

Usage:
  from platform_router import route_platform
  decision = route_platform("dataset/", output_dir="results")
  # decision["platform"] in {"Visium","Xenium"}; decision["status"] in
  # {"routed","stop"}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Signature detection helpers (presence-only; no file is opened for decoding)
# ---------------------------------------------------------------------------

def _exists_any(base: Path, names) -> Optional[str]:
    for n in names:
        p = base / n
        if p.exists():
            return n
    return None


def _glob_first(base: Path, patterns) -> Optional[str]:
    for pat in patterns:
        hits = sorted(base.glob(pat))
        if hits:
            return hits[0].name
    return None


def _spaceranger_matrix(base: Path) -> Optional[str]:
    hit = _exists_any(base, [
        "filtered_feature_bc_matrix.h5",
        "raw_feature_bc_matrix.h5",
        "filtered_feature_bc_matrix",
        "raw_feature_bc_matrix",
    ])
    return hit


def _visium_spatial_dir(base: Path) -> bool:
    sp = base / "spatial"
    if not sp.is_dir():
        return False
    positions = _exists_any(sp, [
        "tissue_positions.csv", "tissue_positions_list.csv",
        "tissue_positions.parquet",
    ])
    scale = (sp / "scalefactors_json.json").exists()
    return bool(positions and scale)


def _cell_feature_matrix(base: Path) -> Optional[str]:
    return _exists_any(base, [
        "cell_feature_matrix.h5",
        "cell_feature_matrix.zarr.zip",
        "cell_feature_matrix",
    ])


def detect_signature(bundle: Path) -> dict:
    """Return the raw presence-based signature evidence for a bundle directory."""
    sr_matrix = _spaceranger_matrix(bundle)
    visium_spatial = _visium_spatial_dir(bundle)

    transcripts = (bundle / "transcripts.parquet").exists()
    cfm = _cell_feature_matrix(bundle)
    cells = _exists_any(bundle, ["cells.parquet", "cells.csv.gz"])
    morphology = _glob_first(bundle, ["morphology*.tif", "morphology*.ome.tif", "*morphology*.tif"])
    boundaries = (bundle / "cell_boundaries.parquet").exists() or \
                 (bundle / "nucleus_boundaries.parquet").exists()

    # Negative-control features strengthen the Xenium signature. Detect from a
    # features table if one is cheaply available; otherwise report "unknown".
    controls_present = None
    feat = None
    if cfm and (bundle / "cell_feature_matrix").is_dir():
        feat = _exists_any(bundle / "cell_feature_matrix", ["features.tsv.gz", "features.tsv"])
    if feat:
        try:
            from spatial_common import is_control_feature
        except Exception:
            is_control_feature = None
        if is_control_feature is not None:
            import gzip
            fpath = bundle / "cell_feature_matrix" / feat
            opener = gzip.open if str(feat).endswith(".gz") else open
            try:
                with opener(fpath, "rt") as fh:
                    names = [ln.split("\t")[0].strip() for ln in fh if ln.strip()]
                controls_present = any(is_control_feature(n) for n in names)
            except Exception:
                controls_present = None

    visium_signature = bool(sr_matrix and visium_spatial)
    xenium_core = bool(transcripts and cfm and cells)
    xenium_full = bool(xenium_core and morphology)

    return {
        "spaceranger_matrix": sr_matrix,
        "visium_spatial_dir": visium_spatial,
        "visium_signature": visium_signature,
        "transcripts_parquet": transcripts,
        "cell_feature_matrix": cfm,
        "cells_table": cells,
        "morphology_image": morphology,
        "segmentation_boundaries": boundaries,
        "control_features_present": controls_present,
        "xenium_core_signature": xenium_core,
        "xenium_full_signature": xenium_full,
    }


def _read_manifest(bundle: Path) -> dict:
    for name in ("spatial_manifest.json", "manifest.json"):
        p = bundle / name
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {"_manifest_unreadable": True, "_manifest_file": name}
    return {}


def resolve_visium_payload(bundle: Path) -> Optional[str]:
    """Pick a loadable caller-selected payload for the production Visium route."""
    h5ad = _glob_first(bundle, ["*.h5ad", "**/*.h5ad"])
    if h5ad:
        return str(next(bundle.glob("**/*.h5ad")))
    if (bundle / "filtered_feature_bc_matrix.h5").exists():
        return str(bundle)  # sc.read_visium reads the directory
    if (bundle / "filtered_feature_bc_matrix").is_dir():
        return str(bundle)
    return None


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

_VALID = {"visium": "Visium", "xenium": "Xenium"}


def route_platform(bundle_dir, output_dir: str = "results",
                   manifest_platform: Optional[str] = None) -> dict:
    """Route a spatial bundle to the Visium or Xenium branch.

    Parameters
    ----------
    bundle_dir : str or Path
        Directory holding the spatial data bundle (and optionally
        ``spatial_manifest.json`` / ``samples.csv``).
    output_dir : str
        Where ``platform_decision.json`` is written.
    manifest_platform : str, optional
        Overrides/supplies the platform hint ("visium"/"xenium"/"auto").

    Returns
    -------
    dict
        Keys: ``status`` ("routed"|"stop"), ``platform`` ("Visium"|"Xenium"|None),
        ``confidence``, ``reason``, ``signature``, ``manifest_platform``,
        ``load`` (loader hints), ``filename_inference`` (always false).
    """
    bundle = Path(bundle_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if not bundle.exists() or not bundle.is_dir():
        decision = {
            "status": "stop", "platform": None, "confidence": "none",
            "reason": f"Bundle directory not found: {bundle}",
            "signature": {}, "manifest_platform": None,
            "filename_inference": False,
        }
        (out / "platform_decision.json").write_text(json.dumps(decision, indent=2, sort_keys=True))
        return decision

    manifest = _read_manifest(bundle)
    hint = (manifest_platform or manifest.get("platform") or "auto")
    hint = str(hint).strip().lower()
    if hint in ("", "auto", "none", "unknown"):
        hint = "auto"

    sig = detect_signature(bundle)
    vis = sig["visium_signature"]
    xen_core = sig["xenium_core_signature"]

    platform = None
    status = "stop"
    confidence = "none"
    reason = ""

    # --- Explicit platform hint: must be corroborated by the signature --------
    if hint in _VALID:
        want = _VALID[hint]
        if want == "Visium":
            if vis and not xen_core:
                platform, status, confidence = "Visium", "routed", "high"
                reason = "Manifest declares Visium and the Space Ranger matrix + spatial signature confirms it."
            elif vis and xen_core:
                reason = ("Manifest declares Visium but BOTH Visium and Xenium signatures are present "
                          "(mixed bundle). Stopping; provide a disambiguated, single-platform bundle.")
            else:
                reason = ("Manifest declares Visium but the Space Ranger matrix + spatial signature is "
                          "incomplete. Stopping; provide the missing Space Ranger/spatial files.")
        else:  # Xenium
            if xen_core and not vis:
                status, platform = "routed", "Xenium"
                confidence = "high" if sig["xenium_full_signature"] else "medium"
                reason = ("Manifest declares Xenium and the transcripts.parquet + cell_feature_matrix + "
                          "cells signature confirms it"
                          + ("." if sig["xenium_full_signature"]
                             else "; morphology image absent, so re-segmentation/subcellular steps are unavailable."))
            elif xen_core and vis:
                reason = ("Manifest declares Xenium but BOTH signatures are present (mixed bundle). "
                          "Stopping; provide a disambiguated, single-platform bundle.")
            else:
                reason = ("Manifest declares Xenium but the transcripts/cell_feature_matrix/cells "
                          "signature is incomplete. Stopping; provide the missing Xenium output files.")

    # --- Auto: decide from the signature alone (never the filename) -----------
    else:
        if vis and not xen_core:
            platform, status, confidence = "Visium", "routed", "high"
            reason = "Auto-detected Visium from the Space Ranger matrix + spatial signature."
        elif xen_core and not vis:
            status, platform = "routed", "Xenium"
            confidence = "high" if sig["xenium_full_signature"] else "medium"
            reason = ("Auto-detected Xenium from the transcripts.parquet + cell_feature_matrix + cells "
                      "signature"
                      + ("." if sig["xenium_full_signature"]
                         else "; morphology image absent, so re-segmentation/subcellular steps are unavailable."))
        elif vis and xen_core:
            reason = ("Both Visium and Xenium signatures are present (mixed bundle). Stopping; provide "
                      "an explicit spatial_manifest.json naming a single platform and a disambiguated bundle.")
        else:
            reason = ("No sufficient Visium or Xenium input signature found (incomplete bundle). "
                      "Filenames are NOT used to infer platform. Provide an explicit "
                      "spatial_manifest.json and the required platform files.")

    load = {}
    if platform == "Visium":
        load = {"reader": "visium", "path": resolve_visium_payload(bundle)}
    elif platform == "Xenium":
        cfm = sig["cell_feature_matrix"]
        load = {
            "reader": "xenium",
            "cell_feature_matrix": str(bundle / cfm) if cfm else None,
            "cells": str(bundle / (sig["cells_table"] or "")) if sig["cells_table"] else None,
            "transcripts": str(bundle / "transcripts.parquet") if sig["transcripts_parquet"] else None,
        }

    decision = {
        "status": status,
        "platform": platform,
        "confidence": confidence,
        "reason": reason,
        "signature": sig,
        "manifest_platform": hint if hint in _VALID else "auto",
        "manifest_present": bool(manifest) and "_manifest_unreadable" not in manifest,
        "filename_inference": False,   # invariant: never infer platform from a filename
        "bundle_dir": str(bundle),
        "load": load,
    }
    (out / "platform_decision.json").write_text(
        json.dumps(decision, indent=2, sort_keys=True), encoding="utf-8"
    )
    return decision


if __name__ == "__main__":
    import sys
    d = route_platform(sys.argv[1] if len(sys.argv) > 1 else "dataset",
                       sys.argv[2] if len(sys.argv) > 2 else "results")
    print(json.dumps(d, indent=2, sort_keys=True))
