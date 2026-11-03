"""Derive the report assembly contract from report_facts.json."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPORT_SPEC_SCHEMA = "clinicaltrials-landscape-report-spec/v2"
REPORT_NAME = "report_clinicaltrials-landscape.pdf"
TOP_LEVEL_SECTIONS = [
    "Task Context",
    "Methods & Sources",
    "Results",
    "Conclusions & Interpretation",
    "Limitations",
]
PROTECTED_HEADINGS = [
    "Task Context",
    "Methods & Sources",
    "Results",
    "Conclusions & Interpretation",
    "Limitations",
    "Data Source",
    "Query Parameters",
    "Classification and Match Evidence",
    "Coverage Boundary",
    "Study Composition",
    "Intervention Categories",
    "Phase Distribution",
    "Sponsor Composition",
    "Geography",
    "Enrollment",
    "Output Artifacts",
    "References",
]


def _facts_digest(facts_path: Path) -> str:
    return hashlib.sha256(facts_path.read_bytes()).hexdigest()


def build_report_spec(facts: dict, facts_sha256: str) -> dict:
    """Build a deterministic content, figure-order, and pagination specification."""
    required_facts = (
        "boundary_statement",
        "limitation_statement",
        "record_asset_boundary_statement",
        "match_evidence_availability_sentence",
        "sponsor_composition_sentence",
        "figures",
    )
    missing = [field for field in required_facts if not facts.get(field)]
    if missing:
        raise ValueError("report facts missing required fields: " + ", ".join(missing))

    figures = [
        {
            "number": 1,
            "kind": "GenerateImage infographic",
            "file": "infographic.png",
            "placement": "on page 1 after Task Context and before every analytical figure",
            "keep_together": True,
        }
    ]
    for figure in facts["figures"]:
        if figure.get("file"):
            figures.append(
                {
                    "number": len(figures) + 1,
                    "kind": "analytical result",
                    "file": figure["file"],
                    "caption": figure["caption"],
                    "step": figure["step"],
                    "keep_together": True,
                }
            )

    return {
        "schema": REPORT_SPEC_SCHEMA,
        "report_name": REPORT_NAME,
        "style_provider": "pdf-report-generation",
        "facts_artifact": "report_facts.json",
        "facts_sha256": facts_sha256,
        "top_level_sections": TOP_LEVEL_SECTIONS,
        "required_verbatim": {
            "boundary_statement": {
                "text": facts["boundary_statement"],
                "minimum_occurrences": 1,
            },
            "limitation_statement": {
                "text": facts["limitation_statement"],
                "minimum_occurrences": 1,
            },
            "record_asset_boundary_statement": {
                "text": facts["record_asset_boundary_statement"],
                "minimum_occurrences": 1,
            },
            "match_evidence_availability_sentence": {
                "text": facts["match_evidence_availability_sentence"],
                "sections": ["Methods & Sources", "Results"],
                "minimum_occurrences": 2,
            },
            "sponsor_composition_sentence": {
                "text": facts["sponsor_composition_sentence"],
                "minimum_occurrences": 1,
            },
        },
        "figures": figures,
        "layout_constraints": {
            "first_substantive_visual": "infographic.png",
            "protected_headings": PROTECTED_HEADINGS,
            "heading_rule": "keep every protected heading with substantive following content",
            "reportlab_heading_style": "set keepWithNext=1 on every protected heading style",
            "heading_before_keep_together": "when a heading precedes a KeepTogether figure or callout, include the heading inside that same KeepTogether block; keepWithNext alone is insufficient",
            "figure_rule": "keep each figure image and caption together",
            "minimum_normalized_page_characters": 120,
        },
        "language_constraints": {
            "dataset_noun": "registered records",
            "coverage_claim": "complete only within the declared source, query, statuses, and cutoff",
            "forbid_global_exhaustiveness": True,
            "forbid_strategic_or_commercial_inference": True,
            "forbid_record_to_asset_inference": True,
        },
    }


def write_report_spec(results_dir=".", filename="report_spec.json") -> dict:
    results = Path(results_dir)
    facts_path = results / "report_facts.json"
    if not facts_path.is_file():
        raise ValueError(f"report facts artifact is missing: {facts_path}")
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    if not isinstance(facts, dict):
        raise ValueError("report_facts.json must contain a JSON object")
    spec = build_report_spec(facts, _facts_digest(facts_path))
    output_path = results / filename
    output_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    print(f"\u2713 Wrote report specification: {output_path}")
    return spec


if __name__ == "__main__":  # pragma: no cover
    import sys

    write_report_spec(sys.argv[1] if len(sys.argv) > 1 else ".")
