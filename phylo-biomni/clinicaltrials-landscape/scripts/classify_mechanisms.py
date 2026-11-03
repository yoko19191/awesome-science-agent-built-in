"""
Backwards-compatibility shim for the former mechanism classifier.

The classifier was rewritten as a record-grounded study-purpose + intervention-type taxonomy in
``classify_study.py`` (mechanism-of-action buckets are no longer invented from generic interventions).
This module preserves the historical import path so existing callers of
``scripts.classify_mechanisms.classify_all`` keep working: it delegates to ``classify_study`` for the
new ``study_purpose`` / ``intervention_category`` / ``classification_resolved`` / ``mechanism`` fields
and then restores the five fields the historical ``classify_all`` documented:

* ``phase_normalized``      - display phase label (``compile_trials._normalize_phase``)
* ``drug_names``            - DRUG / BIOLOGICAL intervention names (raw records); every retained
                              intervention name on the compiled DataFrame path (types are not kept)
* ``drug_names_normalized`` - ``drug_names`` mapped through the disease config ``drug_normalization``
                              regex table (identity when no config is supplied)
* ``is_industry``           - lead-sponsor class INDUSTRY (``compile_trials.is_industry_sponsor``)
* ``is_biosimilar``         - biosimilar product cue in intervention names/descriptions or titles

``mechanism`` equals ``intervention_category`` (or a config-driven label when a disease config is
supplied) so legacy consumers of ``mechanism`` continue to receive a sensible value.

Prefer importing from ``classify_study`` in new code.
"""

from __future__ import annotations

import re

import pandas as pd

try:
    from classify_study import (  # noqa: F401
        classify_all as _study_classify_all,
        classify_record,
        classify_intervention_category,
        classify_study_purpose,
        extract_evidence,
        INTERVENTION_CATEGORIES,
        STUDY_PURPOSES,
    )
    from compile_trials import PHASE_MAP, _normalize_phase, is_industry_sponsor  # noqa: F401
    from disease_config import get_drug_normalization
except ImportError:  # pragma: no cover - package vs. flat execution shim
    from scripts.classify_study import (  # noqa: F401
        classify_all as _study_classify_all,
        classify_record,
        classify_intervention_category,
        classify_study_purpose,
        extract_evidence,
        INTERVENTION_CATEGORIES,
        STUDY_PURPOSES,
    )
    from scripts.compile_trials import PHASE_MAP, _normalize_phase, is_industry_sponsor  # noqa: F401
    from scripts.disease_config import get_drug_normalization


LEGACY_FIELDS = ("phase_normalized", "drug_names", "drug_names_normalized", "is_industry", "is_biosimilar")

_DRUG_LIKE_TYPES = ("DRUG", "BIOLOGICAL")

# Historical biosimilar cues (product names and generic phrases), matched on lowercase text.
_BIOSIMILAR_CUES = (
    "biosimilar", "ct-p13", "sb2", "sb5", "abp 501", "gp2017",
    "remsima", "inflectra", "renflexis", "avsola", "ixifi",
    "hadlima", "hyrimoz", "cyltezo", "amjevita", "idacio",
    "similar biologic", "proposed biosimilar",
)


def _text(value) -> str:
    return "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)


def _drug_names(interventions) -> list[str]:
    return [
        intv.get("name", "")
        for intv in (interventions or [])
        if isinstance(intv, dict) and intv.get("type") in _DRUG_LIKE_TYPES and intv.get("name")
    ]


def _normalize_drug_name(name: str, normalization: dict) -> str:
    """Map a drug name to its canonical form through the config regex table (identity otherwise)."""
    if not name:
        return name
    stripped = name.strip()
    for pattern, canonical in normalization.items():
        if re.match(pattern, stripped):
            return canonical
    return stripped


def _is_biosimilar_text(corpus: str) -> bool:
    lowered = corpus.lower()
    return any(cue in lowered for cue in _BIOSIMILAR_CUES)


def _legacy_fields_for_record(trial: dict, normalization: dict) -> dict:
    interventions = trial.get("interventions") or []
    drug_names = _drug_names(interventions)
    corpus = " ".join(
        [_text(intv.get("name", "")) + " " + _text(intv.get("description", ""))
         for intv in interventions if isinstance(intv, dict)]
        + [_text(trial.get("brief_title", "")), _text(trial.get("official_title", ""))]
    )
    return {
        "phase_normalized": trial.get("phase_normalized") or _normalize_phase(trial.get("phases", [])),
        "drug_names": drug_names,
        "drug_names_normalized": [_normalize_drug_name(name, normalization) for name in drug_names],
        "is_industry": is_industry_sponsor(trial.get("sponsor_class", "")),
        "is_biosimilar": _is_biosimilar_text(corpus),
    }


def _legacy_fields_for_row(row, normalization: dict) -> dict:
    phase = _text(row.get("phase_normalized", "")).strip()
    if not phase:
        phases = row.get("phases", None)
        phase = _normalize_phase(list(phases)) if isinstance(phases, (list, tuple)) else "Not Applicable"
    # Compiled rows keep names but not intervention types: reuse the evidence extractor's split.
    drug_names = list(extract_evidence(row)["intervention_names"])
    corpus = " ".join(
        _text(row.get(col, ""))
        for col in ("intervention_names_str", "drug_names_str", "intervention_descriptions_str",
                    "brief_title", "official_title")
    )
    return {
        "phase_normalized": phase,
        "drug_names": drug_names,
        "drug_names_normalized": [_normalize_drug_name(name, normalization) for name in drug_names],
        "is_industry": is_industry_sponsor(_text(row.get("sponsor_class", ""))),
        "is_biosimilar": _is_biosimilar_text(corpus),
    }


def classify_all(data, config=None):
    """Classify every trial and restore the historical field set.

    Accepts a list of raw records or a compiled DataFrame. For a list, mutates each dict in place and
    returns the list; for a DataFrame, returns a copy with the columns added. Adds the record-grounded
    fields from ``classify_study.classify_all`` plus ``LEGACY_FIELDS``.
    """
    normalization = get_drug_normalization(config)
    result = _study_classify_all(data, config=config)

    if hasattr(result, "columns") and hasattr(result, "iterrows"):
        legacy = [_legacy_fields_for_row(row, normalization) for _, row in result.iterrows()]
        for field in LEGACY_FIELDS:
            result[field] = pd.Series([entry[field] for entry in legacy], index=result.index, dtype=object)
        for field in ("is_industry", "is_biosimilar"):
            result[field] = result[field].astype(bool)
        return result

    for trial in result:
        trial.update(_legacy_fields_for_record(trial, normalization))
    return result


def classify_mechanism(interventions, brief_title="", official_title=""):
    """Legacy signature shim.

    Historically returned ``(mechanism_label, drug_names)``. It now returns
    ``(intervention_category, drug_names)`` from the record-grounded classifier so old call sites keep
    working without inventing a mechanism-of-action label.
    """
    record = {
        "interventions": interventions or [],
        "brief_title": brief_title,
        "official_title": official_title,
        "study_type": "INTERVENTIONAL",
    }
    ev = extract_evidence(record)
    category = classify_intervention_category(ev)
    return category, _drug_names(interventions)


__all__ = [
    "classify_all",
    "classify_record",
    "classify_mechanism",
    "classify_intervention_category",
    "classify_study_purpose",
    "extract_evidence",
    "INTERVENTION_CATEGORIES",
    "STUDY_PURPOSES",
    "LEGACY_FIELDS",
    "PHASE_MAP",
    "_normalize_phase",
]
