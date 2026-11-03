#!/usr/bin/env python3
"""Load the complete user-facing bibliography required by the DRUG-seq report."""

from __future__ import annotations

import json
import pathlib


CATALOG = pathlib.Path(__file__).resolve().parent.parent / "references" / "report_references.json"
REFERENCE_FIELDS = {"id", "authors", "year", "title", "venue", "locator"}


def load_references() -> list[dict]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict) or catalog.get("schema") != "phylo-report-references/1":
        raise ValueError("DRUG-seq report reference catalog has an unsupported schema")
    references = catalog.get("references")
    if not isinstance(references, list) or not references:
        raise ValueError("DRUG-seq report reference catalog is empty")
    if any(not isinstance(item, dict) or set(item) != REFERENCE_FIELDS for item in references):
        raise ValueError("DRUG-seq report references must use the complete canonical field set")
    if len({item["id"] for item in references}) != len(references):
        raise ValueError("DRUG-seq report reference identifiers must be unique")
    if any(not str(item[field]).strip() for item in references for field in REFERENCE_FIELDS):
        raise ValueError("DRUG-seq report reference records cannot contain blank fields")
    return references


def citation_marker(reference_id: str) -> str:
    """Return the canonical numbered inline marker for one catalog reference."""
    normalized_id = str(reference_id).strip()
    for number, item in enumerate(load_references(), 1):
        if item["id"] == normalized_id:
            return f"[{number}]"
    raise ValueError(f"unknown DRUG-seq report reference identifier: {reference_id!r}")


def reference_paragraphs() -> list[str]:
    return [
        f"[{number}] {item['authors']} ({item['year']}). {item['title']}. "
        f"{item['venue']}. {item['locator']}"
        for number, item in enumerate(load_references(), 1)
    ]
