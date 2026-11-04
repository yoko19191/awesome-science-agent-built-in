"""Mutation and mechanism tests for the finalization-v1 acceptance contract."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

import pytest

from conftest import MockResponse, load_fixture


def test_production_study_projection_preserves_ontology_diseases():
    from run_analysis import _check_study_concordance, _study_fact

    fact = _study_fact({
        "id": "STUDY-X",
        "traitFromSource": "trait label intentionally unrelated",
        "diseases": [{"id": "MONDO_123", "name": "Example disease"}],
    })
    assert fact["diseases"] == [{"id": "MONDO_123", "name": "Example disease"}]
    verdict = _check_study_concordance("MONDO_123", "different text", [fact])
    assert verdict["concordant"] is True
    assert verdict["reason"] == "study diseases list contains the queried EFO ID"


def test_success_ledger_hashes_exact_request_and_response_bytes():
    from open_targets_client import OpenTargetsClient

    raw = json.dumps(load_fixture("meta_and_associated_targets.json")).encode("utf-8")
    variables = {"efoId": "MONDO_123", "size": 3}
    query = "query Test($efoId: String!) { disease(efoId: $efoId) { id } }"
    with patch("open_targets_client.urllib.request.urlopen",
               return_value=MockResponse(raw, 200)):
        client = OpenTargetsClient(source_mode="fixture")
        client.request("test", query, variables)

    record = client.ledger.records[0]
    request_body = json.dumps(
        {"query": query, "variables": variables}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert record.query_hash == hashlib.sha256(query.encode("utf-8")).hexdigest()
    assert record.request_hash == hashlib.sha256(request_body).hexdigest()
    assert record.response_hash == hashlib.sha256(raw).hexdigest()
    assert record.response_bytes == len(raw)
    assert record.response_body_state == "received"


def test_graphql_failure_keeps_response_hash_and_retry_links():
    from open_targets_client import GraphQLError, OpenTargetsClient

    bad = json.dumps(load_fixture("failure_body_error.json")).encode("utf-8")
    good = json.dumps(load_fixture("meta_and_associated_targets.json")).encode("utf-8")
    with patch("open_targets_client.urllib.request.urlopen",
               side_effect=[MockResponse(bad, 200), MockResponse(good, 200)]):
        client = OpenTargetsClient(source_mode="fixture")
        with pytest.raises(GraphQLError):
            client.request("meta", "query { bad }")
        predecessor = client.ledger.records[0]
        client.request("meta", "query { good }", retry_of=predecessor.attempt_id)

    successor = client.ledger.records[1]
    assert predecessor.response_hash == hashlib.sha256(bad).hexdigest()
    assert successor.response_hash == hashlib.sha256(good).hexdigest()
    assert successor.retry_of == predecessor.attempt_id
    assert predecessor.recovered_by == successor.attempt_id


def test_retry_of_success_is_rejected_before_network():
    from open_targets_client import OpenTargetsClient

    raw = json.dumps(load_fixture("meta_and_associated_targets.json")).encode("utf-8")
    with patch("open_targets_client.urllib.request.urlopen",
               return_value=MockResponse(raw, 200)) as urlopen:
        client = OpenTargetsClient(source_mode="fixture")
        client.request("meta", "query { first }")
        with pytest.raises(ValueError, match="successful operation"):
            client.request("meta", "query { second }",
                           retry_of=client.ledger.records[0].attempt_id)
    assert urlopen.call_count == 1


def test_response_over_byte_budget_is_recorded_as_budget_exceeded():
    from open_targets_client import BudgetExceeded, OpenTargetsClient

    raw = b'{"data":{"oversized":"' + (b"x" * 100) + b'"}}'
    with patch("open_targets_client.urllib.request.urlopen",
               return_value=MockResponse(raw, 200)):
        client = OpenTargetsClient(budgets={"max_bytes": 32}, source_mode="fixture")
        with pytest.raises(BudgetExceeded):
            client.request("test", "query { test }")
    record = client.ledger.records[0]
    assert record.terminal_state == "budget_exceeded"
    assert record.response_hash == hashlib.sha256(raw[:33]).hexdigest()
    assert record.response_bytes == 33
    assert record.response_body_state == "received"


def test_visual_review_event_requires_an_explicit_typed_state(tmp_path, monkeypatch):
    monkeypatch.setenv("BIOMNI_RESULTS", str(tmp_path))
    import importlib
    import report_qc
    importlib.reload(report_qc)

    page = tmp_path / "page-1.png"
    page.write_bytes(b"x" * 2000)
    with pytest.raises(TypeError):
        report_qc.record_pdf_review(
            "report.pdf", "report.txt", ["page-1.png"], [1], "reviewed"
        )
    report_qc.record_pdf_review(
        "report.pdf", "report.txt", ["page-1.png"], [],
        "visual inspection unavailable", review_state="not_evaluable",
    )
    event = json.loads((tmp_path / "qc_run_log.json").read_text())["events"][-1]
    assert event["state"] == "not_evaluable"
    assert event["pages"] == []
