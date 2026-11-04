#!/usr/bin/env python3
"""Report QC gates for a generated Biomni skill. Copy into the skill's scripts/ and CALL them.

A gate is wired only when a script calls it. Several shipped skills define validators that nothing
invokes — those are documentation, not gates. Call these from your export step.

    from report_qc import assert_generated_by_tool, assert_report_styled, staged_copy

Every deliverable path taken by this module is resolved against RESULTS when relative: the working
directory is /workspace but deliverables must land on the results mount, so a bare "report_facts.json"
written relative to the CWD is invisible to the report builder and to the user.

Stdlib only except assert_figure_ok, which needs Pillow or numpy (it degrades to a size check).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import zlib

from report_style import (
    StyleProviderError,
    resolve_provider,
    selected_style_from_transcript,
)

RESULTS = pathlib.Path(os.environ.get("BIOMNI_RESULTS", "/mnt/results"))
_TRANSCRIPT_REL = pathlib.Path("execution_trace") / "transcript.jsonl"   # resolved at call time
TRANSCRIPT = RESULTS / _TRANSCRIPT_REL
DEFAULT_REPORT_STYLE_PROVIDER = "pdf-report-generation"
RECEIPT_SCHEMA_V3 = "phylo-run-receipt/3"


class GateFailure(AssertionError):
    """Raised before any facts artifact is written, so a failing run produces no quotable numbers."""


def _at_results(path: str | pathlib.Path) -> pathlib.Path:
    """A relative path names the results mount, not the CWD. Absolute paths are honoured as given.

    Reads RESULTS at call time so a caller (or a test) can repoint the mount.
    """
    p = pathlib.Path(path)
    return p if p.is_absolute() else RESULTS / p


# --- the infographic honesty gate ----------------------------------------------------------------

def _record_text(line: str) -> str:
    """One transcript record flattened to searchable text — never more than one record.

    Transcript shape is not guaranteed, so a line that is not JSON is searched raw.
    """
    try:
        return json.dumps(json.loads(line), ensure_ascii=False)
    except ValueError:
        return line


# Anything that cannot appear inside a path. Backslash also separates JSON-escaped line breaks from
# a preceding filename, so a multi-line tool result cannot glue the basename to its next field.
_TOKEN_SPLIT_RE = re.compile(r"""[\s"',;:()\[\]{}<>|`=\\]+""")


def _basenames_in(record: str) -> set[str]:
    """Every path-like token in one record, reduced to its basename.

    Substring matching is not usable here: `surreal.png` contains `real.png`, so a success for the
    first would evidence a request for the second. Tokenise, then compare basenames exactly. Reading
    tokens rather than parsing one fixed success sentence means a reworded tool message yields no
    basenames and the caller raises — the failure is loud, never a silent pass.
    """
    out: set[str] = set()
    for tok in _TOKEN_SPLIT_RE.split(record):
        tok = tok.strip().rstrip(".,")
        if not tok:
            continue
        base = os.path.basename(tok.replace("\\", "/"))
        if base:
            out.add(base)
    return out


def _normalize_tool_call(call: object) -> dict | None:
    """Map one flattened tool-call entry onto the internal ``tool_use`` block.

    Two native call shapes are accepted and reduced to one representation:

    * platform-native ``{"id", "name", "args"}`` where ``args`` is a JSON-encoded
      *string* (this environment's ``assistant.tool_calls[]`` schema), and
    * OpenAI-style ``{"id", "function": {"name", "arguments"}}``.

    Fail-closed rules: an entry without a real string join id is dropped — a join id is
    never invented. ``args`` is JSON-decoded when it is a string; if decoding fails or
    yields a non-dict, the raw value is preserved so the downstream
    ``isinstance(input, dict)`` guard rejects it rather than guessing arguments.
    """
    if not isinstance(call, dict):
        return None
    call_id = call.get("id") or call.get("tool_call_id")
    name = call.get("name")
    args = call.get("args")
    function = call.get("function")
    if isinstance(function, dict):
        name = name or function.get("name")
        if args is None:
            args = function.get("arguments")
    if not isinstance(call_id, str) or not call_id:
        return None
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            pass  # leave raw; a non-dict input fails the downstream dict guard
    return {"type": "tool_use", "id": call_id, "name": name, "input": args}


def _normalize_tool_result(record: dict) -> dict | None:
    """Map one flattened ``type == "tool"`` record onto the internal ``tool_result`` block.

    The join id is the record's own ``tool_call_id`` (or ``tool_use_id`` / ``call_id``);
    a record without a real string join id is dropped so an unlinked success line can
    never complete a pairing. The result ``content`` is carried verbatim for the
    success-line and exact-basename checks — it is never paraphrased or reconstructed.
    """
    call_id = (record.get("tool_call_id") or record.get("tool_use_id")
               or record.get("call_id"))
    if not isinstance(call_id, str) or not call_id:
        return None
    block: dict = {"type": "tool_result", "tool_use_id": call_id,
                   "content": record.get("content")}
    if record.get("tool_name") is not None:
        block["name"] = record.get("tool_name")
    if record.get("id") is not None:
        block["id"] = record.get("id")
    return block


def _content_blocks(value) -> list[dict]:
    """Normalize every supported transcript schema into one immutable internal
    call/result representation — typed ``tool_use`` / ``tool_result`` blocks — before
    the fail-closed lineage rules in :func:`assert_generated_by_tool` run.

    Supported native platform schemas, all reduced to the same internal shape:

    * **Typed content blocks** — ``{"type": "tool_use", "id", "name", "input"}`` and
      ``{"type": "tool_result", "tool_use_id", ...}``. Already internal; passed through.
    * **Flattened message records** — an assistant message carrying a ``tool_calls``
      list (each entry ``{"id", "name", "args"}`` with ``args`` a JSON string, or the
      OpenAI-style ``{"id", "function": {"name", "arguments"}}``) plus its results as
      ``{"type": "tool", "tool_call_id", "tool_name", "content"}`` records. Each is
      normalized via :func:`_normalize_tool_call` / :func:`_normalize_tool_result`,
      reusing the transcript's own join ids.

    Normalization never invents a join id, never reconstructs a missing event, and never
    paraphrases arguments: an entry without a real id, or a result without a real join
    id, is dropped so the downstream same-id rules fail closed. Message objects whose
    ``content`` is itself a list/dict, and the task-messages ``{"type": "list",
    "data": [...]}`` envelope, are still traversed.
    """
    if isinstance(value, list):
        blocks: list[dict] = []
        for item in value:
            blocks.extend(_content_blocks(item))
        return blocks
    if not isinstance(value, dict):
        return []

    # (1) Already-internal typed blocks pass through unchanged.
    if value.get("type") in {"tool_use", "tool_result"}:
        return [value]

    normalized: list[dict] = []

    # (2) Flattened native call record: any message carrying a tool_calls list.
    tool_calls = value.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            block = _normalize_tool_call(call)
            if block is not None:
                normalized.append(block)

    # (3) Flattened native result record: type == "tool" with a real join id.
    if value.get("type") == "tool":
        block = _normalize_tool_result(value)
        if block is not None:
            normalized.append(block)

    if normalized:
        return normalized

    # (4) Nested content and the typed list envelope are traversed as before.
    content = value.get("content")
    if isinstance(content, (list, dict)):
        return _content_blocks(content)
    if value.get("type") == "list" and isinstance(value.get("data"), list):
        return _content_blocks(value["data"])
    return []


# --- OT-R4: three-state infographic-lineage outcome ----------------------------------------------
# repair-brief.md (r4): the fail-closed lineage rule required a complete exact prompt from the
# platform trace. Real execution traces truncate the GenerateImage tool-call args (the fixed prompt
# is ~1124 chars; the trace caps args near 600 chars and appends a literal "[truncated N chars]"
# marker), so json.loads(args) fails, the call is dropped by the dict guard, the gate returns
# "found 0", and infographics_generated_by_tool fails even though the image is honest.
#
# classify_infographic_lineage returns one of three states and NEVER synthesizes a missing field,
# reconstructs a prompt from a truncated string, copies a trace into a more trusted location, or
# treats not_evaluable as verified. Contradiction dominates incompleteness.
LINEAGE_VERIFIED = "verified"
LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE = "not_evaluable_platform_trace"
LINEAGE_FAILED = "failed"
LINEAGE_STATES = (LINEAGE_VERIFIED, LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE, LINEAGE_FAILED)

# The single disclosure substring every surface (facts, provenance, report) must carry when the
# state is not `verified`, and must NOT carry when it is `verified`.
LINEAGE_DISCLOSURE_MARKER = "provenance is not independently verified"

# Positive platform-truncation signal on a raw (non-dict) GenerateImage args payload.
_TRUNCATION_MARKER_RE = re.compile(r"\[truncated\b", re.IGNORECASE)


def assert_generated_by_tool(*filenames: str, transcript: pathlib.Path | None = None,
                             prompt_file: str | pathlib.Path | None = None,
                             image_file: str | pathlib.Path | None = None) -> dict:
    """Fail unless each schematic was really produced by the Biomni GenerateImage tool.

    Why this exists: a box-and-arrow diagram drawn with matplotlib looks like an infographic and
    passes a visual check. The only in-run evidence that the tool actually ran is its own success
    line in the session transcript, and it must be a line naming *that* file — a success elsewhere
    in the transcript is evidence for the file it names and nothing else. Note that GenerateImage
    strips directory components and always writes to the results root, so compare basenames only.

    A successful lineage is one typed GenerateImage invocation and its own typed
    tool result, joined by the exact tool-use id.  The invocation must contain the
    exact package prompt and requested filename; the linked successful result must
    return that same filename. Prompt text or a success filename elsewhere in the
    transcript is never combined into a synthetic pairing.

    A relative `transcript` is resolved against RESULTS.

    Returns a dict with pairing evidence: ``prompt_sha256``, ``image_sha256``,
    ``tool_use_found`` (bool), and ``verified_filenames`` (list).
    """
    path = _at_results(transcript if transcript is not None else _TRANSCRIPT_REL)
    if not path.exists():
        raise GateFailure(
            f"cannot verify infographic provenance: {path} is missing. "
            "Do not claim a GenerateImage infographic you cannot evidence."
        )

    # --- OT-R2-02: derive prompt hash and search transcript for exact prompt text ---
    prompt_sha256: str | None = None
    prompt_text: str | None = None
    if prompt_file is None:
        raise GateFailure("prompt_file is mandatory for exact GenerateImage lineage")
    prompt_path = pathlib.Path(prompt_file)
    if not prompt_path.is_absolute():
        prompt_path = SKILL_ROOT / prompt_file
    if not prompt_path.exists():
        raise GateFailure(
            f"infographic prompt file not found at {prompt_path}. "
            "The prompt file is the single source for GenerateImage and must exist."
        )
    prompt_text = prompt_path.read_text(encoding="utf-8").strip()
    prompt_sha256 = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()

    raw_transcript = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[dict] = []
    try:
        # A task-messages API response is one (often pretty-printed) JSON document.
        blocks.extend(_content_blocks(json.loads(raw_transcript)))
    except ValueError:
        # The mounted execution trace is JSONL: one complete record per line.
        for line in raw_transcript.splitlines():
            if not line.strip():
                continue
            try:
                blocks.extend(_content_blocks(json.loads(line)))
            except ValueError:
                continue

    calls: dict[str, dict] = {}
    results: dict[str, list[dict]] = {}
    for block in blocks:
        if block.get("type") == "tool_use" and block.get("name") == "GenerateImage":
            call_id = block.get("id")
            call_input = block.get("input")
            if isinstance(call_id, str) and isinstance(call_input, dict):
                if call_id in calls:
                    raise GateFailure(
                        f"duplicate GenerateImage tool-call id {call_id!r}; "
                        "an ambiguous call cannot evidence a single lineage"
                    )
                calls[call_id] = block
        elif block.get("type") == "tool_result":
            linked_id = block.get("tool_use_id")
            if isinstance(linked_id, str):
                results.setdefault(linked_id, []).append(block)

    wanted = [os.path.basename(fn) for fn in filenames]
    pairings: list[dict] = []
    for filename in wanted:
        candidates: list[dict] = []
        for call_id, call in calls.items():
            call_input = call["input"]
            actual_prompt = call_input.get("prompt")
            requested = os.path.basename(str(
                call_input.get("file_name") or call_input.get("filename") or ""
            ))
            if actual_prompt != prompt_text or requested != filename:
                continue
            for result in results.get(call_id, []):
                result_text = _record_text(json.dumps(result, ensure_ascii=False))
                if "generated successfully" not in result_text.lower():
                    continue
                returned = _basenames_in(result_text) & {filename}
                if returned:
                    candidates.append({
                        "tool_use_id": call_id,
                        "result_id": result.get("id") or result.get("tool_use_id"),
                        "requested_filename": filename,
                        "returned_filename": filename,
                    })
        if len(candidates) != 1:
            raise GateFailure(
                f"need exactly one same-id GenerateImage call/result pair for {filename}; "
                f"found {len(candidates)}"
            )
        pairings.append(candidates[0])

    # OT-R2-02: derive image hash if image_file is provided
    image_sha256: str | None = None
    if image_file is not None:
        img_path = _at_results(image_file)
        if not img_path.exists():
            raise GateFailure(
                f"infographic image file not found at {img_path}. "
                "The delivered image must exist for content-identity verification."
            )
        image_sha256 = _sha256(img_path)

    return {
        "prompt_sha256": prompt_sha256,
        "image_sha256": image_sha256,
        "tool_use_id": pairings[0]["tool_use_id"] if len(pairings) == 1 else None,
        "result_id": pairings[0]["result_id"] if len(pairings) == 1 else None,
        "tool_use_found": True,
        "verified_filenames": wanted,
        "pairings": pairings,
    }


# --- OT-R4: id-inclusive event scans (a genuine event may be present yet unjoinable) -------------

