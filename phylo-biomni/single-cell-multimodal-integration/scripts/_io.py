#!/usr/bin/env python3
"""
_io.py — bundled I/O helper for single-cell-multimodal-integration.

Two responsibilities, both keeping HDF5 reading on the Python side so the R
scripts (dsb, Seurat WNN, presto) do not depend on a fragile R HDF5 reader:

  --probe <dir>            emit routing signals JSON (feature types, pairing, isotypes)
  --ingest <dir> --out <w> read the CITE-seq filtered+raw h5, split RNA/ADT, and write
                           MatrixMarket files (for R) + a MuData .h5mu (for Python) +
                           ingest_meta.json (assay identity, isotype match, counts).

10x CellRanger-v3 layout: /matrix/{data,indices,indptr,shape,barcodes,
features/{id,name,feature_type}}.
"""
import argparse, gzip, json, os, sys
from dataclasses import dataclass
import numpy as np
import scipy.sparse as sp
import scipy.io as sio
import h5py

from adt_contract import canonical_adt_feature_names


DEFAULT_BACKGROUND_MIN_ADT_COUNTS = 10
DEFAULT_BACKGROUND_MAX_ADT_COUNTS = 200
DEFAULT_BACKGROUND_MAX_RNA_COUNTS = 200
DEFAULT_BACKGROUND_MIN_DROPLETS = 10


class BackgroundSelectionError(ValueError):
    """The raw matrix cannot supply a defensible DSB ambient population."""


@dataclass(frozen=True)
class DsbBackgroundThresholds:
    """Capture/depth bounds for raw, non-cell DSB background droplets."""

    min_adt_counts: int = DEFAULT_BACKGROUND_MIN_ADT_COUNTS
    max_adt_counts: int = DEFAULT_BACKGROUND_MAX_ADT_COUNTS
    max_rna_counts: int = DEFAULT_BACKGROUND_MAX_RNA_COUNTS
    min_droplets: int = DEFAULT_BACKGROUND_MIN_DROPLETS

    def validate(self) -> None:
        if self.min_adt_counts < 1:
            raise BackgroundSelectionError("DSB background min_adt_counts must be at least 1")
        if self.max_adt_counts < self.min_adt_counts:
            raise BackgroundSelectionError(
                "DSB background max_adt_counts must be >= min_adt_counts"
            )
        if self.max_rna_counts < 0:
            raise BackgroundSelectionError("DSB background max_rna_counts must be non-negative")
        if self.min_droplets < 1:
            raise BackgroundSelectionError("DSB background min_droplets must be at least 1")

def _dec(a):
    return [x.decode() if isinstance(x, (bytes, bytearray)) else str(x) for x in a]

def read_10x(path):
    """Return (csc matrix features x cells, barcodes, feat_names, feat_types)."""
    with h5py.File(path, "r") as f:
        g = f["matrix"]
        data = g["data"][:]; indices = g["indices"][:]; indptr = g["indptr"][:]
        shape = tuple(int(x) for x in g["shape"][:])
        m = sp.csc_matrix((data, indices, indptr), shape=shape)
        barcodes = _dec(g["barcodes"][:])
        feat = g["features"]
        names = _dec(feat["name"][:]) if "name" in feat else _dec(feat["id"][:])
        ftypes = _dec(feat["feature_type"][:]) if "feature_type" in feat else ["Gene Expression"] * shape[0]
    return m, barcodes, names, ftypes

def read_metadata(d):
    p = os.path.join(d, "metadata.csv")
    if not os.path.exists(p):
        return {}
    import csv
    with open(p) as fh:
        rows = list(csv.DictReader(fh))
    return {"rows": rows,
            "assay": sorted({r.get("assay", "") for r in rows if r.get("assay")}),
            "regime": sorted({r.get("regime", "") for r in rows if r.get("regime")})}

def frag_barcodes(fp, cap=200000):
    s = set()
    with gzip.open(fp, "rt") as fh:
        for i, ln in enumerate(fh):
            if ln.startswith("#"):
                continue
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 4:
                s.add(p[3])
            if i > cap:
                break
    return s

def isotype_names(names):
    return [n for n in names if ("isotype" in n.lower()) or n.lower().startswith("igg")]


