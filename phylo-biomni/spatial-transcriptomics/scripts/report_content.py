#!/usr/bin/env python3
"""Build the provider-neutral spatial-transcriptomics report content contract."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REPORT_CONTENT_SCHEMA = "spatial-transcriptomics-report-content/1"
REPORT_STRUCTURE_SCHEMA = "spatial-transcriptomics-report-structure/1"
REQUIRED_SECTIONS = (
    "Task Context",
    "Methods & Sources",
    "Results",
    "Conclusions & Interpretation",
    "Limitations",
    "References",
    "Output Artifacts",
)
REQUIRED_INPUTS = (
    "report_facts.json",
    "provenance.json",
    "platform_decision.json",
    "figures/manifest.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, expected_type: type) -> object:
    if not path.is_file():
        raise ValueError(f"required report-content input is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read report-content input {path}: {exc}") from exc
    if not isinstance(value, expected_type):
        raise ValueError(
            f"report-content input {path} must be {expected_type.__name__}, "
            f"not {type(value).__name__}"
        )
    return value


def _source(path: Path, results_dir: Path) -> dict:
    return {
        "path": str(path.relative_to(results_dir)),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _claim_bindings(facts: dict) -> list[dict]:
    de_status = "blocked" if facts.get("de_blocked") else str(facts.get("de_n_sig", 0))
    return [
        {"label": "Platform", "value": str(facts.get("platform", ""))},
        {
            "label": "Cells or spots passing QC",
            "value": str(facts.get("n_cells_or_spots", "")),
        },
        {"label": "Spatial niches", "value": str(facts.get("n_niches", ""))},
        {
            "label": "Significant spatially variable genes",
            "value": str(facts.get("n_svg_sig", "")),
        },
        {"label": "Differential-expression status", "value": de_status},
    ]


def build_report_content(results_dir: str | Path) -> dict:
    """Return one style-free object shared by the default and explicit providers."""
    root = Path(results_dir)
    paths = {name: root / name for name in REQUIRED_INPUTS}
    facts = _load_json(paths["report_facts.json"], dict)
    provenance = _load_json(paths["provenance.json"], dict)
    decision = _load_json(paths["platform_decision.json"], dict)
    manifest = _load_json(paths["figures/manifest.json"], list)

    figure_placements = []
    seen: set[str] = set()
    for raw in manifest:
        if not isinstance(raw, dict):
            raise ValueError("figures/manifest.json entries must be objects")
        rel = raw.get("file")
        caption = raw.get("caption")
        reason = raw.get("reason")
        step = raw.get("step")
        if not isinstance(step, int):
            raise ValueError(f"figure {rel!r} has no integer step")
        if rel is None:
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"step {step!r} declares no figure and gives no reason")
            figure_placements.append(
                {
                    "step": step,
                    "file": None,
                    "caption": None,
                    "reason": reason.strip(),
                    "placement": "Results",
                    "data_bearing": False,
                }
            )
            continue
        if not isinstance(rel, str) or not rel.startswith("figures/") or rel in seen:
            raise ValueError(f"invalid or duplicate figure path in manifest: {rel!r}")
        if not isinstance(caption, str) or not caption.strip():
            raise ValueError(f"figure {rel!r} has no caption")
        figure = root / rel
        try:
            figure.resolve(strict=True).relative_to(root.resolve(strict=True))
        except (FileNotFoundError, ValueError) as exc:
            raise ValueError(f"figure does not resolve beneath the results directory: {rel}") from exc
        if not figure.is_file() or figure.stat().st_size == 0:
            raise ValueError(f"figure is missing or empty: {figure}")
        seen.add(rel)
        figure_placements.append(
            {
                "step": step,
                "file": rel,
                "caption": caption,
                "reason": None,
                "placement": "Results",
                "data_bearing": True,
                "bytes": figure.stat().st_size,
                "sha256": _sha256(figure),
            }
        )

    sources = {name: _source(path, root) for name, path in paths.items()}
    return {
        "schema": REPORT_CONTENT_SCHEMA,
        "presentation": None,
        "required_sections": list(REQUIRED_SECTIONS),
        "required_claims": _claim_bindings(facts),
        "sources": sources,
        "facts": facts,
        "scientific_provenance": provenance,
        "platform_decision": decision,
        "figure_placements": figure_placements,
    }


def report_structure(content: dict) -> dict:
    return {
        "schema": REPORT_STRUCTURE_SCHEMA,
        "section_order": list(content["required_sections"]),
        "figure_placements": [
            {key: figure[key] for key in ("step", "file", "caption", "reason", "placement")}
            for figure in content["figure_placements"]
        ],
        "standalone_figures_section_allowed": False,
    }


def write_report_content(results_dir: str | Path) -> tuple[Path, Path]:
    root = Path(results_dir)
    content = build_report_content(root)
    content_path = root / "report_content.json"
    structure_path = root / "report_structure.json"
    content_path.write_text(json.dumps(content, indent=2, sort_keys=True), encoding="utf-8")
    structure_path.write_text(
        json.dumps(report_structure(content), indent=2, sort_keys=True), encoding="utf-8"
    )
    return content_path, structure_path


def load_verified_report_content(results_dir: str | Path) -> tuple[Path, Path, dict]:
    """Load content only when it still equals all current scientific inputs and figures."""
    root = Path(results_dir)
    content_path = root / "report_content.json"
    structure_path = root / "report_structure.json"
    if not content_path.is_file() or not structure_path.is_file():
        raise ValueError("report_content.json and report_structure.json must be created first")
    current = _load_json(content_path, dict)
    expected = build_report_content(root)
    if current != expected:
        raise ValueError("report_content.json is stale or disagrees with current scientific inputs")
    structure = _load_json(structure_path, dict)
    if structure != report_structure(expected):
        raise ValueError("report_structure.json is stale or disagrees with report_content.json")
    return content_path, structure_path, current


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", required=True)
    args = parser.parse_args(argv)
    content, structure = write_report_content(args.results_dir)
    print(json.dumps({"content": str(content), "structure": str(structure)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
