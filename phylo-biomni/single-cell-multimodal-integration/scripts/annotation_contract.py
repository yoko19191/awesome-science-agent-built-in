#!/usr/bin/env python3
"""Package-owned surface-marker matching and cluster-assignment contract."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path

import numpy as np


TOTAL_SEQ_SUFFIX = re.compile(r"[-_. ]*TOTALSEQ[A-Z0-9]*$", re.IGNORECASE)
MARKER_ALIASES = {"CD8A": "CD8"}


def canonical_surface_marker(feature: str) -> str:
    """Map a TotalSeq feature label to the antigen spelling used by marker programs."""
    marker = str(feature).strip().replace("_", "-")
    marker = TOTAL_SEQ_SUFFIX.sub("", marker).upper()
    return MARKER_ALIASES.get(marker, marker)


def _program_scores(features: list[str], values: np.ndarray,
                    programs: dict[str, list[str]]) -> dict[str, float | None]:
    feature_markers = [canonical_surface_marker(feature) for feature in features]
    scores: dict[str, float | None] = {}
    for name, program_markers in programs.items():
        wanted = {canonical_surface_marker(marker) for marker in program_markers}
        selected = [index for index, marker in enumerate(feature_markers) if marker in wanted]
        finite = values[selected][np.isfinite(values[selected])] if selected else np.asarray([])
        scores[name] = float(np.mean(finite)) if finite.size else None
    return scores


def _assignment(cluster: str, scores: dict[str, float | None]) -> dict[str, object]:
    finite = [(name, value) for name, value in scores.items()
              if value is not None and math.isfinite(value)]
    if not finite:
        return {
            "cluster": cluster,
            "cell_type": "unassigned",
            "top_program_score": None,
            "margin": None,
            "confidence": "unassigned_no_matching_adt_markers",
        }
    ranked = sorted(finite, key=lambda item: item[1], reverse=True)
    top, top_score = ranked[0]
    margin = top_score - ranked[1][1] if len(ranked) > 1 else None
    if top_score < 0.25:
        confidence = "low"
    elif margin is not None and margin < 0.5:
        confidence = "ambiguous"
    else:
        confidence = "confident"
    return {
        "cluster": cluster,
        "cell_type": top,
        "top_program_score": round(top_score, 3),
        "margin": round(margin, 3) if margin is not None else None,
        "confidence": confidence,
    }


def assign_cluster_programs(*, features: list[str], clusters: list[str], z_scores,
                            programs: dict[str, list[str]]) -> list[dict[str, object]]:
    """Assign clusters only from programs with at least one matched, finite ADT value."""
    matrix = np.asarray(z_scores, dtype=float)
    if matrix.shape != (len(features), len(clusters)):
        raise ValueError(
            "ADT score matrix shape does not match its feature and cluster labels: "
            f"{matrix.shape} != {(len(features), len(clusters))}"
        )
    if len(set(features)) != len(features) or len(set(clusters)) != len(clusters):
        raise ValueError("ADT score matrix feature and cluster labels must be unique")
    if not programs:
        raise ValueError("surface-marker program collection is empty")
    return [
        _assignment(cluster, _program_scores(features, matrix[:, index], programs))
        for index, cluster in enumerate(clusters)
    ]


def _read_scores(path: Path) -> tuple[list[str], list[str], np.ndarray]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    if len(rows) < 2 or len(rows[0]) < 2 or rows[0][0] != "feature":
        raise ValueError("annotation score CSV must have feature rows and cluster columns")
    clusters = rows[0][1:]
    features = [row[0] for row in rows[1:]]
    try:
        values = np.asarray([[float(value) for value in row[1:]] for row in rows[1:]])
    except ValueError as exc:
        raise ValueError("annotation score CSV contains a non-numeric score") from exc
    return features, clusters, values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--programs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    programs = json.loads(args.programs.read_text(encoding="utf-8"))
    if not isinstance(programs, dict) or not all(isinstance(value, list) for value in programs.values()):
        raise ValueError("surface-marker programs must be a JSON object of marker arrays")
    features, clusters, values = _read_scores(args.scores)
    assignments = assign_cluster_programs(
        features=features, clusters=clusters, z_scores=values, programs=programs
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=(
            "cluster", "cell_type", "top_program_score", "margin", "confidence"
        ))
        writer.writeheader()
        writer.writerows(assignments)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