def _gi_call_from_entry(call: object) -> dict | None:
    """Extract a GenerateImage tool-call from one native/OpenAI ``tool_calls[]`` entry.

    Unlike :func:`_normalize_tool_call`, the id is *kept even when absent* so the tri-state
    classifier can recognize a genuine-but-unjoinable (id-less) GenerateImage event. ``args`` is
    JSON-decoded only when it is a string; a non-dict decode result is preserved as raw so the raw
    string can be inspected for a platform-truncation signal (it is never treated as a prompt).
    """
    if not isinstance(call, dict):
        return None
    name = call.get("name")
    args = call.get("args")
    function = call.get("function")
    if isinstance(function, dict):
        name = name or function.get("name")
        if args is None:
            args = function.get("arguments")
    if name != "GenerateImage":
        return None
    call_id = call.get("id") or call.get("tool_call_id")
    parsed: dict | None = None
    if isinstance(args, dict):
        parsed = args
    elif isinstance(args, str):
        try:
            decoded = json.loads(args)
        except ValueError:
            decoded = None
        if isinstance(decoded, dict):
            parsed = decoded
    return {"id": call_id if isinstance(call_id, str) and call_id else None,
            "raw_args": args, "input": parsed, "raw_is_string": isinstance(args, str)}


def _iter_generateimage_calls(value: object) -> list[dict]:
    """Every GenerateImage call in one transcript value, id-less ones INCLUDED (typed + native)."""
    out: list[dict] = []

    def walk(v: object) -> None:
        if isinstance(v, list):
            for item in v:
                walk(item)
            return
        if not isinstance(v, dict):
            return
        if v.get("type") == "tool_use" and v.get("name") == "GenerateImage":
            inp = v.get("input")
            cid = v.get("id")
            out.append({"id": cid if isinstance(cid, str) and cid else None,
                        "raw_args": inp, "input": inp if isinstance(inp, dict) else None,
                        "raw_is_string": isinstance(inp, str)})
        tool_calls = v.get("tool_calls")
        if isinstance(tool_calls, list):
            for call in tool_calls:
                got = _gi_call_from_entry(call)
                if got is not None:
                    out.append(got)
        content = v.get("content")
        if isinstance(content, (list, dict)):
            walk(content)
        if v.get("type") == "list" and isinstance(v.get("data"), list):
            walk(v["data"])

    walk(value)
    return out


def _iter_tool_results(value: object) -> list[tuple[str | None, object]]:
    """(join_id_or_None, content) for every typed ``tool_result`` and native ``type: tool`` record."""
    out: list[tuple[str | None, object]] = []

    def walk(v: object) -> None:
        if isinstance(v, list):
            for item in v:
                walk(item)
            return
        if not isinstance(v, dict):
            return
        if v.get("type") == "tool_result":
            jid = v.get("tool_use_id")
            out.append((jid if isinstance(jid, str) and jid else None, v.get("content")))
        if v.get("type") == "tool":
            jid = v.get("tool_call_id") or v.get("tool_use_id") or v.get("call_id")
            out.append((jid if isinstance(jid, str) and jid else None, v.get("content")))
        content = v.get("content")
        if isinstance(content, (list, dict)):
            walk(content)
        if v.get("type") == "list" and isinstance(v.get("data"), list):
            walk(v["data"])

    walk(value)
    return out


def _result_text(content: object) -> str:
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def _image_hash_or_none(image_file: str | pathlib.Path | None) -> str | None:
    if image_file is None:
        return None
    p = _at_results(image_file)
    if not p.is_file():
        return None
    return _sha256(p)


def _lineage(state: str, reason: str, evidence: dict) -> dict:
    ev = dict(evidence)
    ev["state"] = state
    ev["reason"] = reason
    return {"state": state, "reason": reason, "evidence": ev}


def _classify_one(filename: str, prompt_text: str, blocks: list[dict], gi_calls: list[dict],
                  results: list[tuple[str | None, object]], image_file, transcript_trusted: bool) -> dict:
    """Classify lineage for one filename. Contradiction (failed) dominates incompleteness."""
    # Strict same-id pairing over normalized blocks (mirrors assert_generated_by_tool exactly).
    calls_by_id: dict[str, dict] = {}
    dup_ids: set[str] = set()
    for block in blocks:
        if block.get("type") == "tool_use" and block.get("name") == "GenerateImage":
            cid = block.get("id")
            inp = block.get("input")
            if isinstance(cid, str) and isinstance(inp, dict):
                if cid in calls_by_id:
                    dup_ids.add(cid)
                calls_by_id[cid] = block
    if dup_ids:
        return {"state": LINEAGE_FAILED,
                "reason": f"duplicate GenerateImage tool-call id(s) {sorted(dup_ids)!r}; ambiguous lineage",
                "evidence": {"duplicate_ids": sorted(dup_ids)}}
    results_by_id: dict[str, list[dict]] = {}
    for block in blocks:
        if block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
            results_by_id.setdefault(block["tool_use_id"], []).append(block)

    verified_candidates: list[dict] = []
    prompt_present_wrong = False   # paraphrased: complete prompt for this file that differs
    filename_mismatch = False      # call/result filenames disagree with the wanted file
    for cid, call in calls_by_id.items():
        inp = call["input"]
        actual_prompt = inp.get("prompt")
        requested = os.path.basename(str(inp.get("file_name") or inp.get("filename") or ""))
        if requested == filename and isinstance(actual_prompt, str) and actual_prompt != prompt_text:
            prompt_present_wrong = True
        if actual_prompt == prompt_text and requested and requested != filename:
            filename_mismatch = True
        if actual_prompt != prompt_text or requested != filename:
            continue
        for result in results_by_id.get(cid, []):
            rtext = _record_text(json.dumps(result, ensure_ascii=False))
            if "generated successfully" not in rtext.lower():
                continue
            if _basenames_in(rtext) & {filename}:
                verified_candidates.append({"tool_use_id": cid,
                                            "result_id": result.get("id") or result.get("tool_use_id")})
            else:
                filename_mismatch = True   # same-id success result returns a different file

    if len(verified_candidates) == 1:
        if not transcript_trusted:
            return {"state": LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE,
                    "reason": ("exact same-id pairing found, but the transcript is an agent-writable / "
                               "results-local copy, not the immutable execution trace; provenance is not "
                               "independently verified"),
                    "evidence": {"tool_use_id": verified_candidates[0]["tool_use_id"],
                                 "source_trusted": False, "prompt_verifiable": True}}
        img_hash = _image_hash_or_none(image_file)
        if img_hash is None:
            return {"state": LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE,
                    "reason": ("exact same-id pairing found, but the delivered image has no computable "
                               "output hash (hashless); output identity is not verified"),
                    "evidence": {"tool_use_id": verified_candidates[0]["tool_use_id"],
                                 "image_sha256": None, "prompt_verifiable": True}}
        return {"state": LINEAGE_VERIFIED,
                "reason": ("complete same-id GenerateImage call/result with the exact package prompt, "
                           "requested==returned filename, and a computable delivered-image hash"),
                "evidence": {"tool_use_id": verified_candidates[0]["tool_use_id"],
                             "result_id": verified_candidates[0]["result_id"],
                             "image_sha256": img_hash, "requested_filename": filename,
                             "returned_filename": filename, "prompt_verifiable": True}}
    if len(verified_candidates) > 1:
        return {"state": LINEAGE_FAILED,
                "reason": f"{len(verified_candidates)} distinct same-id exact pairings for {filename}; ambiguous",
                "evidence": {"pairings": len(verified_candidates)}}

    # No verified pairing. Contradictions first (failed), then incompleteness (not_evaluable).
    if prompt_present_wrong:
        return {"state": LINEAGE_FAILED,
                "reason": (f"a GenerateImage call for {filename} carries a complete prompt that does not "
                           "match the package prompt (paraphrased); the prompt is never reconstructed"),
                "evidence": {"paraphrased": True, "prompt_verifiable": True}}
    if filename_mismatch:
        return {"state": LINEAGE_FAILED,
                "reason": f"GenerateImage call/result filenames do not agree with {filename} (mismatched)",
                "evidence": {"filename_mismatch": True}}

    gi_present = len(gi_calls) > 0
    truncated = False
    idless = False
    for call in gi_calls:
        if call["input"] is None and call["raw_is_string"]:
            raw = call["raw_args"] or ""
            if _TRUNCATION_MARKER_RE.search(raw) or raw.lstrip().startswith('{"'):
                truncated = True
        if call["id"] is None:
            idless = True

    success_for_file = False
    for _jid, content in results:
        rtext = _result_text(content)
        if "generated successfully" in rtext.lower() and filename in _basenames_in(rtext):
            success_for_file = True
            break

    if gi_present and truncated:
        return {"state": LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE,
                "reason": ("a genuine GenerateImage call is present but its arguments are truncated in the "
                           "platform trace (truncation marker / cut JSON prefix), so the exact prompt cannot "
                           "be verified; the truncated string is never mapped to a prompt and provenance is "
                           "not independently verified"),
                "evidence": {"truncated": True, "prompt_verifiable": False, "prompt_sha256": None}}
    if gi_present and idless:
        return {"state": LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE,
                "reason": ("a genuine GenerateImage call is present but carries no tool-use id, so it cannot "
                           "be joined to its result; provenance is not independently verified"),
                "evidence": {"id_less": True, "prompt_verifiable": False}}
    if success_for_file and not gi_present:
        return {"state": LINEAGE_FAILED,
                "reason": (f"a GenerateImage success result names {filename} but no GenerateImage call exists "
                           "anywhere in the trace (fabricated result)"),
                "evidence": {"fabricated_result": True}}
    if gi_present:
        return {"state": LINEAGE_FAILED,
                "reason": (f"GenerateImage call(s) exist but none provide an interpretable exact match for "
                           f"{filename} and no platform-truncation or id-less signal applies "
                           "(mismatched / uninterpretable evidence)"),
                "evidence": {"gi_calls": len(gi_calls)}}
    return {"state": LINEAGE_FAILED,
            "reason": f"no GenerateImage tool call or result evidences {filename}",
            "evidence": {"gi_calls": 0}}


def classify_infographic_lineage(*filenames: str, transcript: pathlib.Path | None = None,
                                 prompt_file: str | pathlib.Path | None = None,
                                 image_file: str | pathlib.Path | None = None,
                                 transcript_trusted: bool = True,
                                 platform_event_attested: bool = False) -> dict:
    """Three-state infographic-lineage outcome (OT-R4). Never raises on evidence *shape*.

    Returns ``{"state", "reason", "evidence"}`` with ``state`` in :data:`LINEAGE_STATES`:

    * ``verified`` — a complete, exact, immutable same-id call/result binding with the exact package
      prompt, ``requested == returned`` filename, and a computable delivered-image hash, read from the
      immutable execution trace.
    * ``not_evaluable_platform_trace`` — a genuine GenerateImage event is present (or attested by the
      native execution record) but a required proof field is absent, truncated, id-less,
      agent-writable/results-local, or lacks an output hash, and there is **no contradiction**.
    * ``failed`` — contradictory, fabricated, reconstructed, paraphrased, duplicate, mismatched, or
      otherwise invalid evidence.

    The exact prompt is never inferred from a truncated string, no field is synthesized, a trace is
    never copied into a more trusted location, and not_evaluable is never returned as verified.
    """
    wanted = [os.path.basename(f) for f in filenames]
    base_ev = {"verified_filenames": wanted, "transcript_trusted": bool(transcript_trusted)}
    if prompt_file is None:
        return _lineage(LINEAGE_FAILED, "prompt_file is mandatory for lineage classification", base_ev)
    prompt_path = pathlib.Path(prompt_file)
    if not prompt_path.is_absolute():
        prompt_path = SKILL_ROOT / prompt_file
    if not prompt_path.exists():
        return _lineage(LINEAGE_FAILED, f"infographic prompt file not found at {prompt_path}", base_ev)
    prompt_text = prompt_path.read_text(encoding="utf-8").strip()
    base_ev["prompt_sha256"] = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()

    path = _at_results(transcript if transcript is not None else _TRANSCRIPT_REL)
    if not path.exists():
        if platform_event_attested:
            return _lineage(LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE,
                            ("transcript unavailable, but a platform GenerateImage event is attested by the "
                             "native execution record; provenance is not independently verified"), base_ev)
        return _lineage(LINEAGE_FAILED, f"transcript missing at {path}; cannot evidence provenance", base_ev)

    raw = path.read_text(encoding="utf-8", errors="replace")
    docs: list[object] = []
    try:
        docs.append(json.loads(raw))
    except ValueError:
        for line in raw.splitlines():
            if line.strip():
                try:
                    docs.append(json.loads(line))
                except ValueError:
                    continue
    blocks: list[dict] = []
    gi_calls: list[dict] = []
    results: list[tuple[str | None, object]] = []
    for doc in docs:
        blocks.extend(_content_blocks(doc))
        gi_calls.extend(_iter_generateimage_calls(doc))
        results.extend(_iter_tool_results(doc))

    per_file = [_classify_one(name, prompt_text, blocks, gi_calls, results, image_file,
                              transcript_trusted) for name in wanted]
    if not per_file:
        return _lineage(LINEAGE_FAILED, "no infographic filename was requested", base_ev)
    order = {LINEAGE_FAILED: 0, LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE: 1, LINEAGE_VERIFIED: 2}
    chosen = min(per_file, key=lambda p: order[p["state"]])
    ev = dict(base_ev)
    ev.update(chosen["evidence"])
    ev["per_file"] = per_file
    return _lineage(chosen["state"], chosen["reason"], ev)


# --- figure sanity -------------------------------------------------------------------------------

