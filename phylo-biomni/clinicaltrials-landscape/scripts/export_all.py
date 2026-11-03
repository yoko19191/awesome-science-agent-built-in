"""
Export bounded ClinicalTrials.gov landscape results (Step 4).

Writes, to the results directory:
  1. trials_all.csv            - all retrieved records + study_purpose / intervention_category
  2. coverage_scope.json       - bounded query + exact-bytes dataset fingerprint (preserved gate)
  3. trials_by_category.csv    - intervention_category x phase with EXPLICIT denominators (no bare "Total")
  4. trials_by_sponsor.csv     - sponsor summary, full (non-truncated) names
  5. trials_match_evidence.csv - per-record query-match evidence WITH field-availability flags

The coverage-scope gate is preserved exactly: export fails before writing anything if no valid
coverage_scope is supplied. The legacy hand-styled ReportLab report is removed; the PDF is produced
by the mandatory terminal report step via pdf-report-generation.
"""

import os
import re

import pandas as pd

try:
    from alias_match import alias_matches
    from coverage_scope import bind_exported_dataset, validate_coverage_scope, write_coverage_scope
except ImportError:  # pragma: no cover
    from scripts.alias_match import alias_matches
    from scripts.coverage_scope import bind_exported_dataset, validate_coverage_scope, write_coverage_scope


PHASE_ORDER = ["Phase 1", "Phase 1/2", "Phase 2", "Phase 2/3", "Phase 3", "Phase 3/4", "Phase 4"]


def extract_positive_query_terms(expression):
    """Return the positive leaf terms from a simple ClinicalTrials.gov Boolean expression.

    Match evidence must be evaluated against each declared alias, not against the entire literal
    expression (for example ``PSMA OR PSMA-617``). Quoted phrases are kept intact, Boolean operators
    and parentheses are discarded, and a directly negated leaf is excluded. The ClinicalTrials.gov
    API remains authoritative for query evaluation; this helper only makes the exported field-level
    audit intelligible and does not attempt to reimplement registry search semantics.
    """
    if not expression or not str(expression).strip():
        return []

    tokens = re.findall(r'"(?:[^"\\]|\\.)*"|\(|\)|\b(?:AND|OR|NOT)\b|[^\s()]+', str(expression), re.I)
    terms = []
    negate_next = False
    for token in tokens:
        upper = token.upper()
        if upper in {"AND", "OR", "(", ")"}:
            continue
        if upper == "NOT":
            negate_next = True
            continue
        value = token.strip().strip('"').strip()
        if negate_next:
            negate_next = False
            continue
        if value and value.lower() not in {term.lower() for term in terms}:
            terms.append(value)
    return terms


def build_category_by_phase(trials_df):
    """intervention_category x phase crosstab with explicit, unambiguous denominators.

    Columns: the phase columns present, then 'Not Applicable', then 'All retrieved' (the true grand
    total per category). There is deliberately no bare 'Total' column: a phase-only subtotal must not
    be presented as the all-records total.
    """
    ct = pd.crosstab(trials_df["intervention_category"], trials_df["phase_normalized"])
    phase_cols = [p for p in PHASE_ORDER if p in ct.columns]
    na_cols = [c for c in ct.columns if c not in PHASE_ORDER]
    ordered = phase_cols + na_cols
    ct = ct.reindex(columns=ordered, fill_value=0)
    if na_cols:
        ct["Not Applicable"] = ct[na_cols].sum(axis=1)
        ct = ct.drop(columns=[c for c in na_cols if c != "Not Applicable"], errors="ignore")
    else:
        ct["Not Applicable"] = 0
    ct["Phase-applicable subtotal"] = ct[phase_cols].sum(axis=1) if phase_cols else 0
    ct["All retrieved"] = ct[phase_cols + ["Not Applicable"]].sum(axis=1) if phase_cols else ct["Not Applicable"]
    ct = ct.sort_values("All retrieved", ascending=False)
    return ct


def build_sponsor_summary(trials_df):
    agg = {"trials": ("nct_id", "count"), "intervention_categories": ("intervention_category", "nunique")}
    if "is_industry" in trials_df.columns:
        agg["industry"] = ("is_industry", "first")
    summary = trials_df.groupby("sponsor_normalized").agg(**agg)
    # One deterministic ranking feeds both exported tables and the chart cutoff.
    summary = summary.sort_values(
        ["trials", "sponsor_normalized"], ascending=[False, True], kind="stable"
    )
    return summary


MATCH_EVIDENCE_COLUMNS = [
    'nct_id',
    'brief_title',
    'official_title',
    'conditions_str',
    'intervention_names',
    'intervention_descriptions',
    'brief_summary',
    'detailed_description',
    'study_purpose',
    'intervention_category',
    'intervention_term',
    'matched_intervention_terms',
    'matched_query_fields',
    'matched_condition_terms',
    'match_basis',
    'intervention_descriptions_available',
    'brief_summary_available',
    'detailed_description_available',
    'match_evidence_source',
]

def _field_text(value):
    """Represent absent CSV scalar fields as absent source evidence."""
    return "" if pd.isna(value) else str(value)


