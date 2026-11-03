"""Production loaders for caller-selected Visium inputs."""

from pathlib import Path
from typing import Optional, Union


def _read_spatial_image(path: Path):
    """Read a Space Ranger tissue image from its selected path."""
    import numpy as np

    try:
        from matplotlib.image import imread
        image = imread(path)
    except Exception:
        import tifffile
        image = tifffile.imread(path)

    image = np.asarray(image)
    if np.issubdtype(image.dtype, np.integer):
        image = image.astype(np.float32) / np.iinfo(image.dtype).max
    return image


def _read_tissue_positions(spatial_dir: Path):
    """Read current, legacy, or parquet Space Ranger tissue positions."""
    import pandas as pd

    current = spatial_dir / "tissue_positions.csv"
    legacy = spatial_dir / "tissue_positions_list.csv"
    parquet = spatial_dir / "tissue_positions.parquet"
    if current.is_file():
        positions = pd.read_csv(current)
    elif legacy.is_file():
        positions = pd.read_csv(legacy, header=None)
        positions.columns = [
            "barcode", "in_tissue", "array_row", "array_col",
            "pxl_row_in_fullres", "pxl_col_in_fullres",
        ]
    elif parquet.is_file():
        positions = pd.read_parquet(parquet)
    else:
        raise FileNotFoundError(
            f"Space Ranger input is missing tissue positions under {spatial_dir}"
        )

    if "barcode" not in positions.columns:
        if positions.index.name == "barcode":
            positions = positions.reset_index()
        else:
            raise ValueError("tissue positions must contain a barcode column")
    required = {
        "barcode", "in_tissue", "array_row", "array_col",
        "pxl_row_in_fullres", "pxl_col_in_fullres",
    }
    missing = sorted(required.difference(positions.columns))
    if missing:
        raise ValueError(f"tissue positions are missing required columns: {missing}")
    if positions["barcode"].duplicated().any():
        raise ValueError("tissue positions contain duplicate barcodes")
    return positions.set_index("barcode")


def _read_visium_matrix_directory(
    path: Path,
    library_id: Optional[str] = None,
):
    """Load a caller-selected matrix-directory Space Ranger bundle."""
    import json
    import scanpy as sc

    matrix_dir = path / "filtered_feature_bc_matrix"
    if not matrix_dir.is_dir():
        matrix_dir = path / "raw_feature_bc_matrix"
    if not matrix_dir.is_dir():
        raise FileNotFoundError(
            "Space Ranger directory contains neither filtered_feature_bc_matrix "
            "nor raw_feature_bc_matrix"
        )

    spatial_dir = path / "spatial"
    scalefactors_path = spatial_dir / "scalefactors_json.json"
    if not scalefactors_path.is_file():
        raise FileNotFoundError(
            f"Space Ranger matrix-directory input is missing {scalefactors_path}"
        )

    adata = sc.read_10x_mtx(
        matrix_dir,
        var_names="gene_symbols",
        make_unique=True,
        gex_only=True,
    )
    positions = _read_tissue_positions(spatial_dir)
    adata.obs = adata.obs.join(positions, how="left")
    coordinate_columns = ["pxl_col_in_fullres", "pxl_row_in_fullres"]
    if adata.obs[coordinate_columns].isna().any(axis=None):
        missing_barcodes = adata.obs_names[
            adata.obs[coordinate_columns].isna().any(axis=1)
        ].tolist()
        raise ValueError(
            "tissue positions do not cover every matrix barcode; missing "
            f"{missing_barcodes[:5]}"
        )
    adata.obsm["spatial"] = adata.obs[coordinate_columns].to_numpy(dtype=float)
    adata.obs.drop(columns=coordinate_columns, inplace=True)

    resolved_library_id = library_id or path.name
    images = {}
    for resolution in ("hires", "lowres"):
        image_path = spatial_dir / f"tissue_{resolution}_image.png"
        if image_path.is_file():
            images[resolution] = _read_spatial_image(image_path)
    adata.uns["spatial"] = {
        resolved_library_id: {
            "images": images,
            "scalefactors": json.loads(scalefactors_path.read_text(encoding="utf-8")),
            "metadata": {},
        }
    }
    return adata


def load_visium_data(
    path: Union[str, Path],
    library_id: Optional[str] = None,
):
    """Load caller-selected AnnData, 10x HDF5, or Space Ranger Visium input."""
    import scanpy as sc

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    if path.suffix == ".h5ad":
        adata = sc.read_h5ad(path)
    elif path.suffix == ".h5":
        adata = sc.read_10x_h5(path)
    elif path.is_dir():
        if (path / "filtered_feature_bc_matrix.h5").is_file():
            adata = sc.read_visium(path, library_id=library_id)
        else:
            adata = _read_visium_matrix_directory(path, library_id=library_id)
    else:
        raise ValueError(
            f"unsupported Visium input format {path.suffix!r}; expected .h5ad, .h5, "
            "or a Space Ranger output directory"
        )
    adata.var_names_make_unique()
    return adata
