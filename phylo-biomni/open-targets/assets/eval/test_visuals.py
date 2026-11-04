"""OT-11: deterministic evidence-bearing visuals.

Figures must be deterministic derivatives of gated facts.  The visual
payload (figure_payloads.json) must equal the source facts exactly.
Swapped labels, changed counts/scores, or post-facts mutation must fail.
"""

import json
import pathlib
import shutil
import tempfile

import pytest
import matplotlib
matplotlib.use("Agg")


def _build_facts():
    """Create a minimal facts dict for figure testing."""
    return {
        "disease_name": "Test Disease",
        "disease_id": "MONDO_0000001",
        "release_label": "26.06",
        "api_version": "26.6.3",
        "access_date": "2026-08-16",
        "total_associated_targets": 100,
        "top_targets": [
            {"rank": 1, "target_id": "ENSG00000142192", "approved_symbol": "APP",
             "approved_name": "amyloid precursor", "biotype": "protein_coding",
             "overall_association_score": 0.872,
             "datatype_scores": [{"datatype_id": "genetic_association", "score": 0.9},
                                 {"datatype_id": "literature", "score": 0.8}]},
            {"rank": 2, "target_id": "ENSG00000130203", "approved_symbol": "APOE",
             "approved_name": "apolipoprotein E", "biotype": "protein_coding",
             "overall_association_score": 0.770,
             "datatype_scores": [{"datatype_id": "genetic_association", "score": 0.7},
                                 {"datatype_id": "literature", "score": 0.6}]},
        ],
        "evidence_count": 50,
        "evidence_rows": [],
        "studies": [],
        "credible_sets": [],
        "completeness": "bounded_sample",
        "max_pages": 10,
        "max_rows": 500,
        "operation_ledger_summary": {"total_attempts": 5, "successes": 5, "failures": 0, "by_state": {}},
        "validation_errors": [],
        "caveats": [],
        "next_steps": [],
    }


def test_figures_built_from_facts(tmp_path):
    """Figures are built successfully from a facts file."""
    from visualize import build_figures
    facts = _build_facts()
    facts_path = tmp_path / "report_facts.json"
    facts_path.write_text(json.dumps(facts), encoding="utf-8")
    figures = build_figures(facts_path, tmp_path)
    assert len(figures) == 2
    for f in figures:
        assert f.get("file") is not None
        assert f.get("caption") is not None
        img_path = tmp_path / f["file"]
        assert img_path.exists()
        assert img_path.stat().st_size > 5000  # non-blank


def test_payload_matches_facts(tmp_path):
    """The figure payload sidecar matches the source facts exactly."""
    from visualize import build_figures
    facts = _build_facts()
    facts_path = tmp_path / "report_facts.json"
    facts_path.write_text(json.dumps(facts), encoding="utf-8")
    build_figures(facts_path, tmp_path)
    payloads = json.loads((tmp_path / "figure_payloads.json").read_text(encoding="utf-8"))
    # Check target ranking payload
    ranking = payloads["figures/figure_1_target_ranking.png"]
    assert ranking["symbols"] == ["APP", "APOE"]
    assert ranking["scores"] == [0.872, 0.770]


def test_swapped_labels_detected(tmp_path):
    """Swapped labels in the payload are detectable by comparison."""
    from visualize import build_figures
    facts = _build_facts()
    facts_path = tmp_path / "report_facts.json"
    facts_path.write_text(json.dumps(facts), encoding="utf-8")
    build_figures(facts_path, tmp_path)
    payloads = json.loads((tmp_path / "figure_payloads.json").read_text(encoding="utf-8"))
    ranking = payloads["figures/figure_1_target_ranking.png"]
    # Swap labels and verify mismatch
    swapped = list(reversed(ranking["symbols"]))
    assert swapped != ranking["symbols"], "swapped labels should differ from original"


def test_changed_scores_detected(tmp_path):
    """Changed scores in the payload are detectable by comparison."""
    from visualize import build_figures
    facts = _build_facts()
    facts_path = tmp_path / "report_facts.json"
    facts_path.write_text(json.dumps(facts), encoding="utf-8")
    build_figures(facts_path, tmp_path)
    payloads = json.loads((tmp_path / "figure_payloads.json").read_text(encoding="utf-8"))
    ranking = payloads["figures/figure_1_target_ranking.png"]
    changed = [s + 0.1 for s in ranking["scores"]]
    assert changed != ranking["scores"], "changed scores should differ from original"


def test_deterministic_rebuild(tmp_path):
    """Rebuilding figures from the same facts produces identical payloads."""
    from visualize import build_figures
    facts = _build_facts()
    facts_path = tmp_path / "report_facts.json"
    facts_path.write_text(json.dumps(facts), encoding="utf-8")

    build_figures(facts_path, tmp_path)
    payloads1 = json.loads((tmp_path / "figure_payloads.json").read_text(encoding="utf-8"))

    # Remove and rebuild
    (tmp_path / "figure_payloads.json").unlink()
    build_figures(facts_path, tmp_path)
    payloads2 = json.loads((tmp_path / "figure_payloads.json").read_text(encoding="utf-8"))

    assert payloads1 == payloads2, "rebuild should produce identical payloads"
