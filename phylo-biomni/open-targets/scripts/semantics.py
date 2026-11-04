#!/usr/bin/env python3
"""Discriminated scientific-semantic record schema for Open Targets outputs.

Each exported record carries a ``record_type`` discriminator, a metric name,
value, scale/range, ``datasource_id`` ONLY when the record is a real evidence
datasource record, and an interpretation boundary string.

Association, evidence, L2G, and colocalisation records are NEVER labeled as raw
effect estimates, replication, causal proof, or clinical validation (OT-07).

L2G and colocalisation rows leave ``datasource_id`` null with an explicit
``datasource_not_applicable_reason`` — the candidate-era invented strings
``ot_genetics_l2g`` and ``ot_genetics_colocalisation`` must never appear in a
datasource field (OT-08).
"""

from __future__ import annotations

from typing import Any

# The set of record_type discriminators.
RECORD_TYPES = {
    "association",
    "datatype_score",
    "evidence",
    "l2g_prediction",
    "colocalisation",
    "credible_set",
    "study",
    "search_hit",
    "target_annotation",
    "disease_annotation",
    "drug_candidate",
    "drug_profile",
    "variant_annotation",
    "release_metadata",
}

# Datasource IDs that are valid ONLY for evidence records (sourced from the API
# ``evidences.rows[].datasourceId`` field).  L2G and colocalisation are NOT in
# this set — they are nested fields on CredibleSet, not evidence datasources.
FORBIDDEN_DATASOURCE_IDS = {"ot_genetics_l2g", "ot_genetics_colocalisation"}

# Interpretation boundaries per record type.  These constrain the strongest
# sentence the report may write about each record type (OT-07).
INTERPRETATION_BOUNDARIES = {
    "association": (
        "Overall association score (0–1) integrating multiple evidence datasources; "
        "a prioritization metric, NOT an effect estimate, replication, causal proof, "
        "or clinical validation."
    ),
    "datatype_score": (
        "Per-datatype contribution to the overall association score (0–1); "
        "a prioritization metric, NOT an effect estimate or causal proof."
    ),
    "evidence": (
        "Individual evidence record with a datasource-specific score; "
        "NOT an effect estimate, causal proof, or clinical validation."
    ),
    "l2g_prediction": (
        "Locus-to-Gene prediction score (0–1) prioritising the causal gene at a locus; "
        "NOT an effect estimate, replication, causal proof, or clinical validation."
    ),
    "colocalisation": (
        "Colocalisation posterior probability (H4 / clpp) that two signals share a causal variant; "
        "supportive evidence, NOT causal proof or clinical validation."
    ),
    "credible_set": (
        "Fine-mapped credible set with p-value and purity; "
        "a genetic-locus annotation, NOT an effect estimate or causal proof."
    ),
    "study": (
        "GWAS study metadata; descriptive, NOT an effect estimate or causal proof."
    ),
}

# Scale/range for each metric type.
METRIC_SCALES = {
    "association_score": "0–1 (higher = stronger integrated association)",
    "datatype_score": "0–1 (higher = stronger datatype contribution)",
    "evidence_score": "datasource-specific (0–1 for most; see datasource docs)",
    "l2g_score": "0–1 (higher = more likely causal gene)",
    "colocalisation_h4": "0–1 posterior probability (higher = more likely shared causal variant)",
    "colocalisation_clpp": "0–1 (higher = stronger colocalisation)",
    "pvalue": "scientific notation (mantissa × 10^exponent)",
}


def _validate_record_type(rt: str) -> str:
    if rt not in RECORD_TYPES:
        raise ValueError(f"unknown record_type {rt!r}; valid: {sorted(RECORD_TYPES)}")
    return rt


