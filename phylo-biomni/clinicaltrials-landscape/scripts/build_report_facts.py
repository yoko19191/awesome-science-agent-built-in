"""
Build the runtime facts payload for the bounded ClinicalTrials.gov landscape report.

Every number and every pre-formatted sentence is derived from artifacts on disk (trials_all.csv,
coverage_scope.json, trials_match_evidence.csv) - never from conversation memory. The payload is later
promoted to report_facts.json by landscape_evidence.write_report_facts, which attaches the checked
figure manifest and enforces the domain accounting in assets/fact_definitions.json.

Two guards run here, before the payload is written, so a run that would overclaim never produces
quotable numbers:
  * assert_report_claims_bounded              - boundary + limitation statements present; no completeness overclaim
  * assert_report_free_of_record_asset_inference - no conversion of record activity into an asset pipeline
  * assert_report_free_of_strategic_inference - no investment/commercial/whitespace/first-mover/etc.
"""

from __future__ import annotations

import json
import os

import pandas as pd

try:
    from compile_trials import validate_industry_flags
    from export_all import build_sponsor_summary, extract_positive_query_terms
except ImportError:  # pragma: no cover
    from scripts.compile_trials import validate_industry_flags
    from scripts.export_all import build_sponsor_summary, extract_positive_query_terms

try:
    from coverage_scope import (
        BOUNDARY_STATEMENT,
        LIMITATION_STATEMENT,
        RECORD_ASSET_BOUNDARY_STATEMENT,
        assert_report_claims_bounded,
        assert_report_free_of_record_asset_inference,
        assert_report_free_of_strategic_inference,
    )
except ImportError:  # pragma: no cover
    from scripts.coverage_scope import (
        BOUNDARY_STATEMENT,
        LIMITATION_STATEMENT,
        RECORD_ASSET_BOUNDARY_STATEMENT,
        assert_report_claims_bounded,
        assert_report_free_of_record_asset_inference,
        assert_report_free_of_strategic_inference,
    )

_CATEGORY_FIELD = {
    "Diagnostic radiotracer / imaging agent": "n_cat_diagnostic_radiotracer",
    "Radioligand therapy (radiopharmaceutical)": "n_cat_radioligand_therapy",
    "Small molecule": "n_cat_small_molecule",
    "Biologic (antibody/protein)": "n_cat_biologic",
    "Cell or gene therapy": "n_cat_cell_gene_therapy",
    "Radiation therapy (external/brachytherapy)": "n_cat_radiation_therapy",
    "Device / Procedure": "n_cat_device_procedure",
    "Behavioral / Supportive / Other": "n_cat_behavioral_supportive_other",
    "Unclassified (insufficient description)": "n_cat_unclassified",
}
_PURPOSE_FIELD = {
    "Observational": "n_purpose_observational",
    "Diagnostic/Imaging": "n_purpose_diagnostic_imaging",
    "Therapeutic": "n_purpose_therapeutic",
    "Other/Supportive": "n_purpose_other_supportive",
    "Unresolved": "n_purpose_unresolved",
}


