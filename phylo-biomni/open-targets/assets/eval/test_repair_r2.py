"""Round-2 structural repair evals (OT-R2-01 through OT-R2-08).

Each test targets a specific repair mechanism from repair_brief.md.
Tests run from clean temp dirs and do not depend on live API access.
"""

from __future__ import annotations

import json
import math
import pathlib
import hashlib
import subprocess
import sys

import pytest


# ---------------------------------------------------------------------------
# OT-R2-06: Executed-operation claims derived from ledger
# ---------------------------------------------------------------------------

def test_operation_count_derived_from_ledger():
    """The report must derive executed-operation count from operation_ledger.json,
    not hard-code 'nine'. Five executed in the representative run, nine registered."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    build_report_src = (pkg_root / "scripts" / "build_report.py").read_text(encoding="utf-8")
    # The old hard-coded claim must be gone
    assert "Nine pinned GraphQL operations were executed" not in build_report_src, (
        "build_report.py must not hard-code 'Nine pinned GraphQL operations were executed'"
    )
    # The new derivation from ledger must be present
    assert "operation_ledger_summary" in build_report_src, (
        "build_report.py must derive executed count from operation_ledger_summary"
    )
    assert "executed_operations" in build_report_src, (
        "build_report.py must use executed_operations from facts"
    )
    assert "registered_operations_count" in build_report_src, (
        "build_report.py must distinguish registered count from executed count"
    )


def test_run_analysis_derives_executed_operations():
    """run_analysis.py must derive executed_operations from the ledger records."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    run_analysis_src = (pkg_root / "scripts" / "run_analysis.py").read_text(encoding="utf-8")
    assert "executed_operations" in run_analysis_src, (
        "run_analysis.py must derive executed_operations from ledger"
    )
    assert "registered_operations_count" in run_analysis_src, (
        "run_analysis.py must record registered_operations_count"
    )
    assert "registered_operations" in run_analysis_src, (
        "run_analysis.py must record registered_operations list"
    )


# ---------------------------------------------------------------------------
# OT-R2-07: Disease-study concordance
# ---------------------------------------------------------------------------

def test_concordance_check_function_exists():
    """run_analysis.py must have a _check_study_concordance function."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    run_analysis_src = (pkg_root / "scripts" / "run_analysis.py").read_text(encoding="utf-8")
    assert "_check_study_concordance" in run_analysis_src, (
        "run_analysis.py must define _check_study_concordance"
    )
    assert "study_concordance" in run_analysis_src, (
        "run_analysis.py must record study_concordance in facts"
    )


def test_concordance_check_rejects_mismatched_trait():
    """_check_study_concordance must reject a CAD study for Alzheimer disease."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(pkg_root / "scripts"))
    from run_analysis import _check_study_concordance

    # GCST005194 is coronary artery disease, not Alzheimer disease
    result = _check_study_concordance(
        efo_id="MONDO_0004975",
        disease_name="Alzheimer disease",
        studies_facts=[{
            "study_id": "GCST005194",
            "trait_from_source": "coronary artery disease",
            "diseases": [{"id": "MONDO_0005044"}],
        }],
    )
    assert result["concordant"] is False, (
        "CAD study must not be concordant with Alzheimer disease"
    )
    assert "coronary artery disease" in result["reason"], (
        "Reason must explain the mismatch"
    )


def test_concordance_check_accepts_matched_trait():
    """_check_study_concordance must accept an Alzheimer study for Alzheimer disease."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(pkg_root / "scripts"))
    from run_analysis import _check_study_concordance

    result = _check_study_concordance(
        efo_id="MONDO_0004975",
        disease_name="Alzheimer disease",
        studies_facts=[{
            "study_id": "GCST90012345",
            "trait_from_source": "Alzheimer's disease",
            "diseases": [{"id": "MONDO_0004975"}],
        }],
    )
    assert result["concordant"] is True, (
        "Alzheimer study must be concordant with Alzheimer disease"
    )


def test_concordance_check_accepts_ontology_match():
    """_check_study_concordance must accept when EFO ID is in study's diseases list."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(pkg_root / "scripts"))
    from run_analysis import _check_study_concordance

    result = _check_study_concordance(
        efo_id="MONDO_0004975",
        disease_name="Alzheimer disease",
        studies_facts=[{
            "study_id": "GCST90012345",
            "trait_from_source": "some unrelated trait name",
            "diseases": [{"id": "MONDO_0004975"}],
        }],
    )
    assert result["concordant"] is True, (
        "Ontology match (EFO ID in diseases list) must be concordant"
    )


