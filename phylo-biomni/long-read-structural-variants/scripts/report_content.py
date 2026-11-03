#!/usr/bin/env python3
"""Derive report facts and presentation-independent content from current SV artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys


SECTIONS = (
    "Task Context",
    "Methods & Sources",
    "Results",
    "Conclusions & Interpretation",
    "Limitations",
    "References",
)
REFERENCE_CATALOG = pathlib.Path(__file__).resolve().parent.parent / "references" / "report_references.json"


def _load(path: pathlib.Path) -> dict | list:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, (dict, list)):
        raise ValueError(f"expected JSON object or array: {path}")
    return value


def _references() -> list[dict]:
    catalog = _load(REFERENCE_CATALOG)
    if not isinstance(catalog, dict) or catalog.get("schema") != "phylo-report-references/1":
        raise ValueError("long-read SV report reference catalog has an unsupported schema")
    references = catalog.get("references")
    required = {"id", "authors", "year", "title", "venue", "locator"}
    if not isinstance(references, list) or not references:
        raise ValueError("long-read SV report reference catalog is empty")
    if any(not isinstance(item, dict) or set(item) != required for item in references):
        raise ValueError("long-read SV report references must use the complete canonical field set")
    if len({item["id"] for item in references}) != len(references):
        raise ValueError("long-read SV report reference identifiers must be unique")
    if any(not str(item[field]).strip() for item in references for field in required):
        raise ValueError("long-read SV report reference records cannot contain blank fields")
    return [dict(item) for item in references]


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_agreement(facts: dict, provenance: dict, selection: dict) -> None:
    for key in ("execution_mode", "requested_dataset", "package_data_used"):
        values = (facts.get(key), provenance.get(key), selection.get(key))
        if values[0] != values[1] or values[1] != values[2]:
            raise ValueError(f"facts/provenance/input selection disagree on {key}: {values}")
    manifest_hash = selection.get("manifest_sha256")
    if facts.get("input_selection_sha256") != manifest_hash:
        raise ValueError("facts disagree with input-selection manifest hash")
    if provenance.get("input_selection_sha256") != manifest_hash:
        raise ValueError("provenance disagrees with input-selection manifest hash")
    if facts.get("reference_build_mismatch_violations") != 0:
        raise ValueError("cannot finalize a report with a reference-build mismatch")
    if facts.get("vcf_valid_indexed") is not True:
        raise ValueError("cannot finalize a report for invalid or unindexed VCFs")


def derive(out: pathlib.Path, contract: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    os.environ["BIOMNI_RESULTS"] = str(out)
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import report_qc
    import svlib

    figures = report_qc.assert_figures("figures/manifest.json")
    facts_path = report_qc.write_facts_from_artifact(
        "report_facts.json",
        source="sv/facts_payload.json",
        figures=figures,
        contract=contract,
    )
    facts = _load(facts_path)
    provenance_path = out / "sv" / "provenance.json"
    selection_path = out / "input_selection.json"
    provenance = _load(provenance_path)
    selection = _load(selection_path)
    infographic_claims_path = out / "sv" / "infographic_claims.json"
    infographic_claims = _load(infographic_claims_path)
    infographic_prompt_path = out / "sv" / "infographic_prompt.txt"
    if not all(isinstance(item, dict) for item in (facts, provenance, selection)):
        raise ValueError("facts, provenance, and input selection must be JSON objects")
    _require_agreement(facts, provenance, selection)
    expected_claims, expected_prompt = svlib.facts_derived_infographic(facts)
    if infographic_claims != expected_claims:
        raise ValueError("infographic claim ledger is not the exact facts-derived artifact")
    if infographic_prompt_path.read_text() != expected_prompt:
        raise ValueError("infographic prompt is not the exact facts-derived prompt")

    second = str(facts["second_caller"])
    metrics = [
        {"label": "Sniffles2 calls", "value": int(facts["sniffles2_sv_count"])},
        {"label": f"{second} calls", "value": int(facts["second_caller_sv_count"])},
        {"label": "Concordant calls", "value": int(facts["shared_sv_count"])},
        {"label": "Sniffles2-only calls", "value": int(facts["sniffles2_unique_count"])},
        {"label": f"{second}-only calls", "value": int(facts["second_caller_unique_count"])},
    ]
    content = {
        "schema": "phylo-report-content/1",
        "report_name": "report_long-read-structural-variants.pdf",
        "title": "Long-read structural-variant analysis",
        "subtitle": str(facts["requested_dataset"]),
        "headline": str(facts["headline_sentence"]),
        "presentation": None,
        "references": _references(),
        "sections": [
            {
                "heading": "Task Context",
                "paragraphs": [
                    "This report summarizes a hypothesis-generating, dual-caller structural-variant analysis of the explicitly selected aligned long-read data.",
                    f"The declared platform is {facts.get('platform') or 'not recorded'} and the reference build is {facts.get('reference_build') or 'not recorded'}.",
                ],
            },
            {
                "heading": "Methods & Sources",
                "paragraphs": [
                    f"Sniffles2 and {second} were run as independent caller routes. Both callsets were normalized, bgzip-compressed, tabix-indexed, and checked for BAM/reference identity before descriptive comparison.",
                    "Concordance uses the production class-aware, one-to-one breakpoint matching implementation. Agreement is descriptive and is not a truth-set accuracy estimate.",
                ],
            },
            {
                "heading": "Results",
                "paragraphs": [str(facts["headline_sentence"])],
                "metrics": metrics,
                "figures": figures,
            },
            {
                "heading": "Conclusions & Interpretation",
                "paragraphs": [
                    "The two normalized callsets and their caller-concordance inventory are suitable for candidate review and orthogonal follow-up.",
                    "Caller-shared variants provide descriptive robustness evidence; caller-unique variants remain candidates rather than errors, and no sensitivity, precision, or clinical claim is made.",
                ],
            },
            {
                "heading": "Limitations",
                "paragraphs": [
                    "Low depth, low variant-allele fraction, repetitive sequence, and alignment artifacts can reduce sensitivity or breakpoint precision.",
                    "Caller counts and concordance depend on caller versions, parameters, alignment, coverage, reference build, and variant representation.",
                    "BND and complex rearrangement representations are not directly comparable to length-bearing insertion, deletion, duplication, and inversion records.",
                    "Candidate calls require read-level and orthogonal review before biological or translational decisions.",
                ],
            },
            {
                "heading": "References",
            },
        ],
        "lineage": {
            "input_selection_sha256": str(facts["input_selection_sha256"]),
            "facts_sha256": _sha256(facts_path),
            "provenance_sha256": _sha256(provenance_path),
            "figure_manifest_sha256": _sha256(out / "figures" / "manifest.json"),
            "infographic_claims_sha256": _sha256(infographic_claims_path),
            "infographic_prompt_sha256": _sha256(infographic_prompt_path),
        },
    }
    if tuple(section["heading"] for section in content["sections"]) != SECTIONS:
        raise ValueError("report sections do not match the mandatory order")
    content_path = out / "report_content.json"
    content_path.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    structure = {
        "schema": "phylo-report-structure/1",
        "report_name": content["report_name"],
        "section_order": list(SECTIONS),
        "infographic": "workflow_infographic_long_read_sv.png",
        "figures": [item.get("file") for item in figures if item.get("file")],
        "report_content_sha256": _sha256(content_path),
    }
    structure_path = out / "report_structure.json"
    structure_path.write_text(
        json.dumps(structure, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return facts_path, content_path, structure_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-root", required=True, type=pathlib.Path)
    parser.add_argument("--contract", required=True, type=pathlib.Path)
    args = parser.parse_args()
    paths = derive(args.out_root.expanduser().resolve(strict=True), args.contract.resolve(strict=True))
    print(json.dumps({"derived": [str(path) for path in paths]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
