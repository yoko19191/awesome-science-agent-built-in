"""
Compile classified ClinicalTrials.gov records into a structured analysis DataFrame (Step 2b).

Accepts either a list of classified raw API records (live path) or an already-compiled
``pandas.DataFrame`` (offline path). In both cases it guarantees a single, consistent set of derived
fields and an explicit phase-applicability flag, so downstream tables and figures never conflate the
all-retrieved denominator with the phase-applicable subset.

The compiled frame uses one therapeutic/pharmacological definition, carries an explicit
``phase_applicable`` denominator flag, and preserves every record-grounded classification field.
"""

import os
import re

import numpy as np
import pandas as pd

try:
    from classify_study import classify_all
except ImportError:  # pragma: no cover
    from scripts.classify_study import classify_all


PHASE_NUMERIC = {
    "Phase 1": 1, "Phase 1/2": 1.5, "Phase 2": 2, "Phase 2/3": 2.5,
    "Phase 3": 3, "Phase 3/4": 3.5, "Phase 4": 4, "Not Applicable": 0,
}
PHASE_ORDER = ["Phase 1", "Phase 1/2", "Phase 2", "Phase 2/3", "Phase 3", "Phase 3/4", "Phase 4"]

RECORD_COLUMNS = [
    'nct_id',
    'brief_title',
    'official_title',
    'brief_summary',
    'detailed_description',
    'lead_sponsor',
    'sponsor_normalized',
    'sponsor_class',
    'is_industry',
    'study_purpose',
    'intervention_category',
    'classification_resolved',
    'mechanism',
    'intervention_names_str',
    'intervention_descriptions_str',
    'drug_names_str',
    'phase_normalized',
    'overall_status',
    'conditions_str',
    'enrollment',
    'enrollment_type',
    'start_date',
    'start_year',
    'completion_date',
    'study_type',
    'countries_str',
    'n_countries',
    'regions_str',
    'study_design_category',
    'is_fda_regulated_drug',
]

COUNTRY_TO_REGION = {
    # North America
    "United States": "North America", "Canada": "North America", "Mexico": "North America",
    # Western Europe
    "United Kingdom": "Western Europe", "Germany": "Western Europe", "France": "Western Europe",
    "Italy": "Western Europe", "Spain": "Western Europe", "Netherlands": "Western Europe",
    "Belgium": "Western Europe", "Switzerland": "Western Europe", "Austria": "Western Europe",
    "Ireland": "Western Europe", "Sweden": "Western Europe", "Denmark": "Western Europe",
    "Norway": "Western Europe", "Finland": "Western Europe", "Portugal": "Western Europe",
    "Greece": "Western Europe", "Luxembourg": "Western Europe",
    # Eastern Europe
    "Poland": "Eastern Europe", "Czech Republic": "Eastern Europe", "Czechia": "Eastern Europe",
    "Hungary": "Eastern Europe", "Romania": "Eastern Europe", "Bulgaria": "Eastern Europe",
    "Slovakia": "Eastern Europe", "Croatia": "Eastern Europe", "Serbia": "Eastern Europe",
    "Slovenia": "Eastern Europe", "Estonia": "Eastern Europe", "Latvia": "Eastern Europe",
    "Lithuania": "Eastern Europe", "Ukraine": "Eastern Europe", "Russia": "Eastern Europe",
    "Russian Federation": "Eastern Europe", "Georgia": "Eastern Europe", "Moldova": "Eastern Europe",
    "Bosnia and Herzegovina": "Eastern Europe", "North Macedonia": "Eastern Europe",
    # Asia-Pacific
    "Japan": "Asia-Pacific", "China": "Asia-Pacific", "South Korea": "Asia-Pacific",
    "Korea, Republic of": "Asia-Pacific", "Taiwan": "Asia-Pacific", "India": "Asia-Pacific",
    "Australia": "Asia-Pacific", "New Zealand": "Asia-Pacific", "Singapore": "Asia-Pacific",
    "Malaysia": "Asia-Pacific", "Thailand": "Asia-Pacific", "Philippines": "Asia-Pacific",
    "Vietnam": "Asia-Pacific", "Indonesia": "Asia-Pacific", "Hong Kong": "Asia-Pacific",
    # Latin America
    "Brazil": "Latin America", "Argentina": "Latin America", "Chile": "Latin America",
    "Colombia": "Latin America", "Peru": "Latin America",
    # Middle East & Africa
    "Israel": "Middle East & Africa", "Turkey": "Middle East & Africa", "Türkiye": "Middle East & Africa",
    "South Africa": "Middle East & Africa", "Egypt": "Middle East & Africa",
    "Saudi Arabia": "Middle East & Africa", "Lebanon": "Middle East & Africa",
    "United Arab Emirates": "Middle East & Africa",
}

