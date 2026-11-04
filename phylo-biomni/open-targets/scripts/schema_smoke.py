#!/usr/bin/env python3
"""Schema compatibility gate for the pinned nine-operation query registry (OT-03).

Runs each operation against its frozen fixture, checks that every field named in
the query selection is present in the response, and validates semantic-nonempty
expectations.  On schema drift (removed/renamed field, empty semantic result)
the gate returns a bounded diagnostic and preserves the failure — it never
silently rewrites a field and calls the original operation successful.

Usage:
    python schema_smoke.py [--fixtures-dir DIR] [--json]

Exit codes: 0 all pass | 1 one or more operations failed
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

# Import the query registry from the sibling module
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from queries import REGISTRY, NINE_OPERATIONS, check_semantic_nonempty, query_hash  # noqa: E402


def _load_fixture(fixtures_dir: pathlib.Path, op_name: str) -> dict | None:
    """Load the frozen fixture for one operation."""
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
    fname = fixture_map.get(op_name)
    if not fname:
        return None
    path = fixtures_dir / fname
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_fields_from_query(query: str) -> list[str]:
    """Extract leaf field names from a GraphQL query string (best-effort)."""
    import re
    # Remove string literals so entity names inside arrays (e.g. ["target",
    # "disease", "drug"]) are not mistaken for response field names.
    body = re.sub(r'"[^"]*"', '""', query.strip())
    # Remove variable references ($varName) so variable names are not mistaken
    # for response field names.
    body = re.sub(r"\$\w+", "", body)
    # Remove the query wrapper and variable definitions
    body = re.sub(r"^query\s+\w+\s*\([^)]*\)\s*\{", "", body)
    if body.endswith("}"):
        body = body[:-1]
    # Find all field-like tokens (word followed by optional subselection or end)
    fields = set()
    for m in re.finditer(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b(?!\s*[:(])", body):
        token = m.group(1)
        # Skip GraphQL keywords and known argument/variable names
        if token in ("query", "fragment", "on", "page", "size", "index", "cursor",
                      "efoId", "ensemblId", "chemblId", "variantId", "studyId",
                      "studyIds", "datasourceIds", "enableIndirect", "BInt",
                      "entityNames", "queryString", "ensemblIds"):
            continue
        fields.add(token)
    return sorted(fields)


def _check_field_present(data: dict, field_name: str) -> bool:
    """Recursively check if a field name appears anywhere in the response data."""
    if isinstance(data, dict):
        if field_name in data:
            return True
        return any(_check_field_present(v, field_name) for v in data.values())
    if isinstance(data, list):
        return any(_check_field_present(item, field_name) for item in data)
    return False


def run_schema_smoke(fixtures_dir: pathlib.Path) -> list[dict]:
    """Run all nine operations against their fixtures. Returns a list of result dicts."""
    results = []
    for op_name in NINE_OPERATIONS:
        entry = REGISTRY[op_name]
        fixture = _load_fixture(fixtures_dir, op_name)

        result = {
            "operation": op_name,
            "query_hash": query_hash(op_name),
            "fixture_loaded": fixture is not None,
            "semantic_nonempty_ok": False,
            "semantic_nonempty_msg": "",
            "field_check": [],
            "passed": False,
            "errors": [],
        }

        if fixture is None:
            result["errors"].append(f"no fixture found for {op_name!r}")
            results.append(result)
            continue

        # Check for GraphQL errors in the fixture
        if isinstance(fixture, dict) and "errors" in fixture:
            result["errors"].append(f"fixture carries GraphQL errors: {fixture['errors']}")
            results.append(result)
            continue

        data = fixture.get("data", fixture)

        # Semantic nonempty check
        ok, msg = check_semantic_nonempty(op_name, data)
        result["semantic_nonempty_ok"] = ok
        result["semantic_nonempty_msg"] = msg
        if not ok:
            result["errors"].append(msg)

        # Field presence check (best-effort: check key fields from the query)
        query = entry["query"]
        query_fields = _extract_fields_from_query(query)
        missing_fields = []
        for field_name in query_fields:
            if not _check_field_present(data, field_name):
                missing_fields.append(field_name)
        if missing_fields:
            result["field_check"] = missing_fields
            result["errors"].append(
                f"fields absent in fixture response: {', '.join(missing_fields)}"
            )

        result["passed"] = len(result["errors"]) == 0
        results.append(result)

    return results


def run_live_smoke() -> list[dict]:
    """Run all nine operations against the live Open Targets API.

    Opt-in only (``--live``).  Uses representative customer-neutral variable
    values.  Never replaces the offline rejection tests — its purpose is to
    confirm the pinned queries still resolve against the current API schema.
    """
    from open_targets_client import OpenTargetsClient, GraphQLError, TransportError
    from queries import REGISTRY, check_semantic_nonempty

    # Representative customer-neutral variable values
    live_vars: dict[str, dict] = {
        "meta_and_associated_targets": {"efoId": "MONDO_0004975", "size": 10},
        "search": {"q": "Alzheimer"},
        "target": {"ensemblId": "ENSG00000130208"},        # APOE
        "disease_drugs": {"efoId": "MONDO_0004975"},
        "evidence": {"efoId": "MONDO_0004975", "ensemblId": "ENSG00000130208",
                     "size": 10, "cursor": None},
        "drug": {"chemblId": "CHEMBL3"},                    # galantamine
        "variant": {"variantId": "16_83012185_A_G"},
        "study": {"studyId": "GCST005194"},
        "credible_sets": {"studyIds": ["GCST005194"], "size": 10},
    }

    client = OpenTargetsClient()
    # Capture release metadata first (OT-05)
    try:
        release = client.capture_release()
    except (GraphQLError, TransportError) as exc:
        release = {}

    results: list[dict] = []
    for op_name in NINE_OPERATIONS:
        entry = REGISTRY[op_name]
        query = entry["query"]
        variables = live_vars.get(op_name, {})

        result = {
            "operation": op_name,
            "query_hash": query_hash(op_name),
            "live": True,
            "release": release,
            "semantic_nonempty_ok": False,
            "semantic_nonempty_msg": "",
            "passed": False,
            "errors": [],
        }

        try:
            data = client.request(op_name, query, variables)
            ok, msg = check_semantic_nonempty(op_name, data)
            result["semantic_nonempty_ok"] = ok
            result["semantic_nonempty_msg"] = msg
            if not ok:
                result["errors"].append(msg)
        except (GraphQLError, TransportError) as exc:
            result["errors"].append(f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            result["errors"].append(f"unexpected {type(exc).__name__}: {exc}")

        result["passed"] = len(result["errors"]) == 0
        results.append(result)

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Schema compatibility smoke gate")
    parser.add_argument("--fixtures-dir", default=str(
        pathlib.Path(__file__).resolve().parent.parent / "assets" / "fixtures"))
    parser.add_argument("--json", action="store_true", help="output JSON instead of text")
    parser.add_argument("--live", action="store_true",
                        help="opt-in: run all nine operations against the live API")
    args = parser.parse_args()

    if args.live:
        results = run_live_smoke()
    else:
        fixtures_dir = pathlib.Path(args.fixtures_dir)
        results = run_schema_smoke(fixtures_dir)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            status = "PASS" if r["passed"] else "FAIL"
            print(f"  [{status}] {r['operation']} (hash={r['query_hash']})")
            if r["errors"]:
                for e in r["errors"]:
                    print(f"         {e}")

    failed = [r for r in results if not r["passed"]]
    if failed:
        print(f"\n{len(failed)}/{len(results)} operations FAILED", file=sys.stderr)
        return 1
    print(f"\nAll {len(results)} operations passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
