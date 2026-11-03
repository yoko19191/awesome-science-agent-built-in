#!/usr/bin/env python3
# =====================================================================================
# 07_build_report.py  --  Phylo-branded PDF report for a cytometry clustering run.
#
# DATA-DRIVEN: reads <outdir>/run_manifest.json (written by the R pipeline) + figures/
# + tables/ + qc_transform_log.txt. It does NOT hardcode any dataset numbers -- every
# value comes from the manifest/CSVs of THIS run. Sections are emitted conditionally
# Differential abundance appears only if that step ran.
#
# Required infographic: generated up-stream with GenerateImage and passed via
# --infographic <png>. Missing input blocks report assembly.
#
# Report structure (adapts to what exists):
#   Title -> Executive Summary -> 1 Introduction (dataset-at-a-glance, provenance)
#   -> 2 Methods (transform/compensation/QC/clustering, all from the log)
#   -> 3 Results (3.1 populations+heatmap, 3.2 UMAP/tSNE, 3.3 abundance)
#   -> 5 Differential abundance (conditional; states test-vs-descriptive mode honestly)
#   -> 6 Conclusions -> 7 Limitations -> 8 Next steps -> References -> Data & Outputs
#
# Usage:
#   python 07_build_report.py --outdir <outdir> [--infographic <png>] \
#     [--title "Cytometry clustering & annotation report"]
# =====================================================================================
import argparse, json, os, glob, datetime, shutil, csv, math
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                Image, PageBreak, KeepTogether, HRFlowable)
from pypdf import PdfReader

# ---- Phylo brand ----------------------------------------------------------------------
PHYLO_GOLD = HexColor("#D4A04A"); HEADING = HexColor("#111111"); BODY = HexColor("#2C2A26")
MUTED = HexColor("#8A8378"); TBL_HEAD_BG = PHYLO_GOLD; TBL_HEAD_FG = HexColor("#FFFFFF")
TBL_ALT = HexColor("#F9F7F3"); TBL_BORDER = HexColor("#D5CFC5"); CALLOUT_BG = HexColor("#FAF9F3")

ap = argparse.ArgumentParser()
ap.add_argument("--outdir", default="/mnt/results/cyto_run")
ap.add_argument("--infographic", default="")
ap.add_argument("--title", default="Cytometry Clustering & Annotation Report")
ap.add_argument("--out", default="")
A = ap.parse_args()
FIG = os.path.join(A.outdir, "figures"); TAB = os.path.join(A.outdir, "tables")
# Reports stay with the case outputs. Evaluation inputs and scripts remain outside this directory.
_default_pdf = f"report_cytometry_{datetime.date.today().isoformat()}.pdf"
out_pdf = os.path.join(A.outdir, os.path.basename(A.out) if A.out else _default_pdf)

# The PDF report is MANDATORY and must embed a GenerateImage infographic (skill policy).
# Fail fast if the infographic is missing rather than silently omitting it.
if not A.infographic or not os.path.exists(A.infographic):
    raise SystemExit(
        "ERROR: 07_build_report.py requires an infographic. Generate a one-page workflow "
        "infographic with the Biomni GenerateImage tool and pass it via --infographic <png>. "
        "The flow-cytometry-analysis skill makes the PDF report mandatory WITH a GenerateImage "
        f"infographic; refusing to build a report without one. (got --infographic={A.infographic!r})")

# The embedded raster must live inside the run directory so artifact_manifest.csv (refreshed below)
# and verify_run_complete.py cover the exact bytes the PDF shows. Copy it in when it was generated
# elsewhere; an infographic already inside the run directory is used in place.
def _inside(path, root):
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)
    except ValueError:
        return False
if not _inside(A.infographic, A.outdir):
    _name = os.path.basename(A.infographic)
    if "infographic" not in _name.lower():
        _name = "infographic_" + _name
    _dest = os.path.join(A.outdir, _name)
    os.makedirs(A.outdir, exist_ok=True)
    shutil.copyfile(A.infographic, _dest)
    print(f"Copied infographic into the run directory: {_dest}")
    A.infographic = _dest

# ---- load manifest (single source of truth for this run) ------------------------------
man_path = os.path.join(A.outdir, "run_manifest.json")
if not os.path.isfile(man_path):
    raise SystemExit("Run build_manifest.R before report assembly; run_manifest.json is required.")
with open(man_path) as fh:
    man = json.load(fh)
def g(key, default="n/a"):
    v = man.get(key); return default if v in (None, "") else v
def fig(name):  # return figure path if it exists (prefer png for embedding)
    p = os.path.join(FIG, name)
    return p if os.path.exists(p) else None
def read_csv_rows(name, limit=25):
    p = os.path.join(TAB, name)
    if not os.path.exists(p): p = os.path.join(A.outdir, name)
    if not os.path.exists(p): return None, None
    import csv
    with open(p) as fh:
        r = list(csv.reader(fh))
    return (r[0], r[1:1 + limit]) if r else (None, None)
def csv_data_row_count(name):
    p = os.path.join(TAB, name)
    if not os.path.exists(p): p = os.path.join(A.outdir, name)
    if not os.path.exists(p): return 0
    import csv
    with open(p) as fh:
        return max(0, sum(1 for _ in csv.reader(fh)) - 1)

TITLE = A.title
doc = SimpleDocTemplate(out_pdf, pagesize=letter, topMargin=52, bottomMargin=52,
                        leftMargin=60, rightMargin=60, title=TITLE)
S = getSampleStyleSheet()
S.add(ParagraphStyle(name="RTitle", fontName="Helvetica-Bold", fontSize=24, textColor=HEADING, leading=30, spaceAfter=6))
S.add(ParagraphStyle(name="Sub", fontName="Helvetica", fontSize=11, textColor=PHYLO_GOLD, spaceAfter=4))
S.add(ParagraphStyle(name="Attr", fontName="Helvetica-Oblique", fontSize=10, textColor=MUTED, spaceAfter=8))
S.add(ParagraphStyle(name="H1", fontName="Helvetica-Bold", fontSize=16, textColor=HEADING, spaceBefore=20, spaceAfter=9))
S.add(ParagraphStyle(name="H2", fontName="Helvetica-Bold", fontSize=12.5, textColor=HEADING, spaceBefore=12, spaceAfter=6))
S.add(ParagraphStyle(name="Body2", fontName="Helvetica", fontSize=10.5, textColor=BODY, alignment=TA_JUSTIFY, leading=15, spaceAfter=8))
S.add(ParagraphStyle(name="BulletBody", parent=S["Body2"], leftIndent=14, firstLineIndent=0,
                     bulletIndent=0, alignment=0, spaceAfter=6))
S.add(ParagraphStyle(name="Cap", fontName="Helvetica-Oblique", fontSize=9, textColor=MUTED, alignment=TA_CENTER, spaceAfter=14))
S.add(ParagraphStyle(name="Cell", fontName="Helvetica", fontSize=9, textColor=BODY, leading=12))
S.add(ParagraphStyle(name="CellH", fontName="Helvetica-Bold", fontSize=9, textColor=TBL_HEAD_FG, leading=12))
S.add(ParagraphStyle(name="Ref", fontName="Helvetica", fontSize=8.5, textColor=BODY, leading=12, spaceAfter=4))

