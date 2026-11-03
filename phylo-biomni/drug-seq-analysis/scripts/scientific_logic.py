#!/usr/bin/env python3
"""Pure, version-controlled scientific claim and gate logic for DRUG-seq."""

from __future__ import annotations

import pandas as pd


MAX_NULL_DEGS = 5
NULL_CONTRAST_ID = "NULL_VEHICLE_vs_VEHICLE"
NULL_CONTRAST_FAMILY = "vehicle_vs_vehicle_null"
TREATMENT_CONTRAST_FAMILY = "treatment_vs_vehicle"
CONTROL_ROLE = "vehicle"
TREATMENT_ROLE = "treatment"
CONTROL_TRUE_VALUES = frozenset({
    "1", "1.0", "true", "yes", "y", "vehicle", "vehicle_control", "control",
    "negative", "negative_control", "negative control", "negcon",
})
CONTROL_FALSE_VALUES = frozenset({
    "0", "0.0", "false", "no", "n", "treatment", "test", "sample",
})
PATHWAY_RANKING_METRIC = "maximum absolute ULM t-value (sign preserved for interpretation)"
VIABILITY_COLUMN_CANDIDATES = frozenset({
    "viability", "cell_viability", "cell_viability_pct", "viability_pct",
})


def _parse_control_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in CONTROL_TRUE_VALUES:
        return True
    if normalized in CONTROL_FALSE_VALUES:
        return False
    raise ValueError(
        "is_control must use a supported semantic value: true/false, yes/no, 1/0, "
        "vehicle/control/negative_control/negcon, or treatment/test/sample"
    )


