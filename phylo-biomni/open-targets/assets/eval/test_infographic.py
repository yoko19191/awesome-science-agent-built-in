"""Infographic honesty gate (mandatory generated-infographic addendum).

A schematic drawn with matplotlib is not an infographic. The receipt's
infographic lineage check (``assert_generated_by_tool``) must fail when:

  - the GenerateImage success line is absent from the transcript
  - the requested filename does not match any success line (wrong filename)
  - the infographic file is absent so the PDF embeds fewer images (unembedded)

and the package prompt guard must reject a data-bearing prompt (one that
contains run-specific numbers, gene names, variant IDs, or scores).

These are rejection tests: each must FAIL the gate before the fix and PASS
only when a real GenerateImage trace evidences the exact filename.
"""

from __future__ import annotations

import json
import pathlib
import re

import numpy as np
import pytest
from PIL import Image as PILImage


def _make_test_infographic(path: pathlib.Path, size=(800, 600)):
    """Create a non-blank, sufficiently large PNG for testing."""
    arr = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    # Gradient + pattern to ensure multiple pixel values and >5KB file size
    for y in range(size[1]):
        for x in range(size[0]):
            arr[y, x] = [(x * 255) // size[0], (y * 255) // size[1],
                         ((x + y) * 128) // (size[0] + size[1])]
    PILImage.fromarray(arr).save(str(path))


def _paired_trace(prompt: str, filename: str, *, call_id: str = "tool-image-1",
                  result_id: str | None = None) -> str:
    linked_id = result_id if result_id is not None else call_id
    records = [
        {"content": [{"type": "tool_use", "id": call_id, "name": "GenerateImage",
                      "input": {"prompt": prompt, "file_name": filename}}]},
        {"content": [{"type": "tool_result", "tool_use_id": linked_id,
                      "content": f"Image generated successfully and saved to /mnt/results/{filename}"}]},
    ]
    return "\n".join(json.dumps(record) for record in records) + "\n"


# ---------------------------------------------------------------------------
# assert_generated_by_tool — the receipt's infographic lineage gate
# ---------------------------------------------------------------------------

def test_missing_transcript_raises(tmp_path):
    """assert_generated_by_tool raises when the transcript file is absent."""
    from report_qc import assert_generated_by_tool, GateFailure

    with pytest.raises(GateFailure, match="missing"):
        assert_generated_by_tool(
            "infographic_open_targets_workflow.png",
            transcript=tmp_path / "nonexistent.jsonl",
        )


def test_missing_success_line_raises(tmp_path):
    """A transcript with no GenerateImage success line fails the gate."""
    from report_qc import assert_generated_by_tool, GateFailure

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"role": "user", "content": "please generate an infographic"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(GateFailure, match="same-id"):
        assert_generated_by_tool(
            "infographic_open_targets_workflow.png",
            transcript=transcript,
            prompt_file=_PROMPT_PATH,
        )


def test_wrong_filename_raises(tmp_path):
    """A success line for a *different* filename does not evidence the requested one.

    This is the substring-matching guard: ``surreal.png`` must not vouch for
    ``real.png``, and ``wrong_name.png`` must not vouch for
    ``infographic_open_targets_workflow.png``.
    """
    from report_qc import assert_generated_by_tool, GateFailure

    transcript = tmp_path / "transcript.jsonl"
    prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    transcript.write_text(_paired_trace(prompt, "wrong_name.png"), encoding="utf-8")
    with pytest.raises(GateFailure, match="same-id"):
        assert_generated_by_tool(
            "infographic_open_targets_workflow.png",
            transcript=transcript,
            prompt_file=_PROMPT_PATH,
        )


def test_correct_success_line_passes(tmp_path):
    """A success line naming the exact basename passes the gate without raising."""
    from report_qc import assert_generated_by_tool

    transcript = tmp_path / "transcript.jsonl"
    prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    transcript.write_text(
        _paired_trace(prompt, "infographic_open_targets_workflow.png"), encoding="utf-8"
    )
    # Must not raise
    assert_generated_by_tool(
        "infographic_open_targets_workflow.png",
        transcript=transcript,
        prompt_file=_PROMPT_PATH,
    )


def test_success_in_a_different_record_does_not_leak(tmp_path):
    """A success line in one record must not evidence a filename named only in a
    *different* record — the gate matches within a single record, not across the file."""
    from report_qc import assert_generated_by_tool, GateFailure

    transcript = tmp_path / "transcript.jsonl"
    prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    transcript.write_text(
        _paired_trace(prompt, "other.png")
        + json.dumps({"content": "requesting infographic_open_targets_workflow.png"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(GateFailure, match="same-id"):
        assert_generated_by_tool(
            "infographic_open_targets_workflow.png",
            transcript=transcript,
            prompt_file=_PROMPT_PATH,
        )


def test_mismatched_result_id_is_rejected(tmp_path):
    """A success result belonging to another invocation cannot complete the lineage."""
    from report_qc import assert_generated_by_tool, GateFailure

    prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        _paired_trace(prompt, "infographic_open_targets_workflow.png",
                      call_id="tool-image-1", result_id="tool-image-2"),
        encoding="utf-8",
    )
    with pytest.raises(GateFailure, match="same-id"):
        assert_generated_by_tool(
            "infographic_open_targets_workflow.png", transcript=transcript,
            prompt_file=_PROMPT_PATH,
        )


def test_prompt_from_another_call_is_rejected(tmp_path):
    """The package prompt and a successful filename must belong to the same call."""
    from report_qc import assert_generated_by_tool, GateFailure

    prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        _paired_trace(prompt, "other.png", call_id="tool-image-1")
        + _paired_trace("different prompt", "infographic_open_targets_workflow.png",
                        call_id="tool-image-2"),
        encoding="utf-8",
    )
    with pytest.raises(GateFailure, match="same-id"):
        assert_generated_by_tool(
            "infographic_open_targets_workflow.png", transcript=transcript,
            prompt_file=_PROMPT_PATH,
        )


# ---------------------------------------------------------------------------
# Package prompt guard — the prompt must be qualitative, non-data-bearing
# ---------------------------------------------------------------------------

_PROMPT_PATH = pathlib.Path(__file__).resolve().parent.parent / "infographic_prompt.txt"

# Tokens that would make the prompt data-bearing rather than qualitative.
_FORBIDDEN_PROMPT_PATTERNS = [
    r"\bAPP\b", r"\bAPOE\b", r"\bPSEN1\b", r"\bPSEN2\b",   # gene names
    r"0\.\d{2,}",                                          # scores like 0.872
    r"\bGCST\d+\b",                                        # GWAS Catalog study IDs
    r"\bENSG\d+\b",                                        # Ensembl gene IDs
    r"\bMONDO_\d+\b",                                      # disease IDs
    r"\b\d{4,}\b",                                         # 4+ digit numbers (sample sizes, counts)
    r"\bp\s*[=<]\s*10",                                    # p-value notation
]


def test_prompt_is_qualitative():
    """The fixed infographic prompt must not contain run-specific data tokens."""
    prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    for pattern in _FORBIDDEN_PROMPT_PATTERNS:
        assert not re.search(pattern, prompt), (
            f"infographic prompt contains a data-bearing token matching {pattern!r}; "
            f"the prompt must be purely qualitative with no run-specific numbers, "
            f"gene names, variant IDs, or scores"
        )


def test_prompt_declares_non_data_bearing():
    """The prompt must explicitly state it is qualitative / non-data-bearing."""
    prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    lowered = prompt.lower()
    assert ("qualitative" in lowered or "no numbers" in lowered or "no data" in lowered), (
        "infographic prompt must declare itself qualitative / non-data-bearing"
    )


def test_prompt_filename_is_pinned():
    """run_analysis.py and build_report.py must agree on the infographic filename."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    run_analysis = (pkg_root / "scripts" / "run_analysis.py").read_text(encoding="utf-8")
    build_report = (pkg_root / "scripts" / "build_report.py").read_text(encoding="utf-8")
    fname = "infographic_open_targets_workflow.png"
    assert fname in run_analysis, "run_analysis.py must reference the pinned infographic filename"
    assert fname in build_report, "build_report.py must reference the pinned infographic filename"


# ---------------------------------------------------------------------------
# Unembedded-image rejection — the PDF embeds the infographic only when present
# ---------------------------------------------------------------------------

def _minimal_facts():
    return {
        "disease_name": "Test Disease",
        "disease_id": "MONDO_0000001",
        "release_label": "26.06",
        "api_version": "26.6.3",
        "access_date": "2026-08-16",
        "total_associated_targets": 100,
        "top_targets": [
            {"rank": 1, "target_id": "ENSG00000142192", "approved_symbol": "T1",
             "approved_name": "test target 1", "biotype": "protein_coding",
             "overall_association_score": 0.9,
             "datatype_scores": [{"datatype_id": "genetic_association", "score": 0.9}]},
        ],
        "evidence_count": 5,
        "evidence_rows": [
            {"datasource_id": "genetic_literature", "datatype_id": "genetic_association",
             "score": 0.8, "publication_first_author": "Author", "publication_year": 2024},
        ],
        "studies": [],
        "credible_sets": [],
        "completeness": "bounded_sample",
        "max_pages": 10,
        "max_rows": 500,
        "operation_ledger_summary": {"total_attempts": 5, "successes": 5, "failures": 0, "by_state": {}},
        "validation_errors": [],
        "caveats": ["Test caveat."],
        "next_steps": ["Test next step."],
    }


def test_unembedded_infographic_detected(tmp_path, monkeypatch):
    """A PDF built without the infographic must ABORT, not silently skip (OT-R2-03).

    build_report.py raises InfographicError when the infographic is missing,
    blank, or corrupt. A report that silently skips it and still returns a PDF
    violates the pinned contract.
    """
    monkeypatch.setenv("BIOMNI_RESULTS", str(tmp_path))
    import importlib
    import build_report
    importlib.reload(build_report)

    figs_dir = tmp_path / "figures"
    figs_dir.mkdir(parents=True, exist_ok=True)
    for name in ("figure_1_target_ranking.png", "figure_2_datatype_heatmap.png"):
        PILImage.new("RGB", (100, 100), "#D4A04A").save(str(figs_dir / name))

    facts = _minimal_facts()
    facts_path = tmp_path / "report_facts.json"
    facts_path.write_text(json.dumps(facts), encoding="utf-8")

    # Build WITHOUT the infographic present — must raise InfographicError
    with pytest.raises(build_report.InfographicError, match="mandatory infographic"):
        build_report.build_pdf(facts_path, tmp_path / "report_no_infographic.pdf")

    # Create the infographic and rebuild — must succeed
    _make_test_infographic(tmp_path / "infographic_open_targets_workflow.png")
    pdf_yes = build_report.build_pdf(facts_path, tmp_path / "report_with_infographic.pdf")
    assert pdf_yes.exists(), "PDF must be built when infographic is present"


def test_blank_infographic_rejected(tmp_path, monkeypatch):
    """A blank (single-pixel-value) infographic must be rejected (OT-R2-03)."""
    monkeypatch.setenv("BIOMNI_RESULTS", str(tmp_path))
    import importlib
    import build_report
    importlib.reload(build_report)

    figs_dir = tmp_path / "figures"
    figs_dir.mkdir(parents=True, exist_ok=True)
    for name in ("figure_1_target_ranking.png", "figure_2_datatype_heatmap.png"):
        PILImage.new("RGB", (100, 100), "#D4A04A").save(str(figs_dir / name))

    facts = _minimal_facts()
    facts_path = tmp_path / "report_facts.json"
    facts_path.write_text(json.dumps(facts), encoding="utf-8")

    # Create a blank infographic (single pixel value, but large enough to pass size check)
    arr = np.full((600, 800, 3), 255, dtype=np.uint8)
    PILImage.fromarray(arr).save(str(tmp_path / "infographic_open_targets_workflow.png"))

    with pytest.raises(build_report.InfographicError, match="blank"):
        build_report.build_pdf(facts_path, tmp_path / "report_blank_infographic.pdf")


def test_corrupt_infographic_rejected(tmp_path, monkeypatch):
    """A corrupt (too-small) infographic must be rejected (OT-R2-03)."""
    monkeypatch.setenv("BIOMNI_RESULTS", str(tmp_path))
    import importlib
    import build_report
    importlib.reload(build_report)

    figs_dir = tmp_path / "figures"
    figs_dir.mkdir(parents=True, exist_ok=True)
    for name in ("figure_1_target_ranking.png", "figure_2_datatype_heatmap.png"):
        PILImage.new("RGB", (100, 100), "#D4A04A").save(str(figs_dir / name))

    facts = _minimal_facts()
    facts_path = tmp_path / "report_facts.json"
    facts_path.write_text(json.dumps(facts), encoding="utf-8")

    # Create a corrupt infographic (too small)
    (tmp_path / "infographic_open_targets_workflow.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    with pytest.raises(build_report.InfographicError, match="blank or corrupt"):
        build_report.build_pdf(facts_path, tmp_path / "report_corrupt_infographic.pdf")


def test_content_identity_embedding_check(tmp_path, monkeypatch):
    """report_embeds_infographic verifies content-identity, not just count (OT-R2-03)."""
    monkeypatch.setenv("BIOMNI_RESULTS", str(tmp_path))
    import importlib
    import build_report
    import report_qc
    importlib.reload(build_report)
    importlib.reload(report_qc)

    figs_dir = tmp_path / "figures"
    figs_dir.mkdir(parents=True, exist_ok=True)
    for name in ("figure_1_target_ranking.png", "figure_2_datatype_heatmap.png"):
        PILImage.new("RGB", (100, 100), "#D4A04A").save(str(figs_dir / name))

    # Create the real infographic (non-blank, large enough)
    info_path = tmp_path / "infographic_open_targets_workflow.png"
    _make_test_infographic(info_path)

    facts = _minimal_facts()
    facts_path = tmp_path / "report_facts.json"
    facts_path.write_text(json.dumps(facts), encoding="utf-8")

    pdf = build_report.build_pdf(facts_path, tmp_path / "report.pdf")

    # The real infographic must be found by content identity
    ok, detail = report_qc.report_embeds_infographic(pdf, info_path)
    assert ok, f"content-identity check should pass for the real infographic: {detail}"
    embedding = report_qc.inspect_infographic_embedding(pdf, info_path)
    assert embedding["state"] == "pass"
    assert embedding["embedded_page"] == 1, (
        "the infographic must be the first substantive visual on page 1"
    )
    assert embedding["image_pixel_sha256"] == embedding["embedded_pixel_sha256"]

    # A different image must NOT be found
    fake_path = tmp_path / "fake_infographic.png"
    _make_test_infographic(fake_path, size=(600, 400))
    ok2, detail2 = report_qc.report_embeds_infographic(pdf, fake_path)
    assert not ok2, (
        f"a substituted image must not pass content-identity: {detail2}"
    )