def divider(w=480): return HRFlowable(width=w, thickness=1, color=PHYLO_GOLD, spaceAfter=10, spaceBefore=4)
def _safe_str(v):
    """Coerce nullable DataFrame/CSV values to a ReportLab-safe string.

    NaN/None/NA/inf become 'n/a' so Paragraph() never receives a bare float('nan')
    (which str() renders as 'nan' and can crash XML parsing in some ReportLab
    versions) or an unescaped ampersand from a stray value."""
    import math
    if v is None:
        return "n/a"
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return "n/a"
    # pandas.NA / numpy.nan (imported lazily so the script runs without pandas)
    try:
        import pandas as _pd
        if v is _pd.NA:
            return "n/a"
    except Exception:
        pass
    s = str(v)
    if s.lower() in ("nan", "none", "<na>", "na"):
        return "n/a"
    return s
def callout(text):
    t = Table([[Paragraph(text, S["Body2"])]], colWidths=[452]); t.hAlign = "CENTER"
    t.setStyle(TableStyle([("BACKGROUND", (0,0),(-1,-1), CALLOUT_BG), ("BOX",(0,0),(-1,-1),0.5,TBL_BORDER),
        ("LINEBEFORE",(0,0),(0,-1),3,PHYLO_GOLD), ("TOPPADDING",(0,0),(-1,-1),12),
        ("BOTTOMPADDING",(0,0),(-1,-1),12),("LEFTPADDING",(0,0),(-1,-1),14),("RIGHTPADDING",(0,0),(-1,-1),14)]))
    return t
def kv_table(rows, w=(150, 302)):
    data = [[Paragraph(f"<b>{_safe_str(k)}</b>", S["Cell"]), Paragraph(_safe_str(v), S["Cell"])] for k, v in rows]
    t = Table(data, colWidths=list(w)); t.hAlign = "CENTER"
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(0,-1),TBL_ALT),("GRID",(0,0),(-1,-1),0.5,TBL_BORDER),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8)]))
    return t
def data_table(header, rows, widths):
    if not header: return None
    data = [[Paragraph(_safe_str(h), S["CellH"]) for h in header]] + \
           [[Paragraph(_safe_str(c), S["Cell"]) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1); t.hAlign = "CENTER"
    style = [("BACKGROUND",(0,0),(-1,0),TBL_HEAD_BG),("TEXTCOLOR",(0,0),(-1,0),TBL_HEAD_FG),
        ("GRID",(0,0),(-1,-1),0.5,TBL_BORDER),("BOX",(0,0),(-1,-1),0.75,TBL_BORDER),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6)]
    for i in range(2, len(data), 2): style.append(("BACKGROUND",(0,i),(-1,i),TBL_ALT))
    t.setStyle(TableStyle(style)); return t
def add_figure(story, path, caption, w=460, h=None):
    if not path: return
    from PIL import Image as PImage
    iw, ih = PImage.open(path).size
    if h is None: h = w * ih / iw
    img = Image(path, width=w, height=h); img.hAlign = "CENTER"
    story.append(KeepTogether([img, Spacer(1, 4), Paragraph(caption, S["Cap"])]))

story = []
date_str = datetime.date.today().strftime("%B %d, %Y")

# ---------------- Title + Executive summary --------------------------------------------
story += [Spacer(1, 30), Paragraph(TITLE, S["RTitle"]),
          Paragraph(f"{g('modality','Cytometry').upper()} · unsupervised clustering · annotation", S["Sub"]),
          Paragraph(f"<i>Generated by Biomni  |  {date_str}</i>", S["Attr"]), divider()]

if A.infographic and os.path.exists(A.infographic):
    add_figure(story, A.infographic, "Figure 1. Analysis workflow overview.", w=470)

story.append(PageBreak())
story.append(Paragraph("Executive Summary", S["H1"]))
n_clusters = g("n_clusters"); n_pops = g("n_populations"); chosen = g("chosen_k")
_states = man.get("analysis_state") if isinstance(man.get("analysis_state"), dict) else {}
_overall_state = str(man.get("overall_state", "INCOMPLETE")).upper()
_workflow_state = str(man.get("workflow_state", _overall_state)).upper()
_scientific_state = str(man.get("scientific_state", _overall_state)).upper()
_annotation_confirmed = str(_states.get("annotation", "")).upper().startswith("PASS")
_resolution_confirmed = str(_states.get("resolution", "")).upper().startswith("PASS")
_population_word = "reviewed output groups" if _annotation_confirmed else "candidate cluster groups"
_annotation_source = "annotation_applied.csv" if os.path.exists(os.path.join(A.outdir, "annotation_applied.csv")) else "annotation_template.csv"
_annotation_header_all, _annotation_rows_all = read_csv_rows(_annotation_source, limit=1000000)
_supported_annotations = 0
_unresolved_annotations = 0
if _annotation_header_all and "proposal_status" in _annotation_header_all:
    _status_idx = _annotation_header_all.index("proposal_status")
    _statuses = [row[_status_idx].strip().upper() for row in _annotation_rows_all if _status_idx < len(row)]
    _supported_annotations = sum(value == "SUPPORTED" for value in _statuses)
    _unresolved_annotations = sum(value != "SUPPORTED" for value in _statuses)
summ = (f"This report summarizes an unsupervised analysis of a {g('modality')} cytometry dataset "
        f"comprising <b>{g('n_cells')}</b> cells across <b>{g('n_samples')}</b> sample(s) and "
        f"<b>{g('n_markers')}</b> markers recorded for clustering. The recorded output contains "
        f"<b>{n_pops}</b> {_population_word} at resolution "
        f"<b>{chosen}</b>. ")
if _supported_annotations or _unresolved_annotations:
    summ += (f"Measured-marker evidence supported coarse labels for <b>{_supported_annotations}</b> "
             f"metacluster(s); <b>{_unresolved_annotations}</b> remained explicitly unresolved. ")
story.append(Paragraph(summ, S["Body2"]))
if _overall_state != "PASS":
    story.append(callout(
        f"WORKFLOW STATE: <b>{_workflow_state}</b>. SCIENTIFIC STATE: <b>{_scientific_state}</b>. "
        "Workflow completion, candidate selection, and biological identity validation are reported "
        "separately in run_manifest.json. A reviewed selection is not biological confirmation."))

# key-findings callout (only true, manifest-derived statements)
kf = []
if man.get("top_population") and _annotation_confirmed: kf.append(f"Most abundant reviewed output group: <b>{man['top_population']}</b> ({man.get('top_population_pct','?')}% of cells). A reviewed group may remain biologically unresolved.")
if man.get("diff_abundance", {}).get("mode") == "descriptive_only":
    kf.append("Differential abundance reported <b>descriptively only</b>: group sizes were below the pre-registered threshold for valid statistical testing.")
if kf:
    story.append(callout("<b>Key points.</b><br/>" + "<br/>".join(f"• {x}" for x in kf)))

# ---------------- 1 Introduction -------------------------------------------------------
story += [PageBreak(), Paragraph("1. Introduction", S["H1"])]
story.append(Paragraph(
    "High-dimensional cytometry (flow and mass cytometry/CyTOF) measures dozens of protein "
    "markers per cell, enabling detailed immune profiling. Manual gating is labor-intensive and "
    "operator-dependent; unsupervised clustering offers a reproducible alternative. This analysis "
    "applies a modality-aware pipeline: transformation and compensation appropriate to the platform, "
    "explicit quality control, FlowSOM self-organizing-map clustering with consensus meta-clustering, "
    "marker-based annotation, and per-sample abundance quantification.", S["Body2"]))
