"""Release-readiness mutations for general inputs, narrative reports, and root lineage."""
from __future__ import annotations

import json
import os
import pathlib

import pytest


PKG = pathlib.Path(__file__).resolve().parent.parent.parent


def test_starting_prompt_is_only_a_short_research_question():
    skill = (PKG / "SKILL.md").read_text(encoding="utf-8")
    line = next(item for item in skill.splitlines() if item.startswith("starting-prompt:"))
    assert line.rstrip().endswith('?"')
    assert "Deliverables:" not in line
    assert "Decision context:" not in line
    assert len(line) < 360


def _write_runtime_bundle(root: pathlib.Path, disease_id: str = "EFO_0000249") -> list[str]:
    from PIL import Image
    ledger = root / "operation_ledger.json"
    ledger.write_text(json.dumps({"schema": "test-ledger", "events": []}), encoding="utf-8")
    import hashlib
    ledger_sha = hashlib.sha256(ledger.read_bytes()).hexdigest()
    facts = {
        "source_mode": "fixture",
        "operation_ledger_sha256": ledger_sha,
        "disease_id": disease_id,
        "disease_name": "test disease",
        "release_label": "99.99",
        "top_targets": [{"approved_symbol": "TEST1"}],
        "total_associated_targets": 1234,
        "evidence_count": 25,
        "studies": [],
        "validation_errors": [],
    }
    provenance = {
        "schema": "phylo-ot-provenance/1",
        "source_mode": "fixture",
        "operation_ledger_sha256": ledger_sha,
        "disease": {"id": disease_id, "name": "test disease"},
        "validation_errors": [],
    }
    (root / "report_facts.json").write_text(json.dumps(facts), encoding="utf-8")
    (root / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    (root / "target_ranking.csv").write_text("rank,symbol\n1,TEST1\n", encoding="utf-8")
    (root / "evidence_rows.csv").write_text("score\n1.0\n", encoding="utf-8")
    (root / "figure_payloads.json").write_text("{}", encoding="utf-8")
    (root / "figures").mkdir()
    for name in ("figure_1_target_ranking.png", "figure_2_datatype_heatmap.png"):
        Image.new("RGB", (400, 240), "white").save(root / "figures" / name)
    Image.new("RGB", (400, 240), "white").save(root / "infographic_open_targets_workflow.png")
    (root / "report_open-targets.pdf").write_bytes(b"%PDF-test-root-lineage")
    return [
        "target_ranking.csv", "evidence_rows.csv", "operation_ledger.json",
        "provenance.json", "report_facts.json", "figure_payloads.json",
        "figures/figure_1_target_ranking.png", "figures/figure_2_datatype_heatmap.png",
        "infographic_open_targets_workflow.png", "report_open-targets.pdf",
    ]


def test_non_demo_disease_uses_runtime_source_witnesses_and_root_lineage(tmp_path):
    import report_qc
    paths = _write_runtime_bundle(tmp_path)
    prior = report_qc.RESULTS
    report_qc.RESULTS = tmp_path
    try:
        report_qc.finalize_root_bundle(paths)
        contract = json.loads((PKG / "skill_contract.json").read_text(encoding="utf-8"))
        witnesses = report_qc.assert_source_witnesses(contract)
        root = report_qc.assert_root_bundle_lineage("root_bundle.json", paths)
    finally:
        report_qc.RESULTS = prior
    assert {item["id"] for item in witnesses} == {
        "runtime-facts", "runtime-provenance", "runtime-operation-ledger", "root-bundle"
    }
    assert root["source_mode"] == "fixture"
    assert root["live_claim_eligible"] is False


def test_report_content_contract_requires_sections_and_runtime_facts(tmp_path):
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate
    import report_qc

    facts = {
        "disease_id": "EFO_0000249", "disease_name": "Alzheimer disease",
        "release_label": "26.06", "top_targets": [{"approved_symbol": "APP"}],
        "total_associated_targets": 13367, "evidence_count": 39846,
    }
    provenance = {"disease": {"id": "EFO_0000249"}}
    facts_path = tmp_path / "report_facts.json"
    provenance_path = tmp_path / "provenance.json"
    facts_path.write_text(json.dumps(facts), encoding="utf-8")
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    report = tmp_path / "report.pdf"
    styles = getSampleStyleSheet()
    base = (
        "Alzheimer disease EFO_0000249 release 26.06 APP 1 of 13367 targets and 39846 "
        "evidence records. This is a bounded association prioritization analysis, not causal proof. "
    )
    lengths = {
        "Task Context": 4, "Methods and Sources": 7, "Results": 9,
        "Conclusions": 5, "Scientific Caveats": 6, "References": 3,
        "Suggested Next Steps": 4,
    }
    story = []
    for heading in report_qc.OPEN_TARGETS_REPORT_SECTION_ORDER:
        story.extend([Paragraph(heading, styles["Heading2"]),
                      Paragraph(base * lengths[heading], styles["BodyText"])])
    SimpleDocTemplate(str(report)).build(story)
    evidence = report_qc.assert_report_content_contract(report, facts_path, provenance_path)
    assert evidence["page_count"] >= 1

    sparse = tmp_path / "sparse.pdf"
    SimpleDocTemplate(str(sparse)).build([
        Paragraph(heading, styles["Heading2"])
        for heading in report_qc.OPEN_TARGETS_REPORT_SECTION_ORDER
    ])
    with pytest.raises(report_qc.GateFailure):
        report_qc.assert_report_content_contract(sparse, facts_path, provenance_path)
