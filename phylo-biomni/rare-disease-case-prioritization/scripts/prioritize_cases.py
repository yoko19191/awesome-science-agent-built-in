#!/usr/bin/env python3
"""Deterministic evidence-gating engine for established rare-disease disease-gene hypotheses.

Research decision support only. This entrypoint never emits a diagnosis, a diagnostic
probability, an ACMG/AMP classification, or a patient-facing result. It normalizes one
structured case into a single contract, runs one deterministic evidence-gating cascade,
and writes ranked hypotheses with an explicit evidence state plus contradictions,
missing-evidence codes, abstentions, provenance, an optional reanalysis diff, and one
data figure. It is invoked only through the canonical runner.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import pathlib
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

SCHEMA_DOSSIER = "rare-disease-evidence-dossier/1"
COMPONENT_ID = "rare-disease-case-prioritization/entrypoint/1"

ENTRY_MODES = ("phenotype_first", "genotype_first", "incomplete_evidence")

# ---- controlled vocabularies (input-contract.md) -------------------------------------
ASSEMBLIES = {"GRCh37", "GRCh38"}
INHERITANCE = {"AD", "AR", "X_LINKED", "UNKNOWN"}
ZYGOSITY = {"heterozygous", "homozygous", "hemizygous", "unknown"}
PHASE = {"trans", "cis", "unknown", "not_applicable"}
PARENTAL = {"maternal", "paternal", "de_novo", "unknown"}
CALL_QUALITY = {"pass", "fail", "unknown"}
CLASSIFICATION = {"P", "LP", "VUS", "LB", "B", "UNCLASSIFIED"}
VALIDITY = {"definitive", "strong", "moderate", "limited", "disputed", "refuted", "unknown"}
PHENOTYPE_MATCH = {"specific", "supportive", "nonspecific", "contradictory", "unknown"}
POPULATION = {"compatible", "too_common", "insufficient_context"}
VERIFICATION = {"verified", "unverified", "unresolvable"}
RELATEDNESS = {"consistent", "inconsistent", "not_tested"}

SUPPORTED_VARIANT_CLASSES = {"snv", "indel"}
STRONG_VALIDITY = {"definitive", "strong", "moderate"}
PREDICTOR_SOURCE_TYPES = {
    "in_silico", "in_silico_predictor", "computational_predictor", "predictor",
}
# Curated gene-disease assertion/curation source types. A strong-tier gene_disease_validity
# may support concordance only when its resolved, verified gene_disease_source is one of
# these curated types. A lone source whose type is merely a publication does NOT establish a
# disease-gene association; publication quality is never
# inferred. Accepted semantics are documented in references/evidence-gating-model.md and
# DATA_SOURCES.md. Extend this allowlist only with genuinely curated assertion sources.
CURATED_GENE_DISEASE_SOURCE_TYPES = {
    "gene_disease_validity",
    "gene_disease_curation",
    "gene_disease_assertion",
    "curated_gene_disease_assertion",
    "clingen_gene_validity",
    "expert_panel_curation",
}

CANDIDATE_COLUMNS = [
    "case_id", "hypothesis_id", "variant_id", "gene_id", "gene_symbol", "disease_id",
    "genome_assembly", "transcript_id", "variant_class", "inheritance_model", "zygosity",
    "phase", "parental_origin", "call_quality_status", "classification",
    "classification_source", "gene_disease_validity", "gene_disease_source",
    "phenotype_match", "population_frequency_status", "predictor_group",
    "supporting_evidence_refs", "contradicting_evidence_refs",
]
MANIFEST_COLUMNS = [
    "evidence_id", "source_type", "identifier", "version_or_date", "locator",
    "verification_status", "checksum_if_local",
]
FAMILY_COLUMNS = [
    "case_id", "sample_id", "role", "biological_parent_ids", "reported_sex",
    "genotype_available", "relatedness_status",
]

HYPOTHESES_TSV_COLUMNS = [
    "case_id", "rank", "entry_mode", "hypothesis_id", "disease_id", "gene_id",
    "variant_ids", "evidence_state", "inheritance_status", "phenotype_status",
    "contradiction_codes", "missing_evidence_codes", "abstention_code",
    "source_version_set_id", "reanalysis_status",
]

STATE_ORDER = {
    "concordant_hypothesis": 0,
    "incomplete_hypothesis": 1,
    "conflicting_hypothesis": 2,
    "abstained": 3,
}


class InputRejected(Exception):
    """Fail-closed input rejection; the offending field/file is named in the message."""


# ---- small IO helpers ----------------------------------------------------------------
def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_tsv(path: pathlib.Path, required_columns: list[str], label: str) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputRejected(f"input rejected: {label} is not valid UTF-8") from exc
    reader = csv.reader(text.splitlines(), delimiter="\t")
    rows = list(reader)
    if not rows:
        raise InputRejected(f"input rejected: {label} is empty")
    header = [h.strip() for h in rows[0]]
    missing = [c for c in required_columns if c not in header]
    if missing:
        raise InputRejected(f"input rejected: {label} missing required column(s): {', '.join(missing)}")
    index = {name: i for i, name in enumerate(header)}
    records = []
    for raw in rows[1:]:
        if not any(cell.strip() for cell in raw):
            continue  # skip blank lines
        record = {}
        for name in header:
            i = index[name]
            record[name] = raw[i].strip() if i < len(raw) else ""
        records.append(record)
    return records


def _check_vocab(records: list[dict], column: str, allowed: set[str], label: str) -> None:
    for i, r in enumerate(records, start=1):
        value = r.get(column, "")
        if value not in allowed:
            raise InputRejected(
                f"input rejected: {label} row {i} column {column} has value {value!r} "
                f"outside allowed set {sorted(allowed)}"
            )


def _split_refs(value: str) -> list[str]:
    return [x.strip() for x in value.split("|") if x.strip()]


# ---- input loading and validation (fail-closed) --------------------------------------
def load_and_validate(input_dir: pathlib.Path) -> dict:
    if not input_dir.exists():
        raise InputRejected(f"input rejected: --input path does not exist: {input_dir}")
    if not input_dir.is_dir():
        raise InputRejected(f"input rejected: --input must be a directory: {input_dir}")

    required = {
        "phenopacket.json": input_dir / "phenopacket.json",
        "candidates.tsv": input_dir / "candidates.tsv",
        "evidence_manifest.tsv": input_dir / "evidence_manifest.tsv",
    }
    for name, path in required.items():
        if not path.is_file():
            raise InputRejected(f"input rejected: required file not found: {name}")

    raw_bytes = {name: path.read_bytes() for name, path in required.items()}

    # --- phenopacket.json ---
    try:
        phenopacket = json.loads(raw_bytes["phenopacket.json"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise InputRejected(f"input rejected: phenopacket.json is not valid JSON: {exc}") from exc
    if not isinstance(phenopacket, dict):
        raise InputRejected("input rejected: phenopacket.json must be a JSON object")
    case_id = phenopacket.get("id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise InputRejected("input rejected: phenopacket.json is missing a case id (field 'id')")
    subject = phenopacket.get("subject")
    if not isinstance(subject, dict) or not isinstance(subject.get("id"), str) or not subject["id"].strip():
        raise InputRejected("input rejected: phenopacket.json subject id is absent or unresolved")
    # Fail-closed GA4GH Phenopacket v2 contract: metaData.phenopacketSchemaVersion must
    # EXIST, be a non-empty string, and have major version 2. Missing/malformed/non-v2
    # rejects the input before any success artifact is written.
    metadata = phenopacket.get("metaData")
    if not isinstance(metadata, dict):
        raise InputRejected(
            "input rejected: phenopacket.json metaData is absent or malformed; a "
            "phenopacketSchemaVersion string with major version 2 is required"
        )
    version = metadata.get("phenopacketSchemaVersion")
    if not isinstance(version, str) or not version.strip():
        raise InputRejected(
            "input rejected: phenopacket.json metaData.phenopacketSchemaVersion is missing, "
            "empty, or not a string; a non-empty phenopacketSchemaVersion with major version "
            "2 is required"
        )
    major = version.strip().split(".")[0]
    if major != "2":
        raise InputRejected(
            f"input rejected: unsupported phenopacket schema major version in "
            f"phenopacketSchemaVersion={version!r} (only major version 2 is supported)"
        )
    features = phenopacket.get("phenotypicFeatures", [])
    if not isinstance(features, list):
        raise InputRejected("input rejected: phenopacket.json phenotypicFeatures must be a list")
    present, absent = [], []
    for f in features:
        if not isinstance(f, dict):
            raise InputRejected("input rejected: phenopacket.json phenotypicFeatures entry is malformed")
        if not isinstance(f.get("type"), dict):
            raise InputRejected("input rejected: phenopacket.json phenotypicFeatures type must be an object")
        term = f["type"].get("id")
        if not isinstance(term, str) or not term.strip():
            raise InputRejected("input rejected: phenopacket.json phenotypicFeatures entry lacks type.id")
        if "excluded" in f and not isinstance(f["excluded"], bool):
            raise InputRejected("input rejected: phenotypicFeatures excluded must be a boolean when supplied")
        if f.get("excluded") is True:
            absent.append(term)
        else:
            present.append(term)

    if set(present) & set(absent):
        raise InputRejected("input rejected: phenotype term is both present and excluded")

    # --- evidence_manifest.tsv ---
    manifest_records = _read_tsv(input_dir / "evidence_manifest.tsv", MANIFEST_COLUMNS, "evidence_manifest.tsv")
    _check_vocab(manifest_records, "verification_status", VERIFICATION, "evidence_manifest.tsv")
    manifest = {}
    for r in manifest_records:
        eid = r.get("evidence_id", "")
        if not eid:
            raise InputRejected("input rejected: evidence_manifest.tsv has a row with empty evidence_id")
        if eid in manifest:
            raise InputRejected(
                f"input rejected: evidence_manifest.tsv has a duplicate evidence_id: {eid!r}"
            )
        for field in ("source_type", "version_or_date"):
            if not r.get(field, "").strip():
                raise InputRejected(f"input rejected: evidence_manifest.tsv {eid!r} has empty {field}")
        manifest[eid] = r

    # --- candidates.tsv ---
    candidates = _read_tsv(input_dir / "candidates.tsv", CANDIDATE_COLUMNS, "candidates.tsv")
    if not candidates:
        raise InputRejected("input rejected: candidates.tsv has no candidate rows")
    # controlled-vocabulary checks (variant_class is intentionally NOT rejected here)
    _check_vocab(candidates, "genome_assembly", ASSEMBLIES, "candidates.tsv")
    _check_vocab(candidates, "inheritance_model", INHERITANCE, "candidates.tsv")
    _check_vocab(candidates, "zygosity", ZYGOSITY, "candidates.tsv")
    _check_vocab(candidates, "phase", PHASE, "candidates.tsv")
    _check_vocab(candidates, "parental_origin", PARENTAL, "candidates.tsv")
    _check_vocab(candidates, "call_quality_status", CALL_QUALITY, "candidates.tsv")
    _check_vocab(candidates, "classification", CLASSIFICATION, "candidates.tsv")
    _check_vocab(candidates, "gene_disease_validity", VALIDITY, "candidates.tsv")
    _check_vocab(candidates, "phenotype_match", PHENOTYPE_MATCH, "candidates.tsv")
    _check_vocab(candidates, "population_frequency_status", POPULATION, "candidates.tsv")

    allele_identities: set[tuple[str, str]] = set()
    for i, r in enumerate(candidates, start=1):
        identity = (r["hypothesis_id"].strip(), r["variant_id"].strip())
        if not all(identity):
            raise InputRejected(f"input rejected: candidates.tsv row {i} allele identity is empty")
        if identity in allele_identities:
            raise InputRejected(f"input rejected: candidates.tsv row {i} has duplicate allele identity {identity!r}")
        allele_identities.add(identity)
        if r.get("case_id") != case_id:
            raise InputRejected(
                f"input rejected: candidates.tsv row {i} case_id {r.get('case_id')!r} "
                f"does not match phenopacket case_id {case_id!r}"
            )
        # transcript_id required for consequence-bearing rows:
        consequence_bearing = (r.get("classification") in {"P", "LP", "VUS", "LB", "B"}) or bool(
            r.get("classification_source", "").strip()
        )
        if consequence_bearing and not r.get("transcript_id", "").strip():
            raise InputRejected(
                f"input rejected: candidates.tsv row {i} is consequence-bearing but transcript_id is empty"
            )

    hypothesis_models: dict[str, set[str]] = {}
    for row in candidates:
        hypothesis_models.setdefault(row["hypothesis_id"], set()).add(row["inheritance_model"])
    for hypothesis_id, models in hypothesis_models.items():
        if len(models) != 1:
            raise InputRejected(
                f"input rejected: hypothesis {hypothesis_id!r} has conflicting inheritance models: "
                f"{', '.join(sorted(models))}"
            )

    hypothesis_identities: dict[str, set[tuple[str, str, str]]] = {}
    hypothesis_rows: dict[str, list[dict]] = {}
    for i, row in enumerate(candidates, start=1):
        identity = (row["gene_id"].strip(), row["gene_symbol"].strip(), row["disease_id"].strip())
        if not all(identity):
            raise InputRejected(
                f"input rejected: candidates.tsv row {i} hypothesis disease-gene identity is incomplete; "
                "gene_id, gene_symbol, and disease_id are all required"
            )
        hypothesis_identities.setdefault(row["hypothesis_id"], set()).add(identity)
        hypothesis_rows.setdefault(row["hypothesis_id"], []).append(row)
    for hypothesis_id, identities in hypothesis_identities.items():
        if len(identities) != 1:
            raise InputRejected(
                f"input rejected: hypothesis {hypothesis_id!r} has conflicting disease-gene identity "
                f"(gene_id, gene_symbol, disease_id): {sorted(identities)}"
            )
    for hypothesis_id, rows in hypothesis_rows.items():
        _predictor_group_by_ref(hypothesis_id, rows, manifest)

    # --- optional family.tsv ---
    relatedness_inconsistent = False
    family_records = []
    family_path = input_dir / "family.tsv"
    if family_path.is_file():
        raw_bytes["family.tsv"] = family_path.read_bytes()
        family_records = _read_tsv(family_path, FAMILY_COLUMNS, "family.tsv")
        _check_vocab(family_records, "relatedness_status", RELATEDNESS, "family.tsv")
        for i, row in enumerate(family_records, start=1):
            if row.get("case_id") != case_id:
                raise InputRejected(
                    f"input rejected: family.tsv row {i} case_id {row.get('case_id')!r} "
                    f"does not match phenopacket case_id {case_id!r}"
                )
        relatedness_inconsistent = any(r.get("relatedness_status") == "inconsistent" for r in family_records)

    # --- optional phenotype-state-companion.json (parsed, never turns missing into absent) ---
    companion_path = input_dir / "phenotype-state-companion.json"
    if companion_path.is_file():
        raw_bytes["phenotype-state-companion.json"] = companion_path.read_bytes()
        try:
            json.loads(raw_bytes["phenotype-state-companion.json"].decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise InputRejected(
                f"input rejected: phenotype-state-companion.json is not valid JSON: {exc}"
            ) from exc

    # --- optional prior-evidence-dossier.json ---
    prior = None
    prior_path = input_dir / "prior-evidence-dossier.json"
    if prior_path.is_file():
        # Recorded in checks.input_artifacts for provenance; _analysis_input_map excludes it
        # from the reanalysis input comparison.
        raw_bytes["prior-evidence-dossier.json"] = prior_path.read_bytes()
        try:
            prior = json.loads(raw_bytes["prior-evidence-dossier.json"].decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise InputRejected(
                f"input rejected: prior-evidence-dossier.json is not valid JSON: {exc}"
            ) from exc
        if not isinstance(prior, dict):
            raise InputRejected("input rejected: prior-evidence-dossier.json must be a JSON object")
        if prior.get("schema") != SCHEMA_DOSSIER:
            raise InputRejected(
                f"input rejected: prior-evidence-dossier.json schema {prior.get('schema')!r} "
                f"does not match {SCHEMA_DOSSIER!r}"
            )
        if prior.get("case_id") != case_id:
            raise InputRejected(
                f"input rejected: prior-evidence-dossier.json case_id {prior.get('case_id')!r} "
                f"does not match phenopacket case_id {case_id!r}"
            )


        checks = prior.get("checks", {})
        hypotheses = prior.get("hypotheses", {})
        if not isinstance(checks, dict) or not isinstance(checks.get("input_artifacts", {}), dict):
            raise InputRejected("input rejected: prior-evidence-dossier.json checks/input_artifacts must be objects")
        for name, metadata in checks.get("input_artifacts", {}).items():
            if not isinstance(metadata, dict):
                raise InputRejected(f"input rejected: prior-evidence-dossier.json input_artifacts {name!r} must be an object")
        if not isinstance(hypotheses, dict):
            raise InputRejected("input rejected: prior-evidence-dossier.json hypotheses must be an object")
        for hypothesis_id, hypothesis in hypotheses.items():
            if not isinstance(hypothesis, dict):
                raise InputRejected(f"input rejected: prior-evidence-dossier.json hypothesis {hypothesis_id!r} must be an object")
            for field in ("contradiction_codes", "missing_evidence_codes"):
                value = hypothesis.get(field, [])
                if not isinstance(value, list) or not all(isinstance(code, str) for code in value):
                    raise InputRejected(f"input rejected: prior-evidence-dossier.json hypothesis {hypothesis_id!r} {field} must be a string array")

    input_hashes = {name: {"sha256": _sha256_bytes(data), "bytes": len(data)} for name, data in raw_bytes.items()}

    return {
        "case_id": case_id,
        "subject": subject,
        "present": present,
        "absent": absent,
        "manifest": manifest,
        "candidates": candidates,
        "relatedness_inconsistent": relatedness_inconsistent,
        "family": family_records,
        "prior": prior,
        "input_hashes": input_hashes,
    }


# ---- evidence gating -----------------------------------------------------------------
def _ref_status(manifest: dict, ref: str) -> str:
    if ref not in manifest:
        return "absent"
    return manifest[ref].get("verification_status", "unresolvable")


def _predictor_group_by_ref(hypothesis_id: str, rows: list[dict], manifest: dict) -> dict[str, str]:
    """Map each cited predictor reference to its single predictor_group label ('' when unlabelled).

    One reference is one evidence line however many allele rows cite it; the same reference
    carrying two different non-empty labels is contradictory input and is rejected.
    """
    labels: dict[str, set[str]] = {}
    for r in rows:
        group = r.get("predictor_group", "").strip()
        for ref in _split_refs(r.get("supporting_evidence_refs", "")):
            if manifest.get(ref, {}).get("source_type", "") not in PREDICTOR_SOURCE_TYPES:
                continue
            found = labels.setdefault(ref, set())
            if group:
                found.add(group)
    for ref, found in sorted(labels.items()):
        if len(found) > 1:
            raise InputRejected(
                f"input rejected: hypothesis {hypothesis_id!r} predictor evidence {ref!r} carries "
                f"conflicting predictor_group labels: {sorted(found)}"
            )
    return {ref: next(iter(found), "") for ref, found in labels.items()}


def _independent_line_count(rows: list[dict], manifest: dict) -> int:
    predictor_group = _predictor_group_by_ref(rows[0].get("hypothesis_id", ""), rows, manifest)
    lines = set()
    for r in rows:
        for ref in _split_refs(r.get("supporting_evidence_refs", "")):
            status = _ref_status(manifest, ref)
            if status in {"absent", "unresolvable"}:
                continue
            st = manifest.get(ref, {}).get("source_type", "")
            if st in PREDICTOR_SOURCE_TYPES:
                lines.add(("predictor", predictor_group[ref] or ref))
            else:
                lines.add(("type", st or ref))
    return len(lines)


def evaluate_hypothesis(hyp_id: str, rows: list[dict], manifest: dict, present: list[str],
                        absent: list[str], relatedness_inconsistent: bool) -> dict:
    reasons: list[str] = []
    contradiction_codes: list[str] = []
    missing_codes: list[str] = []
    abstention_code = None

    variant_ids = sorted({r.get("variant_id", "") for r in rows if r.get("variant_id")})
    inheritance_model = rows[0].get("inheritance_model")
    zygosities = {r.get("zygosity") for r in rows}
    phases = {r.get("phase") for r in rows}
    parental = [r.get("parental_origin") for r in rows]
    call_quality = {r.get("call_quality_status") for r in rows}
    classifications = {r.get("classification") for r in rows}
    variant_classes = {r.get("variant_class") for r in rows}
    validities = {r.get("gene_disease_validity") for r in rows}
    phenotype_matches = {r.get("phenotype_match") for r in rows}
    populations = {r.get("population_frequency_status") for r in rows}
    assemblies = {r.get("genome_assembly") for r in rows}
    transcripts = {r.get("transcript_id") for r in rows if r.get("transcript_id")}

    supporting_ids, contradicting_ids, cited_ids = [], [], []
    for r in rows:
        supporting_ids += _split_refs(r.get("supporting_evidence_refs", ""))
        contradicting_ids += _split_refs(r.get("contradicting_evidence_refs", ""))
        if r.get("classification_source", "").strip():
            cited_ids.append(r["classification_source"].strip())
        if r.get("gene_disease_source", "").strip():
            cited_ids.append(r["gene_disease_source"].strip())
    cited_ids = cited_ids + supporting_ids + contradicting_ids

    n_alleles = len(rows)
    is_biallelic = (n_alleles >= 2) or ("homozygous" in zygosities)

    # unresolvable evidence
    unresolvable_refs = sorted({ref for ref in cited_ids if _ref_status(manifest, ref) in {"absent", "unresolvable"}})
    # unverified supporting evidence
    unverified_support = sorted({ref for ref in supporting_ids if _ref_status(manifest, ref) == "unverified"})
    # unverified CITED source roles (classification_source / gene_disease_source). Absent or
    # unresolvable cited sources are already the EVIDENCE_UNRESOLVABLE hard stop above; a
    # present-but-unverified cited source cannot support concordance.
    cited_source_ids = sorted({
        r[col].strip()
        for r in rows
        for col in ("classification_source", "gene_disease_source")
        if r.get(col, "").strip()
    })
    unverified_cited_sources = sorted({
        ref for ref in cited_source_ids if _ref_status(manifest, ref) == "unverified"
    })
    # union of every unverified cited evidence id, preserved for machine-readable output
    unverified_evidence_ids = sorted({ref for ref in cited_ids if _ref_status(manifest, ref) == "unverified"})
    # strong-tier validity backed only by a VERIFIED but non-curated gene_disease_source
    # (e.g. a lone publication) is insufficient for concordance.
    noncurated_gene_disease_source_ids = sorted({
        r["gene_disease_source"].strip()
        for r in rows
        if r.get("gene_disease_validity") in STRONG_VALIDITY
        and r.get("gene_disease_source", "").strip()
        and _ref_status(manifest, r["gene_disease_source"].strip()) == "verified"
        and manifest.get(r["gene_disease_source"].strip(), {}).get("source_type")
        not in CURATED_GENE_DISEASE_SOURCE_TYPES
    })
    # resolvable contradicting refs and their source types
    resolvable_contradicting = [
        ref for ref in contradicting_ids if _ref_status(manifest, ref) in {"verified", "unverified"}
    ]
    build_contract_refs = [
        ref for ref in resolvable_contradicting
        if manifest.get(ref, {}).get("source_type") == "build_contract"
    ]
    other_contradicting = [ref for ref in resolvable_contradicting if ref not in build_contract_refs]

    independent_lines = _independent_line_count(rows, manifest)
    predictor_groups = sorted({r.get("predictor_group", "").strip() for r in rows if r.get("predictor_group", "").strip()})

    # ---------------- ABSTENTION cascade (highest precedence first) ----------------
    if relatedness_inconsistent:
        abstention_code = "BIOLOGICAL_RELATEDNESS_INCONSISTENT"
        reasons.append("Case-level biological-relatedness inconsistency forces abstention for every hypothesis.")
    elif unresolvable_refs:
        abstention_code = "EVIDENCE_UNRESOLVABLE"
        reasons.append(f"Cited evidence cannot be resolved to the manifest: {unresolvable_refs}.")
    elif not variant_classes.issubset(SUPPORTED_VARIANT_CLASSES):
        abstention_code = "UNSUPPORTED_VARIANT_CLASS"
        bad = sorted(variant_classes - SUPPORTED_VARIANT_CLASSES)
        reasons.append(f"Variant class(es) {bad} are outside supported snv/indel logic.")
    elif build_contract_refs or len(assemblies) > 1 or len(transcripts) > 1:
        abstention_code = "BUILD_OR_TRANSCRIPT_MISMATCH"
        if build_contract_refs:
            reasons.append(f"Supplied resolvable build-contract contradiction: {sorted(build_contract_refs)}.")
        if len(assemblies) > 1:
            reasons.append(f"Multiple genome assemblies within one hypothesis: {sorted(assemblies)}.")
        if len(transcripts) > 1:
            reasons.append(f"Conflicting transcripts within one hypothesis: {sorted(transcripts)}.")
    elif _impossible_inheritance(rows):
        abstention_code = "IMPOSSIBLE_INHERITANCE"
        reasons.append("Supplied phase/parental-origin combination is demonstrably impossible.")
    elif inheritance_model != "AD" and is_biallelic and "cis" in phases:
        abstention_code = "ALLELES_IN_CIS"
        reasons.append("Two relevant alleles are in cis; they cannot jointly satisfy a biallelic model.")
    elif call_quality & {"fail", "unknown"}:
        abstention_code = "CALL_QUALITY_INADEQUATE"
        bad = sorted(call_quality & {"fail", "unknown"})
        reasons.append(f"Call-quality status {bad} forces abstention per the input contract.")

    if abstention_code is not None:
        return _finish(hyp_id, rows, variant_ids, "abstained", contradiction_codes, missing_codes,
                       abstention_code, 0, independent_lines, reasons, supporting_ids, contradicting_ids,
                       inheritance_model, phenotype_matches, present, absent, manifest,
                       unresolvable_refs, predictor_groups,
                       unverified_evidence_ids, noncurated_gene_disease_source_ids)

    # ---------------- CONFLICTING (contradictions) ----------------
    if other_contradicting:
        contradiction_codes.append("MATERIAL_CONTRADICTION")
        reasons.append(f"Resolvable contradicting evidence: {sorted(set(other_contradicting))}.")
    if "contradictory" in phenotype_matches:
        contradiction_codes.append("MATERIAL_CONTRADICTION")
        reasons.append("Supplied phenotype_match is contradictory.")
    if validities & {"disputed", "refuted"}:
        contradiction_codes.append("MATERIAL_CONTRADICTION")
        reasons.append(f"Gene-disease validity is {sorted(validities & {'disputed', 'refuted'})}.")
    if "too_common" in populations:
        contradiction_codes.append("MATERIAL_CONTRADICTION")
        reasons.append("Population frequency is too common for the disease/inheritance model.")
    if classifications & {"LB", "B"}:
        contradiction_codes.append("MATERIAL_CONTRADICTION")
        reasons.append(f"Input classification includes {sorted(classifications & {'LB', 'B'})}.")

    contradiction_count = len(contradiction_codes)
    if contradiction_codes:
        contradiction_codes = sorted(set(contradiction_codes))
        return _finish(hyp_id, rows, variant_ids, "conflicting_hypothesis", contradiction_codes,
                       missing_codes, None, contradiction_count, independent_lines, reasons,
                       supporting_ids, contradicting_ids, inheritance_model, phenotype_matches,
                       present, absent, manifest, unresolvable_refs, predictor_groups,
                       unverified_evidence_ids, noncurated_gene_disease_source_ids)

    # ---------------- INCOMPLETE (concordance blockers) ----------------
    if inheritance_model == "AR" and not is_biallelic:
        missing_codes.append("SECOND_ALLELE_OR_COVERAGE_REQUIRED")
        reasons.append("One P/LP allele under an AR model needs a demonstrated second allele or coverage.")
    if inheritance_model == "AR" and n_alleles >= 2 and "homozygous" not in zygosities \
            and phases != {"trans"}:
        missing_codes.append("PHASE_REQUIRED")
        reasons.append("Two heterozygous alleles need phase before a biallelic model can be elevated.")
    if _inheritance_context_required(inheritance_model, rows):
        missing_codes.append("INHERITANCE_CONTEXT_REQUIRED")
        reasons.append("Necessary inheritance/segregation context is missing but not contradictory.")
    for v in validities:
        if v in {"limited", "unknown"}:
            missing_codes.append("GENE_DISEASE_VALIDITY_INSUFFICIENT")
            reasons.append(f"Gene-disease validity {v!r} is insufficient to elevate a hypothesis.")
            break
    if validities & STRONG_VALIDITY:
        for r in rows:
            if r.get("gene_disease_validity") in STRONG_VALIDITY:
                src = r.get("gene_disease_source", "").strip()
                if not src or _ref_status(manifest, src) in {"absent", "unresolvable"}:
                    missing_codes.append("GENE_DISEASE_VALIDITY_INSUFFICIENT")
                    reasons.append("Declared validity lacks a resolvable gene_disease_source.")
                    break
    if noncurated_gene_disease_source_ids:
        missing_codes.append("GENE_DISEASE_SOURCE_NOT_CURATED")
        reasons.append(
            "A strong-tier gene-disease validity is supported only by a non-curated "
            f"gene_disease_source (e.g. a lone publication): {noncurated_gene_disease_source_ids}. "
            "A single publication cannot establish a disease-gene association; a curated "
            "gene-disease assertion/curation source is required."
        )
    if "insufficient_context" in populations:
        missing_codes.append("POPULATION_CONTEXT_INSUFFICIENT")
        reasons.append("Population-frequency context is insufficient for the disease/inheritance model.")
    if unverified_support:
        missing_codes.append("SUPPORTING_EVIDENCE_UNVERIFIED")
        reasons.append(f"Supporting evidence is unverified: {unverified_support}.")
    if unverified_cited_sources:
        missing_codes.append("CITED_SOURCE_UNVERIFIED")
        reasons.append(
            "A cited source (classification_source/gene_disease_source) resolves but is "
            f"unverified and cannot support concordance: {unverified_cited_sources}."
        )
    if classifications & {"VUS", "UNCLASSIFIED"}:
        missing_codes.append("VARIANT_CLASSIFICATION_UNRESOLVED")
        reasons.append(f"Input classification {sorted(classifications & {'VUS', 'UNCLASSIFIED'})} is not P/LP.")
    if phenotype_matches & {"nonspecific", "unknown"} and not (phenotype_matches & {"specific", "supportive"}):
        missing_codes.append("PHENOTYPE_CORRELATION_INSUFFICIENT")
        reasons.append("Phenotype correlation is nonspecific or unknown.")

    if missing_codes:
        missing_codes = sorted(set(missing_codes))
        return _finish(hyp_id, rows, variant_ids, "incomplete_hypothesis", contradiction_codes,
                       missing_codes, None, 0, independent_lines, reasons, supporting_ids,
                       contradicting_ids, inheritance_model, phenotype_matches, present, absent, manifest,
                       unresolvable_refs, predictor_groups,
                       unverified_evidence_ids, noncurated_gene_disease_source_ids)

    # ---------------- CONCORDANT ----------------
    reasons.append("All quality, evidence-resolution, variant-class, inheritance/phase, validity, "
                   "population, and phenotype gates are satisfied.")
    return _finish(hyp_id, rows, variant_ids, "concordant_hypothesis", contradiction_codes,
                   missing_codes, None, 0, independent_lines, reasons, supporting_ids,
                   contradicting_ids, inheritance_model, phenotype_matches, present, absent, manifest,
                   unresolvable_refs, predictor_groups,
                   unverified_evidence_ids, noncurated_gene_disease_source_ids)


def _impossible_inheritance(rows: list[dict]) -> bool:
    # Two alleles asserted in trans but sharing one non-de-novo parental origin is impossible.
    if len(rows) >= 2 and all(r.get("phase") == "trans" for r in rows):
        origins = [r.get("parental_origin") for r in rows]
        non_denovo = [o for o in origins if o and o != "de_novo" and o != "unknown"]
        if len(non_denovo) >= 2 and len(set(non_denovo)) == 1:
            return True
    return False


def _inheritance_context_required(inheritance_model: str, rows: list[dict]) -> bool:
    if inheritance_model == "UNKNOWN":
        return True
    if any(row.get("zygosity") == "unknown" for row in rows):
        return True
    # X-linked heterozygous with unknown phase is INCOMPLETE (phase/segregation context is
    # required), never impossible; no sex-specific hard stop is inferred here.
    if inheritance_model == "X_LINKED":
        for r in rows:
            if r.get("zygosity") == "heterozygous" and r.get("phase") == "unknown":
                return True
    return False


def _phenotype_status(phenotype_matches: set) -> str:
    """Summarise per-allele phenotype_match with the gate's own precedence.

    A contradictory annotation always wins. Otherwise any specific/supportive annotation
    satisfies the phenotype gate (PHENOTYPE_CORRELATION_INSUFFICIENT fires only when the
    hypothesis is never specific/supportive), so the reported status is the best positive
    level present; only a purely adverse set reports nonspecific/unknown.
    """
    if "contradictory" in phenotype_matches:
        return "contradictory"
    for level in ("specific", "supportive", "nonspecific", "unknown"):
        if level in phenotype_matches:
            return level
    return "unknown"


def _inheritance_status(state: str, abstention_code, inheritance_model: str, rows: list[dict]) -> str:
    n = len(rows)
    zyg = {r.get("zygosity") for r in rows}
    phases = {r.get("phase") for r in rows}
    if abstention_code == "ALLELES_IN_CIS":
        return f"{inheritance_model}_alleles_in_cis"
    if abstention_code == "IMPOSSIBLE_INHERITANCE":
        return f"{inheritance_model}_impossible_segregation"
    if inheritance_model == "AR":
        if "unknown" in zyg:
            return "AR_zygosity_unknown"
        if n < 2 and "homozygous" not in zyg:
            return "AR_single_allele"
        if phases == {"trans"}:
            return "AR_biallelic_trans"
        if "homozygous" in zyg:
            return "AR_homozygous"
        if phases != {"trans"}:
            return "AR_phase_unknown"
        return "AR_biallelic"
    if inheritance_model == "AD":
        return "AD_" + (next(iter(zyg)) if len(zyg) == 1 else "mixed_zygosity")
    if inheritance_model == "X_LINKED":
        return "X_linked"
    return "unknown_inheritance_context"


def _finish(hyp_id, rows, variant_ids, state, contradiction_codes, missing_codes, abstention_code,
            contradiction_count, independent_lines, reasons, supporting_ids, contradicting_ids,
            inheritance_model, phenotype_matches, present, absent, manifest,
            unresolved_evidence_ids, predictor_groups,
            unverified_evidence_ids=None, noncurated_gene_disease_source_ids=None) -> dict:
    r0 = rows[0]
    phenotype_status = _phenotype_status(phenotype_matches)
    inheritance_status = _inheritance_status(state, abstention_code, inheritance_model, rows)
    unresolved = sorted(set(unresolved_evidence_ids))
    _resolved = {"verified", "unverified"}
    supporting_resolved = sorted({x for x in set(supporting_ids) if _ref_status(manifest, x) in _resolved})
    contradicting_resolved = sorted({x for x in set(contradicting_ids) if _ref_status(manifest, x) in _resolved})
    claim = (
        f"Research hypothesis {hyp_id}: {r0.get('gene_symbol') or r0.get('gene_id')} / "
        f"{r0.get('disease_id')} evaluated as {state} under {inheritance_model}. "
        f"This is research decision support, not a diagnosis, probability, or formal classification."
    )
    gates = {
        "call_quality": sorted({r.get("call_quality_status") for r in rows}),
        "variant_class": sorted({r.get("variant_class") for r in rows}),
        "inheritance_model": inheritance_model,
        "phase": sorted({r.get("phase") for r in rows}),
        "gene_disease_validity": sorted({r.get("gene_disease_validity") for r in rows}),
        "phenotype_match": sorted(phenotype_matches),
        "population_frequency_status": sorted({r.get("population_frequency_status") for r in rows}),
        "evidence_resolution": "hard_stop_detected" if unresolved else "all_resolved",
    }
    return {
        "hypothesis_id": hyp_id,
        "case_id": r0.get("case_id"),
        "disease_id": r0.get("disease_id"),
        "gene_id": r0.get("gene_id"),
        "gene_symbol": r0.get("gene_symbol"),
        "variant_ids": variant_ids,
        "inheritance_model": inheritance_model,
        "evidence_state": state,
        "inheritance_status": inheritance_status,
        "phenotype_status": phenotype_status,
        "contradiction_codes": sorted(set(contradiction_codes)),
        "missing_evidence_codes": sorted(set(missing_codes)),
        "abstention_code": abstention_code,
        "contradiction_count": int(contradiction_count),
        "independent_evidence_line_count": int(independent_lines),
        "predictor_groups": sorted(set(predictor_groups)),
        "supporting_evidence_ids": supporting_resolved,
        "contradicting_evidence_ids": contradicting_resolved,
        "unresolved_evidence_ids": unresolved,
        "unverified_evidence_ids": sorted(set(unverified_evidence_ids or [])),
        "noncurated_gene_disease_source_ids": sorted(set(noncurated_gene_disease_source_ids or [])),
        "gates": gates,
        "reasons": reasons,
        "claim_text": claim,
    }


# ---- provenance and reanalysis -------------------------------------------------------
def compute_source_version_set_id(manifest: dict) -> str:
    """Identifier for the supplied evidence source/version SET only.

    Derived purely from the (evidence_id, version_or_date) pairs in the evidence
    manifest -- never from analysis-input file bytes and never from a supplied prior
    dossier. This keeps reanalysis source-version comparison meaningful: merely
    re-supplying the prior dossier cannot manufacture a false source-version change.
    Complete per-file input hashes are recorded separately in dossier provenance
    (checks.input_artifacts).
    """
    items = sorted((eid, row.get("version_or_date", "")) for eid, row in manifest.items())
    payload = json.dumps({"evidence": items}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _analysis_input_map(hashes: dict) -> dict:
    """Per-file sha256 map for analysis inputs, excluding the self-referential prior dossier."""
    return {
        name: meta.get("sha256")
        for name, meta in (hashes or {}).items()
        if name != "prior-evidence-dossier.json"
    }


def compute_reanalysis(prior, current_hypotheses: dict, current_svsid: str,
                       current_input_hashes: dict) -> tuple[dict, dict]:
    if not prior:
        diff = {"applicable": False, "review_recommended": False,
                "reason": "no prior evidence dossier was supplied"}
        statuses = {hid: "not_applicable" for hid in current_hypotheses}
        return diff, statuses

    prior_hyps = prior.get("hypotheses", {}) if isinstance(prior.get("hypotheses"), dict) else {}
    prior_svsid = prior.get("source_version_set_id")
    source_version_changed = prior_svsid != current_svsid

    # Analysis-input hashes are compared separately from the source-version set. The prior
    # dossier is excluded from both sides so that merely supplying it cannot register as an
    # input change (it is never part of the analysis-input set in the first place).
    current_input_map = _analysis_input_map(current_input_hashes)
    prior_input_map = _analysis_input_map(prior.get("checks", {}).get("input_artifacts", {}))
    analysis_inputs_changed = current_input_map != prior_input_map

    per = {}
    statuses = {}
    review = source_version_changed or analysis_inputs_changed
    for hid, cur in current_hypotheses.items():
        if hid not in prior_hyps:
            statuses[hid] = "new"
            per[hid] = {"status": "new", "current_evidence_state": cur["evidence_state"]}
            review = True
            continue
        p = prior_hyps[hid]
        state_changed = p.get("evidence_state") != cur["evidence_state"]
        rank_changed = p.get("rank") != cur.get("rank")
        contra_changed = sorted(p.get("contradiction_codes", []) or []) != cur["contradiction_codes"]
        missing_changed = sorted(p.get("missing_evidence_codes", []) or []) != cur["missing_evidence_codes"]
        abst_changed = p.get("abstention_code") != cur["abstention_code"]
        changed = any([state_changed, rank_changed, contra_changed, missing_changed, abst_changed])
        statuses[hid] = "changed" if changed else "unchanged"
        if changed:
            review = True
        per[hid] = {
            "status": statuses[hid],
            "prior_evidence_state": p.get("evidence_state"),
            "current_evidence_state": cur["evidence_state"],
            "prior_rank": p.get("rank"),
            "current_rank": cur.get("rank"),
            "state_changed": state_changed,
            "rank_changed": rank_changed,
            "contradiction_changed": contra_changed,
            "missing_evidence_changed": missing_changed,
            "abstention_changed": abst_changed,
        }
    removed = sorted(set(prior_hyps) - set(current_hypotheses))
    if removed:
        review = True
    diff = {
        "applicable": True,
        "review_recommended": bool(review),
        "prior_source_version_set_id": prior_svsid,
        "current_source_version_set_id": current_svsid,
        "source_version_changed": bool(source_version_changed),
        "analysis_inputs_changed": bool(analysis_inputs_changed),
        "hypotheses": per,
        "added_hypotheses": sorted(set(current_hypotheses) - set(prior_hyps)),
        "removed_hypotheses": removed,
    }
    return diff, statuses


# ---- figure --------------------------------------------------------------------------
def write_figure(path: pathlib.Path, ordered: list[dict]) -> None:
    matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
    matplotlib.rcParams["svg.fonttype"] = "none"

    # Okabe-Ito colorblind-safe palette
    C_OK = "#009E73"      # satisfied / concordant
    C_MISS = "#E69F00"    # missing / incomplete
    C_CONFLICT = "#D55E00"  # contradiction / conflicting
    C_ABSTAIN = "#000000"   # abstained (hard gate)
    C_NA = "#999999"        # not applicable

    dims = ["call_quality", "variant_class", "evidence_resolution", "inheritance_phase",
            "gene_disease_validity", "phenotype", "population", "evidence_state"]

    def cell_color(h, dim):
        st = h["evidence_state"]
        code = h["abstention_code"]
        if dim == "evidence_state":
            return {"concordant_hypothesis": C_OK, "incomplete_hypothesis": C_MISS,
                    "conflicting_hypothesis": C_CONFLICT, "abstained": C_ABSTAIN}[st]
        if dim == "call_quality":
            return C_OK if set(h["gates"]["call_quality"]) == {"pass"} else C_ABSTAIN
        if dim == "variant_class":
            return C_ABSTAIN if not set(h["gates"]["variant_class"]).issubset(SUPPORTED_VARIANT_CLASSES) else C_OK
        if dim == "evidence_resolution":
            if h["unresolved_evidence_ids"]:
                return C_ABSTAIN
            if h["unverified_evidence_ids"] or ({"SUPPORTING_EVIDENCE_UNVERIFIED", "CITED_SOURCE_UNVERIFIED"}
                                                  & set(h["missing_evidence_codes"])):
                return C_MISS
            return C_OK
        if dim == "inheritance_phase":
            if code in {"ALLELES_IN_CIS", "IMPOSSIBLE_INHERITANCE", "BUILD_OR_TRANSCRIPT_MISMATCH",
                        "BIOLOGICAL_RELATEDNESS_INCONSISTENT"}:
                return C_ABSTAIN
            # The cis hard stop is derived from the retained gate observation, so a
            # higher-precedence abstention (e.g. EVIDENCE_UNRESOLVABLE) cannot hide it.
            if (h["inheritance_model"] != "AD" and len(h["variant_ids"]) >= 2
                    and "cis" in h["gates"]["phase"]):
                return C_ABSTAIN
            if h["inheritance_status"] in {"unknown_inheritance_context", "AR_zygosity_unknown",
                                             "AR_single_allele", "AR_phase_unknown"}:
                return C_MISS
            if {"SECOND_ALLELE_OR_COVERAGE_REQUIRED", "PHASE_REQUIRED",
                    "INHERITANCE_CONTEXT_REQUIRED"} & set(h["missing_evidence_codes"]):
                return C_MISS
            return C_OK
        if dim == "gene_disease_validity":
            # Gate observations and source audit survive early conflict/abstention returns.
            validities = set(h["gates"]["gene_disease_validity"])
            if validities & {"disputed", "refuted"}:
                return C_CONFLICT
            if (validities & {"limited", "unknown"}
                    or h["noncurated_gene_disease_source_ids"]
                    or {"GENE_DISEASE_VALIDITY_INSUFFICIENT", "GENE_DISEASE_SOURCE_NOT_CURATED"}
                    & set(h["missing_evidence_codes"])):
                return C_MISS
            return C_OK
        if dim == "phenotype":
            if h["phenotype_status"] == "contradictory":
                return C_CONFLICT
            if set(h["gates"]["phenotype_match"]).issubset({"unknown", "nonspecific"}):
                return C_MISS
            return C_OK
        if dim == "population":
            if "too_common" in h["gates"]["population_frequency_status"]:
                return C_CONFLICT
            if "insufficient_context" in h["gates"]["population_frequency_status"]:
                return C_MISS
            return C_OK
        return C_NA

    n = len(ordered)
    fig_h = max(2.4, 0.6 * n + 1.5)
    fig, ax = plt.subplots(figsize=(9.5, fig_h))
    for yi, h in enumerate(ordered):
        for xi, dim in enumerate(dims):
            ax.add_patch(plt.Rectangle((xi, n - 1 - yi), 1, 1, facecolor=cell_color(h, dim),
                                       edgecolor="white", linewidth=1.5))
    ax.set_xlim(0, len(dims))
    ax.set_ylim(0, n)
    ax.set_xticks([i + 0.5 for i in range(len(dims))])
    ax.set_xticklabels([d.replace("_", "\n") for d in dims], fontsize=8)
    ax.set_yticks([n - 1 - yi + 0.5 for yi in range(n)])
    ax.set_yticklabels([f"#{h['rank']} {h['hypothesis_id']}" for h in ordered], fontsize=8)
    ax.set_title("Per-hypothesis evidence completeness and hard-gate outcomes", fontsize=11)
    ax.set_aspect("equal")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0)
    legend = [
        Patch(facecolor=C_OK, label="satisfied"),
        Patch(facecolor=C_MISS, label="missing / incomplete"),
        Patch(facecolor=C_CONFLICT, label="contradiction"),
        Patch(facecolor=C_ABSTAIN, label="hard-gate abstention"),
    ]
    ax.legend(handles=legend, loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8, frameon=False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="png", dpi=150)
    plt.close(fig)


# ---- output assembly -----------------------------------------------------------------
def build_outputs(data: dict, entry_mode: str) -> dict:
    manifest = data["manifest"]
    present, absent = data["present"], data["absent"]
    svsid = compute_source_version_set_id(manifest)

    groups: dict[str, list[dict]] = {}
    order_seen = []
    for r in data["candidates"]:
        hid = r.get("hypothesis_id")
        if hid not in groups:
            groups[hid] = []
            order_seen.append(hid)
        groups[hid].append(r)

    evaluated = {}
    for hid in order_seen:
        evaluated[hid] = evaluate_hypothesis(
            hid, groups[hid], manifest, present, absent, data["relatedness_inconsistent"]
        )

    ordered = sorted(
        evaluated.values(),
        key=lambda h: (STATE_ORDER[h["evidence_state"]], -h["independent_evidence_line_count"], h["hypothesis_id"]),
    )
    for i, h in enumerate(ordered, start=1):
        h["rank"] = i

    current_for_diff = {h["hypothesis_id"]: h for h in ordered}
    diff, statuses = compute_reanalysis(data["prior"], current_for_diff, svsid, data["input_hashes"])
    for h in ordered:
        h["reanalysis_status"] = statuses.get(h["hypothesis_id"], "not_applicable")
        h["entry_mode"] = entry_mode
        h["source_version_set_id"] = svsid

    counts = {
        "n_hypotheses_total": len(ordered),
        "n_concordant": sum(1 for h in ordered if h["evidence_state"] == "concordant_hypothesis"),
        "n_incomplete": sum(1 for h in ordered if h["evidence_state"] == "incomplete_hypothesis"),
        "n_conflicting": sum(1 for h in ordered if h["evidence_state"] == "conflicting_hypothesis"),
        "n_abstained": sum(1 for h in ordered if h["evidence_state"] == "abstained"),
    }

    all_unresolved = sorted({rid for h in ordered for rid in h.get("unresolved_evidence_ids", [])})
    all_unverified = sorted({rid for h in ordered for rid in h.get("unverified_evidence_ids", [])})
    all_noncurated = sorted({rid for h in ordered for rid in h.get("noncurated_gene_disease_source_ids", [])})

    dossier = {
        "schema": SCHEMA_DOSSIER,
        "case_id": data["case_id"],
        "entry_mode": entry_mode,
        "generated_by": COMPONENT_ID,
        "source_version_set_id": svsid,
        "subject": {"id": data["subject"].get("id"), "sex": data["subject"].get("sex", "UNKNOWN_SEX")},
        "phenotype": {
            "present": present,
            "absent": absent,
            "n_present": len(present),
            "n_absent": len(absent),
            "note": "Terms absent from the record are unknown, never absent; excluded:true is explicitly absent.",
        },
        "ordering": [h["hypothesis_id"] for h in ordered],
        "unresolved_evidence_ids": all_unresolved,
        "unverified_evidence_ids": all_unverified,
        "noncurated_gene_disease_source_ids": all_noncurated,
        "hypotheses": {h["hypothesis_id"]: _dossier_entry(h) for h in ordered},
        "summary": counts,
        "checks": {
            "schema_validation": "pass",
            "evidence_resolution": "hard_stop_detected" if all_unresolved else "pass",
            "unresolved_evidence_id_count": len(all_unresolved),
            "unverified_evidence_id_count": len(all_unverified),
            "noncurated_gene_disease_source_id_count": len(all_noncurated),
            "hard_gates": "evaluated",
            "report_data_consistency": "pending_presentation",
            "input_artifacts": data["input_hashes"],
        },
        "limitations": [
            "Research decision support only; not a diagnosis, probability, or ACMG/AMP classification.",
            "A single publication cannot establish a novel gene-disease association.",
            "Only pre-annotated nuclear germline SNVs/indels for established disease-gene hypotheses.",
        ],
    }
    return {"ordered": ordered, "dossier": dossier, "reanalysis": diff, "svsid": svsid}


def _dossier_entry(h: dict) -> dict:
    keys = [
        "hypothesis_id", "rank", "case_id", "disease_id", "gene_id", "gene_symbol", "variant_ids",
        "inheritance_model", "evidence_state", "inheritance_status", "phenotype_status",
        "contradiction_codes", "missing_evidence_codes", "abstention_code", "contradiction_count",
        "independent_evidence_line_count", "predictor_groups",
        "supporting_evidence_ids", "contradicting_evidence_ids", "unresolved_evidence_ids",
        "unverified_evidence_ids", "noncurated_gene_disease_source_ids",
        "gates", "reasons", "claim_text", "reanalysis_status", "entry_mode", "source_version_set_id",
    ]
    return {k: h.get(k) for k in keys}


def write_hypotheses_tsv(path: pathlib.Path, ordered: list[dict], entry_mode: str, svsid: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(HYPOTHESES_TSV_COLUMNS)
        for h in ordered:
            writer.writerow([
                h["case_id"], h["rank"], entry_mode, h["hypothesis_id"], h["disease_id"], h["gene_id"],
                "|".join(h["variant_ids"]), h["evidence_state"], h["inheritance_status"],
                h["phenotype_status"], "|".join(h["contradiction_codes"]),
                "|".join(h["missing_evidence_codes"]), h["abstention_code"] or "",
                svsid, h["reanalysis_status"],
            ])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--entry-mode", choices=list(ENTRY_MODES), default="incomplete_evidence")
    args = parser.parse_args(argv)

    try:
        data = load_and_validate(pathlib.Path(args.input))
    except InputRejected as exc:
        print(str(exc), file=sys.stderr)
        return 2

    result = build_outputs(data, args.entry_mode)

    results_dir = pathlib.Path(os.environ.get("BIOMNI_RESULTS") or os.getcwd())
    hyp_tsv = results_dir / "hypotheses.tsv"
    dossier_json = results_dir / "evidence_dossier.json"
    reanalysis_json = results_dir / "reanalysis_diff.json"
    figure_png = results_dir / "figures" / "evidence_completeness.png"

    write_hypotheses_tsv(hyp_tsv, result["ordered"], args.entry_mode, result["svsid"])
    dossier_json.write_text(json.dumps(result["dossier"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    reanalysis_json.write_text(json.dumps(result["reanalysis"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_figure(figure_png, result["ordered"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
