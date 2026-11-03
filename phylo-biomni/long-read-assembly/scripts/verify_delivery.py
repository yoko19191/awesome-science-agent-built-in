#!/usr/bin/env python3
"""Reject a long-read assembly receipt unless every mandatory outcome is deliverable."""

from __future__ import annotations

import json
import pathlib
import sys

RECEIPT_SCHEMA = "phylo-run-receipt/3"
RECEIPT_GENERATOR = "report_qc.write_receipt"
REQUIRED_TRUE_OUTCOMES = (
    "execution_contract_satisfied",
    "outputs_appeared",
    "figure_contract_satisfied",
    "facts_artifact_verified",
    "report_at_results_root",
    "report_style_verified",
    "text_extracted",
    "pages_rendered",
    "report_sections_present",
    "source_assertions_verified",
    "infographic_lineage_verified",
)
PLATFORM_TRACE_NOT_EVALUABLE = "not_evaluable_platform_trace"
PLATFORM_TOLERANT_OUTCOMES = {
    "infographic_lineage_verified",
}


class DeliveryBlocked(AssertionError):
    """Raised when a receipt cannot authorize delivery."""


def assert_deliverable_receipt(path: str | pathlib.Path) -> dict:
    receipt_path = pathlib.Path(path)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeliveryBlocked(f"cannot read delivery receipt at {receipt_path}: {exc}") from exc
    if not isinstance(receipt, dict):
        raise DeliveryBlocked("delivery receipt must be a JSON object")
    if receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("generated_by") != RECEIPT_GENERATOR:
        raise DeliveryBlocked("delivery receipt has the wrong schema or generator")

    def accepted(key: str) -> bool:
        value = receipt.get(key)
        if value is True:
            return True
        return (
            key in PLATFORM_TOLERANT_OUTCOMES
            and value == PLATFORM_TRACE_NOT_EVALUABLE
            and receipt.get("independent_post_run_campaign_audit_required") is True
        )

    failed = [key for key in REQUIRED_TRUE_OUTCOMES if not accepted(key)]
    evidence = receipt.get("evidence")
    if not isinstance(evidence, dict):
        failed.append("evidence")
    else:
        failed.extend(
            f"{key}:evidence"
            for key in REQUIRED_TRUE_OUTCOMES
            if (not isinstance(evidence.get(key), dict) or not evidence[key]
                or (
                    receipt.get(key) == PLATFORM_TRACE_NOT_EVALUABLE
                    and (
                        evidence[key].get("status") != PLATFORM_TRACE_NOT_EVALUABLE
                        or evidence[key].get(
                            "independent_post_run_campaign_audit_required"
                        ) is not True
                    )
                ))
        )
    if receipt.get("figures_embedded") != "pass":
        failed.append("figures_embedded")
    if failed:
        raise DeliveryBlocked(
            "receipt is non-deliverable; mandatory outcomes did not pass: " + ", ".join(failed)
        )
    return receipt


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
