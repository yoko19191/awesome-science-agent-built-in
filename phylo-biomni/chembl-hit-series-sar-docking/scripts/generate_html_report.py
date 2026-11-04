#!/usr/bin/env python3
"""Generate interactive HTML report for hit triage.

Self-contained HTML with embedded figures (base64 PNG), sortable tables,
and series recommendation cards. This is the PRIMARY deliverable.

Usage:
    python generate_html_report.py --data-dir <dir> --figures-dir <dir> \
        --target-name "TYK2" --output <path>
"""
import argparse, os, sys, json, base64
import pandas as pd
import numpy as np
from scipy.stats import pearsonr


def img_to_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def main():
    parser = argparse.ArgumentParser(description="Generate interactive HTML report")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--figures-dir", required=True)
    parser.add_argument("--target-name", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    hits = pd.read_csv(os.path.join(args.data_dir, "hits_scored_clustered.csv"))
    docking = pd.read_csv(os.path.join(args.data_dir, "docking_results.csv"))
    series_stats = pd.read_csv(os.path.join(args.data_dir, "series_stats.csv"), index_col=0)
    with open(os.path.join(args.data_dir, "sar_results.json")) as f:
        sar = json.load(f)

    # Backward-compatible guards for user-supplied CSVs that predate variant columns.
    if "variant_best" not in hits.columns:
        hits["variant_best"] = "WT/unspecified"
    if "variant" not in docking.columns:
        docking["variant"] = "WT/unspecified"

    # Load provenance if available
    prov = {}
    prov_path = os.path.join(args.data_dir, "processing_provenance.json")
    if os.path.exists(prov_path):
        with open(prov_path) as f:
            prov = json.load(f)

    # Variant composition (assay_variant_mutation) and which variant the docked receptor
    # represents -- so the report can flag target-variant conflation instead of hiding it.
    variant_report = {}
    vpath = os.path.join(args.data_dir, "variant_composition.json")
    if os.path.exists(vpath):
        with open(vpath) as f:
            variant_report = json.load(f) or {}
    receptor_variant = ""
    box_path = os.path.join(args.data_dir, "docking_box.json")
    if os.path.exists(box_path):
        with open(box_path) as f:
            receptor_variant = (json.load(f) or {}).get("receptor_variant") or ""

    # Build the variant-composition HTML block + a prominent pooling warning.
    variant_block_html = ""
    if variant_report:
        comp = variant_report.get("composition", [])
        rows = "".join(
            f"<tr><td>{c['variant']}</td><td>{c['n_records']:,}</td>"
            f"<td>{c['pct_records']:.1f}%</td><td>{c.get('n_molecules', 0):,}</td></tr>"
            for c in comp)
        vtable = (
            "<table style='max-width:640px'><thead><tr>"
            "<th>Assay variant</th><th>Records</th><th>% of records</th><th>Molecules</th>"
            "</tr></thead><tbody>" + rows + "</tbody></table>")
        # F04: the writer now emits the contract key 'applied_filter'; fall back to the legacy
        # 'variant_filter_applied' so an older variant_composition.json still renders.
        filt = variant_report.get("applied_filter",
                                  variant_report.get("variant_filter_applied", "any"))
        rcv = receptor_variant or "unspecified"
        if variant_report.get("pooled"):
            warn = (
                f"<div class='caveat' style='border-left-color:var(--phylo-orange);"
                f"background:#fff3e0'><strong>Target-variant conflation warning.</strong> "
                f"This activity set pools <strong>{variant_report.get('n_variants')} assay "
                f"variants</strong> "
                f"({variant_report.get('n_mutant_variants')} mutant + wild-type/unspecified) into a "
                f"single pChEMBL per molecule, while docking uses a <strong>single</strong> "
                f"structure representing variant <strong>{rcv}</strong>. A compound optimised "
                f"against a different variant is therefore ranked against the wrong receptor, and "
                f"series clustering can merge chemotypes developed against different variants. "
                f"Potency tiers are <strong>not variant-specific</strong>. Variant filter applied "
                f"to this run: <code>{filt}</code>. To score one variant, re-run "
                f"<code>process_activities.py --variant WT|&lt;MUTATION&gt;</code> and dock a "
                f"matching structure (<code>--pdb-id</code> / <code>--receptor-variant</code>).</div>")
        else:
            only = comp[0]["variant"] if comp else "WT/unspecified"
            warn = (f"<div class='caveat'><strong>Assay variant:</strong> a single variant "
                    f"(<strong>{only}</strong>) is present; no cross-variant pooling. Docked "
                    f"receptor variant: <strong>{rcv}</strong>. Variant filter: <code>{filt}</code>.</div>")
        variant_block_html = (
            "<h3>Assay-variant composition</h3>"
            "<p style='font-size:13px'>ChEMBL activity is pooled across the wild type and "
            "distinct mutants. Composition below is over the exact-measurement records that feed "
            "per-molecule scoring (structured field <code>assay_variant_mutation</code>; "
            "wild-type/unspecified is its own bucket).</p>"
            + warn + vtable)

    # ChEMBL release, for the CC BY-SA attribution (the licence requires naming the release,
    # not just "ChEMBL"). Written by retrieve_activities.py.
    chembl_release = ""
    meta_path = os.path.join(args.data_dir, "target_metadata.json")
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            chembl_release = (json.load(f) or {}).get("chembl_release") or ""
    chembl_label = f"ChEMBL ({chembl_release})" if chembl_release else "ChEMBL"

    FIG = args.figures_dir
    target = args.target_name

    # Embed figures
    figs = {}
    for i in range(1, 11):
        names = {
            1: "fig1_potency_distribution", 2: "fig2_series_overview",
            3: "fig3_chemical_space", 4: "fig4_docking_vs_potency",
            5: "fig5_series_potency_boxplots", 6: "fig6_sar_correlations",
            7: "fig7_le_lipe", 8: "fig8_top_hit_structures",
            9: "fig9_series_recommendations", 10: "fig10_docking_by_series",
        }
        path = os.path.join(FIG, f"{names[i]}.png")
        if os.path.exists(path):
            figs[i] = img_to_base64(path)

    # Prepare table data
    top_hits = hits.head(50)[["molecule_chembl_id", "molecule_pref_name", "series", "variant_best",
                               "best_pchembl", "best_value_nm", "standard_type", "mol_wt", "logp",
                               "ligand_efficiency", "lipe", "hit_score", "tier"]].copy()
    top_hits.columns = ["ChEMBL ID", "Name", "Series", "Variant", "pChEMBL", "Best Value (nM)",
                        "Type", "MW", "cLogP", "LE", "LipE", "Hit Score", "Tier"]
    top_hits_json = top_hits.to_json(orient="records")

    series_summary = []
    for sname, sdata in sar.items():
        series_summary.append({
            "series": sname, "n_members": sdata["n_members"],
            "median_pchembl": round(sdata["pchembl_median"], 2),
            "pchembl_range": f"{sdata['pchembl_range'][0]:.1f}-{sdata['pchembl_range'][1]:.1f}",
            "n_cliffs": sdata["n_activity_cliffs"],
            "le_median": round(sdata.get("le_median", 0), 3),
            "lipe_median": round(sdata.get("lipe_median", 0), 2),
            "recommendation": sdata["recommendation"],
            "best_member": sdata["best_member"]["chembl_id"],
            "best_pchembl": round(sdata["best_member"]["pchembl"], 2),
            "mcs_success": bool(sdata.get("mcs_smarts")),
            "rgroup_success": bool(sdata.get("rgroup_success")),
        })
    series_summary_json = json.dumps(series_summary)

    dock_success = docking[docking["status"] == "success"].copy()
    dock_table = dock_success[["chembl_id", "series", "variant", "docking_score", "pchembl", "hit_score"]].copy()
    dock_table.columns = ["ChEMBL ID", "Series", "Variant", "Docking Score", "pChEMBL", "Hit Score"]
    dock_table = dock_table.sort_values("Docking Score")
    dock_json = dock_table.to_json(orient="records")

    # Compute docking-potency correlation HONESTLY
    dock_corr_text = "N/A"
    if len(dock_success) > 2:
        valid = dock_success[dock_success["docking_score"].notna() & dock_success["pchembl"].notna()]
        if len(valid) > 2:
            r, p = pearsonr(valid["docking_score"], valid["pchembl"])
            sig = "significant" if p < 0.05 else "not significant"
            dock_corr_text = f"Pearson r = {r:.3f} (p = {p:.3f}, {sig})"

    expand_count = sum(1 for s in series_summary if "EXPAND" in s["recommendation"])
    explore_count = sum(1 for s in series_summary if "EXPLORE" in s["recommendation"])
    monitor_count = sum(1 for s in series_summary if "MONITOR" in s["recommendation"])
    drop_count = sum(1 for s in series_summary if "DROP" in s["recommendation"])

    # MCS success stats
    mcs_success_count = sum(1 for s in series_summary if s["mcs_success"])
    rgroup_success_count = sum(1 for s in series_summary if s["rgroup_success"])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{target} Hit Triage Report</title>
<style>
:root {{
  --phylo-black: #000000; --phylo-cream: #ECE9E2; --phylo-bg: #FAF9F3;
  --phylo-yellow: #E9ED4C; --phylo-orange: #FF9400; --phylo-green: #75A025;
  --phylo-pink: #FD9BED; --phylo-blue: #0279EE;
  /* Primary accent + functional tokens, per pdf-report-generation/SKILL.md:48-60,
     so this appendix reads as the same document family as the PDF. */
  --phylo-gold: #D4A04A; --phylo-gold-dark: #B4863A;
  --heading: #111111; --body-text: #2C2A26; --muted: #8A8378;
  --alt-row: #F9F7F3; --border: #D5CFC5;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: Helvetica, Arial, sans-serif; background: var(--phylo-bg); color: var(--body-text); line-height: 1.6; }}
.header {{ background: white; color: var(--heading); padding: 30px 40px 22px; border-bottom: 2px solid var(--phylo-gold); }}
.header h1 {{ font-size: 28px; margin-bottom: 5px; }}
.header .subtitle {{ color: var(--muted); font-size: 14px; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 20px 40px; }}
.section {{ background: white; border-radius: 8px; padding: 25px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.section h2 {{ color: var(--heading); border-bottom: 2px solid var(--phylo-gold); padding-bottom: 8px; margin-bottom: 15px; font-size: 22px; }}
.section h3 {{ color: var(--heading); margin: 15px 0 8px; font-size: 17px; }}
.kpi-row {{ display: flex; gap: 15px; margin: 15px 0; flex-wrap: wrap; }}
.kpi {{ flex: 1; min-width: 140px; background: var(--phylo-cream); border-radius: 8px; padding: 15px; text-align: center; }}
.kpi .value {{ font-size: 28px; font-weight: bold; color: var(--phylo-gold-dark); }}
.kpi .label {{ font-size: 12px; color: var(--muted); margin-top: 3px; }}
.kpi.green .value {{ color: var(--phylo-green); }}
.kpi.orange .value {{ color: var(--phylo-orange); }}
.kpi.pink .value {{ color: var(--phylo-pink); }}
table {{ width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 13px; }}
th {{ background: var(--phylo-gold); color: white; padding: 8px 10px; text-align: left; cursor: pointer; position: sticky; top: 0; }}
th:hover {{ background: var(--phylo-gold-dark); }}
td {{ padding: 6px 10px; border-bottom: 1px solid var(--border); }}
tr:nth-child(even) {{ background: var(--alt-row); }}
tr:hover {{ background: var(--phylo-cream); }}
.tier-1 {{ color: var(--phylo-green); font-weight: bold; }}
.tier-2 {{ color: var(--phylo-blue); }}
.tier-3 {{ color: var(--phylo-orange); }}
.tier-4 {{ color: var(--phylo-pink); }}
.rec-expand {{ background: var(--phylo-bg); border-left: 4px solid var(--phylo-green); padding: 10px 15px; margin: 8px 0; border-radius: 4px; }}
.rec-explore {{ background: var(--phylo-bg); border-left: 4px solid var(--phylo-blue); padding: 10px 15px; margin: 8px 0; border-radius: 4px; }}
.rec-monitor {{ background: var(--phylo-bg); border-left: 4px solid var(--phylo-orange); padding: 10px 15px; margin: 8px 0; border-radius: 4px; }}
.rec-drop {{ background: var(--phylo-bg); border-left: 4px solid var(--phylo-pink); padding: 10px 15px; margin: 8px 0; border-radius: 4px; }}
.figure {{ margin: 15px 0; text-align: center; }}
.figure img {{ max-width: 100%; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.15); }}
.figure .caption {{ font-size: 12px; color: var(--muted); margin-top: 5px; }}
.nav {{ position: sticky; top: 0; background: white; border-bottom: 1px solid var(--border); padding: 10px 40px; z-index: 100; display: flex; gap: 20px; }}
.nav a {{ color: #0563C1; text-decoration: none; font-size: 14px; font-weight: 500; }}
.nav a:hover {{ text-decoration: underline; }}
.filter-input {{ padding: 6px 10px; border: 1px solid var(--border); border-radius: 4px; font-size: 13px; margin: 5px 0; width: 200px; }}
.tab-buttons {{ display: flex; gap: 5px; margin: 10px 0; }}
.tab-btn {{ padding: 8px 16px; border: none; background: var(--phylo-cream); cursor: pointer; border-radius: 4px; font-size: 13px; }}
.tab-btn.active {{ background: var(--phylo-gold); color: white; }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }}
.badge-expand {{ background: var(--phylo-green); color: white; }}
.badge-explore {{ background: var(--phylo-blue); color: white; }}
.badge-monitor {{ background: var(--phylo-orange); color: white; }}
.badge-drop {{ background: var(--phylo-pink); color: white; }}
.summary-box {{ background: var(--phylo-cream); border-radius: 8px; padding: 15px; margin: 10px 0; }}
.summary-box ul {{ margin-left: 20px; }}
.summary-box li {{ margin: 5px 0; }}
.caveat {{ background: var(--phylo-bg); border-left: 4px solid var(--phylo-orange); padding: 10px 15px; margin: 10px 0; border-radius: 4px; font-size: 13px; }}
</style>
</head>
<body>

<div class="header">
  <h1>{target} Small-Molecule Hit Triage Report</h1>
  <div class="subtitle">ChEMBL bioactivity screening | Chemical series clustering & SAR | Structure-based docking | Hit-expansion guidance</div>
</div>

<div class="nav">
  <a href="#overview">Overview</a>
  <a href="#potency">Potency</a>
  <a href="#series">Chemical Series</a>
  <a href="#sar">SAR Analysis</a>
  <a href="#docking">Docking</a>
  <a href="#recommendations">Recommendations</a>
  <a href="#appendix">Data Tables</a>
</div>

<div class="container">

<div class="section" id="overview">
  <h2>1. Executive Overview</h2>
  <div class="kpi-row">
    <div class="kpi"><div class="value">{len(hits):,}</div><div class="label">Unique hits (ChEMBL)</div></div>
    <div class="kpi green"><div class="value">{(hits['best_pchembl']>=7).sum():,}</div><div class="label">Sub-100 nM hits</div></div>
    <div class="kpi"><div class="value">{series_stats.shape[0]}</div><div class="label">Chemical series</div></div>
    <div class="kpi green"><div class="value">{expand_count}</div><div class="label">Series to expand</div></div>
    <div class="kpi orange"><div class="value">{explore_count}</div><div class="label">Series to explore</div></div>
    <div class="kpi pink"><div class="value">{monitor_count + drop_count}</div><div class="label">Monitor/Drop</div></div>
  </div>
  <div class="summary-box">
    <h3>Screening Output</h3>
    <p>Retrieved <strong>{len(hits):,} unique molecules</strong> with exact potency measurements against {target} from {prov.get('raw_records', 'N/A'):,} raw bioactivity records. Potency endpoints (IC50, Ki, Kd) were standardized to pChEMBL values. Hits were scored by a composite of potency (50%), efficacy/ligand efficiency (30%), and drug-likeness (20%), then clustered into <strong>{series_stats.shape[0]} chemical series</strong> using Butina clustering on ECFP4 fingerprints (Tanimoto &ge; 0.60).</p>
    {f'<p><small>Filtering note: {prov.get("n_dropped_censored", 0)} censored records were excluded ({prov.get("n_dropped_mols_censored_only", 0)} molecules with only inactive/bound measurements).</small></p>' if prov.get('n_dropped_censored') else ''}
  </div>
  <div class="caveat">
    <strong>ChEMBL bias caveat:</strong> ChEMBL over-represents published active molecules. The potency distribution is skewed toward potent compounds and does not represent a random screening campaign.
  </div>
  {variant_block_html}
</div>

<div class="section" id="potency">
  <h2>2. Potency & Hit Scoring</h2>
  <div class="figure">
    <img src="data:image/png;base64,{figs.get(1,'')}" alt="Potency distribution">
    <div class="caption">Figure 1. Left: pChEMBL distribution. Right: tier distribution by composite hit score.</div>
  </div>
  <div class="figure">
    <img src="data:image/png;base64,{figs.get(3,'')}" alt="Chemical space">
    <div class="caption">Figure 3. Chemical space (MW vs cLogP), colored by pChEMBL. Dashed lines mark Lipinski Ro5 boundaries.</div>
  </div>
  <div class="figure">
    <img src="data:image/png;base64,{figs.get(7,'')}" alt="LE vs LipE">
    <div class="caption">Figure 7. Ligand efficiency vs lipophilic efficiency.</div>
  </div>
  <div class="figure">
    <img src="data:image/png;base64,{figs.get(8,'')}" alt="Top hit structures">
    <div class="caption">Figure 8. Structures of the top 12 hits by potency (raster-only; not exported as SVG).</div>
  </div>
</div>

<div class="section" id="series">
  <h2>3. Chemical Series</h2>
  <p>Butina clustering identified {series_stats.shape[0]} chemical series with &ge; 5 members.</p>
  <div class="figure">
    <img src="data:image/png;base64,{figs.get(2,'')}" alt="Series overview">
    <div class="caption">Figure 2. Top 20 series ranked by mean hit score.</div>
  </div>
  <div class="figure">
    <img src="data:image/png;base64,{figs.get(5,'')}" alt="Series potency">
    <div class="caption">Figure 5. Potency distribution across top 15 series.</div>
  </div>
</div>

<div class="section" id="sar">
  <h2>4. SAR Analysis</h2>
  <div class="summary-box">
    <p>SAR analysis was performed for the top {len(sar)} series, including MCS extraction, Murcko scaffold analysis, activity cliff detection, property-potency correlations, and R-group decomposition.</p>
    <p><small>MCS extraction: {mcs_success_count}/{len(sar)} series successful. R-group decomposition: {rgroup_success_count}/{len(sar)} series successful.</small></p>
  </div>
  <div class="figure">
    <img src="data:image/png;base64,{figs.get(6,'')}" alt="SAR correlations">
    <div class="caption">Figure 6. Property-potency correlations within each series.</div>
  </div>
  <h3>Series SAR summaries</h3>
  <div id="sar-cards"></div>
</div>

<div class="section" id="docking">
  <h2>5. Structure-Based Docking</h2>
  <div class="summary-box">
    <p>Representative ligands from {dock_success['series'].nunique() if 'series' in dock_success.columns else 0} series were docked using AutoDock Vina (exhaustiveness=16, seed=42) into a single structure representing variant <strong>{receptor_variant or 'unspecified'}</strong>. The Variant column in the docking table (below) shows which assay variant each experimental pChEMBL came from; where it differs from the receptor variant, the docking score and that potency describe different proteins.</p>
  </div>
  <div class="caveat">
    <strong>Docking correlation caveat:</strong> The Pearson correlation between docking score and experimental pChEMBL is {dock_corr_text}. This weak correlation is expected when docking a congeneric series with a narrow potency range. Docking scores assess structural plausibility, not binding affinity. Experimental validation is required. Vina scores carry &plusmn;2-3 kcal/mol error.
  </div>
  <div class="figure">
    <img src="data:image/png;base64,{figs.get(4,'')}" alt="Docking vs potency">
    <div class="caption">Figure 4. Docking score vs experimental pChEMBL. {dock_corr_text}.</div>
  </div>
  <div class="figure">
    <img src="data:image/png;base64,{figs.get(10,'')}" alt="Docking by series">
    <div class="caption">Figure 10. Docking score distribution by chemical series.</div>
  </div>
</div>

<div class="section" id="recommendations">
  <h2>6. Hit-Expansion Recommendations</h2>
  <div class="figure">
    <img src="data:image/png;base64,{figs.get(9,'')}" alt="Series recommendations">
    <div class="caption">Figure 9. Series recommendations by median potency.</div>
  </div>
  <div id="rec-cards"></div>
</div>

<div class="section" id="appendix">
  <h2>7. Data Tables</h2>
  <div class="tab-buttons">
    <button class="tab-btn active" onclick="showTab('tab-hits')">Top 50 Hits</button>
    <button class="tab-btn" onclick="showTab('tab-series')">Series Summary</button>
    <button class="tab-btn" onclick="showTab('tab-docking')">Docking Results</button>
  </div>
  <div id="tab-hits" class="tab-content active">
    <input type="text" class="filter-input" placeholder="Filter hits..." onkeyup="filterTable('hits-table', this.value)">
    <table id="hits-table"><thead><tr></tr></thead><tbody></tbody></table>
  </div>
  <div id="tab-series" class="tab-content">
    <table id="series-table"><thead><tr></tr></thead><tbody></tbody></table>
  </div>
  <div id="tab-docking" class="tab-content">
    <input type="text" class="filter-input" placeholder="Filter docking..." onkeyup="filterTable('docking-table', this.value)">
    <table id="docking-table"><thead><tr></tr></thead><tbody></tbody></table>
  </div>
</div>

</div>

<script>
const hitsData = {top_hits_json};
const seriesData = {series_summary_json};
const dockingData = {dock_json};

function renderTable(tableId, data, columns) {{
  const table = document.getElementById(tableId);
  const thead = table.querySelector('thead tr');
  const tbody = table.querySelector('tbody');
  thead.innerHTML = columns.map(c => `<th onclick="sortTable('${{tableId}}', ${{JSON.stringify(c.key)}}, ${{c.numeric}})">${{c.label}}</th>`).join('');
  tbody.innerHTML = data.map(row => {{
    return '<tr>' + columns.map(c => {{
      let val = row[c.key];
      if (c.key === 'Tier') {{
        const cls = val.includes('Expand') ? 'tier-1' : val.includes('Explore') ? 'tier-2' : val.includes('Monitor') ? 'tier-3' : 'tier-4';
        return `<td class="${{cls}}">${{val}}</td>`;
      }}
      if (c.key === 'recommendation') {{
        const short = val.split(' - ')[0];
        const cls = short === 'EXPAND' ? 'badge-expand' : short === 'EXPLORE' ? 'badge-explore' : short === 'MONITOR' ? 'badge-monitor' : 'badge-drop';
        return `<td><span class="badge ${{cls}}">${{short}}</span> ${{val.split(' - ').slice(1).join(' - ')}}</td>`;
      }}
      if (typeof val === 'number') val = val.toFixed(c.decimals || 2);
      return `<td>${{val ?? ''}}</td>`;
    }}).join('') + '</tr>';
  }}).join('');
}}

renderTable('hits-table', hitsData, [
  {{key:'ChEMBL ID', label:'ChEMBL ID'}}, {{key:'Name', label:'Name'}}, {{key:'Series', label:'Series'}},
  {{key:'Variant', label:'Variant'}},
  {{key:'pChEMBL', label:'pChEMBL', numeric:true, decimals:2}}, {{key:'Best Value (nM)', label:'Best (nM)', numeric:true, decimals:3}},
  {{key:'Type', label:'Type'}}, {{key:'MW', label:'MW', numeric:true, decimals:1}}, {{key:'cLogP', label:'cLogP', numeric:true, decimals:2}},
  {{key:'LE', label:'LE', numeric:true, decimals:3}}, {{key:'LipE', label:'LipE', numeric:true, decimals:2}},
  {{key:'Hit Score', label:'Score', numeric:true, decimals:3}}, {{key:'Tier', label:'Tier'}},
]);

renderTable('series-table', seriesData, [
  {{key:'series', label:'Series'}}, {{key:'n_members', label:'Members', numeric:true, decimals:0}},
  {{key:'median_pchembl', label:'Median pChEMBL', numeric:true, decimals:2}}, {{key:'pchembl_range', label:'Range'}},
  {{key:'n_cliffs', label:'SAR discontinuity flags', numeric:true, decimals:0}}, {{key:'le_median', label:'Median LE', numeric:true, decimals:3}},
  {{key:'lipe_median', label:'Median LipE', numeric:true, decimals:2}}, {{key:'best_member', label:'Best Member'}},
  {{key:'best_pchembl', label:'Best pChEMBL', numeric:true, decimals:2}}, {{key:'recommendation', label:'Recommendation'}},
]);

renderTable('docking-table', dockingData, [
  {{key:'ChEMBL ID', label:'ChEMBL ID'}}, {{key:'Series', label:'Series'}},
  {{key:'Variant', label:'Variant'}},
  {{key:'Docking Score', label:'Docking (kcal/mol)', numeric:true, decimals:2}},
  {{key:'pChEMBL', label:'pChEMBL', numeric:true, decimals:2}}, {{key:'Hit Score', label:'Hit Score', numeric:true, decimals:3}},
]);

const sarCards = document.getElementById('sar-cards');
sarCards.innerHTML = seriesData.map(s => {{
  const rec = s.recommendation;
  const short = rec.split(' - ')[0];
  const cls = short === 'EXPAND' ? 'rec-expand' : short === 'EXPLORE' ? 'rec-explore' : short === 'MONITOR' ? 'rec-monitor' : 'rec-drop';
  const mcsLabel = s.mcs_success ? 'MCS: OK' : 'MCS: N/A';
  const rgdLabel = s.rgroup_success ? 'R-group: OK' : 'R-group: N/A';
  return `<div class="${{cls}}">
    <strong>${{s.series}}</strong> (n=${{s.n_members}}, median pChEMBL=${{s.median_pchembl}}, cliffs=${{s.n_cliffs}}, ${{mcsLabel}}, ${{rgdLabel}})<br>
    <span class="badge ${{short === 'EXPAND' ? 'badge-expand' : short === 'EXPLORE' ? 'badge-explore' : short === 'MONITOR' ? 'badge-monitor' : 'badge-drop'}}">${{short}}</span> ${{rec.split(' - ').slice(1).join(' - ')}}
  </div>`;
}}).join('');

const recCards = document.getElementById('rec-cards');
const expandSeries = seriesData.filter(s => s.recommendation.includes('EXPAND'));
const exploreSeries = seriesData.filter(s => s.recommendation.includes('EXPLORE'));
const monitorSeries = seriesData.filter(s => s.recommendation.includes('MONITOR'));
const dropSeries = seriesData.filter(s => s.recommendation.includes('DROP'));

recCards.innerHTML = `
  <div class="rec-expand"><h3>EXPAND (${{expandSeries.length}} series)</h3>${{expandSeries.map(s => `<strong>${{s.series}}</strong> (n=${{s.n_members}}, pChEMBL=${{s.median_pchembl}})${{s.n_cliffs > 0 ? ' &mdash; discontinuous SAR flagged (coarse indicator only)' : ''}}`).join('<br>')}}</div>
  <div class="rec-explore"><h3>EXPLORE (${{exploreSeries.length}} series)</h3>${{exploreSeries.map(s => `<strong>${{s.series}}</strong> (n=${{s.n_members}}, pChEMBL=${{s.median_pchembl}})`).join('<br>')}}</div>
  <div class="rec-monitor"><h3>MONITOR/DROP (${{monitorSeries.length + dropSeries.length}} series)</h3>${{monitorSeries.concat(dropSeries).map(s => `<strong>${{s.series}}</strong> (n=${{s.n_members}}, pChEMBL=${{s.median_pchembl}})`).join('<br>')}}</div>
`;

let sortDir = {{}};
function sortTable(tableId, key, numeric) {{
  sortDir[tableId] = !sortDir[tableId];
  const dir = sortDir[tableId] ? 1 : -1;
  const table = document.getElementById(tableId);
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  rows.sort((a, b) => {{
    const cells = Array.from(table.querySelector('thead tr').cells);
    const colIdx = cells.findIndex(c => c.textContent === key);
    const av = a.cells[colIdx].textContent;
    const bv = b.cells[colIdx].textContent;
    if (numeric) return (parseFloat(av) - parseFloat(bv)) * dir;
    return av.localeCompare(bv) * dir;
  }});
  rows.forEach(r => tbody.appendChild(r));
}}

function filterTable(tableId, query) {{
  query = query.toLowerCase();
  const table = document.getElementById(tableId);
  const rows = table.querySelectorAll('tbody tr');
  rows.forEach(r => {{
    r.style.display = r.textContent.toLowerCase().includes(query) ? '' : 'none';
  }});
}}

function showTab(tabId) {{
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(tabId).classList.add('active');
  event.target.classList.add('active');
}}
</script>

<div class="licence-notice" style="margin:2rem 0 1rem;padding:1rem 1.25rem;border-left:4px solid #D4A04A;background:#faf7f0;font-size:0.9rem;line-height:1.5">
  <p style="margin:0 0 0.6rem"><strong>Data attribution.</strong> Bioactivity data from
  <strong>{chembl_label}</strong> (EMBL-EBI). ChEMBL is made available under the
  <strong>Creative Commons Attribution-ShareAlike 3.0 Unported (CC BY-SA 3.0)</strong> licence.
  <strong>Share-alike applies:</strong> any dataset derived from this report that is
  redistributed must be released under the same licence, with attribution to ChEMBL retained.</p>
  <p style="margin:0 0 0.6rem">Structures from the <strong>RCSB Protein Data Bank</strong>.
  Tools: RDKit (BSD-3), AutoDock Vina (Apache-2.0), Meeko or ADFR for receptor preparation.</p>
  <p style="margin:0"><strong>On the SAR discontinuity flags.</strong> The per-series flag above is a
  <em>coarse orientation aid</em>, not an activity-cliff statistic. Cliff counts are highly sensitive
  to the similarity criterion used &mdash; on one dataset the same pairs gave 0.13&nbsp;% under a
  Tanimoto-only rule versus 6.04&nbsp;% under a consensus rule. Do not quote these numbers as cliff
  statistics. Quantitative activity-cliff and matched-molecular-pair analysis belongs to the
  dedicated <code>matched-molecular-pairs</code> skill, which reports counts under every criterion
  with the criterion named.</p>
</div>
</body>
</html>
"""

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        f.write(html)
    print(f"HTML report saved to {args.output} ({len(html):,} chars)")


if __name__ == "__main__":
    main()