def select_dsb_background(
    raw_matrix: sp.csc_matrix,
    raw_barcodes: list[str],
    raw_feature_types: list[str],
    filtered_barcodes: list[str],
    metadata_barcodes: list[str],
    thresholds: DsbBackgroundThresholds,
) -> tuple[sp.csc_matrix, list[str], dict[str, object]]:
    """Select captured low-depth non-cell droplets for DSB, never raw-minus-QC cells.

    Cell Ranger's filtered matrix is the authoritative called-cell population. Non-empty barcode
    identities supplied by per-cell metadata are an additional semantic exclusion. The remaining
    raw barcodes must show ADT capture while staying below declared RNA and ADT depth ceilings.
    """
    thresholds.validate()
    matrix = sp.csc_matrix(raw_matrix)
    if matrix.shape[1] != len(raw_barcodes):
        raise BackgroundSelectionError("raw matrix barcode axis does not match raw_barcodes")
    if matrix.shape[0] != len(raw_feature_types):
        raise BackgroundSelectionError("raw matrix feature axis does not match raw_feature_types")
    if len(set(raw_barcodes)) != len(raw_barcodes):
        raise BackgroundSelectionError("raw matrix contains duplicate barcodes")
    if not np.isfinite(matrix.data).all() or np.any(matrix.data < 0):
        raise BackgroundSelectionError("raw matrix contains invalid counts")

    raw_barcode_set = set(raw_barcodes)
    filtered_set = {barcode for barcode in filtered_barcodes if barcode}
    missing_filtered = sorted(filtered_set - raw_barcode_set)
    if missing_filtered:
        preview = ", ".join(missing_filtered[:5])
        raise BackgroundSelectionError(
            f"raw matrix is missing {len(missing_filtered)} filtered/cell-called barcode(s): {preview}"
        )
    metadata_set = {barcode for barcode in metadata_barcodes if barcode}
    missing_metadata = sorted(metadata_set - raw_barcode_set)
    if missing_metadata:
        preview = ", ".join(missing_metadata[:5])
        raise BackgroundSelectionError(
            f"raw matrix is missing {len(missing_metadata)} metadata-declared cell barcode(s): "
            f"{preview}"
        )
    semantic_cells = filtered_set | metadata_set

    feature_types = np.asarray(raw_feature_types, dtype=object)
    adt_rows = np.flatnonzero(feature_types == "Antibody Capture")
    rna_rows = np.flatnonzero(feature_types == "Gene Expression")
    if adt_rows.size == 0 or rna_rows.size == 0:
        raise BackgroundSelectionError(
            "raw matrix must contain Gene Expression and Antibody Capture features for DSB"
        )

    adt_totals = np.asarray(matrix[adt_rows, :].sum(axis=0)).ravel()
    rna_totals = np.asarray(matrix[rna_rows, :].sum(axis=0)).ravel()
    non_cell = np.fromiter(
        (barcode not in semantic_cells for barcode in raw_barcodes),
        dtype=bool,
        count=len(raw_barcodes),
    )
    captured = adt_totals >= thresholds.min_adt_counts
    adt_depth_ok = adt_totals <= thresholds.max_adt_counts
    rna_depth_ok = rna_totals <= thresholds.max_rna_counts
    selected_mask = non_cell & captured & adt_depth_ok & rna_depth_ok
    selected_indices = np.flatnonzero(selected_mask)

    diagnostics: dict[str, object] = {
        "selection_basis": "raw_non_cell_barcodes_with_declared_rna_adt_depth_bounds",
        "raw_barcode_count": len(raw_barcodes),
        "filtered_cell_barcode_count": len(filtered_set),
        "metadata_cell_barcode_count": len(metadata_set),
        "metadata_only_cell_exclusions": len((metadata_set - filtered_set) & raw_barcode_set),
        "candidate_non_cell_barcode_count": int(non_cell.sum()),
        "rejected_below_min_adt_count": int((non_cell & ~captured).sum()),
        "rejected_above_max_adt_count": int((non_cell & ~adt_depth_ok).sum()),
        "rejected_above_max_rna_count": int((non_cell & ~rna_depth_ok).sum()),
        "selected_background_droplet_count": int(selected_indices.size),
        "thresholds": {
            "min_adt_counts": thresholds.min_adt_counts,
            "max_adt_counts": thresholds.max_adt_counts,
            "max_rna_counts": thresholds.max_rna_counts,
            "min_droplets": thresholds.min_droplets,
        },
    }
    if selected_indices.size < thresholds.min_droplets:
        raise BackgroundSelectionError(
            "no valid DSB background is available: "
            f"selected {selected_indices.size} captured low-depth non-cell droplets, "
            f"require >= {thresholds.min_droplets}; diagnostics={json.dumps(diagnostics, sort_keys=True)}"
        )

    selected_barcodes = [raw_barcodes[index] for index in selected_indices]
    leaked_cells = semantic_cells.intersection(selected_barcodes)
    if leaked_cells:
        raise BackgroundSelectionError("DSB background selection retained a called/metadata cell")
    selected_adt = sp.csc_matrix(matrix[adt_rows, :][:, selected_indices])
    selected_totals = adt_totals[selected_indices]
    diagnostics["selected_adt_count_min"] = float(selected_totals.min())
    diagnostics["selected_adt_count_median"] = float(np.median(selected_totals))
    diagnostics["selected_adt_count_max"] = float(selected_totals.max())
    diagnostics["called_cell_overlap_count"] = 0
    return selected_adt, selected_barcodes, diagnostics

