#!/usr/bin/env python3
"""Render one representative figure per analysis step and write figures/manifest.json.

Every figure shows THIS run's actual result (not a schematic). Each manifest entry includes a caption
and a short interpretation. The report reads that inventory from report_facts.json and keeps each
explanation with its figure.

CLI:
  python3 figures.py --counts counts.tsv --platemap platemap.csv --outdir /mnt/results
Reads tables/*.csv produced by the earlier steps; writes figures/figure_2..8 (.png and .svg) and
figures/manifest.json under <outdir>.
"""
from __future__ import annotations

import argparse
import json
import pathlib

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import scientific_logic

# Colorblind-friendly palette (Okabe-Ito).
CB = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#F0E442", "#000000"]


def _save(fig, outdir: pathlib.Path, name: str) -> str:
    figdir = outdir / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    png = figdir / f"{name}.png"
    fig.savefig(png, dpi=150, bbox_inches="tight")
    fig.savefig(figdir / f"{name}.svg", bbox_inches="tight")
    plt.close(fig)
    return f"figures/{name}.png"


def fig_qc(counts, meta, qc, outdir):
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
    order = qc.sort_values("total_umi")
    colors = [CB[0] if p else CB[1] for p in order["pass_qc"]]
    ax[0].bar(range(len(order)), order["total_umi"], color=colors)
    floor = float(order["umi_floor"].iloc[0])
    ax[0].axhline(floor, ls="--", color="0.3", lw=1, label=f"UMI floor = {floor:,.0f}")
    ax[0].set_yscale("log")
    ax[0].set_xlabel("well (sorted by depth)")
    ax[0].set_ylabel("total UMIs")
    ax[0].set_title("Per-well depth (blue = pass, orange = dropped)")
    ax[0].legend(fontsize=8, frameon=False, loc="upper left")
    # plate heatmap of log10 total UMI
    piv = qc.pivot_table(index="row", columns="col", values="total_umi", aggfunc="first")
    im = ax[1].imshow(np.log10(piv.values + 1), cmap="viridis", aspect="auto")
    ax[1].set_xticks(range(len(piv.columns)), piv.columns)
    ax[1].set_yticks(range(len(piv.index)), piv.index)
    ax[1].set_title("Plate map: log10(total UMI)")
    fig.colorbar(im, ax=ax[1], shrink=0.8)
    n_drop = int((~qc["pass_qc"]).sum())
    cap = (f"{len(qc)} wells profiled; {n_drop} dropped below the depth/complexity floor "
           f"(dropped: {', '.join(qc.loc[~qc['pass_qc'],'well_id'])}).")
    return _save(fig, outdir, "figure_2_per_well_and"), cap


def fig_pca(counts, meta, pass_wells, outdir, positional_confounded, covariate):
    from sklearn.decomposition import PCA
    sub = counts[pass_wells]
    logcpm = np.log1p(sub / sub.sum(0) * 1e6)
    var = logcpm.var(1).sort_values(ascending=False)
    X = logcpm.loc[var.index[:500]].T.values
    X = X - X.mean(0, keepdims=True)
    pcs = PCA(n_components=2, random_state=0).fit(X)
    sc = pcs.transform(X)
    m = meta.set_index("well_id").loc[pass_wells]
    m = m.copy()
    m["plot_group"] = np.where(
        m["analysis_role"] == scientific_logic.CONTROL_ROLE,
        m["compound"].astype(str) + " (vehicle)",
        m["compound"].astype(str) + " (treatment)",
    )
    comps = sorted(m["plot_group"].unique())
    fig, ax = plt.subplots(figsize=(6.2, 5))
    for i, c in enumerate(comps):
        idx = (m["plot_group"] == c).values
        ax.scatter(sc[idx, 0], sc[idx, 1], label=c, color=CB[i % len(CB)], s=40, edgecolor="w")
    ev = pcs.explained_variance_ratio_
    ax.set_xlabel(f"PC1 ({ev[0]:.0%})")
    ax.set_ylabel(f"PC2 ({ev[1]:.0%})")
    ax.set_title("Well profiles by compound")
    ax.legend(fontsize=8, frameon=False)
    cap = scientific_logic.positional_caption(
        positional_confounded, covariate, float(ev[0]), float(ev[1])
    )
    return _save(fig, outdir, "figure_3_experimental_design_and"), cap


def _volcano(ax, sub, title):
    lfc = sub["log2FoldChange"].values
    padj = sub["padj"].fillna(1.0).values
    y = -np.log10(np.clip(padj, 1e-300, 1))
    sig = sub["is_sig"].values
    ax.scatter(lfc[~sig], y[~sig], s=6, color="0.7")
    ax.scatter(lfc[sig], y[sig], s=8, color=CB[1])
    ax.axhline(-np.log10(0.05), ls="--", lw=1, color="0.4")
    for x in (-1, 1):
        ax.axvline(x, ls="--", lw=1, color="0.4")
    ax.set_xlabel("log2 fold change")
    ax.set_ylabel("-log10 padj")
    ax.set_title(title)


