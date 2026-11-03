#!/usr/bin/env python3
"""Runtime-component identity and version capture for the DRUG-seq skill.

This module keeps component identity and version capture strictly separate. It never lets the R
interpreter version, or an "installed" boolean, stand in for the DESeq2 package version:

  * component_versions(...) normalizes a captured environment and REFUSES to accept the R version or a
    boolean in the DESeq2 version slot -- the exact conflation this repair prevents.
  * render_environment(...) prints R and DESeq2 as separate components; a missing DESeq2 version reads
    "version unknown", never the R version and never a boolean.
  * de_deseq2.R writes tables/software_versions.csv at the point DESeq2 is used; read_r_provenance /
    software_facts turn that file into the normalized record run_analysis.py puts in the facts payload.

It uses only the standard library, so the validation can run with or without R installed.
"""
from __future__ import annotations

import csv
import pathlib
import re
from typing import Optional

# A dotted numeric version such as 4.4.3 embedded in "R version 4.4.3 (2025-02-28)".
_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")
_UNKNOWN_TOKENS = {"", "na", "n/a", "none", "null", "unknown"}


def _numeric_version(text) -> Optional[str]:
    """Extract a dotted numeric version from a string; None if there is none (bools are never a version)."""
    if text is None or isinstance(text, bool):
        return None
    match = _VERSION_RE.search(str(text))
    return match.group(1) if match else None


def _clean_version(value) -> Optional[str]:
    """Normalize a package-version string; empty/NA-like -> None. A bool is never a version."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("a boolean 'installed' flag is not a package version")
    text = str(value).strip()
    return None if text.lower() in _UNKNOWN_TOKENS else text


def component_versions(r_version, *, deseq2_version=None, deseq2_installed=None) -> dict:
    """Assemble a runtime-component/version record with identity and version kept separate.

    Parameters
    ----------
    r_version
        The R interpreter version (e.g. "R version 4.4.3 (2025-02-28)" or "4.4.3").
    deseq2_version
        The DESeq2 PACKAGE version, queried with packageVersion("DESeq2"). Never the R version and never
        a boolean. None means "not captured".
    deseq2_installed
        Optional boolean; whether DESeq2 is available. A captured version implies True.

    Raises
    ------
    ValueError
        If the DESeq2 version slot is given a boolean, or the R interpreter version -- the exact
        conflation ("DESeq2 4.4.3") this repair exists to prevent.
    """
    r_raw = None if r_version is None else str(r_version).strip()
    r_num = _numeric_version(r_raw)

    if isinstance(deseq2_version, bool):
        raise ValueError("DESeq2 version must be a package version string, not the installed boolean")
    ds_version = _clean_version(deseq2_version)
    if ds_version is not None and r_raw is not None and ds_version in {r_raw, r_num}:
        raise ValueError(
            "DESeq2 package version must not equal the R interpreter version "
            f"({ds_version!r}); query packageVersion('DESeq2') separately"
        )

    if deseq2_installed is None:
        installed = True if ds_version is not None else None
    else:
        installed = bool(deseq2_installed)

    return {
        "r": {"component": "R", "version": r_num, "raw": r_raw},
        "deseq2": {"component": "DESeq2", "installed": installed, "version": ds_version},
    }


def render_environment(record: dict) -> str:
    """One line, R and DESeq2 as SEPARATE components. Never prints the R version as DESeq2's."""
    r = record.get("r", {}) if isinstance(record, dict) else {}
    d = record.get("deseq2", {}) if isinstance(record, dict) else {}

    r_ver = r.get("version") or r.get("raw")
    r_part = f"R {r_ver}" if r_ver else "R (version unknown)"

    d_ver = d.get("version")
    if d_ver:
        d_part = f"DESeq2 {d_ver}"
    elif d.get("installed") is False:
        d_part = "DESeq2 (not installed)"
    elif d.get("installed") is True:
        d_part = "DESeq2 (installed; version unknown)"
    else:
        d_part = "DESeq2 (version unknown)"
    return f"{r_part}; {d_part}"


def read_r_provenance(csv_path) -> dict:
    """Parse tables/software_versions.csv (component,version,installed) into a normalized record."""
    csv_path = pathlib.Path(csv_path)
    r_version = None
    deseq2_version = None
    deseq2_installed = None
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            component = (row.get("component") or "").strip().lower()
            version = (row.get("version") or "").strip()
            installed_raw = (row.get("installed") or "").strip().lower()
            installed = (True if installed_raw in {"true", "1", "yes", "t"}
                         else False if installed_raw in {"false", "0", "no", "f"} else None)
            if component == "r":
                r_version = version or r_version
            elif component == "deseq2":
                deseq2_version = version or None
                deseq2_installed = installed
    return component_versions(r_version, deseq2_version=deseq2_version,
                              deseq2_installed=deseq2_installed)


def software_facts(tables_dir) -> dict:
    """Facts fragment for the run payload: the normalized record plus a rendered one-line sentence.

    Reads <tables_dir>/software_versions.csv when present (written by de_deseq2.R at the point DESeq2 is
    used). If absent (e.g. a not_computable stop before DESeq2 ran), returns an unknown-but-valid record
    rather than inventing a version.
    """
    tables_dir = pathlib.Path(tables_dir)
    csv_path = tables_dir / "software_versions.csv"
    record = (read_r_provenance(csv_path) if csv_path.is_file()
              else component_versions(None, deseq2_version=None, deseq2_installed=None))
    return {"software": record, "sentence": render_environment(record)}
