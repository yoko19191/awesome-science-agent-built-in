"""Validate extracted PDF text against the run's facts before writing the final receipt."""

from __future__ import annotations

import json
import hashlib
import os
import pathlib
import re

try:
    from coverage_scope import (
        assert_report_claims_bounded,
        assert_report_free_of_record_asset_inference,
        assert_report_free_of_strategic_inference,
    )
    from export_all import extract_positive_query_terms
except ImportError:  # pragma: no cover
    from scripts.coverage_scope import (
        assert_report_claims_bounded,
        assert_report_free_of_record_asset_inference,
        assert_report_free_of_strategic_inference,
    )
    from scripts.export_all import extract_positive_query_terms


try:
    from build_report_spec import REPORT_NAME, REPORT_SPEC_SCHEMA, TOP_LEVEL_SECTIONS
except ImportError:  # pragma: no cover
    from scripts.build_report_spec import REPORT_NAME, REPORT_SPEC_SCHEMA, TOP_LEVEL_SECTIONS


def _normalized(value: str) -> str:
    return " ".join(str(value).split())


def validate_visual_review_attestation(
    review_attestation: str,
    review_verdict: str,
    review_issues: list[str],
    *, review_performed: bool = False,
) -> None:
    """Reject a visual-pass claim that is only a text, structure, or pixel-statistics check."""
    if type(review_performed) is not bool:
        raise ValueError("visual review performed must be an explicit boolean")
    if review_verdict not in {"pass", "fail"}:
        raise ValueError("visual review verdict must be 'pass' or 'fail'")
    if not isinstance(review_issues, list) or not all(
        isinstance(issue, str) and issue.strip() for issue in review_issues
    ):
        raise ValueError("visual review issues must be an array of non-empty strings")
    attestation = _normalized(review_attestation)
    if not attestation:
        raise ValueError("visual review attestation is required")
    if review_verdict == "fail":
        if not review_issues:
            raise ValueError("a failing visual review must record at least one issue")
        return
    if review_issues:
        raise ValueError("a passing visual review cannot contain unresolved issues")
    if not review_performed:
        raise ValueError("a passing visual review requires actual rendered-page inspection with performed=true")



def validate_report_spec(spec: dict, facts_path: pathlib.Path) -> None:
    """Fail when the renderer is not using the report spec derived from these exact facts."""
    if spec.get("schema") != REPORT_SPEC_SCHEMA:
        raise ValueError(f"unsupported report specification schema: {spec.get('schema')}")
    if spec.get("report_name") != REPORT_NAME:
        raise ValueError(f"report specification has the wrong report name: {spec.get('report_name')}")
    if spec.get("top_level_sections") != TOP_LEVEL_SECTIONS:
        raise ValueError("report specification top-level section order has drifted")
    actual_hash = hashlib.sha256(facts_path.read_bytes()).hexdigest()
    if spec.get("facts_sha256") != actual_hash:
        raise ValueError("report specification is stale for the current report_facts.json")


