#!/usr/bin/env python3
"""Artifact-level enforcement for the DRUG-seq no-infographic policy."""

from __future__ import annotations

import json
import pathlib


INFOGRAPHIC_TOKEN = "infographic"
IMAGE_GENERATION_TOOL_MARKERS = frozenset({
    "generateimage", "generateimagesnapshot", "imagegen", "imagegenimagegen", "generatedimage",
})


def _contains_infographic(value: object) -> bool:
    return INFOGRAPHIC_TOKEN in str(value).casefold()


def _tool_marker(value: object) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


def assert_no_infographic_artifacts(
    results_root: str | pathlib.Path,
    declared_infographics: list[str] | tuple[str, ...],
    ledger_events: list[dict],
    declared_figures: list[dict],
) -> dict:
    """Reject infographic declarations, files, manifest entries, and generation events."""
    root = pathlib.Path(results_root)
    if declared_infographics:
        raise ValueError("infographic policy is not applicable but infographic files were declared")

    generated_events = []
    for index, event in enumerate(ledger_events):
        if not isinstance(event, dict):
            continue
        markers = {
            _tool_marker(event.get("type", "")),
            _tool_marker(event.get("name", "")),
            _tool_marker(event.get("tool_name", "")),
        }
        if markers.intersection(IMAGE_GENERATION_TOOL_MARKERS) or any(
            _contains_infographic(value) for value in (
            event.get("filename", ""), event.get("artifact", "")
            )
        ):
            generated_events.append(index)
    if generated_events:
        raise ValueError(
            "infographic policy is not applicable but generation/snapshot events were recorded: "
            + ", ".join(map(str, generated_events))
        )

    artifact_files = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and _contains_infographic(path.name)
    )
    if artifact_files:
        raise ValueError(
            "infographic policy is not applicable but result artifacts were produced: "
            + ", ".join(artifact_files)
        )

    manifest_entries = list(declared_figures)
    manifest_path = root / "figures" / "manifest.json"
    if manifest_path.is_file():
        try:
            parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot inspect figure manifest for infographic artifacts: {exc}") from exc
        if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
            raise ValueError("figure manifest must be an array of objects")
        manifest_entries.extend(parsed)
    offending_entries = [
        entry for entry in manifest_entries
        if _contains_infographic(entry.get("file", ""))
        or _contains_infographic(entry.get("caption", ""))
    ]
    if offending_entries:
        raise ValueError("figure manifest/declaration contains an infographic entry")

    return {
        "status": "not_applicable",
        "declared_infographics": [],
        "infographic_files": [],
        "infographic_manifest_entries": [],
        "generateimage_events": [],
        "figure_manifest": (
            str(manifest_path.relative_to(root)) if manifest_path.is_file() else None
        ),
    }