def assert_report_exists(report_name: str, min_bytes: int = 20_000) -> pathlib.Path:
    """Fail unless the report was actually produced, at the results root, under its declared name.

    Why this exists: the platform's own guidance tells an agent not to create file deliverables for
    simple queries or single analyses. A skill that declares a mandatory report is therefore asking
    for something the agent may reasonably decide to skip — and without this assertion the run then
    finishes *successfully* with no deliverable and nobody notices.

    This does not stop an agent skipping the step. It stops the run quietly succeeding when it did.
    Call it as the last thing the skill does.
    """
    base = os.path.basename(report_name)
    path = RESULTS / base
    if not path.exists():
        raise GateFailure(
            f"the run is not complete: {base} was not produced at {RESULTS}/. "
            "The report is a required deliverable of this skill, not an optional extra. "
            "Generate it before finishing, or report plainly that the run failed and why."
        )
    size = path.stat().st_size
    if size < min_bytes:
        raise GateFailure(
            f"{base} is only {size} B — a report of a multi-step analysis that small is a "
            "rendering failure, not a concise summary. Check the build for a silent error."
        )
    return path


OPEN_TARGETS_REPORT_SECTION_ORDER = (
    "Task Context",
    "Methods and Sources",
    "Results",
    "Conclusions",
    "Scientific Caveats",
    "References",
    "Suggested Next Steps",
)


def assert_report_content_contract(
    report_path: str | pathlib.Path,
    facts_path: str | pathlib.Path,
    provenance_path: str | pathlib.Path,
) -> dict:
    """Require a substantive, facts-bound Open Targets narrative report."""
    pdf = _at_results(report_path)
    facts_file = _at_results(facts_path)
    provenance_file = _at_results(provenance_path)
    for artifact in (pdf, facts_file, provenance_file):
        if not artifact.is_file():
            raise GateFailure(f"report content contract input is missing: {artifact}")
    try:
        import pypdf  # type: ignore
        reader = pypdf.PdfReader(str(pdf))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        facts = json.loads(facts_file.read_text(encoding="utf-8"))
        provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
    except (ImportError, OSError, ValueError, TypeError) as exc:
        raise GateFailure(f"could not evaluate report narrative: {type(exc).__name__}: {exc}") from exc

    normalized = re.sub(r"\s+", " ", text).strip()
    positions = []
    for heading in OPEN_TARGETS_REPORT_SECTION_ORDER:
        position = normalized.find(heading)
        if position < 0:
            raise GateFailure(f"required narrative section is absent: {heading}")
        positions.append(position)
    if positions != sorted(positions) or len(set(positions)) != len(positions):
        raise GateFailure("required narrative sections are not present in the declared order")

    minimum_chars = {
        "Task Context": 300,
        "Methods and Sources": 500,
        "Results": 700,
        "Conclusions": 350,
        "Scientific Caveats": 400,
        "References": 180,
        "Suggested Next Steps": 250,
    }
    section_lengths = {}
    for index, heading in enumerate(OPEN_TARGETS_REPORT_SECTION_ORDER):
        start = positions[index] + len(heading)
        end = positions[index + 1] if index + 1 < len(positions) else len(normalized)
        length = len(normalized[start:end].strip())
        section_lengths[heading] = length
        if length < minimum_chars[heading]:
            raise GateFailure(
                f"narrative section {heading!r} is too sparse: {length} characters; "
                f"minimum {minimum_chars[heading]}"
            )

    if facts.get("disease_id") != provenance.get("disease", {}).get("id"):
        raise GateFailure("report facts and provenance disagree on disease identity")
    witnesses = {
        "disease id": facts.get("disease_id"),
        "disease name": facts.get("disease_name"),
        "release": facts.get("release_label"),
        "top target": (facts.get("top_targets") or [{}])[0].get("approved_symbol"),
        "bounded evidence": "bounded",
        "non-causal interpretation": "causal",
    }
    lower = normalized.lower()
    for label, witness in witnesses.items():
        if witness is None or str(witness).lower() not in lower:
            raise GateFailure(f"report narrative lacks {label} witness: {witness!r}")
    numbers_text = normalized.replace(",", "")
    for label, value in (
        ("retrieved target denominator", len(facts.get("top_targets") or [])),
        ("total associated targets", facts.get("total_associated_targets")),
        ("top-target evidence total", facts.get("evidence_count")),
    ):
        if value is None or str(value) not in numbers_text:
            raise GateFailure(f"report narrative lacks facts-bound {label}: {value}")
    for forbidden in ("proves causality", "causal target identified", "clinically validated"):
        if forbidden in lower:
            raise GateFailure(f"report contains unsupported interpretation: {forbidden!r}")
    return {
        "report": _ev_file(pdf),
        "page_count": len(reader.pages),
        "required_sections": list(OPEN_TARGETS_REPORT_SECTION_ORDER),
        "section_characters": section_lengths,
        "facts": _ev_file(facts_file),
        "provenance": _ev_file(provenance_file),
        "method": "PDF text extraction plus ordered-section and facts-witness validation",
    }


def assert_figures(manifest: str | pathlib.Path | list[dict]) -> list[dict]:
    """Every declared figure must exist and be non-blank. Returns the manifest for the facts file.

    Call this BEFORE writing report_facts.json, so a run that produced a blank figure stops instead
    of shipping a report that points at it. The returned list is what the report should read its
    figure inventory from — a report that lists figures by reading this cannot claim one it never
    produced, or quietly drop one it did.

    A manifest entry is {"step": 2, "file": "figures/figure_2_qc.png", "caption": "..."}. The
    manifest path and every relative entry `file` are resolved against RESULTS.
    """
    if isinstance(manifest, (str, pathlib.Path)):
        p = _at_results(manifest)
        if not p.exists():
            raise GateFailure(
                f"figure manifest {p} is missing. Declare one entry per analysis step; if a step "
                "genuinely has nothing to plot, record it with \"file\": null and a reason."
            )
        entries = json.loads(p.read_text(encoding="utf-8"))
    else:
        entries = manifest

    if not isinstance(entries, list) or not entries:
        raise GateFailure("figure manifest is empty — an analysis with no figure shows the reader nothing")

    checked = []
    for e in entries:
        fn = e.get("file")
        if fn is None:                       # an explicit, reasoned absence is allowed
            if not e.get("reason"):
                raise GateFailure(f"step {e.get('step')!r} declares no figure and gives no reason")
            checked.append(e)
            continue
        assert_figure_ok(fn)
        if not str(e.get("caption", "")).strip():
            raise GateFailure(
                f"{fn} has no caption. State what the figure shows, not what it is called."
            )
        checked.append(e)
    return checked


