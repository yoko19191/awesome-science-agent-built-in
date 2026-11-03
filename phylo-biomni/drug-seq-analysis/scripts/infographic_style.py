#!/usr/bin/env python3
"""Select the DRUG-seq workflow prompt and bind it to provider and image evidence."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re

import report_qc

PACKAGE = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_PROVIDER = "pdf-report-generation"
DEFAULT_PROMPT = PACKAGE / "assets" / "infographic_prompt.txt"
OVERRIDE_TEMPLATE = PACKAGE / "assets" / "infographic_prompt_template.txt"
OVERRIDE_PROMPT_NAME = "infographic_prompt_selected.txt"
PROMPT_BINDING_NAME = "infographic_prompt_binding.json"
PROVIDER_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PLACEHOLDER = "{{STYLE_PROVIDER}}"
BINDING_SCHEMA = "phylo-infographic-prompt-binding/1"
PROMPT_CONTRACT_MARKERS = (
    "directional stages",
    "minimal text",
    "tables",
    "dashboards",
    "scientific result plots",
    "no logos",
    "wordmarks",
    "watermarks",
    "attribution marks",
    "no run values",
)


class InfographicStyleError(ValueError):
    """The selected provider cannot be bound to an exact infographic prompt."""


def _provider(selection: dict) -> str:
    provider = selection.get("provider") if isinstance(selection, dict) else None
    if not isinstance(provider, str) or not PROVIDER_RE.fullmatch(provider):
        raise InfographicStyleError("report-style selection has no valid provider slug")
    return provider


def expected_prompt(provider: str) -> str:
    """Return the exact workflow prompt required by the resolved provider."""
    if provider == DEFAULT_PROVIDER:
        return DEFAULT_PROMPT.read_text(encoding="utf-8")
    if not PROVIDER_RE.fullmatch(provider):
        raise InfographicStyleError(f"invalid report-style provider slug: {provider!r}")
    template = OVERRIDE_TEMPLATE.read_text(encoding="utf-8")
    if template.count(PLACEHOLDER) != 1:
        raise InfographicStyleError("enterprise infographic template needs one provider placeholder")
    return template.replace(PLACEHOLDER, provider)


def resolve_prompt(selection: dict, results: pathlib.Path) -> pathlib.Path:
    """Return the immutable default asset or write/verify the enterprise prompt."""
    provider = _provider(selection)
    if provider == DEFAULT_PROVIDER:
        return DEFAULT_PROMPT
    results = pathlib.Path(results)
    results.mkdir(parents=True, exist_ok=True)
    target = results / OVERRIDE_PROMPT_NAME
    expected = expected_prompt(provider)
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file() or target.read_text(encoding="utf-8") != expected:
            raise InfographicStyleError(f"existing enterprise infographic prompt is stale: {target}")
    else:
        target.write_text(expected, encoding="utf-8")
    return target


def assert_prompt_matches_provider(
    prompt_file: str | pathlib.Path,
    provider: str,
    results: pathlib.Path,
) -> dict[str, str]:
    """Fail unless the exact submitted prompt is derived from the selected provider."""
    path = pathlib.Path(prompt_file)
    if not path.is_absolute():
        package_candidate = PACKAGE / path
        path = package_candidate if package_candidate.is_file() else pathlib.Path(results) / path
    if path.is_symlink() or not path.is_file():
        raise InfographicStyleError(f"selected infographic prompt is missing: {path}")
    if provider != DEFAULT_PROVIDER:
        try:
            path.resolve(strict=True).relative_to(pathlib.Path(results).resolve(strict=True))
        except (FileNotFoundError, ValueError) as exc:
            raise InfographicStyleError(
                "enterprise infographic prompt must stay beneath the results root"
            ) from exc
    actual = path.read_text(encoding="utf-8")
    expected = expected_prompt(provider)
    if actual != expected:
        raise InfographicStyleError(
            f"infographic prompt does not match selected report-style provider {provider!r}"
        )
    normalized = actual.lower()
    missing = [marker for marker in PROMPT_CONTRACT_MARKERS if marker not in normalized]
    if missing:
        raise InfographicStyleError(
            f"infographic prompt lacks workflow-only contract markers: {missing}"
        )
    return {
        "provider": provider,
        "prompt_file": str(path),
        "prompt_sha256": hashlib.sha256(actual.strip().encode("utf-8")).hexdigest(),
    }


def record_provider_bound_infographic(
    filename: str,
    selection: dict,
    prompt_file: str | pathlib.Path,
    *,
    results: pathlib.Path | None = None,
    log_path: str | pathlib.Path = "qc_run_log.json",
) -> pathlib.Path:
    """Record exact provider/prompt/GenerateImage binding before report assembly."""
    root = pathlib.Path(results) if results is not None else report_qc.RESULTS
    provider = _provider(selection)
    prompt = assert_prompt_matches_provider(prompt_file, provider, root)
    trace = report_qc.assert_generated_by_tool(filename)
    if len(trace) != 1 or trace[0].get("prompt_sha256") != prompt["prompt_sha256"]:
        raise InfographicStyleError(
            "GenerateImage prompt does not match the exact provider-selected prompt"
        )
    image = root / pathlib.Path(filename).name
    if image.is_symlink() or not image.is_file():
        raise InfographicStyleError(f"generated infographic is missing: {image}")
    report_qc.record_generated_infographic(filename, log_path=log_path)
    binding = {
        "schema": BINDING_SCHEMA,
        "provider": provider,
        "selection": selection.get("selection"),
        "selection_evidence": selection.get("selection_evidence"),
        "prompt_file": prompt["prompt_file"],
        "prompt_sha256": prompt["prompt_sha256"],
        "generateimage": trace[0],
        "image": {
            "path": pathlib.Path(filename).name,
            "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "decoded_pixel_sha256": report_qc._decoded_pixel_sha256(image),
        },
    }
    target = root / PROMPT_BINDING_NAME
    target.write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
