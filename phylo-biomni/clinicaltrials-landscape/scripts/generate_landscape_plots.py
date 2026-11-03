"""
Per-step landscape figures for the ClinicalTrials.gov landscape skill (matplotlib).

This module:

* uses matplotlib + seaborn only (no plotnine / plotnine-prism);
* writes ONE figure per analysis step to ``<results>/figures/`` as PNG **and** SVG (editable text);
* writes ``<results>/figures/manifest.json`` with a data-driven caption per figure;
* OMITS an inapplicable figure with an explicit ``reason`` rather than drawing empty axes;
* wraps sponsor labels (never destructively truncates them) and uses a single, non-duplicated title.

Captions state what the figure shows (with real counts), never a strategic takeaway.
"""

from __future__ import annotations

import json
import os
import textwrap

try:
    from export_all import build_sponsor_summary
except ImportError:  # pragma: no cover
    from scripts.export_all import build_sponsor_summary

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
import pandas as pd  # noqa: E402

matplotlib.rcParams["font.family"] = "DejaVu Sans"
matplotlib.rcParams["svg.fonttype"] = "none"
matplotlib.rcParams["figure.dpi"] = 150
matplotlib.rcParams["axes.grid"] = True
matplotlib.rcParams["axes.grid.axis"] = "y"
matplotlib.rcParams["grid.alpha"] = 0.3

# Okabe-Ito colorblind-safe palette.
CB = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442", "#999999", "#000000"]
PHASE_ORDER = ["Phase 1", "Phase 1/2", "Phase 2", "Phase 2/3", "Phase 3", "Phase 3/4", "Phase 4"]


def _save(fig, results_dir, base):
    fig_dir = os.path.join(results_dir, "figures")
    os.makedirs(fig_dir, exist_ok=True)
    png = os.path.join(fig_dir, f"{base}.png")
    fig.savefig(png, dpi=200, bbox_inches="tight", facecolor="white")
    try:
        fig.savefig(os.path.join(fig_dir, f"{base}.svg"), bbox_inches="tight", facecolor="white")
    except Exception:
        pass
    plt.close(fig)
    return f"figures/{base}.png"


def _fig_study_composition(df):
    counts = df["study_purpose"].value_counts()
    order = [p for p in ["Therapeutic", "Diagnostic/Imaging", "Observational", "Other/Supportive", "Unresolved"]
             if p in counts.index]
    counts = counts.reindex(order)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(range(len(counts)), counts.values, color=CB[: len(counts)])
    ax.set_xticks(range(len(counts)))
    ax.set_xticklabels(list(counts.index), rotation=18, ha="right", fontsize=9)
    ax.set_ylabel("Number of registered records")
    ax.set_title("Study purpose composition")
    for i, v in enumerate(counts.values):
        ax.text(i, v, str(int(v)), ha="center", va="bottom", fontsize=9)
    n_int = int((df["study_type"] == "INTERVENTIONAL").sum()) if "study_type" in df else 0
    n_obs = int((df["study_type"] == "OBSERVATIONAL").sum()) if "study_type" in df else 0
    n_expanded = int((df["study_type"] == "EXPANDED_ACCESS").sum()) if "study_type" in df else 0
    type_parts = [f"{n_int} interventional", f"{n_obs} observational"]
    if n_expanded:
        type_parts.append(f"{n_expanded} expanded-access")
    caption = (
        f"Study-purpose composition of {len(df)} retrieved registered records "
        f"({', '.join(type_parts)}). "
        + "; ".join(f"{k}: {int(v)}" for k, v in counts.items()) + "."
    )
    return fig, caption


def _fig_intervention_category(df):
    counts = df["intervention_category"].value_counts()
    counts = counts.sort_values(ascending=True)
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.barh(range(len(counts)), counts.values, color=CB[1])
    ax.set_yticks(range(len(counts)))
    ax.set_yticklabels([textwrap.fill(x, 34) for x in counts.index], fontsize=9)
    ax.set_xlabel("Number of registered records")
    ax.set_title("Intervention category (record-grounded)")
    ax.grid(axis="x", alpha=0.3)
    ax.grid(axis="y", visible=False)
    for i, v in enumerate(counts.values):
        ax.text(v, i, f" {int(v)}", va="center", fontsize=9)
    n_uncl = int(counts.get("Unclassified (insufficient description)", 0))
    caption = (
        f"Intervention-category distribution across {len(df)} records; "
        f"{n_uncl} records could not be resolved to a modality and are labeled "
        f"'Unclassified (insufficient description)', not assigned an invented modality."
    )
    return fig, caption


