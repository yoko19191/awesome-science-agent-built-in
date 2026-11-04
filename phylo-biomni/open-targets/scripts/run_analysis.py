#!/usr/bin/env python3
"""Main analysis orchestrator for the Open Targets target-prioritization workflow.

Runs the full pipeline:
  1. Capture API/data release metadata
  2. Resolve disease identity
  3. Rank a bounded target set by overall association score
  4. Retrieve bounded evidence for the top target
  5. Inspect GWAS study metadata and credible sets
  6. Serialize all records through the discriminated semantic schema
  7. Write provenance.json, result tables, report_facts.json
  8. Build deterministic figures (via run_bundled)
  9. Render one canonical PDF through the selected report-style provider
 10. Record PDF review (text extraction, page render, visual review attestation)
 11. Write run_receipt.json via report_qc.write_receipt

Usage:
    python run_analysis.py --efo-id MONDO_0004975 --top-n 10 --study-id GCST005194
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

# Add scripts directory to path for imports
SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from open_targets_client import (  # noqa: E402
    OpenTargetsClient, OperationLedger, save_ledger,
    GraphQLError, TransportError, DEFAULT_BUDGETS,
)
from queries import REGISTRY, NINE_OPERATIONS, query_hash  # noqa: E402
from semantics import (  # noqa: E402
    serialize_association, serialize_evidence, serialize_study,
    serialize_credible_set, serialize_l2g, serialize_colocalisation,
    validate_records,
)

RESULTS = pathlib.Path(os.environ.get("BIOMNI_RESULTS", "/mnt/results"))


def _check_study_concordance(efo_id: str, disease_name: str,
                             studies_facts: list[dict]) -> dict:
    """Check whether a GWAS study's returned trait/ontology matches the disease context.

    Returns a dict with:
      - concordant: bool
      - study_id: str
      - study_trait: str
      - disease_id: str
      - reason: str

    If concordance cannot be established, the study must be presented as
    independent_study_context and excluded from target-support conclusions (OT-R2-07).
    """
    if not studies_facts:
        return {
            "concordant": False,
            "study_id": "",
            "study_trait": "",
            "disease_id": efo_id,
            "reason": "no studies retrieved",
        }

    study = studies_facts[0]
    study_trait = study.get("trait_from_source", "") or ""
    study_id = study.get("study_id", "")

    # Check 1: does the study's trait text match the disease name?
    trait_lower = study_trait.lower().strip()
    disease_lower = disease_name.lower().strip()
    text_match = (
        trait_lower and disease_lower
        and (trait_lower in disease_lower or disease_lower in trait_lower)
    )

    # Check 2: does the study's diseases list contain the queried EFO ID?
    study_diseases = study.get("diseases") or []
    ontology_match = any(
        d.get("id", "") == efo_id for d in study_diseases
    ) if isinstance(study_diseases, list) else False

    concordant = text_match or ontology_match
    reason = (
        "study trait matches disease name" if text_match
        else "study diseases list contains the queried EFO ID" if ontology_match
        else f"study trait '{study_trait}' does not match disease '{disease_name}' "
             f"and EFO ID {efo_id} is not in the study's diseases list"
    )

    return {
        "concordant": concordant,
        "study_id": study_id,
        "study_trait": study_trait,
        "disease_id": efo_id,
        "reason": reason,
    }


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _study_fact(study_obj: dict) -> dict:
    """Project API study metadata without dropping ontology disease evidence."""
    return {
        "study_id": study_obj.get("id", ""),
        "study_type": study_obj.get("studyType"),
        "trait_from_source": study_obj.get("traitFromSource", ""),
        "pubmed_id": study_obj.get("pubmedId"),
        "publication_first_author": study_obj.get("publicationFirstAuthor"),
        "n_samples": study_obj.get("nSamples"),
        "n_cases": study_obj.get("nCases"),
        "n_controls": study_obj.get("nControls"),
        "diseases": study_obj.get("diseases") or [],
    }


def run_pipeline(efo_id: str, top_n: int, study_id: str, *,
                 outdir: pathlib.Path = RESULTS,
                 client: OpenTargetsClient | None = None,
                 provider_rendered_report: pathlib.Path | None = None,
                 visual_review_state: str = "not_evaluable",
                 visual_review_notes: str = "",
                 content_only: bool = False) -> dict:
    """Run the full analysis pipeline and return a summary dict."""

    if visual_review_state == "pass" and not visual_review_notes.strip():
        raise ValueError("a passing visual review requires non-empty inspection evidence")

    if client is None:
        client = OpenTargetsClient()

    # --- Step 1: Capture release ---
    release = client.capture_release()
    _dv = release['dataVersion']
    release_label = f"{_dv['year']}.{int(_dv['month']):02d}"
    api_version = f"{release['apiVersion']['x']}.{release['apiVersion']['y']}.{release['apiVersion']['z']}"

    # --- Step 2: Resolve disease + rank targets ---
    assoc_query = REGISTRY["meta_and_associated_targets"]["query"]
    plan = client.plan_request("meta_and_associated_targets", top_n, n_entities=1)
    if plan["route"] != "api":
        raise RuntimeError(f"over-budget for API: {plan}")

    data = client.request("meta_and_associated_targets", assoc_query,
                          {"efoId": efo_id, "size": top_n})
    disease = data.get("disease") or {}
    assoc = disease.get("associatedTargets") or {}
    total_targets = assoc.get("count", 0)
    rows = assoc.get("rows") or []

    # Serialize association records
    association_records = []
    top_targets_facts = []
    for i, row in enumerate(rows):
        target = row.get("target") or {}
        score = row.get("score", 0.0)
        dt_scores = row.get("datatypeScores") or []
        rec = serialize_association(
            rank=i + 1,
            target_id=target.get("id", ""),
            symbol=target.get("approvedSymbol", ""),
            name=target.get("approvedName", ""),
            biotype=target.get("biotype", ""),
            overall_score=score,
            datatype_scores=dt_scores,
        )
        association_records.append(rec)
        top_targets_facts.append({
            "rank": i + 1,
            "target_id": target.get("id", ""),
            "approved_symbol": target.get("approvedSymbol", ""),
            "approved_name": target.get("approvedName", ""),
            "biotype": target.get("biotype", ""),
            "overall_association_score": score,
            "datatype_scores": [
                {"datatype_id": ds.get("id", ""), "score": ds.get("score", 0.0)}
                for ds in dt_scores
            ],
        })

    # --- Step 3: Retrieve evidence for top target ---
    evidence_records = []
    evidence_rows_facts = []
    top_target_id = rows[0]["target"]["id"] if rows else ""
    top_target_symbol = rows[0]["target"]["approvedSymbol"] if rows else ""

    if top_target_id:
        ev_query = REGISTRY["evidence"]["query"]
        ev_data = client.request("evidence", ev_query,
                                 {"efoId": efo_id, "ensemblId": top_target_id,
                                  "size": 25, "cursor": None})
        ev_field = (ev_data.get("disease") or {}).get("evidences") or {}
        ev_count = ev_field.get("count", 0)
        ev_rows = ev_field.get("rows") or []

        for ev_row in ev_rows:
            rec = serialize_evidence(
                datasource_id=ev_row.get("datasourceId", ""),
                datatype_id=ev_row.get("datatypeId", ""),
                score=ev_row.get("score", 0.0),
                literature=ev_row.get("literature") or [],
                urls=ev_row.get("urls") or [],
                study_overview=ev_row.get("studyOverview"),
                author=ev_row.get("publicationFirstAuthor"),
                year=ev_row.get("publicationYear"),
            )
            evidence_records.append(rec)
            evidence_rows_facts.append({
                "datasource_id": ev_row.get("datasourceId", ""),
                "datatype_id": ev_row.get("datatypeId", ""),
                "score": ev_row.get("score", 0.0),
                "publication_first_author": ev_row.get("publicationFirstAuthor"),
                "publication_year": ev_row.get("publicationYear"),
            })

    # --- Step 4: GWAS study metadata ---
    study_records = []
    studies_facts = []
    study_query = REGISTRY["study"]["query"]
    try:
        study_data = client.request("study", study_query, {"studyId": study_id})
        study_obj = study_data.get("study") or {}
        rec = serialize_study(
            study_id=study_obj.get("id", ""),
            study_type=study_obj.get("studyType"),
            trait=study_obj.get("traitFromSource", ""),
            pmid=study_obj.get("pubmedId"),
            author=study_obj.get("publicationFirstAuthor"),
            n_samples=study_obj.get("nSamples"),
            n_cases=study_obj.get("nCases"),
            n_controls=study_obj.get("nControls"),
            diseases=study_obj.get("diseases") or [],
        )
        study_records.append(rec)
        studies_facts.append(_study_fact(study_obj))
    except (GraphQLError, TransportError) as exc:
        studies_facts.append({"study_id": study_id, "error": str(exc)})

    # --- Disease-study concordance check (OT-R2-07) ---
    # Before integrating an optional study, derive its returned trait/ontology
    # identifiers and compare them to the disease context.  If concordance cannot
    # be established, the study is labeled independent_study_context and excluded
    # from target-support conclusions.
    study_concordance = _check_study_concordance(
        efo_id, disease.get("name", ""), studies_facts
    )

    # --- Step 5: Credible sets ---
    credible_set_records = []
    l2g_records = []
    colocalisation_records = []
    credible_sets_facts = []

    cs_query = REGISTRY["credible_sets"]["query"]
    try:
        cs_data = client.request("credible_sets", cs_query,
                                 {"studyIds": [study_id], "size": 10})
        cs_field = cs_data.get("credibleSets") or {}
        cs_rows = cs_field.get("rows") or []

        for cs_row in cs_rows:
            sl_id = cs_row.get("studyLocusId", "")
            region = cs_row.get("region", "")
            pval_m = cs_row.get("pValueMantissa")
            pval_e = cs_row.get("pValueExponent")
            variant = cs_row.get("variant") or {}
            variant_id = variant.get("id")
            rs_ids = variant.get("rsIds") or []

            cs_rec = serialize_credible_set(sl_id, region, pval_m or 0, pval_e or 0,
                                            variant_id, rs_ids)
            credible_set_records.append(cs_rec)

            # L2G predictions
            l2g = cs_row.get("l2GPredictions") or {}
            for l2g_row in (l2g.get("rows") or []):
                tgt = l2g_row.get("target") or {}
                l2g_rec = serialize_l2g(sl_id, tgt.get("id", ""),
                                        tgt.get("approvedSymbol", ""),
                                        l2g_row.get("score", 0.0))
                l2g_records.append(l2g_rec)

            # Colocalisation
            coloc = cs_row.get("colocalisation") or {}
            for coloc_row in (coloc.get("rows") or []):
                other = coloc_row.get("otherStudyLocus") or {}
                coloc_rec = serialize_colocalisation(
                    sl_id, other.get("studyId"),
                    coloc_row.get("h4"), coloc_row.get("clpp"),
                )
                colocalisation_records.append(coloc_rec)

            credible_sets_facts.append({
                "study_locus_id": sl_id,
                "study_id": study_concordance.get("study_id") or study_id,
                "study_context": (
                    "concordant_study_context"
                    if study_concordance.get("concordant")
                    else "independent_study_context"
                ),
                "region": region,
                "pvalue": f"{pval_m}e{pval_e}" if pval_m is not None else None,
                "variant_id": variant_id,
            })
    except (GraphQLError, TransportError) as exc:
        credible_sets_facts.append({"error": str(exc)})

    # --- Step 6: Validate all records ---
    all_records = (association_records + evidence_records + study_records
                   + credible_set_records + l2g_records + colocalisation_records)
    validation_errors = validate_records(all_records)

    # --- Step 7: Write artifacts ---
    outdir.mkdir(parents=True, exist_ok=True)

    # Operation ledger
    ledger_path = outdir / "operation_ledger.json"
    save_ledger(client.ledger, str(ledger_path), source_mode=client.source_mode)
    ledger_sha256 = _sha256_file(ledger_path)

    # Provenance
    # Read the fixed qualitative infographic prompt and record its hash (OT-R2-02)
    infographic_prompt_path = SCRIPTS_DIR.parent / "assets" / "infographic_prompt.txt"
    infographic_prompt_text = ""
    infographic_prompt_hash = ""
    if infographic_prompt_path.exists():
        infographic_prompt_text = infographic_prompt_path.read_text(encoding="utf-8").strip()
        infographic_prompt_hash = hashlib.sha256(
            infographic_prompt_text.encode("utf-8")
        ).hexdigest()

    # OT-R2-02: record delivered image SHA256 if the image exists (generated by agent via GenerateImage)
    infographic_image_path = outdir / "infographic_open_targets_workflow.png"
    infographic_image_hash = ""
    if infographic_image_path.exists():
        infographic_image_hash = _sha256_file(infographic_image_path)

    # OT-R4: classify the three-state infographic lineage from the platform execution trace and
    # thread a single source-of-truth state through facts, provenance, and the report. The exact
    # prompt is never reconstructed from a truncated trace; a not_evaluable state is disclosed, not
    # laundered into a verified claim.
    from report_qc import classify_infographic_lineage, LINEAGE_VERIFIED
    _lineage = classify_infographic_lineage(
        "infographic_open_targets_workflow.png",
        prompt_file=str(infographic_prompt_path),
        image_file=str(infographic_image_path),
    )
    lineage_state = _lineage["state"]
    lineage_verified = lineage_state == LINEAGE_VERIFIED
    lineage_disclosure = "" if lineage_verified else (
        "Infographic provenance is not independently verified from the platform execution trace "
        "(the GenerateImage tool-call arguments were truncated or incomplete in the trace); "
        "no verification claim is made."
    )

    provenance = {
        "schema": "phylo-ot-provenance/1",
        "release": release,
        "release_label": release_label,
        "api_version": api_version,
        "access_utc": release.get("access_utc", ""),
        "source_mode": client.source_mode,
        "operation_ledger_sha256": ledger_sha256,
        "disease": {
            "id": disease.get("id", efo_id),
            "name": disease.get("name", ""),
            "dbXRefs": disease.get("dbXRefs") or [],
        },
        "query_hashes": {op: query_hash(op) for op in NINE_OPERATIONS},
        "budgets": dict(DEFAULT_BUDGETS),
        "completeness": "bounded_sample",
        "total_associated_targets": total_targets,
        "retrieved_targets": len(rows),
        "validation_errors": validation_errors,
        "infographic": {
            "filename": "infographic_open_targets_workflow.png",
            "prompt_source": "assets/infographic_prompt.txt",
            "prompt_sha256": infographic_prompt_hash,
            "image_sha256": infographic_image_hash,
            "qualitative": True,
            "data_bearing": False,
            # OT-R4: the lineage state governs whether provenance may be claimed as verified.
            "lineage_state": lineage_state,
            "independently_verified": lineage_verified,
            "disclosure": lineage_disclosure,
            "description": (
                "Qualitative workflow diagram showing the five-stage Open Targets API "
                "pipeline and the API-versus-bulk data boundary. Produced during the run via "
                "the Biomni GenerateImage tool from a fixed package prompt; provenance is "
                "verified from the execution trace. Contains no run-specific numbers, scores, "
                "rankings, or scientific data values."
                if lineage_verified else
                "Qualitative workflow diagram showing the five-stage Open Targets API "
                "pipeline and the API-versus-bulk data boundary. Produced during the run via "
                "the Biomni GenerateImage tool from a fixed package prompt. " + lineage_disclosure +
                " Contains no run-specific numbers, scores, rankings, or scientific data values."
            ),
        },
    }
    provenance_path = outdir / "provenance.json"
    provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True), encoding="utf-8")

    # Result tables (CSV)
    targets_csv = outdir / "target_ranking.csv"
    with open(targets_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["source_mode", "rank", "target_id", "approved_symbol", "approved_name",
                         "biotype", "overall_association_score"])
        for t in top_targets_facts:
            writer.writerow([client.source_mode, t["rank"], t["target_id"], t["approved_symbol"],
                             t["approved_name"], t["biotype"],
                             f"{t['overall_association_score']:.6f}"])

    evidence_csv = outdir / "evidence_rows.csv"
    with open(evidence_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["source_mode", "datasource_id", "datatype_id", "score",
                         "publication_first_author", "publication_year"])
        for e in evidence_rows_facts:
            writer.writerow([client.source_mode, e["datasource_id"], e["datatype_id"],
                             f"{e['score']:.6f}" if e.get("score") is not None else "",
                             e.get("publication_first_author") or "",
                             e.get("publication_year") or ""])

    # Report facts
    ledger_summary = client.ledger.count_by_state()
    # Derive executed operations from the ledger (OT-R2-06)
    executed_operations = [r.operation for r in client.ledger.records
                           if r.terminal_state == "success"]
    facts = {
        "source_mode": client.source_mode,
        "operation_ledger_sha256": ledger_sha256,
        "disease_name": disease.get("name", ""),
        "disease_id": disease.get("id", efo_id),
        "release_label": release_label,
        "api_version": api_version,
        "access_date": release.get("access_utc", "")[:10],
        "total_associated_targets": total_targets,
        "top_targets": top_targets_facts,
        "evidence_count": ev_count if top_target_id else 0,
        "evidence_rows": evidence_rows_facts,
        "studies": studies_facts,
        "credible_sets": credible_sets_facts,
        "l2g_predictions": l2g_records,
        "colocalisation": colocalisation_records,
        "study_concordance": study_concordance,
        # OT-R4: single source-of-truth infographic-lineage state consumed by the report and
        # re-verified by the run receipt. verification_claim is True only when lineage is verified.
        "infographic_lineage": {
            "state": lineage_state,
            "reason": _lineage.get("reason", ""),
            "verification_claim": lineage_verified,
            "prompt_verifiable": bool(_lineage.get("evidence", {}).get("prompt_verifiable",
                                                                       lineage_verified)),
            "image_sha256": infographic_image_hash or None,
            "disclosure": lineage_disclosure,
        },
        "completeness": "bounded_sample",
        "max_pages": DEFAULT_BUDGETS["max_pages"],
        "max_rows": DEFAULT_BUDGETS["max_rows"],
        "operation_ledger_summary": {
            "total_attempts": len(client.ledger.records),
            "successes": client.ledger.success_count(),
            "failures": client.ledger.failure_count(),
            "by_state": ledger_summary,
        },
        "executed_operations": executed_operations,
        "registered_operations_count": len(NINE_OPERATIONS),
        "registered_operations": list(NINE_OPERATIONS),
        # schema_smoke.py is opt-in and not invoked in run_pipeline; the report must not
        # claim live schema smoke validation unless it was actually executed.
        "schema_smoke_executed": False,
        "validation_errors": validation_errors,
        "caveats": [
            "Association scores (0-1) are integrated prioritization metrics, not effect estimates, replication, causal proof, or clinical validation.",
            f"The sample of {len(rows)} targets out of {total_targets} total is bounded, not exhaustive.",
            "L2G and colocalisation records carry null datasource_id with an explicit not-applicable reason; they are not evidence datasource records.",
            "Do not freeze today's target ranking as a timeless biological oracle; rankings change with each data release.",
            "The Open Targets API has no documented rate limits; do not infer unlimited-use from missing rate-limit headers.",
        ],
        "next_steps": [
            "Retrieve bulk association/evidence parquet for genome-wide analysis via the Open Targets FTP/AWS Open Data bucket.",
            "Cross-reference top targets with druggability and tractability assessments.",
            "Investigate credible-set L2G predictions for the top-ranked locus.",
            "Re-run this analysis after each Open Targets data release to track ranking changes.",
        ],
    }
    facts_path = outdir / "report_facts.json"
    facts_path.write_text(json.dumps(facts, indent=2, sort_keys=True), encoding="utf-8")

    # --- Step 8: Build figures via run_bundled ---
    from report_content import write_report_content
    from report_qc import (run_bundled, write_receipt, record_pdf_review,
                           assert_figures, finalize_root_bundle,
                           finalize_report_artifacts, report_render_plan,
                           staged_copy, _pdf_page_count)

    # Run visualize.py
    run_bundled(
        [sys.executable, "scripts/visualize.py", "--facts", str(facts_path),
         "--outdir", str(outdir)],
        "scripts/visualize.py",
        ["figures/figure_1_target_ranking.png", "figures/figure_2_datatype_heatmap.png"],
    )

    # Establish scientific lineage before deriving presentation-independent content.
    scientific_artifacts = [
        "target_ranking.csv", "evidence_rows.csv", "operation_ledger.json",
        "provenance.json", "report_facts.json", "figure_payloads.json",
        "figures/figure_1_target_ranking.png", "figures/figure_2_datatype_heatmap.png",
        "infographic_open_targets_workflow.png",
    ]
    finalize_root_bundle(scientific_artifacts)
    report_content_path = write_report_content(outdir)
    if content_only:
        return {
            "release_label": release_label,
            "disease_name": disease.get("name", ""),
            "disease_id": disease.get("id", efo_id),
            "total_targets": total_targets,
            "retrieved_targets": len(rows),
            "evidence_count": ev_count if top_target_id else 0,
            "ledger_summary": ledger_summary,
            "validation_errors": validation_errors,
            "provenance_path": str(provenance_path),
            "facts_path": str(facts_path),
            "report_content_path": str(report_content_path),
            "report_status": "awaiting_provider_render",
        }

    # --- Step 9: Render one canonical PDF through the selected provider ---
    staging_dir = pathlib.Path(os.environ.get("BIOMNI_WORKSPACE", "/workspace")) / "ot_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    workspace_report_file = staging_dir / "report_open-targets.pdf"
    render_plan = report_render_plan()
    bundled_files = ["scripts/visualize.py"]
    if render_plan["render_with_bundled"]:
        workspace_report_file.unlink(missing_ok=True)
        run_bundled(
            [sys.executable, "scripts/build_report.py", "--content", str(report_content_path),
             "--output", str(workspace_report_file)],
            "scripts/build_report.py",
            [str(workspace_report_file)],
        )
        bundled_files.append("scripts/build_report.py")
    else:
        if provider_rendered_report is None:
            raise RuntimeError(
                "an explicit report-style provider was selected; render report_content.json "
                "through that provider and pass provider_rendered_report"
            )
        workspace_report_file = provider_rendered_report
        if not workspace_report_file.is_file() or workspace_report_file.stat().st_size == 0:
            raise RuntimeError(
                f"provider-rendered canonical report is missing or empty: {workspace_report_file}"
            )
    staged_copy(workspace_report_file, "report_open-targets.pdf")
    finalize_report_artifacts("report_open-targets.pdf")
    root_artifacts = [
        *scientific_artifacts, "report_content.json", "report_structure.json",
        "report_open-targets.pdf",
    ]

    # --- Step 10: Record PDF review ---
    report_name = "report_open-targets.pdf"
    report_path = outdir / report_name

    # Extract text
    text_artifact = "report_open-targets_text.txt"
    text_path = outdir / text_artifact
    try:
        import pypdf
        reader = pypdf.PdfReader(str(report_path))
        text_parts = []
        for i, page in enumerate(reader.pages):
            text_parts.append(f"--- Page {i+1} ---\n{page.extract_text()}")
        text_path.write_text("\n".join(text_parts), encoding="utf-8")
    except Exception:
        pdftotext = shutil.which("pdftotext")
        if pdftotext is None:
            raise RuntimeError("neither pypdf nor pdftotext is available for PDF text extraction")
        completed = subprocess.run(
            [pdftotext, str(report_path), str(text_path)], check=False,
            capture_output=True, text=True, timeout=30,
        )
        if completed.returncode != 0 or not text_path.is_file():
            raise RuntimeError(f"pdftotext failed: {completed.stderr[-300:]}")

    # Render pages
    rendered_pages = []
    n_pages = _pdf_page_count(report_path)

    pages_dir = outdir / "figures" / "pdf_pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    for page_num in range(1, n_pages + 1):
        page_img = pages_dir / f"page_{page_num}.png"
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(str(report_path))
            page = doc[page_num - 1]
            pix = page.get_pixmap(dpi=150)
            pix.save(str(page_img))
            doc.close()
        except ImportError:
            pdftoppm = shutil.which("pdftoppm")
            if pdftoppm is None:
                raise RuntimeError("neither PyMuPDF nor pdftoppm is available for PDF rendering")
            completed = subprocess.run(
                [pdftoppm, "-f", str(page_num), "-l", str(page_num), "-singlefile",
                 "-png", "-r", "150", str(report_path), str(page_img.with_suffix(""))],
                check=False, capture_output=True, text=True, timeout=60,
            )
            if completed.returncode != 0 or not page_img.is_file():
                raise RuntimeError(f"pdftoppm failed: {completed.stderr[-300:]}")
        rendered_pages.append(str(page_img.relative_to(outdir)))

    # Record PDF review with honest attestation
    # OT-R2-05: bind every event to the current final-PDF SHA256 and every artifact SHA256
    current_pdf_sha256 = _sha256_file(report_path)
    artifact_sha256s = {}
    artifact_sha256s[text_artifact] = _sha256_file(text_path)
    for rp in rendered_pages:
        rp_path = outdir / rp
        if rp_path.exists():
            artifact_sha256s[rp] = _sha256_file(rp_path)

    if visual_review_state == "pass":
        review_attestation = (
            f"Every rendered page was visually inspected. Visual review evidence: "
            f"{visual_review_notes}. The canonical report was rendered through "
            f"{render_plan['provider']} and its provider-specific style is verified "
            "independently by the receipt gate."
        )
    else:
        review_attestation = (
            "Rendered pages and extracted text are available, but visual review did not pass. "
            f"Review state: {visual_review_state}. Evidence: {visual_review_notes}. "
            f"The canonical report was rendered through {render_plan['provider']}."
        )
    record_pdf_review(
        report_name=report_name,
        text_artifact=text_artifact,
        rendered_page_files=rendered_pages,
        reviewed_page_numbers=(
            list(range(1, n_pages + 1)) if visual_review_state == "pass" else []
        ),
        review_attestation=review_attestation,
        review_state=visual_review_state,
        pdf_sha256=current_pdf_sha256,
        artifact_sha256s=artifact_sha256s,
    )

    # --- Step 11: Write run receipt ---
    figures_manifest = [
        {"step": 1, "file": "figures/figure_1_target_ranking.png",
         "caption": f"Top {len(rows)} targets ranked by overall association score for {disease.get('name', '')}. Scores are integrated prioritization metrics (0-1), not effect estimates."},
        {"step": 2, "file": "figures/figure_2_datatype_heatmap.png",
         "caption": f"Per-datatype contribution scores for the top {len(rows)} targets. Each cell is a prioritization metric (0-1), not an effect estimate or causal proof."},
    ]

    write_receipt(
        report_name=report_name,
        figures=figures_manifest,
        bundled_files=tuple(bundled_files),
        outputs=("figures/figure_1_target_ranking.png",
                 "figures/figure_2_datatype_heatmap.png"),
        infographics=("infographic_open_targets_workflow.png",),
        infographic_prompt="assets/infographic_prompt.txt",
        qc_run_log="qc_run_log.json",
        contract="skill_contract.json",
        narrative_facts="report_facts.json",
        narrative_provenance="provenance.json",
        root_artifacts=root_artifacts,
        path="run_receipt.json",
        strict=True,
    )

    return {
        "release_label": release_label,
        "disease_name": disease.get("name", ""),
        "disease_id": disease.get("id", efo_id),
        "total_targets": total_targets,
        "retrieved_targets": len(rows),
        "evidence_count": ev_count if top_target_id else 0,
        "ledger_summary": ledger_summary,
        "validation_errors": validation_errors,
        "provenance_path": str(provenance_path),
        "facts_path": str(facts_path),
        "report_path": str(report_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Open Targets target-prioritization analysis")
    parser.add_argument("--efo-id", default="MONDO_0004975", help="EFO disease ID")
    parser.add_argument("--top-n", type=int, default=10, help="number of top targets to retrieve")
    parser.add_argument("--study-id", default="GCST005194", help="GWAS Catalog study ID")
    parser.add_argument("--outdir", default=str(RESULTS), help="output directory")
    parser.add_argument(
        "--content-only",
        action="store_true",
        help="stop after scientific artifacts and style-free report content are finalized",
    )
    parser.add_argument(
        "--provider-rendered-report",
        help=(
            "completed canonical PDF rendered by the explicitly selected provider; "
            "this path is never provider-selection evidence"
        ),
    )
    parser.add_argument(
        "--visual-review-state",
        required=False,
        choices=["pass", "fail", "not_evaluable", "not_applicable"],
    )
    parser.add_argument(
        "--visual-review-notes",
        required=False,
        help="evidence from genuine inspection of every rendered page",
    )
    args = parser.parse_args()

    if not args.content_only and (not args.visual_review_state or not args.visual_review_notes):
        parser.error("final report generation requires --visual-review-state and --visual-review-notes")
    if args.visual_review_state == "pass" and "not_evaluable" in args.visual_review_notes.lower():
        parser.error("a pass review cannot describe itself as not_evaluable")

    summary = run_pipeline(args.efo_id, args.top_n, args.study_id,
                           outdir=pathlib.Path(args.outdir),
                           provider_rendered_report=(
                               pathlib.Path(args.provider_rendered_report)
                               if args.provider_rendered_report else None
                           ),
                           visual_review_state=args.visual_review_state or "not_evaluable",
                           visual_review_notes=args.visual_review_notes or "",
                           content_only=args.content_only)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
