#!/usr/bin/env python3
"""Render, review, and receipt an existing Open Targets scientific result set."""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys


SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from report_qc import (  # noqa: E402
    RESULTS,
    _pdf_page_count,
    finalize_report_artifacts,
    record_pdf_review,
    report_render_plan,
    run_bundled,
    staged_copy,
    write_receipt,
)


REPORT_NAME = "report_open-targets.pdf"
INFOGRAPHIC = "infographic_open_targets_workflow.png"
SCIENTIFIC_ARTIFACTS = [
    "target_ranking.csv",
    "evidence_rows.csv",
    "operation_ledger.json",
    "provenance.json",
    "report_facts.json",
    "figure_payloads.json",
    "figures/figure_1_target_ranking.png",
    "figures/figure_2_datatype_heatmap.png",
    INFOGRAPHIC,
]


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_text(report: pathlib.Path, destination: pathlib.Path) -> int:
    try:
        import pypdf

        reader = pypdf.PdfReader(str(report))
        text = [
            f"--- Page {index} ---\n{page.extract_text() or ''}"
            for index, page in enumerate(reader.pages, 1)
        ]
        destination.write_text("\n".join(text), encoding="utf-8")
        return len(reader.pages)
    except ImportError:
        executable = shutil.which("pdftotext")
        if executable is None:
            raise RuntimeError("neither pypdf nor pdftotext is available for PDF text extraction")
        completed = subprocess.run(
            [executable, str(report), str(destination)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0 or not destination.is_file():
            raise RuntimeError(f"pdftotext failed: {completed.stderr[-300:]}")
        return _pdf_page_count(report)


def _render_pages(report: pathlib.Path, pages_dir: pathlib.Path, page_count: int) -> list[str]:
    pages_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[str] = []
    try:
        import fitz

        document = fitz.open(str(report))
        for page_number in range(1, page_count + 1):
            destination = pages_dir / f"page_{page_number}.png"
            document[page_number - 1].get_pixmap(dpi=150).save(str(destination))
            rendered.append(str(destination.relative_to(RESULTS)))
        document.close()
        return rendered
    except ImportError:
        executable = shutil.which("pdftoppm")
        if executable is None:
            raise RuntimeError("neither PyMuPDF nor pdftoppm is available for PDF rendering")
        for page_number in range(1, page_count + 1):
            destination = pages_dir / f"page_{page_number}.png"
            completed = subprocess.run(
                [
                    executable,
                    "-f", str(page_number),
                    "-l", str(page_number),
                    "-singlefile",
                    "-png",
                    "-r", "150",
                    str(report),
                    str(destination.with_suffix("")),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if completed.returncode != 0 or not destination.is_file():
                raise RuntimeError(f"pdftoppm failed: {completed.stderr[-300:]}")
            rendered.append(str(destination.relative_to(RESULTS)))
        return rendered


def finalize(
    provider_rendered_report: pathlib.Path | None,
    workspace_dir: pathlib.Path,
    visual_review_state: str,
    visual_review_notes: str,
) -> pathlib.Path:
    content = RESULTS / "report_content.json"
    structure = RESULTS / "report_structure.json"
    if not content.is_file() or not structure.is_file():
        raise RuntimeError("run run_analysis.py --content-only before finalizing the report")

    render_plan = report_render_plan()
    bundled_files = ["scripts/visualize.py"]
    workspace_dir.mkdir(parents=True, exist_ok=True)
    staged_report = workspace_dir / REPORT_NAME
    if render_plan["render_with_bundled"]:
        staged_report.unlink(missing_ok=True)
        run_bundled(
            [sys.executable, "scripts/build_report.py", "--content", str(content),
             "--output", str(staged_report)],
            "scripts/build_report.py",
            [str(staged_report)],
        )
        bundled_files.append("scripts/build_report.py")
    else:
        if provider_rendered_report is None:
            raise RuntimeError(
                "the explicit report-style provider must render report_content.json before finalization"
            )
        staged_report = provider_rendered_report
        if not staged_report.is_file() or staged_report.stat().st_size == 0:
            raise RuntimeError(f"provider-rendered report is missing or empty: {staged_report}")

    staged_copy(staged_report, REPORT_NAME)
    finalize_report_artifacts(REPORT_NAME)
    report = RESULTS / REPORT_NAME
    review_dir = RESULTS / "report_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    text_path = review_dir / "report_text.txt"
    page_count = _extract_text(report, text_path)
    rendered = _render_pages(report, review_dir / "pages", page_count)
    artifact_hashes = {str(text_path.relative_to(RESULTS)): _sha256(text_path)}
    artifact_hashes.update({path: _sha256(RESULTS / path) for path in rendered})
    attestation = (
        f"Every rendered page was visually inspected. Visual review evidence: "
        f"{visual_review_notes}. The canonical report was rendered through "
        f"{render_plan['provider']} and provider-specific style is verified by the receipt gate."
        if visual_review_state == "pass"
        else f"Visual review state: {visual_review_state}. Evidence: {visual_review_notes}."
    )
    record_pdf_review(
        report_name=REPORT_NAME,
        text_artifact=str(text_path.relative_to(RESULTS)),
        rendered_page_files=rendered,
        reviewed_page_numbers=(list(range(1, page_count + 1)) if visual_review_state == "pass" else []),
        review_attestation=attestation,
        review_state=visual_review_state,
        pdf_sha256=_sha256(report),
        artifact_sha256s=artifact_hashes,
    )
    figures = [
        {
            "step": 1,
            "file": "figures/figure_1_target_ranking.png",
            "caption": "Targets ranked by overall association score; scores are prioritization metrics, not effect estimates.",
        },
        {
            "step": 2,
            "file": "figures/figure_2_datatype_heatmap.png",
            "caption": "Per-datatype contribution scores; values are prioritization metrics, not causal proof.",
        },
    ]
    root_artifacts = [
        *SCIENTIFIC_ARTIFACTS,
        "report_content.json",
        "report_structure.json",
        REPORT_NAME,
    ]
    write_receipt(
        report_name=REPORT_NAME,
        figures=figures,
        bundled_files=tuple(bundled_files),
        outputs=("figures/figure_1_target_ranking.png", "figures/figure_2_datatype_heatmap.png"),
        infographics=(INFOGRAPHIC,),
        infographic_prompt="assets/infographic_prompt.txt",
        qc_run_log="qc_run_log.json",
        contract="skill_contract.json",
        narrative_facts="report_facts.json",
        narrative_provenance="provenance.json",
        root_artifacts=root_artifacts,
        path="run_receipt.json",
        strict=True,
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-rendered-report")
    parser.add_argument(
        "--workspace-dir",
        default=os.environ.get("BIOMNI_WORKSPACE", "/workspace"),
    )
    parser.add_argument(
        "--visual-review-state",
        required=True,
        choices=["pass", "fail", "not_evaluable", "not_applicable"],
    )
    parser.add_argument("--visual-review-notes", required=True)
    args = parser.parse_args()
    if args.visual_review_state == "pass" and not args.visual_review_notes.strip():
        parser.error("a passing visual review requires non-empty inspection evidence")
    report = finalize(
        pathlib.Path(args.provider_rendered_report) if args.provider_rendered_report else None,
        pathlib.Path(args.workspace_dir),
        args.visual_review_state,
        args.visual_review_notes,
    )
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
