#!/usr/bin/env python3
"""Validate the single authoring and runtime specification for generated skills."""

from __future__ import annotations

import json
import math
import pathlib
import re

SCHEMA = "phylo-analysis-spec/1"
PROFILES = {"artifacts-only", "scientific-report"}
LANGUAGES = {"python", "r"}
OUTPUT_KINDS = {"csv", "json", "image", "file"}
PARAMETER_TYPES = {"string", "integer", "number", "boolean"}
CAVEAT_KINDS = {"assumption", "limitation", "watch-out"}
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILL_ID_RE = re.compile(r"^skill_[0-9a-f]{32}$")
FLAG_RE = re.compile(r"^--[a-z0-9]+(?:-[a-z0-9]+)*$")
CATEGORIES = {
    "data_analysis", "data_discovery", "drug_discovery", "epigenomics",
    "experimental_design", "functional_analysis", "functional_genomics", "general",
    "genomics_genetics", "integration", "literature", "molecular_design", "multi_omics",
    "pathway_analysis", "proteomics_metabolomics", "reporting", "transcriptomics",
}
VISIBILITIES = {"internal", "public", "shared"}
TOP_LEVEL_KEYS = {
    "schema", "id", "slug", "description", "category", "visibility", "starting_prompt",
    "objective", "input", "caveats", "presentation_profile", "analysis", "sources",
    "presentation", "guidance",
}
RECEIPT_PATH = "run_receipt.json"


def _reject_unknown(value: dict, allowed: set[str], where: str, errors: list[str]) -> None:
    if unknown := sorted(set(value).difference(allowed)):
        errors.append(f"{where} contains undeclared fields: {unknown}")


def canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"


