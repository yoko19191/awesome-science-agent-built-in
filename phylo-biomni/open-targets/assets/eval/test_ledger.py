"""OT-01: failure-preserving request ledger.

Every GraphQL request must be recorded in an immutable operation ledger,
including failures.  Successful retries supplement rather than replace
failed attempts.
"""

import json
import sys
import pathlib
from unittest.mock import patch, MagicMock

import pytest
from conftest import load_fixture, MockResponse, make_mock_urlopen, make_mock_urlopen_raw


def test_success_records_ledger_entry():
    """A successful request appends exactly one success record."""
    from open_targets_client import OpenTargetsClient, OperationLedger
    fixture = load_fixture("meta_and_associated_targets.json")
    raw = json.dumps(fixture).encode("utf-8")
    with patch("open_targets_client.urllib.request.urlopen",
               return_value=MockResponse(raw, 200)):
        client = OpenTargetsClient()
        data = client.request("meta", "query { meta { dataVersion { year } } }")
    assert client.ledger.success_count() == 1
    assert client.ledger.failure_count() == 0
    rec = client.ledger.records[0]
    assert rec.terminal_state == "success"
    assert rec.http_status == 200


def test_body_error_preserves_failure():
    """HTTP 200 with GraphQL body errors records a graphql_error, not success."""
    from open_targets_client import OpenTargetsClient, GraphQLError
    fixture = load_fixture("failure_body_error.json")
    raw = json.dumps(fixture).encode("utf-8")
    with patch("open_targets_client.urllib.request.urlopen",
               return_value=MockResponse(raw, 200)):
        client = OpenTargetsClient()
        with pytest.raises(GraphQLError):
            client.request("evidence", "query { test }")
    assert client.ledger.failure_count() == 1
    assert client.ledger.success_count() == 0
    rec = client.ledger.records[0]
    assert rec.terminal_state == "graphql_error"
    assert rec.graphql_errors is not None


def test_transport_error_preserves_failure():
    """HTTP 400 records a transport_error."""
    from open_targets_client import OpenTargetsClient, TransportError
    import urllib.error
    fixture = load_fixture("failure_http_400.json")
    raw = json.dumps(fixture).encode("utf-8")
    exc = urllib.error.HTTPError(
        url="https://example.com", code=400,
        msg="Bad Request", hdrs=None,
        fp=__import__("io").BytesIO(raw),
    )
    with patch("open_targets_client.urllib.request.urlopen", side_effect=exc):
        client = OpenTargetsClient()
        with pytest.raises(TransportError):
            client.request("meta", "query { bad }")
    assert client.ledger.failure_count() == 1
    rec = client.ledger.records[0]
    assert rec.terminal_state == "transport_error"
    assert rec.http_status == 400


def test_malformed_json_preserves_failure():
    """Malformed JSON response records a transport_error."""
    from open_targets_client import OpenTargetsClient, TransportError
    raw = load_fixture("failure_malformed_json.txt").encode("utf-8")
    with patch("open_targets_client.urllib.request.urlopen",
               return_value=MockResponse(raw, 200)):
        client = OpenTargetsClient()
        with pytest.raises(TransportError):
            client.request("meta", "query { test }")
    assert client.ledger.failure_count() == 1
    rec = client.ledger.records[0]
    assert rec.terminal_state == "transport_error"


def test_retry_supplements_not_replaces():
    """A failed attempt followed by a successful retry preserves both."""
    from open_targets_client import OpenTargetsClient, GraphQLError
    bad_fixture = load_fixture("failure_body_error.json")
    good_fixture = load_fixture("meta_and_associated_targets.json")
    bad_raw = json.dumps(bad_fixture).encode("utf-8")
    good_raw = json.dumps(good_fixture).encode("utf-8")

    responses = [MockResponse(bad_raw, 200), MockResponse(good_raw, 200)]
    with patch("open_targets_client.urllib.request.urlopen",
               side_effect=responses):
        client = OpenTargetsClient()
        # First attempt fails
        try:
            client.request("meta", "query { bad }")
        except GraphQLError:
            pass
        # Retry succeeds, linked to the failed attempt
        data = client.request("meta", "query { meta { dataVersion { year } } }",
                              retry_of=client.ledger.records[0].attempt_id)

    assert client.ledger.success_count() == 1
    assert client.ledger.failure_count() == 1
    assert len(client.ledger.records) == 2
    # The failed record should be marked as recovered_by the successful one
    failed = client.ledger.records[0]
    success = client.ledger.records[1]
    assert failed.terminal_state == "graphql_error"
    assert success.terminal_state == "success"
    assert success.retry_of == failed.attempt_id
    assert failed.recovered_by == success.attempt_id


def test_mutation_dropping_failure_fails():
    """If a failed record is dropped, the ledger count must change."""
    from open_targets_client import OpenTargetsClient, GraphQLError
    bad_fixture = load_fixture("failure_body_error.json")
    good_fixture = load_fixture("meta_and_associated_targets.json")
    bad_raw = json.dumps(bad_fixture).encode("utf-8")
    good_raw = json.dumps(good_fixture).encode("utf-8")

    responses = [MockResponse(bad_raw, 200), MockResponse(good_raw, 200)]
    with patch("open_targets_client.urllib.request.urlopen",
               side_effect=responses):
        client = OpenTargetsClient()
        try:
            client.request("meta", "query { bad }")
        except GraphQLError:
            pass
        client.request("meta", "query { good }",
                       retry_of=client.ledger.records[0].attempt_id)

    # Simulate a mutation that drops the failed record
    original_count = len(client.ledger.records)
    client.ledger._records = client.ledger._records[1:]  # drop the failure
    assert len(client.ledger.records) == original_count - 1
    assert client.ledger.failure_count() == 0  # failure was dropped
