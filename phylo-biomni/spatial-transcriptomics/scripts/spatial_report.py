#!/usr/bin/env python3
"""
============================================================================
PACKAGED DEFAULT-PROVIDER RENDERER — report_spatial_transcriptomics.pdf
============================================================================

Repairs Finding 1 for the report deliverable. The v3 sample built its branded
PDF from an unrecorded, candidate-authored reportlab driver outside the
receipt. This packaged module renders the PDF with ReportLab from the
provider-neutral ``report_content.json``, painting the ``pdf-report-generation``
palette (gold accent #D4A04A + functional colours) so ``assert_report_styled``
passes on the produced file's real vector colour operators. It is called only
by ``finalize_report.py`` after that module freezes the package-default provider
and its source evidence. Scientific provenance remains presentation-neutral;
conditional render provenance binds this renderer and ReportLab.

The palette is read from the live ``pdf-report-generation`` skill via
``report_qc.brand_markers`` when available (single source of truth), with the
current published values as an offline fallback, so the painted colours always
match what the brand gate looks for.

Sections follow the frozen content contract: Task Context; Methods & Sources;
Results; Conclusions & Interpretation; Limitations; References; Output Artifacts.

Usage
-----
    python scripts/spatial_report.py <run_dir>
    from spatial_report import build_report
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import (Image, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)
from reportlab.lib.styles import ParagraphStyle

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

REPORT_NAME = "report_spatial_transcriptomics.pdf"
SECTIONS = (
    "Task Context", "Methods & Sources", "Results", "Conclusions & Interpretation",
    "Limitations", "References", "Output Artifacts",
)

# Offline fallback == the published pdf-report-generation palette. Live values (below) take priority.
_FALLBACK_PALETTE = {
    "PHYLO_GOLD": "#D4A04A", "HEADING_COLOR": "#111111", "BODY_TEXT": "#2C2A26",
    "MUTED_TEXT": "#8A8378", "TABLE_ALT_ROW": "#F9F7F3", "TABLE_BORDER": "#D5CFC5",
    "LINK_COLOR": "#0563C1",
}


def _palette() -> dict:
    """Prefer the live brand palette (matches the gate exactly); fall back to published values."""
    pal = dict(_FALLBACK_PALETTE)
    try:
        import report_qc
        live = report_qc.brand_markers()
        if isinstance(live, dict) and live.get("PHYLO_GOLD"):
            pal.update({k: v for k, v in live.items() if isinstance(v, str)})
    except Exception:
        pass
    return pal


def _load_json(p: Path, default=None):
    if p.is_file() and p.stat().st_size:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return default
    return default


def build_report(run_dir: str | Path, out_pdf: str | Path | None = None,
                 infographic_path: str | Path | None = None,
                 content_path: str | Path | None = None) -> Path:
    run_dir = Path(run_dir)
    out_pdf = Path(out_pdf) if out_pdf else run_dir / REPORT_NAME
    if out_pdf.exists() or out_pdf.is_symlink():
        raise FileExistsError(f"refusing to overwrite pre-existing PDF: {out_pdf}")
    pal = _palette()
    C = {k: HexColor(v) for k, v in pal.items()}
    gold = C["PHYLO_GOLD"]
    heading_c = C.get("HEADING_COLOR", HexColor("#111111"))
    body_c = C.get("BODY_TEXT", HexColor("#2C2A26"))
    muted_c = C.get("MUTED_TEXT", HexColor("#8A8378"))
    border_c = C.get("TABLE_BORDER", HexColor("#D5CFC5"))
    altrow_c = C.get("TABLE_ALT_ROW", HexColor("#F9F7F3"))

    content_file = Path(content_path) if content_path else run_dir / "report_content.json"
    content = _load_json(content_file, {}) or {}
    if content.get("schema") != "spatial-transcriptomics-report-content/1":
        raise ValueError(f"default renderer requires verified report_content.json: {content_file}")
    if content.get("presentation") is not None:
        raise ValueError("report_content.json must remain presentation-neutral")
    facts = content.get("facts", {}) or {}
    prov = content.get("scientific_provenance", {}) or {}
    decision = content.get("platform_decision", {}) or {}
    manifest = content.get("figure_placements", []) or []

    platform = facts.get("platform", prov.get("platform", "?"))
    unit = "cells" if platform == "Xenium" else "spots"
    qc = facts.get("qc", {})
    feats = facts.get("features", {})
    svg = facts.get("svg_significance", {})
    thr = facts.get("thresholds", {})

    title_style = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=19,
                                 textColor=heading_c, spaceAfter=2, leading=23)
    sub_style = ParagraphStyle("sub", fontName="Helvetica", fontSize=10, textColor=muted_c,
                               spaceAfter=8, leading=13)
    h_style = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=14, textColor=heading_c,
                             spaceBefore=14, spaceAfter=4, leading=17)
    body_style = ParagraphStyle("body", fontName="Helvetica", fontSize=10, textColor=body_c,
                                spaceAfter=6, leading=14, alignment=TA_LEFT)
    cap_style = ParagraphStyle("cap", fontName="Helvetica-Oblique", fontSize=8.5,
                               textColor=muted_c, spaceAfter=10, leading=11)

    def gold_rule():
        """A gold accent bar — a vector fill op the brand gate reads (accent marker)."""
        t = Table([[""]], colWidths=[6.9 * inch], rowHeights=[3])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), gold),
                               ("LINEBELOW", (0, 0), (-1, -1), 0, gold)]))
        return t

    def heading(text):
        return [Paragraph(text, h_style), gold_rule(), Spacer(1, 5)]

    hdr_cell = ParagraphStyle("hdr_cell", fontName="Helvetica-Bold", fontSize=9,
                              textColor=HexColor("#FFFFFF"), leading=11)
    key_cell = ParagraphStyle("key_cell", fontName="Helvetica-Bold", fontSize=9,
                              textColor=body_c, leading=11)
    val_cell = ParagraphStyle("val_cell", fontName="Helvetica", fontSize=9,
                              textColor=body_c, leading=11)

    def kv_table(rows):
        # Cells are wrapping, XML-escaped Paragraphs so a long label (e.g. the SVG-significance row,
        # which contains '<') never overruns its column into the value cell.
        data = [[Paragraph("Field", hdr_cell), Paragraph("Value", hdr_cell)]]
        for k, v in rows:
            data.append([Paragraph(_xml_escape(str(k)), key_cell),
                         Paragraph(_xml_escape(str(v)), val_cell)])
        t = Table(data, colWidths=[2.7 * inch, 4.2 * inch])
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), gold),                 # gold header fill
            ("GRID", (0, 0), (-1, -1), 0.5, border_c),             # warm-gray grid lines
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        for r in range(1, len(data)):
            if r % 2 == 0:
                style.append(("BACKGROUND", (0, r), (-1, r), altrow_c))  # alt-row shading
        t.setStyle(TableStyle(style))
        return t

    story = []
    story.append(Paragraph("Spatial Transcriptomics Analysis Report", title_style))
    story.append(Paragraph(
        f"Platform: {platform} &nbsp;|&nbsp; scientific preparation: scripts/run_spatial.py prepare "
        f"&nbsp;|&nbsp; default presentation: scripts/spatial_report.py", sub_style))
    story.append(gold_rule())
    story.append(Spacer(1, 8))

    # Summary infographic (GenerateImage) — embedded as the first image on page 1.
    info_path = Path(infographic_path) if infographic_path else None
    if info_path and info_path.is_file():
        story.append(Image(str(info_path), width=6.0 * inch, height=6.0 * inch * 0.62,
                           kind="proportional"))
        story.append(Paragraph("Summary infographic (generated via Biomni GenerateImage).", cap_style))

    # 1 — Task Context
    story += heading("Task Context")
    adjacency_label = facts.get(
        "adjacency_relationship_label", "cell-type adjacency"
    )
    niche_selection = facts.get("niche_selection", {})
    gene_universe = svg.get(
        "gene_universe_definition", "tested gene universe not recorded"
    )
    task_context = (
        "This report answers, for one 10x spatial transcriptomics bundle, which platform it is and "
        "what spatial niches, spatially variable genes, between-condition differences, and "
        f"{adjacency_label} patterns it shows after platform-specific QC. The routing decision, QC branch, "
        "standardized object, and every headline number below are read from artifacts written by the "
        "packaged entrypoint; a spatial biologist uses them to decide which findings warrant "
        "follow-up."
    )
    story.append(Paragraph(task_context, body_style))
    story.append(kv_table([
        ["Detected platform", str(decision.get("platform", platform))],
        ["Routing confidence", str(decision.get("confidence", "n/a"))],
        ["Filename inference", str(decision.get("filename_inference", "false"))],
        ["Cells or spots passing QC", str(facts.get("n_cells_or_spots", "n/a"))],
    ]))

    # 2 — Methods & Sources
    story += heading("Methods & Sources")
    tv = prov.get("tool_versions", {}) or {}
    soft = ", ".join(f"{k} {v}" for k, v in sorted(tv.items()) if v) or "recorded in provenance.json"
    story.append(Paragraph(
        f"Platform routing used the manifest and input-file signature (never filenames). "
        f"QC applied the exact thresholds recorded in provenance.json "
        f"({', '.join(f'{k}={v}' for k, v in thr.items()) or 'see provenance'}); negative-control "
        f"features are split at load and excluded from biology. Niches use "
        f"{niche_selection.get('method', 'the recorded selection method')} over "
        f"{niche_selection.get('feature_definition', 'observed neighbor-composition features')}; "
        f"spatially variable genes come from squidpy [1] Moran's I [3] with the "
        f"authoritative {svg.get('fdr_column','pval_norm_fdr_bh')} column "
        f"({svg.get('method','moran_normal_asymptotic')}); replicate-aware pseudobulk DE requires "
        f"\u2265 2 biological replicates per condition or is blocked, with Benjamini\u2013Hochberg "
        f"FDR [4] correction; {adjacency_label} results are neighborhood co-localization "
        f"hypotheses with no ligand-receptor direction. "
        f"Scanpy [2] provides the single-cell analysis framework. "
        f"For Xenium runs, transcripts are filtered at Phred quality Q\u226520 per the 10x Xenium "
        f"Onboard Analysis output specification [5].", body_style))
    story.append(Paragraph(f"Software (from provenance.json:tool_versions): {soft}.", body_style))
    story.append(Paragraph(
        "Executed dependencies recorded for this run: "
        f"{', '.join(prov.get('executed_dependencies', [])) or 'see provenance.json'}. "
        "Data-bearing figures are produced by scripts/spatial_figures.py during scientific "
        "preparation. This PDF is rendered from the immutable report-content contract by the "
        "package-default scripts/spatial_report.py branch and bound separately in render provenance.",
        body_style))

    # 3 — Results
    story += heading("Results")
    story.append(kv_table([
        ["Platform", str(platform)],
        ["Cells or spots passing QC", str(facts.get("n_cells_or_spots", "n/a"))],
        ["Features: genes / controls / total",
         f"{feats.get('n_genes','?')} / {feats.get('n_control','?')} / {feats.get('n_total','?')}"],
        ["Spatial niches", str(facts.get("n_niches", "n/a"))],
        ["Niche-count selection",
         f"{niche_selection.get('method','n/a')}; candidates={niche_selection.get('candidate_k','n/a')}"],
        ["Significant spatially variable genes",
         f"{facts.get('n_svg_sig','n/a')} / {svg.get('n_genes_tested','?')} tested"],
        ["SVG tested gene universe", gene_universe],
        ["Adjacency result label", str(adjacency_label)],
        ["Differential-expression status",
         "blocked" if facts.get("de_blocked") else str(facts.get("de_n_sig", "n/a"))],
    ]))
    story.append(Spacer(1, 6))
    order = {int(e.get("step", i)): e for i, e in enumerate(manifest)}
    for step in sorted(order):
        e = order[step]
        fig_rel = e.get("file")
        if not fig_rel:
            reason = str(e.get("reason", "")).strip()
            if reason:
                story.append(Paragraph(
                    f"<b>Step {step}.</b> {_xml_escape(reason)}", body_style
                ))
            continue
        fig_path = run_dir / fig_rel
        if not fig_path.is_file():
            continue
        story.append(Image(str(fig_path), width=6.0 * inch, height=6.0 * inch * 0.62,
                           kind="proportional"))
        story.append(Paragraph(f"Figure {step}. {e.get('caption','')}", cap_style))

    # 4 — Conclusions & Interpretation
    story += heading("Conclusions & Interpretation")
    de_blocked = bool(facts.get("de_blocked", False))
    de_n_sig = int(facts.get("de_n_sig", 0))
    if de_blocked:
        de_txt = ("DE was blocked (insufficient biological replicates), so no between-condition genes are "
                  "claimed")
    elif de_n_sig == 0:
        de_txt = "0 genes differed between conditions at BH-FDR < 0.05"
    else:
        de_txt = f"{de_n_sig} genes differ between conditions at BH-FDR < 0.05"
    story.append(Paragraph(
        f"The bundle routed to {platform} and yielded {facts.get('n_niches','?')} spatial niches and "
        f"{facts.get('n_svg_sig','?')} spatially variable genes at the authoritative FDR column. "
        f"{de_txt}. {adjacency_label} results are spatial-adjacency hypotheses only. Follow-up "
        f"should prioritize the strongest spatially variable genes and co-localized pairs, "
        f"confirming that QC, segmentation, and biological replication support each call.", body_style))

    # 5 — Limitations
    story += heading("Limitations")
    limitations = (
        f"{adjacency_label} results are adjacency hypotheses with no causal reference standard or directionality. "
        "Asymptotic-normal Moran's I can flag a few negligible-effect genes as significant. Niche and "
        "cell-type labels are provisional. DE is blocked without biological replicates. "
        "All numbers are read from this run's artifacts; nothing is asserted beyond them."
    )
    story.append(Paragraph(limitations, body_style))

    # 6 — References
    story += heading("References")
    refs = [
        "[1] Palla, G., Spitzer, H., Klein, M. et al. Squidpy: a scalable framework for spatial "
        "omics analysis. Nat Methods 19, 171\u2013172 (2022). "
        "https://doi.org/10.1038/s41592-021-01358-2",
        "[2] Wolf, F.A., Angerer, P. & Theis, F.J. SCANPY: large-scale single-cell gene expression "
        "data analysis. Genome Biol 19, 15 (2018). "
        "https://doi.org/10.1186/s13059-017-1382-0",
        "[3] Moran, P.A.P. Notes on Continuous Stochastic Phenomena. Biometrika 37, 17\u201323 "
        "(1950). Moran's I spatial autocorrelation statistic.",
        "[4] Benjamini, Y. & Hochberg, Y. Controlling the False Discovery Rate: A Practical and "
        "Powerful Approach to Multiple Testing. J R Stat Soc Ser B 57, 289\u2013300 (1995).",
        "[5] 10x Genomics. Xenium Onboard Analysis output specification: cell-feature matrix "
        "contains transcripts at Phred quality Q\u226520 by default. "
        "https://www.10xgenomics.com/support/software/xenium-onboard-analysis/latest/analysis/"
        "xoa-output-at-a-glance",
    ]
    for ref in refs:
        story.append(Paragraph(ref, body_style))

    # Output-artifact table
    story += heading("Output Artifacts")
    arts = [["platform_decision.json", "routing decision"], ["qc.csv", "per-" + unit[:-1] + " QC"],
            ["niches.csv", "spatial niches"], ["spatially_variable_genes.csv", "Moran's I + FDR"],
            ["differential_expression.csv", "replicate-aware DE"], ["cci.csv", "co-localization hypotheses"],
            ["provenance.json", "params, versions, code + dependency provenance"],
            ["adata_processed.h5ad", "standardized spatial object"],
            [REPORT_NAME, "this branded report"]]
    story.append(kv_table(arts))

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(out_pdf), pagesize=letter, topMargin=52, bottomMargin=52,
                      leftMargin=54, rightMargin=54,
                      title="Spatial Transcriptomics Report").build(story)
    return out_pdf


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print("usage: spatial_report.py <run_dir>", file=sys.stderr)
        return 2
    out = build_report(argv[0])
    print(json.dumps({"report": str(out), "bytes": out.stat().st_size}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