def probe(d):
    comb = os.path.join(d, "filtered_feature_bc_matrix.h5")
    rna_only = os.path.join(d, "rna_matrix.h5")
    frag = os.path.join(d, "atac_fragments.tsv.gz")
    has_comb = os.path.exists(comb); has_rna_only = os.path.exists(rna_only); has_frag = os.path.exists(frag)
    meta = read_metadata(d)
    sig = {
        "has_combined_matrix": has_comb,
        "has_rna_only_matrix": has_rna_only,
        "has_fragments": has_frag,
        "separate_rna_atac": bool(has_rna_only and has_frag and not has_comb),
        "feature_types": [],
        "isotype_controls_present": False,
        "declared_regime": (meta.get("assay") or [""])[0] if meta else "",
        "regime_hint": (meta.get("regime") or [""])[0] if meta else "",
        "barcode_overlap": 0.0,
    }
    src = comb if has_comb else (rna_only if has_rna_only else None)
    names = ftypes = None; barcodes = None
    if src:
        _, barcodes, names, ftypes = read_10x(src)
        sig["feature_types"] = sorted(set(ftypes))
        adt_names = [n for n, t in zip(names, ftypes) if t == "Antibody Capture"]
        sig["isotype_controls_present"] = len(isotype_names(adt_names)) > 0
    if has_rna_only and has_frag and not has_comb and barcodes is not None:
        fb = frag_barcodes(frag)
        if fb:
            sig["barcode_overlap"] = round(float(len(set(barcodes) & fb) / max(1, len(set(barcodes)))), 4)
    return sig

def _wmtx(mat, path):
    """Write features x cells sparse to MatrixMarket, gzipped."""
    raw = path[:-3] if path.endswith(".gz") else path
    sio.mmwrite(raw, sp.csr_matrix(mat).astype(np.float64))
    with open(raw, "rb") as fi, gzip.open(path, "wb") as fo:
        fo.writelines(fi)
    os.remove(raw)

def _wlines(items, path):
    with gzip.open(path, "wt") as f:
        f.write("\n".join(items) + "\n")