def build_match_evidence(trials_df, coverage_scope):
    """Per-record query-match evidence with explicit field availability.

    Records where the declared intervention aliases and declared condition terms appear among the
    exported fields (alias-aware: see ``alias_match.alias_matches``), and which source fields were
    actually available. Absent source fields (e.g.,
    intervention descriptions or brief summary in an offline compiled CSV) are left blank AND flagged
    not-available; they are never implied present. Field availability and the recall limitation are
    documented in references/output-schema.md, the report, and the facts caveats.
    """
    term = (coverage_scope.get("intervention_filter") or "").strip()
    query_terms = extract_positive_query_terms(term)
    conditions = [str(c).strip() for c in coverage_scope.get("conditions", []) if str(c).strip()]

    text_cols = [
        ("intervention_names", "intervention_names_str"),
        ("intervention_names", "drug_names_str"),
        ("intervention_descriptions", "intervention_descriptions_str"),
        ("brief_title", "brief_title"),
        ("official_title", "official_title"),
        ("brief_summary", "brief_summary"),
        ("detailed_description", "detailed_description"),
        ("conditions", "conditions_str"),
    ]

    rows = []
    for _, r in trials_df.iterrows():
        descr = _field_text(r.get("intervention_descriptions_str", ""))
        summary = _field_text(r.get("brief_summary", ""))
        detailed = _field_text(r.get("detailed_description", ""))
        descr_avail = bool(descr.strip())
        summary_avail = bool(summary.strip())
        detailed_avail = bool(detailed.strip())

        matched_fields = []
        matched_terms = []
        if query_terms:
            for logical, col in text_cols:
                val = _field_text(r.get(col, ""))
                # Alias-aware: short acronyms need boundaries (CAR is not evidence inside Carcinoma).
                field_terms = [alias for alias in query_terms if alias_matches(alias, val)]
                if field_terms and logical not in matched_fields:
                    matched_fields.append(logical)
                for alias in field_terms:
                    if alias.lower() not in {matched.lower() for matched in matched_terms}:
                        matched_terms.append(alias)
        conditions_text = _field_text(r.get("conditions_str", ""))
        matched_conditions = [c for c in conditions if alias_matches(c, conditions_text)]

        if matched_fields:
            basis = "intervention_term_matched_in_exported_fields"
        elif matched_conditions:
            basis = "condition_matched_only"
        else:
            basis = "registry_index_only"  # matched via the registry's own query indexing

        rows.append({
            "nct_id": r.get("nct_id", ""),
            "brief_title": r.get("brief_title", ""),
            "official_title": r.get("official_title", ""),
            "conditions_str": r.get("conditions_str", ""),
            "intervention_names": _field_text(r.get("intervention_names_str", "")) or _field_text(r.get("drug_names_str", "")),
            "intervention_descriptions": descr,
            "brief_summary": summary,
            "detailed_description": detailed,
            "study_purpose": r.get("study_purpose", ""),
            "intervention_category": r.get("intervention_category", ""),
            "intervention_term": term,
            "matched_intervention_terms": ";".join(matched_terms),
            "matched_query_fields": ";".join(matched_fields),
            "matched_condition_terms": ";".join(matched_conditions),
            "match_basis": basis,
            "intervention_descriptions_available": descr_avail,
            "brief_summary_available": summary_avail,
            "detailed_description_available": detailed_avail,
            "match_evidence_source": "raw_api" if (descr_avail or summary_avail or detailed_avail) else "compiled_csv",
        })
    return pd.DataFrame(rows, columns=MATCH_EVIDENCE_COLUMNS)


def export_all(trials_df, parameters=None, output_dir="landscape_results", config=None):
    """Export the bounded landscape result set. Fails before writing if coverage_scope is missing."""
    if parameters is None:
        parameters = {}
    parameters = dict(parameters)

    # PRESERVED GATE: validate scope BEFORE creating the output dir or writing anything.
    query_scope = validate_coverage_scope(parameters.get("coverage_scope"))

    os.makedirs(output_dir, exist_ok=True)
    print("\n" + "=" * 70)
    print("EXPORTING RESULTS")
    print("=" * 70)

    # 1. Single source dataset + exact-bytes provenance.
    all_path = os.path.join(output_dir, "trials_all.csv")
    trials_df.to_csv(all_path, index=False)
    coverage_scope = bind_exported_dataset(query_scope, all_path)
    write_coverage_scope(coverage_scope, os.path.join(output_dir, "coverage_scope.json"))
    print(f"   Saved: trials_all.csv ({len(trials_df)} retrieved records)")
    print("   Saved: coverage_scope.json")

    # 2. intervention_category x phase with explicit denominators.
    build_category_by_phase(trials_df).to_csv(os.path.join(output_dir, "trials_by_category.csv"))
    print("   Saved: trials_by_category.csv")

    # 3. Sponsor summary (full names).
    build_sponsor_summary(trials_df).to_csv(os.path.join(output_dir, "trials_by_sponsor.csv"))
    print("   Saved: trials_by_sponsor.csv")

    # 4. Auditable per-record query-match evidence with field availability.
    match_df = build_match_evidence(trials_df, coverage_scope)
    match_df.to_csv(os.path.join(output_dir, "trials_match_evidence.csv"), index=False)
    print(f"   Saved: trials_match_evidence.csv ({len(match_df)} records)")

    print("=== Export Complete ===")
    return coverage_scope
