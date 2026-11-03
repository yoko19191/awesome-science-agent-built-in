"""
============================================================================
SHARED SPATIAL HELPERS (platform-agnostic)
============================================================================

Utilities shared by the Visium branch, the Xenium branch, and the router:
  - Negative-control feature identification (Xenium-specific, computation-critical)
  - Standardization of a QC'd branch object to a common spatial object
  - Provenance capture

The negative-control feature name prefixes are an external, computation-critical
fact taken from the 10x Xenium output specification (see references and
skill_contract.json:source_assertions). Control features MUST be separated from
genes at load and MUST NEVER enter biological analysis.

These helpers are intentionally dependency-light (numpy/pandas/anndata) so the
router and QC can run without the full analysis stack.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

# --- Xenium negative-control feature classes -------------------------------
# Source: 10x Genomics Xenium Onboard Analysis output specification. The cell-
# feature matrix ships gene features together with several non-biological control
# feature classes; these prefixes identify them for exclusion. Kept as a pinned
# literal here (not fetched at runtime); recorded as a source assertion.
NEG_CONTROL_PREFIXES: tuple[str, ...] = (
    "NegControlProbe",
    "NegControlCodeword",
    "NegPrb",
    "antisense",
    "BLANK",
    "Blank",
    "UnassignedCodeword",
    "DeprecatedCodeword",
    "Intergenic",
    "Genomic",
    "Control",
)

# The 10x convention: the cell-feature matrix contains transcripts at Phred
# quality Q >= 20 by default. Pinned literal, recorded as a source assertion.
XENIUM_MIN_TRANSCRIPT_QV: int = 20

PROVENANCE_SCHEMA = "spatial-transcriptomics-provenance/1"


def is_control_feature(name: str) -> bool:
    """True if a feature name matches a Xenium negative-control class prefix."""
    lname = str(name)
    return any(lname.startswith(p) or lname.lower().startswith(p.lower())
               for p in NEG_CONTROL_PREFIXES)


def split_control_features(var_names: Iterable[str]) -> tuple[list[str], list[str]]:
    """Partition feature names into (genes, control_features)."""
    genes, controls = [], []
    for n in var_names:
        (controls if is_control_feature(n) else genes).append(str(n))
    return genes, controls


def standardize_spatial_object(
    adata: "anndata.AnnData",
    *,
    platform: str,
    sample_key: Optional[str] = None,
    condition_key: Optional[str] = None,
) -> "anndata.AnnData":
    """Standardize a QC'd branch object to the common spatial object contract.

    Guarantees, regardless of platform:
      - ``.obsm['spatial']`` exists (2D float coordinates)
      - ``.obs['platform']`` records the originating platform on every observation
      - ``.uns['spatial_platform']`` records the platform once
      - ``.obs['sample_id']`` and ``.obs['condition']`` exist when provided
      - control features are NOT present in ``.var`` (Xenium controls already split
        out upstream); any that remain are moved to ``.uns['control_features']``

    The standardized object is the ONLY thing shared downstream consumes, so the
    platform label travels into every downstream output.
    """
    if "spatial" not in adata.obsm:
        raise ValueError("standardize_spatial_object: .obsm['spatial'] is required")

    adata.obs["platform"] = platform
    adata.uns["spatial_platform"] = platform

    # Defensive: never let a control feature reach biology.
    remaining_controls = [v for v in adata.var_names if is_control_feature(v)]
    if remaining_controls:
        adata.uns.setdefault("control_features", [])
        adata.uns["control_features"] = sorted(
            set(list(adata.uns["control_features"]) + remaining_controls)
        )
        keep = [v for v in adata.var_names if not is_control_feature(v)]
        adata = adata[:, keep].copy()

    if sample_key and sample_key in adata.obs:
        adata.obs["sample_id"] = adata.obs[sample_key].astype(str).values
    elif "sample_id" not in adata.obs:
        adata.obs["sample_id"] = "sample_1"

    if condition_key and condition_key in adata.obs:
        adata.obs["condition"] = adata.obs[condition_key].astype(str).values

    adata.obsm["spatial"] = np.asarray(adata.obsm["spatial"], dtype=float)
    return adata


def replicate_summary(adata: "anndata.AnnData",
                      sample_key: str = "sample_id",
                      condition_key: str = "condition") -> dict:
    """Summarize biological replication for replicate-aware DE gating.

    Returns counts of independent samples per condition. Technical units (cells,
    spots, transcripts) never satisfy a biological-replicate minimum.
    """
    if condition_key not in adata.obs or sample_key not in adata.obs:
        return {"has_condition": False, "samples_per_condition": {}, "min_reps": 0}
    df = adata.obs[[sample_key, condition_key]].astype(str).drop_duplicates()
    per = df.groupby(condition_key)[sample_key].nunique().to_dict()
    return {
        "has_condition": True,
        "samples_per_condition": {str(k): int(v) for k, v in per.items()},
        "min_reps": int(min(per.values())) if per else 0,
    }


def write_provenance(path, info: dict) -> str:
    """Write a provenance.json capturing platform, parameters, versions, thresholds."""
    payload = {
        "schema": PROVENANCE_SCHEMA,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **info,
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return str(p)


def tool_versions() -> dict:
    """Best-effort version capture for provenance (never raises)."""
    out = {}
    for mod in ("scanpy", "squidpy", "anndata", "numpy", "pandas"):
        try:
            out[mod] = __import__(mod).__version__
        except Exception:
            out[mod] = None
    return out
