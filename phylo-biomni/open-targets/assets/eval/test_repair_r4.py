"""Round-4 three-state infographic-lineage regressions (OT-R4-01 .. OT-R4-18).

repair-brief.md (r4): the fail-closed lineage rule required a *complete exact* prompt from the
platform trace. Real execution traces truncate the GenerateImage tool-call ``args`` (the fixed
prompt is ~1124 chars; the trace caps args near 600 chars and appends a literal
``[truncated N chars]`` marker), so ``json.loads(args)`` fails, the call is dropped by the dict
guard, and the receipt records ``infographics_generated_by_tool: fail`` (12/13) even though the
image is honest.

These tests pin a fail-closed *three-state* outcome
(``verified`` / ``not_evaluable_platform_trace`` / ``failed``) and its cross-surface disclosure
contract:

* the exact runtime truncation regresses as ``not_evaluable_platform_trace`` — never ``verified``
  and never a fabricated pass, and the truncated args string is never mapped to a prompt key;
* a complete same-id call/result with the exact prompt/filename and an output hash ``verified``;
* truncated, id-less, results-local/agent-writable, and hashless counterexamples never ``verified``;
* reconstructed, paraphrased, duplicate, mismatched, fabricated, and contradictory evidence ``failed``;
* facts, provenance, report, and receipt carry the SAME state, and a ``not_evaluable`` lineage is
  release-blocking unless every surface discloses that provenance is not independently verified.

Discrimination: the module-level import of ``classify_infographic_lineage`` fails on the r3 source
(the tri-state API does not exist there), so the whole file errors on the source candidate and
passes only after the repair. Stdlib + Pillow + pytest only; no live API and no GenerateImage call.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest
from PIL import Image as PILImage

# Module-level import is the source/repaired discriminator: absent on r3 -> collection error there.
from report_qc import (  # noqa: E402
    LINEAGE_DISCLOSURE_MARKER,
    LINEAGE_FAILED,
    LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE,
    LINEAGE_VERIFIED,
    classify_infographic_lineage,
)

_PROMPT_PATH = pathlib.Path(__file__).resolve().parent.parent / "infographic_prompt.txt"
_FILENAME = "infographic_open_targets_workflow.png"
# Synthetic, real-*shaped* platform ids — fabricated for these fixtures only, never copied from any
# real/live transcript (customer-neutral).
_CALL_ID = "toolu_synthetic_generateimage_r4_0001"
_ALT_ID = "toolu_synthetic_generateimage_r4_0002"


def _prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8").strip()


def _write(path: pathlib.Path, records: list[dict]) -> pathlib.Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def _image(path: pathlib.Path, size=(320, 240)) -> pathlib.Path:
    arr = np.random.default_rng(7).integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)
    PILImage.fromarray(arr).save(str(path))
    return path


# ---------------------------------------------------------------------------
# Transcript builders (typed content blocks and native assistant.tool_calls[])
# ---------------------------------------------------------------------------

def typed_call(prompt, filename, call_id, filename_key="file_name"):
    return {"content": [{"type": "tool_use", "id": call_id, "name": "GenerateImage",
                         "input": {"prompt": prompt, filename_key: filename}}]}


def typed_result(filename, join_id):
    return {"content": [{"type": "tool_result", "tool_use_id": join_id,
                         "content": f"Image generated successfully and saved to /mnt/results/{filename}"}]}


def native_call(prompt, filename, call_id):
    args = json.dumps({"prompt": prompt, "file_name": filename,
                       "description": "Open Targets workflow infographic"})
    return {"i": 3, "type": "assistant", "content": "",
            "tool_calls": [{"id": call_id, "name": "GenerateImage", "args": args}]}


def native_result(filename, join_id):
    return {"i": 4, "type": "tool", "tool_name": "GenerateImage", "tool_call_id": join_id,
            "content": f"Image generated successfully and saved to /mnt/results/{filename}"}


def native_call_truncated(filename, call_id, *, keep=560, marker=True):
    """A GenerateImage call whose args are a valid JSON-object *prefix* cut short by the platform.

    This mirrors the observed runtime condition: args is a string that begins ``{"prompt": "<...>``
    and is truncated mid-value (optionally with the literal ``[truncated N chars]`` marker). It is
    NOT valid JSON, so the exact prompt is unreadable and must never be reconstructed.
    """
    full = json.dumps({"prompt": _prompt(), "file_name": filename,
                       "description": "Open Targets workflow infographic"})
    cut = full[:keep]
    dropped = len(full) - keep
    args = cut + (f" [truncated {dropped} chars]" if marker else "")
    return {"i": 3, "type": "assistant", "content": "",
            "tool_calls": [{"id": call_id, "name": "GenerateImage", "args": args}]}


def native_call_idless(prompt, filename):
    args = json.dumps({"prompt": prompt, "file_name": filename})
    return {"i": 3, "type": "assistant", "content": "",
            "tool_calls": [{"name": "GenerateImage", "args": args}]}


# ===========================================================================
# Group A — the classifier's three states (absolute paths; no results mount)
# ===========================================================================

def test_r4_01_truncated_runtime_condition_is_not_evaluable(tmp_path):
    """OT-R4-01: the exact runtime truncation -> not_evaluable_platform_trace, never verified."""
    img = _image(tmp_path / _FILENAME)
    t = _write(tmp_path / "t.jsonl", [native_call_truncated(_FILENAME, _CALL_ID),
                                      native_result(_FILENAME, _CALL_ID)])
    res = classify_infographic_lineage(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH, image_file=img)
    assert res["state"] == LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE
    assert res["state"] != LINEAGE_VERIFIED
    ev = res["evidence"]
    # never reconstruct or misattribute the truncated string to a prompt key
    assert ev.get("prompt_verifiable") is False
    assert ev.get("prompt_sha256") is None
    assert "observed_prompt" not in ev and "prompt" not in ev


def test_r4_02_truncated_without_marker_prefix_is_not_evaluable(tmp_path):
    """OT-R4-02: a cut ``{"...`` JSON-object prefix (no explicit marker) is still not_evaluable."""
    img = _image(tmp_path / _FILENAME)
    t = _write(tmp_path / "t.jsonl", [native_call_truncated(_FILENAME, _CALL_ID, marker=False),
                                      native_result(_FILENAME, _CALL_ID)])
    res = classify_infographic_lineage(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH, image_file=img)
    assert res["state"] == LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE


@pytest.mark.parametrize("builder", ["typed", "native"])
def test_r4_03_complete_pair_with_hash_verifies(tmp_path, builder):
    """OT-R4-03: complete same-id call/result + exact prompt/filename + output hash -> verified."""
    img = _image(tmp_path / _FILENAME)
    if builder == "typed":
        recs = [typed_call(_prompt(), _FILENAME, _CALL_ID), typed_result(_FILENAME, _CALL_ID)]
    else:
        recs = [native_call(_prompt(), _FILENAME, _CALL_ID), native_result(_FILENAME, _CALL_ID)]
    t = _write(tmp_path / "t.jsonl", recs)
    res = classify_infographic_lineage(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH, image_file=img)
    assert res["state"] == LINEAGE_VERIFIED
    assert res["evidence"]["tool_use_id"] == _CALL_ID
    assert res["evidence"]["image_sha256"]  # a real output hash is recorded


def test_r4_04_hashless_pair_is_not_evaluable(tmp_path):
    """OT-R4-04: an otherwise-exact pairing with no computable output hash -> not_evaluable."""
    t = _write(tmp_path / "t.jsonl", [typed_call(_prompt(), _FILENAME, _CALL_ID),
                                      typed_result(_FILENAME, _CALL_ID)])
    res = classify_infographic_lineage(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH,
                                       image_file=tmp_path / "does_not_exist.png")
    assert res["state"] == LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE
    assert res["state"] != LINEAGE_VERIFIED


def test_r4_05_agent_writable_source_is_not_evaluable(tmp_path):
    """OT-R4-05: an exact pairing read from an agent-writable/results-local copy -> not_evaluable."""
    img = _image(tmp_path / _FILENAME)
    t = _write(tmp_path / "t.jsonl", [typed_call(_prompt(), _FILENAME, _CALL_ID),
                                      typed_result(_FILENAME, _CALL_ID)])
    res = classify_infographic_lineage(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH,
                                       image_file=img, transcript_trusted=False)
    assert res["state"] == LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE
    assert res["state"] != LINEAGE_VERIFIED


@pytest.mark.parametrize("builder", ["typed", "native"])
def test_r4_06_idless_call_is_not_evaluable(tmp_path, builder):
    """OT-R4-06: a genuine GenerateImage call with no join id -> not_evaluable, never verified."""
    img = _image(tmp_path / _FILENAME)
    if builder == "typed":
        call = {"content": [{"type": "tool_use", "name": "GenerateImage",
                             "input": {"prompt": _prompt(), "file_name": _FILENAME}}]}
    else:
        call = native_call_idless(_prompt(), _FILENAME)
    t = _write(tmp_path / "t.jsonl", [call, native_result(_FILENAME, _CALL_ID)])
    res = classify_infographic_lineage(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH, image_file=img)
    assert res["state"] == LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE
    assert res["state"] != LINEAGE_VERIFIED


def test_r4_07_transcript_missing_is_failed_without_attestation(tmp_path):
    """OT-R4-07: no trace and no native attestation -> failed (cannot evidence a genuine event)."""
    res = classify_infographic_lineage(_FILENAME, transcript=tmp_path / "none.jsonl",
                                       prompt_file=_PROMPT_PATH, image_file=None)
    assert res["state"] == LINEAGE_FAILED


def test_r4_08_missing_trace_with_native_attestation_is_not_evaluable(tmp_path):
    """OT-R4-08: no trace but an attested native execution record -> not_evaluable, never verified."""
    res = classify_infographic_lineage(_FILENAME, transcript=tmp_path / "none.jsonl",
                                       prompt_file=_PROMPT_PATH, image_file=None,
                                       platform_event_attested=True)
    assert res["state"] == LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE
    assert res["state"] != LINEAGE_VERIFIED


@pytest.mark.parametrize("builder", ["typed", "native"])
def test_r4_09_paraphrased_prompt_fails(tmp_path, builder):
    """OT-R4-09: a complete but non-matching (paraphrased) prompt -> failed, not not_evaluable."""
    img = _image(tmp_path / _FILENAME)
    if builder == "typed":
        recs = [typed_call("a paraphrased, not-exact infographic prompt", _FILENAME, _CALL_ID),
                typed_result(_FILENAME, _CALL_ID)]
    else:
        recs = [native_call("a paraphrased, not-exact infographic prompt", _FILENAME, _CALL_ID),
                native_result(_FILENAME, _CALL_ID)]
    t = _write(tmp_path / "t.jsonl", recs)
    res = classify_infographic_lineage(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH, image_file=img)
    assert res["state"] == LINEAGE_FAILED
    assert res["state"] != LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE


def test_r4_10_mismatched_filename_fails(tmp_path):
    """OT-R4-10: a call/result naming a different file -> failed."""
    img = _image(tmp_path / _FILENAME)
    t = _write(tmp_path / "t.jsonl", [typed_call(_prompt(), "some_other_diagram.png", _CALL_ID),
                                      typed_result("some_other_diagram.png", _CALL_ID)])
    res = classify_infographic_lineage(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH, image_file=img)
    assert res["state"] == LINEAGE_FAILED


def test_r4_11_duplicate_same_id_fails(tmp_path):
    """OT-R4-11: two calls sharing one id -> failed (ambiguous)."""
    img = _image(tmp_path / _FILENAME)
    t = _write(tmp_path / "t.jsonl", [typed_call(_prompt(), _FILENAME, _CALL_ID),
                                      typed_call(_prompt(), _FILENAME, _CALL_ID),
                                      typed_result(_FILENAME, _CALL_ID)])
    res = classify_infographic_lineage(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH, image_file=img)
    assert res["state"] == LINEAGE_FAILED


def test_r4_12_fabricated_result_without_call_fails(tmp_path):
    """OT-R4-12: a success result with no GenerateImage call anywhere -> failed."""
    img = _image(tmp_path / _FILENAME)
    t = _write(tmp_path / "t.jsonl", [typed_result(_FILENAME, _CALL_ID)])
    res = classify_infographic_lineage(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH, image_file=img)
    assert res["state"] == LINEAGE_FAILED


def test_r4_13_contradictory_same_id_filenames_fail(tmp_path):
    """OT-R4-13: a same-id call for the wanted file whose result returns a different file -> failed."""
    img = _image(tmp_path / _FILENAME)
    t = _write(tmp_path / "t.jsonl", [typed_call(_prompt(), _FILENAME, _CALL_ID),
                                      typed_result("surreal_other.png", _CALL_ID)])
    res = classify_infographic_lineage(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH, image_file=img)
    assert res["state"] == LINEAGE_FAILED


def test_r4_14_unrecognized_malformed_args_fail(tmp_path):
    """OT-R4-14: unparseable args with NO platform-truncation signal -> failed (not not_evaluable)."""
    img = _image(tmp_path / _FILENAME)
    call = {"i": 3, "type": "assistant", "content": "",
            "tool_calls": [{"id": _CALL_ID, "name": "GenerateImage", "args": "{not-valid-json"}]}
    t = _write(tmp_path / "t.jsonl", [call, native_result(_FILENAME, _CALL_ID)])
    res = classify_infographic_lineage(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH, image_file=img)
    assert res["state"] == LINEAGE_FAILED
    assert res["state"] != LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE


# ===========================================================================
# Group B — cross-surface disclosure and the receipt's blocking decision
# ===========================================================================

def _rq(monkeypatch, tmp_path):
    monkeypatch.setenv("BIOMNI_RESULTS", str(tmp_path))
    import importlib
    import report_qc
    importlib.reload(report_qc)
    return report_qc


def _minimal_facts(lineage: dict) -> dict:
    return {
        "disease_name": "Test Disease", "disease_id": "MONDO_0000001",
        "release_label": "26.06", "api_version": "26.6.3", "access_date": "2026-08-16",
        "total_associated_targets": 100,
        "top_targets": [{"rank": 1, "target_id": "ENSG00000142192", "approved_symbol": "T1",
                         "approved_name": "test target 1", "biotype": "protein_coding",
                         "overall_association_score": 0.9,
                         "datatype_scores": [{"datatype_id": "genetic_association", "score": 0.9}]}],
        "evidence_count": 5,
        "evidence_rows": [{"datasource_id": "genetic_literature", "datatype_id": "genetic_association",
                           "score": 0.8, "publication_first_author": "Author", "publication_year": 2024}],
        "studies": [], "credible_sets": [], "completeness": "bounded_sample",
        "max_pages": 10, "max_rows": 500,
        "operation_ledger_summary": {"total_attempts": 5, "successes": 5, "failures": 0, "by_state": {}},
        "validation_errors": [], "caveats": ["Test caveat."], "next_steps": ["Test next step."],
        "infographic_lineage": lineage,
    }


def _surfaces(rq, tmp_path, state, *, disclose=True):
    """Write image + facts + provenance + a real PDF report for one lineage state."""
    import importlib
    import build_report
    importlib.reload(build_report)
    _image(tmp_path / _FILENAME, size=(800, 600))
    verified = state == LINEAGE_VERIFIED
    disclosure = "" if verified else (
        "Infographic provenance is not independently verified from the platform execution trace; "
        "no verification claim is made." if disclose else "")
    lineage = {"state": state, "reason": "regression fixture",
               "verification_claim": verified,
               "prompt_verifiable": verified,
               "image_sha256": rq._sha256(tmp_path / _FILENAME),
               "disclosure": disclosure}
    facts = _minimal_facts(lineage)
    facts_path = tmp_path / "report_facts.json"
    facts_path.write_text(json.dumps(facts), encoding="utf-8")
    provenance = {"schema": "phylo-ot-provenance/1", "disease": {"id": "MONDO_0000001"},
                  "infographic": {"filename": _FILENAME,
                                  "lineage_state": state,
                                  "independently_verified": verified,
                                  "disclosure": disclosure}}
    (tmp_path / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    build_report.build_pdf(facts_path, tmp_path / "report_open-targets.pdf")
    return facts_path


def _receipt_for(rq, tmp_path, transcript_records):
    trace = tmp_path / "execution_trace"
    trace.mkdir(exist_ok=True)
    _write(trace / "transcript.jsonl", transcript_records)
    rq.write_receipt(
        report_name="report_open-targets.pdf", figures=[],
        figure_not_applicable_reason="isolating the infographic lineage gate",
        bundled_files=(), outputs=(),
        infographics=(_FILENAME,), infographic_prompt=str(_PROMPT_PATH),
        contract={}, qc_run_log=None,
        narrative_facts="report_facts.json", narrative_provenance="provenance.json",
        root_artifacts=(_FILENAME,), path="run_receipt.json", strict=False,
    )
    return json.loads((tmp_path / "run_receipt.json").read_text(encoding="utf-8"))


def test_r4_15_receipt_records_verified_and_is_nonblocking(tmp_path, monkeypatch):
    """OT-R4-15: verified lineage -> receipt state verified, not release-blocking."""
    rq = _rq(monkeypatch, tmp_path)
    _surfaces(rq, tmp_path, LINEAGE_VERIFIED)
    receipt = _receipt_for(rq, tmp_path, [typed_call(_prompt(), _FILENAME, _CALL_ID),
                                          typed_result(_FILENAME, _CALL_ID)])
    assert receipt["states"]["infographics_generated_by_tool"] == LINEAGE_VERIFIED
    assert "infographics_generated_by_tool" not in receipt["release_blocking_gates"]


def test_r4_16_disclosed_not_evaluable_is_nonblocking(tmp_path, monkeypatch):
    """OT-R4-16: truncated + full disclosure across facts/provenance/report -> non-blocking."""
    rq = _rq(monkeypatch, tmp_path)
    _surfaces(rq, tmp_path, LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE, disclose=True)
    receipt = _receipt_for(rq, tmp_path, [native_call_truncated(_FILENAME, _CALL_ID),
                                          native_result(_FILENAME, _CALL_ID)])
    assert receipt["states"]["infographics_generated_by_tool"] == LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE
    # never a boolean pass, but not release-blocking once every surface discloses
    assert receipt["infographics_generated_by_tool"] is False
    assert "infographics_generated_by_tool" not in receipt["release_blocking_gates"]
    # the report itself carries the disclosure and makes no verification claim
    text = rq._extract_report_text("report_open-targets.pdf")
    assert LINEAGE_DISCLOSURE_MARKER.lower() in text.lower()


def test_r4_17_undisclosed_not_evaluable_blocks(tmp_path, monkeypatch):
    """OT-R4-17: the same truncation WITHOUT disclosure is release-blocking (no silent pass)."""
    rq = _rq(monkeypatch, tmp_path)
    # facts/provenance/report claim verified while the trace is only truncated -> disclosure missing
    _surfaces(rq, tmp_path, LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE, disclose=False)
    receipt = _receipt_for(rq, tmp_path, [native_call_truncated(_FILENAME, _CALL_ID),
                                          native_result(_FILENAME, _CALL_ID)])
    assert receipt["states"]["infographics_generated_by_tool"] == LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE
    assert "infographics_generated_by_tool" in receipt["release_blocking_gates"]


def test_r4_18_failed_lineage_blocks_and_states_agree(tmp_path, monkeypatch):
    """OT-R4-18: a paraphrased (failed) lineage is release-blocking; receipt/facts states agree."""
    rq = _rq(monkeypatch, tmp_path)
    _surfaces(rq, tmp_path, LINEAGE_FAILED, disclose=True)
    receipt = _receipt_for(rq, tmp_path, [typed_call("not the package prompt", _FILENAME, _CALL_ID),
                                          typed_result(_FILENAME, _CALL_ID)])
    assert receipt["states"]["infographics_generated_by_tool"] == LINEAGE_FAILED
    assert "infographics_generated_by_tool" in receipt["release_blocking_gates"]
    # cross-surface: the receipt's independent re-classification equals the facts-recorded state
    assert receipt["evidence"]["infographics_generated_by_tool"]["facts_state"] == LINEAGE_FAILED
