"""OT-09: explicit API/bulk boundary.

The request planner must return a structured bulk_handoff when the estimated
workload exceeds budget, and route to the API when within budget.
"""

import pytest


def test_within_budget_routes_to_api():
    """A small single-entity request routes to the API."""
    from open_targets_client import OpenTargetsClient
    client = OpenTargetsClient()
    plan = client.plan_request("meta_and_associated_targets", 10, n_entities=1)
    assert plan["route"] == "api"
    assert "estimated_rows" in plan


def test_over_entities_routes_to_bulk():
    """Over-budget entities route to bulk_handoff."""
    from open_targets_client import OpenTargetsClient
    client = OpenTargetsClient(budgets={"max_entities": 5})
    plan = client.plan_request("evidence", 100, n_entities=10)
    assert plan["route"] == "bulk_handoff"
    assert "official_routes" in plan
    assert "downstream_filtering" in plan


def test_over_rows_routes_to_bulk():
    """Over-budget rows route to bulk_handoff."""
    from open_targets_client import OpenTargetsClient
    client = OpenTargetsClient(budgets={"max_rows": 100})
    plan = client.plan_request("evidence", 500, n_entities=1)
    assert plan["route"] == "bulk_handoff"


def test_bulk_handoff_names_official_route():
    """The bulk handoff names the official Open Targets data download route."""
    from open_targets_client import OpenTargetsClient
    client = OpenTargetsClient(budgets={"max_rows": 10})
    plan = client.plan_request("evidence", 100, n_entities=1)
    assert plan["route"] == "bulk_handoff"
    assert any("opentargets.org" in url for url in plan.get("official_routes", []))


def test_bulk_handoff_includes_release():
    """The bulk handoff includes the release for provenance binding."""
    from open_targets_client import OpenTargetsClient, OperationLedger
    ledger = OperationLedger()
    ledger.set_release({"dataVersion": {"year": 26, "month": 6}})
    client = OpenTargetsClient(ledger=ledger, budgets={"max_rows": 10})
    plan = client.plan_request("evidence", 100, n_entities=1)
    assert plan["route"] == "bulk_handoff"
    assert plan.get("release") is not None