story.append(Paragraph("1.1 Dataset at a glance", S["H2"]))
glance = [("Modality", g("modality")), ("Cells (total)", g("n_cells")), ("Samples", g("n_samples")),
          ("Markers (clustering)", g("n_markers")), ("Transformation", g("transform")),
          ("Compensation", g("compensation")), ("QC applied", g("qc_summary")),
          ("Cells removed in QC", g("qc_removed"))]
# Show the gating engine only when the manifest records it.
if g("gate_engine") not in ("n/a", None):
    glance.append(("Gating engine", g("gate_engine")))
# provenance ONLY from metadata — never inferred
if man.get("provenance"): glance.append(("Provenance (from metadata)", man["provenance"]))
story.append(kv_table(glance))
story.append(Paragraph(
    "<i>Cohort/provenance fields are read directly from dataset metadata. Where metadata does not "
    "specify a clinical cohort, none is asserted.</i>", S["Cap"]))

# ---------------- 2 Methods ------------------------------------------------------------
story += [Paragraph("2. Methods", S["H1"])]
story.append(Paragraph(
    f"<b>Preprocessing.</b> Modality was detected as <b>{g('modality')}</b>. "
    f"Transformation: <b>{g('transform')}</b>. Compensation: <b>{g('compensation')}</b>. "
    f"Quality control: {g('qc_detail', g('qc_summary'))}. "
    f"The complete, per-step preprocessing log is reproduced in qc_transform_log.txt.", S["Body2"]))
# ---- manual-gating validation verdict (step 4b) -- surface prominently; never bury a REVIEW ----
# 08_validate_vs_manual.R writes validation_vs_manual.csv, validation_verdict.txt,
# manual_reconciliation_state.csv (and optionally RECONCILIATION_SUMMARY.md) either into the run
# directory or into its manual_reconciliation/ folder when run as a separate artifact.
def _find_reconciliation_dir():
    for candidate in (os.path.join(A.outdir, "manual_reconciliation"), A.outdir):
        if os.path.isfile(os.path.join(candidate, "validation_verdict.txt")):
            return candidate
    return None
_rec_dir = _find_reconciliation_dir()
_verdict = ""
if _rec_dir:
    try: _verdict = open(os.path.join(_rec_dir, "validation_verdict.txt")).read().strip().upper()
    except Exception: _verdict = ""
if _verdict == "PASS":
    story.append(Paragraph(
        "<b>Validation vs manual gating.</b> Automated gates were reconciled against the supplied "
        "manual-gating export and <b>PASSED</b> within tolerance (per-sample deltas in "
        "validation_vs_manual.csv).", S["Body2"]))
elif _verdict == "REVIEW":
    story.append(callout(
        "VALIDATION VERDICT: REVIEW &mdash; automated gating DISAGREES with the manual-gating export "
        "beyond tolerance. Downstream results below are PROVISIONAL. The direction and likely source of "
        "each discrepancy must be established from validation_vs_manual.csv and the matching gate figures; "
        "the report does not assume that an automated gate is too tight or too loose."))
elif _verdict:
    story.append(Paragraph(
        "<b>Validation vs manual gating.</b> Reconciliation is <b>INCOMPLETE</b>; unmatched or ambiguous "
        "samples/populations prevent a pass. Section 2.6 lists the matched comparisons and the coverage "
        "gaps (validation_vs_manual.csv).", S["Body2"]))
story.append(Paragraph(
    f"<b>Clustering.</b> FlowSOM was trained on the SOM grid ({g('som_grid','10x10')} = "
    f"{g('n_som_nodes','100')} nodes) over type/lineage markers, followed by ConsensusClusterPlus "
    f"meta-clustering (maxK={g('maxK','20')}). The meta-clustering resolution was selected by "
    f"{g('resolution_method','multi-metric SOM-code assessment')} (candidate: <b>{chosen}</b>), yielding {n_pops} "
    f"cluster groups. Resolution state: <b>{_safe_str(_states.get('resolution'))}</b>. Seeds were fixed for reproducibility.", S["Body2"]))
# Annotation claim is conditional on whether a marker database was actually supplied to 03
# (recorded in the manifest as cellmarker_used) -- never claim CellMarker2 if it was not used.
if man.get("cellmarker_used"):
    _annot_txt = ("<b>Annotation.</b> Per-cluster median marker expression (z-scored across clusters) "
                  "was evaluated against conservative measured-marker signatures. CellMarker2 supplied "
                  "only supplemental likely alternatives when at least two reference markers were measured; "
                  "its positive-only scores never confirmed or independently supported a label.")
else:
    _annot_txt = ("<b>Annotation.</b> Per-cluster median marker expression (z-scored across clusters) "
                  "was summarized into an editable annotation template; lineage labels are proposed for "
                  "expert review before finalization. No external marker database was supplied for this "
                  "run, so labels were not auto-scaffolded from CellMarker2.")
_annot_txt += f" Annotation state: <b>{_safe_str(_states.get('annotation'))}</b>."
story.append(Paragraph(_annot_txt, S["Body2"]))
# ---- Manifest-derived QC / gating feature detection -----------------------------------
_gate_engine = str(man.get("gate_engine", "") or "").lower()
_tqc   = man.get("time_qc")           if isinstance(man.get("time_qc"), dict) else None
_cdiag = man.get("compensation_diag") if isinstance(man.get("compensation_diag"), dict) else None
_harm  = man.get("harmonization")     if isinstance(man.get("harmonization"), dict) else None
_cnorm = man.get("cytof_norm")        if isinstance(man.get("cytof_norm"), dict) else None
_gh    = man.get("gate_hierarchy")    if isinstance(man.get("gate_hierarchy"), dict) else None
_tqc_backend = str((_tqc or {}).get("backend", "") or "").lower()
_harm_did = (((_harm or {}).get("n_batches") or 0) > 1) or (((_harm or {}).get("n_groups_harmonized") or 0) > 0)
_cnorm_on = bool((_cnorm or {}).get("applied"))
def _isnum(v): return isinstance(v, (int, float)) and not isinstance(v, bool)
def _pct(v, nd=2): return (f"{v:.{nd}f}".rstrip("0").rstrip(".") + "%") if _isnum(v) else "n/a"
def _yn(v): return "yes" if bool(v) else "no"

# Software list mirrors only what actually ran (keeps claims aligned with References).
_sw = ["CATALYST", "FlowSOM", "ConsensusClusterPlus", "flowCore"]
if man.get("diff_abundance"): _sw.append("diffcyt")
if str(g("modality", "")).lower() == "flow": _sw.append("flowDensity/flowClust (data-driven gating)")
if _gate_engine == "opencyto": _sw.append("openCyto/flowWorkspace (hierarchical gating)")
if "peaco" in _tqc_backend: _sw.append("PeacoQC (time QC)")
if "flowai" in _tqc_backend: _sw.append("flowAI (time QC)")
if _harm_did: _sw.append("batch-aware cutoff harmonization")
if _cnorm_on: _sw.append("CATALYST normCytof (bead normalization)")
_sw.append("ggplot2 (figures)")
story.append(Paragraph(
    "<b>Software.</b> R/Bioconductor: " + ", ".join(_sw) + ". See References.", S["Body2"]))