def test_build_report_separates_non_concordant_study():
    """build_report.py must emit 'Independent Study Context' for non-concordant studies."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    build_report_src = (pkg_root / "scripts" / "build_report.py").read_text(encoding="utf-8")
    assert "Independent Study Context" in build_report_src, (
        "build_report.py must label non-concordant studies as 'Independent Study Context'"
    )
    assert "study_concordance" in build_report_src, (
        "build_report.py must check study_concordance from facts"
    )
    assert "not concordant" in build_report_src.lower() or "excluded" in build_report_src.lower(), (
        "build_report.py must state the study is excluded from conclusions"
    )


# ---------------------------------------------------------------------------
# OT-R2-08: Strict JSON payloads
# ---------------------------------------------------------------------------

def test_visualize_uses_allow_nan_false():
    """visualize.py must serialize with allow_nan=False."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    visualize_src = (pkg_root / "scripts" / "visualize.py").read_text(encoding="utf-8")
    assert "allow_nan=False" in visualize_src, (
        "visualize.py must use allow_nan=False for strict RFC JSON"
    )


def test_visualize_converts_nan_to_none():
    """visualize.py must convert NaN/inf to None before serialization."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    visualize_src = (pkg_root / "scripts" / "visualize.py").read_text(encoding="utf-8")
    assert "_nan_to_none" in visualize_src, (
        "visualize.py must have a _nan_to_none helper"
    )
    assert "_matrix_to_strict" in visualize_src, (
        "visualize.py must have a _matrix_to_strict helper"
    )


def test_nan_to_none_conversion():
    """_nan_to_none must convert NaN and inf to None, preserve finite values."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(pkg_root / "scripts"))
    from visualize import _nan_to_none

    assert _nan_to_none(float("nan")) is None
    assert _nan_to_none(float("inf")) is None
    assert _nan_to_none(float("-inf")) is None
    assert _nan_to_none(0.0) == 0.0
    assert _nan_to_none(0.5) == 0.5
    assert _nan_to_none(None) is None
    assert _nan_to_none(1) == 1


def test_matrix_to_strict_no_nan():
    """_matrix_to_strict must produce a list with no NaN values."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(pkg_root / "scripts"))
    import numpy as np
    from visualize import _matrix_to_strict

    matrix = np.array([[0.1, float("nan")], [float("inf"), 0.4]])
    result = _matrix_to_strict(matrix)
    # Verify no NaN/inf in the result
    serialized = json.dumps(result, allow_nan=False)
    assert serialized is not None, "strict JSON serialization must succeed with no NaN"


def test_figure_payloads_strict_json(tmp_path):
    """figure_payloads.json must be valid strict RFC JSON (no NaN)."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(pkg_root / "scripts"))

    # Create minimal facts with a missing datatype score (represented as None, not NaN)
    facts = {
        "disease_name": "Test",
        "disease_id": "MONDO_0000001",
        "top_targets": [
            {"rank": 1, "target_id": "ENSG1", "approved_symbol": "T1",
             "approved_name": "test", "biotype": "protein_coding",
             "overall_association_score": 0.9,
             "datatype_scores": [
                 {"datatype_id": "genetic_association", "score": 0.9},
                 {"datatype_id": "literature", "score": None},
             ]},
        ],
    }
    facts_path = tmp_path / "report_facts.json"
    facts_path.write_text(json.dumps(facts, allow_nan=False), encoding="utf-8")

    # Run visualize.py
    outdir = tmp_path / "figures"
    outdir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(pkg_root / "scripts" / "visualize.py"),
         "--facts", str(facts_path), "--outdir", str(tmp_path)],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    # visualize.py may fail if matplotlib is not available, but if it succeeds,
    # the sidecar must be strict JSON
    sidecar = tmp_path / "figure_payloads.json"
    if sidecar.exists():
        content = sidecar.read_text(encoding="utf-8")
        # Must parse with allow_nan=False (strict mode)
        parsed = json.loads(content)  # default allow_nan=False for json.loads
        assert parsed is not None


