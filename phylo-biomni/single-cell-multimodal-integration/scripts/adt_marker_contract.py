#!/usr/bin/env python3
"""Rank biological ADT markers on the normalized-score scale used by the run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu

from adt_contract import canonical_adt_feature_names


OUTPUT_COLUMNS = (
    "cluster",
    "gene",
    "avg_normalized_adt_difference",
    "p_val_adj",
    "pct.1",
    "pct.2",
    "normalization_method",
)


def _validated_matrix(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    if frame.index.has_duplicates or frame.columns.has_duplicates:
        raise ValueError(f"{name} matrix needs unique barcode and feature axes")
    canonical = canonical_adt_feature_names(frame.columns.tolist(), context=f"{name} ADT panel")
    matrix = frame.copy()
    matrix.columns = canonical
    values = matrix.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError(f"{name} matrix contains non-finite values")
    return matrix


def rank_adt_markers(
    normalized: pd.DataFrame,
    raw_counts: pd.DataFrame,
    clusters: pd.Series,
    isotype_controls: list[str],
    normalization_method: str,
    *,
    min_pct: float = 0.1,
    min_effect: float = 0.1,
    top_n: int = 8,
) -> pd.DataFrame:
    """Return positive Wilcoxon markers with arithmetic effects on the normalized ADT scale."""
    norm = _validated_matrix(normalized, "normalized")
    counts = _validated_matrix(raw_counts, "raw-count")
    if set(norm.index) != set(counts.index) or set(norm.columns) != set(counts.columns):
        raise ValueError("normalized and raw-count ADT matrices do not have identical axes")
    counts = counts.loc[norm.index, norm.columns]

    labels = clusters.astype(str)
    if labels.index.has_duplicates or set(labels.index) != set(norm.index):
        raise ValueError("cluster labels must provide one unique value for every ADT barcode")
    labels = labels.loc[norm.index]
    cluster_names = sorted(labels.unique())
    if len(cluster_names) < 2:
        raise ValueError("ADT marker ranking requires at least two clusters")
    if not 0 <= min_pct <= 1:
        raise ValueError("min_pct must lie in [0,1]")
    if min_effect < 0:
        raise ValueError("min_effect must be non-negative")
    if top_n < 1:
        raise ValueError("top_n must be at least 1")

    canonical_isotypes = set(canonical_adt_feature_names(
        isotype_controls, context="ADT marker isotype controls"
    ))
    biological_features = [feature for feature in norm.columns if feature not in canonical_isotypes]
    rows: list[dict[str, object]] = []
    for cluster in cluster_names:
        inside = labels == cluster
        outside = ~inside
        cluster_rows: list[dict[str, object]] = []
        for feature in biological_features:
            observed = norm.loc[inside, feature].to_numpy(dtype=float)
            reference = norm.loc[outside, feature].to_numpy(dtype=float)
            detected_inside = float((counts.loc[inside, feature] > 0).mean())
            detected_outside = float((counts.loc[outside, feature] > 0).mean())
            effect = float(observed.mean() - reference.mean())
            if max(detected_inside, detected_outside) < min_pct or effect < min_effect:
                continue
            p_value = float(mannwhitneyu(
                observed,
                reference,
                alternative="greater",
                method="asymptotic",
            ).pvalue)
            cluster_rows.append({
                "cluster": cluster,
                "gene": feature,
                "avg_normalized_adt_difference": effect,
                "p_value": p_value,
                "pct.1": detected_inside,
                "pct.2": detected_outside,
                "normalization_method": normalization_method,
            })
        comparison_count = max(1, len(biological_features))
        for row in cluster_rows:
            row["p_val_adj"] = min(1.0, float(row.pop("p_value")) * comparison_count)
        cluster_rows.sort(
            key=lambda row: (
                -float(row["avg_normalized_adt_difference"]),
                float(row["p_val_adj"]),
                str(row["gene"]),
            )
        )
        rows.extend(cluster_rows[:top_n])
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def _read_matrix(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if "barcode" not in frame.columns:
        raise ValueError(f"ADT matrix {path} has no barcode column")
    return frame.set_index("barcode")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized", required=True)
    parser.add_argument("--raw-counts", required=True)
    parser.add_argument("--clusters", required=True)
    parser.add_argument("--isotypes", default="")
    parser.add_argument("--normalization-method", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--min-pct", type=float, default=0.1)
    parser.add_argument("--min-effect", type=float, default=0.1)
    parser.add_argument("--top-n", type=int, default=8)
    args = parser.parse_args(argv)

    cluster_frame = pd.read_csv(args.clusters)
    required = {"barcode", "seurat_clusters"}
    if not required.issubset(cluster_frame.columns):
        raise ValueError(f"cluster table requires columns {sorted(required)}")
    clusters = cluster_frame.set_index("barcode")["seurat_clusters"]
    result = rank_adt_markers(
        _read_matrix(Path(args.normalized)),
        _read_matrix(Path(args.raw_counts)),
        clusters,
        [value.strip() for value in args.isotypes.split(",") if value.strip()],
        args.normalization_method,
        min_pct=args.min_pct,
        min_effect=args.min_effect,
        top_n=args.top_n,
    )
    output = Path(args.output)
    result.to_csv(output, index=False)
    status = {
        "schema": "single-cell-multimodal-adt-markers/1",
        "effect_statistic": "avg_normalized_adt_difference",
        "effect_definition": "arithmetic mean normalized ADT score in cluster minus outside cluster",
        "normalization_method": args.normalization_method,
        "isotype_controls_excluded": True,
        "n_markers": len(result),
    }
    output.with_name("protein_marker_status.json").write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
