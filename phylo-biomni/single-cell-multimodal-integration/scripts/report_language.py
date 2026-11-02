#!/usr/bin/env python3
"""Scientific wording gates shared by figure finalization and report assembly."""

from __future__ import annotations

import math


MEASURED_ISOTYPE_STATE = "measured_no_universal_zero_threshold"
NO_ISOTYPE_STATE = "not_evaluable_no_isotype_controls"
DSB_BACKGROUND_SELECTION_BASIS = (
    "raw_non_cell_barcodes_with_declared_rna_adt_depth_bounds"
)


def _finite_number(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def validate_adt_normalization_status(status: dict) -> None:
    """Reject report inputs that overstate or omit the observed isotype diagnostic."""
    method = str(status.get("method", ""))
    if method.startswith("dsb"):
        background = status.get("background_selection")
        if not isinstance(background, dict):
            raise ValueError("DSB reporting requires recorded background-selection diagnostics")
        if background.get("selection_basis") != DSB_BACKGROUND_SELECTION_BASIS:
            raise ValueError("DSB reporting requires the package's semantic background-selection basis")
        try:
            selected = int(background["selected_background_droplet_count"])
            reported = int(status["n_empty_droplets"])
            minimum = int((background.get("thresholds") or {})["min_droplets"])
            overlap = int(background["called_cell_overlap_count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("DSB background-selection diagnostics are incomplete") from exc
        if selected != reported or selected < minimum or overlap != 0:
            raise ValueError(
                "DSB reporting requires a sufficient background with zero called-cell overlap "
                "and consistent selected-droplet counts"
            )
    controls = status.get("isotype_controls") or []
    state = status.get("isotype_background_interpretation")
    median = status.get("isotype_background_median")
    if controls:
        if state != MEASURED_ISOTYPE_STATE or not _finite_number(median):
            raise ValueError(
                "ADT normalization with named controls requires a finite measured isotype-control "
                f"median and interpretation={MEASURED_ISOTYPE_STATE!r}"
            )
    elif state != NO_ISOTYPE_STATE:
        raise ValueError(
            "ADT normalization without named controls must mark isotype background not evaluable"
        )


def adt_normalization_sentence(status: dict) -> str:
    validate_adt_normalization_status(status)
    method = status.get("method", "NA")
    reason = status.get("reason", "")
    if status.get("isotype_controls"):
        median = float(status["isotype_background_median"])
        return (
            f"Antibody-derived tags were normalized with <b>{method}</b> ({reason}); the measured "
            f"post-normalization isotype-control median was {median:g}. This is a diagnostic on "
            "the reported normalized-output scale; no universal near-zero acceptance threshold "
            "is applied or inferred [2]."
        )
    return (
        f"Antibody-derived tags were normalized with <b>{method}</b> ({reason}). No isotype-control "
        "background median was evaluable because no named control was present; no denoising claim "
        "is inferred from that missing diagnostic [2]."
    )


def adt_figure_caption(status: dict) -> str:
    validate_adt_normalization_status(status)
    method = status.get("method", "NA")
    marker = status.get("figure_marker")
    marker_phrase = f" and selected marker {marker}" if marker else ""
    if status.get("isotype_controls"):
        median = float(status["isotype_background_median"])
        return (
            f"ADT signal before versus after {method} normalization for the isotype control"
            f"{marker_phrase}; the measured post-normalization isotype median is {median:g}."
        )
    return (
        f"ADT signal before versus after {method} normalization{marker_phrase}; no isotype-control "
        "median was evaluable because no named control was present."
    )