# ---- 2.1 Quality-control gating (data-driven, reviewable, multivariate) ----------------
# Emitted only when the QC step produced per-gate diagnostics (gate_<sample>_<gate>.png).
# All statements below are generic method description; specific cutoffs come from the
# editable template written by 01_load_and_qc.R (never fabricated here).
gate_figs = sorted(glob.glob(os.path.join(FIG, "gate_*.png")))
if gate_figs:
    story.append(Paragraph("2.1 Quality-control gating (data-driven, reviewable)", S["H2"]))
    story.append(Paragraph(
        "QC cutoffs are proposed by the method recorded for each gate. Where a supported density valley "
        "exists it is used on the transformed channel; permissive FSC/SSC debris floors intentionally use "
        "lower percentiles, and supplied unstained/FMO controls can anchor marker boundaries. A unimodality "
        "guard (Hartigan dip test) and a "
        "minimum valley-depth check prevent inventing a cutoff where no real separation exists; in that "
        "case the pipeline falls back to a conservative percentile and flags the gate <b>for review</b> "
        "rather than asserting a spurious threshold. Where unstained/FMO controls are supplied, the "
        "positive/negative boundary is anchored to the control instead. Debris and live/dead steps also "
        "show diagnostic-only 2D density contours (FSC-A×SSC-A; viability×scatter, or Pt×DNA for mass cytometry). Scatter and "
        "singlet gates default to <b>permissive</b>, because over-tight gating silently removes real "
        "cells and biases every downstream population. Every proposed cutoff is written to an editable "
        "template (gating_thresholds_template.csv) alongside the diagnostic figures below, so a reviewer "
        "can confirm or override each threshold and re-run (two-pass: propose → edit → apply).", S["Body2"]))
    # ---- editable threshold table (source of truth = the template CSV for THIS run) ----
    tmpl_path = os.path.join(A.outdir, "gating_thresholds_applied.csv")
    if not os.path.exists(tmpl_path):
        tmpl_path = os.path.join(A.outdir, "gating_thresholds_template.csv")
    if os.path.exists(tmpl_path):
        import csv as _csv
        with open(tmpl_path) as fh:
            _tr = list(_csv.reader(fh))
        if _tr and len(_tr) > 1:
            th = _tr[0]; trows = _tr[1:]
            wmap = {"sample_id": 52, "gate": 86, "method": 54, "final_cutoff": 58,
                    "direction": 64, "status": 74, "apply": 30}
            present = [c for c in ["sample_id", "gate", "method", "final_cutoff",
                                   "direction", "status", "apply"] if c in th]
            idx = [th.index(c) for c in present]
            widths = [wmap[c] for c in present]
            th2 = present
            tr2 = [[(r[i] if i < len(r) else "") for i in idx] for r in trows[:24]]
            story.append(KeepTogether([data_table(th2, tr2, widths),
                Paragraph("Table. Proposed QC gating cutoffs for this run (editable). <i>status</i> flags "
                          "gates needing review (unimodal / shallow valley / too few events / control-"
                          "anchored); <i>sample_id=ALL</i> applies to every sample. Edit final_cutoff / "
                          "apply and re-run with --gate-review apply to override.", S["Cap"])]))
            if len(trows) > 24:
                story.append(Paragraph(f"<i>{len(trows) - 24} further rows in gating_thresholds_template.csv.</i>", S["Cap"]))
    # ---- curated diagnostic figures: highest-leverage gates first ----
    def _gate_priority(p):
        b = os.path.basename(p).lower()
        for i, key in enumerate(["live_dead", "debris_2d", "debris", "dna_intact",
                                 "singlet", "gaussian_doublet", "beads"]):
            if key in b:
                return i
        return 99
    show = sorted(gate_figs, key=lambda p: (_gate_priority(p), p))[:4]
    for p in show:
        nm = os.path.basename(p)[len("gate_"):-len(".png")]
        add_figure(story, p, f"Figure. QC gate diagnostic — {nm}: proposed cutoff and retained region "
                             "on the density (1D) or joint (2D) distribution.", w=300)
    if len(gate_figs) > len(show):
        story.append(Paragraph(
            f"<i>{len(gate_figs) - len(show)} additional per-sample gate diagnostics are in figures/ "
            "(gate_&lt;sample&gt;_&lt;gate&gt;.png).</i>", S["Cap"]))

# ---- 2.2 Advanced acquisition & signal QC diagnostics ---------------------------------
# These diagnostics report by default and remove events only when explicitly enabled.
if any(x is not None for x in (_tqc, _cdiag, _harm, _cnorm)):
    if gate_figs:
        story.append(PageBreak())
    story.append(Paragraph("2.2 Advanced acquisition &amp; signal QC", S["H2"]))
    story.append(Paragraph(
        "Beyond the gating above, the pipeline computes acquisition- and signal-level QC diagnostics on "
        "every run. By design these are <b>diagnostics-on / removal-opt-in</b>: each metric is measured "
        "and reported here, but events are removed only when the corresponding option is explicitly "
        "enabled &mdash; so these numbers never silently change population counts.", S["Body2"]))
    if _tqc is not None:
        _mode = str(_tqc.get("mode", "n/a")).lower()
        if _mode in ("off", "", "n/a"):
            _t = "<b>Time / acquisition QC.</b> Not computed for this run (time-channel QC was off)."
        else:
            _t = (f"<b>Time / acquisition QC</b> (backend: {_safe_str(_tqc.get('backend'))}, mode: "
                  f"<b>{_mode}</b>). Flow-rate anomalies {_pct(_tqc.get('pct_rate'))}, signal-stability "
                  f"anomalies {_pct(_tqc.get('pct_signal'))}, and margin/boundary events "
                  f"{_pct(_tqc.get('pct_margin'))} of acquired events. ")
            _rm = _tqc.get("removed")
            if _mode == "report" or (_isnum(_rm) and not _rm):
                _t += ("Reported as diagnostics only &mdash; <b>no events were removed</b> (enable "
                       "removal to act on them).")
            else:
                _t += f"<b>{_safe_str(_rm)}</b> flagged event(s) were removed."
        story.append(Paragraph(_t, S["Body2"]))
    if _cdiag is not None:
        _src = str(_cdiag.get("source", "none")).lower()
        if _src in ("none", "", "n/a"):
            _c = ("<b>Compensation / spillover.</b> No spillover/compensation matrix was supplied or "
                  "embedded, so no fluorescence compensation was applied and no condition-number "
                  "diagnostic was computed. When a matrix is provided, its condition number (kappa) is "
                  "reported as an ill-conditioning check before compensation.")
        else:
            _c = (f"<b>Compensation / spillover</b> (source: {_safe_str(_cdiag.get('source'))}). Spillover "
                  f"matrix {_safe_str(_cdiag.get('dim'))}; condition number kappa "
                  f"<b>{_safe_str(_cdiag.get('kappa'))}</b> (reciprocal condition "
                  f"{_safe_str(_cdiag.get('rcond'))}) &mdash; verdict <b>{_safe_str(_cdiag.get('verdict'))}</b>; "
                  f"compensation applied: <b>{_yn(_cdiag.get('applied'))}</b>. A high condition number "
                  f"flags a near-singular spillover matrix whose inversion can amplify measurement noise.")
        story.append(Paragraph(_c, S["Body2"]))
    if _harm is not None:
        if _harm_did:
            _h = (f"<b>Batch-aware cutoff harmonization.</b> Scope {_safe_str(_harm.get('scope'))}; "
                  f"{_safe_str(_harm.get('n_batches'))} batch(es) and "
                  f"{_safe_str(_harm.get('n_groups_harmonized'))} gating group(s) harmonized with "
                  f"shrinkage factor {_safe_str(_harm.get('shrink'))} toward a batch-consensus cutoff, "
                  f"reducing batch-driven gate-placement variance.")
        else:
            _h = ("<b>Batch-aware cutoff harmonization.</b> Per-sample cutoffs were used; no cross-batch "
                  "harmonization was applied (single batch, or harmonization not enabled). With multiple "
                  "batches enabled, per-batch cutoffs are shrunk toward a batch-consensus to reduce "
                  "batch-driven gate variance.")
        story.append(Paragraph(_h, S["Body2"]))
    if _cnorm is not None and (_cnorm_on or str(g("modality", "")).lower() in ("cytof", "mass")):
        if _cnorm_on:
            _n = (f"<b>CyTOF bead normalization.</b> Applied using {_safe_str(_cnorm.get('beads'))} beads "
                  f"(smoothing k={_safe_str(_cnorm.get('k'))}); {_safe_str(_cnorm.get('n_beads'))} bead "
                  f"event(s) detected and {_safe_str(_cnorm.get('n_removed'))} removed after signal "
                  f"normalization.")
        else:
            _n = ("<b>CyTOF bead normalization.</b> Not applied (opt-in). When enabled, EQ bead channels "
                  "correct within- and between-sample signal drift before clustering.")
        story.append(Paragraph(_n, S["Body2"]))