PHASE_MAP = {
    "EARLY_PHASE1": "Phase 1", "PHASE1": "Phase 1", "PHASE2": "Phase 2",
    "PHASE3": "Phase 3", "PHASE4": "Phase 4", "NA": "Not Applicable",
}
INDUSTRY_SPONSOR_CLASS = "INDUSTRY"


def validate_industry_flags(values: pd.Series) -> pd.Series:
    """Reject ambiguous external flags rather than treating nonempty strings as true.

    pandas reads complete True/False CSV columns as booleans. Sponsor-class labels
    belong in sponsor_class; numeric and other string encodings are not inferred.
    """
    if not values.map(lambda value: isinstance(value, (bool, np.bool_))).all():
        raise ValueError("is_industry must contain only boolean True/False values; "
                         "use sponsor_class for registry sponsor labels")
    return values.astype(bool)


def is_industry_sponsor(sponsor_class) -> bool:
    """Registry lead-sponsor class INDUSTRY is the single industry definition used everywhere."""
    return sponsor_class == INDUSTRY_SPONSOR_CLASS


def _normalize_sponsor(sponsor_name):
    """Missing (None/NaN/NA) or whitespace-only sponsor cells normalize to "Unknown", never "nan"."""
    if sponsor_name is None or pd.isna(sponsor_name):
        return "Unknown"
    name = str(sponsor_name).strip()
    return name or "Unknown"


def _get_region(country):
    return COUNTRY_TO_REGION.get(country, "Other")


def _normalize_phase(phases_list):
    if not phases_list:
        return "Not Applicable"
    normalized = [PHASE_MAP.get(p, p) for p in phases_list]
    if len(normalized) == 1:
        return normalized[0]
    nums = [m.group(1) for n in normalized if (m := re.search(r"(\d)", n))]
    if len(nums) >= 2:
        return f"Phase {nums[0]}/{nums[-1]}"
    return " / ".join(normalized)


def _classify_study_design(allocation, masking, intervention_model, study_type):
    if study_type == "OBSERVATIONAL":
        return "Observational"
    alloc = str(allocation or "").lower()
    mask = str(masking or "").lower()
    if "randomized" in alloc and "non-randomized" not in alloc:
        if any(k in mask for k in ("double", "triple", "quadruple")):
            return "RCT Double-Blind"
        if "single" in mask:
            return "RCT Single-Blind"
        if "none" in mask or "open" in mask:
            return "RCT Open-Label"
        return "RCT (Other)"
    if "non-randomized" in alloc:
        return "Non-Randomized"
    if "single" in str(intervention_model or "").lower():
        return "Single-Arm"
    return "Other Design"


def _records_to_frame(classified_trials):
    """Build a DataFrame from a list of classified raw API records (live path)."""
    rows = []
    for t in classified_trials:
        conditions = t.get("conditions", [])
        conditions_str = "; ".join(conditions) if isinstance(conditions, list) else str(conditions)
        countries = t.get("countries", []) or []
        regions = sorted({_get_region(c) for c in countries}) if countries else []
        interventions = t.get("interventions", []) or []
        intervention_names = [i.get("name", "") for i in interventions if i.get("name")]
        intervention_descriptions = [i.get("description", "") for i in interventions if i.get("description")]
        start_date = t.get("start_date", "") or ""
        start_year = int(m.group(1)) if (m := re.search(r"(\d{4})", str(start_date))) else None
        phase_normalized = t.get("phase_normalized") or _normalize_phase(t.get("phases", []))
        rows.append({
            "nct_id": t.get("nct_id", ""),
            "brief_title": t.get("brief_title", ""),
            "official_title": t.get("official_title", ""),
            "brief_summary": t.get("brief_summary", ""),
            "detailed_description": t.get("detailed_description", ""),
            "lead_sponsor": t.get("lead_sponsor", ""),
            "sponsor_normalized": _normalize_sponsor(t.get("lead_sponsor", "")),
            "sponsor_class": t.get("sponsor_class", ""),
            "is_industry": is_industry_sponsor(t.get("sponsor_class", "")),
            "study_purpose": t.get("study_purpose", ""),
            "intervention_category": t.get("intervention_category", ""),
            "classification_resolved": t.get("classification_resolved", False),
            "mechanism": t.get("mechanism", t.get("intervention_category", "")),
            "intervention_names_str": " | ".join(intervention_names),
            "intervention_descriptions_str": " | ".join(intervention_descriptions),
            "drug_names_str": "; ".join(intervention_names),
            "phase_normalized": phase_normalized,
            "overall_status": t.get("overall_status", ""),
            "conditions_str": conditions_str,
            "enrollment": t.get("enrollment"),
            "enrollment_type": t.get("enrollment_type", ""),
            "start_date": start_date,
            "start_year": start_year,
            "completion_date": t.get("completion_date", ""),
            "study_type": t.get("study_type", ""),
            "countries_str": "; ".join(countries),
            "n_countries": len(countries),
            "regions_str": "; ".join(regions),
            "study_design_category": _classify_study_design(
                t.get("allocation"), t.get("masking"), t.get("intervention_model"), t.get("study_type")
            ),
            "is_fda_regulated_drug": t.get("is_fda_regulated_drug"),
        })
    return pd.DataFrame(rows, columns=RECORD_COLUMNS)


