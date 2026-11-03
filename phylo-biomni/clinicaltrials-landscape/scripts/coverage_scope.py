"""Coverage provenance and claim guards for ClinicalTrials.gov landscapes."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

try:
    from query_profiles import (
        all_registry_statuses, boolean_or_expression, get_query_profile, profile_metadata,
    )
except ImportError:  # pragma: no cover
    from scripts.query_profiles import (
        all_registry_statuses, boolean_or_expression, get_query_profile, profile_metadata,
    )

SCHEMA = "clinicaltrials-landscape-coverage/v1"
SOURCE = "ClinicalTrials.gov API v2"
RETRIEVAL_STATE = "complete_for_declared_query"
BOUNDARY_STATEMENT = (
    "Coverage is limited to registered records returned by the declared "
    "ClinicalTrials.gov API query as of the recorded query date."
)
LIMITATION_STATEMENT = (
    "This is not a complete target-development or all-program inventory; pre-IND, "
    "unregistered, and query-term or registry-indexing omissions remain outside scope."
)
RECORD_ASSET_BOUNDARY_STATEMENT = (
    "Registered-record counts do not measure unique agents, assets, or development programs."
)

_REQUIRED_FIELDS = {
    "schema",
    "source",
    "query_date",
    "conditions",
    "statuses",
    "intervention_filter",
    "phases",
    "sponsor_filter",
    "pages_retrieved",
    "records_retrieved",
    "api_total_count",
    "pagination_exhausted",
    "retrieval_state",
    "input_resolution",
    "query_profile",
}

_FORBIDDEN_REPORT_CLAIMS = (
    re.compile(r"\bexhaustive\b", re.IGNORECASE),
    re.compile(r"\bcomplete\s+(?:catalog|inventory|landscape)\b", re.IGNORECASE),
    re.compile(r"\ball\s+(?:agents|assets|programs)\b", re.IGNORECASE),
    re.compile(r"\bcomplete trial listing\b", re.IGNORECASE),
)


def _validated_query_terms(value: object, field: str, *, allow_empty: bool = False) -> list[str]:
    """Require a JSON array of nonblank terms without normalizing query text."""
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"coverage_scope {field} must be an array of nonempty strings"
                         + ("" if allow_empty else " with at least one term"))
    if any(not isinstance(term, str) or not term.strip() for term in value):
        raise ValueError(f"coverage_scope {field} must be a nonempty array of nonempty strings")
    return list(value)


def build_coverage_scope(
    *,
    conditions,
    statuses,
    intervention_filter,
    phases,
    sponsor_filter,
    pages_retrieved,
    records_retrieved,
    api_total_count,
    pagination_exhausted,
    query_date=None,
    input_resolution="declared_query",
    query_profile=None,
):
    """Build and validate provenance for one fully exhausted API query."""
    scope = {
        "schema": SCHEMA,
        "source": SOURCE,
        "query_date": query_date or datetime.now(timezone.utc).date().isoformat(),
        "conditions": _validated_query_terms(conditions, "conditions"),
        "statuses": _validated_query_terms(statuses, "statuses"),
        "intervention_filter": intervention_filter,
        "phases": _validated_query_terms(phases, "phases", allow_empty=True),
        "sponsor_filter": sponsor_filter,
        "pages_retrieved": int(pages_retrieved),
        "records_retrieved": int(records_retrieved),
        "api_total_count": int(api_total_count),
        "pagination_exhausted": bool(pagination_exhausted),
        "retrieval_state": RETRIEVAL_STATE,
        "input_resolution": input_resolution,
        "query_profile": query_profile,
    }
    return validate_coverage_scope(scope)


def _validate_profile_binding(scope: dict) -> None:
    """Require external versioned provenance to match the bundled query contract."""
    metadata = scope["query_profile"]
    if not isinstance(metadata["id"], str) or not metadata["id"].strip():
        raise ValueError("query_profile id must be a nonblank string")
    profile = get_query_profile(metadata["id"])
    for field, expected in profile_metadata(profile).items():
        if type(metadata[field]) is not type(expected) or metadata[field] != expected:
            raise ValueError(f"query_profile {field} disagrees with the bundled profile")
    expected_query = {
        "conditions": profile["conditions"],
        "intervention_filter": boolean_or_expression(profile["aliases"]),
        "statuses": all_registry_statuses(),
        "phases": [],
        "sponsor_filter": None,
    }
    for field, expected in expected_query.items():
        if scope[field] != expected:
            raise ValueError(f"coverage_scope {field} disagrees with the bundled query profile")


def validate_coverage_scope(scope):
    """Fail unless scope proves a complete retrieval for the declared API query."""
    if not isinstance(scope, dict):
        raise ValueError("coverage_scope is required and must be a dictionary")

    missing = sorted(_REQUIRED_FIELDS - set(scope))
    if missing:
        raise ValueError(f"coverage_scope missing required fields: {', '.join(missing)}")
    if scope["schema"] != SCHEMA:
        raise ValueError(f"unsupported coverage scope schema: {scope['schema']}")
    if scope["source"] != SOURCE:
        raise ValueError(f"unsupported coverage source: {scope['source']}")
    for field in ("conditions", "statuses"):
        _validated_query_terms(scope[field], field)
    _validated_query_terms(scope["phases"], "phases", allow_empty=True)
    if scope["input_resolution"] not in {"declared_query", "versioned_profile"}:
        raise ValueError("coverage_scope input_resolution is invalid")
    if scope["input_resolution"] == "versioned_profile":
        profile = scope["query_profile"]
        if not isinstance(profile, dict):
            raise ValueError("versioned-profile coverage must record query_profile metadata")
        required_profile_fields = {"id", "version", "reviewed_on", "alias_count", "sha256"}
        missing_profile_fields = sorted(required_profile_fields - set(profile))
        if missing_profile_fields:
            raise ValueError(
                "query_profile metadata missing fields: " + ", ".join(missing_profile_fields)
            )
        _validate_profile_binding(scope)
    elif scope["query_profile"] is not None:
        raise ValueError("declared-query coverage must not record query_profile metadata")
    if scope["pagination_exhausted"] is not True:
        raise ValueError("coverage_scope must prove exhausted pagination")
    if scope["retrieval_state"] != RETRIEVAL_STATE:
        raise ValueError(f"coverage_scope retrieval_state must be {RETRIEVAL_STATE}")
    if int(scope["records_retrieved"]) != int(scope["api_total_count"]):
        raise ValueError(
            "coverage_scope record count does not match the API total count: "
            f"{scope['records_retrieved']} != {scope['api_total_count']}"
        )
    return dict(scope)


def bind_exported_dataset(scope, csv_path):
    """Bind a validated query scope to the exact exported trials CSV."""
    bound = validate_coverage_scope(scope)
    csv_path = Path(csv_path)
    payload = csv_path.read_bytes()
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        row_count = max(sum(1 for _ in csv.reader(handle)) - 1, 0)
    if row_count != int(bound["records_retrieved"]):
        raise ValueError(
            "exported trials row count does not match the query scope: "
            f"{row_count} != {bound['records_retrieved']}"
        )
    bound["dataset_file"] = csv_path.name
    bound["dataset_rows"] = row_count
    bound["dataset_sha256"] = hashlib.sha256(payload).hexdigest()
    bound["boundary_statement"] = BOUNDARY_STATEMENT
    bound["limitation_statement"] = LIMITATION_STATEMENT
    bound["record_asset_boundary_statement"] = RECORD_ASSET_BOUNDARY_STATEMENT
    return bound


def write_coverage_scope(scope, output_path):
    """Write the query and dataset provenance artifact."""
    Path(output_path).write_text(
        json.dumps(scope, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def assert_report_claims_bounded(report_text):
    """Reject reports that omit the scope boundary or overclaim completeness."""
    if BOUNDARY_STATEMENT not in report_text:
        raise ValueError("report is missing the required coverage boundary statement")
    if LIMITATION_STATEMENT not in report_text:
        raise ValueError("report is missing the required coverage limitation statement")
    if RECORD_ASSET_BOUNDARY_STATEMENT not in report_text:
        raise ValueError("report is missing the required record-versus-asset boundary statement")
    for pattern in _FORBIDDEN_REPORT_CLAIMS:
        match = pattern.search(report_text)
        if match:
            raise ValueError(f"report contains an unqualified coverage claim: {match.group(0)}")


# "pipeline" is rejected only in its development-asset sense (an owner's or the field's pipeline of
# agents, assets, candidates, programs, portfolios). Operational prose about the analysis pipeline
# ("the analysis pipeline queried ClinicalTrials.gov") is valid report text. A qualifier may be
# separated from "pipeline" by at most one word ("development program pipeline").
_PIPELINE_OWNER_QUALIFIERS = (
    r"(?:development|clinical|drug|therapeutic|therapy|r&d|commercial|industry|corporate|"
    r"compan(?:y|ies)|sponsors?|competitors?|oncology|late[\s-]?stage|early[\s-]?stage|emerging|"
    r"\w+['\u2019]s)"
)
_PIPELINE_ASSET_NOUNS = (
    r"(?:assets?|candidates?|programs?|programmes?|portfolios?|agents?|drugs?|products?|"
    r"therap(?:y|ies|eutics)|molecules?|compounds?)"
)
_FORBIDDEN_RECORD_ASSET_INFERENCE = (
    re.compile(r"\b" + _PIPELINE_OWNER_QUALIFIERS + r"\s+(?:\w+\s+)?pipelines?\b", re.IGNORECASE),
    re.compile(r"\bpipelines?\s+(?:of\s+(?:\w+\s+)?)?" + _PIPELINE_ASSET_NOUNS + r"\b", re.IGNORECASE),
    re.compile(r"\bin\s+the\s+pipelines?\b", re.IGNORECASE),
    re.compile(r"\b(?:early[\s-]?stage|emerging)\s+(?:agents?|assets?|programs?)\b", re.IGNORECASE),
)


def assert_report_free_of_record_asset_inference(report_text):
    """Reject conclusions that promote registered-record activity into unique development assets."""
    for pattern in _FORBIDDEN_RECORD_ASSET_INFERENCE:
        match = pattern.search(report_text)
        if match:
            raise ValueError(
                "report contains unsupported record-to-asset/pipeline inference: "
                f"{match.group(0)!r}. Registered-record counts are not unique agents, assets, "
                "or development programs."
            )


# A bounded registry query cannot support strategic or commercial inference.
_FORBIDDEN_STRATEGIC_INFERENCE = (
    re.compile(r"\bwhitespace\b", re.IGNORECASE),
    re.compile(r"\bfirst[\s-]?mover\b", re.IGNORECASE),
    re.compile(r"\bcommercial potential\b", re.IGNORECASE),
    re.compile(r"\bcommercial maturation\b", re.IGNORECASE),
    re.compile(r"\bcommercial intent\b", re.IGNORECASE),
    re.compile(r"\binvestment and commercial\b", re.IGNORECASE),
    re.compile(r"\bregistration intent\b", re.IGNORECASE),
    re.compile(r"\bmarket access\b", re.IGNORECASE),
    re.compile(r"\b(?:sponsor|advancement)\s+conviction\b", re.IGNORECASE),
    re.compile(r"\bconviction by sponsors\b", re.IGNORECASE),
    re.compile(r"\bunmet need\b", re.IGNORECASE),
)


def assert_report_free_of_strategic_inference(report_text):
    """Reject reports that infer investment/commercial/registration/whitespace/first-mover/etc.

    A bounded ClinicalTrials.gov registry query over a mixed diagnostic/therapeutic set with generic
    classification does not support strategic or commercial inference. This guard makes that a hard
    failure, not a review comment.
    """
    for pattern in _FORBIDDEN_STRATEGIC_INFERENCE:
        match = pattern.search(report_text)
        if match:
            raise ValueError(
                "report contains unsupported strategic/commercial inference: "
                f"{match.group(0)!r}. This bounded registry landscape does not support it."
            )
