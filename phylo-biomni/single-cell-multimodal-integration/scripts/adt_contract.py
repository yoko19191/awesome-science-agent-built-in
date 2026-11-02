#!/usr/bin/env python3
"""Validation for the normalized ADT layer before integrated-object delivery."""

from __future__ import annotations

import numpy as np


def canonical_adt_feature_name(feature: str) -> str:
    """Return the shared R/Python spelling for one 10x antibody feature."""
    canonical = str(feature).strip().replace("_", "-")
    if not canonical:
        raise ValueError("ADT feature names must be nonempty")
    return canonical


def canonical_adt_feature_names(features, *, context: str = "ADT feature panel") -> list[str]:
    """Canonicalize names and reject aliases that would destroy one-to-one alignment."""
    canonical = [canonical_adt_feature_name(feature) for feature in features]
    counts = {feature: canonical.count(feature) for feature in set(canonical)}
    collisions = sorted(feature for feature, count in counts.items() if count > 1)
    if collisions:
        raise ValueError(
            f"canonical ADT feature collision in {context}: {collisions[:5]}"
        )
    return canonical


def validated_normalized_adt(frame, barcodes: list[str], features: list[str]) -> np.ndarray:
    """Return an aligned nonempty ADT matrix or raise instead of zero-filling gaps."""
    if not frame.index.is_unique or not frame.columns.is_unique:
        raise ValueError("normalized ADT export has duplicate barcodes or feature names")
    frame = frame.copy()
    frame.columns = canonical_adt_feature_names(
        frame.columns, context="normalized ADT export"
    )
    features = canonical_adt_feature_names(features, context="integrated ADT panel")
    missing_barcodes = [barcode for barcode in barcodes if barcode not in frame.index]
    missing_features = [feature for feature in features if feature not in frame.columns]
    if missing_barcodes or missing_features:
        raise ValueError(
            "normalized ADT export does not align to the integrated object: "
            f"missing_barcodes={missing_barcodes[:5]}, missing_features={missing_features[:5]}"
        )
    values = frame.loc[barcodes, features].to_numpy(dtype=np.float32)
    if not np.isfinite(values).all():
        raise ValueError("normalized ADT export contains non-finite values")
    if not np.any(np.abs(values) > np.finfo(np.float32).eps):
        raise ValueError("normalized ADT export is entirely zero; refusing delivery")
    return values


def load_validated_normalized_adt(path: str, barcodes: list[str],
                                  features: list[str]) -> np.ndarray:
    """Read the exact R CSV contract (explicit barcode column) and validate alignment."""
    import pandas as pd

    frame = pd.read_csv(path)
    if "barcode" not in frame.columns:
        raise ValueError("normalized ADT export lacks the required explicit barcode column")
    frame = frame.set_index("barcode")
    return validated_normalized_adt(frame, barcodes, features)


def assert_h5mu_normalized_adt(mdata, expected: np.ndarray,
                               barcodes: list[str], features: list[str]) -> None:
    """Verify a re-opened H5MU protein layer matches the gated normalized source."""
    protein = mdata.mod["prot"]
    if list(map(str, protein.obs_names)) != list(map(str, barcodes)):
        raise ValueError("H5MU protein barcodes do not match the normalized ADT source")
    if list(map(str, protein.var_names)) != list(map(str, features)):
        raise ValueError("H5MU protein features do not match the normalized ADT source")
    actual = np.asarray(protein.layers["normalized"], dtype=np.float32)
    if not np.array_equal(actual, np.asarray(expected, dtype=np.float32)):
        raise ValueError("H5MU normalized protein layer differs from the gated ADT source values")
    if not np.any(np.abs(actual) > np.finfo(np.float32).eps):
        raise ValueError("H5MU normalized protein layer is entirely zero")
