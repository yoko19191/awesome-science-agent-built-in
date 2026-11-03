#!/usr/bin/env python3
"""Fail closed unless the scATAC receipt can authorize terminal delivery."""

from __future__ import annotations

import hashlib
import json
import pathlib


REQUIRED_TRUE = (
    "execution_contract_satisfied",
    "outputs_appeared",
    "figure_contract_satisfied",
    "facts_artifact_verified",
    "report_at_results_root",
    "report_style_verified",
    "report_content_within_margins",
    "text_extracted",
    "pages_rendered",
    "report_sections_present",
    "stage_status_prose_verified",
    "report_facts_grounded",
    "source_assertions_verified",
    "infographic_lineage_verified",
)


class DeliveryBlocked(AssertionError):
    pass


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_deliverable_receipt(path: str | pathlib.Path) -> dict:
    receipt_path = pathlib.Path(path)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeliveryBlocked(f"cannot read receipt: {exc}") from exc
    if receipt.get("schema") != "phylo-run-receipt/3" or receipt.get("generated_by") != (
        "report_qc.write_receipt"
    ):
        raise DeliveryBlocked("receipt has the wrong schema or generator")
    evidence = receipt.get("evidence")
    failed = []
    if not isinstance(evidence, dict):
        failed.append("evidence")
        evidence = {}
    for key in REQUIRED_TRUE:
        if (
            receipt.get(key) is not True
            or not isinstance(evidence.get(key), dict)
            or not evidence.get(key)
        ):
            failed.append(key)
    if receipt.get("figures_embedded") != "pass":
        failed.append("figures_embedded")
    if failed:
        raise DeliveryBlocked("receipt is non-deliverable: " + ", ".join(sorted(set(failed))))
    return receipt


def assert_delivery_authorization(
    authorization_path: str | pathlib.Path,
    receipt_path: str | pathlib.Path,
    report_path: str | pathlib.Path,
) -> dict:
    authorization_file = pathlib.Path(authorization_path)
    receipt_file = pathlib.Path(receipt_path)
    report_file = pathlib.Path(report_path)
    try:
        authorization = json.loads(authorization_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeliveryBlocked(f"cannot read authorization: {exc}") from exc
    receipt = assert_deliverable_receipt(receipt_file)
    expected_message = f"scATAC Multiome analysis completed. Report: {report_file.name}."
    checks = {
        "schema": authorization.get("schema") == "scatac-multiome-delivery-authorization/1",
        "report": authorization.get("report") == report_file.name,
        "report_sha256": authorization.get("report_sha256") == _sha256(report_file),
        "receipt": authorization.get("receipt") == receipt_file.name,
        "receipt_sha256": authorization.get("receipt_sha256") == _sha256(receipt_file),
        "receipt_schema": authorization.get("receipt_schema") == receipt.get("schema"),
        "terminal_message": authorization.get("terminal_message") == expected_message,
        "terminal_message_sha256": authorization.get("terminal_message_sha256") == hashlib.sha256(
            expected_message.encode("utf-8")
        ).hexdigest(),
    }
    failed = [key for key, passed in checks.items() if not passed]
    if failed:
        raise DeliveryBlocked("authorization binding failed: " + ", ".join(failed))
    return authorization
