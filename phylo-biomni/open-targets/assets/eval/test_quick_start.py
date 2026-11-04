"""OT-02: safe quick start.

The package must not contain bare requests.post().json() examples outside
the shipped helper.  Body-error fixtures must exit nonzero and write a
failed ledger entry.
"""

import ast
import pathlib
import sys

import pytest
from conftest import SCRIPTS_DIR, PKG_ROOT


def test_no_bare_requests_post_in_scripts():
    """No script uses requests.post(...).json() directly."""
    for py_file in SCRIPTS_DIR.glob("*.py"):
        if py_file.name == "report_qc.py":
            continue
        text = py_file.read_text(encoding="utf-8")
        assert "requests.post(" not in text, (
            f"{py_file.name} contains bare requests.post() — use the shipped client helper instead"
        )


def test_no_bare_requests_post_in_skill_md():
    """SKILL.md must not contain bare requests.post().json() examples."""
    skill_md = PKG_ROOT / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    assert "requests.post(" not in text, (
        "SKILL.md contains a bare requests.post() example — use the shipped client helper"
    )


def test_client_uses_urllib_not_requests():
    """The production client uses stdlib urllib, not the requests package."""
    client_code = (SCRIPTS_DIR / "open_targets_client.py").read_text(encoding="utf-8")
    tree = ast.parse(client_code)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    assert "requests" not in imports, "client imports requests — should use urllib only"
    assert any("urllib" in imp for imp in imports), "client should import urllib.request"


def test_body_error_exits_nonzero(tmp_results):
    """A body-error fixture must cause the client to raise (nonzero exit)."""
    import json
    from unittest.mock import patch
    from open_targets_client import OpenTargetsClient, GraphQLError
    from conftest import load_fixture, MockResponse

    fixture = load_fixture("failure_body_error.json")
    raw = json.dumps(fixture).encode("utf-8")
    with patch("open_targets_client.urllib.request.urlopen",
               return_value=MockResponse(raw, 200)):
        client = OpenTargetsClient()
        with pytest.raises(GraphQLError):
            client.request("evidence", "query { test }")
    assert client.ledger.failure_count() == 1
    assert client.ledger.records[0].terminal_state == "graphql_error"
