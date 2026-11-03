"""Bind landscape facts and report inspection records to actual runtime artifacts.

These are domain accounting and artifact-integrity checks, not scientific acceptance.
Visual review remains an attributed attestation by the model/person viewing the pages.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from PIL import Image, ImageStat
try:
    from validate_report_output import validate_report_output
except ImportError:  # pragma: no cover
    from scripts.validate_report_output import validate_report_output
from pathlib import Path


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def artifact(root, path):
    root = Path(root).resolve()
    path = Path(path)
    path = (path if path.is_absolute() else root / path).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"Artifact must be a file under the results directory: {path}")
    return path


def evidence(root, path):
    path = artifact(root, path)
    return {"file": str(path.relative_to(Path(root).resolve())),
            "bytes": path.stat().st_size, "sha256": sha256(path)}


def validate_accounting(facts):
    definitions = json.loads((Path(__file__).resolve().parents[1] /
                              "assets/fact_definitions.json").read_text())
    for headline in definitions["headline_definitions"]:
        if facts.get(headline["field"]) is None or not headline["operational_definition"]:
            raise ValueError(f"Missing defined headline fact: {headline['field']}")
    for group in definitions["partition_groups"]:
        denominator = facts.get(group["denominator_field"])
        members = [facts.get(field) for field in group["member_fields"]]
        if not all(type(value) is int and value >= 0 for value in [denominator, *members]):
            raise ValueError(f"Invalid count in {group['name']}")
        if sum(members) != denominator:
            raise ValueError(f"Partition does not account: {group['name']}")
    return definitions


def write_report_facts(results_dir):
    root = Path(results_dir).resolve()
    source = artifact(root, "facts_payload.json")
    facts = json.loads(source.read_text())
    validate_accounting(facts)
    entries = json.loads(artifact(root, "figures/manifest.json").read_text())
    if not isinstance(entries, list) or not entries:
        raise ValueError("A nonempty figure inventory is required")
    files = []
    for entry in entries:
        if entry.get("file") is None:
            if not str(entry.get("reason", "")).strip():
                raise ValueError("An omitted figure requires a reason")
            continue
        path = artifact(root, entry["file"])
        if not str(entry.get("caption", "")).strip():
            raise ValueError("Every figure requires a data-derived caption")
        with Image.open(path) as image:
            if min(image.size) < 100 or max(ImageStat.Stat(image.convert("RGB")).var) < 1:
                raise ValueError(f"Figure is empty or too small: {path}")
        files.append(evidence(root, path))
    facts["figures"] = entries
    facts["source_evidence"] = {"payload": evidence(root, source), "figures": files}
    dest = root / "report_facts.json"
    dest.write_text(json.dumps(facts, indent=2, sort_keys=True) + "\n")
    return dest


def prepare_report_review(results_dir, report_file="report_clinicaltrials-landscape.pdf"):
    """Render exact PDF bytes before inspection; refuse to overwrite prior inspection evidence."""
    for command in ("pdftoppm", "pdftotext"):
        if not shutil.which(command):
            raise RuntimeError(f"Report inspection requires Poppler command: {command}")
    root = Path(results_dir).resolve()
    report = artifact(root, report_file)
    review_dir = root / ("report-review-" + sha256(report)[:16])
    review_dir.mkdir(exist_ok=False)
    subprocess.run(["pdftoppm", "-r", "144", "-png", str(report), str(review_dir / "page")], check=True)
    paths = sorted(review_dir.glob("page-*.png"), key=lambda path: int(path.stem.split("-")[-1]))
    if not paths:
        raise ValueError("PDF produced no rendered pages")
    pages = [evidence(root, path) for path in paths]
    text_path = review_dir / "extracted-text.txt"
    subprocess.run(["pdftotext", "-layout", str(report), str(text_path)], check=True)
    snapshot = {"report": evidence(root, report), "text": evidence(root, text_path),
                "pages": pages, "method": "Poppler rendering and text extraction from the bound PDF"}
    (review_dir / "snapshot.json").write_text(json.dumps(snapshot, indent=2) + "\n")
    return snapshot


def write_report_review(results_dir, *, report_file, text_file, rendered_page_files,
                        reviewed_page_numbers, checks):
    """Verify unchanged inspection evidence and record a truthful visual-review attestation."""
    root = Path(results_dir).resolve()
    report = artifact(root, report_file)
    review_dir = root / ("report-review-" + sha256(report)[:16])
    snapshot = json.loads(artifact(root, review_dir / "snapshot.json").read_text())
    if snapshot["report"] != evidence(root, report) or snapshot["text"] != evidence(root, text_file):
        raise ValueError("Report or extracted text changed after inspection preparation")
    pages = [evidence(root, page) for page in rendered_page_files]
    if pages != snapshot["pages"]:
        raise ValueError("Rendered page inventory changed or is incomplete")
    if sorted(reviewed_page_numbers) != list(range(1, len(pages) + 1)):
        raise ValueError("Visual review must cover every page exactly once")
    visual = checks["visual_review"]
    # Re-run domain text/spec checks on bound artifacts; never trust caller-supplied pass flags.
    verified = validate_report_output(
        artifact(root, text_file), artifact(root, "report_facts.json"),
        artifact(root, "report_spec.json"), review_attestation=visual["attestation"],
        review_verdict=visual["verdict"], review_issues=visual["issues"],
        review_performed=visual["performed"])
    receipt = {"schema": "clinicaltrials-report-review/1", "report": evidence(root, report),
               "facts": evidence(root, "report_facts.json"), "spec": evidence(root, "report_spec.json"),
               "inspection": snapshot, "reviewed_page_numbers": reviewed_page_numbers,
               "checks": verified, "scientific_acceptance": "not established by these checks"}
    (root / "report_review.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if not verified["report_ready"]:
        raise ValueError("Report review failed; see report_review.json")
    return receipt
