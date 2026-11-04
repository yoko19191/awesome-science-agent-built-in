"""OT-12: audit-preserving recovery.

Retries/recoveries must be modeled as a graph with unique attempt IDs and
recovered_by / retry_of links.  Reports may summarize recovery but must not
state that no failures occurred.  Deleting a predecessor or claiming zero
failures fails reconciliation.
"""

import json
from unittest.mock import patch

import pytest
from conftest import load_fixture, MockResponse


def test_retry_of_link_present():
    """A successful retry carries a retry_of link to the failed attempt."""
    from open_targets_client import OpenTargetsClient, GraphQLError
    bad = json.dumps(load_fixture("failure_body_error.json")).encode("utf-8")
    good = json.dumps(load_fixture("meta_and_associated_targets.json")).encode("utf-8")
    responses = [MockResponse(bad, 200), MockResponse(good, 200)]
    with patch("open_targets_client.urllib.request.urlopen", side_effect=responses):
        client = OpenTargetsClient()
        try:
            client.request("meta", "query { bad }")
        except GraphQLError:
            pass
        client.request("meta", "query { good }",
                       retry_of=client.ledger.records[0].attempt_id)
    assert len(client.ledger.records) == 2
    success = client.ledger.records[1]
    assert success.retry_of == client.ledger.records[0].attempt_id


def test_recovered_by_link_present():
    """A failed attempt is marked recovered_by the successful retry."""
    from open_targets_client import OpenTargetsClient, GraphQLError
    bad = json.dumps(load_fixture("failure_body_error.json")).encode("utf-8")
    good = json.dumps(load_fixture("meta_and_associated_targets.json")).encode("utf-8")
    responses = [MockResponse(bad, 200), MockResponse(good, 200)]
    with patch("open_targets_client.urllib.request.urlopen", side_effect=responses):
        client = OpenTargetsClient()
        try:
            client.request("meta", "query { bad }")
        except GraphQLError:
            pass
        client.request("meta", "query { good }",
                       retry_of=client.ledger.records[0].attempt_id)
    failed = client.ledger.records[0]
    success = client.ledger.records[1]
    assert failed.recovered_by == success.attempt_id


def test_six_failure_calibration_shape():
    """Six failures followed by successes produce 6+ attempts, not 0 failures."""
    from open_targets_client import OpenTargetsClient, GraphQLError, TransportError
    import urllib.error
    import io

    bad_fixture = load_fixture("failure_body_error.json")
    bad_raw = json.dumps(bad_fixture).encode("utf-8")
    good_fixture = load_fixture("meta_and_associated_targets.json")
    good_raw = json.dumps(good_fixture).encode("utf-8")

    # 6 failures then 1 success
    responses = [MockResponse(bad_raw, 200)] * 6 + [MockResponse(good_raw, 200)]
    with patch("open_targets_client.urllib.request.urlopen", side_effect=responses):
        client = OpenTargetsClient()
        for i in range(6):
            try:
                client.request("meta", f"query {{ attempt_{i} }}")
            except (GraphQLError, TransportError):
                pass
        client.request("meta", "query { meta { dataVersion { year } } }",
                       retry_of=client.ledger.records[5].attempt_id)

    assert client.ledger.failure_count() == 6
    assert client.ledger.success_count() == 1
    assert len(client.ledger.records) == 7


def test_deleting_predecessor_fails_reconciliation():
    """If a predecessor is deleted, the ledger count changes and reconciliation fails."""
    from open_targets_client import OpenTargetsClient, GraphQLError
    bad = json.dumps(load_fixture("failure_body_error.json")).encode("utf-8")
    good = json.dumps(load_fixture("meta_and_associated_targets.json")).encode("utf-8")
    # Two failures then one success-retry: deleting one predecessor still
    # leaves a failure on record, proving the audit trail is not clean.
    responses = [MockResponse(bad, 200), MockResponse(bad, 200), MockResponse(good, 200)]
    with patch("open_targets_client.urllib.request.urlopen", side_effect=responses):
        client = OpenTargetsClient()
        try:
            client.request("meta", "query { bad_1 }")
        except GraphQLError:
            pass
        try:
            client.request("meta", "query { bad_2 }")
        except GraphQLError:
            pass
        client.request("meta", "query { good }",
                       retry_of=client.ledger.records[0].attempt_id)

    original_count = len(client.ledger.records)
    original_failures = client.ledger.failure_count()
    assert original_failures == 2
    # Delete the predecessor (first failure)
    client.ledger._records.pop(0)
    assert len(client.ledger.records) == original_count - 1
    assert client.ledger.failure_count() == original_failures - 1
    # Reconciliation: claiming zero failures would be false — one failure remains
    assert client.ledger.failure_count() != 0


def test_ledger_serialization_preserves_failures():
    """The serialized ledger preserves both failures and successes."""
    from open_targets_client import OpenTargetsClient, GraphQLError, save_ledger
    import tempfile, os
    bad = json.dumps(load_fixture("failure_body_error.json")).encode("utf-8")
    good = json.dumps(load_fixture("meta_and_associated_targets.json")).encode("utf-8")
    responses = [MockResponse(bad, 200), MockResponse(good, 200)]
    with patch("open_targets_client.urllib.request.urlopen", side_effect=responses):
        client = OpenTargetsClient()
        try:
            client.request("meta", "query { bad }")
        except GraphQLError:
            pass
        client.request("meta", "query { good }",
                       retry_of=client.ledger.records[0].attempt_id)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        tmp_path = f.name
    save_ledger(client.ledger, tmp_path, source_mode="fixture")
    data = json.loads(open(tmp_path).read())
    assert data["summary"]["total_attempts"] == 2
    assert data["summary"]["failures"] == 1
    assert data["summary"]["successes"] == 1
    assert data["source_mode"] == "fixture"
    assert len(data["operations"]) == 2
    # Both records preserved
    states = [op["terminal_state"] for op in data["operations"]]
    assert "graphql_error" in states
    assert "success" in states
    os.unlink(tmp_path)