def strict_json(path: pathlib.Path) -> object:
    def reject(value: str) -> None:
        raise ValueError(f"non-finite JSON value {value!r}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject)


def safe_relative(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or "\\" in value:
        return False
    path = pathlib.PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and "." not in path.parts


def presentation_outputs(spec: dict) -> list[dict]:
    if spec.get("presentation_profile") != "scientific-report":
        return []
    return [
        {"path": "report_facts.json", "kind": "json", "stage": "presentation"},
        {"path": "infographic.png", "kind": "image", "stage": "presentation"},
        {
            "path": f"report_{spec.get('slug', 'skill')}.pdf",
            "kind": "pdf",
            "stage": "presentation",
        },
    ]


def all_outputs(spec: dict) -> list[dict]:
    analysis = spec.get("analysis") if isinstance(spec, dict) else None
    analysis_outputs = analysis.get("outputs", []) if isinstance(analysis, dict) else []
    return [*analysis_outputs, *presentation_outputs(spec)]


def _matches_parameter_type(value: object, kind: object) -> bool:
    return (
        (kind == "string" and isinstance(value, str))
        or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool))
        or (
            kind == "number"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
        or (kind == "boolean" and isinstance(value, bool))
    )


def _validate_parameters(parameters: object, errors: list[str]) -> None:
    if not isinstance(parameters, list):
        errors.append("analysis.parameters must be an array")
        return
    names: set[str] = set()
    flags: set[str] = set()
    for item in parameters:
        if not isinstance(item, dict):
            errors.append("analysis parameters must be objects")
            continue
        _reject_unknown(
            item,
            {"name", "flag", "type", "required", "default", "choices", "description", "example"},
            "parameter",
            errors,
        )
        name, flag, kind = item.get("name"), item.get("flag"), item.get("type")
        if not isinstance(name, str) or not ID_RE.fullmatch(name):
            errors.append("parameter names must be lowercase-kebab IDs")
        elif name in names:
            errors.append(f"duplicate parameter name {name!r}")
        else:
            names.add(name)
        if not isinstance(flag, str) or not FLAG_RE.fullmatch(flag) or flag in {"--input", "--help"}:
            errors.append(f"parameter {name!r} has an invalid or reserved flag")
        elif flag in flags:
            errors.append(f"duplicate parameter flag {flag!r}")
        else:
            flags.add(flag)
        if kind not in PARAMETER_TYPES:
            errors.append(f"parameter {name!r} has unsupported type {kind!r}")
        if item.get("required") not in {True, False}:
            errors.append(f"parameter {name!r} must declare required as a boolean")
        if item.get("required") is False and "default" not in item:
            errors.append(f"optional parameter {name!r} needs a default")
        if "default" in item and not _matches_parameter_type(item["default"], kind):
            errors.append(f"parameter {name!r} default does not match declared type {kind!r}")
        if "example" in item and not _matches_parameter_type(item["example"], kind):
            errors.append(f"parameter {name!r} example does not match declared type {kind!r}")
        if not isinstance(item.get("description"), str) or not item["description"].strip():
            errors.append(f"parameter {name!r} needs a non-empty description")
        choices = item.get("choices")
        if choices is not None and (not isinstance(choices, list) or not choices):
            errors.append(f"parameter {name!r} choices must be a non-empty array")
        elif isinstance(choices, list):
            if any(not _matches_parameter_type(choice, kind) for choice in choices):
                errors.append(f"parameter {name!r} choices do not match declared type {kind!r}")
            if "default" in item and item["default"] not in choices:
                errors.append(f"parameter {name!r} default is not one of its choices")
            if "example" in item and item["example"] not in choices:
                errors.append(f"parameter {name!r} example is not one of its choices")


def _validate_outputs(outputs: object, reserved_paths: set[str], errors: list[str]) -> None:
    if not isinstance(outputs, list) or not outputs:
        errors.append("analysis.outputs must contain at least one output")
        return
    paths: set[str] = set()
    for output in outputs:
        if not isinstance(output, dict):
            errors.append("analysis outputs must be objects")
            continue
        _reject_unknown(output, {"path", "kind", "min_bytes"}, "analysis output", errors)
        path, kind = output.get("path"), output.get("kind")
        if not safe_relative(path):
            errors.append(f"analysis output path is unsafe: {path!r}")
        elif path in paths:
            errors.append(f"duplicate analysis output {path!r}")
        elif path in reserved_paths:
            errors.append(f"analysis output collides with a receipt or derived report path: {path!r}")
        elif any(
            pathlib.PurePosixPath(path).is_relative_to(other)
            or pathlib.PurePosixPath(other).is_relative_to(path)
            for other in paths | reserved_paths
        ):
            errors.append(f"analysis output overlaps another output or reserved path: {path!r}")
        else:
            paths.add(path)
        if kind not in OUTPUT_KINDS:
            errors.append(f"analysis output {path!r} has unsupported kind {kind!r}")
        minimum = output.get("min_bytes", 1)
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
            errors.append(f"analysis output {path!r} min_bytes must be a positive integer")
        if kind == "image" and not str(path).lower().endswith(".png"):
            errors.append(f"image output {path!r} must be PNG")


def _validate_workflow(workflow: object, errors: list[str]) -> None:
    if not isinstance(workflow, list) or len(workflow) < 2:
        errors.append("analysis.workflow must contain at least two ordered steps")
        return
    if not all(isinstance(step, str) and step.strip() for step in workflow):
        errors.append("analysis.workflow steps must be non-empty text")


def _validate_user_guidance(spec: dict, errors: list[str]) -> None:
    input_guidance = spec.get("input")
    if not isinstance(input_guidance, dict):
        errors.append("input must be an object")
    else:
        _reject_unknown(input_guidance, {"summary", "requirements", "reject_if"}, "input", errors)
        if not isinstance(input_guidance.get("summary"), str) or not input_guidance["summary"].strip():
            errors.append("input.summary must be non-empty text")
        for field in ("requirements", "reject_if"):
            values = input_guidance.get(field)
            if not isinstance(values, list) or not values or not all(
                isinstance(value, str) and value.strip() for value in values
            ):
                errors.append(f"input.{field} must contain non-empty text")

    caveats = spec.get("caveats")
    if not isinstance(caveats, list) or not caveats:
        errors.append("caveats must contain at least one scientific caveat")
        return
    for caveat in caveats:
        if not isinstance(caveat, dict):
            errors.append("caveats must be objects")
            continue
        _reject_unknown(caveat, {"kind", "text"}, "caveat", errors)
        if caveat.get("kind") not in CAVEAT_KINDS:
            errors.append(f"caveat kind must be one of {sorted(CAVEAT_KINDS)}")
        if not isinstance(caveat.get("text"), str) or not caveat["text"].strip():
            errors.append("caveat text must be non-empty")


def _validate_guidance(guidance: object, errors: list[str]) -> None:
    if guidance is None:
        return
    if not isinstance(guidance, list) or not guidance:
        errors.append("guidance must be a non-empty array when present")
        return
    paths: set[str] = set()
    for item in guidance:
        if not isinstance(item, dict):
            errors.append("guidance entries must be objects")
            continue
        _reject_unknown(item, {"path", "use_when"}, "guidance entry", errors)
        path = item.get("path")
        if not safe_relative(path) or not str(path).startswith("references/"):
            errors.append(f"guidance path must be a safe references/ path: {path!r}")
        elif path in paths:
            errors.append(f"duplicate guidance path {path!r}")
        else:
            paths.add(path)
        if not isinstance(item.get("use_when"), str) or not item["use_when"].strip():
            errors.append(f"guidance {path!r} needs non-empty use_when text")


def validate_spec(spec: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(spec, dict) or spec.get("schema") != SCHEMA:
        return [f"spec must be a {SCHEMA!r} object"]
    _reject_unknown(spec, TOP_LEVEL_KEYS, "spec", errors)
    slug = spec.get("slug")
    if not isinstance(slug, str) or not ID_RE.fullmatch(slug) or len(slug) >= 65:
        errors.append("slug must be lowercase-kebab and under 65 characters")
    if not isinstance(spec.get("id"), str) or not SKILL_ID_RE.fullmatch(spec["id"]):
        errors.append("id must be skill_ followed by 32 lowercase hex characters")
    if spec.get("category") not in CATEGORIES:
        errors.append("category is not a live catalog value")
    if spec.get("visibility") not in VISIBILITIES:
        errors.append("visibility must be internal, public, or shared")
    for field in ("description", "starting_prompt", "objective"):
        if not isinstance(spec.get(field), str) or not spec[field].strip():
            errors.append(f"{field} must be non-empty text")
    _validate_user_guidance(spec, errors)
    _validate_guidance(spec.get("guidance"), errors)
    profile = spec.get("presentation_profile")
    if profile not in PROFILES:
        errors.append(f"presentation_profile must be one of {sorted(PROFILES)}")
    analysis = spec.get("analysis")
    if not isinstance(analysis, dict):
        errors.append("analysis must be an object")
        return errors
    _reject_unknown(
        analysis,
        {"language", "entrypoint", "purpose", "workflow", "parameters", "outputs"},
        "analysis",
        errors,
    )
    if analysis.get("language") not in LANGUAGES:
        errors.append("analysis.language must be python or r")
    entrypoint = analysis.get("entrypoint")
    if not safe_relative(entrypoint) or not str(entrypoint).startswith("scripts/"):
        errors.append("analysis.entrypoint must be a safe scripts/ path")
    expected_suffix = ".py" if analysis.get("language") == "python" else ".R"
    if isinstance(entrypoint, str) and not entrypoint.endswith(expected_suffix):
        errors.append("analysis entrypoint extension does not match its language")
    _validate_workflow(analysis.get("workflow"), errors)
    _validate_parameters(analysis.get("parameters"), errors)
    reserved_paths = {RECEIPT_PATH, *(item["path"] for item in presentation_outputs(spec))}
    _validate_outputs(analysis.get("outputs"), reserved_paths, errors)
    outputs = analysis.get("outputs", [])
    output_paths = {
        item["path"] for item in outputs
        if isinstance(item, dict) and safe_relative(item.get("path"))
    } if isinstance(outputs, list) else set()
    if profile == "scientific-report":
        presentation = spec.get("presentation")
        if not isinstance(presentation, dict):
            errors.append("scientific-report needs a presentation object")
        else:
            _reject_unknown(
                presentation,
                {"default_provider", "explicit_provider_allowed", "source_outputs", "partition_groups"},
                "presentation",
                errors,
            )
            if presentation.get("default_provider") != "pdf-report-generation":
                errors.append("scientific-report default provider must be pdf-report-generation")
            if presentation.get("explicit_provider_allowed") is not True:
                errors.append("scientific-report must allow explicit compatible provider selection")
            source_paths = presentation.get("source_outputs")
            if not isinstance(source_paths, list) or not source_paths:
                errors.append("scientific-report needs presentation.source_outputs")
            elif not all(isinstance(path, str) and path.strip() for path in source_paths):
                errors.append("presentation.source_outputs entries must be non-empty strings")
            elif unknown := sorted(set(source_paths).difference(output_paths)):
                errors.append(f"presentation names unknown analysis outputs: {unknown}")
            groups = presentation.get("partition_groups", [])
            if not isinstance(groups, list):
                errors.append("presentation.partition_groups must be an array")
            else:
                for group in groups:
                    if not isinstance(group, dict):
                        errors.append("partition groups must be objects")
                        continue
                    _reject_unknown(
                        group,
                        {"name", "denominator_field", "member_fields", "value_type", "tolerance"},
                        "partition group",
                        errors,
                    )
                    if not all(isinstance(group.get(field), str) and group[field] for field in ("name", "denominator_field")):
                        errors.append("partition groups need name and denominator_field")
                    members = group.get("member_fields")
                    if not isinstance(members, list) or not members or not all(
                        isinstance(member, str) and member.strip() for member in members
                    ):
                        errors.append("partition groups need member_fields containing non-empty strings")
                    value_type = group.get("value_type")
                    if value_type not in {"integer", "floating"}:
                        errors.append("partition value_type must be integer or floating")
                    if value_type == "floating":
                        tolerance = group.get("tolerance")
                        if not isinstance(tolerance, dict):
                            errors.append("floating partitions need an explicit tolerance")
                        else:
                            _reject_unknown(tolerance, {"abs_tol", "rel_tol"}, "partition tolerance", errors)
                            values = [tolerance[field] for field in ("abs_tol", "rel_tol") if field in tolerance]
                            if not all(_matches_parameter_type(value, "number") and value >= 0 for value in values):
                                errors.append("each supplied partition tolerance must be finite and nonnegative")
                            elif not any(value > 0 for value in values):
                                errors.append("floating partition tolerance must have a positive abs_tol or rel_tol")
    elif "presentation" in spec:
        errors.append("artifacts-only specs must not carry presentation configuration")
    sources = spec.get("sources", [])
    if not isinstance(sources, list):
        errors.append("sources must be an array")
    else:
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                errors.append(f"source {index} must be an object")
            elif not all(
                isinstance(source.get(field), str) and source[field].strip()
                for field in ("name", "use", "reference")
            ):
                errors.append(f"source {index} needs non-empty name, use, and reference")
    return errors