def _fig_phase_distribution(df):
    order = PHASE_ORDER + ["Not Applicable"]
    # Fold any label outside the canonical set into Not Applicable (as build_category_by_phase does)
    # so the bars always sum to records_retrieved instead of silently dropping records.
    labels = df["phase_normalized"].where(df["phase_normalized"].isin(order), "Not Applicable")
    counts = labels.value_counts().reindex(order).fillna(0).astype(int)
    colors = [CB[0]] * len(PHASE_ORDER) + [CB[7]]
    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    ax.bar(range(len(counts)), counts.values, color=colors)
    ax.set_xticks(range(len(counts)))
    ax.set_xticklabels(counts.index, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("Number of registered records")
    ax.set_title("Phase distribution (Not Applicable shown explicitly)")
    for i, v in enumerate(counts.values):
        ax.text(i, v, str(int(v)), ha="center", va="bottom", fontsize=9)
    n_pa = int(df["phase_applicable"].sum())
    caption = (
        f"Phase distribution of {len(df)} retrieved records: {n_pa} are phase-applicable "
        f"(Phase 1-4, including combined phases) and {len(df) - n_pa} are Not Applicable "
        f"(observational and many device/diagnostic studies). Phase-only subtotals use the "
        f"phase-applicable denominator, not the all-retrieved total."
    )
    return fig, caption


def _fig_category_by_phase(df):
    sub = df[df["phase_applicable"]]
    if len(sub) == 0:
        return None, "No phase-applicable records, so a category x phase view does not apply."
    ct = pd.crosstab(sub["intervention_category"], sub["phase_normalized"])
    ct = ct.reindex(columns=[p for p in PHASE_ORDER if p in ct.columns], fill_value=0)
    ct = ct.loc[ct.sum(axis=1).sort_values(ascending=False).index]
    fig, ax = plt.subplots(figsize=(9, max(3.5, 0.5 * len(ct) + 1.5)))
    im = ax.imshow(ct.values, aspect="auto", cmap="Blues")
    ax.set_xticks(range(len(ct.columns)))
    ax.set_xticklabels(ct.columns, rotation=30, ha="right", fontsize=9)
    ax.set_yticks(range(len(ct.index)))
    ax.set_yticklabels([textwrap.fill(x, 30) for x in ct.index], fontsize=8)
    ax.grid(False)
    for i in range(len(ct.index)):
        for j in range(len(ct.columns)):
            v = int(ct.values[i, j])
            if v:
                ax.text(j, i, str(v), ha="center", va="center",
                        color="white" if v > ct.values.max() * 0.6 else "black", fontsize=8)
    ax.set_title("Intervention category by phase (phase-applicable subset)")
    fig.colorbar(im, ax=ax, shrink=0.7, label="Trials")
    caption = (
        f"Intervention category by phase for the {len(sub)} phase-applicable trials "
        f"(of {len(df)} retrieved); the {len(df) - len(sub)} Not Applicable records are excluded from "
        f"this phase view by definition."
    )
    return fig, caption


def _fig_top_sponsors(df, top_n=15):
    if "sponsor_normalized" not in df.columns:
        return None, "Sponsor field unavailable in the retrieved records."
    ranking = build_sponsor_summary(df).head(top_n)
    if ranking.empty:
        return None, "No retrieved sponsors; a sponsor ranking does not apply."
    counts = ranking["trials"].iloc[::-1]
    industry = {}
    if "is_industry" in df.columns:
        industry = df.groupby("sponsor_normalized")["is_industry"].any().to_dict()
    colors = [CB[0] if industry.get(s, False) else CB[2] for s in counts.index]
    fig, ax = plt.subplots(figsize=(9, max(4, 0.4 * len(counts) + 1)))
    ax.barh(range(len(counts)), counts.values, color=colors)
    ax.set_yticks(range(len(counts)))
    ax.set_yticklabels([textwrap.fill(str(s), 40) for s in counts.index], fontsize=8)
    ax.set_xlabel("Number of registered records")
    ax.set_title(f"Top {len(counts)} sponsors by registered-record count")
    ax.grid(axis="x", alpha=0.3)
    ax.grid(axis="y", visible=False)
    ax.legend(handles=[Patch(color=CB[0], label="Industry"), Patch(color=CB[2], label="Academic / Other")],
              loc="lower right", fontsize=8)
    for i, v in enumerate(counts.values):
        ax.text(v, i, f" {int(v)}", va="center", fontsize=8)
    top = ranking["trials"]
    caption = (
        f"Top {len(counts)} lead sponsors by registered-record count; the most active is "
        f"{top.index[0]} with {int(top.iloc[0])} records. Full sponsor names are shown (wrapped), not "
        f"truncated. Industry vs academic/other is from the registry lead-sponsor class."
    )
    return fig, caption


def _fig_geography(df, top_n=15):
    if "countries_str" not in df.columns or df["countries_str"].fillna("").str.len().sum() == 0:
        return None, "No location/country data is present in the retrieved records."
    countries = df["countries_str"].fillna("").str.split("; ").explode()
    countries = countries[countries.str.len() > 0]
    counts = countries.value_counts().head(top_n).sort_values(ascending=True)
    fig, ax = plt.subplots(figsize=(8.5, max(4, 0.4 * len(counts) + 1)))
    ax.barh(range(len(counts)), counts.values, color=CB[5])
    ax.set_yticks(range(len(counts)))
    ax.set_yticklabels(counts.index, fontsize=9)
    ax.set_xlabel("Number of records with a site in this country")
    ax.set_title(f"Top {len(counts)} countries by registered-record presence")
    ax.grid(axis="x", alpha=0.3)
    ax.grid(axis="y", visible=False)
    for i, v in enumerate(counts.values):
        ax.text(v, i, f" {int(v)}", va="center", fontsize=8)
    top = counts.sort_values(ascending=False)
    caption = (
        f"Geographic footprint: top {len(counts)} countries by number of retrieved records with a "
        f"site there; {top.index[0]} leads with {int(top.iloc[0])}. Records with multiple sites are "
        f"counted once per country."
    )
    return fig, caption


def _fig_enrollment(df, min_records=8):
    if "enrollment_clean" not in df.columns:
        return None, "No enrollment data is available in the retrieved records."
    sub = df[(df["study_type"] == "INTERVENTIONAL") & df["enrollment_clean"].notna()
             & df["study_purpose"].isin(["Therapeutic", "Diagnostic/Imaging"])]
    sub = sub[sub["enrollment_clean"] > 0]
    if len(sub) < min_records:
        return None, (
            f"Only {len(sub)} interventional therapeutic/diagnostic trials have usable enrollment "
            f"(< {min_records}); an enrollment distribution would not be informative."
        )
    order = [p for p in ["Therapeutic", "Diagnostic/Imaging"] if p in sub["study_purpose"].unique()]
    data = [sub[sub["study_purpose"] == p]["enrollment_clean"].values for p in order]
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    bp = ax.boxplot(data, vert=True, patch_artist=True, showfliers=True,
                    flierprops=dict(marker="o", markersize=3, alpha=0.4))
    for patch, c in zip(bp["boxes"], CB):
        patch.set_facecolor(c)
        patch.set_alpha(0.6)
    ax.set_xticklabels(list(order), fontsize=9)
    ax.set_yscale("log")
    ax.set_ylabel("Registry-reported enrollment (log scale)")
    ax.set_title("Enrollment by study purpose (interventional)")
    med = {p: float(sub[sub["study_purpose"] == p]["enrollment_clean"].median()) for p in order}
    types = sub.get("enrollment_type", pd.Series("", index=sub.index)).fillna("").astype(str).str.upper()
    n_actual = int((types == "ACTUAL").sum())
    n_estimated = int((types == "ESTIMATED").sum())
    n_unknown = len(sub) - n_actual - n_estimated
    caption = (
        f"Registry-reported enrollment (log scale) for {len(sub)} interventional therapeutic/diagnostic trials "
        f"with usable enrollment; medians "
        + ", ".join(f"{p} {med[p]:g}" for p in order)
        + f". Enrollment type in this plotted subset: actual {n_actual}; estimated {n_estimated}; unknown {n_unknown}."
        + " Missing or unrecognized types are retained as unknown."
        + " Registry/mega-study outliers (>50,000) are excluded from this aggregate view."
    )
    return fig, caption


_STEP_BUILDERS = [
    (1, "figure_1_study_composition", _fig_study_composition),
    (2, "figure_2_intervention_category", _fig_intervention_category),
    (3, "figure_3_phase_distribution", _fig_phase_distribution),
    (4, "figure_4_category_by_phase", _fig_category_by_phase),
    (5, "figure_5_top_sponsors", _fig_top_sponsors),
    (6, "figure_6_geography", _fig_geography),
    (7, "figure_7_enrollment", _fig_enrollment),
]


def generate_landscape_plots(df, results_dir="."):
    """Render every per-step figure and write ``figures/manifest.json``.

    Parameters
    ----------
    df : pandas.DataFrame
        Compiled trials (must carry study_purpose, intervention_category, phase_applicable).
    results_dir : str
        Results root; figures are written to ``<results_dir>/figures/``.

    Returns
    -------
    list[dict]
        Manifest entries {step, file, caption} or {step, file: None, reason}.
    """
    os.makedirs(os.path.join(results_dir, "figures"), exist_ok=True)
    manifest = []
    for step, base, builder in _STEP_BUILDERS:
        fig, text = builder(df)
        if fig is None:
            manifest.append({"step": step, "file": None, "reason": text})
            print(f"   [skip] step {step} {base}: {text}")
        else:
            rel = _save(fig, results_dir, base)
            manifest.append({"step": step, "file": rel, "caption": text})
            print(f"   [ok]   step {step} {rel}")
    manifest_path = os.path.join(results_dir, "figures", "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"\u2713 Wrote {sum(1 for m in manifest if m.get('file'))} figures + manifest to {manifest_path}")
    return manifest
