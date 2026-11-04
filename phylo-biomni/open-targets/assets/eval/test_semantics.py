"""OT-07/OT-08: discriminated scientific semantics + no invented datasource.

Each record type validates into its own schema branch.  Cross-labeling
fails.  L2G and colocalisation records carry null datasource_id with an
explicit not-applicable reason.  The forbidden invented strings
ot_genetics_l2g and ot_genetics_colocalisation must never appear in a
datasource field.
"""

import pytest


def test_association_record_has_discriminator():
    """Association records carry the correct record_type discriminator."""
    from semantics import serialize_association
    rec = serialize_association(1, "ENSG00000142192", "APP", "amyloid precursor", "protein_coding", 0.872)
    assert rec["record_type"] == "association"
    assert rec["metric_name"] == "association_score"
    assert rec["datasource_id"] is None
    assert rec["datasource_not_applicable_reason"] is not None
    assert "prioritization metric" in rec["interpretation_boundary"]


def test_evidence_record_has_datasource():
    """Evidence records carry a real datasource_id."""
    from semantics import serialize_evidence
    rec = serialize_evidence("europepmc", "literature", 0.5)
    assert rec["record_type"] == "evidence"
    assert rec["datasource_id"] == "europepmc"
    assert rec["datasource_not_applicable_reason"] is None


def test_l2g_record_null_datasource():
    """L2G records have null datasource_id with explicit reason."""
    from semantics import serialize_l2g
    rec = serialize_l2g("SL123", "ENSG00000142192", "APP", 0.9)
    assert rec["record_type"] == "l2g_prediction"
    assert rec["datasource_id"] is None
    assert rec["datasource_not_applicable_reason"] is not None
    assert "not evidence datasource" in rec["datasource_not_applicable_reason"]


def test_colocalisation_record_null_datasource():
    """Colocalisation records have null datasource_id with explicit reason."""
    from semantics import serialize_colocalisation
    rec = serialize_colocalisation("SL123", "GCST005194", 0.8, 0.7)
    assert rec["record_type"] == "colocalisation"
    assert rec["datasource_id"] is None
    assert rec["datasource_not_applicable_reason"] is not None


def test_forbidden_datasource_ids_rejected():
    """The invented strings ot_genetics_l2g and ot_genetics_colocalisation are rejected."""
    from semantics import serialize_evidence
    with pytest.raises(ValueError, match="invented label"):
        serialize_evidence("ot_genetics_l2g", "genetic_association", 0.5)
    with pytest.raises(ValueError, match="invented label"):
        serialize_evidence("ot_genetics_colocalisation", "colocalisation", 0.5)


def test_cross_labeling_fails():
    """An evidence record with null datasource_id fails validation."""
    from semantics import serialize_evidence, validate_record
    rec = serialize_evidence("europepmc", "literature", 0.5)
    rec["datasource_id"] = None  # cross-label: evidence must have datasource
    errors = validate_record(rec)
    assert any("must carry a real datasource_id" in e for e in errors)


def test_association_with_datasource_fails():
    """An association record with a non-null datasource_id fails validation."""
    from semantics import serialize_association, validate_record
    rec = serialize_association(1, "ENSG1", "GENE", "gene", "protein_coding", 0.5)
    rec["datasource_id"] = "some_datasource"  # association must have null datasource
    errors = validate_record(rec)
    assert any("must have null datasource_id" in e for e in errors)


def test_all_records_validate():
    """A full set of valid records passes validation."""
    from semantics import (
        serialize_association, serialize_evidence, serialize_study,
        serialize_credible_set, serialize_l2g, serialize_colocalisation,
        validate_records,
    )
    records = [
        serialize_association(1, "ENSG1", "GENE1", "gene 1", "protein_coding", 0.9),
        serialize_evidence("europepmc", "literature", 0.5),
        serialize_study("GCST001", "gwas", "trait", "12345", "Author", 1000, 500, 500),
        serialize_credible_set("SL1", "1:1000", 1.5, -5),
        serialize_l2g("SL1", "ENSG1", "GENE1", 0.8),
        serialize_colocalisation("SL1", "GCST002", 0.7, 0.6),
    ]
    errors = validate_records(records)
    assert errors == [], f"validation errors: {errors}"


def test_interpretation_boundaries_present():
    """Every record with a metric carries an interpretation boundary."""
    from semantics import (
        serialize_association, serialize_evidence, serialize_l2g,
        serialize_colocalisation, serialize_credible_set,
    )
    for rec in [
        serialize_association(1, "ENSG1", "G", "g", "pc", 0.5),
        serialize_evidence("ds", "dt", 0.5),
        serialize_l2g("SL", "ENSG1", "G", 0.5),
        serialize_colocalisation("SL", "GCST", 0.5, 0.4),
        serialize_credible_set("SL", "r", 1.0, -3),
    ]:
        assert rec["interpretation_boundary"] is not None
        assert "NOT" in rec["interpretation_boundary"]