# ---- 2.3 Hierarchical gating (openCyto) -- emitted only when that backend was used ----
if _gh is not None:
    story.append(Paragraph("2.3 Hierarchical gating (openCyto)", S["H2"]))
    story.append(Paragraph(
        f"This run used the optional <b>openCyto</b> hierarchical gating backend (template: "
        f"<b>{_safe_str(_gh.get('template'))}</b>) in place of the default data-driven gating. A "
        f"flowWorkspace GatingSet was built and gated through the population hierarchy "
        f"<b>{_safe_str(_gh.get('populations'))}</b>; cells at the terminal population "
        f"<b>{_safe_str(_gh.get('terminal'))}</b> were carried forward to clustering. Across "
        f"{_safe_str(_gh.get('n_samples'))} sample(s), <b>{_safe_str(_gh.get('total_retained'))}</b> of "
        f"<b>{_safe_str(_gh.get('total_input'))}</b> events were retained "
        f"({_safe_str(_gh.get('pct_removed'))}% removed). The full per-sample tree is written to "
        f"{_safe_str(_gh.get('hierarchy_file'))}.", S["Body2"]))
    _pp = _gh.get("per_population")
    if isinstance(_pp, list) and _pp:
        _pph = ["Population", "Mean count", "% of parent"]
        _ppr = [[_safe_str(e.get("population")), _safe_str(e.get("mean_count")),
                 (_pct(e.get("mean_pct_parent")) if _isnum(e.get("mean_pct_parent")) else "n/a")]
                for e in _pp]
        story.append(KeepTogether([data_table(_pph, _ppr, [250, 100, 100]),
            Paragraph("Table. openCyto gating hierarchy &mdash; mean event count and percentage of the "
                      "parent population at each node (averaged across samples).", S["Cap"])]))

# ---- 2.4 Resolution evidence ---------------------------------------------------------
_rh, _rr = read_csv_rows("resolution_shortlist.csv", limit=4)
_resolution_table_source = "shortlist"
if not _rh:
    _rh, _rr = read_csv_rows("resolution_candidates.csv", limit=4)
    _resolution_table_source = "leading candidates"
if _rh:
    story.append(Paragraph("2.4 Clustering-resolution evidence", S["H2"]))
    story.append(Paragraph(
        "FlowSOM was trained once. Consensus diagnostics were reconstructed from its stored SOM codes "
        "with the same seed and parameters, then verified against CATALYST's stored partitions. The "
        "data-adaptive search ceiling reflects the occupied SOM nodes and consensus-resampling limit. "
        "Selection compares PAC (lower is better), cluster consensus, event-weighted and cluster-balanced "
        "silhouette (higher), and Davies–Bouldin index (lower). Separate broad, balanced, fine, and "
        "rare-preserving candidates are reported because no single k represents every biological objective. "
        "Delta area is retained as provenance but is not differenced or used alone. UMAP is visual review "
        "evidence only.", S["Body2"]))
    def _resolution_cell(row, name, digits=3):
        if name not in _rh:
            return "n/a"
        i = _rh.index(name)
        value = row[i] if i < len(row) else ""
        try:
            return f"{float(value):.{digits}f}"
        except (TypeError, ValueError):
            return _safe_str(value) or "n/a"

    _head = ["k", "Role", "PAC", "Median/min consensus", "Event/macro silhouette", "DBI", "Min cluster %"]
    _rows = []
    for row in _rr:
        _rows.append([
            str(int(float(_resolution_cell(row, "k", 0)))),
            _resolution_cell(row, "candidate_roles", 0),
            _resolution_cell(row, "pac"),
            f"{_resolution_cell(row, 'median_cluster_consensus')}/{_resolution_cell(row, 'minimum_cluster_consensus')}",
            f"{_resolution_cell(row, 'weighted_silhouette')}/{_resolution_cell(row, 'macro_silhouette')}",
            _resolution_cell(row, "weighted_dbi"),
            _resolution_cell(row, "minimum_cluster_pct", 2),
        ])
    story.append(KeepTogether([data_table(_head, _rows, [24, 64, 34, 78, 82, 34, 52]),
        Paragraph(f"Table. Resolution {_resolution_table_source}. The complete candidate table retains "
                  "delta-area, sample-coverage, event-balanced stability, and rank fields as a machine-readable artifact.",
                  S["Cap"])]))
    add_figure(story, fig("fig_resolution_diagnostics.png"),
               "Figure. Delta area, PAC, consensus, event-weighted and cluster-balanced silhouette, and Davies–Bouldin evidence. The labelled red line marks the selected or proposed k.", w=440)

_rare = man.get("rare_population")
if isinstance(_rare, dict):
    story.append(Paragraph("2.5 Rare-population refinement", S["H2"]))
    if _rare.get("applied"):
        _rare_summary = (
            f"A separate marker-aware search for <b>{_safe_str(_rare.get('population'))}</b> applied "
            f"candidate <b>{_safe_str(_rare.get('candidate_id'))}</b>, containing "
            f"<b>{_safe_str(_rare.get('event_count'))}</b> events "
            f"({_safe_str(_rare.get('event_pct'))}%)."
        )
    else:
        _rare_summary = (
            f"The marker-aware search for <b>{_safe_str(_rare.get('population'))}</b> remained "
            "unresolved; no rare-population branch was applied."
        )
    story.append(Paragraph(
        f"The primary global clustering was preserved. {_rare_summary} Its state is "
        f"<b>{_safe_str(_rare.get('workflow_state'))}</b>; selection state "
        f"<b>{_safe_str(_rare.get('selection_state'))}</b>; biological identity-validation state "
        f"<b>{_safe_str(_rare.get('identity_validation_state'))}</b>. Candidate ranking used marker "
        "direction, local separation, sample recurrence, and event-set stability across three FlowSOM "
        "seeds; reference labels were not used. Local contrasts compare nearby SOM nodes and do not "
        "define biological marker positivity.", S["Body2"]))
    add_figure(story, fig("fig_rare_population_candidates.png"),
               "Figure. Marker evidence, frequency, and cross-seed stability for rare-population candidates.", w=440)