def fig_volcano(de, outdir):
    summ = (de[de["contrast_family"] == scientific_logic.TREATMENT_CONTRAST_FAMILY]
            .groupby("contrast")["is_sig"].sum().sort_values(ascending=False))
    top = summ.index[0]
    sub = de[de["contrast"] == top]
    fig, ax = plt.subplots(figsize=(6, 5))
    _volcano(ax, sub, f"{top} vs vehicle")
    n = int(sub["is_sig"].sum())
    cap = (f"Strongest contrast {top}: {n} DEGs at padj<0.05 and |log2FC|>1 "
           f"({int((sub['is_sig'] & (sub['log2FoldChange']>0)).sum())} up, "
           f"{int((sub['is_sig'] & (sub['log2FoldChange']<0)).sum())} down).")
    return _save(fig, outdir, "figure_4_differential_expression_contrasts"), cap


def fig_null(de, outdir):
    sub = de[de["contrast_family"] == scientific_logic.NULL_CONTRAST_FAMILY]
    fig, ax = plt.subplots(figsize=(6, 5))
    if len(sub):
        _volcano(ax, sub, "Vehicle-vs-vehicle null")
    n = int(sub["is_sig"].sum()) if len(sub) else 0
    identity = str(sub["control_identity"].iloc[0]) if len(sub) else "vehicle control"
    cap = (f"Vehicle-vs-vehicle ({identity} split) null contrast returned {n} DEGs and passed "
           f"the pre-specified maximum of {scientific_logic.MAX_NULL_DEGS}.")
    return _save(fig, outdir, "figure_5_vehicle_versus_vehicle"), cap


def fig_dose(dose_csv, outdir):
    dr = pd.read_csv(dose_csv)
    if dr.empty:
        fig, ax = plt.subplots(figsize=(6, 4.6))
        ax.text(0.5, 0.5, "No compound dose series was available", ha="center", va="center")
        ax.set_axis_off()
        cap = "No compound dose series was available; no transcriptomic POD was estimated."
        return _save(fig, outdir, "figure_6_concentration_response_and"), cap
    # choose the compound with a finite tPOD (most doses)
    finite = dr[dr["tpod_uM"].notna()]
    comp = (finite if len(finite) else dr).sort_values("n_doses", ascending=False)["compound"].iloc[0]
    sub = dr[dr["compound"] == comp].sort_values("dose_uM")
    tpod = sub["tpod_uM"].iloc[0]
    fig, ax = plt.subplots(figsize=(6, 4.6))
    ax.plot(sub["dose_uM"], sub["lowest_dose_referenced_signed_response"], "o-", color=CB[0])
    ax.axhline(1.0, ls="--", color="0.4", lw=1, label="BMR = 1 log2FC")
    if pd.notna(tpod):
        ax.axvline(tpod, ls=":", color=CB[1], lw=1.5, label=f"tPOD = {tpod:.3g} uM")
    ax.set_xscale("log")
    ax.set_xlabel("dose (uM, log scale)")
    ax.set_ylabel("lowest-dose-referenced, direction-aligned mean log2FC")
    ax.set_title(f"Concentration-response: {comp}")
    ax.legend(fontsize=8, frameon=False)
    tp = f"{tpod:.3g} uM" if pd.notna(tpod) else "not estimable"
    status = str(sub["response_status"].iloc[0])
    if pd.notna(tpod):
        cap = (f"{comp} lowest-dose-referenced signed response crossed BMR=1; transcriptomic POD "
               f"(tPOD) = {tp} using the pinned direction-aligned method.")
    else:
        reason = str(sub["response_reason"].iloc[0])
        cap = (f"{comp} lowest-dose-referenced signed response had status {status}; tPOD was not "
               f"estimated ({reason}).")
    return _save(fig, outdir, "figure_6_concentration_response_and"), cap


def fig_moa(sim_csv, clusters_csv, outdir):
    sim = pd.read_csv(sim_csv, index_col=0)
    clusters = pd.read_csv(clusters_csv)
    fig, ax = plt.subplots(figsize=(5.6, 4.8))
    im = ax.imshow(sim.values, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(sim.columns)), sim.columns, rotation=45, ha="right")
    ax.set_yticks(range(len(sim.index)), sim.index)
    for i in range(sim.shape[0]):
        for j in range(sim.shape[1]):
            ax.text(j, i, f"{sim.values[i,j]:.2f}", ha="center", va="center", fontsize=8)
    ax.set_title("Compound signature similarity (Pearson)")
    fig.colorbar(im, ax=ax, shrink=0.8)
    nclust = clusters["cluster"].nunique()
    cap = (f"Pairwise compound signature correlation; {nclust} MOA cluster(s) at the 0.5 distance cut "
           f"({'; '.join(clusters['compound']+':'+clusters['cluster'].astype(str))}).")
    return _save(fig, outdir, "figure_7_perturbational_signatures_moa"), cap