def normalize_control_roles(meta: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Validate the semantic control flag and return one globally resolved vehicle identity."""
    required = {"well_id", "compound", "is_control"}
    missing = sorted(required - set(meta.columns))
    if missing:
        raise ValueError(f"platemap is missing required control columns: {missing}")
    normalized = meta.copy()
    if normalized["well_id"].isna().any() or normalized["well_id"].astype(str).str.strip().eq("").any():
        raise ValueError("well_id must be populated for every platemap row")
    normalized["is_control"] = normalized["is_control"].map(_parse_control_flag)
    normalized["analysis_role"] = normalized["is_control"].map(
        {True: CONTROL_ROLE, False: TREATMENT_ROLE}
    )
    identities = normalized.loc[normalized["is_control"], "compound"].astype(str).str.strip()
    if identities.empty or identities.eq("").any() or identities.str.casefold().eq("nan").any():
        raise ValueError("every vehicle-control row must have a nonempty compound identity")
    identity_keys = identities.str.casefold().unique()
    if len(identity_keys) != 1:
        raise ValueError(
            "platemap contains mixed vehicle-control identities; define one global matched vehicle "
            "rather than pooling controls"
        )
    vehicle_identity = identities.iloc[0]
    normalized["vehicle_identity"] = vehicle_identity
    return normalized, vehicle_identity


def treatment_rows(meta: pd.DataFrame) -> pd.DataFrame:
    if "analysis_role" not in meta.columns:
        raise ValueError("normalized platemap is missing analysis_role")
    return meta.loc[meta["analysis_role"] == TREATMENT_ROLE].copy()


def gate_null_contrast(summary: pd.DataFrame,
                       max_null_degs: int = MAX_NULL_DEGS) -> dict:
    """Require one computed null contrast and stop when apparent hits exceed the cap."""
    if not isinstance(max_null_degs, int) or isinstance(max_null_degs, bool) or max_null_degs < 0:
        raise ValueError("maximum acceptable null DEGs must be a nonnegative integer")
    required = {"contrast", "contrast_family", "control_identity", "n_sig"}
    missing = sorted(required - set(summary.columns))
    if missing:
        raise ValueError(f"DE summary is missing semantic contrast columns: {missing}")
    rows = summary[summary["contrast_family"] == NULL_CONTRAST_FAMILY]
    if len(rows) != 1:
        raise ValueError(f"expected exactly one vehicle-vs-vehicle null row; observed {len(rows)}")
    if rows["contrast"].iloc[0] != NULL_CONTRAST_ID:
        raise ValueError("vehicle-null row does not use the canonical neutral contrast identifier")
    control_identity = str(rows["control_identity"].iloc[0]).strip()
    if not control_identity:
        raise ValueError("vehicle-null row is missing its derived control identity")
    raw_hits = rows["n_sig"].iloc[0]
    try:
        numeric_hits = float(raw_hits)
    except (TypeError, ValueError) as exc:
        raise ValueError("vehicle-null apparent DEG count must be numeric") from exc
    if not numeric_hits.is_integer() or numeric_hits < 0:
        raise ValueError("vehicle-null apparent DEG count must be a nonnegative integer")
    apparent_hits = int(numeric_hits)
    result = {
        "status": "passed" if apparent_hits <= max_null_degs else "failed",
        "n_deg": apparent_hits,
        "maximum_acceptable_degs": int(max_null_degs),
        "threshold_source": "scientific_logic.MAX_NULL_DEGS",
        "contrast": NULL_CONTRAST_ID,
        "control_identity": control_identity,
    }
    return result


def require_null_contrast_passed(gate: dict) -> None:
    """Hard-stop downstream work for a failed or malformed null-contrast gate."""
    if gate.get("status") != "passed":
        raise RuntimeError(
            "vehicle-vs-vehicle null contrast exceeded the pre-specified maximum: "
            f"{gate.get('n_deg')} > {gate.get('maximum_acceptable_degs')} DEGs"
        )


def top_activity(series: pd.Series) -> dict:
    """Rank one pathway table by the single pinned, direction-symmetric metric."""
    finite = pd.to_numeric(series, errors="coerce").dropna()
    if finite.empty:
        raise ValueError("no finite ULM activity values are available for ranking")
    gene_set = finite.abs().idxmax()
    return {
        "gene_set": str(gene_set),
        "ulm_t_value": float(finite.loc[gene_set]),
        "ranking_metric": PATHWAY_RANKING_METRIC,
    }


def positional_caption(flagged: bool, covariate: str,
                       pc1_fraction: float, pc2_fraction: float) -> str:
    """Derive the PCA caption from the diagnostic flag and executed DE covariate."""
    if not isinstance(flagged, bool):
        raise ValueError("positional flag must be boolean")
    if covariate not in {"none", "edge"}:
        raise ValueError("positional covariate must be 'none' or 'edge'")
    variance = f"PC1 {pc1_fraction:.0%}, PC2 {pc2_fraction:.0%}"
    if flagged and covariate == "edge":
        verdict = "positional structure was detected and the DESeq2 model included the edge covariate"
    elif flagged:
        verdict = "positional structure was detected but the DESeq2 model did not include an edge covariate"
    elif covariate == "edge":
        verdict = "positional structure was not flagged; the selected DESeq2 model still included the edge covariate"
    else:
        verdict = "positional structure was not flagged and no positional covariate was applied"
    return f"PCA of QC-passing wells ({variance}); {verdict}."


def cytotoxicity_report_sentence(contrast: str, n_deg: int, n_tested_genes: int,
                                 viability_available: bool) -> str:
    """Canonical limitation for large/high-dose perturbational signatures."""
    viability = (
        "A viability measurement was present, but this workflow did not fit a viability-adjusted model"
        if viability_available else
        "No cell-viability measurement was available for adjustment"
    )
    return (
        f"High-dose signatures (including the strongest, {contrast}, with {n_deg} DEGs among "
        f"{n_tested_genes} tested genes) may reflect cytotoxicity, global transcription/translation "
        f"inhibition, or a general stress response rather than compound-specific pharmacology. {viability}; "
        "confirm the mechanism with viability-matched doses or a separately validated "
        "viability-adjusted analysis."
    )
