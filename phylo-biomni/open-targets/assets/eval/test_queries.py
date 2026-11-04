"""OT-03: pinned query registry and schema drift.

Frozen response fixtures must pass all nine operations.  A removed/renamed
field fixture must fail with the exact operation and field error.  Empty-
but-HTTP-200 semantic fixtures must fail where the requested entity must
be nonempty.
"""

import json
import pathlib
import sys

import pytest
from conftest import FIXTURES_DIR, PKG_ROOT


def test_nine_operations_defined():
    """The registry defines exactly nine operation families."""
    from queries import NINE_OPERATIONS, REGISTRY
    assert len(NINE_OPERATIONS) == 9
    for op in NINE_OPERATIONS:
        assert op in REGISTRY
        assert "query" in REGISTRY[op]
        assert "semantic_nonempty" in REGISTRY[op]
        assert "variables" in REGISTRY[op]


def test_all_fixtures_pass_semantic_nonempty():
    """All nine frozen fixtures pass the semantic nonempty check."""
    from queries import check_semantic_nonempty, NINE_OPERATIONS
    fixture_map = {
        "meta_and_associated_targets": "meta_and_associated_targets.json",
        "search": "search.json",
        "target": "target.json",
        "disease_drugs": "disease_drugs.json",
        "evidence": "evidence.json",
        "drug": "drug.json",
        "variant": "variant.json",
        "study": "study.json",
        "credible_sets": "credible_sets.json",
    }
    for op in NINE_OPERATIONS:
        fixture_path = FIXTURES_DIR / fixture_map[op]
        assert fixture_path.exists(), f"fixture missing for {op}"
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        data = data.get("data", data)
        ok, msg = check_semantic_nonempty(op, data)
        assert ok, f"{op} failed semantic nonempty: {msg}"


def test_schema_drift_fixture_fails():
    """A fixture with a body-level GraphQL error fails the semantic check."""
    from queries import check_semantic_nonempty
    fixture = json.loads((FIXTURES_DIR / "failure_body_error.json").read_text(encoding="utf-8"))
    # The fixture has errors, not data — semantic check should fail
    data = fixture.get("data", {})
    ok, msg = check_semantic_nonempty("evidence", data)
    assert not ok, "body-error fixture should fail semantic nonempty check"


def test_empty_semantic_fixture_fails():
    """An empty semantic result (disease=null) fails the semantic check."""
    from queries import check_semantic_nonempty
    fixture = json.loads((FIXTURES_DIR / "failure_empty_semantic.json").read_text(encoding="utf-8"))
    data = fixture.get("data", fixture)
    ok, msg = check_semantic_nonempty("meta_and_associated_targets", data)
    assert not ok, "empty semantic fixture should fail — disease.id is null"


def test_query_hashes_are_stable():
    """Query hashes are deterministic and non-empty."""
    from queries import query_hash, NINE_OPERATIONS
    for op in NINE_OPERATIONS:
        h = query_hash(op)
        assert len(h) == 16, f"{op} hash should be 16 hex chars"
        assert all(c in "0123456789abcdef" for c in h), f"{op} hash should be hex"


def test_drug_query_no_typo():
    """The drug query must not contain the maximumClinicalStages: maximumClinicalStage typo."""
    from queries import REGISTRY
    drug_query = REGISTRY["drug"]["query"]
    assert "maximumClinicalStages: maximumClinicalStage" not in drug_query, (
        "drug query still has the maximumClinicalStages: maximumClinicalStage typo"
    )
    assert "maximumClinicalStage" in drug_query


def test_schema_smoke_passes_all():
    """The schema_smoke gate passes all nine operations against fixtures."""
    from schema_smoke import run_schema_smoke
    results = run_schema_smoke(FIXTURES_DIR)
    failed = [r for r in results if not r["passed"]]
    assert not failed, f"{len(failed)} operations failed: {[r['operation'] for r in failed]}"
