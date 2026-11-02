#!/usr/bin/env python3
"""Verify the SCMI receipt and its only hash-bound terminal authorization."""

from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import re
import sys


RECEIPT_SCHEMA = "phylo-run-receipt/3"
RECEIPT_GENERATOR = "report_qc.write_receipt"
TRACE_NOT_EVALUABLE = "not_evaluable_platform_trace"
PLATFORM_TRACE_READ_ONLY_MODE = "0o444"
REQUIRED_TRUE_OUTCOMES = (
    "attached_package_tree_unchanged",
    "one_shot_execution_verified",
    "execution_contract_satisfied",
    "outputs_appeared",
    "figure_contract_satisfied",
    "facts_artifact_verified",
    "report_at_results_root",
    "report_style_verified",
    "text_extracted",
    "pages_rendered",
    "report_sections_present",
    "report_citations_numbered",
    "figure_local_narrative_verified",
    "source_assertions_verified",
    "infographic_lineage_verified",
)
PLATFORM_TOLERANT_OUTCOMES = {
    "infographic_lineage_verified",
}


class DeliveryBlocked(AssertionError):
    """A run cannot advertise successful delivery."""


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: pathlib.Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeliveryBlocked(f"cannot read JSON artifact at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DeliveryBlocked(f"JSON artifact must contain an object: {path}")
    return payload


def _valid_trace_source(source: object, *, allow_unavailable: bool) -> bool:
    if not isinstance(source, dict):
        return False
    status = source.get("status")
    if status == "origin_attested_read_only_platform_trace":
        return (
            source.get("source_id") == "fixed-platform-trace:0"
            and source.get("path") == "/mnt/platform/execution_trace/transcript.jsonl"
            and isinstance(source.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", source["sha256"]) is not None
            and isinstance(source.get("bytes"), int)
            and not isinstance(source.get("bytes"), bool)
            and source["bytes"] > 0
            and source.get("read_only_mode") == PLATFORM_TRACE_READ_ONLY_MODE
            and source.get("read_only_mount") is True
        )
    return allow_unavailable and status == "unavailable" and (
        source.get("accepted_source") == "fixed read-only platform trace mount only"
        and source.get("caller_writable_sources_rejected") is True
    )


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _valid_generateimage_item(item: object, *, allow_not_evaluable: bool) -> bool:
    if not isinstance(item, dict):
        return False
    source = item.get("trace_source")
    source_status = source.get("status") if isinstance(source, dict) else None
    if source_status == "unavailable":
        return allow_not_evaluable and (
            item.get("status") == TRACE_NOT_EVALUABLE
            and item.get("prompt_verification_status") == TRACE_NOT_EVALUABLE
            and item.get("independent_post_run_evidence_review_required") is True
        )
    verified_join = (
        isinstance(item.get("tool_call_id"), str)
        and re.fullmatch(
            r"(?:toolu_[A-Za-z0-9]{16,}|chatcmpl-tool-[A-Za-z0-9]+)",
            item["tool_call_id"],
        ) is not None
        and isinstance(item.get("result_id"), str)
        and re.fullmatch(
            r"(?:msg_[A-Za-z0-9]{16,}#content-\d+|platform-transcript-record:\d+)",
            item["result_id"],
        ) is not None
        and _is_sha256(item.get("call_record_sha256"))
        and _is_sha256(item.get("result_record_sha256"))
        and isinstance(item.get("requested_filename"), str)
        and item.get("requested_filename") == item.get("returned_filename")
    )
    if not verified_join:
        return False
    if item.get("prompt_verification_status") == "verified":
        return _is_sha256(item.get("prompt_sha256"))
    return allow_not_evaluable and (
        item.get("prompt_verification_status") == TRACE_NOT_EVALUABLE
        and item.get("independent_post_run_evidence_review_required") is True
    )


def assert_deliverable_receipt(path: str | pathlib.Path) -> dict:
    receipt_path = pathlib.Path(path)
    receipt = _load_json(receipt_path)
    if receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("generated_by") != RECEIPT_GENERATOR:
        raise DeliveryBlocked("delivery receipt has the wrong schema or generator")

    def accepted(key: str) -> bool:
        if receipt.get(key) is True:
            return True
        return (
            key in PLATFORM_TOLERANT_OUTCOMES
            and receipt.get(key) == TRACE_NOT_EVALUABLE
            and receipt.get("independent_post_run_evidence_review_required") is True
        )

    failed = [key for key in REQUIRED_TRUE_OUTCOMES if not accepted(key)]
    evidence = receipt.get("evidence")
    if not isinstance(evidence, dict):
        failed.append("evidence")
    else:
        for key in REQUIRED_TRUE_OUTCOMES:
            item = evidence.get(key)
            if not isinstance(item, dict) or not item:
                failed.append(f"{key}:evidence")
            elif receipt.get(key) == TRACE_NOT_EVALUABLE and (
                item.get("status") != TRACE_NOT_EVALUABLE
                or item.get("independent_post_run_evidence_review_required") is not True
            ):
                failed.append(f"{key}:evidence")
        for key in PLATFORM_TOLERANT_OUTCOMES:
            item = evidence.get(key)
            if not isinstance(item, dict):
                continue
            source = item.get("trace_source")
            allow_unavailable = receipt.get(key) == TRACE_NOT_EVALUABLE
            if not _valid_trace_source(source, allow_unavailable=allow_unavailable):
                failed.append(f"{key}:trace_source")
            if key == "infographic_lineage_verified":
                entries = item.get("items")
                snapshots = item.get("snapshots")
                if (
                    not isinstance(entries, list) or not entries
                    or not isinstance(snapshots, list) or len(snapshots) != len(entries)
                    or any(not isinstance(entry, dict) or entry.get("trace_source") != source
                           for entry in entries)
                ):
                    failed.append("infographic_lineage_verified:source_binding")
                elif any(
                    not isinstance(snapshot, dict)
                    or not isinstance(snapshot.get("trace_evidence"), dict)
                    or not _valid_trace_source(
                        snapshot["trace_evidence"].get("trace_source"),
                        allow_unavailable=True,
                    )
                    for snapshot in snapshots
                ):
                    failed.append("infographic_lineage_verified:snapshot_source")
                else:
                    snapshot_sources = {
                        snapshot.get("filename"): snapshot["trace_evidence"].get("trace_source")
                        for snapshot in snapshots
                    }
                    if any(
                        entry.get("snapshot_trace_source")
                        != snapshot_sources.get(entry.get("requested_filename"))
                        for entry in entries
                    ):
                        failed.append("infographic_lineage_verified:snapshot_source_binding")
                if isinstance(entries, list) and any(
                    not _valid_generateimage_item(
                        entry,
                        allow_not_evaluable=receipt.get(key) == TRACE_NOT_EVALUABLE,
                    )
                    for entry in entries
                ):
                    failed.append("infographic_lineage_verified:native_join")
                if isinstance(snapshots, list) and any(
                    not isinstance(snapshot, dict)
                    or not _valid_generateimage_item(
                        snapshot.get("trace_evidence"),
                        allow_not_evaluable=receipt.get(key) == TRACE_NOT_EVALUABLE,
                    )
                    for snapshot in snapshots
                ):
                    failed.append("infographic_lineage_verified:snapshot_native_join")
        styled = evidence.get("report_style_verified")
        if isinstance(styled, dict):
            selection = styled.get("selection")
            selection_evidence = styled.get("selection_evidence")
            if not isinstance(selection_evidence, dict):
                failed.append("report_style_verified:selection_evidence")
            else:
                source = selection_evidence.get("trace_source")
                if isinstance(source, dict) and source.get("status") == (
                    "origin_attested_read_only_platform_trace"
                ) and (
                    selection_evidence.get("transcript_path") != source.get("path")
                    or selection_evidence.get("transcript_sha256") != source.get("sha256")
                ):
                    failed.append("report_style_verified:selection_source_binding")
                if selection == "explicit_override":
                    if (
                        selection_evidence.get("source") != "origin_attested_user_message"
                        or not _valid_trace_source(source, allow_unavailable=False)
                        or not isinstance(selection_evidence.get("message_locator"), dict)
                        or not _is_sha256(selection_evidence.get("message_sha256"))
                        or not _is_sha256(selection_evidence.get("user_messages_sha256"))
                        or not isinstance(selection_evidence.get("matched_alias"), str)
                    ):
                        failed.append("report_style_verified:explicit_selection_source")
                elif selection == "contract_default":
                    unavailable = (
                        selection_evidence.get("explicit_override_evaluation")
                        == TRACE_NOT_EVALUABLE
                    )
                    if unavailable:
                        if not _valid_trace_source(source, allow_unavailable=True):
                            failed.append("report_style_verified:default_unavailable_source")
                    elif (
                        selection_evidence.get("status") != "verified_no_explicit_override"
                        or not _valid_trace_source(source, allow_unavailable=False)
                        or not _is_sha256(selection_evidence.get("user_messages_sha256"))
                    ):
                        failed.append("report_style_verified:default_selection_source")
                else:
                    failed.append("report_style_verified:selection")
    if receipt.get("figures_embedded") != "pass":
        failed.append("figures_embedded")
    if failed:
        raise DeliveryBlocked(
            "receipt is non-deliverable; mandatory outcomes did not pass: " + ", ".join(failed)
        )
    return receipt


def _scorecard_value(path: pathlib.Path, metric: str) -> str | None:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        raise DeliveryBlocked(f"cannot read scorecard at {path}: {exc}") from exc
    matches = [row.get("value") for row in rows if row.get("metric") == metric]
    if len(matches) != 1 or matches[0] in (None, ""):
        return None
    return str(matches[0])


def expected_terminal_message(
    report_path: pathlib.Path,
    facts_path: pathlib.Path,
    totalvi_status_path: pathlib.Path,
    scorecard_path: pathlib.Path,
) -> str:
    facts = _load_json(facts_path)
    status = _load_json(totalvi_status_path)
    n_cells = facts.get("n_joint_cells")
    n_samples = facts.get("n_samples")
    if isinstance(n_cells, bool) or not isinstance(n_cells, (int, float)):
        raise DeliveryBlocked("report facts have no numeric n_joint_cells")
    if isinstance(n_samples, bool) or not isinstance(n_samples, (int, float)):
        raise DeliveryBlocked("report facts have no numeric n_samples")
    if status.get("ran") is True:
        elbo = status.get("final_elbo_train")
        if isinstance(elbo, bool) or not isinstance(elbo, (int, float)):
            raise DeliveryBlocked("trained totalVI status has no numeric final_elbo_train")
        agreement = _scorecard_value(scorecard_path, "wnn_totalvi_knn_agreement")
        if agreement is None:
            raise DeliveryBlocked("scorecard has no unique wnn_totalvi_knn_agreement row")
        model_clause = (
            f"Final totalVI training ELBO: {float(elbo):.6g}; "
            f"WNN-totalVI kNN agreement: {float(agreement):.4f}."
        )
    else:
        reason = str(status.get("reason") or "not eligible")
        model_clause = f"totalVI was not run ({reason}); no kNN-agreement value is claimed."
    return (
        f"Single-cell multimodal integration completed for {int(n_cells)} cells across "
        f"{int(n_samples)} samples. {model_clause} Report: {report_path.name}."
    )


def assert_delivery_authorization(
    authorization_path: str | pathlib.Path,
    receipt_path: str | pathlib.Path,
    report_path: str | pathlib.Path,
    facts_path: str | pathlib.Path,
    totalvi_status_path: str | pathlib.Path,
    scorecard_path: str | pathlib.Path,
) -> dict:
    authorization_file = pathlib.Path(authorization_path)
    receipt_file = pathlib.Path(receipt_path)
    report_file = pathlib.Path(report_path)
    facts_file = pathlib.Path(facts_path)
    totalvi_file = pathlib.Path(totalvi_status_path)
    scorecard_file = pathlib.Path(scorecard_path)
    authorization = _load_json(authorization_file)
    if authorization.get("schema") != "single-cell-multimodal-delivery-authorization/1":
        raise DeliveryBlocked("delivery authorization has the wrong schema")
    receipt = assert_deliverable_receipt(receipt_file)
    package_identity = receipt["evidence"]["attached_package_tree_unchanged"]
    execution = receipt["evidence"]["one_shot_execution_verified"]
    expected_message = expected_terminal_message(
        report_file, facts_file, totalvi_file, scorecard_file,
    )
    checks = {
        "report": authorization.get("report") == report_file.name,
        "report_sha256": authorization.get("report_sha256") == _sha256(report_file),
        "receipt": authorization.get("receipt") == receipt_file.name,
        "receipt_sha256": authorization.get("receipt_sha256") == _sha256(receipt_file),
        "facts_sha256": authorization.get("facts_sha256") == _sha256(facts_file),
        "totalvi_status_sha256": authorization.get("totalvi_status_sha256") == _sha256(totalvi_file),
        "scorecard_sha256": authorization.get("scorecard_sha256") == _sha256(scorecard_file),
        "package_snapshot_sha256": authorization.get("package_snapshot_sha256") == package_identity.get("snapshot_sha256"),
        "package_tree_sha256": authorization.get("package_tree_sha256") == package_identity.get("after_tree_sha256"),
        "execution_seal_sha256": authorization.get("execution_seal_sha256") == execution.get("seal_sha256"),
        "terminal_message": authorization.get("terminal_message") == expected_message,
        "terminal_message_sha256": authorization.get("terminal_message_sha256") == hashlib.sha256(expected_message.encode()).hexdigest(),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise DeliveryBlocked(
            "delivery authorization is not bound to current artifacts: " + ", ".join(failed)
        )
    return authorization


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: verify_delivery.py /path/to/run_receipt.json", file=sys.stderr)
        return 2
    try:
        assert_deliverable_receipt(arguments[0])
    except DeliveryBlocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 1
    print("PASS: receipt authorizes delivery")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
