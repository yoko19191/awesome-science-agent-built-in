"""Round-3 transcript-normalization regressions (OT-R3-01 .. OT-R3-11).

repair-brief.md: the live sample run executed a genuine GenerateImage call/result,
but the package's lineage parser only recognized synthetic typed ``tool_use`` /
``tool_result`` blocks. The observed environment trace used ``type: assistant`` with
``tool_calls[]`` (each ``{"id", "name", "args"}`` where ``args`` is a JSON string) and
``type: tool`` results (``{"tool_call_id", "tool_name", "content"}``), so the production
receipt found zero pairs and failed ``infographics_generated_by_tool``.

These tests pin the *general* normalization mechanism: each supported native schema is
mapped into one immutable internal call/result representation before the SAME fail-closed
lineage rules run. The battery below is parametrized over two builders — the typed schema
and the native ``assistant.tool_calls[]`` + ``type: tool`` schema — so identical evidence
and identical fail-closed behavior are proven for both.

Discrimination: the native ``test_exact_pair_passes`` case fails closed against the
unrepaired parser (0 pairs) and passes only after normalization is added. Every rejection
case asserts BOTH that the correct value is accepted (where applicable) and that the
specific wrong shape is rejected with ``GateFailure``. Stdlib + pytest only; no live API.
"""

from __future__ import annotations

import json
import pathlib

import pytest


def _gate():
    """Import report_qc names *inside* each test, not at module scope.

    Earlier eval modules (test_finalization_v1.py, test_infographic.py) call
    ``importlib.reload(report_qc)``, which rebinds ``GateFailure`` to a fresh class
    object. A module-level ``from report_qc import GateFailure`` would then be stale,
    so ``pytest.raises(GateFailure)`` would check a different class than the reloaded
    function raises. Fetching both names together, fresh, keeps their identity aligned.
    """
    from report_qc import assert_generated_by_tool, GateFailure
    return assert_generated_by_tool, GateFailure


# The real, pinned package prompt — the single source for GenerateImage lineage.
_PROMPT_PATH = pathlib.Path(__file__).resolve().parent.parent / "infographic_prompt.txt"
_FILENAME = "infographic_open_targets_workflow.png"
# Synthetic, real-*shaped* platform tool-call ids — fabricated for these fixtures only,
# never copied from any real/live transcript (customer-neutral).
_CALL_ID = "toolu_synthetic_generateimage_0001"
_ALT_ID = "toolu_synthetic_generateimage_0002"


def _prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8").strip()


def _write(path: pathlib.Path, records: list[dict]) -> pathlib.Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Two builders for the same immutable internal representation
# ---------------------------------------------------------------------------

class TypedSchema:
    """Typed content-block transcript: {"content": [{"type": "tool_use"/"tool_result"}]}."""

    id = "typed"

    def call(self, *, prompt, filename, call_id, filename_key="file_name"):
        return {"content": [{"type": "tool_use", "id": call_id, "name": "GenerateImage",
                             "input": {"prompt": prompt, filename_key: filename}}]}

    def call_without_id(self, *, prompt, filename):
        return {"content": [{"type": "tool_use", "name": "GenerateImage",
                             "input": {"prompt": prompt, "file_name": filename}}]}

    def call_malformed_args(self, *, call_id):
        # A non-dict input can never yield a prompt/filename -> dropped by the dict guard.
        return {"content": [{"type": "tool_use", "id": call_id, "name": "GenerateImage",
                             "input": "not-a-dict"}]}

    def result(self, *, filename, join_id):
        return {"content": [{"type": "tool_result", "tool_use_id": join_id,
                             "content": f"Image generated successfully and saved to "
                                        f"/mnt/results/{filename}"}]}

    def text_only_mention(self, *, filename):
        return {"content": f"I will now generate {filename} for the report."}


