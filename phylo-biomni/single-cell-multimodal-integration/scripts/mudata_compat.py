#!/usr/bin/env python3
"""Version-adaptive, explicit MuData construction and round-trip checks."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def _names(values) -> list[str]:
    return [str(value) for value in values]


def configure_explicit_annotation_management(mudata_module) -> bool:
    """Disable the v0.3 legacy implicit annotation pull when the API supports it."""
    setter = getattr(mudata_module, "set_options", None)
    if setter is None:
        return False
    try:
        setter(pull_on_update=False)
    except (AttributeError, TypeError):
        return False
    return True


def construct_mudata(mudata_module, modalities: dict, common_obs_names,
                      shared_obs: pd.DataFrame | None = None):
    """Construct a MuData container without implicit modality-annotation pulls."""
    common = _names(common_obs_names)
    if len(common) != len(set(common)):
        raise ValueError("common MuData observation barcodes must be unique")
    if not modalities:
        raise ValueError("at least one MuData modality is required")
    index_name = (shared_obs.index.name if shared_obs is not None else None) or "barcode"
    for name, modality in modalities.items():
        observed = _names(modality.obs_names)
        if observed != common:
            raise ValueError(
                f"MuData modality {name!r} observations do not match the validated common barcode index"
            )
        # MuData 0.3.10 joins modality and global observations during read-back.
        # Give every copy of the already validated common index one explicit name
        # so pandas cannot reject that join as having no overlapping index names.
        modality.obs_names = pd.Index(common, name=index_name)
        # Real feature-barcode matrices may contain repeated display names (most
        # commonly duplicated gene symbols).  MuData 0.3.2 can write such an
        # object but fail while rebuilding its global var map in read_h5mu.
        # Preserve the source labels explicitly and make only the AnnData index
        # collision-safe before MuData constructs the cross-modality map.
        modality_var_names = getattr(modality, "var_names", None)
        var_names = (
            pd.Index(_names(modality_var_names), name=modality_var_names.name)
            if modality_var_names is not None else None
        )
        if var_names is not None and not var_names.is_unique:
            witness_column = "_original_var_name"
            if witness_column in modality.var.columns:
                raise ValueError(
                    f"MuData modality {name!r} has duplicate feature names and already "
                    f"uses the reserved witness column {witness_column!r}"
                )
            modality.var[witness_column] = var_names.to_numpy(copy=True)
            modality.var_names = var_names
            modality.var_names_make_unique(join="-")
            if not modality.var_names.is_unique:
                raise ValueError(
                    f"MuData modality {name!r} feature names remain non-unique after normalization"
                )

    configure_explicit_annotation_management(mudata_module)
    mdata = mudata_module.MuData(modalities)
    if _names(mdata.obs_names) != common:
        raise ValueError("MuData global observations do not match the validated common barcode index")

    if shared_obs is None:
        shared = pd.DataFrame(index=pd.Index(common, name=index_name))
    else:
        if not shared_obs.index.is_unique:
            raise ValueError("shared MuData observations contain duplicate barcodes")
        missing = [barcode for barcode in common if barcode not in shared_obs.index]
        extra = [str(barcode) for barcode in shared_obs.index if str(barcode) not in set(common)]
        if missing or extra:
            raise ValueError(
                "shared MuData observations do not match the validated common barcode index: "
                f"missing={missing[:5]}, extra={extra[:5]}"
            )
        shared = shared_obs.copy()
        shared.index = shared.index.map(str)
        shared = shared.loc[common]
        shared.index.name = index_name
    mdata.obs = shared
    if _names(mdata.obs_names) != common:
        raise ValueError("explicit MuData shared observations changed the common barcode order")
    return mdata


def write_readback_validate(mudata_module, mdata, path,
                            required_modality_obs_columns: dict[str, list[str]] | None = None):
    """Write/read H5MU and verify shared observations, shapes, and modality metadata."""
    path = Path(path)
    common = _names(mdata.obs_names)
    global_obs = mdata.obs.copy()
    shapes = {name: tuple(modality.shape) for name, modality in mdata.mod.items()}
    columns = required_modality_obs_columns or {
        name: list(modality.obs.columns) for name, modality in mdata.mod.items()
    }
    snapshots = {
        name: modality.obs.loc[:, list(columns.get(name, []))].copy()
        for name, modality in mdata.mod.items()
    }

    mdata.write(path)
    reader = getattr(mudata_module, "read_h5mu", None) or mudata_module.read
    persisted = reader(path)
    if _names(persisted.obs_names) != common:
        raise ValueError("H5MU shared observation barcodes changed during round-trip")
    pd.testing.assert_frame_equal(
        persisted.obs.loc[common, list(global_obs.columns)],
        global_obs.loc[common, list(global_obs.columns)],
        check_dtype=False,
        check_categorical=False,
        check_names=False,
    )
    for name, expected_shape in shapes.items():
        observed = persisted.mod[name]
        if tuple(observed.shape) != expected_shape:
            raise ValueError(
                f"H5MU modality {name!r} shape changed during round-trip: "
                f"expected={expected_shape}, observed={tuple(observed.shape)}"
            )
        if _names(observed.obs_names) != common:
            raise ValueError(f"H5MU modality {name!r} barcode order changed during round-trip")
        required = list(columns.get(name, []))
        missing = [column for column in required if column not in observed.obs.columns]
        if missing:
            raise ValueError(f"H5MU modality {name!r} lost observation metadata: {missing}")
        pd.testing.assert_frame_equal(
            observed.obs.loc[common, required],
            snapshots[name].loc[common, required],
            check_dtype=False,
            check_categorical=False,
            check_names=False,
        )
    return persisted