def build_facts_payload(results_dir="."):
    df = pd.read_csv(os.path.join(results_dir, "trials_all.csv"))
    scope = json.loads(open(os.path.join(results_dir, "coverage_scope.json")).read())
    match_path = os.path.join(results_dir, "trials_match_evidence.csv")
    match = pd.read_csv(match_path) if os.path.exists(match_path) else pd.DataFrame()

    n = len(df)
    n_int = int((df["study_type"] == "INTERVENTIONAL").sum())
    n_obs = int((df["study_type"] == "OBSERVATIONAL").sum())
    n_expanded = int((df["study_type"] == "EXPANDED_ACCESS").sum())
    n_pa = int(df["phase_applicable"].sum())

    payload = {
        "schema_note": "runtime facts payload; promoted to report_facts.json with the figure manifest",
        "tier": "complete_for_declared_query",
        "source": scope.get("source"),
        "query_date": scope.get("query_date"),
        "boundary_statement": BOUNDARY_STATEMENT,
        "limitation_statement": LIMITATION_STATEMENT,
        "record_asset_boundary_statement": RECORD_ASSET_BOUNDARY_STATEMENT,
        "query": {
            "conditions": scope.get("conditions"),
            "statuses": scope.get("statuses"),
            "intervention_filter": scope.get("intervention_filter"),
            "phases": scope.get("phases"),
            "sponsor_filter": scope.get("sponsor_filter"),
            "input_resolution": scope.get("input_resolution"),
            "query_profile": scope.get("query_profile"),
        },
        "dataset_sha256": scope.get("dataset_sha256"),
        "api_total_count": scope.get("api_total_count"),
        "pages_retrieved": scope.get("pages_retrieved"),
        "pagination_exhausted": scope.get("pagination_exhausted"),
        # headline counts
        "records_retrieved": n,
        "n_interventional": n_int,
        "n_observational": n_obs,
        "n_expanded_access": n_expanded,
        "n_phase_applicable": n_pa,
        "n_phase_not_applicable": n - n_pa,
    }

    # study_purpose partition
    purpose_counts = df["study_purpose"].value_counts().to_dict()
    for label, field in _PURPOSE_FIELD.items():
        payload[field] = int(purpose_counts.get(label, 0))

    # intervention_category partition
    cat_counts = df["intervention_category"].value_counts().to_dict()
    for label, field in _CATEGORY_FIELD.items():
        payload[field] = int(cat_counts.get(label, 0))

    payload["n_purpose_unresolved"] = int(purpose_counts.get("Unresolved", 0))
    payload["n_resolved"] = int(df["classification_resolved"].sum())
    payload["n_unresolved_modality"] = payload["n_cat_unclassified"]

    # sponsor / geography descriptors
    payload["n_sponsors"] = int(df["sponsor_normalized"].nunique())
    # Fail closed on a trials_all.csv without a complete lead-sponsor class: the facts contract has no
    # null for n_industry/n_academic, and a 0/N sentence would assert an unknown split as academic/other.
    if "is_industry" not in df.columns or df["is_industry"].isna().any():
        raise ValueError(
            "trials_all.csv has no complete is_industry column; refusing to report an industry/academic "
            "record split (sponsor class not in input)"
        )
    payload["n_industry"] = int(validate_industry_flags(df["is_industry"]).sum())
    payload["n_academic"] = n - payload["n_industry"]
    top_sponsor = build_sponsor_summary(df)["trials"]
    payload["top_sponsor"] = str(top_sponsor.index[0]) if len(top_sponsor) else ""
    payload["top_sponsor_trials"] = int(top_sponsor.iloc[0]) if len(top_sponsor) else 0
    payload["sponsor_composition_sentence"] = (
        f"Across {n} retrieved records, {payload['n_sponsors']} unique lead sponsors are represented; "
        f"{payload['n_industry']} records have an industry lead sponsor and {payload['n_academic']} "
        f"have an academic/other lead sponsor."
    )

    # match-evidence availability summary (never imply absent fields were present)
    if len(match):
        payload["match_evidence"] = {
            "n_records": int(len(match)),
            "n_intervention_term_matched": int((match["match_basis"] == "intervention_term_matched_in_exported_fields").sum()),
            "n_condition_matched_only": int((match["match_basis"] == "condition_matched_only").sum()),
            "n_registry_index_only": int((match["match_basis"] == "registry_index_only").sum()),
            "descriptions_available_count": int(match["intervention_descriptions_available"].sum()),
            "brief_summary_available_count": int(match["brief_summary_available"].sum()),
            "detailed_description_available_count": int(match["detailed_description_available"].sum()),
            "sources": sorted(match["match_evidence_source"].unique().tolist()),
        }
    else:
        payload["match_evidence"] = {}

    # Keep presentation-critical availability counts at the top level as well as in the structured
    # match_evidence object. Report builders commonly use a flat fact accessor; forcing them to
    # traverse a nested object led to a rendered "None of N" claim even though the nested evidence
    # was correct. These are aliases of the measured values, not independently computed facts.
    for field in (
        "n_records",
        "n_intervention_term_matched",
        "n_condition_matched_only",
        "n_registry_index_only",
        "descriptions_available_count",
        "brief_summary_available_count",
        "detailed_description_available_count",
    ):
        payload[field] = int(payload["match_evidence"].get(field, 0))

    # caveats, bound to concrete numbers
    mixed = payload["n_purpose_diagnostic_imaging"] > 0 and payload["n_purpose_therapeutic"] > 0
    payload["caveats_fired"] = {
        "generic_classification": True,
        "mixed_diagnostic_therapeutic": bool(mixed),
        "unresolved_records_present": bool(payload["n_unresolved_modality"] > 0 or payload["n_purpose_unresolved"] > 0),
        "single_term_intervention_filter": len(extract_positive_query_terms(scope.get("intervention_filter"))) == 1,
        "phase_not_applicable_present": bool(payload["n_phase_not_applicable"] > 0),
        "match_evidence_field_availability": True,
        "record_counts_not_unique_assets": True,
    }

    # pre-formatted sentences the report quotes verbatim
    term = scope.get("intervention_filter")
    term_txt = f" and intervention expression '{term}'" if term else ""
    type_parts = [f"{n_int} interventional", f"{n_obs} observational"]
    if n_expanded:
        type_parts.append(f"{n_expanded} expanded-access")
    me = payload["match_evidence"]
    descr_txt = (
        f"intervention descriptions were available for {payload['descriptions_available_count']} of "
        f"{n} records, brief summaries for {payload['brief_summary_available_count']}, and detailed "
        f"descriptions for {payload['detailed_description_available_count']}"
        if me else "match-evidence availability was not computed"
    )
    payload["match_evidence_availability_sentence"] = descr_txt
    payload["headline_sentences"] = [
        f"This bounded ClinicalTrials.gov query returned {n} registered records "
        f"({', '.join(type_parts)}) for the declared conditions{term_txt} "
        f"as of {scope.get('query_date')}.",
        f"By study purpose: {payload['n_purpose_therapeutic']} therapeutic, "
        f"{payload['n_purpose_diagnostic_imaging']} diagnostic/imaging, "
        f"{payload['n_purpose_observational']} observational, "
        f"{payload['n_purpose_other_supportive']} other/supportive, "
        f"{payload['n_purpose_unresolved']} unresolved.",
        f"Of {n} retrieved records, {n_pa} are phase-applicable and {n - n_pa} are Not Applicable; "
        f"phase-only subtotals use the phase-applicable denominator, not the all-retrieved total.",
        f"Classification is generic and record-grounded: {payload['n_resolved']} of {n} records "
        f"resolved to a modality and {payload['n_unresolved_modality']} are labeled "
        f"'Unclassified (insufficient description)' rather than assigned an invented modality.",
        f"Query-match evidence is exported per record and per declared positive alias with explicit "
        f"field availability ({descr_txt}); registry-index matches remain labeled when no declared "
        f"alias is visible in the exported fields.",
        BOUNDARY_STATEMENT,
        LIMITATION_STATEMENT,
        RECORD_ASSET_BOUNDARY_STATEMENT,
    ]
    payload["narrative"] = " ".join(payload["headline_sentences"])

    # GUARDS (run before writing): boundary/limitation present, no completeness or strategic overclaim
    assert_report_claims_bounded(payload["narrative"])
    assert_report_free_of_record_asset_inference(payload["narrative"])
    assert_report_free_of_strategic_inference(payload["narrative"])

    return payload


def write_facts_payload(results_dir=".", filename="facts_payload.json"):
    payload = build_facts_payload(results_dir)
    out = os.path.join(results_dir, filename)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\u2713 Wrote facts payload: {out}")
    return payload


if __name__ == "__main__":  # pragma: no cover
    import sys
    write_facts_payload(sys.argv[1] if len(sys.argv) > 1 else ".")