def ingest(
    d,
    out,
    isotypes=None,
    *,
    adt_normalization="dsb",
    background_thresholds=None,
):
    os.makedirs(out, exist_ok=True)
    comb = os.path.join(d, "filtered_feature_bc_matrix.h5")
    if not os.path.exists(comb):
        raise SystemExit(f"ingest: no filtered_feature_bc_matrix.h5 in {d}")
    m, barcodes, names, ftypes = read_10x(comb)
    ftypes = np.array(ftypes)
    gex = np.where(ftypes == "Gene Expression")[0]
    adt = np.where(ftypes == "Antibody Capture")[0]
    observed = sorted(set(ftypes.tolist()))
    declared = (read_metadata(d).get("assay") or [""])[0]
    is_citeseq = ("Gene Expression" in observed and "Antibody Capture" in observed and "Peaks" not in observed)
    identity_violations = 1 if (declared == "cite_seq" and not is_citeseq) else 0
    rna = m[gex, :]; adt_mat = m[adt, :]
    rna_names = [names[i] for i in gex]
    adt_names = canonical_adt_feature_names(
        [names[i] for i in adt], context="filtered 10x Antibody Capture panel"
    )
    requested_isotypes = canonical_adt_feature_names(
        isotypes or [], context="requested isotype controls"
    )
    iso = [a for a in requested_isotypes if a in adt_names] or isotype_names(adt_names)
    raw_path = os.path.join(d, "raw_feature_bc_matrix.h5")
    has_raw = os.path.exists(raw_path); empty_n = 0
    background_diagnostics = None
    ig = os.path.join(out, "ingest"); os.makedirs(ig, exist_ok=True)
    _wmtx(rna, os.path.join(ig, "rna.mtx.gz")); _wlines(rna_names, os.path.join(ig, "rna_features.tsv.gz"))
    _wmtx(adt_mat, os.path.join(ig, "adt.mtx.gz")); _wlines(adt_names, os.path.join(ig, "adt_features.tsv.gz"))
    _wlines(barcodes, os.path.join(ig, "barcodes.tsv.gz"))
    if adt_normalization == "dsb" and not has_raw:
        raise BackgroundSelectionError(
            "DSB normalization was requested but raw_feature_bc_matrix.h5 is unavailable"
        )
    if has_raw and adt_normalization == "dsb":
        rm, rbc, rnames, rft = read_10x(raw_path)
        metadata_rows = read_metadata(d).get("rows", [])
        metadata_barcodes = [str(row.get("barcode", "")).strip() for row in metadata_rows]
        raw_adt, background_barcodes, background_diagnostics = select_dsb_background(
            rm,
            rbc,
            rft,
            barcodes,
            metadata_barcodes,
            background_thresholds or DsbBackgroundThresholds(),
        )
        rft_array = np.asarray(rft, dtype=object)
        radt = np.flatnonzero(rft_array == "Antibody Capture")
        raw_adt_names = canonical_adt_feature_names(
            [rnames[i] for i in radt], context="raw 10x Antibody Capture panel"
        )
        _wmtx(raw_adt, os.path.join(ig, "adt_raw.mtx.gz"))
        _wlines(raw_adt_names, os.path.join(ig, "adt_raw_features.tsv.gz"))
        _wlines(background_barcodes, os.path.join(ig, "adt_raw_barcodes.tsv.gz"))
        empty_n = int(raw_adt.shape[1])
    try:
        import anndata as ad, mudata as md, pandas as pd
        rna_ad = ad.AnnData(X=sp.csr_matrix(rna.T.astype(np.float32)))
        rna_ad.var_names = rna_names; rna_ad.obs_names = barcodes
        prot_ad = ad.AnnData(X=sp.csr_matrix(adt_mat.T.astype(np.float32)))
        prot_ad.var_names = adt_names; prot_ad.obs_names = barcodes
        meta = read_metadata(d).get("rows", [])
        if meta:
            mdf = pd.DataFrame(meta).set_index("barcode").reindex(barcodes)
            for c in mdf.columns:
                rna_ad.obs[c] = mdf[c].values
                prot_ad.obs[c] = mdf[c].values
        from mudata_compat import construct_mudata, write_readback_validate
        shared_obs = mdf if meta else pd.DataFrame(index=pd.Index(barcodes, name="barcode"))
        mdata = construct_mudata(
            md, {"rna": rna_ad, "prot": prot_ad}, barcodes, shared_obs=shared_obs
        )
        required = {"rna": list(rna_ad.obs.columns), "prot": list(prot_ad.obs.columns)}
        write_readback_validate(
            md, mdata, os.path.join(ig, "mdata_raw.h5mu"),
            required_modality_obs_columns=required,
        )
    except Exception as e:
        sys.stderr.write(f"[_io] MuData assembly skipped: {e}\n")
    meta_out = {
        "n_cells": int(rna.shape[1]), "n_genes": int(rna.shape[0]), "n_adt": int(adt_mat.shape[0]),
        "adt_names": adt_names, "isotype_controls": iso, "declared_regime": declared,
        "observed_feature_types": observed, "assay_identity_violations": int(identity_violations),
        "has_raw": bool(has_raw), "empty_droplet_count": int(empty_n),
        "dsb_background_selection": background_diagnostics,
    }
    with open(os.path.join(ig, "ingest_meta.json"), "w") as f:
        json.dump(meta_out, f, indent=2)
    print(json.dumps({"ingest_dir": ig, **{k: meta_out[k] for k in
          ("n_cells", "n_genes", "n_adt", "isotype_controls", "assay_identity_violations", "empty_droplet_count")}}))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe"); ap.add_argument("--ingest"); ap.add_argument("--out")
    ap.add_argument("--isotypes", default="")
    ap.add_argument("--adt-normalization", choices=("dsb", "clr"), default="dsb")
    ap.add_argument("--background-min-adt-counts", type=int, default=DEFAULT_BACKGROUND_MIN_ADT_COUNTS)
    ap.add_argument("--background-max-adt-counts", type=int, default=DEFAULT_BACKGROUND_MAX_ADT_COUNTS)
    ap.add_argument("--background-max-rna-counts", type=int, default=DEFAULT_BACKGROUND_MAX_RNA_COUNTS)
    ap.add_argument("--background-min-droplets", type=int, default=DEFAULT_BACKGROUND_MIN_DROPLETS)
    a = ap.parse_args()
    if a.probe:
        print(json.dumps(probe(a.probe)))
    elif a.ingest:
        iso = [x for x in a.isotypes.split(",") if x.strip()]
        ingest(
            a.ingest,
            a.out or "/workspace/s38_run",
            iso,
            adt_normalization=a.adt_normalization,
            background_thresholds=DsbBackgroundThresholds(
                min_adt_counts=a.background_min_adt_counts,
                max_adt_counts=a.background_max_adt_counts,
                max_rna_counts=a.background_max_rna_counts,
                min_droplets=a.background_min_droplets,
            ),
        )
    else:
        ap.error("need --probe or --ingest")

if __name__ == "__main__":
    main()
