#!/usr/bin/env python3
"""Generate all figures for the hit-triage report.

Produces 10 figures (PNG + SVG) covering potency, series, SAR, and docking.

Usage:
    python generate_figures.py --data-dir <dir> --figures-dir <dir>
"""
import argparse, os, sys, json
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams['font.family'] = ['Liberation Sans', 'Arimo', 'DejaVu Sans']
matplotlib.rcParams['svg.fonttype'] = 'none'
import matplotlib.pyplot as plt
import seaborn as sns
from scipy.stats import pearsonr
from rdkit import Chem
from rdkit.Chem import Draw, AllChem
from rdkit import RDLogger

RDLogger.DisableLog("rdApp.*")

# Colorblind-safe defaults + legible fonts (handoff figure items recovery-2 / recovery-4).
# The v2-recovery-r1 figures used the non-colorblind-safe RdYlGn colormap and small (8-9 pt)
# annotation/legend fonts. These module-level constants are the single source of both, so a
# regression can assert them and a reviewer can change the whole set in one place.
SEQ_CMAP = "viridis"        # colorblind-safe sequential; replaces RdYlGn
ANNOT_FONTSIZE = 11         # on-figure annotations (was 8-10 -- unreadable when embedded in the PDF)
LEGEND_FONTSIZE = 11        # legends (was 9)
MAX_SERIES_IN_FIGURE = 15   # cap the docking-by-series figure so high-series targets stay legible
matplotlib.rcParams['legend.fontsize'] = LEGEND_FONTSIZE
matplotlib.rcParams['axes.titlesize'] = 13
matplotlib.rcParams['axes.labelsize'] = 12


def top_series_for_figure(dock_plot, series_stats=None, n=MAX_SERIES_IN_FIGURE):
    """Choose <= n series to draw in the docking-by-series figure (recovery-2).

    The v2 figure plotted EVERY docked series (471 for EGFR), which is illegible. This mirrors
    fig5's top-N subsetting: prefer the series_stats ranking (as fig5) intersected with the series
    that actually have docking data; fall back to the docked series with the most docked ligands.
    Returns an ordered list of series names.
    """
    docked = dock_plot["series"].value_counts()   # docked series, by number of docked ligands
    if series_stats is not None and len(getattr(series_stats, "index", [])):
        ranked = [s for s in series_stats.index if s in docked.index]
        if ranked:
            return ranked[:n]
    return list(docked.index[:n])