# ---- 2.6 Manual gating reconciliation (only when 08_validate_vs_manual.R artifacts exist) ---------
# External validation evidence, reported in full. It does not alter overall_state, which reflects
# deliverable completeness and reviewed checkpoints (run_manifest.json).
def _num(v, nd=4):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(f):
        return "n/a"
    return f"{f:.{nd}f}".rstrip("0").rstrip(".") if abs(f - round(f)) > 1e-9 else f"{int(round(f))}"
if _rec_dir:
    _rec_rows = []
    try:
        with open(os.path.join(_rec_dir, "validation_vs_manual.csv"), newline="") as _fh:
            _rec_rows = list(csv.DictReader(_fh))
    except Exception:
        _rec_rows = []
    _rec_state = {}
    try:
        with open(os.path.join(_rec_dir, "manual_reconciliation_state.csv"), newline="") as _fh:
            _rec_state = next(csv.DictReader(_fh), {}) or {}
    except Exception:
        _rec_state = {}
    _matched = [r for r in _rec_rows if str(r.get("coverage", "")).upper() == "MATCHED"]
    _gaps = [r for r in _rec_rows if str(r.get("coverage", "")).upper() != "MATCHED"]
    _flagged = [r for r in _matched if str(r.get("flagged", "")).strip().lower() in ("true", "1", "yes")]
    _pipeline_only = [r for r in _gaps if str(r.get("coverage", "")).upper() in
                      ("MISSING_MANUAL_SAMPLE", "MISSING_MANUAL_POPULATION")]
    _manual_only = [r for r in _gaps if str(r.get("coverage", "")).upper() in
                    ("MISSING_PIPELINE_SAMPLE", "MISSING_PIPELINE_POPULATION")]
    _not_computable = [r for r in _gaps if r.get("coverage") == "NOT_COMPUTABLE_DENOMINATOR"]
    story.append(Paragraph("2.6 Manual gating reconciliation", S["H2"]))
    story.append(Paragraph(
        f"The pipeline's aggregate per-sample statistics were reconciled against the supplied manual-gating "
        f"export with 08_validate_vs_manual.R. Verdict: <b>{_safe_str(_verdict or 'n/a')}</b>; "
        f"{_safe_str(_rec_state.get('comparisons', len(_rec_rows)))} comparison(s), "
        f"{_safe_str(_rec_state.get('matched', len(_matched)))} matched, "
        f"{_safe_str(_rec_state.get('flagged', len(_flagged)))} flagged beyond tolerance. This is external "
        f"validation evidence; it does not change the run's overall_state, which reflects deliverable "
        f"completeness and reviewed checkpoints.", S["Body2"]))
    if _verdict == "INCOMPLETE" and _gaps:
        _why = (f"<b>Why the verdict is INCOMPLETE by construction.</b> A PASS requires every pipeline "
                f"population and every manual population to have a comparable matched counterpart. Here "
                f"{len(_pipeline_only)} pipeline row(s) have no manual counterpart"
                f" and {len(_manual_only)} manual row(s) have no pipeline counterpart"
                ". These are coverage gaps between the two population hierarchies, not measured "
                "disagreements; only matched rows support measured comparisons. "
                f"{len(_not_computable)} percentage comparison(s) are not computable because the denominator "
                "is unknown or incompatible. Other incomplete rows and their reasons are listed below.")
        story.append(callout(_why))
    elif _verdict == "INCOMPLETE":
        story.append(callout("<b>Verdict INCOMPLETE.</b> The reconciliation could not be completed (see "
                             "validation_vs_manual.csv and the run log for the missing parent or sample)."))
    if _flagged:
        story.append(Paragraph(
            f"<b>{len(_flagged)}</b> matched comparison(s) exceed the tolerance; their direction and cause must be "
            "established from the gate figures before downstream results are trusted.", S["Body2"]))
    if _matched:
        _ah = ["Sample", "Population", "Stat", "Pipeline", "Manual", "Delta", "Rel. diff", "Flagged"]
        _ar = [[_safe_str(r.get("sample")), _safe_str(r.get("population")), _safe_str(r.get("stat")),
                _num(r.get("pipeline")), _num(r.get("manual")), _num(r.get("delta")), _num(r.get("rel_diff")),
                "yes" if r in _flagged else "no"] for r in _matched[:40]]
        story.append(data_table(_ah, _ar, [65, 91, 35, 56, 56, 53, 53, 43]))
        story.append(Paragraph("Table. Matched pipeline-vs-manual comparisons with per-population deltas "
                               "(delta = pipeline - manual; rel. diff for counts).", S["Cap"]))
        if len(_matched) > 40:
            story.append(Paragraph(f"<i>{len(_matched) - 40} further matched rows in validation_vs_manual.csv.</i>", S["Cap"]))
    if _gaps:
        _gh = ["Sample", "Population", "Stat", "Coverage", "Reason"]
        _gr = [[_safe_str(r.get("sample")), _safe_str(r.get("population")), _safe_str(r.get("stat")),
                _safe_str(r.get("coverage")), _safe_str(r.get("reason"))] for r in _gaps[:40]]
        story.append(data_table(_gh, _gr, [65, 91, 35, 132, 129]))
        story.append(Paragraph("Table. Missing counterparts or comparisons that are not computable; each row is a reason the "
                               "verdict cannot be PASS.", S["Cap"]))
    _summary_md = os.path.join(_rec_dir, "RECONCILIATION_SUMMARY.md")
    if os.path.isfile(_summary_md):
        story.append(Paragraph(
            f"A narrative reconciliation summary was recorded in "
            f"{_safe_str(os.path.relpath(_summary_md, A.outdir))}.", S["Cap"]))

story += [PageBreak(), Paragraph("3. Results", S["H1"])]
story.append(Paragraph("3.1 Cell populations and marker signatures", S["H2"]))
story.append(Paragraph(
    f"Clustering resolved <b>{n_pops}</b> {_population_word} at resolution {chosen}. The heatmap shows "
    "median expression of clustering markers per group (z-scored), the basis for annotation review.", S["Body2"]))
