#!/usr/bin/env python3
"""Objective report checks; scientific and visual judgment remain external."""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

DEFAULT_PROVIDER = "pdf-report-generation"
MIN_REPORT_PDF_BYTES = 10_000
PDF_RENDER_DPI = "96"
PDF_RENDER_TIMEOUT_SECONDS = 120
RENDER_ERROR_TAIL_CHARACTERS = 500
MIN_INFOGRAPHIC_PNG_BYTES = 24
SYSTEM_STYLE_ROOT = pathlib.Path("/mnt/skills/system")
USER_STYLE_ROOT = pathlib.Path("/mnt/skills/user")
PERSONAL_STYLE_ROOT = pathlib.Path("/mnt/skills/personal")
PROVIDER_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_NAME_RE = re.compile(r"^name:\s*['\"]?([^'\"\n]+)", re.MULTILINE)
INTERNAL_COPY = re.compile(r"\b(?:skill_spec|run_receipt)\.json\b|\breport style provider\s*:", re.I)


class ReportFailure(RuntimeError):
    """An objective report, provider, or lineage check failed."""


def _strict_json(path: pathlib.Path) -> object:
    def reject(value: str) -> None:
        raise ValueError(f"non-finite JSON value {value!r}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject)


def _provider_directory(provider: str, *, explicit: bool) -> pathlib.Path:
    if not PROVIDER_RE.fullmatch(provider):
        raise ReportFailure("report provider must be a lowercase skill slug")
    roots = (SYSTEM_STYLE_ROOT, USER_STYLE_ROOT, PERSONAL_STYLE_ROOT) if explicit else (SYSTEM_STYLE_ROOT,)
    for root in roots:
        candidate = root / provider
        skill = candidate / "SKILL.md"
        if not skill.is_file():
            continue
        match = FRONTMATTER_NAME_RE.search(skill.read_text(encoding="utf-8"))
        if match and match.group(1).strip() == provider:
            return candidate
    raise ReportFailure(f"installed report provider is unavailable: {provider}")


def select_provider(spec: dict, provider: str | None, *, explicit: bool) -> dict:
    presentation = spec.get("presentation", {})
    default = presentation.get("default_provider")
    if default != DEFAULT_PROVIDER:
        raise ReportFailure(f"report default must be {DEFAULT_PROVIDER}")
    selected = provider or default
    if selected != default and not explicit:
        raise ReportFailure("non-default report styling requires an explicit user request")
    if selected == default:
        explicit = False
    directory = _provider_directory(selected, explicit=explicit)
    return {
        "provider": selected,
        "selection": "explicit_override" if explicit else "spec_default",
        "source": str(directory / "SKILL.md"),
    }


def _json_value(data: object, dotted_path: str) -> object:
    value = data
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ReportFailure(f"facts field is absent: {dotted_path!r}")
        value = value[part]
    return value


def _validate_partitions(spec: dict, facts: dict) -> None:
    groups = spec.get("presentation", {}).get("partition_groups", [])
    for group in groups:
        denominator = _json_value(facts, group["denominator_field"])
        members = [_json_value(facts, field) for field in group["member_fields"]]
        values = [denominator, *members]
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            raise ReportFailure(f"partition {group['name']!r} must be numeric")
        if group.get("value_type") == "integer":
            if not all(isinstance(value, int) for value in values):
                raise ReportFailure(f"integer partition {group['name']!r} contains non-integers")
            valid = sum(members) == denominator
        else:
            tolerance = group.get("tolerance")
            if not isinstance(tolerance, dict):
                raise ReportFailure(f"floating partition {group['name']!r} needs a tolerance")
            valid = math.isclose(
                float(sum(members)),
                float(denominator),
                abs_tol=float(tolerance.get("abs_tol", 0)),
                rel_tol=float(tolerance.get("rel_tol", 0)),
            )
        if not valid:
            raise ReportFailure(f"partition {group['name']!r} does not account")


def _validate_fact_bindings(spec: dict, facts: dict, results: pathlib.Path, source_evidence: dict[str, str]) -> None:
    """Bind integer report claims to the exact recorded JSON analysis bytes."""
    presentation = spec.get("presentation", {})
    bindings = presentation.get("fact_bindings", [])
    if not isinstance(bindings, list):
        raise ReportFailure("presentation.fact_bindings must be an array")
    json_outputs = {item["path"] for item in spec.get("analysis", {}).get("outputs", []) if item.get("kind") == "json"}
    bound_fields = set()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"fact_field", "source_output", "source_field", "value_type"}:
            raise ReportFailure("malformed fact binding")
        for field in ("fact_field", "source_field"):
            value = binding[field]
            if not isinstance(value, str) or not all(part and part.strip() == part for part in value.split(".")):
                raise ReportFailure(f"fact binding {field} must be a nonempty dotted object path")
        fact_field = binding["fact_field"]
        if fact_field in bound_fields:
            raise ReportFailure(f"duplicate fact binding: {fact_field}")
        bound_fields.add(fact_field)
        source = binding["source_output"]
        if not isinstance(source, str) or source not in json_outputs or source not in presentation.get("source_outputs", []):
            raise ReportFailure("fact binding source_output must be a declared JSON presentation source")
        relative = pathlib.PurePosixPath(source)
        if relative.is_absolute() or ".." in relative.parts or "\\" in source:
            raise ReportFailure("fact binding source_output must stay inside results")
        path = results / source
        if path.is_symlink() or not path.resolve().is_relative_to(results.resolve()):
            raise ReportFailure("fact binding source_output must stay inside results")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != source_evidence.get(source):
            raise ReportFailure(f"fact binding source hash differs: {source}")
        def reject_nonfinite(value: str) -> None:
            raise ValueError(f"non-finite JSON value {value!r}")
        source_data = json.loads(data.decode("utf-8"), parse_constant=reject_nonfinite)
        observed = _json_value(facts, fact_field)
        expected = _json_value(source_data, binding["source_field"])
        if binding["value_type"] != "integer" or type(observed) is not int or type(expected) is not int:
            raise ReportFailure(f"fact binding {fact_field!r} requires integer values")
        if observed != expected:
            raise ReportFailure(f"report fact {fact_field!r} differs from {source}:{binding['source_field']}")


