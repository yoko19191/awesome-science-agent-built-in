#!/usr/bin/env python3
"""Build presentation-independent Open Targets report content from gated artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib


REPORT_CONTENT_SCHEMA = "open-targets-report-content/1"
REPORT_STRUCTURE_SCHEMA = "open-targets-report-structure/1"
REQUIRED_INPUTS = ("report_facts.json", "provenance.json")
REQUIRED_SECTIONS = (
    "Task Context",
    "Methods and Sources",
    "Results",
    "Conclusions and Interpretation",
    "Limitations",
    "References",
    "Suggested Next Steps",
)


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_report_content(results_dir: pathlib.Path) -> dict:
    """Return the single style-free content object used by every report provider."""
    loaded: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    for name in REQUIRED_INPUTS:
        path = results_dir / name
        if not path.is_file():
            raise ValueError(f"required report-content input is missing: {path}")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"required report-content input is not an object: {path}")
        loaded[name] = value
        sources[name] = {
            "path": name,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }

    figures = [
        {
            "file": "figures/figure_1_target_ranking.png",
            "placement": "Results/Target Ranking",
            "data_bearing": True,
        },
        {
            "file": "figures/figure_2_datatype_heatmap.png",
            "placement": "Results/Datatype Contributions",
            "data_bearing": True,
        },
    ]
    return {
        "schema": REPORT_CONTENT_SCHEMA,
        "presentation": None,
        "required_sections": list(REQUIRED_SECTIONS),
        "sources": sources,
        "facts": loaded["report_facts.json"],
        "provenance": loaded["provenance.json"],
        "figure_placements": figures,
        "infographic": {
            "file": "infographic_open_targets_workflow.png",
            "placement": "first_substantive_visual_page_1",
            "data_bearing": False,
        },
    }


def write_report_content(
    results_dir: pathlib.Path,
    output: pathlib.Path | None = None,
) -> pathlib.Path:
    destination = output or results_dir / "report_content.json"
    content = build_report_content(results_dir)
    structure = {
        "schema": REPORT_STRUCTURE_SCHEMA,
        "section_order": list(REQUIRED_SECTIONS),
        "figure_placements": content["figure_placements"],
        "infographic": content["infographic"],
        "standalone_figures_section_allowed": False,
    }
    (results_dir / "report_structure.json").write_text(
        json.dumps(structure, indent=2, sort_keys=True), encoding="utf-8"
    )
    destination.write_text(json.dumps(content, indent=2, sort_keys=True), encoding="utf-8")
    return destination


def load_or_create_report_content(results_dir: pathlib.Path) -> tuple[pathlib.Path, dict]:
    path = results_dir / "report_content.json"
    expected = build_report_content(results_dir)
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != expected:
            raise ValueError("report_content.json is stale or disagrees with gated inputs")
        if not (results_dir / "report_structure.json").is_file():
            write_report_content(results_dir, path)
        return path, existing
    write_report_content(results_dir, path)
    return path, expected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    results_dir = pathlib.Path(args.results_dir)
    output = pathlib.Path(args.output) if args.output else None
    print(write_report_content(results_dir, output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