def _check_datasource(record_type: str, datasource_id: str | None) -> tuple[str | None, str | None]:
    """Return (datasource_id, not_applicable_reason).

    Evidence records keep their real datasource_id.  L2G and colocalisation
    records get null + an explicit reason.  Any forbidden invented ID raises.
    """
    if datasource_id is not None and datasource_id in FORBIDDEN_DATASOURCE_IDS:
        raise ValueError(
            f"datasource_id {datasource_id!r} is an invented label for a non-evidence "
            f"record type; L2G and colocalisation are not evidence datasources (OT-08)"
        )
    if record_type in ("l2g_prediction", "colocalisation", "credible_set"):
        return None, "L2G/colocalisation/credible-set records are nested fields on CredibleSet, not evidence datasource records"
    if record_type in ("association", "datatype_score"):
        return None, "association and datatype scores are integrated metrics, not single-datasource evidence records"
    return datasource_id, None


def serialize_association(rank: int, target_id: str, symbol: str, name: str,
                          biotype: str, overall_score: float,
                          datatype_scores: list[dict] | None = None) -> dict:
    """Serialize one disease–target association row."""
    _validate_record_type("association")
    ds_id, ds_reason = _check_datasource("association", None)
    rec = {
        "record_type": "association",
        "metric_name": "association_score",
        "metric_value": overall_score,
        "scale": METRIC_SCALES["association_score"],
        "datasource_id": ds_id,
        "datasource_not_applicable_reason": ds_reason,
        "interpretation_boundary": INTERPRETATION_BOUNDARIES["association"],
        "rank": rank,
        "target_id": target_id,
        "approved_symbol": symbol,
        "approved_name": name,
        "biotype": biotype,
        "overall_association_score": overall_score,
    }
    if datatype_scores:
        rec["datatype_scores"] = [
            serialize_datatype_score(d["id"], d["score"]) for d in datatype_scores
        ]
    return rec


def serialize_datatype_score(dt_id: str, score: float) -> dict:
    _validate_record_type("datatype_score")
    ds_id, ds_reason = _check_datasource("datatype_score", None)
    return {
        "record_type": "datatype_score",
        "metric_name": "datatype_score",
        "metric_value": score,
        "scale": METRIC_SCALES["datatype_score"],
        "datasource_id": ds_id,
        "datasource_not_applicable_reason": ds_reason,
        "interpretation_boundary": INTERPRETATION_BOUNDARIES["datatype_score"],
        "datatype_id": dt_id,
        "score": score,
    }


def serialize_evidence(datasource_id: str, datatype_id: str, score: float,
                       literature: list[str] | None = None,
                       urls: list[dict] | None = None,
                       study_overview: str | None = None,
                       author: str | None = None, year: int | None = None) -> dict:
    _validate_record_type("evidence")
    ds_id, ds_reason = _check_datasource("evidence", datasource_id)
    return {
        "record_type": "evidence",
        "metric_name": "evidence_score",
        "metric_value": score,
        "scale": METRIC_SCALES["evidence_score"],
        "datasource_id": ds_id,
        "datasource_not_applicable_reason": ds_reason,
        "interpretation_boundary": INTERPRETATION_BOUNDARIES["evidence"],
        "datatype_id": datatype_id,
        "score": score,
        "literature": literature or [],
        "urls": urls or [],
        "study_overview": study_overview,
        "publication_first_author": author,
        "publication_year": year,
    }


def serialize_l2g(study_locus_id: str, target_id: str, symbol: str, score: float) -> dict:
    _validate_record_type("l2g_prediction")
    ds_id, ds_reason = _check_datasource("l2g_prediction", None)
    return {
        "record_type": "l2g_prediction",
        "metric_name": "l2g_score",
        "metric_value": score,
        "scale": METRIC_SCALES["l2g_score"],
        "datasource_id": ds_id,
        "datasource_not_applicable_reason": ds_reason,
        "interpretation_boundary": INTERPRETATION_BOUNDARIES["l2g_prediction"],
        "study_locus_id": study_locus_id,
        "target_id": target_id,
        "approved_symbol": symbol,
        "score": score,
    }