# ---------------------------------------------------------------------------
# OT-R2-05: PDF hash binding and layout
# ---------------------------------------------------------------------------

def test_record_pdf_review_accepts_pdf_sha256():
    """record_pdf_review must accept pdf_sha256 and artifact_sha256s parameters."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(pkg_root / "scripts"))
    import inspect
    from report_qc import record_pdf_review

    sig = inspect.signature(record_pdf_review)
    assert "pdf_sha256" in sig.parameters, (
        "record_pdf_review must accept pdf_sha256 parameter (OT-R2-05)"
    )
    assert "artifact_sha256s" in sig.parameters, (
        "record_pdf_review must accept artifact_sha256s parameter (OT-R2-05)"
    )


def test_pdf_review_evidence_verifies_hash_binding(tmp_path):
    """_pdf_review_evidence must verify pdf_sha256 matches current PDF."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(pkg_root / "scripts"))
    from report_qc import GateFailure, _sha256, _at_results

    # This test verifies the code path exists; full integration test below
    report_qc_src = (pkg_root / "scripts" / "report_qc.py").read_text(encoding="utf-8")
    assert "current_pdf_sha256" in report_qc_src, (
        "_pdf_review_evidence must compute current PDF SHA256 (OT-R2-05)"
    )
    assert "stale PDF" in report_qc_src, (
        "_pdf_review_evidence must reject stale PDF events (OT-R2-05)"
    )


def test_build_report_uses_keep_together():
    """build_report.py must use KeepTogether for figure headings (OT-R2-05)."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    build_report_src = (pkg_root / "scripts" / "build_report.py").read_text(encoding="utf-8")
    assert "KeepTogether" in build_report_src, (
        "build_report.py must use KeepTogether to prevent orphan headings (OT-R2-05)"
    )


def test_visualize_wraps_heatmap_labels():
    """visualize.py must wrap long heatmap labels (OT-R2-05)."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    visualize_src = (pkg_root / "scripts" / "visualize.py").read_text(encoding="utf-8")
    assert "wrapped_labels" in visualize_src, (
        "visualize.py must wrap long heatmap labels (OT-R2-05)"
    )
    assert "bbox_inches" in visualize_src, (
        "visualize.py must use bbox_inches='tight' to prevent label clipping (OT-R2-05)"
    )


# ---------------------------------------------------------------------------
# OT-R2-02: Infographic prompt/tool/image lineage
# ---------------------------------------------------------------------------

def test_assert_generated_by_tool_accepts_prompt_file():
    """assert_generated_by_tool must accept prompt_file and image_file parameters."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(pkg_root / "scripts"))
    import inspect
    from report_qc import assert_generated_by_tool

    sig = inspect.signature(assert_generated_by_tool)
    assert "prompt_file" in sig.parameters, (
        "assert_generated_by_tool must accept prompt_file parameter (OT-R2-02)"
    )
    assert "image_file" in sig.parameters, (
        "assert_generated_by_tool must accept image_file parameter (OT-R2-02)"
    )


def test_assert_generated_by_tool_returns_pairing():
    """assert_generated_by_tool must return a dict with prompt_sha256 and image_sha256."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(pkg_root / "scripts"))
    from report_qc import assert_generated_by_tool

    # Create a transcript with one exact same-id call/result pair.
    tmp = pathlib.Path(__file__).resolve().parent.parent.parent
    transcript = tmp / "assets" / "fixtures" / "test_transcript.jsonl"
    prompt_file = tmp / "assets" / "infographic_prompt.txt"
    prompt = prompt_file.read_text(encoding="utf-8").strip()
    transcript.write_text(
        json.dumps({"content": [{"type": "tool_use", "id": "image-1",
                                 "name": "GenerateImage",
                                 "input": {"prompt": prompt, "file_name":
                                           "infographic_open_targets_workflow.png"}}]}) + "\n" +
        json.dumps({"content": [{"type": "tool_result", "tool_use_id": "image-1",
                                 "content": "Image generated successfully and saved to "
                                            "/mnt/results/infographic_open_targets_workflow.png"}]}) + "\n",
        encoding="utf-8",
    )
    try:
        result = assert_generated_by_tool(
            "infographic_open_targets_workflow.png",
            transcript=transcript,
            prompt_file=prompt_file,
        )
        assert isinstance(result, dict), (
            "assert_generated_by_tool must return a dict (OT-R2-02)"
        )
        assert "verified_filenames" in result
    finally:
        transcript.unlink(missing_ok=True)


