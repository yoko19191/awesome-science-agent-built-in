#!/usr/bin/env python3
"""Import-safe provider-aware report phases for single-cell-multimodal-integration.

prepare freezes provider-neutral content and immutable provider evidence; all renders only the
contract-default style and emits page images for genuine inspection; finalize re-verifies every
content, figure, provider, and PDF hash before writing receipt-v3.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from collections.abc import Sequence  # noqa: TC003

SCRIPTS = pathlib.Path(__file__).resolve().parent
PACKAGE = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from report_content import (  # noqa: E402
    CONTENT_NAME,
    CONTENT_SCHEMA,
    DEFAULT_PROVIDER,
    PREPARATION_NAME,
    PREPARATION_SCHEMA,
    SELECTION_NAME,
    SELECTION_SCHEMA,
    ReportConfig,
    ReportError,
    _file_evidence,
    _load_json,
    _result,
    _sha256,
    build_report_content,
    load_config,
)

BUNDLED_FILE = "scripts/run_all.sh"


def _rq():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import report_qc
    return report_qc


def _rq_for(config: ReportConfig):
    """Bind the QC helper to the config's results root before any artifact resolution."""
    report_qc = _rq()
    report_qc.RESULTS = config.results
    return report_qc


def _write_new_json(path: pathlib.Path, value: dict) -> pathlib.Path:
    if path.exists() or path.is_symlink():
        raise ReportError(f"refusing to overwrite existing report-contract artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _write_or_verify_json(path: pathlib.Path, value: dict) -> pathlib.Path:
    """Allow a receipt retry only when its immutable selection record is byte-equivalent."""
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ReportError(f"cannot verify existing selection record: {path}") from exc
        if existing != value:
            raise ReportError(
                f"refusing to replace stale or conflicting selection record: {path}"
            )
        return path
    return _write_new_json(path, value)


def prepare(config: ReportConfig) -> dict:
    """Write immutable provider-neutral content and hash-bound selection evidence."""
    rq = _rq_for(config)
    config.results.mkdir(parents=True, exist_ok=True)
    canonical = _result(config, config.report_name)
    if canonical.exists() or canonical.is_symlink():
        raise ReportError(
            f"refusing to prepare over an existing provider-rendered PDF: {canonical}"
        )
    content_path = _result(config, CONTENT_NAME)
    preparation_path = _result(config, PREPARATION_NAME)
    content = build_report_content(config)
    _write_new_json(content_path, content)
    try:
        selection = rq.resolve_report_style_selection(str(PACKAGE / "skill_contract.json"))
        preparation = {
            "schema": PREPARATION_SCHEMA,
            "report_content": _file_evidence(config, CONTENT_NAME),
            "source_artifacts": content["source_artifacts"],
            "figure_artifacts": [
                item for item in content["source_artifacts"]
                if str(item["path"]).startswith("figures/")
                and item["path"] != "figures/manifest.json"
            ],
            "selection": selection,
        }
        _write_new_json(preparation_path, preparation)
    except Exception:
        if content_path.is_file():
            content_path.unlink()
        raise
    return preparation


def _load_preparation(config: ReportConfig) -> dict:
    preparation = _load_json(config, PREPARATION_NAME)
    if preparation.get("schema") != PREPARATION_SCHEMA:
        raise ReportError(f"{PREPARATION_NAME} has an unsupported schema")
    return preparation


def _stable_selection_identity(selection: dict) -> dict:
    """Ignore only append-only whole-trace identity; retain user-message/provider semantics."""
    stable = json.loads(json.dumps(selection))
    evidence = stable.get("selection_evidence")
    if isinstance(evidence, dict) and isinstance(evidence.get("trace_source"), dict):
        if evidence["trace_source"].get("status") == "origin_attested_read_only_platform_trace":
            evidence.pop("transcript_sha256", None)
            evidence.pop("trace_source", None)
    return stable


def verify_preparation(config: ReportConfig) -> dict:
    """Re-hash all scientific/presentation inputs and re-resolve immutable provider selection."""
    rq = _rq_for(config)
    preparation = _load_preparation(config)
    content = _load_json(config, CONTENT_NAME)
    if content.get("schema") != CONTENT_SCHEMA:
        raise ReportError(f"{CONTENT_NAME} has an unsupported schema")
    expected_content = preparation.get("report_content")
    current_content = _file_evidence(config, CONTENT_NAME)
    if expected_content != current_content:
        raise ReportError("report_content.json changed after preparation")
    if content.get("source_artifacts") != preparation.get("source_artifacts"):
        raise ReportError("preparation source inventory disagrees with report_content.json")
    for expected in preparation.get("source_artifacts") or []:
        if not isinstance(expected, dict) or not isinstance(expected.get("path"), str):
            raise ReportError("preparation contains an invalid source artifact record")
        if _file_evidence(config, expected["path"]) != expected:
            raise ReportError(
                f"report source changed after preparation: {expected.get('path')}"
            )
    selected = preparation.get("selection")
    if not isinstance(selected, dict) or not isinstance(selected.get("provider"), str):
        raise ReportError("preparation has no resolved provider selection")
    current_selection = rq.resolve_report_style_selection(
        str(PACKAGE / "skill_contract.json"),
        asserted_provider=selected["provider"],
    )
    if _stable_selection_identity(current_selection) != _stable_selection_identity(selected):
        raise ReportError(
            "report-style selection or installed provider source changed after preparation"
        )
    # Downstream receipt evidence binds the current phase's exact origin-attested source hash.
    current = dict(preparation)
    current["selection"] = current_selection
    return current


def assert_default_renderer_allowed(config: ReportConfig) -> dict:
    """Resolve before any write; the bundled renderer is never an enterprise-provider fallback."""
    preparation = verify_preparation(config)
    selection = preparation["selection"]
    if (
        selection.get("provider") != DEFAULT_PROVIDER
        or selection.get("activation") != "default"
        or selection.get("selection") != "contract_default"
    ):
        raise ReportError(
            "the bundled renderer is contract-default only; the explicit immutable provider must "
            "consume report_content.json and the exact figure files, then --phase finalize must "
            "verify its PDF"
        )
    return selection


def _load_content(config: ReportConfig) -> dict:
    content = _load_json(config, CONTENT_NAME)
    if content.get("schema") != CONTENT_SCHEMA:
        raise ReportError(f"{CONTENT_NAME} has an unsupported schema")
    return content


def _image_aspect(path: pathlib.Path) -> float:
    from PIL import Image as PILImage
    with PILImage.open(path) as image:
        return image.height / image.width


def _default_attribution(generated_on: str) -> str:
    """Render default-provider attribution without placing branding in neutral content."""
    try:
        generated = dt.date.fromisoformat(generated_on)
    except (TypeError, ValueError) as exc:
        raise ReportError("report content has an invalid generated_on date") from exc
    return f"Phylo Biomni · generated {generated.strftime('%B %d, %Y')}"


def build_default_pdf(config: ReportConfig) -> pathlib.Path:
    """Render the provider-neutral model with the bundled default style into a fresh workspace file."""
    assert_default_renderer_allowed(config)
    content = _load_content(config)
    rq = _rq_for(config)

    from pypdf import PdfReader
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import (
        HRFlowable,
        Image,
        KeepTogether,
        ListFlowable,
        ListItem,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Table,
        TableStyle,
    )

    gold = HexColor("#D4A04A")
    heading = HexColor("#111111")
    body = HexColor("#2C2A26")
    muted = HexColor("#8A8378")
    table_alt = HexColor("#F9F7F3")
    table_border = HexColor("#D5CFC5")
    callout_bg = HexColor("#FAF9F3")
    styles = getSampleStyleSheet()

    def add_style(name: str, **kwargs) -> None:
        if name in styles.byName:
            for key, value in kwargs.items():
                setattr(styles[name], key, value)
        else:
            styles.add(ParagraphStyle(name=name, **kwargs))

    add_style("ReportTitle", fontName="Helvetica-Bold", fontSize=22, textColor=heading,
              leading=27, spaceAfter=6)
    add_style("Subtitle", fontName="Helvetica", fontSize=11, textColor=gold, spaceAfter=4)
    add_style("Attribution", fontName="Helvetica-Oblique", fontSize=9.5, textColor=muted,
              spaceAfter=8)
    add_style("SectionHead", fontName="Helvetica-Bold", fontSize=15, textColor=heading,
              spaceBefore=18, spaceAfter=8)
    add_style("SubHead", fontName="Helvetica-Bold", fontSize=12, textColor=heading,
              spaceBefore=10, spaceAfter=4)
    add_style("Body", fontName="Helvetica", fontSize=10.5, textColor=body,
              alignment=TA_JUSTIFY, spaceAfter=8, leading=15)
    add_style("Caption", fontName="Helvetica-Oblique", fontSize=9, textColor=muted,
              alignment=TA_CENTER, spaceAfter=5)
    add_style("FigureLocal", fontName="Helvetica", fontSize=9, textColor=body,
              alignment=TA_JUSTIFY, leading=12, spaceAfter=3)
    add_style("CellL", fontName="Helvetica", fontSize=8.5, textColor=body, leading=11)
    add_style("CellHdr", fontName="Helvetica-Bold", fontSize=8.5,
              textColor=HexColor("#FFFFFF"), leading=11)
    add_style("RefItem", fontName="Helvetica", fontSize=9, textColor=body,
              leading=13, spaceAfter=5)

    def divider():
        return HRFlowable(width=480, thickness=1, color=gold, spaceAfter=10, spaceBefore=4)

    def page_chrome(canvas, document):
        canvas.saveState()
        width, height = letter
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(muted)
        canvas.drawString(60, height - 40, str(content["title"])[:72])
        canvas.setStrokeColor(gold)
        canvas.setLineWidth(1)
        canvas.line(60, height - 48, width - 60, height - 48)
        canvas.setStrokeColor(table_border)
        canvas.setLineWidth(0.75)
        canvas.line(60, 40, width - 60, 40)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(muted)
        canvas.drawCentredString(width / 2, 26, f"Page {document.page}")
        canvas.restoreState()

    def render_figure(block: dict):
        image_path = _result(config, block["path"])
        width = int(block.get("width", 440))
        image = Image(
            str(image_path),
            width=width,
            height=width * _image_aspect(image_path),
        )
        image.hAlign = "CENTER"
        narrative = block["local_narrative"]
        local_blocks = [
            Paragraph(f"<b>{field.title()}:</b> {narrative[field]}", styles["FigureLocal"])
            for field in ("claim", "evidence", "interpretation", "limitation")
        ]
        return KeepTogether([
            image,
            Paragraph(str(block.get("caption", "")), styles["Caption"]),
            *local_blocks,
        ])

    def render_table(block: dict):
        header = [Paragraph(str(value), styles["CellHdr"]) for value in block["columns"]]
        rows = [
            [Paragraph(str(value), styles["CellL"]) for value in row]
            for row in block["rows"]
        ]
        table = Table(
            [header] + rows,
            colWidths=block["column_widths"],
            repeatRows=1,
        )
        table.hAlign = "CENTER"
        commands = [
            ("BACKGROUND", (0, 0), (-1, 0), gold),
            ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#FFFFFF")),
            ("GRID", (0, 0), (-1, -1), 0.5, table_border),
            ("BOX", (0, 0), (-1, -1), 0.75, table_border),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]
        for index in range(2, len(rows) + 1, 2):
            commands.append(("BACKGROUND", (0, index), (-1, index), table_alt))
        table.setStyle(TableStyle(commands))
        return table

    def render_callout(text: str):
        table = Table([[Paragraph(text, styles["Body"])]], colWidths=[460])
        table.hAlign = "CENTER"
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), callout_bg),
            ("BOX", (0, 0), (-1, -1), 0.5, table_border),
            ("LINEBEFORE", (0, 0), (0, -1), 3, gold),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ]))
        return table

    story = [
        Paragraph(content["title"], styles["ReportTitle"]),
        Paragraph(content["subtitle"], styles["Subtitle"]),
        Paragraph(_default_attribution(content["generated_on"]), styles["Attribution"]),
        divider(),
    ]
    infographic = content["front_matter"]["infographic"]
    infographic_path = _result(config, infographic["path"])
    image = Image(
        str(infographic_path),
        width=infographic["width"],
        height=infographic["width"] * _image_aspect(infographic_path),
    )
    image.hAlign = "CENTER"
    story.append(KeepTogether([
        image,
        Paragraph(infographic["caption"], styles["Caption"]),
    ]))
    story.extend([render_callout(content["front_matter"]["callout"]), PageBreak()])

    for section in content["sections"]:
        if section.get("page_break_before"):
            story.append(PageBreak())
        story.append(Paragraph(section["heading"], styles["SectionHead"]))
        for block in section["blocks"]:
            kind = block["type"]
            if kind == "paragraph":
                story.append(Paragraph(block["text"], styles["Body"]))
            elif kind == "subheading":
                story.append(Paragraph(block["text"], styles["SubHead"]))
            elif kind == "figure":
                story.append(render_figure(block))
            elif kind == "table":
                story.append(render_table(block))
            elif kind == "bullet_list":
                story.append(ListFlowable(
                    [ListItem(Paragraph(item, styles["Body"])) for item in block["items"]],
                    bulletType="bullet",
                    start="•",
                ))
            elif kind == "numbered_list":
                story.append(ListFlowable(
                    [ListItem(Paragraph(item, styles["RefItem"])) for item in block["items"]],
                    bulletType="1",
                    leftIndent=16,
                ))
            else:
                raise ReportError(f"unsupported report content block type: {kind!r}")

    candidate_dir = config.workspace / "default-report-provider"
    candidate = candidate_dir / config.report_name
    if candidate.exists() or candidate.is_symlink():
        raise ReportError(f"refusing to overwrite existing workspace PDF candidate: {candidate}")
    candidate_dir.mkdir(parents=True, exist_ok=True)
    canonical = _result(config, config.report_name)
    if canonical.exists() or canonical.is_symlink():
        raise ReportError(f"refusing to overwrite existing provider-rendered PDF: {canonical}")

    document = SimpleDocTemplate(
        str(candidate),
        pagesize=letter,
        topMargin=56,
        bottomMargin=52,
        leftMargin=60,
        rightMargin=60,
        title=content["title"],
    )
    document.build(story, onFirstPage=page_chrome, onLaterPages=page_chrome)
    reader = PdfReader(str(candidate))
    if len(reader.pages) < 4 or candidate.stat().st_size <= 20000:
        raise ReportError("default-provider PDF looks incomplete")
    if not reader.pages[0].extract_text().strip():
        raise ReportError("default-provider PDF has no text on page 1")
    return rq.staged_copy(candidate, config.report_name)