def serialize_colocalisation(study_locus_id: str, other_study_id: str | None,
                             h4: float | None, clpp: float | None) -> dict:
    _validate_record_type("colocalisation")
    ds_id, ds_reason = _check_datasource("colocalisation", None)
    return {
        "record_type": "colocalisation",
        "metric_name": "colocalisation_h4" if h4 is not None else "colocalisation_clpp",
        "metric_value": h4 if h4 is not None else clpp,
        "scale": METRIC_SCALES["colocalisation_h4"] if h4 is not None else METRIC_SCALES["colocalisation_clpp"],
        "datasource_id": ds_id,
        "datasource_not_applicable_reason": ds_reason,
        "interpretation_boundary": INTERPRETATION_BOUNDARIES["colocalisation"],
        "study_locus_id": study_locus_id,
        "other_study_id": other_study_id,
        "h4": h4,
        "clpp": clpp,
    }


def serialize_credible_set(study_locus_id: str, region: str,
                           pval_mantissa: float, pval_exponent: int,
                           variant_id: str | None = None, rs_ids: list[str] | None = None) -> dict:
    _validate_record_type("credible_set")
    ds_id, ds_reason = _check_datasource("credible_set", None)
    return {
        "record_type": "credible_set",
        "metric_name": "pvalue",
        "metric_value": f"{pval_mantissa}e{pval_exponent}",
        "scale": METRIC_SCALES["pvalue"],
        "datasource_id": ds_id,
        "datasource_not_applicable_reason": ds_reason,
        "interpretation_boundary": INTERPRETATION_BOUNDARIES["credible_set"],
        "study_locus_id": study_locus_id,
        "region": region,
        "pvalue_mantissa": pval_mantissa,
        "pvalue_exponent": pval_exponent,
        "variant_id": variant_id,
        "rs_ids": rs_ids or [],
    }


def serialize_study(study_id: str, study_type: str | None, trait: str,
                    pmid: str | None, author: str | None,
                    n_samples: int | None, n_cases: int | None, n_controls: int | None,
                    diseases: list[dict] | None = None) -> dict:
    _validate_record_type("study")
    ds_id, ds_reason = _check_datasource("study", None)
    return {
        "record_type": "study",
        "metric_name": None,
        "metric_value": None,
        "scale": None,
        "datasource_id": ds_id,
        "datasource_not_applicable_reason": ds_reason,
        "interpretation_boundary": INTERPRETATION_BOUNDARIES["study"],
        "study_id": study_id,
        "study_type": study_type,
        "trait_from_source": trait,
        "pubmed_id": pmid,
        "publication_first_author": author,
        "n_samples": n_samples,
        "n_cases": n_cases,
        "n_controls": n_controls,
        "diseases": diseases or [],
    }


def validate_record(rec: dict) -> list[str]:
    """Validate one serialized record.  Returns a list of error strings (empty = valid)."""
    errors = []
    rt = rec.get("record_type")
    if rt not in RECORD_TYPES:
        errors.append(f"record_type {rt!r} is not a known discriminator")
    ds = rec.get("datasource_id")
    if ds is not None and ds in FORBIDDEN_DATASOURCE_IDS:
        errors.append(f"datasource_id {ds!r} is forbidden (invented non-evidence label)")
    if rt in ("l2g_prediction", "colocalisation", "credible_set", "association", "datatype_score"):
        if ds is not None:
            errors.append(f"record_type {rt!r} must have null datasource_id, got {ds!r}")
        if not rec.get("datasource_not_applicable_reason"):
            errors.append(f"record_type {rt!r} must carry a datasource_not_applicable_reason")
    if rt == "evidence" and ds is None:
        errors.append("evidence records must carry a real datasource_id")
    return errors


def validate_records(records: list[dict]) -> list[str]:
    all_errors = []
    for i, rec in enumerate(records):
        errs = validate_record(rec)
        for e in errs:
            all_errors.append(f"record[{i}]: {e}")
    return all_errors
