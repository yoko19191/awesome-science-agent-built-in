#!/usr/bin/env python3
"""Deterministic evidence-bearing visuals for Open Targets outputs (OT-11).

Two renderers:
  1. Target-ranking horizontal bar chart — top-N targets by overall association score.
  2. Datatype-score heatmap — per-datatype contribution for the top-N targets.

Both read validated facts/tables at build time and are deterministic derivatives
of gated facts.  No random elements, no generative decoration containing
scientific values.  Saves PNG + SVG.

A payload sidecar (figure_payloads.json) records the exact data each figure was
built from, so a cross-artifact check can verify the visual payload equals the
source facts exactly.

Usage:
    python visualize.py --facts report_facts.json --outdir /mnt/results
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams as rc_params
import numpy as np


def _nan_to_none(value):
    """Convert NaN/inf floats to None for strict RFC JSON (OT-R2-08).

    Missing datatype contributions are serialized as JSON ``null`` and remain
    visually distinct from numeric zero in the heatmap.
    """
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _matrix_to_strict(matrix: np.ndarray) -> list:
    """Convert a numpy matrix to a nested list with NaN/inf replaced by None."""
    return [[_nan_to_none(v) for v in row] for row in matrix.tolist()]

# Phylo palette (functional colours only — chart accents are separate)
PHYLO_GOLD = "#D4A04A"
PHYLO_OFF_WHITE = "#FAF9F3"
PHYLO_BLUE = "#0279EE"
PHYLO_GREEN = "#75A025"
PHYLO_ORANGE = "#FF9400"
PHYLO_PINK = "#FD9BED"
PHYLO_LIME = "#E9ED4C"
PHYLO_BLACK = "#000000"
HEADING_COLOR = "#111111"
BODY_TEXT = "#2C2A26"
MUTED_TEXT = "#8A8378"
TABLE_ALT_ROW = "#F9F7F3"
TABLE_BORDER = "#D5CFC5"

# Chart accent palette (colorblind-friendly ordering)
CHART_ACCENTS = [PHYLO_GOLD, PHYLO_BLUE, PHYLO_GREEN, PHYLO_ORANGE, PHYLO_PINK, PHYLO_LIME]

# Font setup
rc_params["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
rc_params["svg.fonttype"] = "none"  # keep SVG text editable


def _load_facts(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def render_target_ranking(facts: dict, outdir: pathlib.Path) -> dict:
    """Horizontal bar chart of top-N targets by overall association score."""
    targets = facts.get("top_targets", [])
    if not targets:
        return {"file": None, "reason": "no top_targets in facts"}

    n = len(targets)
    symbols = [t.get("approved_symbol", t.get("target_id", "?")) for t in targets]
    scores = [t.get("overall_association_score", 0.0) for t in targets]

    fig, ax = plt.subplots(figsize=(7, max(3.5, 0.45 * n + 1.2)))
    y_pos = np.arange(n)
    bars = ax.barh(y_pos, scores, color=PHYLO_GOLD, edgecolor=HEADING_COLOR, linewidth=0.5)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(symbols, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("Overall association score (0–1)", fontsize=10, color=BODY_TEXT)
    ax.set_xlim(0, 1)
    ax.set_title(
        f"Top {n} targets associated with {facts.get('disease_name', 'disease')}",
        fontsize=11, color=HEADING_COLOR, fontweight="bold",
    )
    ax.set_facecolor(PHYLO_OFF_WHITE)
    fig.patch.set_facecolor("white")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(TABLE_BORDER)
    ax.spines["bottom"].set_color(TABLE_BORDER)
    ax.tick_params(colors=MUTED_TEXT)

    # Annotate score values on bars
    for bar, score in zip(bars, scores):
        ax.text(bar.get_width() + 0.01, bar.get_y() + bar.get_height() / 2,
                f"{score:.3f}", va="center", fontsize=8, color=BODY_TEXT)

    fig.tight_layout()
    png_path = outdir / "figures" / "figure_1_target_ranking.png"
    svg_path = outdir / "figures" / "figure_1_target_ranking.svg"
    png_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(png_path), dpi=150, facecolor="white")
    fig.savefig(str(svg_path), format="svg", facecolor="white")
    plt.close(fig)

    return {
        "file": "figures/figure_1_target_ranking.png",
        "svg": "figures/figure_1_target_ranking.svg",
        "caption": (
            f"Top {n} targets ranked by overall association score for "
            f"{facts.get('disease_name', 'the queried disease')} "
            f"(Open Targets release {facts.get('release_label', 'unknown')}). "
            f"Scores are integrated prioritization metrics (0–1), not effect estimates."
        ),
        "payload": {"symbols": symbols, "scores": scores},
    }


def render_datatype_heatmap(facts: dict, outdir: pathlib.Path) -> dict:
    """Heatmap of per-datatype scores for the top-N targets."""
    targets = facts.get("top_targets", [])
    if not targets:
        return {"file": None, "reason": "no top_targets in facts"}

    # Collect all datatype IDs across targets
    all_dt_ids = set()
    for t in targets:
        for ds in t.get("datatype_scores", []):
            all_dt_ids.add(ds.get("datatype_id", ""))
    dt_ids = sorted(all_dt_ids)
    if not dt_ids:
        return {"file": None, "reason": "no datatype_scores in facts"}

    n_targets = len(targets)
    n_dts = len(dt_ids)
    matrix = np.full((n_targets, n_dts), np.nan)
    for i, t in enumerate(targets):
        for ds in t.get("datatype_scores", []):
            dt_id = ds.get("datatype_id", "")
            if dt_id in dt_ids:
                j = dt_ids.index(dt_id)
                matrix[i, j] = ds.get("score", 0.0)

    fig, ax = plt.subplots(figsize=(max(6, 0.9 * n_dts + 2.5), max(4, 0.5 * n_targets + 1.8)))
    im = ax.imshow(matrix, aspect="auto", cmap="YlOrRd", vmin=0, vmax=1)
    ax.set_xticks(np.arange(n_dts))
    # Wrap long datatype labels to avoid crowding/truncation (OT-R2-05)
    wrapped_labels = []
    for dt_id in dt_ids:
        if len(dt_id) > 18:
            mid = len(dt_id) // 2
            # Find a natural break point near the middle
            for sep_pos in range(mid, min(mid + 6, len(dt_id))):
                if dt_id[sep_pos] in ("_", "-", " "):
                    wrapped_labels.append(dt_id[:sep_pos] + "\n" + dt_id[sep_pos + 1:])
                    break
            else:
                wrapped_labels.append(dt_id[:mid] + "\n" + dt_id[mid:])
        else:
            wrapped_labels.append(dt_id)
    ax.set_xticklabels(wrapped_labels, rotation=45, ha="right", fontsize=7, color=BODY_TEXT)
    ax.set_yticks(np.arange(n_targets))
    ax.set_yticklabels(
        [t.get("approved_symbol", t.get("target_id", "?")) for t in targets],
        fontsize=9, color=BODY_TEXT,
    )
    ax.set_title(
        f"Datatype contribution scores — {facts.get('disease_name', 'disease')}",
        fontsize=11, color=HEADING_COLOR, fontweight="bold",
    )
    cbar = fig.colorbar(im, ax=ax, fraction=0.02, pad=0.04)
    cbar.set_label("Datatype score (0–1)", fontsize=9, color=MUTED_TEXT)
    cbar.ax.tick_params(colors=MUTED_TEXT)
    ax.set_facecolor(PHYLO_OFF_WHITE)
    fig.patch.set_facecolor("white")
    ax.tick_params(colors=MUTED_TEXT)

    fig.tight_layout()
    # Reserve bottom margin so wrapped x-axis labels are not clipped (OT-R2-05)
    fig.subplots_adjust(bottom=0.22)
    png_path = outdir / "figures" / "figure_2_datatype_heatmap.png"
    svg_path = outdir / "figures" / "figure_2_datatype_heatmap.svg"
    png_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(png_path), dpi=150, facecolor="white", bbox_inches="tight")
    fig.savefig(str(svg_path), format="svg", facecolor="white", bbox_inches="tight")
    plt.close(fig)

    return {
        "file": "figures/figure_2_datatype_heatmap.png",
        "svg": "figures/figure_2_datatype_heatmap.svg",
        "caption": (
            f"Per-datatype contribution scores for the top {n_targets} targets associated with "
            f"{facts.get('disease_name', 'the queried disease')}. "
            f"Each cell is a prioritization metric (0–1), not an effect estimate or causal proof."
        ),
        "payload": {"datatype_ids": dt_ids, "matrix": _matrix_to_strict(matrix)},
    }


def build_figures(facts_path: pathlib.Path, outdir: pathlib.Path) -> list[dict]:
    """Build both figures and return a manifest list. Also writes a payload sidecar."""
    facts = _load_facts(facts_path)
    figures = []

    fig1 = render_target_ranking(facts, outdir)
    figures.append({"step": 1, **fig1})

    fig2 = render_datatype_heatmap(facts, outdir)
    figures.append({"step": 2, **fig2})

    # Write payload sidecar for cross-artifact verification
    # Strict RFC JSON: allow_nan=False rejects NaN/inf (OT-R2-08)
    payloads = {}
    for f in figures:
        if f.get("file"):
            payloads[f["file"]] = f.get("payload")
    sidecar = outdir / "figure_payloads.json"
    sidecar.write_text(
        json.dumps(payloads, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )

    return figures


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic Open Targets figures")
    parser.add_argument("--facts", required=True, help="path to report_facts.json")
    parser.add_argument("--outdir", default="/mnt/results", help="output directory")
    args = parser.parse_args()

    figures = build_figures(pathlib.Path(args.facts), pathlib.Path(args.outdir))
    print(json.dumps(figures, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
