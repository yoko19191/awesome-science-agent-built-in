#!/usr/bin/env python3
"""Default-provider PDF renderer for Open Targets analysis results.

Uses ReportLab with the Phylo palette for the package default provider. US Letter,
vector text and tables. Content comes from the presentation-independent
report_content.json shared by every compatible style provider.

The report includes: task context, methods/sources, results, conclusions,
figures (embedded), references, and next steps — matching the delegation
sentence structure.

Usage:
    python build_report.py --content report_content.json --output report_open-targets.pdf
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, PageBreak, HRFlowable,
)
from reportlab.platypus.flowables import KeepTogether

# --- Phylo palette (from pdf-report-generation skill) -------------------------

PHYLO_GOLD = colors.HexColor("#D4A04A")
PHYLO_OFF_WHITE = colors.HexColor("#FAF9F3")
PHYLO_BLUE = colors.HexColor("#0279EE")
PHYLO_GREEN = colors.HexColor("#75A025")
PHYLO_ORANGE = colors.HexColor("#FF9400")
PHYLO_PINK = colors.HexColor("#FD9BED")
PHYLO_LIME = colors.HexColor("#E9ED4C")
PHYLO_BLACK = colors.HexColor("#000000")
HEADING_COLOR = colors.HexColor("#111111")
BODY_TEXT = colors.HexColor("#2C2A26")
MUTED_TEXT = colors.HexColor("#8A8378")
TABLE_ALT_ROW = colors.HexColor("#F9F7F3")
TABLE_BORDER = colors.HexColor("#D5CFC5")
CALLOUT_BG = colors.HexColor("#FAF9F3")
LINK_COLOR = colors.HexColor("#0563C1")

INFOGRAPHIC_MAX_WIDTH = 6.25 * inch
INFOGRAPHIC_MAX_HEIGHT = 4.22 * inch
REPORT_FIGURE_MAX_WIDTH = 6 * inch
REPORT_FIGURE_MAX_HEIGHT = 4.25 * inch

RESULTS = pathlib.Path(os.environ.get("BIOMNI_RESULTS", "/mnt/results"))


class InfographicError(Exception):
    """Raised when the mandatory infographic is missing, blank, or unreadable (OT-R2-03)."""


def proportional_image(
    path: str | pathlib.Path,
    *,
    max_width: float,
    max_height: float,
) -> Image:
    """Fit an image inside a bounding box without changing its aspect ratio."""
    source = pathlib.Path(path)
    try:
        source_width, source_height = ImageReader(str(source)).getSize()
    except Exception as exc:
        raise ValueError(f"cannot read image dimensions from {source}: {exc}") from exc
    if source_width <= 0 or source_height <= 0:
        raise ValueError(f"image has invalid dimensions: {source_width}x{source_height}")
    if max_width <= 0 or max_height <= 0:
        raise ValueError("image bounding-box dimensions must be positive")
    scale = min(max_width / source_width, max_height / source_height)
    image = Image(
        str(source),
        width=source_width * scale,
        height=source_height * scale,
    )
    image.hAlign = "CENTER"
    return image


def _verify_infographic(path: pathlib.Path) -> pathlib.Path:
    """Abort before publication if the infographic is missing, blank, or unreadable.

    The infographic is a mandatory deliverable, not optional.  A report that
    silently skips it and still returns a PDF violates the pinned contract.
    """
    if not path.exists():
        raise InfographicError(
            f"mandatory infographic not found at {path}. The report cannot be "
            "published without the GenerateImage infographic. Generate it first."
        )
    size = path.stat().st_size
    if size < 5000:
        raise InfographicError(
            f"infographic {path.name} is only {size} B — it is blank or corrupt. "
            "Regenerate it with the Biomni GenerateImage tool before publishing."
        )
    try:
        from PIL import Image as PILImage
        with PILImage.open(path) as im:
            extrema = im.convert("L").getextrema()
            if extrema[0] == extrema[1]:
                raise InfographicError(
                    f"infographic {path.name} has a single pixel value — it is blank. "
                    "Regenerate it with the Biomni GenerateImage tool."
                )
    except ImportError:
        pass  # size check already ran
    return path


def _load_facts(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _styles() -> dict:
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=ss["Title"], fontSize=18,
                                textColor=HEADING_COLOR, spaceAfter=6, fontName="Helvetica-Bold"),
        "subtitle": ParagraphStyle("subtitle", parent=ss["Normal"], fontSize=10,
                                   textColor=MUTED_TEXT, spaceAfter=12, fontName="Helvetica"),
        "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontSize=14,
                             textColor=HEADING_COLOR, spaceBefore=14, spaceAfter=6,
                             fontName="Helvetica-Bold"),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12,
                             textColor=HEADING_COLOR, spaceBefore=10, spaceAfter=4,
                             fontName="Helvetica-Bold"),
        "body": ParagraphStyle("body", parent=ss["Normal"], fontSize=10,
                               textColor=BODY_TEXT, spaceAfter=6, leading=14,
                               fontName="Helvetica"),
        "caption": ParagraphStyle("caption", parent=ss["Normal"], fontSize=8,
                                  textColor=MUTED_TEXT, spaceAfter=8, fontName="Helvetica-Oblique"),
        "callout": ParagraphStyle("callout", parent=ss["Normal"], fontSize=9,
                                  textColor=BODY_TEXT, spaceAfter=6, leading=12,
                                  fontName="Helvetica", backColor=CALLOUT_BG,
                                  borderColor=PHYLO_GOLD, borderWidth=0.5,
                                  borderPadding=6),
        "small": ParagraphStyle("small", parent=ss["Normal"], fontSize=8,
                                textColor=MUTED_TEXT, spaceAfter=4, fontName="Helvetica"),
    }


def _divider() -> HRFlowable:
    return HRFlowable(width="100%", thickness=1, color=PHYLO_GOLD,
                      spaceBefore=6, spaceAfter=6)


def _target_table(facts: dict, styles: dict) -> Table:
    """Build the top-targets results table."""
    targets = facts.get("top_targets", [])
    header = ["Rank", "Symbol", "Target ID", "Score", "Biotype"]
    rows = [header]
    for t in targets:
        rows.append([
            str(t.get("rank", "")),
            t.get("approved_symbol", ""),
            t.get("target_id", ""),
            f"{t.get('overall_association_score', 0):.3f}",
            t.get("biotype", ""),
        ])
    col_widths = [0.5 * inch, 1.0 * inch, 1.8 * inch, 0.8 * inch, 1.2 * inch]
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PHYLO_GOLD),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, TABLE_ALT_ROW]),
        ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("TEXTCOLOR", (0, 1), (-1, -1), BODY_TEXT),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return tbl


def _evidence_table(facts: dict, styles: dict) -> Table:
    """Build the evidence rows table."""
    evidence = facts.get("evidence_rows", [])
    if not evidence:
        return Paragraph("No evidence rows retrieved.", styles["body"])
    header = ["Datasource", "Datatype", "Score", "Year", "Author"]
    rows = [header]
    for e in evidence[:15]:  # cap at 15 rows for readability
        rows.append([
            e.get("datasource_id", ""),
            e.get("datatype_id", ""),
            f"{e.get('score', 0):.3f}" if e.get("score") is not None else "",
            str(e.get("publication_year", "")) if e.get("publication_year") else "",
            e.get("publication_first_author", "") or "",
        ])
    col_widths = [1.5 * inch, 1.2 * inch, 0.7 * inch, 0.6 * inch, 1.5 * inch]
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PHYLO_GOLD),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, TABLE_ALT_ROW]),
        ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("TEXTCOLOR", (0, 1), (-1, -1), BODY_TEXT),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return tbl


def _study_table(facts: dict, styles: dict) -> Table:
    """Build the GWAS study metadata table."""
    studies = facts.get("studies", [])
    if not studies:
        return Paragraph("No GWAS studies retrieved.", styles["body"])
    header = ["Study ID", "Trait", "N Samples", "PMID", "Author"]
    rows = [header]
    for s in studies:
        rows.append([
            s.get("study_id", ""),
            (s.get("trait_from_source", "") or "")[:30],
            str(s.get("n_samples", "")) if s.get("n_samples") else "",
            s.get("pubmed_id", "") or "",
            (s.get("publication_first_author", "") or "")[:20],
        ])
    col_widths = [1.2 * inch, 1.8 * inch, 0.8 * inch, 0.8 * inch, 1.2 * inch]
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PHYLO_GOLD),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, TABLE_ALT_ROW]),
        ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("TEXTCOLOR", (0, 1), (-1, -1), BODY_TEXT),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return tbl


def _credible_set_table(facts: dict, styles: dict) -> Table:
    """Build the credible-set summary table (region, lead variant, p-value)."""
    cs_list = [cs for cs in facts.get("credible_sets", []) if "error" not in cs]
    if not cs_list:
        return Paragraph("No credible sets retrieved.", styles["body"])
    header = ["Region", "Lead Variant ID", "p-value"]
    rows = [header]
    for cs in cs_list:
        pval = cs.get("pvalue")
        pval_disp = "N/A"
        if pval:
            try:
                pval_disp = f"{float(pval):.2e}"
            except (ValueError, TypeError):
                pval_disp = str(pval)
        rows.append([
            cs.get("region", "") or "",
            cs.get("variant_id", "") or "",
            pval_disp,
        ])
    col_widths = [2.2 * inch, 2.0 * inch, 1.2 * inch]
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PHYLO_GOLD),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, TABLE_ALT_ROW]),
        ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("TEXTCOLOR", (0, 1), (-1, -1), BODY_TEXT),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return tbl


def _l2g_table(facts: dict, styles: dict, top_k: int = 10) -> Table:
    """Build the top L2G-predicted genes table, sorted by score descending."""
    l2g = facts.get("l2g_predictions", [])
    if not l2g:
        return Paragraph("No L2G predictions retrieved.", styles["body"])
    sorted_l2g = sorted(l2g, key=lambda r: r.get("score", 0.0), reverse=True)
    header = ["Symbol", "Target ID", "L2G Score"]
    rows = [header]
    for rec in sorted_l2g[:top_k]:
        rows.append([
            rec.get("approved_symbol", "") or "",
            rec.get("target_id", "") or "",
            f"{rec.get('score', 0.0):.3f}",
        ])
    col_widths = [1.2 * inch, 2.4 * inch, 1.0 * inch]
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PHYLO_GOLD),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, TABLE_ALT_ROW]),
        ("GRID", (0, 0), (-1, -1), 0.5, TABLE_BORDER),
        ("TEXTCOLOR", (0, 1), (-1, -1), BODY_TEXT),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return tbl


def build_pdf(content_path: pathlib.Path, output_path: pathlib.Path) -> pathlib.Path:
    """Build the default-provider PDF from style-free content.

    Direct report_facts.json input remains accepted for existing scientific and
    infographic regression tests; production orchestration passes report_content.json.
    """
    content = _load_facts(content_path)
    facts = content.get("facts") if content.get("schema") == "open-targets-report-content/1" else content
    if not isinstance(facts, dict):
        raise ValueError("report content has no facts object")
    styles = _styles()
    story = []

    # --- Title page ---
    story.append(Paragraph("Open Targets Target Prioritization Report", styles["title"]))
    release = facts.get("release_label", "unknown release")
    disease = facts.get("disease_name", "the queried disease")
    disease_id = facts.get("disease_id", "")
    story.append(Paragraph(
        f"Disease: {disease} ({disease_id}) &nbsp;|&nbsp; Data release: {release} "
        f"&nbsp;|&nbsp; Access date: {facts.get('access_date', 'N/A')} "
        f"&nbsp;|&nbsp; Source mode: {facts.get('source_mode', 'unknown')}",
        styles["subtitle"],
    ))
    story.append(_divider())

    # --- Workflow infographic: first substantive visual, near top of page 1 ---
    # It follows only the title/source-status strip. Quantitative figures come later.
    infographic_path = RESULTS / "infographic_open_targets_workflow.png"
    _verify_infographic(infographic_path)
    # OT-R4: caption and disclosure follow the single facts-borne lineage state. A verified state may
    # claim GenerateImage provenance; any other state makes NO verification claim and discloses that
    # provenance is not independently verified. The truncated args string is never shown as a prompt.
    lineage = facts.get("infographic_lineage", {}) if isinstance(facts, dict) else {}
    lineage_verified = bool(lineage.get("verification_claim"))
    disclosure_text = str(lineage.get("disclosure", "")).strip()
    if not lineage_verified and not disclosure_text:
        disclosure_text = ("Infographic provenance is not independently verified from the platform "
                           "execution trace; no verification claim is made.")
    infographic_block = [
        Paragraph("Workflow Overview", styles["h1"]),
        Paragraph(
            "This qualitative infographic depicts the Open Targets API workflow and the "
            "API-versus-bulk data boundary. It contains no run-specific scientific values.",
            styles["body"],
        ),
    ]
    inf_img = proportional_image(
        infographic_path,
        max_width=INFOGRAPHIC_MAX_WIDTH,
        max_height=INFOGRAPHIC_MAX_HEIGHT,
    )
    inf_img.hAlign = "CENTER"
    if lineage_verified:
        caption = ("Figure 1: Open Targets API workflow and API/bulk boundary (qualitative; "
                   "generated by Biomni GenerateImage; provenance verified from the execution trace).")
    else:
        caption = ("Figure 1: Open Targets API workflow and API/bulk boundary (qualitative; "
                   "produced during the run via Biomni GenerateImage).")
    infographic_block.extend([inf_img, Paragraph(caption, styles["caption"])])
    if not lineage_verified:
        infographic_block.append(Paragraph(disclosure_text, styles["caption"]))
    story.append(KeepTogether(infographic_block))

    # --- Task context ---
    story.append(Paragraph("Task Context", styles["h1"]))
    story.append(Paragraph(
        f"This report prioritizes therapeutic targets for {disease} ({disease_id}) using the "
        f"Open Targets Platform [1] GraphQL API (release {release}). The analysis resolves disease "
        f"identity, ranks a bounded target set by overall association score, retrieves supporting "
        f"evidence for the top target, and inspects GWAS study metadata and credible sets. "
        f"All scores are integrated prioritization metrics, not effect estimates, replication, "
        f"causal proof, or clinical validation.",
        styles["body"],
    ))

    # --- Methods / Sources ---
    story.append(Paragraph("Methods and Sources", styles["h1"]))
    # Derive executed-operation count from the operation ledger summary (OT-R2-06)
    ledger_summary = facts.get("operation_ledger_summary", {})
    executed_count = ledger_summary.get("total_attempts", 0)
    executed_ops = facts.get("executed_operations", [])
    executed_names = ", ".join(executed_ops) if executed_ops else f"{executed_count} operation(s)"
    registered_count = facts.get("registered_operations_count", 9)
    schema_smoke_executed = facts.get("schema_smoke_executed", False)
    if schema_smoke_executed:
        smoke_clause = (
            f"{registered_count} operation families are registered and "
            f"separately validated by live schema smoke; see the Operation Ledger Summary below. "
        )
    else:
        smoke_clause = (
            f"{registered_count} operation families are registered; "
            f"live schema smoke validation is available as an opt-in step "
            f"(scripts/schema_smoke.py --live) and was not executed in this run. "
        )
    story.append(Paragraph(
        f"Data source: Open Targets Platform [1] GraphQL API v4 [2] "
        f"(https://api.platform.opentargets.org/api/v4/graphql). "
        f"Release: {release}. API version: {facts.get('api_version', 'N/A')}. "
        f"Access date: {facts.get('access_date', 'N/A')} [4]. "
        f"The API requires no authentication. Data is released under CC0 1.0 [5]. "
        f"Bulk data downloads [3] are also available. "
        f"{executed_count} GraphQL operation(s) were executed in this representative run "
        f"({executed_names}) through a production client with an "
        f"immutable operation ledger. {smoke_clause}"
        f"All HTTP 200 body-level GraphQL errors were rejected. "
        f"Pagination was bounded (max {facts.get('max_pages', 'N/A')} pages, "
        f"max {facts.get('max_rows', 'N/A')} rows). "
        f"Completeness classification: {facts.get('completeness', 'bounded_sample')}.",
        styles["body"],
    ))
    story.append(Paragraph(
        "Records were serialized through a discriminated semantic schema with record_type, "
        "metric name, value, scale, datasource_id (only for evidence records), and interpretation "
        "boundary. L2G and colocalisation records carry null datasource_id with an explicit "
        "not-applicable reason.",
        styles["body"],
    ))

    # --- Results ---
    story.append(Paragraph("Results", styles["h1"]))

    story.append(Paragraph("Target Ranking", styles["h2"]))
    n_targets = len(facts.get("top_targets", []))
    total_reported = facts.get("total_associated_targets", "N/A")
    story.append(Paragraph(
        f"Retrieved {n_targets} of {total_reported} total associated targets "
        f"(bounded sample, not exhaustive). "
        f"Completeness: {facts.get('completeness', 'bounded_sample')}.",
        styles["body"],
    ))
    story.append(_target_table(facts, styles))
    story.append(Spacer(1, 8))

    # Figure 1 — use KeepTogether so the heading stays with the figure (OT-R2-05)
    fig1_path = RESULTS / "figures" / "figure_1_target_ranking.png"
    if fig1_path.exists():
        fig1_block = []
        fig1_block.append(Paragraph("Figure 2: Target Ranking", styles["h2"]))
        img = proportional_image(
            fig1_path,
            max_width=REPORT_FIGURE_MAX_WIDTH,
            max_height=REPORT_FIGURE_MAX_HEIGHT,
        )
        img.hAlign = "CENTER"
        fig1_block.append(img)
        fig1_block.append(Paragraph(
            f"Top {n_targets} targets ranked by overall association score for {disease}. "
            f"Scores are integrated prioritization metrics (0-1), not effect estimates.",
            styles["caption"],
        ))
        story.append(KeepTogether(fig1_block))

    # Figure 2 — use KeepTogether so the heading stays with the figure (OT-R2-05)
    fig2_path = RESULTS / "figures" / "figure_2_datatype_heatmap.png"
    if fig2_path.exists():
        fig2_block = []
        fig2_block.append(Paragraph("Figure 3: Datatype Contribution Heatmap", styles["h2"]))
        img2 = proportional_image(
            fig2_path,
            max_width=REPORT_FIGURE_MAX_WIDTH,
            max_height=REPORT_FIGURE_MAX_HEIGHT,
        )
        img2.hAlign = "CENTER"
        fig2_block.append(img2)
        fig2_block.append(Paragraph(
            f"Per-datatype contribution scores for the top {n_targets} targets. "
            f"Each cell is a prioritization metric (0-1), not an effect estimate or causal proof.",
            styles["caption"],
        ))
        story.append(KeepTogether(fig2_block))

    # Evidence
    story.append(Paragraph("Evidence Sample (Top Target, Bounded)", styles["h2"]))
    top_target = facts.get("top_targets", [{}])[0] if facts.get("top_targets") else {}
    story.append(Paragraph(
        f"Evidence rows for {top_target.get('approved_symbol', 'top target')} "
        f"({top_target.get('target_id', '')}): "
        f"{facts.get('evidence_count', 0)} total, {len(facts.get('evidence_rows', []))} retrieved.",
        styles["body"],
    ))
    story.append(_evidence_table(facts, styles))
    story.append(Spacer(1, 8))

    # GWAS studies — check concordance before presenting as disease context (OT-R2-07)
    study_concordance = facts.get("study_concordance", {})
    is_concordant = study_concordance.get("concordant", False)
    if facts.get("studies"):
        if is_concordant:
            story.append(Paragraph("GWAS Study Metadata", styles["h2"]))
            story.append(_study_table(facts, styles))
            story.append(Spacer(1, 8))
        else:
            # Non-concordant study: emit separately labeled independent context (OT-R2-07)
            story.append(Paragraph("Independent Study Context (Not Disease-Supporting)", styles["h2"]))
            story.append(Paragraph(
                f"The following GWAS study was retrieved but its returned trait "
                f"({study_concordance.get('study_trait', 'unknown')}) does not match the queried "
                f"disease ({disease}). It is presented as independent context only and is excluded "
                f"from target-support conclusions. Do not treat this study as supporting evidence "
                f"for the {disease} target ranking.",
                styles["callout"],
            ))
            story.append(_study_table(facts, styles))
            story.append(Spacer(1, 8))

    # Credible sets
    if facts.get("credible_sets"):
        story.append(Paragraph("Credible Sets", styles["h2"]))
        cs_list = facts.get("credible_sets", [])
        cs_intro = (
            f"{len(cs_list)} credible set(s) retrieved. "
            f"Each is a fine-mapped genetic-locus annotation, not an effect estimate or causal proof."
        )
        if not is_concordant:
            cs_intro += (
                f" These credible sets derive from the non-concordant study "
                f"{study_concordance.get('study_id', '')} (trait: "
                f"{study_concordance.get('study_trait', 'unknown')}) and are presented as "
                f"independent context only, excluded from the {disease} target-support conclusions."
            )
        story.append(Paragraph(cs_intro, styles["body"]))

        # --- Credible-set / L2G / colocalisation detail subsection ---
        # All values are rendered from report_facts.json; no new API calls.

        # 1. Credible-set table (region, lead variant ID, p-value)
        cs_clean = [cs for cs in cs_list if "error" not in cs]
        if cs_clean:
            story.append(_credible_set_table(facts, styles))
            story.append(Spacer(1, 6))

        # 2. Top L2G-predicted genes (sorted by score, top ~10)
        l2g_list = facts.get("l2g_predictions", [])
        if l2g_list:
            story.append(_l2g_table(facts, styles))
            story.append(Paragraph(
                "L2G scores are prioritization metrics (0-1) indicating the likely causal "
                "gene at a locus; they are not effect estimates, replication, causal proof, "
                "or clinical validation.",
                styles["caption"],
            ))
            story.append(Spacer(1, 6))

        # 3. Colocalisation summary (one line)
        coloc_list = facts.get("colocalisation", [])
        if coloc_list:
            h4_high = sum(1 for c in coloc_list if (c.get("h4") or 0) >= 0.9)
            coloc_studies = {
                c.get("other_study_id") for c in coloc_list
                if c.get("other_study_id")
            }
            story.append(Paragraph(
                f"Colocalisation: {h4_high} of {len(coloc_list)} entries have H4 >= 0.9, "
                f"spanning {len(coloc_studies)} distinct colocallysing "
                f"stud{'y' if len(coloc_studies) == 1 else 'ies'}. "
                f"Colocalisation H4 is supportive evidence that two signals share a causal "
                f"variant; it is not causal proof or clinical validation.",
                styles["body"],
            ))
            story.append(Spacer(1, 6))

        # 4. Intersection of top-N targets with L2G predictions
        if l2g_list and facts.get("top_targets"):
            top_symbols = {
                t.get("approved_symbol", "") for t in facts.get("top_targets", [])
                if t.get("approved_symbol")
            }
            l2g_symbols = {
                rec.get("approved_symbol", "") for rec in l2g_list
                if rec.get("approved_symbol")
            }
            supported = top_symbols & l2g_symbols
            sorted_l2g_all = sorted(
                l2g_list, key=lambda r: r.get("score", 0.0), reverse=True
            )
            complementary = [
                rec.get("approved_symbol", "") for rec in sorted_l2g_all
                if rec.get("approved_symbol")
                and rec.get("approved_symbol") not in top_symbols
            ]
            supported_str = ", ".join(sorted(supported)) if supported else "none"
            # Deduplicate complementary while preserving score-sorted order
            complementary_str = (
                ", ".join(dict.fromkeys(complementary)) if complementary else "none"
            )
            story.append(Paragraph(
                f"Of the top-{n_targets} ranked targets, {supported_str} have L2G support "
                f"from this study. Top L2G-predicted genes not among the top-{n_targets} "
                f"ranked targets (complementary candidates): {complementary_str}. "
                f"L2G support is a prioritization signal, not causal proof.",
                styles["body"],
            ))

    # --- Operation ledger summary ---
    story.append(Paragraph("Operation Ledger Summary", styles["h2"]))
    ledger = facts.get("operation_ledger_summary", {})
    story.append(Paragraph(
        f"Total attempts: {ledger.get('total_attempts', 'N/A')}. "
        f"Successes: {ledger.get('successes', 'N/A')}. "
        f"Failures: {ledger.get('failures', 'N/A')}. "
        f"By state: {json.dumps(ledger.get('by_state', {}))}. "
        f"All failures and recoveries are preserved in the immutable operation ledger.",
        styles["body"],
    ))

    # --- Conclusions ---
    story.append(Paragraph("Conclusions", styles["h1"]))
    if is_concordant:
        story.append(Paragraph(
            f"The top {n_targets} targets associated with {disease} were identified using the Open "
            f"Targets Platform [1] (release {release}). The ranking reflects integrated evidence from "
            f"multiple datasources and is a prioritization metric, not a causal claim. "
            f"The bounded sample of {n_targets} targets out of {total_reported} total should not be "
            f"treated as exhaustive. Supporting evidence, GWAS study metadata, and credible sets "
            f"provide context for the top-ranked targets but do not constitute effect estimates, "
            f"replication, causal proof, or clinical validation.",
            styles["body"],
        ))
    else:
        study_trait = study_concordance.get("study_trait", "unknown")
        story.append(Paragraph(
            f"The top {n_targets} targets associated with {disease} were identified using the Open "
            f"Targets Platform [1] (release {release}). The ranking reflects integrated evidence from "
            f"multiple datasources and is a prioritization metric, not a causal claim. "
            f"The bounded sample of {n_targets} targets out of {total_reported} total should not be "
            f"treated as exhaustive. Supporting evidence provides context for the top-ranked targets "
            f"but does not constitute effect estimates, replication, causal proof, or clinical "
            f"validation.",
            styles["body"],
        ))
        story.append(Paragraph(
            f"The GWAS study {study_concordance.get('study_id', '')} (trait: {study_trait}) is "
            f"not concordant with {disease} and is presented as independent study context only. "
            f"It is excluded from the target-support conclusions above.",
            styles["callout"],
        ))

    # --- Caveats ---
    story.append(Paragraph("Scientific Caveats", styles["h1"]))
    caveats = facts.get("caveats", [])
    for c in caveats:
        story.append(Paragraph(f"• {c}", styles["body"]))

    # --- References ---
    story.append(Paragraph("References", styles["h1"]))
    story.append(Paragraph(
        "[1] Ochoa D et al., The next-generation Open Targets Platform: reimagined, "
        "redesigned, rebuilt. Nucleic Acids Res. 2023;51(D1):D1353-D1359. "
        "doi:10.1093/nar/gkac1046",
        styles["body"],
    ))
    story.append(Paragraph(
        "[2] Open Targets Platform API documentation. "
        "https://platform-docs.opentargets.org/data-access/graphql-api",
        styles["body"],
    ))
    story.append(Paragraph(
        "[3] Open Targets Platform bulk data downloads. "
        "https://platform-docs.opentargets.org/data-access/datasets",
        styles["body"],
    ))
    story.append(Paragraph(
        f"[4] Data release: {release}. API version: {facts.get('api_version', 'N/A')}. "
        f"Access date: {facts.get('access_date', 'N/A')}.",
        styles["body"],
    ))
    story.append(Paragraph(
        "[5] Data license: CC0 1.0 Universal (Public Domain Dedication).",
        styles["body"],
    ))

    # --- Next steps ---
    story.append(Paragraph("Suggested Next Steps", styles["h1"]))
    next_steps = facts.get("next_steps", [])
    for ns in next_steps:
        story.append(Paragraph(f"• {ns}", styles["body"]))

    # Build the PDF
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path), pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        title="Open Targets Target Prioritization Report",
    )
    doc.build(story)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the default-provider PDF report")
    parser.add_argument("--content", required=True, help="path to report_content.json")
    parser.add_argument("--output", default="report_open-targets.pdf", help="output PDF path")
    args = parser.parse_args()

    path = build_pdf(pathlib.Path(args.content), pathlib.Path(args.output))
    print(f"Report written to {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