class NativeSchema:
    """Native platform transcript: assistant.tool_calls[] (args as JSON string) + type:tool."""

    id = "native"

    def call(self, *, prompt, filename, call_id, filename_key="file_name"):
        args = json.dumps({"prompt": prompt, filename_key: filename,
                           "description": "Open Targets workflow infographic"})
        return {"i": 3, "type": "assistant", "content": "",
                "tool_calls": [{"id": call_id, "name": "GenerateImage", "args": args}]}

    def call_without_id(self, *, prompt, filename):
        args = json.dumps({"prompt": prompt, "file_name": filename})
        return {"i": 3, "type": "assistant", "content": "",
                "tool_calls": [{"name": "GenerateImage", "args": args}]}

    def call_malformed_args(self, *, call_id):
        # args is not valid JSON -> cannot decode to a dict -> dropped by the dict guard.
        return {"i": 3, "type": "assistant", "content": "",
                "tool_calls": [{"id": call_id, "name": "GenerateImage",
                                "args": "{not-valid-json"}]}

    def result(self, *, filename, join_id):
        return {"i": 4, "type": "tool", "tool_name": "GenerateImage",
                "tool_call_id": join_id,
                "content": f"Image generated successfully and saved to /mnt/results/{filename}"}

    def text_only_mention(self, *, filename):
        return {"i": 3, "type": "assistant",
                "content": f"I will now generate {filename} for the report.",
                "tool_calls": []}


SCHEMAS = [TypedSchema(), NativeSchema()]
_ids = [s.id for s in SCHEMAS]


@pytest.fixture(params=SCHEMAS, ids=_ids)
def schema(request):
    return request.param


# ---------------------------------------------------------------------------
# OT-R3-01: the exact same-id pair passes (the core repair; native = RED before fix)
# ---------------------------------------------------------------------------

def test_exact_pair_passes(schema, tmp_path):
    assert_generated_by_tool, _ = _gate()
    t = _write(tmp_path / "t.jsonl", [
        schema.call(prompt=_prompt(), filename=_FILENAME, call_id=_CALL_ID),
        schema.result(filename=_FILENAME, join_id=_CALL_ID),
    ])
    result = assert_generated_by_tool(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH)
    assert result["verified_filenames"] == [_FILENAME]
    assert result["tool_use_id"] == _CALL_ID
    assert result["tool_use_found"] is True


# ---------------------------------------------------------------------------
# OT-R3-02: paraphrased prompt fails closed (exact prompt text is required)
# ---------------------------------------------------------------------------

def test_paraphrased_prompt_fails(schema, tmp_path):
    assert_generated_by_tool, GateFailure = _gate()
    t = _write(tmp_path / "t.jsonl", [
        schema.call(prompt="a paraphrased, not-exact infographic prompt",
                    filename=_FILENAME, call_id=_CALL_ID),
        schema.result(filename=_FILENAME, join_id=_CALL_ID),
    ])
    with pytest.raises(GateFailure, match="same-id"):
        assert_generated_by_tool(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH)


# ---------------------------------------------------------------------------
# OT-R3-03: a call without a real join id is dropped (ids are never invented)
# ---------------------------------------------------------------------------

def test_missing_call_id_fails(schema, tmp_path):
    assert_generated_by_tool, GateFailure = _gate()
    t = _write(tmp_path / "t.jsonl", [
        schema.call_without_id(prompt=_prompt(), filename=_FILENAME),
        schema.result(filename=_FILENAME, join_id=_CALL_ID),
    ])
    with pytest.raises(GateFailure, match="same-id"):
        assert_generated_by_tool(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH)


# ---------------------------------------------------------------------------
# OT-R3-04: a result whose join id does not match the call cannot complete a pairing
# ---------------------------------------------------------------------------

def test_mismatched_join_id_fails(schema, tmp_path):
    assert_generated_by_tool, GateFailure = _gate()
    t = _write(tmp_path / "t.jsonl", [
        schema.call(prompt=_prompt(), filename=_FILENAME, call_id=_CALL_ID),
        schema.result(filename=_FILENAME, join_id=_ALT_ID),   # belongs to nobody
    ])
    with pytest.raises(GateFailure, match="same-id"):
        assert_generated_by_tool(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH)


# ---------------------------------------------------------------------------
# OT-R3-05: two distinct calls both matching prompt+filename are ambiguous -> fail closed
# ---------------------------------------------------------------------------

