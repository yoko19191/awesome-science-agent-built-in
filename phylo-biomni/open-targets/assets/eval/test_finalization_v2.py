"""Attempt-1 repair tests: prompt execution and transcript-envelope lineage."""

from __future__ import annotations

import json
import pathlib
import re

import pytest


COMPLETE_SAMPLE = (
    "Prioritize therapeutic targets for Alzheimer disease (MONDO_0004975) using the "
    "Open Targets Platform GraphQL API. Rank a bounded top-10 target set with datatype "
    "scores, retrieve bounded evidence for the top target, inspect GWAS study GCST005194 "
    "and its credible sets, and produce a provider-styled canonical PDF report with deterministic "
    "figures and a GenerateImage infographic."
)


def test_complete_sample_executes_without_clarification():
    from prompt_contract import resolve_prompt

    decision = resolve_prompt(COMPLETE_SAMPLE)
    assert decision == {
        "status": "ready",
        "inputs": {
            "disease_id": "MONDO_0004975",
            "top_n": 10,
            "study_id": "GCST005194",
            "scope": "with_credible_sets",
        },
        "selected_branch_ids": ["scope:with_credible_sets"],
        "questions": [],
    }


def test_declared_starting_prompt_executes_without_clarification():
    from prompt_contract import resolve_prompt

    skill = (pathlib.Path(__file__).resolve().parent.parent.parent / "SKILL.md").read_text(
        encoding="utf-8"
    )
    match = re.search(r'^starting-prompt: "([^"]+)"$', skill, re.MULTILINE)
    assert match, "SKILL.md must declare a one-line starting prompt"
    decision = resolve_prompt(match.group(1))
    assert decision["status"] == "ready"
    assert decision["questions"] == []
    assert decision["inputs"]["disease_id"] == "MONDO_0004975"
    assert decision["inputs"]["top_n"] == 10
    assert decision["inputs"]["study_id"] == "GCST005194"
    assert decision["inputs"]["scope"] == "with_credible_sets"


def test_evidence_only_uses_documented_top_n_default_without_clarification():
    from prompt_contract import resolve_prompt

    decision = resolve_prompt("Prioritize targets for EFO_0000249; evidence only.")
    assert decision["status"] == "ready"
    assert decision["inputs"] == {
        "disease_id": "EFO_0000249",
        "top_n": 10,
        "study_id": None,
        "scope": "evidence_only",
    }
    assert decision["questions"] == []


@pytest.mark.parametrize(
    ("prompt", "field", "reason_fragment"),
    [
        ("Prioritize targets and inspect study GCST123456.", "disease_id", "missing"),
        ("Compare MONDO_0000001 and EFO_0000002.", "disease_id", "ambiguous"),
        ("Prioritize MONDO_0000001 and inspect credible sets.", "study_id", "no GCST"),
        ("Prioritize MONDO_0000001 for top 10 and top 20.", "top_n", "ambiguous"),
    ],
)
def test_genuinely_missing_or_ambiguous_inputs_request_clarification(prompt, field, reason_fragment):
    from prompt_contract import resolve_prompt

    decision = resolve_prompt(prompt)
    assert decision["status"] == "clarify"
    matching = [question for question in decision["questions"] if question["field"] == field]
    assert matching
    assert reason_fragment.lower() in matching[0]["reason"].lower()


def _api_message_envelope(prompt: str, filename: str) -> dict:
    return {
        "type": "list",
        "data": [{
            "id": "msg-test",
            "role": "assistant",
            "status": "completed",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu-image-1",
                    "name": "GenerateImage",
                    "input": {"prompt": prompt, "file_name": filename},
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu-image-1",
                    "content": f"Image generated successfully and saved to /mnt/results/{filename}",
                },
            ],
        }],
    }


def test_task_messages_api_envelope_preserves_same_id_lineage(tmp_path):
    from report_qc import assert_generated_by_tool

    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("qualitative workflow diagram", encoding="utf-8")
    filename = "infographic_open_targets_workflow.png"
    transcript = tmp_path / "messages.jsonl"
    transcript.write_text(
        json.dumps(_api_message_envelope(prompt_file.read_text(), filename), indent=2) + "\n",
        encoding="utf-8",
    )

    result = assert_generated_by_tool(filename, transcript=transcript, prompt_file=prompt_file)
    assert result["tool_use_id"] == "toolu-image-1"
    assert result["result_id"] == "toolu-image-1"


def test_flattened_tool_records_without_join_ids_are_rejected(tmp_path):
    from report_qc import GateFailure, assert_generated_by_tool

    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("qualitative workflow diagram", encoding="utf-8")
    filename = "infographic_open_targets_workflow.png"
    transcript = tmp_path / "transcript.jsonl"
    records = [
        {
            "i": 1,
            "type": "assistant",
            "content": "",
            "tool_calls": [{
                "name": "GenerateImage",
                "args": json.dumps({"prompt": prompt_file.read_text(), "file_name": filename}),
            }],
        },
        {
            "i": 2,
            "type": "tool",
            "tool_name": "GenerateImage",
            "content": f"Image generated successfully and saved to /mnt/results/{filename}",
        },
    ]
    transcript.write_text("\n".join(map(json.dumps, records)) + "\n", encoding="utf-8")

    with pytest.raises(GateFailure, match="same-id"):
        assert_generated_by_tool(filename, transcript=transcript, prompt_file=prompt_file)
