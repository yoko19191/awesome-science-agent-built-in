#!/usr/bin/env python3
"""Verify that a report-producing cytometry task has its required terminal artifacts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path


REQUIRED_FILES = (
    "environment_state.csv",
    "gating_state.csv",
    "transform_contract.csv",
    "resolution_state.csv",
    "annotation_state.csv",
    "run_manifest.json",
    "report_claims.csv",
    "artifact_manifest.csv",
    "gating_review_receipt_state.csv",
    "resolution_review_receipt_state.csv",
    "annotation_review_receipt_state.csv",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", required=True)
    return parser.parse_args()


def first_state(path: Path) -> str:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            row = next(csv.DictReader(handle))
        return str(row.get("state", ""))
    except (OSError, StopIteration, csv.Error):
        return ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_first_row(path: Path) -> dict[str, str]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return dict(next(csv.DictReader(handle)))
    except (OSError, StopIteration, csv.Error):
        return {}


def validate_claim_sources(outdir: Path) -> list[str]:
    problems: list[str] = []
    claims_path = outdir / "report_claims.csv"
    if not claims_path.is_file():
        return problems
    try:
        with claims_path.open(newline="", encoding="utf-8") as handle:
            claims = list(csv.DictReader(handle))
    except (OSError, csv.Error):
        return ["report_claims.csv is unreadable"]
    for row in claims:
        source = str(row.get("source_artifact", "")).strip()
        if not source:
            problems.append(f"claim {row.get('claim', '?')} has no source artifact")
            continue
        source_path = (outdir / source).resolve()
        if outdir not in source_path.parents and source_path != outdir:
            problems.append(f"claim {row.get('claim', '?')} escapes the run directory")
        elif not source_path.is_file():
            problems.append(f"claim {row.get('claim', '?')} references missing {source}")
    return problems


def validate_artifact_manifest(outdir: Path) -> list[str]:
    """Verify recorded bytes and coverage before certifying deliverables."""
    root = outdir.resolve()
    problems: list[str] = []
    try:
        with (root / "artifact_manifest.csv").open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not {"artifact", "bytes", "sha256"} <= set(reader.fieldnames or []):
                return ["artifact manifest lacks required columns"]
            rows = list(reader)
    except (OSError, csv.Error, UnicodeError):
        return ["artifact manifest is unreadable"]
    seen: set[str] = set()
    for row in rows:
        relative = str(row.get("artifact", ""))
        path = root / relative
        if (not relative or Path(relative).is_absolute() or ".." in Path(relative).parts
                or not path.resolve().is_relative_to(root) or path.is_symlink()):
            problems.append("artifact manifest contains an unsafe path")
            continue
        if relative in seen:
            problems.append(f"artifact manifest repeats {relative}")
        seen.add(relative)
        # Completion is a derived verification receipt written after the manifest.
        # Older manifests may include it; it is not scientific evidence or self-attestation.
        if relative == "completion_state.csv":
            continue
        if relative == "artifact_manifest.csv":
            problems.append("artifact manifest cannot hash itself")
            continue
        try:
            expected_bytes = int(row.get("bytes", ""))
            expected_hash = str(row.get("sha256", ""))
            if (not path.is_file() or expected_bytes != path.stat().st_size
                    or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
                    or sha256_file(path) != expected_hash):
                problems.append(f"artifact manifest hash or size mismatch: {relative}")
        except (OSError, ValueError, TypeError):
            problems.append(f"artifact manifest entry is invalid: {relative}")
    required = set(REQUIRED_FILES) - {"artifact_manifest.csv"}
    required.update(str(path.relative_to(root)) for path in root.glob("*.pdf"))
    required.update(str(path.relative_to(root)) for path in root.rglob("*infographic*.png") if path.is_file())
    try:
        with (root / "report_claims.csv").open(newline="", encoding="utf-8") as handle:
            required.update(row.get("source_artifact", "") for row in csv.DictReader(handle))
    except (OSError, csv.Error, UnicodeError):
        problems.append("claim sources cannot be checked against artifact manifest")
    for relative in sorted(required - seen):
        problems.append(f"artifact manifest omits required evidence: {relative}")
    return problems


def validate_auto_receipt_state(root: Path, stage: str, row: dict[str, str]) -> list[str]:
    """An unattended gating application: no receipt is claimed, the state stays REVIEW, the row is
    bound to the persisted proposal it applied, and the QC uncertainty table it relied on is empty."""
    problems: list[str] = []
    if stage != "gating":
        problems.append(f"{stage} unattended application is not a supported checkpoint")
    if not str(row.get("state", "")).startswith("REVIEW_auto"):
        problems.append(f"{stage} unattended row must remain a REVIEW state, found {row.get('state', '')!r}")
    if str(row.get("receipt_file", "")).strip() or str(row.get("receipt_sha256", "")).strip():
        problems.append(f"{stage} unattended row claims a receipt")
    if not str(row.get("validation_reason", "")).strip():
        problems.append(f"{stage} unattended row has no reason")
    proposal = (root / str(row.get("proposal_file", ""))).resolve() if str(row.get("proposal_file", "")).strip() else None
    expected = str(row.get("proposal_sha256", "")).lower()
    if proposal is None or not proposal.is_relative_to(root) or not proposal.is_file():
        problems.append(f"{stage} unattended row proposal_file does not resolve inside the run")
    elif not re.fullmatch(r"[0-9a-f]{64}", expected) or sha256_file(proposal) != expected:
        problems.append(f"{stage} unattended row proposal hash does not match the persisted proposal")
    uncertainty_path = root / "qc_uncertainty.csv"
    try:
        with uncertainty_path.open(newline="", encoding="utf-8") as handle:
            uncertainties = list(csv.DictReader(handle))
    except (OSError, csv.Error, UnicodeError):
        problems.append(f"{stage} unattended row requires a readable qc_uncertainty.csv")
        return problems
    if uncertainties:
        problems.append(f"{stage} unattended application coexists with {len(uncertainties)} recorded QC uncertainty(ies)")
    return problems


def validate_receipt_provenance(outdir: Path) -> list[str]:
    """Reconcile derived state against original receipts; never trust state alone."""
    problems: list[str] = []
    root = outdir.resolve()
    def local_file(relative: str) -> Path | None:
        path = (root / relative).resolve()
        return path if relative and path.is_relative_to(root) and path.is_file() else None
    for state_path in sorted(root.glob("*_review_receipt_state.csv")):
        row = read_first_row(state_path)
        stage = state_path.name.removesuffix("_review_receipt_state.csv")
        if str(row.get("source", "")).strip().lower() == "auto":
            problems.extend(validate_auto_receipt_state(root, stage, row))
            continue
        receipt_name = row.get("receipt_file")
        if not receipt_name:
            legacy = [name for name in (f"{stage}_review_receipt.json", f"{stage}_receipt.json") if local_file(name)]
            receipt_name = legacy[0] if len(legacy) == 1 else ""
        receipt_path = local_file(receipt_name)
        try:
            receipt = json.loads(receipt_path.read_text()) if receipt_path else None
            if not isinstance(receipt, dict):
                raise ValueError("missing or malformed receipt")
        except (OSError, ValueError):
            problems.append(f"{stage} original review receipt missing or malformed")
            continue
        if row.get("receipt_sha256") and sha256_file(receipt_path) != row["receipt_sha256"]:
            problems.append(f"{stage} original receipt hash changed")
        if receipt.get("stage") != stage or row.get("stage") != stage:
            problems.append(f"{stage} receipt stage does not match")
        for field in ("proposal_sha256", "decision_artifact_sha256", "reviewer", "reviewer_role",
                      "source_task_id", "source_message_id", "decision", "decision_reason", "created_at"):
            if not str(receipt.get(field, "")).strip() or str(receipt.get(field)) != row.get(field):
                problems.append(f"{stage} receipt state differs from original {field}")
        if receipt.get("reviewer_role") not in ("user", "immunologist", "scientist", "reviewer"):
            problems.append(f"{stage} receipt lacks a human reviewer role")
        if str(receipt.get("decision", "")).lower() not in ("apply", "approve", "accept", "select"):
            problems.append(f"{stage} receipt does not authorize application")
        if not re.fullmatch(r"msg_[0-9a-f]{16}", str(receipt.get("source_message_id", ""))):
            problems.append(f"{stage} receipt lacks an actual source message identifier")
        if not re.fullmatch(r"tsk_[A-Za-z0-9]+", str(receipt.get("source_task_id", ""))):
            problems.append(f"{stage} receipt lacks an actual source task identifier")
        for file_field, hash_field in (("proposal_file", "proposal_sha256"),
                                      ("decision_artifact", "decision_artifact_sha256")):
            path = local_file(str(row.get(file_field, "")))
            expected = str(receipt.get(hash_field, "")).lower()
            if path is None:
                problems.append(f"{stage} receipt {file_field} does not resolve inside the run")
            elif not re.fullmatch(r"[0-9a-f]{64}", expected) or sha256_file(path) != expected:
                problems.append(f"{stage} original receipt {file_field} hash does not match")
    return problems


def validate_rare_state(outdir: Path, manifest: dict[str, object]) -> list[str]:
    problems: list[str] = []
    rare_names = (
        "rare_population_candidates.csv", "rare_population_shortlist.csv",
        "rare_population_review.md", "rare_population_review_proposal.json",
        "rare_population_decision.csv", "rare_population_review_receipt_state.csv",
        "rare_population_state.csv", "rare_population_assignments.csv",
        "sce_rare_candidates.rds", "sce_rare_refined.rds",
    )
    present = [name for name in rare_names if (outdir / name).exists()]
    states = manifest.get("analysis_state", {})
    if not isinstance(states, dict):
        return ["manifest analysis_state is malformed"]
    if present and states.get("rare_population") == "NOT_REQUIRED":
        problems.append("rare artifacts coexist with NOT_REQUIRED")
    rare = manifest.get("rare_population")
    if isinstance(rare, dict) and rare.get("applied"):
        if not str(rare.get("workflow_state", "")).startswith("PASS"):
            problems.append("applied rare artifacts lack a complete workflow state")
        if not str(rare.get("selection_state", "")).startswith("REVIEW_rare_"):
            problems.append("applied rare artifacts lack an explicit selection state")
        identity = str(rare.get("identity_validation_state", ""))
        if not identity.startswith(("NOT_EVALUABLE_rare_identity", "PASS_rare_identity")):
            problems.append("applied rare artifacts lack an identity-validation state")
    return problems


def main() -> int:
    args = parse_args()
    outdir = Path(args.outdir).resolve()
    missing = [name for name in REQUIRED_FILES if not (outdir / name).is_file()]
    reports = sorted(outdir.glob("*.pdf"))
    infographics = sorted(path for path in outdir.rglob("*infographic*.png") if path.is_file())
    if not reports:
        missing.append("report PDF")
    if not infographics:
        missing.append("infographic PNG")

    problems: list[str] = []
    environment_state = first_state(outdir / "environment_state.csv")
    if environment_state and not environment_state.startswith("PASS"):
        problems.append(f"environment state is {environment_state}")

    manifest_path = outdir / "run_manifest.json"
    overall_state = ""
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            overall_state = str(manifest.get("overall_state", ""))
            if overall_state == "INCOMPLETE" or not overall_state:
                problems.append(f"manifest overall_state is {overall_state or 'missing'}")
            problems.extend(validate_rare_state(outdir, manifest))
        except (OSError, json.JSONDecodeError):
            problems.append("run_manifest.json is unreadable")

    problems.extend(validate_artifact_manifest(outdir))
    problems.extend(validate_claim_sources(outdir))
    problems.extend(validate_receipt_provenance(outdir))

    state = "PASS_deliverables_complete" if not missing and not problems else "INCOMPLETE_run_deliverables"
    rationale = "; ".join([*(f"missing {name}" for name in missing), *problems]) or (
        f"Required artifacts exist; manifest overall_state is {overall_state}."
    )
    with (outdir / "completion_state.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("state", "reports", "infographics", "rationale"))
        writer.writeheader()
        writer.writerow({
            "state": state,
            "reports": len(reports),
            "infographics": len(infographics),
            "rationale": rationale,
        })
    if state.startswith("INCOMPLETE"):
        print(rationale)
        return 3
    print(rationale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