add_figure(story, fig("fig_marker_heatmap.png"), "Figure. Median marker expression per population (z-scored).", w=470)
_annotation_file = "annotation_applied.csv" if os.path.exists(os.path.join(A.outdir, "annotation_applied.csv")) else "annotation_template.csv"
hh, hr = read_csv_rows(_annotation_file, limit=30)
if hh:
    keep = [i for i, c in enumerate(hh) if c.lower() in
            ("cluster","proposed_population","proposal_status","population","applied_population","top_markers")]
    hh2 = [hh[i] for i in keep]; hr2 = [[r[i] if i < len(r) else "" for i in keep] for r in hr]
    _annotation_headers = {
        "cluster": "#",
        "proposed_population": "Proposal", "proposal_status": "Evidence",
        "population": "Final label", "applied_population": "Applied label",
        "top_markers": "Top markers"
    }
    hh2 = [_annotation_headers.get(value.lower(), value) for value in hh2]
    if "proposal_status" in [hh[i].lower() for i in keep]:
        status_column = [hh[i].lower() for i in keep].index("proposal_status")
        for row in hr2:
            if status_column < len(row): row[status_column] = row[status_column].replace("_", " ").title()
    _annotation_total = csv_data_row_count(_annotation_file)
    _annotation_visible = min(30, _annotation_total)
    story.append(KeepTogether([data_table(hh2, hr2, [28, 76, 66, 91, 75, 90][:len(hh2)]),
                               Paragraph(
                                   f"Table. Showing {_annotation_visible} of {_annotation_total} cluster annotations from "
                                   f"{_annotation_file}; the complete machine-readable table is supplied with the run. "
                                   "Unsupported proposals remain unresolved; applied labels are shown when present.",
                                   S["Cap"])]))

story.append(Paragraph("3.2 Dimensionality reduction", S["H2"]))
story.append(Paragraph(
    "UMAP and t-SNE embeddings use a balanced subsample per sample. When the partition contains more "
    "than 20 groups, the figures show the 20 most abundant groups plus other; the complete assignments "
    "remain in the machine-readable outputs. Embeddings are visualization only; all quantification uses "
    "the full data.", S["Body2"]))
add_figure(story, fig("fig_umap_clusters.png"), "Figure. Subsampled UMAP; large partitions use top 20 + other.", w=440)
add_figure(story, fig("fig_tsne_clusters.png"), "Figure. Subsampled t-SNE; large partitions use top 20 + other.", w=440)

story.append(Paragraph("3.3 Population abundances", S["H2"]))
story.append(Paragraph("Per-sample population frequencies (% of cells) summarize composition across samples.", S["Body2"]))
add_figure(story, fig("fig_abundance_barplot.png"), "Figure. Population abundance per sample.", w=460)
_frequency_state_h, _frequency_state_r = read_csv_rows("frequency_figure_state.csv", limit=1)
_frequency_mode = _frequency_state_r[0][_frequency_state_h.index("mode")] if _frequency_state_h and _frequency_state_r else "raw percentage"
_frequency_figure = "fig_frequency_ranked.png" if _frequency_mode.startswith("single_sample") else "fig_freq_heatmap.png"
add_figure(story, fig(_frequency_figure),
           f"Figure. Population frequencies in raw percent ({_frequency_mode}); large partitions use top 20 + other.", w=460)

# ---------------- 4 Benchmarking (conditional) -----------------------------------------

# ---------------- 5 Differential abundance (conditional) -------------------------------
if man.get("diff_abundance"):
    da = man["diff_abundance"]
    story += [PageBreak(), Paragraph("5. Differential abundance / state", S["H1"])]
    if da.get("mode") == "descriptive_only":
        story.append(callout(
            f"<b>Statistical testing withheld by design.</b> {da.get('reason','Group size below threshold')}. "
            "With fewer than the pre-registered minimum samples per group, variance cannot be reliably "
            "estimated and p-values would be misleading. Results below are <b>descriptive only</b> "
            "(per-group means/medians and fold-change); no significance is claimed."))
        add_figure(story, fig("fig_abundance_by_group.png"), "Figure. Abundance by group (per-sample points; descriptive).", w=460)
        hh, hr = read_csv_rows("abundance_fold_change_descriptive.csv", limit=20)
        if hh:
            story.append(KeepTogether([data_table(hh, hr, None),
                                       Paragraph("Table. Descriptive fold-change between groups (no test).", S["Cap"])]))
    else:
        story.append(Paragraph(
            f"Differential abundance was tested with diffcyt-DA-edgeR and differential state with "
            f"diffcyt-DS-limma using the design <b>~ {da.get('design','group')}</b> (batch and covariates "
            f"included). Significance threshold FDR &lt; {da.get('fdr','0.05')}.", S["Body2"]))
        add_figure(story, fig("fig_abundance_by_group.png"), "Figure. Abundance by group (per-sample points).", w=460)
        hh, hr = read_csv_rows("diff_abundance_edgeR.csv", limit=20)
        if hh:
            story.append(KeepTogether([data_table(hh, hr, None),
                                       Paragraph("Table. Differential abundance (diffcyt-DA-edgeR), top rows.", S["Cap"])]))

# ---------------- 6 Conclusions --------------------------------------------------------
story += [PageBreak(), Paragraph("6. Conclusions", S["H1"])]
concl = (f"The recorded analysis output contains <b>{n_pops}</b> {_population_word} from "
         f"{g('n_cells')} {g('modality')} cells across {g('n_samples')} sample(s). "
         f"Workflow state: {_workflow_state}; scientific state: {_scientific_state}. ")
if _supported_annotations or _unresolved_annotations:
    concl += (f"Coarse lineage labels were supported for {_supported_annotations} metacluster(s), while "
              f"{_unresolved_annotations} metacluster(s) remained unresolved rather than being assigned "
              "unsupported biological identities. ")
concl += "Outputs are provided as editable tables and vector/raster figures for downstream analysis and expert review."
story.append(Paragraph(concl, S["Body2"]))

# ---------------- 7 Limitations --------------------------------------------------------
story += [Paragraph("7. Limitations", S["H1"])]
lims = ["Aggregate manual-statistics reconciliation does not establish per-cell classification accuracy."]
if not _annotation_confirmed:
    lims.append("Cluster annotations are proposals only; CellMarker2 scores were not treated as confirmed biological labels.")
elif _unresolved_annotations:
    lims.append(f"{_unresolved_annotations} metaclusters remain biologically unresolved because the measured panel lacks defining lineage markers; review completion does not convert them into identified populations.")
if not _resolution_confirmed:
    lims.append("The clustering resolution is an unapproved proposal and may split or merge major lineages.")
if man.get("modality") == "spectral" or g("compensation").startswith("REFUSED"):
    lims.append("Raw spectral data require validated upstream unmixing; this pipeline does not guess an unmixing matrix. Already-unmixed spectral data is processed as fluorescence flow.")
if man.get("diff_abundance", {}).get("mode") == "descriptive_only":
    lims.append("Differential abundance is descriptive only due to insufficient replication; a larger cohort is needed for inference.")
if _verdict == "REVIEW":
    lims.append("Automated gating did not reconcile with the manual-gating export within tolerance (validation verdict: REVIEW); results are provisional pending gate-level review of the discrepancy direction and cause.")
elif _verdict == "INCOMPLETE":
    lims.append("The manual-gating reconciliation is INCOMPLETE: populations without a counterpart on one side prevent a PASS verdict (section 2.6); matched populations report their measured deltas but the comparison does not cover every population.")
elif not _verdict:
    lims.append("No manual-gating export was supplied, so automated gates could not be validated against an independent reference; internal diagnostics alone do not guarantee correct gating.")