def _publish_explicit_candidate(
    config: ReportConfig,
    candidate: pathlib.Path | None,
) -> pathlib.Path:
    canonical = _result(config, config.report_name)
    if candidate is None:
        if not canonical.is_file() or canonical.stat().st_size == 0:
            raise ReportError(
                "explicit provider selection requires --provider-rendered-report, unless the "
                "provider already published the canonical results-root PDF"
            )
        return canonical
    candidate = candidate.resolve(strict=True)
    if candidate == canonical.resolve(strict=False):
        if not canonical.is_file() or canonical.stat().st_size == 0:
            raise ReportError("explicit-provider canonical PDF is missing or empty")
        return canonical
    if canonical.exists() or canonical.is_symlink():
        raise ReportError(
            f"refusing to overwrite existing explicit-provider PDF at {canonical}"
        )
    return _rq_for(config).staged_copy(candidate, config.report_name)


def _page_count(path: pathlib.Path) -> int:
    from pypdf import PdfReader
    return len(PdfReader(str(path)).pages)


def record_default_render_for_inspection(config: ReportConfig) -> list[dict]:
    report = _result(config, config.report_name)
    count = _page_count(report)
    if count < 1:
        raise ReportError("rendered PDF has no pages")
    return _rq_for(config).record_pdf_render(
        config.report_name,
        text_artifact="report_text.txt",
        rendered_page_files=[
            f"report_page-{page}.png" for page in range(1, count + 1)
        ],
        log_path="qc_run_log.json",
    )


