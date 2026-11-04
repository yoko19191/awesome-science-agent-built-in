"""OT-06: pagination and completeness labels.

The paginator must enforce max pages, max rows, and classify completeness
as exhaustive or bounded_sample.  Sample summaries cannot be labeled
exhaustive.
"""

import json
from unittest.mock import patch, MagicMock

import pytest
from conftest import load_fixture, MockResponse


def test_bounded_sample_not_exhaustive():
    """A truncated result is classified as bounded_sample, not exhaustive."""
    from open_targets_client import OpenTargetsClient
    fixture = load_fixture("meta_and_associated_targets.json")
    raw = json.dumps(fixture).encode("utf-8")
    with patch("open_targets_client.urllib.request.urlopen",
               return_value=MockResponse(raw, 200)):
        client = OpenTargetsClient(budgets={"max_pages": 1, "max_rows": 5})
        result = client.paginate(
            "meta_and_associated_targets",
            "query { disease(efoId: $efoId) { associatedTargets(page: {index: 0, size: $size}) { count rows { target { id approvedSymbol } score } } } }",
            {"efoId": "MONDO_0004975", "size": 5},
            page_mode="index",
            extract_path=("disease", "associatedTargets"),
            max_pages=1,
            max_rows=5,
        )
    assert result["completeness"] == "bounded_sample"
    assert result["truncated"] is True


def test_completeness_label_present():
    """Every paginate result carries a completeness label."""
    from open_targets_client import OpenTargetsClient
    fixture = load_fixture("meta_and_associated_targets.json")
    raw = json.dumps(fixture).encode("utf-8")
    with patch("open_targets_client.urllib.request.urlopen",
               return_value=MockResponse(raw, 200)):
        client = OpenTargetsClient()
        result = client.paginate(
            "meta_and_associated_targets",
            "query { disease(efoId: $efoId) { associatedTargets(page: {index: 0, size: $size}) { count rows { target { id } score } } } }",
            {"efoId": "MONDO_0004975", "size": 25},
            page_mode="index",
            extract_path=("disease", "associatedTargets"),
            max_pages=1,
        )
    assert "completeness" in result
    assert result["completeness"] in ("exhaustive", "bounded_sample")


def test_max_pages_enforced():
    """The paginator does not exceed max_pages."""
    from open_targets_client import OpenTargetsClient
    fixture = load_fixture("meta_and_associated_targets.json")
    raw = json.dumps(fixture).encode("utf-8")
    call_count = [0]
    def mock_urlopen(*args, **kwargs):
        call_count[0] += 1
        return MockResponse(raw, 200)
    with patch("open_targets_client.urllib.request.urlopen", side_effect=mock_urlopen):
        client = OpenTargetsClient()
        result = client.paginate(
            "meta_and_associated_targets",
            "query { disease(efoId: $efoId) { associatedTargets(page: {index: 0, size: $size}) { count rows { target { id } score } } } }",
            {"efoId": "MONDO_0004975", "size": 5},
            page_mode="index",
            extract_path=("disease", "associatedTargets"),
            max_pages=2,
        )
    assert result["pages_fetched"] <= 2
    assert call_count[0] <= 2


def test_cursor_pagination():
    """Cursor-based pagination (evidences) returns rows and cursor state."""
    from open_targets_client import OpenTargetsClient
    ev_fixture = load_fixture("evidence.json")
    raw = json.dumps(ev_fixture).encode("utf-8")
    with patch("open_targets_client.urllib.request.urlopen",
               return_value=MockResponse(raw, 200)):
        client = OpenTargetsClient()
        result = client.paginate(
            "evidence",
            "query { disease(efoId: $efoId) { evidences(ensemblIds: [$ensemblId], size: $size, cursor: $cursor) { count cursor rows { datasourceId score } } } }",
            {"efoId": "MONDO_0004975", "ensemblId": "ENSG00000142192", "size": 10},
            page_mode="cursor",
            extract_path=("disease", "evidences"),
            max_pages=1,
        )
    assert "rows" in result
    assert isinstance(result["rows"], list)