def _augment(df):
    """Add the consistent derived fields to a compiled DataFrame (idempotent)."""
    # Ensure classification columns exist (offline CSV may predate them).
    if "study_purpose" not in df.columns or "intervention_category" not in df.columns:
        df = classify_all(df)
    # A label-complete offline CSV may predate the documented ``mechanism`` column.
    if "mechanism" not in df.columns:
        df["mechanism"] = df["intervention_category"]

    if "phase_normalized" not in df.columns:
        df["phase_normalized"] = "Not Applicable"
    df["phase_normalized"] = df["phase_normalized"].fillna("Not Applicable")

    # Denominator fix: an explicit, unambiguous phase-applicability flag.
    df["phase_applicable"] = df["phase_normalized"].isin(PHASE_ORDER)
    df["phase_numeric"] = df["phase_normalized"].map(PHASE_NUMERIC).fillna(0)

    # ONE consistent therapeutic definition (no double standard).
    df["is_therapeutic"] = df["study_purpose"].eq("Therapeutic")
    df["is_pharmacological"] = (
        df.get("study_type", pd.Series(index=df.index, dtype=object)).eq("INTERVENTIONAL")
        & df["study_purpose"].isin(["Therapeutic"])
    )

    if "sponsor_normalized" not in df.columns:
        df["sponsor_normalized"] = df.get("lead_sponsor", pd.Series(index=df.index)).map(_normalize_sponsor)
    if "is_industry" not in df.columns:
        if "sponsor_class" not in df.columns:
            # Fail closed: without a lead-sponsor class every record would silently count as
            # academic/other and the report would assert "0 records have an industry lead sponsor".
            # The facts contract (assets/fact_definitions.json) requires integer n_industry/n_academic.
            raise ValueError(
                "input carries neither is_industry nor sponsor_class; the industry/academic split "
                "cannot be derived. Add the registry lead-sponsor class (sponsor_class) or is_industry."
            )
        df["is_industry"] = df["sponsor_class"].eq(INDUSTRY_SPONSOR_CLASS)

    df["is_industry"] = validate_industry_flags(df["is_industry"])

    # Enrollment cleaning: cap registry/mega-study outliers for aggregate views only.
    if "enrollment" in df.columns:
        enrollment = pd.to_numeric(df["enrollment"], errors="coerce")
        outlier = enrollment > 50000
        df["enrollment_clean"] = enrollment.where(~outlier, np.nan)
        df["enrollment_outlier"] = outlier.fillna(False)
    return df


def compile_trials(classified, output_dir=None):
    """Compile classified records/DataFrame into the analysis DataFrame.

    Parameters
    ----------
    classified : list[dict] | pandas.DataFrame
        Classified raw records (from classify_all on API records) or a compiled DataFrame.
    output_dir : str, optional
        Retained for API compatibility; no file is written here.

    Returns
    -------
    pandas.DataFrame
    """
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    print("\n" + "=" * 70)
    print("COMPILING REGISTERED RECORD DATA")
    print("=" * 70)

    if hasattr(classified, "columns"):
        df = classified.copy()
    else:
        df = _records_to_frame(classified)

    before = len(df)
    if "nct_id" in df.columns and df["nct_id"].duplicated().any():
        raise ValueError("duplicate NCT IDs: retrieval must be reconciled before export")
    df = _augment(df).reset_index(drop=True)

    n_phase_applicable = int(df["phase_applicable"].sum())
    print(f"   Retrieved records: {before} (deduplicated: {len(df)})")
    print(f"   Phase-applicable: {n_phase_applicable}; Not Applicable: {len(df) - n_phase_applicable}")
    print(f"   Unique sponsors: {df['sponsor_normalized'].nunique()}")
    print("\u2713 Registered-record data compiled successfully!")
    print("=" * 70)
    return df