if isinstance(_rare, dict) and str(_rare.get("identity_validation_state", "")).startswith("NOT_EVALUABLE"):
    lims.append("A persisted rare branch was selected reproducibly, but its biological identity was not independently validated. Local marker contrasts and a human receipt do not establish marker positivity or target identity.")
lims.append("Rare populations near the limit of detection may require control-derived thresholds, a manual event-level gate, targeted refinement, reference labels, or orthogonal validation.")
for L in lims:
    story.append(Paragraph(f"<bullet>&bull;</bullet>{L}", S["BulletBody"]))

# ---------------- 8 Next steps ---------------------------------------------------------
story += [Paragraph("8. Recommended next steps", S["H1"])]
nxt = []
if not _annotation_confirmed:
    nxt.append("Expert review of the editable annotation template; finalize population labels and re-run downstream steps.")
elif _unresolved_annotations:
    nxt.append("Retain unresolved clusters as unresolved until defining lineage markers, control-informed gates, or orthogonal evidence support a biological identity.")
nxt.append("If lineages of interest were flagged as merged, re-cluster those cells at higher resolution (two-tier annotation).")
if man.get("diff_abundance", {}).get("mode") == "descriptive_only":
    nxt.append("Expand the cohort to >=3 samples per group to enable valid differential-abundance testing (diffcyt).")
elif man.get("diff_abundance"):
    nxt.append("Validate differentially abundant populations in an independent cohort.")
nxt.append("Integrate with orthogonal data (e.g., scRNA-seq, clinical outcomes) to interpret population shifts.")
for i, N in enumerate(nxt, 1):
    story.append(Paragraph(f"<bullet>{i}.</bullet>{N}", S["BulletBody"]))

# ---------------- References -----------------------------------------------------------
story += [PageBreak(), Paragraph("References", S["H1"])]
# References are loaded VERBATIM from the verified citation store (assets/references_cytometry.json,
# every field CrossRef-sourced and audited by scripts/verify_citations.py) -- NEVER hardcoded from
# memory. Only methods/tools this run actually used are cited (honest, conditional).
_ref_json = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "references_cytometry.json")
_refs_db = {}
try:
    with open(_ref_json) as _fh:
        _refs_db = json.load(_fh).get("refs", {})
except Exception:
    _refs_db = {}
_modality = str(g("modality", "")).lower()
_prov = str(man.get("provenance", "") or "").lower()
_ref_plan = [
    ("nowicka",         True),                                      # CyTOF/flow workflow followed
    ("catalyst",        True),                                      # CATALYST preprocessing/clustering toolkit
    ("flowsom",         True),                                      # FlowSOM clustering
    ("weber_compare",   True),                                      # clustering-method basis
    ("cellmarker2",     bool(man.get("cellmarker_used"))),          # only if CellMarker2 was actually used
    ("levine",          ("levine" in _prov) or ("aml" in _prov)),  # only if the Levine AML dataset was analysed
    ("peacoqc",         "peaco" in _tqc_backend),                 # time-QC via PeacoQC (if used)
    ("flowai",          "flowai" in _tqc_backend),                # time-QC via flowAI (if used)
    ("cytonorm",        bool(_harm_did)),                         # batch-aware cutoff harmonization (if performed)
    ("finck_beadnorm",  bool(_cnorm_on)),                         # CyTOF bead normalization (if applied)
    ("diffcyt",         bool(man.get("diff_abundance"))),           # only if differential abundance/state ran
    ("maecker_trotter", _modality == "flow"),                      # positivity/threshold basis (fluorescence gating)
    ("finak_opencyto",  (_modality == "flow") or (_gate_engine == "opencyto")),                      # automated gating framework (fluorescence)
]
_ref_i = 0
for _key, _cond in _ref_plan:
    if not _cond:
        continue
    _entry = _refs_db.get(_key)
    if not _entry or not _entry.get("citation"):
        continue
    _ref_i += 1
    story.append(Paragraph(f"[{_ref_i}] {_entry['citation']}", S["Ref"]))
if _ref_i == 0:
    story.append(Paragraph("Reference metadata unavailable (assets/references_cytometry.json not found "
                           "or unreadable); see the skill's verified citation store.", S["Ref"]))

# ---------------- Data & Outputs -------------------------------------------------------
story += [Paragraph("Data &amp; Outputs", S["H1"])]
figs = sorted(os.path.basename(p) for p in glob.glob(os.path.join(FIG, "*.png")))
tabs = sorted(os.path.basename(p) for p in glob.glob(os.path.join(TAB, "*.csv")))
story.append(Paragraph("<b>Figures:</b> " + (", ".join(figs) if figs else "none"), S["Ref"]))
story.append(Paragraph("<b>Tables:</b> " + (", ".join(tabs) if tabs else "none"), S["Ref"]))
story.append(Paragraph("<b>Logs:</b> qc_transform_log.txt (full preprocessing/QC record).", S["Ref"]))
story.append(Paragraph("<b>Machine-readable evidence:</b> run_manifest.json, report_claims.csv, artifact_manifest.csv, stage state files, review proposal/receipt records, QC uncertainty table, and threshold/candidate tables.", S["Ref"]))

# ---- page chrome ----------------------------------------------------------------------
def chrome(canvas, d):
    canvas.saveState(); w, h = letter
    canvas.setFont("Helvetica", 9); canvas.setFillColor(MUTED)
    canvas.drawString(60, h - 40, TITLE[:70])
    canvas.setStrokeColor(PHYLO_GOLD); canvas.setLineWidth(1); canvas.line(60, h - 48, w - 60, h - 48)
    canvas.setStrokeColor(TBL_BORDER); canvas.setLineWidth(0.75); canvas.line(60, 40, w - 60, 40)
    canvas.setFont("Helvetica", 8); canvas.setFillColor(MUTED); canvas.drawCentredString(w/2, 26, f"Page {d.page}")
    canvas.restoreState()

doc.build(story, onFirstPage=chrome, onLaterPages=chrome)

# Refresh artifact provenance after the PDF is written. The manifest deliberately excludes itself
# so hashing is stable and directly auditable.
import csv as _artifact_csv, hashlib as _hashlib
_artifact_rows = []
for _root, _, _files in os.walk(A.outdir):
    for _name in _files:
        _path = os.path.join(_root, _name)
        if _name in {"artifact_manifest.csv", "completion_state.csv"}:
            continue
        _h = _hashlib.sha256()
        with open(_path, "rb") as _fh:
            for _chunk in iter(lambda: _fh.read(1024 * 1024), b""):
                _h.update(_chunk)
        _artifact_rows.append((os.path.relpath(_path, A.outdir), os.path.getsize(_path), _h.hexdigest()))
with open(os.path.join(A.outdir, "artifact_manifest.csv"), "w", newline="") as _fh:
    _writer = _artifact_csv.writer(_fh); _writer.writerow(["artifact", "bytes", "sha256"])
    _writer.writerows(sorted(_artifact_rows))

# ---- validate -------------------------------------------------------------------------
rdr = PdfReader(out_pdf); npages = len(rdr.pages); size = os.path.getsize(out_pdf)
assert npages >= 2, f"only {npages} page(s)"
assert size > 5000, f"only {size} bytes"
assert len(rdr.pages[0].extract_text().strip()) > 0, "no text on page 1"
print(f"PDF OK: {out_pdf} | {npages} pages | {size/1024:.1f} KB")
