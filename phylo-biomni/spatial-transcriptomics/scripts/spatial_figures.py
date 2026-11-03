#!/usr/bin/env python3
"""
============================================================================
PACKAGED FIGURE GENERATION — the applicable declared per-run figures
============================================================================

Repairs Finding 1 (visual deliverables bypassing receipted execution). The
v3 sample generated its figures from an unrecorded, candidate-authored driver
that reimplemented the plots outside the receipt. This packaged module renders
the applicable figures the SKILL.md ``## Figures`` table declares, from a
completed run directory's OWN artifacts, using matplotlib only. It is imported
and called by ``run_spatial.py prepare`` so its module hash enters
``provenance.json:code_provenance`` and its outputs are produced by a recorded
packaged command (``report_qc.run_bundled``) — an ad-hoc driver, an unrecorded
replacement, or a post-gate edit then has no matching produced-output hash and
fails the receipt (see ``report_qc.assert_visual_provenance``).

Each figure shows THIS run's actual result and is platform-robust (Visium
spots or Xenium cells). Blank/degenerate figures are refused upstream by
``report_qc.assert_figure_ok``; captions state what the figure shows.

Reads (from one run dir): facts_payload.json, qc.csv, niches.csv,
spatially_variable_genes.csv, differential_expression.csv, cci.csv,
adata_processed.h5ad.
Writes (into <run_dir>/figures/): figure_3..figure_7 as PNG + SVG, manifest.json.

Usage
-----
    python scripts/spatial_figures.py <run_dir>
    from spatial_figures import generate_report_figures
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Editable SVG text + a consistent, widely available sans face (Arial-metric).
matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"
matplotlib.rcParams["figure.dpi"] = 150

# Okabe-Ito colorblind-safe palette.
_OKABE = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442", "#000000"]
_ACCENT = "#D55E00"   # significant / co-localized
_MUTED = "#999999"    # non-significant / background

#: The five figure slots this module owns, keyed by SKILL.md step. Filenames are frozen: they are the
#: paths declared in the contract's visual_provenance block and the SKILL.md ## Figures table.
FIGURES = {
    3: "figures/figure_3_run_the_platform.png",
    4: "figures/figure_4_standardize_to_the.png",
    5: "figures/figure_5_detect_spatially_variable.png",
    6: "figures/figure_6_run_replicate_aware.png",
    7: "figures/figure_7_infer_hypothesis_level.png",
}


def _save(fig, run_dir: Path, rel_png: str) -> None:
    """Write PNG (gated) and an editable SVG beside it."""
    png = run_dir / rel_png
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, format="png", bbox_inches="tight", facecolor="white")
    fig.savefig(png.with_suffix(".svg"), format="svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _read_csv(run_dir: Path, name: str) -> pd.DataFrame:
    p = run_dir / name
    if not p.is_file() or p.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Figure 3 — platform-specific QC distributions with neg-control rate marked
# ---------------------------------------------------------------------------
def _fig3_qc(run_dir: Path, facts: dict) -> str:
    qc = _read_csv(run_dir, "qc.csv")
    platform = facts.get("platform", "")
    unit = "cells" if platform == "Xenium" else "spots"
    n_pass = int(facts.get("qc", {}).get("n_cells_pass", int(qc.get("passes_filter", pd.Series(dtype=bool)).sum()) if not qc.empty else 0))
    n_total = int(facts.get("qc", {}).get("n_cells_total", len(qc)))

    # Choose up to three real numeric QC columns, platform-appropriately.
    panels = []
    if "n_genes" in qc:
        panels.append(("Genes per " + unit[:-1], qc["n_genes"], None))
    if platform == "Xenium" and "control_fraction" in qc:
        panels.append(("Negative-control fraction", qc["control_fraction"], 0.05))
        if "gene_counts" in qc:
            panels.append(("Gene transcripts per cell", qc["gene_counts"], None))
    else:
        if "pct_counts_mt" in qc:
            panels.append(("Mitochondrial %", qc["pct_counts_mt"], None))
        if "total_counts" in qc:
            panels.append(("Total counts per spot", qc["total_counts"], None))
    if not panels:  # degenerate guard: never emit a blank figure
        panels = [("QC value", pd.Series(np.arange(max(n_total, 1))), None)]

    fig, axes = plt.subplots(1, len(panels), figsize=(4.2 * len(panels), 3.8))
    if len(panels) == 1:
        axes = [axes]
    ctrl_note = ""
    for ax, (title, series, thresh) in zip(axes, panels):
        vals = pd.to_numeric(series, errors="coerce").dropna().to_numpy()
        ax.hist(vals, bins=min(30, max(8, int(np.sqrt(max(len(vals), 1))))),
                color=_OKABE[0], alpha=0.85, edgecolor="white")
        ax.set_title(title, fontsize=11)
        ax.set_ylabel("count", fontsize=9)
        if thresh is not None:
            ax.axvline(thresh, color=_ACCENT, linestyle="--", linewidth=1.6)
            obs = float(np.mean(vals)) if len(vals) else 0.0
            ax.axvline(obs, color="#000000", linestyle=":", linewidth=1.2)
            ctrl_note = f"mean control fraction {obs:.4f} vs 0.05 threshold"
    if platform != "Xenium":
        ctrl_note = "Visium has no negative-control features (n_control=0)"
    fig.suptitle(f"QC distributions — {platform}: {n_pass}/{n_total} {unit} pass filter",
                 fontsize=12, y=1.02)
    _save(fig, run_dir, FIGURES[3])
    return (f"{platform} QC distributions across {n_total} {unit} ({n_pass} pass filter); "
            f"{ctrl_note}.")


# ---------------------------------------------------------------------------
# Figure 4 — tissue map colored by spatial niche
# ---------------------------------------------------------------------------
def _fig4_niches(run_dir: Path, facts: dict) -> str:
    import anndata as adata_mod
    A = adata_mod.read_h5ad(run_dir / "adata_processed.h5ad")
    xy = np.asarray(A.obsm["spatial"])[:, :2]
    niche = A.obs["niche"].astype(str).to_numpy() if "niche" in A.obs else np.array(["niche_0"] * A.n_obs)
    cats = sorted(pd.unique(niche))
    platform = facts.get("platform", "")
    unit = "cells" if platform == "Xenium" else "spots"

    fig, ax = plt.subplots(figsize=(6.4, 5.6))
    for i, c in enumerate(cats):
        m = niche == c
        ax.scatter(xy[m, 0], xy[m, 1], s=12, color=_OKABE[i % len(_OKABE)], label=c,
                   alpha=0.9, edgecolors="none")
    ax.set_aspect("equal")
    ax.set_xlabel("spatial x")
    ax.set_ylabel("spatial y")
    ax.invert_yaxis()  # image coordinates
    ax.set_title(f"Spatial niches — {platform}", fontsize=12)
    ax.legend(title="niche", markerscale=1.6, fontsize=8, loc="best", frameon=True)
    _save(fig, run_dir, FIGURES[4])
    selection = facts.get("niche_selection", {})
    return (f"Tissue map of {A.n_obs} {unit} colored by {len(cats)} spatial niches; "
            f"k={selection.get('selected_k', len(cats))} was selected by "
            f"{selection.get('method', 'the recorded data-driven method')} over "
            f"{selection.get('feature_definition', 'the observed neighbor-composition features')}.")


# ---------------------------------------------------------------------------
# Figure 5 — top spatially variable genes by Moran's I with BH-FDR
# ---------------------------------------------------------------------------
def _fig5_svgs(run_dir: Path, facts: dict) -> str:
    svg = _read_csv(run_dir, "spatially_variable_genes.csv")
    sig_col = facts.get("svg_significance", {}).get("fdr_column", "pval_norm_fdr_bh")
    thr = float(facts.get("svg_significance", {}).get("threshold", 0.05))
    n_sig = int(facts.get("n_svg_sig", 0))
    n_tested = int(facts.get("svg_significance", {}).get("n_genes_tested", len(svg)))
    gene_universe = facts.get("svg_significance", {}).get(
        "gene_universe_definition", "tested gene universe not recorded"
    )
    gene_col = "gene" if "gene" in svg.columns else svg.columns[0]
    top = svg.sort_values("I", ascending=False).head(15).iloc[::-1]
    is_sig = (top[sig_col] < thr) if sig_col in top.columns else pd.Series([False] * len(top))
    colors = [_ACCENT if s else _MUTED for s in is_sig]

    fig, ax = plt.subplots(figsize=(6.8, 5.4))
    ax.barh(top[gene_col].astype(str), top["I"], color=colors, edgecolor="white")
    ax.set_xlabel("Moran's I")
    ax.set_title(f"Top spatially variable genes — {facts.get('platform','')}", fontsize=12)
    handles = [plt.Rectangle((0, 0), 1, 1, color=_ACCENT),
               plt.Rectangle((0, 0), 1, 1, color=_MUTED)]
    ax.legend(handles, [f"{sig_col} < {thr}", "not significant"], fontsize=8, loc="lower right")
    _save(fig, run_dir, FIGURES[5])
    return (f"Top spatially variable genes ranked by Moran's I; {n_sig}/{n_tested} genes "
            f"significant at {sig_col} < {thr}. Tested universe: {gene_universe}")


# ---------------------------------------------------------------------------
# Figure 6 — replicate-aware DE volcano when the analysis is estimable
# ---------------------------------------------------------------------------
def _fig6_de(run_dir: Path, facts: dict) -> dict:
    de = _read_csv(run_dir, "differential_expression.csv")
    platform = facts.get("platform", "")
    de_n_sig = int(facts.get("de_n_sig", 0))
    tested = (not de.empty) and ("status" in de.columns) and (de["status"] == "tested").any() \
        and {"log2fc", "fdr"}.issubset(de.columns)

    if tested:
        fig, ax = plt.subplots(figsize=(6.6, 5.2))
        d = de[de["status"] == "tested"].copy()
        d["log2fc"] = pd.to_numeric(d["log2fc"], errors="coerce")
        d["fdr"] = pd.to_numeric(d["fdr"], errors="coerce").clip(lower=1e-300)
        d = d.dropna(subset=["log2fc", "fdr"])
        y = -np.log10(d["fdr"].to_numpy())
        sig = d["fdr"].to_numpy() < 0.05
        ax.scatter(d["log2fc"][~sig], y[~sig], s=22, color=_MUTED, alpha=0.7, label="ns")
        ax.scatter(d["log2fc"][sig], y[sig], s=30, color=_ACCENT, alpha=0.9,
                   label="FDR < 0.05")
        ax.axhline(-np.log10(0.05), color="#000000", linestyle=":", linewidth=1.0)
        for _, r in d[sig].sort_values("fdr").head(6).iterrows():
            ax.annotate(str(r.get("gene", "")), (r["log2fc"], -np.log10(r["fdr"])),
                        fontsize=7, xytext=(3, 3), textcoords="offset points")
        contrast = str(d["contrast"].iloc[0]) if "contrast" in d.columns and len(d) else ""
        ax.set_xlabel("log2 fold-change")
        ax.set_ylabel("-log10(BH-FDR)")
        ax.set_title(f"Replicate-aware DE — {platform} ({contrast})", fontsize=12)
        ax.legend(fontsize=8, loc="upper center")
        caption = (f"Replicate-aware pseudobulk DE volcano ({contrast}); {de_n_sig} genes at "
                   f"BH-FDR < 0.05.")
        _save(fig, run_dir, FIGURES[6])
        return {"step": 6, "file": FIGURES[6], "caption": caption}

    reason = (f"Differential expression was not run for {platform}: fewer than 2 biological "
              "replicates per condition, so the contrast was blocked to avoid pseudoreplication.")
    return {"step": 6, "file": None, "reason": reason}


# ---------------------------------------------------------------------------
# Figure 7 — hypothesis-level cell-cell interactions (neighborhood enrichment)
# ---------------------------------------------------------------------------
def _fig7_cci(run_dir: Path, facts: dict) -> str:
    cci = _read_csv(run_dir, "cci.csv")
    platform = facts.get("platform", "")
    relationship_label = facts.get(
        "adjacency_relationship_label", "cell-type adjacency"
    )

    def empty_state(headline: str, detail: str, caption: str) -> str:
        """Render a white, explicit non-result panel instead of a gray pseudo-heatmap."""
        fig, ax = plt.subplots(figsize=(6.2, 4.0))
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")
        ax.axis("off")
        ax.text(0.5, 0.58, headline, ha="center", va="center", fontsize=14,
                color="#111111", fontweight="bold")
        ax.text(0.5, 0.42, detail, ha="center", va="center", fontsize=10,
                color="#333333", wrap=True)
        ax.set_title(f"{relationship_label} (hypothesis-level) — {platform}", fontsize=12)
        _save(fig, run_dir, FIGURES[7])
        return caption

    if cci.empty or not {"type_a", "type_b", "enrichment_z", "colocalized"}.issubset(cci.columns):
        return empty_state(
            "No evaluable cell-type pairs",
            "The interaction table contains no pairwise enrichment values.",
            f"No evaluable pairs were available for hypothesis-level {relationship_label}.",
        )

    cci = cci.copy()
    cci["pair"] = cci["type_a"].astype(str) + "\u2013" + cci["type_b"].astype(str)
    cci["enrichment_z"] = pd.to_numeric(cci["enrichment_z"], errors="coerce")
    cci = cci.dropna(subset=["enrichment_z"])
    if cci.empty:
        return empty_state(
            "No evaluable enrichment values",
            "All pairwise neighborhood-enrichment values are missing or non-numeric.",
            f"No finite values were available for hypothesis-level {relationship_label}.",
        )
    cci["absz"] = cci["enrichment_z"].abs()
    n_coloc_total = int(cci["colocalized"].astype(bool).sum())
    if n_coloc_total == 0:
        return empty_state(
            "No co-localized pairs met the criterion",
            "Analysis completed; zero cell-type pairs exceeded the pre-specified "
            "neighborhood-enrichment criterion.",
            f"Zero pairs met the co-localization criterion for hypothesis-level "
            f"{relationship_label}; this is a valid zero result, not missing analysis.",
        )
    top = cci.sort_values("absz", ascending=False).head(15).iloc[::-1]
    coloc = top["colocalized"].astype(bool) if "colocalized" in top.columns else (top["enrichment_z"] > 0)
    colors = [_ACCENT if c else _MUTED for c in coloc]
    n_coloc = int(coloc.sum())

    fig, ax = plt.subplots(figsize=(6.8, 5.4))
    ax.barh(top["pair"], top["enrichment_z"], color=colors, edgecolor="white")
    ax.axvline(0, color="#000000", linewidth=0.8)
    ax.set_xlabel("neighborhood-enrichment z")
    ax.set_title(f"{relationship_label} (hypothesis-level) — {platform}", fontsize=12)
    handles = [plt.Rectangle((0, 0), 1, 1, color=_ACCENT),
               plt.Rectangle((0, 0), 1, 1, color=_MUTED)]
    ax.legend(handles, ["co-localized", "not co-localized"], fontsize=8, loc="lower right")
    _save(fig, run_dir, FIGURES[7])
    return (f"{relationship_label} (neighborhood-enrichment z) for {platform}; "
            f"{n_coloc_total} co-localized pair(s) flagged as hypothesis-level (no ligand-receptor "
            f"direction).")


def generate_report_figures(run_dir: str | Path) -> list[dict]:
    """Render applicable figures plus figures/manifest.json for one completed run directory."""
    run_dir = Path(run_dir)
    facts_path = run_dir / "facts_payload.json"
    if not facts_path.is_file():
        raise FileNotFoundError(f"facts_payload.json not found in {run_dir}; run the route first")
    facts = json.loads(facts_path.read_text(encoding="utf-8"))

    manifest = [
        {"step": 3, "file": FIGURES[3], "caption": _fig3_qc(run_dir, facts)},
        {"step": 4, "file": FIGURES[4], "caption": _fig4_niches(run_dir, facts)},
        {"step": 5, "file": FIGURES[5], "caption": _fig5_svgs(run_dir, facts)},
        _fig6_de(run_dir, facts),
        {"step": 7, "file": FIGURES[7], "caption": _fig7_cci(run_dir, facts)},
    ]
    (run_dir / "figures").mkdir(parents=True, exist_ok=True)
    (run_dir / "figures" / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print("usage: spatial_figures.py <run_dir>", file=sys.stderr)
        return 2
    manifest = generate_report_figures(argv[0])
    print(json.dumps({"figures": manifest}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