def report_embeds_figures(pdf_path: str | pathlib.Path, figures: list[dict]) -> tuple[bool, str]:
    """Soft check: does the built report actually contain the declared figures?

    Returns (ok, detail) rather than raising — this is the deliberately soft half of the figure rule.
    Degrades to 'not evaluable' when no PDF library is present, and 'not evaluable' is never a pass.
    A relative `pdf_path` is resolved against RESULTS, where the report has to sit.
    """
    expected = [e for e in figures if e.get("file")]
    if not expected:
        return True, "no figures declared"
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(str(_at_results(pdf_path)))
        n = sum(len(getattr(page, "images", []) or []) for page in reader.pages)
    except Exception as exc:                 # noqa: BLE001 - try the pinned system extractor
        executable = shutil.which("pdfimages")
        if executable is None:
            return False, f"NOT-EVALUABLE: could not count embedded images ({type(exc).__name__})"
        try:
            listed = subprocess.run(
                [executable, "-list", str(_at_results(pdf_path))], check=False,
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as fallback_exc:
            return False, ("NOT-EVALUABLE: both PDF image counters failed "
                           f"({type(fallback_exc).__name__})")
        if listed.returncode != 0:
            return False, "NOT-EVALUABLE: pdfimages could not inspect the PDF"
        n = sum(1 for line in listed.stdout.splitlines()
                if re.match(r"^\s*\d+\s+\d+\s+", line))
    if n < len(expected):
        return False, f"report embeds {n} image(s) but {len(expected)} figure(s) were declared"
    return True, f"report embeds {n} image(s) for {len(expected)} declared figure(s)"


def _pixel_sha256(path: pathlib.Path) -> str:
    """Hash decoded RGB pixels; container encoding and metadata are intentionally ignored."""
    try:
        from PIL import Image as PILImage  # type: ignore
    except ImportError as exc:
        raise GateFailure("Pillow is unavailable for decoded-pixel identity") from exc
    with PILImage.open(path) as image:
        return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()


def inspect_infographic_embedding(pdf_path: str | pathlib.Path,
                                  infographic_path: str | pathlib.Path) -> dict:
    """Return typed, hash-bound evidence for the exact infographic embedded in a PDF."""
    pdf = _at_results(pdf_path)
    info = _at_results(infographic_path)
    if not pdf.exists():
        return {"state": "fail", "detail": f"PDF not found at {pdf}"}
    if not info.exists():
        return {"state": "fail", "detail": f"infographic not found at {info}"}

    target_hash = _sha256(info)
    pdf_hash = _sha256(pdf)
    try:
        target_pixel_hash = _pixel_sha256(info)
    except (GateFailure, OSError, ValueError) as exc:
        return {"state": "not_evaluable", "detail": str(exc),
                "pdf_sha256": pdf_hash, "image_sha256": target_hash}

    extracted: list[tuple[int, pathlib.Path]] = []
    temp_dir: tempfile.TemporaryDirectory[str] | None = None

    # Prefer PyMuPDF when present because it exposes the source page directly.
    try:
        import fitz  # type: ignore
        doc = fitz.open(str(pdf))
        temp_dir = tempfile.TemporaryDirectory(prefix="phylo-pdf-images-")
        extraction_root = pathlib.Path(temp_dir.name)
        for page_number, page in enumerate(doc, 1):
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                try:
                    base_image = doc.extract_image(xref)
                    img_bytes = base_image.get("image", b"")
                    if img_bytes:
                        suffix = "." + str(base_image.get("ext") or "bin")
                        image_path = extraction_root / f"page-{page_number}-xref-{xref}{suffix}"
                        image_path.write_bytes(img_bytes)
                        extracted.append((page_number, image_path))
                except Exception:
                    continue
        doc.close()
    except ImportError:
        pass

    # Reproducible fallback for the controller environment: Poppler's pdfimages.
    if not extracted:
        executable = shutil.which("pdfimages")
        if executable:
            temp_dir = tempfile.TemporaryDirectory(prefix="phylo-pdf-images-")
            extraction_root = pathlib.Path(temp_dir.name)
            prefix = extraction_root / "image"
            listed = subprocess.run(
                [executable, "-list", str(pdf)], check=False,
                capture_output=True, text=True, timeout=30,
            )
            rendered = subprocess.run(
                [executable, "-png", str(pdf), str(prefix)], check=False,
                capture_output=True, text=True, timeout=30,
            )
            if listed.returncode == 0 and rendered.returncode == 0:
                pages = []
                for line in listed.stdout.splitlines():
                    match = re.match(r"^\s*(\d+)\s+\d+\s+", line)
                    if match:
                        pages.append(int(match.group(1)))
                files = sorted(extraction_root.glob("image-*.png"))
                if len(files) == len(pages):
                    extracted = list(zip(pages, files))

    if not extracted:
        if temp_dir is not None:
            temp_dir.cleanup()
        return {
            "state": "not_evaluable",
            "detail": "no working PDF image extractor with page mapping",
            "pdf_sha256": pdf_hash,
            "image_sha256": target_hash,
            "image_pixel_sha256": target_pixel_hash,
        }

    try:
        for page_number, embedded_path in extracted:
            try:
                embedded_pixel_hash = _pixel_sha256(embedded_path)
            except (GateFailure, OSError, ValueError):
                continue
            if embedded_pixel_hash == target_pixel_hash:
                return {
                    "state": "pass",
                    "detail": "decoded-pixel identity matched an embedded PDF image",
                    "pdf_sha256": pdf_hash,
                    "image_sha256": target_hash,
                    "image_pixel_sha256": target_pixel_hash,
                    "embedded_page": page_number,
                    "embedded_pixel_sha256": embedded_pixel_hash,
                    "extracted_images_checked": len(extracted),
                }
        return {
            "state": "fail",
            "detail": "delivered infographic pixel identity not found in the PDF",
            "pdf_sha256": pdf_hash,
            "image_sha256": target_hash,
            "image_pixel_sha256": target_pixel_hash,
            "extracted_images_checked": len(extracted),
        }
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def report_embeds_infographic(pdf_path: str | pathlib.Path,
                              infographic_path: str | pathlib.Path) -> tuple[bool, str]:
    """Compatibility wrapper around the typed embedding evidence."""
    evidence = inspect_infographic_embedding(pdf_path, infographic_path)
    prefix = "NOT-EVALUABLE: " if evidence["state"] == "not_evaluable" else ""
    return evidence["state"] == "pass", prefix + str(evidence.get("detail", ""))


def assert_figure_ok(path: str | pathlib.Path, min_bytes: int = 5_000) -> None:
    """Fail on a blank or degenerate figure. A blank plot passing QC is a gate that cannot fail.

    A relative path is resolved against RESULTS.
    """
    p = _at_results(path)
    if not p.exists():
        raise GateFailure(f"figure not written: {p}")
    size = p.stat().st_size
    if size < min_bytes:
        raise GateFailure(f"figure {p.name} is {size} B — almost certainly blank")

    if p.suffix.lower() == ".svg":
        return
    try:
        from PIL import Image  # type: ignore

        with Image.open(p) as im:
            extrema = im.convert("L").getextrema()
        if extrema[0] == extrema[1]:
            raise GateFailure(f"figure {p.name} has a single pixel value — it is blank")
    except ImportError:
        pass  # size check already ran; do not install anything into a live session


# --- legacy Phylo-only helpers (private; retained for historical receipt compatibility) ----------

_DELEGATE = "pdf-report-generation"
_ACCENT = "PHYLO_GOLD"
# The FUNCTIONAL half of the palette, by name. The chart accents (PHYLO_BLUE, PHYLO_GREEN, PHYLO_LIME,
# PHYLO_ORANGE, PHYLO_PINK, PHYLO_BLACK) are left out deliberately, not by oversight: the unbranded
# report's embedded matplotlib figures paint PHYLO_BLUE and PHYLO_GREEN, so counting a chart accent
# would pass the very artifact this gate exists to reject.
#
# By NAME rather than by parsing the palette's `# Derived / functional` section comment. A section
# parse picks up a renamed colour for free, but it fails OPEN — reorder the palette so a chart accent
# lands under that comment and the trap above re-opens silently. A name list can only ever go narrow,
# and going narrow is caught by the two-marker floor in the legacy gate. The cost is that a
# functional colour added to the palette has to be added here too, or it simply is not looked for.
_FUNCTIONAL = (_ACCENT, "HEADING_COLOR", "BODY_TEXT", "MUTED_TEXT", "TABLE_ALT_ROW", "TABLE_BORDER",
               "CALLOUT_BG", "DIVIDER_COLOR", "LINK_COLOR")
# Values still come from the file, so the palette stays the single source: move #111111 there and the
# gate demands the new value. Only direct HexColor literals — the alias forms (DIVIDER_COLOR =
# PHYLO_GOLD, CALLOUT_BG = PHYLO_OFF_WHITE) contribute nothing today, and following a name to a name
# could land on a chart accent. They are listed so a literal value would be read.
_HEXDEF = r'(?<!\w){}\s*=\s*(?:colors\.)?HexColor\(\s*["\']#([0-9A-Fa-f]{{6}})["\']\s*\)'
# Colours that appear in branded and unbranded reports alike, so hitting one proves nothing. White is
# TABLE_HEADER_FG; black is here for the same reason and not because the palette names it today — the
# real unbranded report sets #000000 24 times, so the day a functional name is spelled as black rather
# than #111111, that report would score a marker it did not earn.
_NO_SIGNAL = ("#FFFFFF", "#000000")


def _palette_file(palette: str | pathlib.Path | None = None) -> pathlib.Path:
    """Where the brand palette lives at run time. Env var first, so a moved skills mount still works."""
    if palette is not None:
        return pathlib.Path(palette)
    root = os.environ.get("BIOMNI_SKILLS_ROOT") or "/mnt/skills/system"
    return pathlib.Path(root) / _DELEGATE / "SKILL.md"


def _rgb(hexv: str) -> tuple[int, int, int]:
    return int(hexv[1:3], 16), int(hexv[3:5], 16), int(hexv[5:7], 16)


def _legacy_brand_markers(palette: str | pathlib.Path | None = None) -> dict[str, str]:
    """The functional palette colours this gate looks for, as {NAME: "#RRGGBB"}, accent first.

    Deliberately not pinned in this file: a hex literal here is a second copy of the palette, and it
    goes stale silently the day the brand moves. The delegate skill is the single source.

    A name defined twice with two different values is dropped rather than guessed at. For the accent
    that ambiguity is fatal, because the accent is mandatory; for the others it just costs a marker,
    and the floor in the legacy gate catches the case where it costs the last one.
    """
    p = _palette_file(palette)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise GateFailure(
            f"cannot verify the report's branding: no palette at {p} ({type(exc).__name__}). "
            "An unverifiable brand is not a verified one. Set BIOMNI_SKILLS_ROOT or check that the "
            f"`{_DELEGATE}` skill is mounted; do not report the branding as checked."
        ) from exc

    def defined(name: str) -> set[str]:
        return {"#" + m.group(1).upper() for m in re.finditer(_HEXDEF.format(name), text)}

    found = defined(_ACCENT)
    if len(found) != 1:                      # 0 = palette changed shape; >1 = which one is the brand?
        raise GateFailure(
            f"cannot verify the report's branding: found {len(found)} {_ACCENT} definitions in {p}, "
            "need exactly one. An unverifiable brand is not a verified one — fix the palette source "
            "rather than assuming a value."
        )
    markers = {_ACCENT: found.pop()}
    for name in _FUNCTIONAL:
        if name == _ACCENT:
            continue
        vals = defined(name)
        if len(vals) == 1:
            markers[name] = vals.pop()
    return markers


def _legacy_brand_accent(palette: str | pathlib.Path | None = None) -> tuple[int, int, int]:
    """The gold accent as (r, g, b). Raises if the palette cannot be read."""
    return _rgb(_legacy_brand_markers(palette)[_ACCENT])


# Content streams are normally compressed, and ReportLab's default is /Filter [/ASCII85Decode
# /FlateDecode] — a85 stacked OVER flate. Decoding only the flate half yields zero bytes, which reads
# as "this PDF has no colour operators at all" and is a parser bug wearing a finding's clothes.
_STREAM_RE = re.compile(rb"(?<!end)stream\r?\n")     # 'endstream' contains 'stream'; do not match it
_FILTER_RE = re.compile(rb"/Filter\s*(/[A-Za-z0-9]+|\[[^\]]*\])")
_NUM = rb"[-+]?(?:\d{1,12}(?:\.\d{0,12})?|\.\d{1,12})"   # bounded: unbounded \d+ backtracks on noise
_RGB_RE = re.compile(rb"(?<![\w.])(%s)\s+(%s)\s+(%s)\s+(?:rg|RG)(?![\w.])" % (_NUM, _NUM, _NUM))
_GRAY_RE = re.compile(rb"(?<![\w.])(%s)\s+(?:g|G)(?![\w.])" % _NUM)
_CMYK_RE = re.compile(rb"(?<![\w.])(%s)\s+(%s)\s+(%s)\s+(%s)\s+(?:k|K)(?![\w.])" % ((_NUM,) * 4))
_NONTEXT_RE = re.compile(rb"[^\t\r\n\x20-\x7e]")


def _flate(b: bytes) -> bytes:
    """decompressobj, not decompress: a stream padded to /Length makes the one-shot form raise."""
    for wbits in (15, -15):                  # -15 = raw deflate, no zlib header
        obj = zlib.decompressobj(wbits)
        try:
            out = obj.decompress(b) + obj.flush()
        except zlib.error:
            continue
        if out:
            return out
    raise zlib.error("not a flate stream")


def _a85(b: bytes) -> bytes:
    b = re.sub(rb"\s", b"", b)
    stop = b.find(b"~>")                     # Adobe EOD; a85decode chokes on it
    return base64.a85decode(b[:stop] if stop >= 0 else b)


def _ahx(b: bytes) -> bytes:
    h = re.sub(rb"[^0-9A-Fa-f]", b"", b.split(b">")[0])
    return binascii.unhexlify(h + b"0" * (len(h) % 2))   # spec: a lone final digit is padded with 0


_DECODERS = {b"FlateDecode": _flate, b"Fl": _flate, b"ASCII85Decode": _a85, b"A85": _a85,
             b"ASCIIHexDecode": _ahx, b"AHx": _ahx}


def _content_streams(data: bytes) -> list[bytes]:
    """Every stream that decodes to something text-shaped. Images and font programs are skipped.

    Never parses the xref or follows /Contents: a colour operator anywhere in the drawing operators
    counts, and an object graph is a great deal of code to reach the same answer. Binary payloads are
    dropped twice over — by their object dict, then by a printability test — because regexing image
    bytes for `rg` costs minutes and finds noise.
    """
    out = []
    for m in _STREAM_RE.finditer(data):
        head = data[data.rfind(b"obj", 0, m.start()):m.start()]
        if b"/Image" in head or b"/Length1" in head or b"FontFile" in head:
            continue
        end = data.find(b"endstream", m.end())
        if end < 0:
            continue
        raw = data[m.end():end]
        if raw.endswith(b"\r\n"):            # the EOL before 'endstream' is not stream data
            raw = raw[:-2]
        elif raw[-1:] in (b"\n", b"\r"):
            raw = raw[:-1]

        fm = _FILTER_RE.search(head)         # /Filter is often an ARRAY, applied left to right
        names = re.findall(rb"/([A-Za-z0-9]+)", fm.group(1)) if fm else []
        try:
            for name in names:
                raw = _DECODERS[name](raw)   # KeyError = a filter we cannot undo, e.g. DCTDecode
        except (KeyError, ValueError, zlib.error, binascii.Error):
            continue
        sample = raw[:4096]
        if len(_NONTEXT_RE.findall(sample)) > len(sample) // 10:
            continue
        out.append(raw)
    return out


def pdf_colors(pdf_path: str | pathlib.Path) -> dict[str, int]:
    """Distinct colours set by a PDF's content streams, as {"#RRGGBB": times set}.

    Stdlib only, so this always reaches a verdict — unlike report_embeds_figures, which needs pypdf.
    A relative path is resolved against RESULTS.
    """
    data = _at_results(pdf_path).read_bytes()
    counts: dict[str, int] = {}

    def add(rgb: tuple[float, ...]) -> None:
        chans = tuple(round(v * 255) for v in rgb)
        if all(0 <= c <= 255 for c in chans):
            key = "#%02X%02X%02X" % chans
            counts[key] = counts.get(key, 0) + 1

    for stream in _content_streams(data):
        for m in _RGB_RE.finditer(stream):
            add(tuple(float(m.group(i)) for i in (1, 2, 3)))
        for m in _GRAY_RE.finditer(stream):
            add((float(m.group(1)),) * 3)
        # The palette is HexColor, so this is only a safety net against a CMYK restyling failing the
        # gate. (1-v)(1-k) is what a reader renders DeviceCMYK as, and it returns gold exactly;
        # ReportLab's own cmyk2rgb uses 1-min(1,v+k) instead, which does not.
        for m in _CMYK_RE.finditer(stream):
            k = float(m.group(4))
            add(tuple((1 - float(m.group(i))) * (1 - k) for i in (1, 2, 3)))
    return counts


def _within(hexv: str, accent: tuple[int, int, int], tol: int) -> bool:
    """All three channels or nothing. ReportLab writes 6 decimals (.831373 .627451 .290196), which
    round-trips exactly; 2 units absorbs a producer that writes only 2 or 3.

    Per channel, and never a luminance or single-channel match: #D5CFC5, the warm grey used for table
    grid lines, sits 1 unit from gold in RED — and 47 away in green, so all three cannot be reached.
    """
    chans = (int(hexv[1:3], 16), int(hexv[3:5], 16), int(hexv[5:7], 16))
    return all(abs(a - b) <= tol for a, b in zip(chans, accent))


def _legacy_assert_report_branded(report_name: str, palette: str | pathlib.Path | None = None,
                                  tol: int = 2) -> str:
    """Fail unless the report carries the Phylo accent AND at least one other functional palette
    colour — i.e. unless it really went through the `pdf-report-generation` skill.

    What this is: an exact-value provenance check with a rounding cushion of `tol`/255 per channel. It
    reads the colours the PDF's own vector operators set and asks whether they are the palette's
    values. What it is NOT: a perceptual or design check. It says nothing about whether the report
    looks Phylo, and a gold a designer would accept fails it when the numbers differ. It raises the
    cost of faking branding — it does not make it impossible, and it should not be read as proof.

    Why this exists: the twin of assert_generated_by_tool. That gate exists because a schematic drawn
    with matplotlib passes a visual check and is not an infographic. This one exists because a
    hand-styled ReportLab PDF passes every other check in this module — it exists, it is big enough,
    it embeds the figures — and is not a Phylo report. The delegate skill ships NO code, only a
    palette, so "used ReportLab" is evidence of nothing (the branded path uses ReportLab too) and
    "loaded the skill" is a proxy an agent can satisfy and then ignore. Hence a property of the
    ARTIFACT: the palette's colours are in the page's colour operators or they are not.

    Why two markers rather than one, measured on three artifacts: the branded report carries 6 of the
    functional palette colours, the real unbranded report 0, and that same unbranded report with one
    60x6pt gold rectangle stamped on it carries 1. At one marker the gate was a tripwire — a single
    `rg` operator out of 52, about 0.006% of the page, flipped the verdict on a PDF with a blue title
    and an olive divider. Two markers mean a faked pass has to paint several exact palette values, and
    an agent holding those values has read the palette, which was the goal.

    The accent stays mandatory rather than "any two functional colours": it is the primary accent and
    its absence is the strongest single signal. Measured on the unbranded report, gold is 95 units
    away from the nearest colour it contains, where the near-neutrals are 9 (#CCCCCC nearly reaches
    TABLE_BORDER, #F2F6FC nearly reaches TABLE_ALT_ROW). Open `tol` up and the neutrals start matching
    a plain grey report; gold does not, so gold is the marker worth making compulsory.

    `report_name` is basenamed to the results ROOT, exactly as assert_report_exists does: that is
    where the report is required to be, so that is the only file worth checking.
    """
    markers = _legacy_brand_markers(palette)  # raises first: unverifiable is never a pass
    want = markers[_ACCENT]
    accent = _rgb(want)
    # Independent markers or none. An "other" whose value IS the accent (an alias spelled out as a
    # literal) would let one gold rectangle satisfy both halves; white appears in every report ever
    # written, branded or not.
    others = {n: h for n, h in markers.items()
              if n != _ACCENT and h not in _NO_SIGNAL and not _within(h, accent, tol)}
    if not others:
        raise GateFailure(
            f"cannot verify the report's branding: the palette at {_palette_file(palette)} leaves "
            f"this gate only the accent {want} to look for — of {', '.join(_FUNCTIONAL)} nothing else "
            "resolves to a colour of its own. A one-marker check is satisfied by a single gold "
            "rectangle, so this is a degrade, not a pass. Fix the palette source, or the name list in "
            "report_qc._FUNCTIONAL if the palette renamed something; an unverifiable brand is not a "
            "verified one."
        )

    base = os.path.basename(report_name)
    path = RESULTS / base
    if not path.exists():
        raise GateFailure(
            f"cannot verify branding: {base} is not at {RESULTS}/. Call assert_report_exists first."
        )

    found = pdf_colors(path)
    if not found:
        raise GateFailure(
            f"no colour operators could be read from {base}: the PDF sets no vector fill or stroke "
            "colour anywhere, so its branding cannot be verified, and an unverifiable brand is not a "
            "verified one. A rasterised or screenshot PDF lands here — colour inside an embedded "
            "image is deliberately not read, so a picture of a branded report does not count as one. "
            f"Build the report as real vector text and tables through the `{_DELEGATE}` skill."
        )

    looked_for = {_ACCENT: want, **others}
    hit = [n for n, h in looked_for.items() if any(_within(c, _rgb(h), tol) for c in found)]
    seen = sorted(found, key=lambda k: -found[k])
    used = ", ".join(seen[:12]) + (", ..." if len(seen) > 12 else "")

    if _ACCENT not in hit:
        # Derived, not pinned: the near-miss has to move when the accent or the tolerance does.
        near = "#%02X%02X%02X" % tuple(c + tol + 1 if c + tol + 1 <= 255 else c - tol - 1
                                       for c in accent)
        raise GateFailure(
            f"{base} does not use the Phylo accent {want} — checked as an exact value, within "
            f"{tol}/255 per channel, against the palette in `{_DELEGATE}`. That cushion absorbs a "
            f"producer that writes 2-decimal colours and nothing more: {near} is indistinguishable "
            "from the accent to the eye and is rejected here, so a gold that merely looks right does "
            "not satisfy this gate — the report has to carry the palette's own values. Delegate "
            f"report generation to `{_DELEGATE}` (load the skill and paint from its palette) rather "
            f"than styling one yourself. Colours it used instead: {used}"
        )

    if len(hit) < 2:
        raise GateFailure(
            f"{base} carries the accent {want} and nothing else from the palette. One colour is a "
            "stamp, not a branded report: the accent alone is satisfied by a single gold rectangle "
            "painted onto an otherwise hand-styled PDF, which is why two independent markers are "
            f"required. A report built through `{_DELEGATE}` paints its headings, body text, table "
            "rows and grid lines from the palette too — expected at least one of "
            + ", ".join(f"{n} {h}" for n, h in others.items())
            + f". Colours it used instead: {used}"
        )
    return want


def _report_style_roots(
    roots: tuple[pathlib.Path, ...] | None = None,
) -> tuple[pathlib.Path, ...]:
    if roots is not None:
        return roots
    system_root = pathlib.Path(
        os.environ.get("BIOMNI_SYSTEM_SKILLS_ROOT")
        or os.environ.get("BIOMNI_SKILLS_ROOT")
        or "/mnt/skills/system"
    )
    user_root = pathlib.Path(os.environ.get("BIOMNI_USER_SKILLS_ROOT", "/mnt/skills/user"))
    return user_root, system_root


def report_style_profile(
    *,
    transcript: str | pathlib.Path | None = None,
    roots: tuple[pathlib.Path, ...] | None = None,
) -> dict:
    """Resolve the report provider from immutable user messages or the package default."""
    provider_roots = _report_style_roots(roots)
    transcript_path = _at_results(transcript if transcript is not None else _TRANSCRIPT_REL)
    selected_provider = None
    selection_evidence = None
    if transcript_path.is_file():
        try:
            selected_provider, selection_evidence = selected_style_from_transcript(
                transcript_path,
                provider_roots,
                str(_TRANSCRIPT_REL),
            )
        except StyleProviderError as exc:
            raise GateFailure(str(exc)) from exc
    provider = selected_provider or DEFAULT_REPORT_STYLE_PROVIDER
    activation = "explicit_only" if selected_provider else "default"
    resolution_roots = provider_roots if selected_provider else (provider_roots[-1],)
    try:
        profile, source_path, source_evidence = resolve_provider(
            provider,
            resolution_roots,
            activation_hint=activation,
        )
    except StyleProviderError as exc:
        raise GateFailure(str(exc)) from exc
    return {
        "provider": provider,
        "selection": "explicit_override" if selected_provider else "package_default",
        "selection_evidence": selection_evidence or {
            "source": "package_default",
            "provider": DEFAULT_REPORT_STYLE_PROVIDER,
        },
        "profile": profile,
        "profile_source": str(source_path),
        "profile_source_evidence": source_evidence,
    }


def report_render_plan(
    *,
    transcript: str | pathlib.Path | None = None,
    roots: tuple[pathlib.Path, ...] | None = None,
) -> dict:
    """Return the selected provider and whether the package-owned renderer may run."""
    resolved = report_style_profile(transcript=transcript, roots=roots)
    return {
        **resolved,
        "render_with_bundled": resolved["provider"] == DEFAULT_REPORT_STYLE_PROVIDER,
    }


def assert_report_styled(
    report_name: str,
    *,
    resolved_style: dict | None = None,
    transcript: str | pathlib.Path | None = None,
    roots: tuple[pathlib.Path, ...] | None = None,
    tolerance: int = 2,
) -> dict:
    """Verify the final PDF against the resolved provider-owned marker contract."""
    resolved = resolved_style or report_style_profile(transcript=transcript, roots=roots)
    base = os.path.basename(report_name)
    report = RESULTS / base
    if not report.is_file():
        raise GateFailure(f"cannot verify report style: {base} is not at {RESULTS}/")
    markers = resolved.get("profile", {}).get("pdf_markers", {})
    required = markers.get("required_any", [])
    supporting = markers.get("supporting_any", [])
    minimum = markers.get("minimum_distinct_markers")
    if not required or not supporting or not isinstance(minimum, int):
        raise GateFailure("selected report style provider has no validated PDF marker contract")
    found = pdf_colors(report)
    if not found:
        raise GateFailure("no vector colour operators could be read from the final PDF")

    def matched(values: list[str]) -> list[str]:
        return [
            marker for marker in values
            if any(_within(color, _rgb(marker), tolerance) for color in found)
        ]

    required_hit = matched(required)
    supporting_hit = matched(supporting)
    distinct = sorted(set(required_hit + supporting_hit))
    if not required_hit:
        raise GateFailure(
            f"{base} does not carry a required marker for provider {resolved['provider']!r}"
        )
    if not supporting_hit or len(distinct) < minimum:
        raise GateFailure(
            f"{base} carries too few independent markers for provider {resolved['provider']!r}; "
            f"required {minimum}, found {len(distinct)}"
        )
    return {
        "provider": resolved["provider"],
        "selection": resolved["selection"],
        "selection_evidence": resolved["selection_evidence"],
        "profile_source": resolved["profile_source"],
        "profile_source_evidence": resolved["profile_source_evidence"],
        "required_markers_found": required_hit,
        "supporting_markers_found": supporting_hit,
        "minimum_distinct_markers": minimum,
        "pdf_sha256": _sha256(report),
    }


# --- writing large binaries to the results mount -------------------------------------------------

def staged_copy(src: str | pathlib.Path, dst: str | pathlib.Path) -> pathlib.Path:
    """Publish a completed binary from workspace without truncating it on the results mount.

    Direct writes or rewrites of PDF, HDF5, spreadsheet, presentation, and database files on the
    object-backed mount can fail or truncate. The destination is removed only after a non-empty
    staging file exists, then copied as a complete object.

    A relative `dst` is resolved against RESULTS. `src` stays relative to the CWD — it is the
    workspace staging file, which is the whole point of the two-step write.
    """
    src, dst = pathlib.Path(src), _at_results(dst)
    if not src.is_file() or src.stat().st_size == 0:
        raise GateFailure(f"staging file is absent or empty: {src}")
    if src.resolve() == dst.resolve():
        raise GateFailure("staging source and results destination must be different files")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        subprocess.run(["cp", str(src), str(dst)], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        shutil.copyfile(src, dst)
    if not dst.exists() or dst.stat().st_size == 0:
        raise GateFailure(f"staged copy produced a 0-byte file at {dst}")
    return dst


# --- the facts artifact --------------------------------------------------------------------------

def _load_skill_contract(path: str | pathlib.Path = "skill_contract.json") -> dict:
    candidate = pathlib.Path(path)
    if not candidate.is_absolute():
        candidate = SKILL_ROOT / candidate
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateFailure(f"cannot load facts/evidence contract at {candidate}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != "phylo-skill-evidence/1":
        raise GateFailure(f"{candidate} is not a phylo-skill-evidence/1 contract")
    return data


def clarification_branch_id(question_id: object, choice_id: object) -> str:
    """Return the stable runtime identifier for one clarification branch."""
    question = str(question_id).strip()
    choice = str(choice_id).strip()
    if not question or not choice:
        raise GateFailure("clarification branch IDs require both question_id and choice_id")
    if ":" in question or ":" in choice:
        raise GateFailure("clarification question and choice IDs may not contain ':'")
    return f"{question}:{choice}"


def outputs_for_selected_branches(
    selected_branch_ids: list[str],
    contract: str | pathlib.Path | dict = "skill_contract.json",
) -> list[str]:
    """Resolve receipt outputs from the branches actually selected for this run.

    Branch IDs use ``<question_id>:<choice_id>``. A static union can make mutually exclusive
    choices impossible to complete, so unknown, duplicate, or empty selections fail closed.
    """
    if (not isinstance(selected_branch_ids, list) or not selected_branch_ids
            or not all(isinstance(value, str) and value.strip() for value in selected_branch_ids)):
        raise GateFailure("selected_branch_ids must be a non-empty list of branch ID strings")
    if len(set(selected_branch_ids)) != len(selected_branch_ids):
        raise GateFailure("selected_branch_ids contains a duplicate branch ID")

    spec = contract if isinstance(contract, dict) else _load_skill_contract(contract)
    branches = spec.get("clarification_branches")
    if not isinstance(branches, list) or not branches:
        raise GateFailure("skill contract has no clarification branches")
    by_id: dict[str, dict] = {}
    for branch in branches:
        if not isinstance(branch, dict):
            raise GateFailure("skill contract contains a non-object clarification branch")
        branch_id = clarification_branch_id(branch.get("question_id"), branch.get("choice_id"))
        if branch_id in by_id:
            raise GateFailure(f"skill contract contains duplicate branch ID {branch_id!r}")
        by_id[branch_id] = branch

    unknown = [branch_id for branch_id in selected_branch_ids if branch_id not in by_id]
    if unknown:
        raise GateFailure(f"selected clarification branch IDs are not in the contract: {unknown}")
    questions = spec.get("clarification_questions")
    if not isinstance(questions, list) or not questions:
        raise GateFailure("skill contract has no clarification questions")
    selected_questions = [str(by_id[branch_id].get("question_id"))
                          for branch_id in selected_branch_ids]
    for question in questions:
        if not isinstance(question, dict):
            raise GateFailure("skill contract contains a non-object clarification question")
        question_id = str(question.get("id", "")).strip()
        count = selected_questions.count(question_id)
        if count == 0:
            raise GateFailure(f"clarification question {question_id!r} has no selected branch")
        if question.get("selection_mode") == "single" and count != 1:
            raise GateFailure(f"single-select clarification question {question_id!r} has {count} selections")
        if question.get("selection_mode") not in ("single", "multiple"):
            raise GateFailure(f"clarification question {question_id!r} has an invalid selection mode")
    outputs: list[str] = []
    for branch_id in selected_branch_ids:
        paths = by_id[branch_id].get("artifact_paths")
        if not isinstance(paths, list) or not paths:
            raise GateFailure(f"selected clarification branch {branch_id!r} has no artifact paths")
        for path in paths:
            value = str(path).strip()
            if not value:
                raise GateFailure(f"selected clarification branch {branch_id!r} has an empty output path")
            if value not in outputs:
                outputs.append(value)
    return outputs


def _json_value(data: object, dotted_path: str) -> object:
    value = data
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise GateFailure(f"facts/witness field {dotted_path!r} is absent at {part!r}")
        value = value[part]
    return value


def assert_semantic_facts(facts: dict, contract: dict) -> None:
    """Validate operational headline fields and denominator/completion accounting identities."""
    spec = contract.get("facts", {})
    if spec.get("requirement") != "required":
        raise GateFailure("write_facts called although skill_contract marks facts not_applicable")
    for headline in spec.get("headline_definitions", []):
        field = headline.get("field") if isinstance(headline, dict) else None
        definition = headline.get("operational_definition") if isinstance(headline, dict) else None
        if not field or not definition or _json_value(facts, field) is None:
            raise GateFailure("each headline fact needs a value and an operational definition")
    for group in spec.get("partition_groups", []):
        denominator = _json_value(facts, group["denominator_field"])
        members = [_json_value(facts, field) for field in group["member_fields"]]
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                   for value in [denominator, *members]):
            raise GateFailure(f"partition {group['name']!r} contains a non-numeric value")
        if group["identity"] == "sum_members_equals_denominator" and sum(members) != denominator:
            raise GateFailure(
                f"partition {group['name']!r} does not account: {sum(members)} != {denominator}"
            )


def write_facts(path: str | pathlib.Path, facts: dict, *,
                contract: str | pathlib.Path | dict | None = None) -> pathlib.Path:
    """Write the numbers the report is allowed to quote. Call this AFTER every gate has passed.

    The renderer must read from this file rather than restating what the agent remembers. A report
    that prints its count by reading the table cannot disagree with the table.

    A relative path is resolved against RESULTS: facts written under the CWD are invisible to both
    the report builder and the user.
    """
    if contract is not None:
        spec = contract if isinstance(contract, dict) else _load_skill_contract(contract)
        assert_semantic_facts(facts, spec)
    p = _at_results(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(facts, indent=2, sort_keys=True), encoding="utf-8")
    return p


# --- the run receipt -----------------------------------------------------------------------------

QC_RUN_LOG_SCHEMA = "phylo-qc-run-log/1"

#: Carried in the receipt beside every embedding verdict, so nobody downstream reads the verdict as
#: stronger than it is. Counting images proves the report contains at least as many pictures as were
#: declared; it does not prove they are those pictures. Identity matching is deliberately outside
#: this heuristic, so the receipt states the limitation beside the verdict.
EMBED_STATES = ("pass", "fail", "not_evaluable", "not_applicable")

_EMBED_METHOD = ("counts embedded images in the PDF and compares against the declared figure count; "
                 "does not match figure identity")
MIN_RENDERED_PAGE_BYTES = 1_000


def _ev_file(p: pathlib.Path) -> dict:
    """One artifact, as evidence: where it is and how big it is."""
    try:
        display_path = str(p.relative_to(RESULTS))
    except ValueError:
        display_path = str(p)
    return {"path": display_path, "bytes": p.stat().st_size}


def _sha256(p: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with p.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_qc_run_log(path: str | pathlib.Path) -> tuple[dict, list[dict]]:
    """Load records written by this module's helpers, never an author-composed trace ledger."""
    ledger_path = _at_results(path)
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateFailure(f"cannot load execution ledger at {ledger_path}: {exc}") from exc
    events = ledger.get("events") if isinstance(ledger, dict) else None
    if (not isinstance(ledger, dict) or ledger.get("schema") != QC_RUN_LOG_SCHEMA
            or not isinstance(events, list) or not all(isinstance(event, dict) for event in events)):
        raise GateFailure(f"QC run log must be a {QC_RUN_LOG_SCHEMA!r} object")
    if ledger.get("generated_by") != "report_qc":
        raise GateFailure("QC run log does not identify report_qc as its generator")
    return ledger, events


def _event(events: list[dict], event_type: str, report_name: str) -> dict:
    matches = [event for event in events
               if event.get("type") == event_type and event.get("report") == os.path.basename(report_name)]
    if len(matches) != 1:
        raise GateFailure(f"need exactly one {event_type!r} event for {report_name}, found {len(matches)}")
    return matches[0]


def _pdf_page_count(report: pathlib.Path) -> int:
    """Count pages with the platform's pinned PDF parser; never treat a byte regex as proof."""
    try:
        import pypdf  # type: ignore
    except ImportError as exc:
        executable = shutil.which("pdfinfo")
        if executable is None:
            advisory = len(re.findall(rb"/Type\s*/Page(?!s)\b", report.read_bytes()))
            raise GateFailure(
                f"pypdf and pdfinfo are unavailable, so page-tree verification is "
                f"NOT-EVALUABLE (advisory byte scan found {advisory} page markers)"
            ) from exc
        completed = subprocess.run(
            [executable, str(report)], check=False, capture_output=True, text=True, timeout=30,
        )
        match = re.search(r"^Pages:\s*(\d+)\s*$", completed.stdout, re.MULTILINE)
        if completed.returncode != 0 or match is None:
            raise GateFailure("pdfinfo could not verify the PDF page count") from exc
        count = int(match.group(1))
    else:
        try:
            count = len(pypdf.PdfReader(str(report)).pages)
        except Exception as exc:  # pypdf uses version-specific PdfReadError classes
            raise GateFailure(f"could not parse PDF page tree: {exc}") from exc
    if count < 1:
        raise GateFailure("could not verify the PDF page count")
    return count


def _pdf_review_evidence(events: list[dict], report_name: str) -> dict[str, dict]:
    report = assert_report_exists(report_name)
    # OT-R2-05: bind every event to the current final-PDF SHA256
    current_pdf_sha256 = _sha256(report)

    text_event = _event(events, "pdf_text_extraction", report_name)
    text_path = _at_results(text_event.get("artifact", ""))
    if not text_path.is_file() or not text_path.read_text(encoding="utf-8", errors="replace").strip():
        raise GateFailure("PDF text extraction event has no non-empty text artifact")

    # OT-R2-05: verify pdf_sha256 in text event matches current PDF
    event_pdf_hash = text_event.get("pdf_sha256", "")
    if event_pdf_hash and event_pdf_hash != current_pdf_sha256:
        raise GateFailure(
            f"PDF text extraction event is bound to a stale PDF (event sha256={event_pdf_hash[:12]}..., "
            f"current sha256={current_pdf_sha256[:12]}...). The PDF was overwritten or the event is stale."
        )
    # OT-R2-05: verify text artifact sha256 matches
    event_text_hash = text_event.get("artifact_sha256", "")
    if event_text_hash and event_text_hash != _sha256(text_path):
        raise GateFailure(
            "PDF text extraction artifact sha256 does not match the current text file. "
            "The text was substituted or the event is stale."
        )

    render_event = _event(events, "pdf_render", report_name)
    # OT-R2-05: verify pdf_sha256 in render event matches current PDF
    event_pdf_hash = render_event.get("pdf_sha256", "")
    if event_pdf_hash and event_pdf_hash != current_pdf_sha256:
        raise GateFailure(
            f"PDF render event is bound to a stale PDF (event sha256={event_pdf_hash[:12]}..., "
            f"current sha256={current_pdf_sha256[:12]}...). The PDF was overwritten or the event is stale."
        )
    pages = render_event.get("pages")
    if not isinstance(pages, list) or not pages:
        raise GateFailure("PDF render event contains no pages")
    page_numbers = []
    rendered = []
    for page in pages:
        number = page.get("page") if isinstance(page, dict) else None
        image = _at_results(page.get("image", "")) if isinstance(page, dict) else pathlib.Path()
        if (not isinstance(number, int) or number < 1 or not image.is_file()
                or image.stat().st_size < MIN_RENDERED_PAGE_BYTES):
            raise GateFailure("PDF render event has an invalid or blank page image")
        # OT-R2-05: verify page image sha256 matches
        page_hash = page.get("sha256", "") if isinstance(page, dict) else ""
        if page_hash and page_hash != _sha256(image):
            raise GateFailure(
                f"PDF render page {number} image sha256 does not match the current file. "
                "The page image was substituted or the event is stale."
            )
        page_numbers.append(number)
        rendered.append(_ev_file(image))
    expected = list(range(1, max(page_numbers) + 1))
    if sorted(page_numbers) != expected:
        raise GateFailure(f"PDF render pages {sorted(page_numbers)} do not cover {expected}")
    pdf_page_count = _pdf_page_count(report)
    if len(expected) != pdf_page_count:
        raise GateFailure(f"rendered {len(expected)} page(s), but the PDF contains {pdf_page_count}")

    review_event = _event(events, "pdf_visual_review", report_name)
    # OT-R2-05: verify pdf_sha256 in review event matches current PDF
    event_pdf_hash = review_event.get("pdf_sha256", "")
    if event_pdf_hash and event_pdf_hash != current_pdf_sha256:
        raise GateFailure(
            f"PDF visual review event is bound to a stale PDF (event sha256={event_pdf_hash[:12]}..., "
            f"current sha256={current_pdf_sha256[:12]}...). The PDF was overwritten or the event is stale."
        )
    review_state = review_event.get("state")
    if review_state not in EMBED_STATES:
        raise GateFailure(f"PDF visual-review state must be one of {EMBED_STATES}")
    if review_state != "pass":
        return {
            "text_extracted": {"state": "pass", "report": _ev_file(report),
                               "text": _ev_file(text_path), "pdf_sha256": current_pdf_sha256},
            "pages_rendered": {"state": "pass", "pages": rendered,
                               "pdf_sha256": current_pdf_sha256},
            "visual_review_attested": {
                "state": review_state,
                "pages": review_event.get("pages") or [],
                "attestation": str(review_event.get("review_evidence", "")),
                "pdf_sha256": current_pdf_sha256,
            },
        }
    reviewed_raw = review_event.get("pages")
    if (not isinstance(reviewed_raw, list)
            or not all(isinstance(page, int) and not isinstance(page, bool) for page in reviewed_raw)):
        raise GateFailure("PDF visual-review pages must be an array of page numbers")
    reviewed = sorted(reviewed_raw)
    attestation = str(review_event.get("review_evidence", "")).strip()
    if reviewed != expected or not attestation:
        raise GateFailure("visual review must cover every rendered page and name its evidence")
    if "not_evaluable" in attestation.lower() or "not evaluable" in attestation.lower():
        raise GateFailure("a pass visual-review attestation cannot state that review was unavailable")
    return {
        "text_extracted": {"state": "pass", "report": _ev_file(report), "text": _ev_file(text_path),
                           "pdf_sha256": current_pdf_sha256},
        "pages_rendered": {"state": "pass", "pages": rendered, "pdf_sha256": current_pdf_sha256},
        "visual_review_attested": {
            "state": "pass",
            "pages": reviewed,
            "attestation": attestation,
            "limitation": "author/agent attestation; not independently machine-verifiable",
            "pdf_sha256": current_pdf_sha256,
        },
    }


def assert_source_witnesses(contract: dict) -> list[dict]:
    """Verify computation-critical asserted values against artifacts produced by the run."""
    if contract.get("source_assertion_policy") == "validated_runtime_artifacts":
        facts_path = _at_results("report_facts.json")
        provenance_path = _at_results("provenance.json")
        ledger_path = _at_results("operation_ledger.json")
        bundle_path = _at_results("root_bundle.json")
        try:
            facts = json.loads(facts_path.read_text(encoding="utf-8"))
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GateFailure(f"cannot read runtime source witnesses: {exc}") from exc
        if facts.get("disease_id") != provenance.get("disease", {}).get("id"):
            raise GateFailure("runtime facts and provenance disagree on disease identity")
        if facts.get("source_mode") != provenance.get("source_mode"):
            raise GateFailure("runtime facts and provenance disagree on source mode")
        if facts.get("operation_ledger_sha256") != _sha256(ledger_path):
            raise GateFailure("runtime facts do not match the operation ledger hash")
        if provenance.get("operation_ledger_sha256") != _sha256(ledger_path):
            raise GateFailure("runtime provenance does not match the operation ledger hash")
        if facts.get("validation_errors") or provenance.get("validation_errors"):
            raise GateFailure("runtime validation contains one or more errors")
        if bundle.get("schema") != "phylo-root-bundle/1":
            raise GateFailure("runtime root bundle has the wrong schema")
        lineage = {
            "source_mode": bundle.get("source_mode"),
            "operation_id": bundle.get("operation_id"),
        }
        if facts.get("root_lineage") != lineage or provenance.get("root_lineage") != lineage:
            raise GateFailure("runtime facts and provenance do not share root lineage")
        return [
            {"id": "runtime-facts", "artifact": str(facts_path), "sha256": _sha256(facts_path)},
            {"id": "runtime-provenance", "artifact": str(provenance_path),
             "sha256": _sha256(provenance_path)},
            {"id": "runtime-operation-ledger", "artifact": str(ledger_path),
             "sha256": _sha256(ledger_path)},
            {"id": "root-bundle", "artifact": str(bundle_path), "sha256": _sha256(bundle_path)},
        ]
    checked = []
    assertions = contract.get("source_assertions")
    if not isinstance(assertions, list):
        raise GateFailure("source_assertions must be an array")
    if not assertions:
        reason = str(contract.get("source_assertions_not_applicable_reason", "")).strip()
        if not reason:
            raise GateFailure("no source assertions and no non-empty applicability reason")
        return []
    for assertion in assertions:
        if not isinstance(assertion, dict) or not isinstance(assertion.get("runtime_witness"), dict):
            raise GateFailure("each source assertion needs a runtime_witness object")
        witness = assertion["runtime_witness"]
        artifact = _at_results(witness["artifact"])
        try:
            payload = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GateFailure(f"cannot read source witness {artifact}: {exc}") from exc
        got = _json_value(payload, witness["json_path"])
        if got != witness["expected_value"]:
            raise GateFailure(
                f"source witness {assertion['id']!r} is {got!r}, expected {witness['expected_value']!r}"
            )
        checked.append({"id": assertion["id"], "artifact": str(artifact), "sha256": _sha256(artifact)})
    return checked


def finalize_root_bundle(
    artifact_paths: list[str],
    bundle_path: str | pathlib.Path = "root_bundle.json",
) -> pathlib.Path:
    """Bind the complete Open Targets result set to one live/fixture lineage."""
    facts_path = _at_results("report_facts.json")
    provenance_path = _at_results("provenance.json")
    ledger_path = _at_results("operation_ledger.json")
    try:
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateFailure(f"cannot construct root bundle: {exc}") from exc
    source_mode = provenance.get("source_mode")
    if source_mode not in {"live", "versioned_snapshot", "user_upload", "fixture"}:
        raise GateFailure(f"cannot construct root bundle with source mode {source_mode!r}")
    if facts.get("source_mode") != source_mode:
        raise GateFailure("facts and provenance disagree on source mode")
    operation_id = _sha256(ledger_path)
    if facts.get("operation_ledger_sha256") != operation_id:
        raise GateFailure("facts operation-ledger hash is stale")
    input_payload = {
        "disease_id": facts.get("disease_id"),
        "study_ids": [item.get("study_id") for item in facts.get("studies", [])
                      if isinstance(item, dict)],
        "retrieved_targets": len(facts.get("top_targets") or []),
        "source_mode": source_mode,
    }
    input_sha256 = hashlib.sha256(
        json.dumps(input_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    lineage = {"source_mode": source_mode, "operation_id": operation_id}
    facts["root_lineage"] = lineage
    provenance["root_lineage"] = lineage
    facts_path.write_text(json.dumps(facts, indent=2, sort_keys=True), encoding="utf-8")
    provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True), encoding="utf-8")

    members = {}
    for declared in dict.fromkeys(artifact_paths):
        artifact = _at_results(declared)
        if not artifact.is_file() or artifact.stat().st_size == 0:
            raise GateFailure(f"cannot finalize missing or empty root artifact: {artifact}")
        key = str(artifact.relative_to(RESULTS)) if artifact.is_relative_to(RESULTS) else artifact.name
        members[key] = {
            "bytes": artifact.stat().st_size,
            "sha256": _sha256(artifact),
            "source_mode": source_mode,
            "operation_id": operation_id,
        }
    bundle = {
        "schema": "phylo-root-bundle/1",
        "source_mode": source_mode,
        "operation_id": operation_id,
        "input_sha256": input_sha256,
        "inputs": input_payload,
        "live_claim_eligible": source_mode == "live",
        "fixture_to_live_upgrade_forbidden": True,
        "artifacts": members,
    }
    target = _at_results(bundle_path)
    target.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
    return target


def finalize_report_artifacts(
    report_name: str,
    *,
    infographic: str = "infographic_open_targets_workflow.png",
    bundle_path: str | pathlib.Path = "root_bundle.json",
) -> pathlib.Path:
    """Finalize lineage only after the selected provider's canonical PDF is staged."""
    current_bundle = _at_results(bundle_path)
    try:
        bundle = json.loads(current_bundle.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateFailure(f"cannot read the pre-report root bundle: {exc}") from exc
    artifacts = bundle.get("artifacts")
    if bundle.get("schema") != "phylo-root-bundle/1" or not isinstance(artifacts, dict):
        raise GateFailure("cannot finalize report artifacts from an invalid root bundle")
    required = list(artifacts)
    required.extend([
        os.path.basename(report_name),
        infographic,
        "report_content.json",
        "report_structure.json",
    ])
    return finalize_root_bundle(list(dict.fromkeys(required)), bundle_path=bundle_path)


def assert_root_bundle_lineage(
    bundle_path: str | pathlib.Path,
    required_artifacts: list[str],
) -> dict:
    """Verify one immutable source mode and operation lineage across root deliverables."""
    path = _at_results(bundle_path)
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateFailure(f"cannot load root bundle {path}: {exc}") from exc
    if bundle.get("schema") != "phylo-root-bundle/1":
        raise GateFailure("root bundle has an unsupported schema")
    source_mode = bundle.get("source_mode")
    operation_id = bundle.get("operation_id")
    if source_mode not in {"live", "versioned_snapshot", "user_upload", "fixture"}:
        raise GateFailure(f"root bundle source_mode is invalid: {source_mode!r}")
    if not isinstance(operation_id, str) or len(operation_id) != 64:
        raise GateFailure("root bundle operation_id must be a SHA-256 lineage identifier")
    if source_mode == "fixture" and bundle.get("live_claim_eligible") is not False:
        raise GateFailure("fixture-derived root bundle attempted a live upgrade")
    members = bundle.get("artifacts")
    if not isinstance(members, dict):
        raise GateFailure("root bundle artifacts must be an object")
    checked = []
    for declared in required_artifacts:
        artifact = _at_results(declared)
        key = str(artifact.relative_to(RESULTS)) if artifact.is_relative_to(RESULTS) else artifact.name
        member = members.get(key) or members.get(artifact.name)
        if not isinstance(member, dict):
            raise GateFailure(f"root bundle does not declare {declared!r}")
        if not artifact.is_file() or member.get("sha256") != _sha256(artifact):
            raise GateFailure(f"root artifact drift or absence: {artifact}")
        if member.get("source_mode") != source_mode or member.get("operation_id") != operation_id:
            raise GateFailure(f"root artifact lineage mismatch for {declared!r}")
        checked.append({"path": str(artifact), "sha256": _sha256(artifact)})
    return {
        "source_mode": source_mode,
        "operation_id": operation_id,
        "live_claim_eligible": bundle.get("live_claim_eligible"),
        "checked": checked,
    }


#: The installed skill package — this module is copied to ``<package>/scripts/report_qc.py``, so the
#: package root is two levels up. Resolved at import, not hardcoded, because the slug differs per
#: skill and the mount point differs between authoring (``/mnt/results/skills/<slug>``) and installed
#: (``/mnt/skills/<slug>``).
SKILL_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _find_bundled(name: str) -> pathlib.Path:
    """Resolve a package-relative bundled file across authoring and installed layouts.

    A path such as ``scripts/analyse.py`` can be relative to the current directory, the skill
    package, or the results root. The receipt verifies the first existing candidate and records its
    resolved identity and digest.
    """
    tried = []
    for cand in (pathlib.Path(name), SKILL_ROOT / name, _at_results(name)):
        tried.append(cand)
        if cand.exists():
            return cand
    raise GateFailure(
        f"bundled file {name!r} is not present. Looked in the working directory, the skill package "
        f"({SKILL_ROOT}) and the results root — "
        + ", ".join(str(t) for t in tried)
        + ". Name it as it sits in the package, e.g. 'scripts/analyse.py'."
    )


def _write_qc_event(event: dict, path: str | pathlib.Path = "qc_run_log.json") -> pathlib.Path:
    log_path = _at_results(path)
    if log_path.is_file():
        ledger, events = _load_qc_run_log(log_path)
    else:
        ledger, events = {"schema": QC_RUN_LOG_SCHEMA, "generated_by": "report_qc"}, []
    events.append(event)
    ledger["events"] = events
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
    return log_path


def _replace_qc_events(replacements: list[dict], *,
                       path: str | pathlib.Path = "qc_run_log.json") -> pathlib.Path:
    """Replace matching report/type events and persist one coherent retry set in one write."""
    log_path = _at_results(path)
    if log_path.is_file():
        ledger, events = _load_qc_run_log(log_path)
    else:
        ledger, events = {"schema": QC_RUN_LOG_SCHEMA, "generated_by": "report_qc"}, []
    keys = {(event.get("type"), event.get("report")) for event in replacements}
    ledger["events"] = [
        event for event in events if (event.get("type"), event.get("report")) not in keys
    ] + replacements
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
    return log_path


def _artifact_state(path: pathlib.Path) -> tuple[int, str] | None:
    """Content fingerprint used to distinguish current-run outputs from stale files."""
    if not path.is_file():
        return None
    return path.stat().st_size, _sha256(path)


def run_bundled(argv: list[str], bundled_file: str, expected_outputs: list[str], *,
                log_path: str | pathlib.Path = "qc_run_log.json") -> subprocess.CompletedProcess:
    """Run one bundled command without a shell and log measured hashes and exit status."""
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        raise GateFailure("argv must be a non-empty list of strings")
    bundled = _find_bundled(bundled_file)
    bundled_identity = bundled.resolve()
    argv_paths = {pathlib.Path(item).resolve() for item in argv}
    if bundled_file not in argv and bundled_identity not in argv_paths:
        raise GateFailure("argv does not name the bundled file whose execution would be recorded")
    before = {output: _artifact_state(_at_results(output)) for output in expected_outputs}
    completed = subprocess.run(argv, check=False, capture_output=True, text=True)
    produced = []
    for output in expected_outputs:
        artifact = _at_results(output)
        after = _artifact_state(artifact)
        if after is not None and after[0] and after != before[output]:
            produced.append({"path": output, "sha256": _sha256(artifact), "bytes": artifact.stat().st_size})
    _write_qc_event({
        "type": "command", "argv": argv, "bundled_file": bundled_file,
        "bundled_sha256": _sha256(bundled), "exit_status": completed.returncode,
        "produced_artifacts": produced,
    }, log_path)
    if completed.returncode:
        raise GateFailure(f"bundled command exited {completed.returncode}: {completed.stderr[-500:]}")
    return completed


def record_pdf_review(report_name: str, text_artifact: str, rendered_page_files: list[str],
                      reviewed_page_numbers: list[int], review_attestation: str, *,
                      review_state: str,
                      log_path: str | pathlib.Path = "qc_run_log.json",
                      pdf_sha256: str | None = None,
                      artifact_sha256s: dict[str, str] | None = None) -> pathlib.Path:
    """Record PDF artifacts plus an honestly labelled human/agent visual-review attestation.

    OT-R2-05: every event is hash-bound to the current final PDF via ``pdf_sha256``,
    and every text/page artifact carries its own SHA256 in ``artifact_sha256s``.
    The receipt gate verifies agreement at receipt time, so a PDF overwrite, text
    substitution, or stale event fails.
    """
    if not rendered_page_files:
        raise GateFailure("rendered_page_files must name every rendered PDF page")
    if not review_attestation.strip():
        raise GateFailure("visual review needs a non-empty attestation")
    if review_state not in EMBED_STATES:
        raise GateFailure(f"review_state must be one of {EMBED_STATES}")
    if review_state == "pass" and not reviewed_page_numbers:
        raise GateFailure("a passing visual review must name every reviewed page")
    artifact_hashes = artifact_sha256s or {}
    pages = [{"page": number, "image": path,
              "sha256": artifact_hashes.get(path, "")}
             for number, path in enumerate(rendered_page_files, 1)]
    events = [
        {"type": "pdf_text_extraction", "report": os.path.basename(report_name),
         "artifact": text_artifact,
         "artifact_sha256": artifact_hashes.get(text_artifact, ""),
         "pdf_sha256": pdf_sha256 or ""},
        {"type": "pdf_render", "report": os.path.basename(report_name),
         "pages": pages, "pdf_sha256": pdf_sha256 or ""},
        {"type": "pdf_visual_review", "report": os.path.basename(report_name),
         "pages": reviewed_page_numbers, "review_evidence": review_attestation,
         "state": review_state,
         "evidence_type": "author_or_agent_attestation",
         "pdf_sha256": pdf_sha256 or ""},
    ]
    return _replace_qc_events(events, path=log_path)


def _extract_report_text(report_name: str) -> str | None:
    """Best-effort report text: the extracted text artifact first, then pypdf. None if unreadable."""
    base = os.path.basename(report_name)
    stem = base[:-4] if base.lower().endswith(".pdf") else base
    candidate = _at_results(f"{stem}_text.txt")
    if candidate.is_file():
        try:
            return candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    report = _at_results(base)
    if not report.is_file():
        return None
    try:
        import pypdf  # type: ignore
        reader = pypdf.PdfReader(str(report))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return None


def _read_infographic_disclosure(narrative_facts, narrative_provenance, report_name,
                                 expected_state: str):
    """Confirm facts, provenance, and the report agree on the lineage state and disclose it (OT-R4).

    Returns ``(facts_state, provenance_state, report_discloses, missing)``. ``missing`` lists the
    surfaces that fail the contract. For a ``not_evaluable_platform_trace`` state an empty ``missing``
    means the run may complete non-blocking; any entry keeps it release-blocking. For ``verified`` it
    checks that no surface makes a false non-verification disclosure and that the states agree.
    """
    missing: list[str] = []
    facts = prov = None
    if narrative_facts is not None:
        try:
            facts = json.loads(_at_results(narrative_facts).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            facts = None
    if narrative_provenance is not None:
        try:
            prov = json.loads(_at_results(narrative_provenance).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prov = None
    fl = facts.get("infographic_lineage") if isinstance(facts, dict) else None
    pl = prov.get("infographic") if isinstance(prov, dict) else None
    facts_state = fl.get("state") if isinstance(fl, dict) else None
    provenance_state = pl.get("lineage_state") if isinstance(pl, dict) else None

    report_text = _extract_report_text(report_name)
    marker = LINEAGE_DISCLOSURE_MARKER.lower()
    report_discloses = report_text is not None and marker in report_text.lower()

    if expected_state == LINEAGE_VERIFIED:
        if facts_state != LINEAGE_VERIFIED:
            missing.append(f"facts state {facts_state!r} != verified")
        if not isinstance(fl, dict) or fl.get("verification_claim") is not True:
            missing.append("facts verification_claim is not true")
        if provenance_state != LINEAGE_VERIFIED:
            missing.append(f"provenance lineage_state {provenance_state!r} != verified")
        return facts_state, provenance_state, report_discloses, missing

    # not_evaluable_platform_trace / failed: every surface must carry state + disclosure, no claim.
    if facts_state != expected_state:
        missing.append(f"facts state {facts_state!r} != {expected_state}")
    if not isinstance(fl, dict) or fl.get("verification_claim") is not False:
        missing.append("facts does not set verification_claim=false")
    if not isinstance(fl, dict) or marker not in str(fl.get("disclosure", "")).lower():
        missing.append("facts missing provenance-not-verified disclosure")
    if provenance_state != expected_state:
        missing.append(f"provenance lineage_state {provenance_state!r} != {expected_state}")
    if not isinstance(pl, dict) or pl.get("independently_verified") is not False:
        missing.append("provenance does not set independently_verified=false")
    if not isinstance(pl, dict) or marker not in str(pl.get("disclosure", "")).lower():
        missing.append("provenance missing disclosure text")
    if report_text is None:
        missing.append("report text unavailable to confirm disclosure")
    elif not report_discloses:
        missing.append("report does not disclose that provenance is not independently verified")
    return facts_state, provenance_state, report_discloses, missing


def write_receipt(report_name: str, figures: list[dict], *,
                  bundled_files: tuple[str, ...] | list[str] = (),
                  outputs: tuple[str, ...] | list[str] = (),
                  infographics: tuple[str, ...] | list[str] = (),
                  infographic_prompt: str | pathlib.Path | None = None,
                  path: str | pathlib.Path = "run_receipt.json",
                  qc_run_log: str | pathlib.Path | None = None,
                  figure_not_applicable_reason: str | None = None,
                  contract: str | pathlib.Path | dict = "skill_contract.json",
                  validation_context: dict | None = None,
                  narrative_facts: str | pathlib.Path | None = None,
                  narrative_provenance: str | pathlib.Path | None = None,
                  root_bundle: str | pathlib.Path = "root_bundle.json",
                  root_artifacts: tuple[str, ...] | list[str] = (),
                  style_transcript: str | pathlib.Path | None = None,
                  report_style_roots: tuple[pathlib.Path, ...] | None = None,
                  strict: bool = True) -> pathlib.Path:
    """Run the gates and write the receipt from what they returned. Do not hand-write this file.

    Every boolean is returned by a gate and carries the evidence it was decided from: a resolved
    path, byte count, PDF colour sample, parsed contract witness, or measured subprocess event.

    What it does NOT do: prove anything against an author who is willing to write the JSON by hand.
    An agent that can write a file can write any file, and no in-band artifact fixes that. What it
    does is make the honest path one call and make a pasted checklist fail — `evidence` has to be
    there and has to be non-empty, and it is tedious to forge convincingly. Read it as raising the
    cost of the wrong thing, not as proof against a hostile author.

    Receipt v3 binds the selected style transcript, canonical report, content model, and final
    deliverables into one evidence chain. Other outcomes are read off the artifacts and stand on
    their own, with one
    documented soft edge — the figure-embed count needs pypdf, so where it cannot run the receipt
    records `embed_check_ran: false` rather than failing or pretending it passed.

    Writes to the RESULTS root, never beside SKILL.md: once `Skill(action="create")` installs the
    package that directory is mounted read-only, so a per-run receipt written there cannot work.

    Every check runs even after one fails, the file is written either way, and `strict` then raises —
    a failing run should leave the diagnostic behind rather than dying before it is written.
    """
    outcome: dict[str, bool] = {}
    states: dict[str, str] = {}
    evidence: dict[str, object] = {}
    reasons: dict[str, str] = {}
    # Tri-state rather than a boolean, and separate from figures_present_and_nonblank, because the
    # two are checked to different strengths: the artifacts are proved with the stdlib, the embedding
    # needs pypdf and may not be evaluable at all. Starts "not_evaluable" so a run that dies before
    # the figure step records the honest answer rather than a default that flatters it.
    embedded = ["not_evaluable"]
    ledger_requested = qc_run_log is not None
    ledger_events: list[dict] = []
    ledger_error = ""
    if ledger_requested:
        try:
            _, ledger_events = _load_qc_run_log(qc_run_log)
        except (GateFailure, OSError, ValueError) as exc:
            ledger_error = f"{type(exc).__name__}: {exc}"
    try:
        contract_data = contract if isinstance(contract, dict) else _load_skill_contract(contract)
    except (GateFailure, OSError, ValueError):
        contract_data = {}

    def record(key: str, fn) -> None:
        try:
            evidence[key] = fn()
            outcome[key] = True
            states[key] = "pass"
        except (GateFailure, OSError, ValueError) as exc:
            outcome[key] = False
            states[key] = ("not_evaluable" if str(exc).lower().startswith("not_evaluable:")
                           else "fail")
            reasons[f"{key}_reason"] = f"{type(exc).__name__}: {exc}"

    def _bundled() -> dict:
        if not bundled_files:
            execution = contract_data.get("execution", {}) if ledger_requested else {}
            reason = str(execution.get("not_applicable_reason", "")).strip()
            if ledger_requested and execution.get("bundled_commands_applicable") is False and reason:
                return {"status": "not_applicable", "reason": reason}
            raise GateFailure("no bundled files named and execution is not explicitly not_applicable")
        if ledger_requested and ledger_error:
            raise GateFailure(ledger_error)
        seen = []
        for f in bundled_files:
            bundled = _find_bundled(f)
            if ledger_requested:
                digest = _sha256(bundled)
                matches = [event for event in ledger_events
                           if event.get("type") == "command"
                           and event.get("bundled_file") == f
                           and event.get("bundled_sha256") == digest
                           and event.get("exit_status") == 0]
                if not matches:
                    raise GateFailure(f"no successful trace-derived execution for bundled file {f!r}")
                seen.append({**_ev_file(bundled), "sha256": digest,
                             "qc_log_event": ledger_events.index(matches[0])})
            else:
                seen.append(_ev_file(bundled))
        if ledger_requested:
            return {"executed": seen, "method": "artifact hash matched to report_qc subprocess log"}
        return {"claimed_executed_by_caller": seen,
                "limitation": "existence and size are checked here; execution is the caller's claim"}

    def _outputs() -> dict:
        if not outputs:
            raise GateFailure(
                "no declared outputs named. Pass every path '## Outputs' promises, so the key means "
                "'they appeared' rather than 'nobody looked'."
            )
        if ledger_requested and ledger_error:
            raise GateFailure(ledger_error)
        seen = []
        for f in outputs:
            p = _at_results(f)
            if not p.exists():
                raise GateFailure(f"declared output {f} did not appear at {p}")
            if p.stat().st_size == 0:
                raise GateFailure(f"declared output {p} is 0 bytes")
            item = {**_ev_file(p), "sha256": _sha256(p)}
            if ledger_requested:
                relative = str(p.relative_to(RESULTS)) if p.is_relative_to(RESULTS) else str(p)
                matches = []
                for event in ledger_events:
                    produced_artifacts = event.get("produced_artifacts", [])
                    if not isinstance(produced_artifacts, list):
                        continue
                    for produced in produced_artifacts:
                        if not isinstance(produced, dict):
                            continue
                        if (produced.get("path") in (f, relative, str(p))
                                and produced.get("sha256") == item["sha256"]):
                            matches.append(event)
                if not matches:
                    raise GateFailure(f"output {f!r} has no matching trace-derived artifact hash")
                item["qc_log_event"] = ledger_events.index(matches[0])
            seen.append(item)
        return {"appeared": seen,
                "method": "artifact hashes matched to report_qc subprocess log" if ledger_requested else "filesystem"}

    def _report() -> dict:
        return _ev_file(assert_report_exists(report_name))

    def _figures() -> dict:
        # Artifact validity and PDF embedding are separate claims with different evidence strengths.
        # `figure_contract_satisfied` proves each declared figure exists, is non-blank, and carries a
        # caption. `figures_embedded` separately reports the PDF image-count heuristic.
        if not figures:
            if not figure_not_applicable_reason:
                raise GateFailure("no figures and no figure_not_applicable_reason")
            embedded[0] = "not_applicable"
            return {"status": "not_applicable", "reason": figure_not_applicable_reason}
        checked = assert_figures(figures)
        ok, detail = report_embeds_figures(report_name, checked)
        if ok:
            embedded[0] = "pass"
        elif detail.startswith("NOT-EVALUABLE"):
            embedded[0] = "not_evaluable"
        else:
            embedded[0] = "fail"
        files = [_ev_file(_at_results(e["file"])) for e in checked if e.get("file")]
        skipped = [{"step": e.get("step"), "reason": e.get("reason")}
                   for e in checked if not e.get("file")]
        return {"figures": files, "declared_unplottable": skipped,
                "embedding": {"state": embedded[0], "detail": detail, "method": _EMBED_METHOD}}

    resolved_style: dict | None = None

    def _styled() -> dict:
        nonlocal resolved_style
        resolved_style = report_style_profile(
            transcript=style_transcript,
            roots=report_style_roots,
        )
        return assert_report_styled(report_name, resolved_style=resolved_style)

    record("execution_contract_satisfied" if ledger_requested else "bundled_files_ran", _bundled)
    record("outputs_appeared", _outputs)
    record("report_at_results_root", _report)
    if narrative_facts is not None or narrative_provenance is not None:
        if narrative_facts is None or narrative_provenance is None:
            outcome["report_content_complete"] = False
            states["report_content_complete"] = "fail"
            reasons["report_content_complete_reason"] = (
                "both narrative_facts and narrative_provenance are required"
            )
        else:
            record("report_content_complete", lambda: assert_report_content_contract(
                report_name, narrative_facts, narrative_provenance
            ))
    record("figure_contract_satisfied" if ledger_requested else "figures_present_and_nonblank", _figures)
    record("report_style_verified", _styled)
    root_required = list(root_artifacts) or list(dict.fromkeys([
        *outputs, os.path.basename(report_name), *infographics,
    ]))
    record("root_lineage_verified", lambda: assert_root_bundle_lineage(
        root_bundle, root_required
    ))

    if ledger_requested:
        if ledger_error:
            for key in ("text_extracted", "pages_rendered", "visual_review_attested"):
                outcome[key] = False
                states[key] = "not_evaluable"
                reasons[f"{key}_reason"] = ledger_error
        else:
            try:
                review = _pdf_review_evidence(ledger_events, report_name)
            except (GateFailure, OSError, ValueError) as exc:
                for key in ("text_extracted", "pages_rendered", "visual_review_attested"):
                    outcome[key] = False
                    states[key] = "not_evaluable"
                    reasons[f"{key}_reason"] = f"{type(exc).__name__}: {exc}"
            else:
                for key in ("text_extracted", "pages_rendered"):
                    evidence[key] = review[key]
                    outcome[key] = True
                    states[key] = "pass"
                evidence["visual_review_attested"] = review["visual_review_attested"]
                visual_state = review["visual_review_attested"]["state"]
                outcome["visual_review_attested"] = visual_state == "pass"
                states["visual_review_attested"] = visual_state
                if visual_state != "pass":
                    reasons["visual_review_attested_reason"] = (
                        f"{visual_state}: {review['visual_review_attested']['attestation']}"
                    )

        def _sources() -> dict:
            if ledger_error:
                raise GateFailure(ledger_error)
            witnessed = assert_source_witnesses(contract_data)
            return {
                "checked": witnessed,
                "not_applicable_reason": contract_data.get("source_assertions_not_applicable_reason")
                if not witnessed else None,
            }

        record("source_assertions_verified", _sources)

    # OT-R4: three-state infographic lineage. `verified` passes; `failed` blocks; a
    # `not_evaluable_platform_trace` is release-blocking UNLESS every surface (facts, provenance,
    # report) discloses that provenance is not independently verified and makes no verification claim.
    infographic_release_blocking = True

    def _infographic_outcome():
        image_file = infographics[0] if infographics else None
        cls = classify_infographic_lineage(
            *infographics, prompt_file=infographic_prompt, image_file=image_file,
        )
        state = cls["state"]
        ev = dict(cls["evidence"])
        ev["verified_by_transcript"] = list(infographics)
        ev["transcript"] = str(_at_results(_TRANSCRIPT_REL))
        # Content-identity embedding is a separate, independent check.
        ev["embedding"] = (inspect_infographic_embedding(report_name, image_file)
                           if image_file is not None
                           else {"state": "not_evaluable", "detail": "no infographic file named"})
        facts_state, prov_state, report_discloses, missing = _read_infographic_disclosure(
            narrative_facts, narrative_provenance, report_name, state)
        ev["facts_state"] = facts_state
        ev["provenance_state"] = prov_state
        ev["report_discloses_non_verification"] = report_discloses
        ev["disclosure_missing"] = missing
        return state, ev, missing

    if infographics:
        try:
            _ig_state, _ig_ev, _ig_missing = _infographic_outcome()
        except (GateFailure, OSError, ValueError) as exc:
            states["infographics_generated_by_tool"] = LINEAGE_FAILED
            outcome["infographics_generated_by_tool"] = False
            reasons["infographics_generated_by_tool_reason"] = f"{type(exc).__name__}: {exc}"
        else:
            evidence["infographics_generated_by_tool"] = _ig_ev
            states["infographics_generated_by_tool"] = _ig_state
            _embed_state = _ig_ev.get("embedding", {}).get("state")
            if _ig_state == LINEAGE_VERIFIED:
                if _embed_state != "pass":
                    outcome["infographics_generated_by_tool"] = False
                    states["infographics_generated_by_tool"] = LINEAGE_FAILED
                    reasons["infographics_generated_by_tool_reason"] = (
                        f"verified lineage but content-identity embedding is {_embed_state!r}: "
                        f"{_ig_ev.get('embedding', {}).get('detail', '')}")
                elif _ig_missing:
                    outcome["infographics_generated_by_tool"] = False
                    states["infographics_generated_by_tool"] = LINEAGE_FAILED
                    reasons["infographics_generated_by_tool_reason"] = (
                        "verified lineage but surfaces are inconsistent: " + "; ".join(_ig_missing))
                else:
                    outcome["infographics_generated_by_tool"] = True
            elif _ig_state == LINEAGE_NOT_EVALUABLE_PLATFORM_TRACE:
                outcome["infographics_generated_by_tool"] = False   # never a boolean pass
                blockers = list(_ig_missing)
                if _embed_state == "fail":
                    blockers.append("content-identity embedding failed: "
                                    f"{_ig_ev.get('embedding', {}).get('detail', '')}")
                if blockers:
                    reasons["infographics_generated_by_tool_reason"] = (
                        "not_evaluable_platform_trace but incomplete/invalid (release-blocking): "
                        + "; ".join(blockers))
                else:
                    infographic_release_blocking = False   # genuine, fully disclosed -> non-blocking
                    reasons["infographics_generated_by_tool_reason"] = _ig_ev.get("reason", "")
            else:   # LINEAGE_FAILED
                outcome["infographics_generated_by_tool"] = False
                reasons["infographics_generated_by_tool_reason"] = _ig_ev.get("reason", "")

    states["figures_embedded"] = embedded[0]

    # Compute the release-blocking set BEFORE writing so the receipt records what blocked release.
    failed = sorted(k for k, v in outcome.items() if not v)
    # OT-R4: a genuine, fully disclosed not_evaluable_platform_trace lineage is non-blocking.
    if not infographic_release_blocking and "infographics_generated_by_tool" in failed:
        failed.remove("infographics_generated_by_tool")
    if embedded[0] != "pass" and figures:
        failed.append("figures_embedded")
    failed = sorted(set(failed))

    receipt = {"schema": RECEIPT_SCHEMA_V3,
               "generated_by": "report_qc.write_receipt",
               "validation_context": validation_context or {"scope": "runtime"},
               **outcome, "states": states, "figures_embedded": embedded[0],
               "release_blocking_gates": failed,
               **reasons, "evidence": evidence}
    p = _at_results(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")

    if failed and strict:
        raise GateFailure(
            f"the run did not pass its own receipt: {', '.join(failed)}. Written to {p} with a "
            f"_reason for each. Fix the run; do not edit the receipt."
        )
    return p