def _render_pdf(report: pathlib.Path) -> dict:
    if report.stat().st_size < MIN_REPORT_PDF_BYTES or report.read_bytes()[:5] != b"%PDF-":
        raise ReportFailure(f"required PDF is absent, malformed, or implausibly small: {report.name}")
    if not shutil.which("pdftotext") or not shutil.which("pdftoppm"):
        raise ReportFailure("pdftotext and pdftoppm are required for objective report validation")
    with tempfile.TemporaryDirectory(prefix="phylo-report-qc-") as temporary:
        root = pathlib.Path(temporary)
        text_path = root / "report.txt"
        prefix = root / "page"
        commands = (
            ["pdftotext", "-layout", str(report), str(text_path)],
            ["pdftoppm", "-png", "-r", PDF_RENDER_DPI, str(report), str(prefix)],
        )
        for argv in commands:
            try:
                completed = subprocess.run(argv, capture_output=True, text=True, timeout=PDF_RENDER_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as exc:
                raise ReportFailure(f"report rendering timed out: {argv[0]}") from exc
            if completed.returncode:
                raise ReportFailure(f"report rendering failed: {completed.stderr[-RENDER_ERROR_TAIL_CHARACTERS:]}")
        text = text_path.read_text(encoding="utf-8", errors="replace")
        pages = sorted(root.glob("page-*.png"))
        if not text.strip() or not pages or any(page.stat().st_size == 0 for page in pages):
            raise ReportFailure("rendered report text or pages are empty")
        if INTERNAL_COPY.search(text):
            raise ReportFailure("report exposes internal package or receipt terminology")
        return {"page_count": len(pages), "text_bytes": len(text.encode("utf-8"))}


def validate_objective_report(
    spec: dict,
    *,
    results: pathlib.Path,
    provider: str | None,
    explicit_provider: bool,
    source_evidence: dict[str, str] | None = None,
) -> dict:
    """Validate objective properties only; do not infer scientific or visual quality."""
    if spec.get("presentation_profile") != "scientific-report":
        raise ReportFailure("objective report validation is only valid for scientific-report")
    facts_path = results / "report_facts.json"
    infographic = results / "infographic.png"
    report = results / f"report_{spec['slug']}.pdf"
    facts = _strict_json(facts_path)
    if not isinstance(facts, dict):
        raise ReportFailure("report_facts.json must be an object")
    expected_sources = source_evidence or {}
    if facts.get("source_outputs") != expected_sources:
        raise ReportFailure("report facts are not bound to the current analysis output hashes")
    _validate_partitions(spec, facts)
    _validate_fact_bindings(spec, facts, results, expected_sources)
    if not infographic.is_file() or infographic.stat().st_size < MIN_INFOGRAPHIC_PNG_BYTES:
        raise ReportFailure("scientific-report requires an opening infographic artifact")
    with infographic.open("rb") as handle:
        if handle.read(8) != b"\x89PNG\r\n\x1a\n":
            raise ReportFailure("infographic.png is not a valid PNG")
    provider_detail = select_provider(spec, provider, explicit=explicit_provider)
    rendered = _render_pdf(report)
    return {
        "profile": "scientific-report",
        "provider": provider_detail,
        "report": report.name,
        "facts": facts_path.name,
        "infographic": infographic.name,
        "source_outputs": expected_sources,
        **rendered,
    }