def test_ambiguous_distinct_calls_fail(schema, tmp_path):
    assert_generated_by_tool, GateFailure = _gate()
    t = _write(tmp_path / "t.jsonl", [
        schema.call(prompt=_prompt(), filename=_FILENAME, call_id=_CALL_ID),
        schema.result(filename=_FILENAME, join_id=_CALL_ID),
        schema.call(prompt=_prompt(), filename=_FILENAME, call_id=_ALT_ID),
        schema.result(filename=_FILENAME, join_id=_ALT_ID),
    ])
    with pytest.raises(GateFailure, match="same-id"):
        assert_generated_by_tool(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH)


# ---------------------------------------------------------------------------
# OT-R3-06: two calls sharing ONE id are rejected by the duplicate-id guard
# ---------------------------------------------------------------------------

def test_duplicate_same_id_fails(schema, tmp_path):
    assert_generated_by_tool, GateFailure = _gate()
    t = _write(tmp_path / "t.jsonl", [
        schema.call(prompt=_prompt(), filename=_FILENAME, call_id=_CALL_ID),
        schema.call(prompt=_prompt(), filename=_FILENAME, call_id=_CALL_ID),
        schema.result(filename=_FILENAME, join_id=_CALL_ID),
    ])
    with pytest.raises(GateFailure, match="duplicate"):
        assert_generated_by_tool(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH)


# ---------------------------------------------------------------------------
# OT-R3-07: a text-only mention (no tool block) is never accepted as provenance
# ---------------------------------------------------------------------------

def test_text_only_mention_fails(schema, tmp_path):
    assert_generated_by_tool, GateFailure = _gate()
    t = _write(tmp_path / "t.jsonl", [
        schema.text_only_mention(filename=_FILENAME),
    ])
    with pytest.raises(GateFailure, match="same-id"):
        assert_generated_by_tool(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH)


# ---------------------------------------------------------------------------
# OT-R3-08: a fabricated success result with no matching call fails closed
# ---------------------------------------------------------------------------

def test_fabricated_result_without_call_fails(schema, tmp_path):
    assert_generated_by_tool, GateFailure = _gate()
    t = _write(tmp_path / "t.jsonl", [
        schema.result(filename=_FILENAME, join_id=_CALL_ID),   # no call anywhere
    ])
    with pytest.raises(GateFailure, match="same-id"):
        assert_generated_by_tool(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH)


# ---------------------------------------------------------------------------
# OT-R3-09: a call/result naming a DIFFERENT file does not vouch for the wanted one
# ---------------------------------------------------------------------------

def test_wrong_filename_fails(schema, tmp_path):
    assert_generated_by_tool, GateFailure = _gate()
    t = _write(tmp_path / "t.jsonl", [
        schema.call(prompt=_prompt(), filename="some_other_diagram.png", call_id=_CALL_ID),
        schema.result(filename="some_other_diagram.png", join_id=_CALL_ID),
    ])
    with pytest.raises(GateFailure, match="same-id"):
        assert_generated_by_tool(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH)


# ---------------------------------------------------------------------------
# OT-R3-10: malformed / non-dict arguments cannot be reconstructed -> fail closed
# ---------------------------------------------------------------------------

def test_malformed_args_fails(schema, tmp_path):
    assert_generated_by_tool, GateFailure = _gate()
    t = _write(tmp_path / "t.jsonl", [
        schema.call_malformed_args(call_id=_CALL_ID),
        schema.result(filename=_FILENAME, join_id=_CALL_ID),
    ])
    with pytest.raises(GateFailure, match="same-id"):
        assert_generated_by_tool(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH)


# ---------------------------------------------------------------------------
# OT-R3-11: native `filename` key variant is also accepted (gate reads both keys)
# ---------------------------------------------------------------------------

def test_native_filename_key_variant_passes(tmp_path):
    assert_generated_by_tool, _ = _gate()
    native = NativeSchema()
    t = _write(tmp_path / "t.jsonl", [
        native.call(prompt=_prompt(), filename=_FILENAME, call_id=_CALL_ID,
                    filename_key="filename"),
        native.result(filename=_FILENAME, join_id=_CALL_ID),
    ])
    result = assert_generated_by_tool(_FILENAME, transcript=t, prompt_file=_PROMPT_PATH)
    assert result["verified_filenames"] == [_FILENAME]
    assert result["tool_use_id"] == _CALL_ID