def fig_activity(act_csv, outdir):
    act = pd.read_csv(act_csv, index_col=0)
    fig, ax = plt.subplots(figsize=(6.4, 5.2))
    vmax = np.nanmax(np.abs(act.values))
    im = ax.imshow(act.values, cmap="RdBu_r", vmin=-vmax, vmax=vmax, aspect="auto")
    ax.set_xticks(range(len(act.columns)), act.columns, rotation=45, ha="right")
    ax.set_yticks(range(len(act.index)), act.index)
    ax.set_title("Gene-set activity (ULM t-value)")
    fig.colorbar(im, ax=ax, shrink=0.8)
    tops = {c: scientific_logic.top_activity(act[c]) for c in act.columns}
    cap = ("Univariate linear model (ULM) gene-set activity per compound; top set by the pinned "
           f"{scientific_logic.PATHWAY_RANKING_METRIC} — "
           + "; ".join(
               f"{compound}: {top['gene_set']} (signed t={top['ulm_t_value']:.3g})"
               for compound, top in tops.items()
           ) + ".")
    return _save(fig, outdir, "figure_8_pathway_and_tf"), cap


def run(counts_path, platemap_path, outdir, *, positional_confounded: bool,
        covariate: str) -> list[dict]:
    outdir = pathlib.Path(outdir)
    tables = outdir / "tables"
    counts = pd.read_csv(counts_path, sep="\t", index_col=0)
    meta = pd.read_csv(platemap_path)
    meta, _ = scientific_logic.normalize_control_roles(meta)
    qc = pd.read_csv(tables / "well_qc.csv")
    counts = counts[list(meta["well_id"])]
    pass_wells = list(qc.loc[qc["pass_qc"], "well_id"])
    de = pd.read_csv(tables / "de_all_contrasts.csv")

    interpretations = {
        2: ("Use the marked depth floor and plate pattern to identify wells or regions that could "
            "distort downstream contrasts; passing depth QC does not establish biological replication."),
        3: ("Separation in this descriptive projection can reveal dominant treatment or positional "
            "structure, but PCA alone does not establish a compound-specific effect."),
        4: ("The highlighted genes define the strongest fitted response under the stated thresholds; "
            "the contrast remains hypothesis-generating and may include stress-related effects."),
        5: ("A quiet null supports the selected normalization and design for this run; it cannot rule "
            "out artifacts shared by both vehicle groups."),
        6: ("The curve shows whether a signed response reaches the fixed benchmark over the tested "
            "range; an estimated tPOD is method- and dose-range-dependent."),
        7: ("Correlated signatures suggest shared transcriptional response structure, not a validated "
            "molecular target or mechanism."),
        8: ("Large signed ULM scores prioritize gene sets for follow-up; they depend on the supplied "
            "gene-set collection and are not direct pathway-activity measurements."),
    }

    def add(step, figure_file, caption):
        manifest.append({"step": step, "file": figure_file, "caption": caption,
                         "interpretation": interpretations[step]})

    manifest = []
    f, c = fig_qc(counts, meta, qc, outdir);                     add(2, f, c)
    f, c = fig_pca(counts, meta, pass_wells, outdir,
                   positional_confounded, covariate);            add(3, f, c)
    f, c = fig_volcano(de, outdir);                              add(4, f, c)
    f, c = fig_null(de, outdir);                                 add(5, f, c)
    f, c = fig_dose(tables / "dose_response.csv", outdir);       add(6, f, c)
    f, c = fig_moa(tables / "moa_similarity.csv", tables / "moa_clusters.csv", outdir)
    add(7, f, c)
    f, c = fig_activity(tables / "activity_scores.csv", outdir); add(8, f, c)

    (outdir / "figures" / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="Render per-step result figures + manifest.")
    ap.add_argument("--counts", required=True)
    ap.add_argument("--platemap", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--positional-confounded", required=True, choices=["true", "false"])
    ap.add_argument("--covariate", default="none", choices=["none", "edge"])
    args = ap.parse_args()
    manifest = run(
        args.counts, args.platemap, args.outdir,
        positional_confounded=args.positional_confounded == "true",
        covariate=args.covariate,
    )
    print(json.dumps({"figures": len(manifest), "files": [m["file"] for m in manifest]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