def _selection_record(config: ReportConfig, preparation: dict, report: pathlib.Path) -> dict:
    style_evidence = _rq_for(config).assert_report_styled(
        config.report_name,
        style_provider=preparation["selection"]["provider"],
        allow_personal_provider=(
            preparation["selection"].get("selection") == "explicit_override"
        ),
        expected_activation=preparation["selection"].get("activation"),
    )
    return {
        "schema": SELECTION_SCHEMA,
        "canonical_report": config.report_name,
        "canonical_report_sha256": _sha256(report),
        "canonical_report_bytes": report.stat().st_size,
        "report_content_sha256": preparation["report_content"]["sha256"],
        "figure_artifacts": preparation["figure_artifacts"],
        "provider": preparation["selection"]["provider"],
        "provider_selection": preparation["selection"],
        "provider_style_evidence": style_evidence,
        "preparation_sha256": _sha256(_result(config, PREPARATION_NAME)),
    }


def finalize(
    config: ReportConfig,
    *,
    provider_rendered_report: pathlib.Path | None = None,
    selected_branch_ids: Sequence[str] | None = None,
) -> dict:
    """Finalize only after immutable inputs and genuine post-render page inspections verify."""
    rq = _rq_for(config)
    preparation = verify_preparation(config)
    report = _publish_explicit_candidate(config, provider_rendered_report)
    record_path = _result(config, SELECTION_NAME)
    _write_or_verify_json(
        record_path,
        _selection_record(config, preparation, report),
    )

    figures = rq.assert_figures("figures/manifest.json")
    branches = tuple(selected_branch_ids or config.selected_branch_ids)
    selected_outputs = rq.outputs_for_selected_branches(
        list(branches),
        contract=str(PACKAGE / "skill_contract.json"),
    )
    outputs = list(dict.fromkeys(
        selected_outputs + [CONTENT_NAME, PREPARATION_NAME, SELECTION_NAME]
    ))
    delivery = rq.finalize_delivery(
        config.report_name,
        figures,
        figure_not_applicable_reason=None,
        bundled_files=[BUNDLED_FILE],
        outputs=outputs,
        infographics=[config.infographic_name],
        style_provider=preparation["selection"]["provider"],
        style_selection_record=SELECTION_NAME,
        qc_run_log="qc_run_log.json",
        package_snapshot="attached_package_snapshot.json",
        execution_seal="one_shot_execution.json",
        contract=str(PACKAGE / "skill_contract.json"),
        receipt_path="run_receipt.json",
        authorization_path="delivery_authorization.json",
        facts_path="report_facts.json",
        totalvi_status_path="totalvi_status.json",
        scorecard_path="joint_qc_scorecard.csv",
    )
    payload = json.loads(_result(config, "run_receipt.json").read_text(encoding="utf-8"))
    if payload.get("schema") != rq.RECEIPT_SCHEMA_V3:
        raise ReportError(
            f"provider-aware finalization produced {payload.get('schema')!r}, not receipt-v3"
        )
    return delivery


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--phase",
        choices=("prepare", "all", "finalize"),
        default="all",
    )
    parser.add_argument(
        "--provider-rendered-report",
        help="fresh workspace PDF from the explicitly selected provider; omit if already canonical",
    )
    parser.add_argument(
        "--selected-branch-id",
        action="append",
        default=[],
        help="repeat for each clarification branch; defaults to values in config",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config(args.config)
    if args.phase == "prepare":
        preparation = prepare(config)
        print(json.dumps({
            "status": "prepared",
            "content": str(_result(config, CONTENT_NAME)),
            "provider": preparation["selection"]["provider"],
            "selection": preparation["selection"]["selection"],
        }, sort_keys=True))
        return 0
    if args.phase == "all":
        preparation = prepare(config)
        if preparation["selection"].get("selection") != "contract_default":
            raise ReportError(
                "--phase all is default-provider only; use prepare, let the explicit provider "
                "render report_content.json, inspect its pages, then use finalize"
            )
        report = build_default_pdf(config)
        pages = record_default_render_for_inspection(config)
        print(json.dumps({
            "status": "awaiting_page_inspection",
            "report": str(report),
            "provider": preparation["selection"]["provider"],
            "pages": pages,
            "next": (
                "Open every current page image with the native Read media-output mode, then run "
                "--phase finalize. Optional record_pdf_page_inspection notes are advisory only."
            ),
        }, sort_keys=True))
        return 0

    candidate = (
        pathlib.Path(args.provider_rendered_report)
        if args.provider_rendered_report
        else None
    )
    delivery = finalize(
        config,
        provider_rendered_report=candidate,
        selected_branch_ids=args.selected_branch_id or None,
    )
    print(json.dumps({
        "status": "complete",
        "authorization": delivery["authorization_path"],
        "terminal_message": delivery["terminal_message"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
