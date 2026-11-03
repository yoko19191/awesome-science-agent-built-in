#!/usr/bin/env python3
"""Bind joint-pipeline status prose to immutable run artifacts.

The package driver can exit successfully even when an inference block catches
an internal error and records ``status=failed``.  This module turns the command
event and the inner status artifacts into a small receipt *before* report
assembly, then requires the report to reproduce its canonical sentence.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from typing import Sequence

from execution_contract import (
    ExecutionContractError,
    command_outputs_for_selected_branches,
)


SCHEMA = "scatac-stage-status-receipt/2"
GENERATOR = "stage_status_grounding.write_stage_status_receipt"
RUN_ALL = "scripts/run_all.sh"
INFERENCE_OUTPUTS = {
    "milo": "milo_da_neighborhoods.csv",
    "pseudobulk_de": "pseudobulk_rna_de_by_celltype.csv",
    "atac_da": "top_DA_peaks_per_celltype.csv",
}
BLOCK_LABELS = {
    "milo": "Milo differential abundance",
    "pseudobulk_de": "pseudobulk RNA differential expression",
    "atac_da": "ATAC differential accessibility",
    "multivi": "MultiVI sensitivity analysis",
}


class StageStatusGroundingError(ValueError):
    """Raised when stage evidence is absent, inconsistent, or overstated."""


def _load_object(path: pathlib.Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StageStatusGroundingError(f"cannot load stage evidence {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StageStatusGroundingError(f"stage evidence must be a JSON object: {path}")
    return value


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _object_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _artifact(path: pathlib.Path, results: pathlib.Path) -> dict:
    if not path.is_file() or path.stat().st_size == 0:
        raise StageStatusGroundingError(f"required stage artifact is absent or empty: {path}")
    try:
        relative = path.resolve(strict=True).relative_to(results.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise StageStatusGroundingError(f"stage artifact escapes results root: {path}") from exc
    return {"path": relative.as_posix(), "bytes": path.stat().st_size, "sha256": _sha256(path)}


def _latest_run_all_event(log: dict) -> dict:
    if log.get("schema") != "phylo-qc-run-log/1" or log.get("generated_by") != "report_qc":
        raise StageStatusGroundingError("qc_run_log.json is not a report_qc-owned v1 ledger")
    events = log.get("events")
    if not isinstance(events, list):
        raise StageStatusGroundingError("qc_run_log.json has no events array")
    matches = [
        event for event in events
        if isinstance(event, dict)
        and event.get("type") == "command"
        and event.get("bundled_file") == RUN_ALL
    ]
    if not matches:
        raise StageStatusGroundingError("no package-owned scripts/run_all.sh event was recorded")
    if len(matches) != 1:
        raise StageStatusGroundingError(
            "exactly one package-owned scripts/run_all.sh event is required; "
            f"found {len(matches)}"
        )
    event = matches[0]
    if not isinstance(event.get("produced_artifacts"), list):
        raise StageStatusGroundingError("latest run_all event has no produced-artifact evidence")
    return event


def write_stage_status_receipt(
    results: pathlib.Path,
    *,
    selected_branch_ids: Sequence[str],
    path: str = "stage_status_receipt.json",
    qc_run_log: str = "qc_run_log.json",
    diffabund_status: str = "tables/diffabund_status.json",
    multivi_status: str = "tables/multivi_status.json",
) -> pathlib.Path:
    """Write the only report-stage verdict supported by current run evidence."""
    results = results.resolve(strict=True)
    log_path = results / qc_run_log
    diff_path = results / diffabund_status
    multivi_path = results / multivi_status
    log = _load_object(log_path)
    diff = _load_object(diff_path)
    multivi = _load_object(multivi_path)
    event = _latest_run_all_event(log)

    try:
        selected_outputs = command_outputs_for_selected_branches(selected_branch_ids)
    except ExecutionContractError as exc:
        raise StageStatusGroundingError(str(exc)) from exc
    if event.get("expected_outputs") != selected_outputs:
        raise StageStatusGroundingError(
            "run_all expected outputs do not match the selected branch contract"
        )

    produced = {
        item.get("path"): item
        for item in event["produced_artifacts"]
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    output_evidence: dict[str, dict] = {}
    missing_outputs: list[str] = []
    for output in selected_outputs:
        output_path = results / output
        if not output_path.is_file() or output_path.stat().st_size == 0:
            missing_outputs.append(output)
            continue
        current = _artifact(output_path, results)
        logged = produced.get(output)
        if not isinstance(logged, dict) or logged.get("sha256") != current["sha256"]:
            missing_outputs.append(output)
            continue
        output_evidence[output] = current

    design = diff.get("replicate_design")
    if not isinstance(design, dict) or not isinstance(design.get("sufficient"), bool):
        raise StageStatusGroundingError(
            "diffabund_status.json lacks a boolean replicate_design.sufficient"
        )
    replicate_sufficient = design["sufficient"]
    joint_selected = "integrated.h5mu" in selected_outputs
    atac_da_selected = INFERENCE_OUTPUTS["atac_da"] in selected_outputs
    applicability = {
        "milo": joint_selected and replicate_sufficient,
        "pseudobulk_de": joint_selected and replicate_sufficient,
        "atac_da": atac_da_selected,
    }

    inner_statuses: dict[str, str] = {}
    block_states: dict[str, dict[str, str]] = {}
    failed_blocks: list[str] = []
    for block, required in applicability.items():
        value = diff.get(block)
        state = value.get("status") if isinstance(value, dict) else None
        inner_statuses[block] = str(state) if isinstance(state, str) else "missing"
        reason = str(value.get("reason") or "") if isinstance(value, dict) else ""
        if required and state == "ran":
            block_states[block] = {"status": "ran", "applicability": "required"}
            output = INFERENCE_OUTPUTS[block]
            if output not in output_evidence:
                output_path = results / output
                if not output_path.is_file() or output_path.stat().st_size == 0:
                    if output not in missing_outputs:
                        missing_outputs.append(output)
                else:
                    output_evidence[output] = _artifact(output_path, results)
        elif not required and state == "skipped":
            block_states[block] = {
                "status": "skipped",
                "applicability": "not_applicable",
                "reason": reason or "not selected by the route/design contract",
            }
        else:
            failed_blocks.append(block)
            block_states[block] = {
                "status": inner_statuses[block],
                "applicability": "required" if required else "not_applicable",
                "reason": reason or "status contradicts the selected route/design contract",
            }

    multivi_state = multivi.get("status")
    if multivi_state not in {"ran", "skipped"}:
        failed_blocks.append("multivi")
        block_states["multivi"] = {
            "status": str(multivi_state) if isinstance(multivi_state, str) else "missing",
            "applicability": "optional_sensitivity",
            "reason": str(multivi.get("reason") or "invalid MultiVI status"),
        }
    elif multivi_state == "ran":
        block_states["multivi"] = {
            "status": "ran",
            "applicability": "optional_sensitivity",
        }
    else:
        block_states["multivi"] = {
            "status": "skipped",
            "applicability": "not_applicable",
            "reason": str(multivi.get("reason") or "configured assumption gate"),
        }

    run_all_succeeded = event.get("exit_status") == 0
    selected_succeeded = run_all_succeeded and not failed_blocks and not missing_outputs
    if selected_succeeded:
        ran = [BLOCK_LABELS[name] for name, item in block_states.items()
               if item["status"] == "ran"]
        skipped = [f"{BLOCK_LABELS[name]} ({item['reason']})"
                   for name, item in block_states.items() if item["status"] == "skipped"]
        sentence = "All selected pipeline branches completed successfully"
        if ran:
            sentence += ": " + ", ".join(ran) + " produced current-run outputs"
        sentence += "."
        if skipped:
            sentence += " Not applicable by the selected route/design: " + "; ".join(skipped) + "."
    else:
        problems = []
        if not run_all_succeeded:
            problems.append(f"run_all exited {event.get('exit_status')!r}")
        if failed_blocks:
            problems.append("non-success status: " + ", ".join(failed_blocks))
        if missing_outputs:
            problems.append("missing current-run output: " + ", ".join(missing_outputs))
        sentence = (
            "The selected pipeline branches did not complete successfully: "
            + "; ".join(problems)
            + "."
        )

    receipt = {
        "schema": SCHEMA,
        "generated_by": GENERATOR,
        "selected_branch_ids": list(selected_branch_ids),
        "selected_outputs": selected_outputs,
        "selected_branches_succeeded": selected_succeeded,
        "report_sentence": sentence,
        "run_all": {
            "bundled_file": RUN_ALL,
            "bundled_sha256": event.get("bundled_sha256"),
            "exit_status": event.get("exit_status"),
            "invocation_id": event.get("invocation_id"),
        },
        "inner_statuses": inner_statuses,
        "block_states": block_states,
        "replicate_design_sufficient": replicate_sufficient,
        "multivi_status": multivi_state,
        "failed_blocks": failed_blocks,
        "missing_outputs": missing_outputs,
        "evidence": {
            "run_all_event": {
                "qc_run_log": pathlib.Path(qc_run_log).as_posix(),
                "event": event,
                "event_sha256": _object_sha256(event),
            },
            "diffabund_status": _artifact(diff_path, results),
            "multivi_status": _artifact(multivi_path, results),
            "outputs": output_evidence,
        },
    }
    destination = results / path
    destination.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def load_stage_status_receipt(results: pathlib.Path, path: str = "stage_status_receipt.json") -> dict:
    """Load a receipt only when its source artifacts still match byte for byte."""
    results = results.resolve(strict=True)
    receipt_path = results / path
    receipt = _load_object(receipt_path)
    if receipt.get("schema") != SCHEMA or receipt.get("generated_by") != GENERATOR:
        raise StageStatusGroundingError(f"{receipt_path} is not a package-owned stage receipt")
    evidence = receipt.get("evidence")
    if not isinstance(evidence, dict):
        raise StageStatusGroundingError("stage receipt has no evidence object")
    run_event = evidence.get("run_all_event")
    if not isinstance(run_event, dict):
        raise StageStatusGroundingError("stage receipt has no run_all event evidence")
    raw_log_path = run_event.get("qc_run_log")
    recorded_event = run_event.get("event")
    recorded_hash = run_event.get("event_sha256")
    if (
        not isinstance(raw_log_path, str)
        or not raw_log_path.strip()
        or not isinstance(recorded_event, dict)
        or not isinstance(recorded_hash, str)
        or recorded_hash != _object_sha256(recorded_event)
    ):
        raise StageStatusGroundingError("stage receipt contains malformed run_all event evidence")
    try:
        log_path = (results / raw_log_path).resolve(strict=True)
        log_path.relative_to(results)
    except (FileNotFoundError, ValueError) as exc:
        raise StageStatusGroundingError("stage receipt qc_run_log escapes the results root") from exc
    current_event = _latest_run_all_event(_load_object(log_path))
    if current_event != recorded_event or _object_sha256(current_event) != recorded_hash:
        raise StageStatusGroundingError(
            "package-owned run_all event changed after stage receipt creation"
        )
    selected_branch_ids = receipt.get("selected_branch_ids")
    if not isinstance(selected_branch_ids, list) or not all(
        isinstance(value, str) and value.strip() for value in selected_branch_ids
    ):
        raise StageStatusGroundingError("stage receipt lacks selected branch IDs")
    try:
        selected_outputs = command_outputs_for_selected_branches(selected_branch_ids)
    except ExecutionContractError as exc:
        raise StageStatusGroundingError(str(exc)) from exc
    if (
        receipt.get("selected_outputs") != selected_outputs
        or current_event.get("expected_outputs") != selected_outputs
    ):
        raise StageStatusGroundingError(
            "stage receipt outputs do not match the selected branch contract"
        )
    items = [evidence.get("diffabund_status"), evidence.get("multivi_status")]
    outputs = evidence.get("outputs")
    if not isinstance(outputs, dict):
        raise StageStatusGroundingError("stage receipt has no output evidence")
    items.extend(outputs.values())
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise StageStatusGroundingError("stage receipt contains malformed artifact evidence")
        current = _artifact(results / item["path"], results)
        if current != item:
            raise StageStatusGroundingError(
                f"stage evidence changed after receipt creation: {item['path']}"
            )
    sentence = receipt.get("report_sentence")
    if not isinstance(sentence, str) or not sentence.strip():
        raise StageStatusGroundingError("stage receipt has no canonical report sentence")
    if receipt.get("selected_branches_succeeded") is True:
        if receipt.get("failed_blocks") or receipt.get("missing_outputs"):
            raise StageStatusGroundingError("stage receipt claims success while recording failures")
    return receipt


def derive_stage_status_statement(results: pathlib.Path, path: str = "stage_status_receipt.json") -> str:
    return str(load_stage_status_receipt(results, path)["report_sentence"])


def assert_stage_status_prose_supported(
    report_text: str,
    results: pathlib.Path,
    path: str = "stage_status_receipt.json",
) -> dict:
    """Require canonical receipt prose and reject the retained all-stages overclaim."""
    receipt = load_stage_status_receipt(results, path)
    normalized = " ".join(report_text.casefold().split())
    expected = " ".join(str(receipt["report_sentence"]).casefold().split())
    if expected not in normalized:
        raise StageStatusGroundingError("report omits the receipt-derived pipeline status sentence")
    for sentence in re.split(r"[.!?]+", normalized):
        if re.search(
            r"\ball (?:nine|9)\b.*\bstages?\b.*\b(?:success\w*|succeed\w*|complet\w*)\b",
            sentence,
        ):
            raise StageStatusGroundingError(
                "report claims all nine stages succeeded instead of using selected-branch status"
            )
    if receipt.get("selected_branches_succeeded") is not True:
        for sentence in re.split(r"[.!?]+", normalized):
            all_stages = re.search(r"\ball selected\b.*\b(?:branches?|stages?)\b", sentence)
            affirmative = re.search(
                r"\b(?:success\w*|succeed\w*|complet\w*)\b.*\ball selected\b.*\b(?:branches?|stages?)\b"
                r"|\ball selected\b.*\b(?:branches?|stages?)\b.*\b(?:success\w*|succeed\w*|complet\w*)\b",
                sentence,
            )
            negated = any(boundary in sentence for boundary in (
                "did not complete", "did not successfully", "not all",
                "failed to complete",
            ))
            if all_stages and affirmative and not negated:
                raise StageStatusGroundingError(
                    "report claims all selected branches succeeded although the stage receipt is false"
                )
    return {
        "stage_receipt": _artifact(results / path, results),
        "selected_branches_succeeded": receipt["selected_branches_succeeded"],
        "report_sentence": receipt["report_sentence"],
        "method": "canonical stage-receipt sentence plus prohibited-overclaim scan",
    }