def validate_report_text(report_text: str, facts: dict, spec: dict | None = None) -> dict:
    """Fail on contradictory facts, unresolved placeholders, unsupported prose, or blank pages."""
    normalized = _normalized(report_text)
    if not normalized:
        raise ValueError("extracted PDF text is empty")

    placeholder = re.search(r"\b(?:None|NaN|null)\b", report_text)
    if placeholder:
        raise ValueError(f"report contains unresolved placeholder: {placeholder.group(0)}")

    assert_report_claims_bounded(normalized)
    assert_report_free_of_record_asset_inference(normalized)
    assert_report_free_of_strategic_inference(normalized)

    if spec:
        lines = [_normalized(line) for line in report_text.splitlines()]
        positions = [lines.index(section) if section in lines else -1 for section in spec["top_level_sections"]]
        if any(position < 0 for position in positions) or positions != sorted(positions):
            raise ValueError("report is missing the required top-level sections in canonical order")

    if int(facts.get("n_expanded_access", 0)) > 0 and re.search(
        r"\bregistered(?:-|\s+)trials?\b", normalized, re.I
    ):
        raise ValueError(
            "report labels a mixed trial/expanded-access dataset as registered trials; "
            "use registered records"
        )

    figure_labels = list(re.finditer(r"\bFigure\s+(\d+)\b", normalized, re.I))
    if not figure_labels:
        raise ValueError(
            "report must caption the GenerateImage infographic as Figure 1 so its visual order "
            "is machine-checkable"
        )
    first_figure = figure_labels[0]
    first_figure_end = (
        figure_labels[1].start() if len(figure_labels) > 1 else first_figure.start() + 300
    )
    first_figure_context = normalized[first_figure.start():first_figure_end]
    if int(first_figure.group(1)) != 1 or "infographic" not in first_figure_context.casefold():
        raise ValueError(
            "GenerateImage infographic must be Figure 1 and the first substantive visual near "
            "the beginning of the report"
        )

    if spec:
        captions = list(re.finditer(r"(?mi)^[ \t]*Figure[ \t]+(\d+)[.:—-][ \t]*", report_text))
        expected = [int(figure["number"]) for figure in spec["figures"]]
        if [int(match.group(1)) for match in captions] != expected:
            raise ValueError("report figure captions do not match the complete required sequence")
        for index, (match, figure) in enumerate(zip(captions, spec["figures"])):
            end = captions[index + 1].start() if index + 1 < len(captions) else len(report_text)
            if figure.get("caption") and _normalized(figure["caption"]) not in _normalized(report_text[match.end():end]):
                raise ValueError(f"Figure {figure['number']} is missing its required caption")

    availability = _normalized(facts.get("match_evidence_availability_sentence", ""))
    if not availability or normalized.count(availability) < 2:
        raise ValueError(
            "report must quote match_evidence_availability_sentence in Methods & Sources and Results"
        )

    sponsor_sentence = _normalized(facts.get("sponsor_composition_sentence", ""))
    if not sponsor_sentence or sponsor_sentence not in normalized:
        raise ValueError("report must quote sponsor_composition_sentence from report_facts.json")

    n_sponsors = int(facts.get("n_sponsors", -1))
    for match in re.finditer(r"\b\d+\s+of\s+(\d+)\s+(?:unique\s+)?sponsors\b", normalized, re.I):
        if int(match.group(1)) != n_sponsors:
            raise ValueError(
                f"report uses a non-sponsor denominator for sponsors: {match.group(0)!r}; "
                f"expected denominator {n_sponsors}"
            )

    query = facts.get("query", {})
    aliases = extract_positive_query_terms(
        query.get("intervention_filter") if isinstance(query, dict) else None
    )
    missing_ampersand_aliases = [alias for alias in aliases if "&" in alias and alias not in normalized]
    if missing_ampersand_aliases:
        raise ValueError(
            "extracted PDF text does not preserve displayed ampersand aliases: "
            + ", ".join(missing_ampersand_aliases)
        )

    pages = report_text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    thin_pages = [
        index for index, page in enumerate(pages, 1)
        if len(_normalized(page)) < 120
    ]
    if thin_pages:
        raise ValueError(
            "report contains effectively blank/orphaned page(s): "
            + ", ".join(str(page) for page in thin_pages)
        )

    protected_headings = {
        "task context", "methods & sources", "results", "conclusions & interpretation",
        "limitations", "data source", "query parameters", "classification and match evidence",
        "coverage boundary", "study composition", "intervention categories",
        "phase distribution", "sponsor composition", "geography", "enrollment",
        "output artifacts", "references",
    }
    orphaned_headings = []
    for page_number, page in enumerate(pages[:-1], 1):
        lines = [_normalized(line) for line in page.splitlines() if _normalized(line)]
        content_lines = [line for line in lines if not re.fullmatch(r"Page\s+\d+", line, re.I)]
        if content_lines and content_lines[-1].casefold() in protected_headings:
            orphaned_headings.append(f"page {page_number}: {content_lines[-1]}")
    if orphaned_headings:
        raise ValueError(
            "report contains heading(s) orphaned at a page bottom: " + "; ".join(orphaned_headings)
        )

    return {
        "pages_checked": len(pages),
        "availability_sentence_occurrences": normalized.count(availability),
        "sponsor_sentence_verified": True,
        "infographic_first_visual_verified": True,
        "orphaned_headings_verified": True,
        "ampersand_aliases_verified": [alias for alias in aliases if "&" in alias],
    }


def validate_report_output(
    text_artifact: str | pathlib.Path,
    facts_artifact: str | pathlib.Path = "report_facts.json",
    spec_artifact: str | pathlib.Path = "report_spec.json",
    *,
    review_attestation: str,
    review_verdict: str,
    review_issues: list[str],
    review_performed: bool = False,
) -> dict:
    """Load results-root artifacts and apply validate_report_text()."""
    results = pathlib.Path(os.environ.get("BIOMNI_RESULTS", "/mnt/results"))

    def resolve(path: str | pathlib.Path) -> pathlib.Path:
        candidate = pathlib.Path(path)
        return candidate if candidate.is_absolute() else results / candidate

    text_path = resolve(text_artifact)
    facts_path = resolve(facts_artifact)
    spec_path = resolve(spec_artifact)
    if not text_path.is_file():
        raise ValueError(f"extracted PDF text artifact is missing: {text_path}")
    if not facts_path.is_file():
        raise ValueError(f"report facts artifact is missing: {facts_path}")
    if not spec_path.is_file():
        raise ValueError(f"report specification artifact is missing: {spec_path}")
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    if not isinstance(facts, dict):
        raise ValueError("report_facts.json must contain a JSON object")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError("report_spec.json must contain a JSON object")
    validate_report_spec(spec, facts_path)
    validate_visual_review_attestation(review_attestation, review_verdict, review_issues, review_performed=review_performed)
    result = validate_report_text(
        text_path.read_text(encoding="utf-8", errors="replace"),
        facts,
        spec,
    )
    result["visual_review"] = {
        "verdict": review_verdict,
        "performed": review_performed,
        "issues": review_issues,
        "attestation": review_attestation,
        "method": "reviewer attestation; not independent proof of visual inspection",
    }
    result["report_ready"] = review_performed and review_verdict == "pass" and not review_issues
    return result


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(
        "Import validate_report_output and supply the recorded visual-review attestation, "
        "verdict, and issues; the text path alone cannot prove visual review."
    )
