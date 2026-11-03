#!/usr/bin/env python3
"""Validated metrics on actual WNN outputs, with explicit degenerate states."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_NEIGHBOR_COUNT = 30
MIN_METRIC_CELLS = 3


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_barcode_order(barcodes: list[str]) -> str:
    return hashlib.sha256(("\n".join(barcodes) + "\n").encode("utf-8")).hexdigest()


def neighbors_from_coordinates(coordinates: np.ndarray,
                               k: int = DEFAULT_NEIGHBOR_COUNT) -> np.ndarray:
    """Build deterministic neighbors for an alternative coordinate representation."""
    from sklearn.neighbors import NearestNeighbors

    n_cells = coordinates.shape[0]
    if n_cells < MIN_METRIC_CELLS:
        raise ValueError(f"integration metrics need at least {MIN_METRIC_CELLS} cells")
    if not np.isfinite(coordinates).all() or np.unique(coordinates, axis=0).shape[0] < 2:
        raise ValueError("alternative coordinate representation is non-finite or constant")
    neighbor_count = min(int(k), n_cells - 1)
    model = NearestNeighbors(n_neighbors=neighbor_count + 1).fit(coordinates)
    return model.kneighbors(coordinates, return_distance=False)[:, 1:]


def load_wnn_coordinates(path: str | Path, barcodes: list[str]) -> np.ndarray:
    """Load aligned WNN UMAP coordinates and reject unusable/degenerate arrays."""
    frame = pd.read_csv(path)
    if "barcode" not in frame.columns:
        raise ValueError("WNN coordinate export lacks barcode")
    frame["barcode"] = frame["barcode"].astype(str)
    if frame["barcode"].duplicated().any():
        raise ValueError("WNN coordinate export has duplicate barcodes")
    frame = frame.set_index("barcode")
    missing = [barcode for barcode in barcodes if barcode not in frame.index]
    if missing:
        raise ValueError(f"WNN coordinate export is missing barcodes: {missing[:5]}")
    if frame.index.tolist() != list(barcodes):
        raise ValueError("WNN coordinate export barcode order differs from the metric order")
    values = frame.loc[barcodes].to_numpy(dtype=np.float32)
    if values.shape != (len(barcodes), 2):
        raise ValueError(f"expected an n_cells x 2 WNN UMAP; observed {values.shape}")
    if len(barcodes) < MIN_METRIC_CELLS:
        raise ValueError(f"integration metrics need at least {MIN_METRIC_CELLS} cells")
    if not np.isfinite(values).all():
        raise ValueError("WNN coordinate export contains non-finite values")
    if np.unique(values, axis=0).shape[0] < 2:
        raise ValueError("WNN coordinate export is constant")
    return values


def load_wnn_neighbors(path: str | Path, barcodes: list[str]) -> np.ndarray:
    """Load the exact weighted-neighbor graph export as zero-based row indices."""
    frame = pd.read_csv(path)
    required = {"barcode", "neighbor_barcode", "rank"}
    if not required.issubset(frame.columns):
        raise ValueError(f"WNN neighbor export lacks columns: {sorted(required - set(frame.columns))}")
    frame["barcode"] = frame["barcode"].astype(str)
    frame["neighbor_barcode"] = frame["neighbor_barcode"].astype(str)
    position = {barcode: index for index, barcode in enumerate(barcodes)}
    source_order = list(dict.fromkeys(frame["barcode"].tolist()))
    if source_order != list(barcodes):
        raise ValueError("WNN neighbor source order differs from the metric barcode order")
    if set(frame["barcode"]) != set(barcodes):
        raise ValueError("WNN neighbor sources do not match the metric barcode set")
    if not set(frame["neighbor_barcode"]).issubset(position):
        raise ValueError("WNN neighbor targets do not match the metric barcode set")
    ordered = frame.sort_values(["barcode", "rank"])
    counts = ordered.groupby("barcode", sort=False).size()
    if counts.empty or counts.nunique() != 1 or int(counts.iloc[0]) < 1:
        raise ValueError("WNN neighbor export has inconsistent or empty neighborhoods")
    by_source = {source: group.sort_values("rank") for source, group in ordered.groupby("barcode")}
    for source in barcodes:
        group = by_source[source]
        expected_ranks = np.arange(1, len(group) + 1)
        if not np.array_equal(group["rank"].to_numpy(), expected_ranks):
            raise ValueError(f"WNN neighbor ranks are not contiguous for {source}")
        targets = group["neighbor_barcode"].tolist()
        if source in targets or len(targets) != len(set(targets)):
            raise ValueError(f"WNN neighbor list has self or duplicate targets for {source}")
    return np.asarray(
        [[position[target] for target in by_source[source]["neighbor_barcode"]] for source in barcodes],
        dtype=np.int64,
    )


def _label_vector(values, n_cells: int, name: str):
    if values is None:
        return None, f"{name} is unavailable"
    series = pd.Series(values, dtype="object")
    if len(series) != n_cells:
        return None, f"{name} length does not match the WNN representation"
    missing = series.isna() | series.astype(str).str.strip().isin({"", "NA", "nan", "None"})
    if missing.any():
        return None, f"{name} has {int(missing.sum())} missing value(s)"
    vector = series.astype(str).to_numpy()
    levels = np.unique(vector)
    if len(levels) < 2:
        return None, f"{name} has only {len(levels)} level; metric is structurally degenerate"
    if len(levels) >= n_cells:
        return None, f"{name} has one level per cell; silhouette/purity are structurally degenerate"
    return vector, None


def compute_wnn_metrics(coordinates: np.ndarray, neighbors: np.ndarray,
                        independent_labels=None, batch=None) -> dict:
    """Compute graph kNN + WNN-UMAP coordinate metrics with per-metric states."""
    from sklearn.metrics import silhouette_score

    n_cells = coordinates.shape[0]
    if n_cells < MIN_METRIC_CELLS or neighbors.shape[0] != n_cells:
        raise ValueError("metric inputs do not share a valid minimum cell count")
    result = {}

    labels, label_reason = _label_vector(independent_labels, n_cells, "independent label")
    if labels is None:
        result.update(
            label_purity=None,
            label_purity_status="not_evaluable_independent_labels",
            label_purity_reason=label_reason,
            silhouette_label=None,
            silhouette_label_status="not_evaluable_independent_labels",
            silhouette_label_reason=label_reason,
        )
    else:
        result["label_purity"] = float(np.mean([
            np.mean(labels[neighbors[index]] == labels[index]) for index in range(n_cells)
        ]))
        result["label_purity_status"] = "computed"
        result["silhouette_label"] = float((silhouette_score(coordinates, labels) + 1) / 2)
        result["silhouette_label_status"] = "computed"

    batches, batch_reason = _label_vector(batch, n_cells, "batch")
    if batches is None:
        result.update(
            batch_mixing_entropy=None,
            batch_mixing_entropy_status="structurally_degenerate",
            batch_mixing_entropy_reason=batch_reason,
            silhouette_batch=None,
            silhouette_batch_status="structurally_degenerate",
            silhouette_batch_reason=batch_reason,
        )
    else:
        categories = np.unique(batches)
        entropy = []
        for row in neighbors:
            _, counts = np.unique(batches[row], return_counts=True)
            probabilities = counts / counts.sum()
            entropy.append(-np.sum(probabilities * np.log(probabilities)) / np.log(len(categories)))
        result["batch_mixing_entropy"] = float(np.mean(entropy))
        result["batch_mixing_entropy_status"] = "computed"
        result["silhouette_batch"] = float((silhouette_score(coordinates, batches) + 1) / 2)
        result["silhouette_batch_status"] = "computed"
    return result