def main():
    parser = argparse.ArgumentParser(description="Generate report figures")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--figures-dir", required=True)
    args = parser.parse_args()

    os.makedirs(args.figures_dir, exist_ok=True)
    FIG = args.figures_dir

    hits = pd.read_csv(os.path.join(args.data_dir, "hits_scored_clustered.csv"))
    docking = pd.read_csv(os.path.join(args.data_dir, "docking_results.csv"))
    series_stats = pd.read_csv(os.path.join(args.data_dir, "series_stats.csv"), index_col=0)
    with open(os.path.join(args.data_dir, "sar_results.json")) as f:
        sar = json.load(f)

    # ---- Figure 1: pChEMBL distribution + tiers ----
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    axes[0].hist(hits["best_pchembl"], bins=50, color="#0279EE", edgecolor="white", alpha=0.85)
    axes[0].axvline(x=6, color="#FF9400", linestyle="--", linewidth=2, label="1 uM")
    axes[0].axvline(x=7, color="#75A025", linestyle="--", linewidth=2, label="100 nM")
    axes[0].axvline(x=9, color="#FD9BED", linestyle="--", linewidth=2, label="1 nM")
    axes[0].set_xlabel("pChEMBL value")
    axes[0].set_ylabel("Number of molecules")
    axes[0].set_title("Potency distribution")
    axes[0].legend(fontsize=LEGEND_FONTSIZE)

    tier_counts = hits["tier"].value_counts()
    tier_order = ["Tier 1 (Expand)", "Tier 2 (Explore)", "Tier 3 (Monitor)", "Tier 4 (Drop)"]
    tier_counts = tier_counts.reindex([t for t in tier_order if t in tier_counts.index])
    colors = ["#75A025", "#0279EE", "#FF9400", "#FD9BED"]
    axes[1].barh(range(len(tier_counts)), tier_counts.values, color=colors[:len(tier_counts)])
    axes[1].set_yticks(range(len(tier_counts)))
    axes[1].set_yticklabels(tier_counts.index)
    axes[1].set_xlabel("Number of molecules")
    axes[1].set_title("Tier distribution")
    axes[1].invert_yaxis()
    for i, v in enumerate(tier_counts.values):
        axes[1].text(v + 30, i, str(v), va="center", fontsize=ANNOT_FONTSIZE)
    plt.tight_layout()
    plt.savefig(f"{FIG}/fig1_potency_distribution.png", dpi=150, bbox_inches="tight")
    plt.savefig(f"{FIG}/fig1_potency_distribution.svg", bbox_inches="tight")
    plt.close()
    print("Figure 1: potency distribution")

    # ---- Figure 2: Top series overview ----
    top20 = series_stats.head(20).copy().sort_values("mean_hit_score", ascending=True)
    fig, ax = plt.subplots(figsize=(10, 8))
    colors = []
    for _, row in top20.iterrows():
        if row["mean_hit_score"] >= 0.55: colors.append("#75A025")
        elif row["mean_hit_score"] >= 0.45: colors.append("#0279EE")
        elif row["mean_hit_score"] >= 0.35: colors.append("#FF9400")
        else: colors.append("#FD9BED")
    ax.barh(range(len(top20)), top20["mean_hit_score"], color=colors, edgecolor="white")
    ax.set_yticks(range(len(top20)))
    ax.set_yticklabels(top20.index)
    ax.set_xlabel("Mean hit score")
    ax.set_title("Chemical series ranked by mean hit score")
    for i, (idx, row) in enumerate(top20.iterrows()):
        ax.text(row["mean_hit_score"] + 0.005, i, f"n={int(row['size'])}, pChEMBL={row['median_pchembl']:.1f}",
                va="center", fontsize=ANNOT_FONTSIZE)
    ax.axvline(x=0.5, color="gray", linestyle="--", alpha=0.5)
    plt.tight_layout()
    plt.savefig(f"{FIG}/fig2_series_overview.png", dpi=150, bbox_inches="tight")
    plt.savefig(f"{FIG}/fig2_series_overview.svg", bbox_inches="tight")
    plt.close()
    print("Figure 2: series overview")

    # ---- Figure 3: Chemical space ----
    fig, ax = plt.subplots(figsize=(10, 7))
    scatter = ax.scatter(hits["mol_wt"], hits["logp"], c=hits["best_pchembl"],
                         cmap=SEQ_CMAP, s=8, alpha=0.5, vmin=5, vmax=11)
    ax.set_xlabel("Molecular weight (Da)")
    ax.set_ylabel("cLogP")
    ax.set_title("Chemical space of hits")
    plt.colorbar(scatter, ax=ax, label="pChEMBL")
    ax.axvline(x=500, color="gray", linestyle="--", alpha=0.4)
    ax.axhline(y=5, color="gray", linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(f"{FIG}/fig3_chemical_space.png", dpi=150, bbox_inches="tight")
    plt.savefig(f"{FIG}/fig3_chemical_space.svg", bbox_inches="tight")
    plt.close()
    print("Figure 3: chemical space")

    # ---- Figure 4: Docking vs potency (with HONEST correlation) ----
    dock_success = docking[docking["status"] == "success"].copy()
    fig, ax = plt.subplots(figsize=(9, 7))
    series_colors = pd.factorize(dock_success["series"])[0]
    scatter = ax.scatter(dock_success["docking_score"], dock_success["pchembl"],
                         c=series_colors, cmap="tab20", s=60, alpha=0.8, edgecolors="white")
    ax.set_xlabel("Docking score (kcal/mol)")
    ax.set_ylabel("pChEMBL (experimental potency)")
    ax.set_title("Docking score vs experimental potency")
    # Compute and display correlation HONESTLY
    valid = dock_success[dock_success["docking_score"].notna() & dock_success["pchembl"].notna()]
    if len(valid) > 2:
        r, p = pearsonr(valid["docking_score"], valid["pchembl"])
        sig_label = "significant" if p < 0.05 else "not significant"
        ax.text(0.05, 0.95, f"Pearson r = {r:.3f} (p = {p:.3f}, {sig_label})",
                transform=ax.transAxes, fontsize=11, verticalalignment="top",
                bbox=dict(boxstyle="round", facecolor="white", alpha=0.8))
    # Label the most potent compounds with leader lines to stacked right-margin text,
    # so labels never collide or overlap the points (previous version stacked them at the
    # top of the plot and they were unreadable). Points are sorted by pChEMBL descending so
    # connector lines do not cross.
    to_label = valid.nlargest(5, "pchembl").sort_values("pchembl", ascending=False)
    n_lab = len(to_label)
    if n_lab:
        y_fracs = np.linspace(0.95, 0.95 - 0.11 * (n_lab - 1), n_lab)
        for (_, row), yf in zip(to_label.iterrows(), y_fracs):
            ax.annotate(
                row["chembl_id"],
                xy=(row["docking_score"], row["pchembl"]), xycoords="data",
                xytext=(1.005, yf), textcoords="axes fraction",
                fontsize=ANNOT_FONTSIZE, ha="left", va="center", annotation_clip=False,
                arrowprops=dict(arrowstyle="-", color="0.5", lw=0.6, alpha=0.8,
                                connectionstyle="arc3,rad=0.0"),
            )
    plt.tight_layout()
    plt.savefig(f"{FIG}/fig4_docking_vs_potency.png", dpi=150, bbox_inches="tight")
    plt.savefig(f"{FIG}/fig4_docking_vs_potency.svg", bbox_inches="tight")
    plt.close()
    print("Figure 4: docking vs potency")

    # ---- Figure 5: Series potency boxplots ----
    top15_series = series_stats.head(15).index.tolist()
    plot_data = hits[hits["series"].isin(top15_series)].copy()
    fig, ax = plt.subplots(figsize=(12, 6))
    order = plot_data.groupby("series")["best_pchembl"].median().sort_values(ascending=False).index
    sns.boxplot(data=plot_data, x="series", y="best_pchembl", order=order, ax=ax,
                palette=SEQ_CMAP, hue="series", legend=False)
    ax.axhline(y=7, color="gray", linestyle="--", alpha=0.5, label="100 nM")
    ax.axhline(y=9, color="gray", linestyle=":", alpha=0.5, label="1 nM")
    ax.set_xlabel("Chemical series")
    ax.set_ylabel("pChEMBL")
    ax.set_title("Potency distribution across top 15 series")
    ax.legend(fontsize=LEGEND_FONTSIZE)
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(f"{FIG}/fig5_series_potency_boxplots.png", dpi=150, bbox_inches="tight")
    plt.savefig(f"{FIG}/fig5_series_potency_boxplots.svg", bbox_inches="tight")
    plt.close()
    print("Figure 5: series potency boxplots")

    # ---- Figure 6: SAR correlations heatmap ----
    corr_data = []
    for sname, sdata in sar.items():
        if sdata.get("property_correlations"):
            for prop, corr in sdata["property_correlations"].items():
                if corr is not None:
                    corr_data.append({"series": sname, "property": prop, "correlation": corr})
    corr_df = pd.DataFrame(corr_data)
    if len(corr_df) > 0:
        pivot = corr_df.pivot_table(index="series", columns="property", values="correlation")
        series_order = [s for s in sar.keys() if s in pivot.index]
        pivot = pivot.reindex(series_order)
        key_props = ["mol_wt", "logp", "tpsa", "hbd", "hba", "rot_bonds", "ligand_efficiency", "lipe"]
        pivot = pivot[[p for p in key_props if p in pivot.columns]]
        fig, ax = plt.subplots(figsize=(10, 8))
        sns.heatmap(pivot, annot=True, fmt=".2f", cmap="RdBu_r", center=0, ax=ax,
                    vmin=-0.6, vmax=0.6, linewidths=0.5)
        ax.set_title("Property-potency correlations within series")
        ax.set_ylabel("Chemical series")
        ax.set_xlabel("Molecular property")
        plt.tight_layout()
        plt.savefig(f"{FIG}/fig6_sar_correlations.png", dpi=150, bbox_inches="tight")
        plt.savefig(f"{FIG}/fig6_sar_correlations.svg", bbox_inches="tight")
        plt.close()
        print("Figure 6: SAR correlations heatmap")

    # ---- Figure 7: LE vs LipE ----
    fig, ax = plt.subplots(figsize=(9, 7))
    scatter = ax.scatter(hits["ligand_efficiency"], hits["lipe"], c=hits["best_pchembl"],
                         cmap=SEQ_CMAP, s=8, alpha=0.5, vmin=5, vmax=11)
    ax.axhline(y=3, color="gray", linestyle="--", alpha=0.4, label="LipE = 3")
    ax.axhline(y=5, color="gray", linestyle=":", alpha=0.4, label="LipE = 5")
    ax.axvline(x=0.3, color="gray", linestyle="--", alpha=0.4, label="LE = 0.3")
    ax.set_xlabel("Ligand efficiency (kcal/mol/HA)")
    ax.set_ylabel("LipE (pChEMBL - cLogP)")
    ax.set_title("Ligand efficiency vs LipE")
    plt.colorbar(scatter, ax=ax, label="pChEMBL")
    ax.legend(fontsize=LEGEND_FONTSIZE)
    plt.tight_layout()
    plt.savefig(f"{FIG}/fig7_le_lipe.png", dpi=150, bbox_inches="tight")
    plt.savefig(f"{FIG}/fig7_le_lipe.svg", bbox_inches="tight")
    plt.close()
    print("Figure 7: LE vs LipE")

    # ---- Figure 8: Top hit structures ----
    top_hits = hits.head(12)
    mols = [Chem.MolFromSmiles(s) for s in top_hits["smiles"]]
    legends = [f"{row['molecule_chembl_id']}\npChEMBL={row['best_pchembl']:.1f}"
               for _, row in top_hits.iterrows()]
    img = Draw.MolsToGridImage(mols, molsPerRow=4, subImgSize=(300, 250), legends=legends)
    img.save(f"{FIG}/fig8_top_hit_structures.png")
    print("Figure 8: top hit structures")

    # ---- Figure 9: Series recommendations ----
    rec_data = []
    for sname, sdata in sar.items():
        rec_data.append({
            "series": sname, "n_members": sdata["n_members"],
            "median_pchembl": sdata["pchembl_median"],
            "n_cliffs": sdata["n_activity_cliffs"],
            "le_median": sdata.get("le_median", 0),
            "lipe_median": sdata.get("lipe_median", 0),
            "recommendation": sdata["recommendation"],
        })
    rec_df = pd.DataFrame(rec_data)
    rec_df["rec_short"] = rec_df["recommendation"].str.split(" - ").str[0]
    rec_df = rec_df.sort_values("median_pchembl", ascending=True)

    fig, ax = plt.subplots(figsize=(10, 8))
    rec_colors = {"EXPAND": "#75A025", "EXPLORE": "#0279EE", "MONITOR": "#FF9400", "DROP": "#FD9BED"}
    colors = [rec_colors.get(r, "gray") for r in rec_df["rec_short"]]
    ax.barh(range(len(rec_df)), rec_df["median_pchembl"], color=colors, edgecolor="white")
    ax.set_yticks(range(len(rec_df)))
    ax.set_yticklabels(rec_df["series"])
    ax.set_xlabel("Median pChEMBL")
    ax.set_title("Series recommendations by potency")
    for i, (_, row) in enumerate(rec_df.iterrows()):
        ax.text(row["median_pchembl"] + 0.1, i, f"n={row['n_members']}, cliffs={row['n_cliffs']}",
                va="center", fontsize=ANNOT_FONTSIZE)
    ax.axvline(x=7, color="gray", linestyle="--", alpha=0.5)
    ax.axvline(x=9, color="gray", linestyle=":", alpha=0.5)
    from matplotlib.patches import Patch
    legend_elements = [Patch(facecolor=c, label=r) for r, c in rec_colors.items()]
    ax.legend(handles=legend_elements, fontsize=LEGEND_FONTSIZE, loc="lower right")
    plt.tight_layout()
    plt.savefig(f"{FIG}/fig9_series_recommendations.png", dpi=150, bbox_inches="tight")
    plt.savefig(f"{FIG}/fig9_series_recommendations.svg", bbox_inches="tight")
    plt.close()
    print("Figure 9: series recommendations")

    # ---- Figure 10: Docking by series ----
    dock_plot = docking[docking["status"] == "success"].copy()
    dock_plot = dock_plot[dock_plot["series"] != ""]
    if len(dock_plot) > 0:
        # recovery-2: subset to the top-N series (as fig5) so high-series targets stay legible.
        # The v2 figure drew EVERY docked series (471 for EGFR) and was unreadable.
        chosen = top_series_for_figure(dock_plot, series_stats, n=MAX_SERIES_IN_FIGURE)
        dock_plot = dock_plot[dock_plot["series"].isin(chosen)]
        n_shown = dock_plot["series"].nunique()
        fig, ax = plt.subplots(figsize=(12, 6))
        order = dock_plot.groupby("series")["docking_score"].median().sort_values().index
        sns.boxplot(data=dock_plot, x="series", y="docking_score", order=order, ax=ax,
                    palette=SEQ_CMAP, hue="series", legend=False)
        ax.set_xlabel("Chemical series")
        ax.set_ylabel("Docking score (kcal/mol)")
        ax.set_title(f"Docking score distribution (top {n_shown} series)")
        plt.xticks(rotation=45)
        plt.tight_layout()
        plt.savefig(f"{FIG}/fig10_docking_by_series.png", dpi=150, bbox_inches="tight")
        plt.savefig(f"{FIG}/fig10_docking_by_series.svg", bbox_inches="tight")
        plt.close()
        print(f"Figure 10: docking by series (top {n_shown} of "
              f"{docking[docking['status']=='success']['series'].nunique()} docked series)")

    print("\nAll figures generated.")


if __name__ == "__main__":
    main()