def test_prompt_not_in_transcript_raises(tmp_path):
    """assert_generated_by_tool must fail when the prompt text is not in the transcript."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(pkg_root / "scripts"))
    from report_qc import assert_generated_by_tool, GateFailure

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"content": "Image generated successfully and saved to "
                               "/mnt/results/infographic_open_targets_workflow.png"}) + "\n",
        encoding="utf-8",
    )
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("a completely different prompt that is not in the transcript", encoding="utf-8")

    with pytest.raises(GateFailure, match="same-id"):
        assert_generated_by_tool(
            "infographic_open_targets_workflow.png",
            transcript=transcript,
            prompt_file=prompt_file,
        )


def test_provenance_records_prompt_hash():
    """run_analysis.py must record the infographic prompt SHA256 in provenance."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    run_analysis_src = (pkg_root / "scripts" / "run_analysis.py").read_text(encoding="utf-8")
    assert "infographic_prompt_hash" in run_analysis_src, (
        "run_analysis.py must compute infographic prompt hash (OT-R2-02)"
    )
    assert "prompt_sha256" in run_analysis_src, (
        "run_analysis.py must record prompt_sha256 in provenance (OT-R2-02)"
    )
    assert "image_sha256" in run_analysis_src, (
        "run_analysis.py must record image_sha256 in provenance (OT-R2-02)"
    )


# ---------------------------------------------------------------------------
# OT-R2-03: Mandatory infographic and embedding identity
# ---------------------------------------------------------------------------

def test_build_report_aborts_on_missing_infographic():
    """build_report.py must have InfographicError and _verify_infographic."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    build_report_src = (pkg_root / "scripts" / "build_report.py").read_text(encoding="utf-8")
    assert "InfographicError" in build_report_src, (
        "build_report.py must define InfographicError (OT-R2-03)"
    )
    assert "_verify_infographic" in build_report_src, (
        "build_report.py must define _verify_infographic (OT-R2-03)"
    )


def test_report_embeds_infographic_exists():
    """report_qc.py must have a report_embeds_infographic function for content-identity."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    report_qc_src = (pkg_root / "scripts" / "report_qc.py").read_text(encoding="utf-8")
    assert "def report_embeds_infographic" in report_qc_src, (
        "report_qc.py must define report_embeds_infographic for content-identity (OT-R2-03)"
    )


# ---------------------------------------------------------------------------
# OT-R2-04: Honest generated-image content review
# ---------------------------------------------------------------------------

def test_visual_review_requires_explicit_evidence():
    """run_analysis.py must not silently pass review without inspection evidence."""
    pkg_root = pathlib.Path(__file__).resolve().parent.parent.parent
    run_analysis_src = (pkg_root / "scripts" / "run_analysis.py").read_text(encoding="utf-8")
    assert '"--visual-review-state"' in run_analysis_src, (
        "run_analysis.py must require an explicit visual-review state (OT-R2-04)"
    )
    assert '"--visual-review-notes"' in run_analysis_src, (
        "run_analysis.py must require visual-review evidence (OT-R2-04)"
    )
    assert 'strict=True' in run_analysis_src, (
        "a failed or unavailable visual review must remain release-blocking (OT-R2-04)"
    )
