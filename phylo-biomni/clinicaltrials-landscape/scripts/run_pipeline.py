#!/usr/bin/env python3
"""
Bundled offline/live data pipeline for the ClinicalTrials.gov landscape skill.

Runs the deterministic data portion of the workflow end to end and writes, under the results
directory: trials_all.csv, coverage_scope.json, trials_by_category.csv, trials_by_sponsor.csv,
trials_match_evidence.csv, figures/ + figures/manifest.json, facts_payload.json, report_facts.json, and
report_spec.json.

It does NOT produce the infographic or the PDF (those are agent-tool steps in the terminal report
step). The report is assembled separately after these retrieval and synthesis helpers complete.

Usage (offline / deterministic validation):
    python3 scripts/run_pipeline.py --input <compiled_trials.csv> \
        --coverage <coverage_scope.json> --results-dir <results_root>

Usage (live API):
    python3 scripts/run_pipeline.py --live --conditions "Crohn's Disease" "Ulcerative Colitis" \
        --intervention "interleukin-23" --results-dir <results_root>

Usage (simple PSMA prompt with versioned profile):
    python3 scripts/run_pipeline.py --live \
        --prompt "What is the current ClinicalTrials.gov landscape for PSMA in prostate cancer?" \
        --results-dir <results_root>
"""

from __future__ import annotations

import argparse
import json
import hashlib
from io import BytesIO
from pathlib import Path
import os
import sys

_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)
_SCRIPTS_ROOT = os.path.join(_PKG_ROOT, "scripts")
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)

import pandas as pd  # noqa: E402
from scripts.build_report_spec import write_report_spec  # noqa: E402
from scripts.landscape_evidence import write_report_facts  # noqa: E402
from scripts import query_clinicaltrials  # noqa: E402

from scripts.classify_study import classify_all, STUDY_PURPOSES, INTERVENTION_CATEGORIES  # noqa: E402
from scripts.compile_trials import compile_trials  # noqa: E402
from scripts.coverage_scope import build_coverage_scope, validate_coverage_scope  # noqa: E402
from scripts.export_all import export_all  # noqa: E402
from scripts.generate_landscape_plots import generate_landscape_plots  # noqa: E402
from scripts.build_report_facts import write_facts_payload  # noqa: E402
from scripts.query_profiles import (  # noqa: E402
    all_registry_statuses,
    boolean_or_expression,
    get_query_profile,
    profile_for_prompt,
    profile_metadata,
)


def _resolve_live_query(args):
    """Resolve an explicit query or one unambiguous versioned prompt profile."""
    if args.profile and args.prompt:
        raise ValueError("use --profile or --prompt, not both")

    profile = get_query_profile(args.profile) if args.profile else None
    if args.prompt:
        profile = profile_for_prompt(args.prompt)
        if profile is None:
            raise ValueError(
                "the prompt does not match a versioned query profile; provide --conditions and "
                "an optional --intervention expression explicitly"
            )

    if profile:
        if args.conditions or args.intervention or args.statuses or args.phases or args.sponsor_filter:
            raise ValueError(
                "a versioned query profile cannot be combined with explicit conditions, "
                "intervention aliases, statuses, phases, or sponsor filters"
            )
        return {
            "conditions": list(profile["conditions"]),
            "intervention_filter": boolean_or_expression(profile["aliases"]),
            "statuses": all_registry_statuses(),
            "query_profile": profile_metadata(profile),
            "phases": None,
            "sponsor_filter": None,
        }

    if not args.conditions:
        raise ValueError("live mode requires --conditions, --profile, or a recognized --prompt")
    return {
        "conditions": list(args.conditions),
        "intervention_filter": args.intervention,
        "statuses": list(args.statuses or all_registry_statuses()),
        "query_profile": None,
        "phases": args.phases,
        "sponsor_filter": args.sponsor_filter,
    }


def run(args) -> int:
    results_dir = os.path.abspath(args.results_dir)
    # Resolve report artifacts against the same results directory.
    os.environ["BIOMNI_RESULTS"] = results_dir
    os.makedirs(results_dir, exist_ok=True)

    if args.live:
        query = _resolve_live_query(args)
        raw, scope = query_clinicaltrials.query_trials(
            conditions=query["conditions"],
            intervention_filter=query["intervention_filter"],
            statuses=query["statuses"],
            return_metadata=True,
            query_profile=query["query_profile"],
            max_pages=args.max_pages,
            phases=query["phases"],
            sponsor_filter=query["sponsor_filter"],
        )
        classified = classify_all(raw)
        df = compile_trials(classified)
        coverage_input = scope  # already validated; export re-binds to the exported bytes
    else:
        prov = json.loads(Path(args.coverage).read_text())
        validate_coverage_scope(prov)
        payload = Path(args.input).read_bytes()
        if not prov.get("dataset_sha256") or hashlib.sha256(payload).hexdigest() != prov["dataset_sha256"]:
            raise ValueError("offline input fingerprint does not match the coverage binding")
        if "dataset_rows" not in prov:
            raise ValueError("offline coverage binding is missing dataset_rows")
        df = pd.read_csv(BytesIO(payload))
        if len(df) != int(prov["dataset_rows"]) or len(df) != int(prov["records_retrieved"]):
            raise ValueError("offline coverage binding row count mismatch")
        label_columns = {"study_purpose", "intervention_category"}
        if label_columns.issubset(df.columns):
            if not df["study_purpose"].isin(STUDY_PURPOSES).all() or not df["intervention_category"].isin(INTERVENTION_CATEGORIES).all():
                raise ValueError("offline input contains invalid saved classifications")
            df["classification_resolved"] = (df["study_purpose"] != "Unresolved") & (df["intervention_category"] != "Unclassified (insufficient description)")
        elif label_columns.intersection(df.columns):
            raise ValueError("offline input has only part of the saved classification schema")
        else:
            df = classify_all(df)
        df = compile_trials(df)
        # Reuse the declared query provenance from the supplied coverage_scope.json, re-bound to the
        # freshly exported CSV bytes by export_all/bind_exported_dataset.
        coverage_input = build_coverage_scope(
            conditions=prov["conditions"],
            statuses=prov["statuses"],
            intervention_filter=prov.get("intervention_filter"),
            phases=prov.get("phases"),
            sponsor_filter=prov.get("sponsor_filter"),
            pages_retrieved=prov.get("pages_retrieved", 1),
            records_retrieved=len(df),
            api_total_count=prov.get("api_total_count", len(df)),
            pagination_exhausted=prov.get("pagination_exhausted", True),
            query_date=prov.get("query_date"),
            input_resolution=prov.get("input_resolution", "declared_query"),
            query_profile=prov.get("query_profile"),
        )

    export_all(df, parameters={"coverage_scope": coverage_input}, output_dir=results_dir)
    generate_landscape_plots(df, results_dir=results_dir)

    write_facts_payload(results_dir=results_dir)
    write_report_facts(results_dir)
    write_report_spec(results_dir=results_dir)
    print(f"\u2713 Pipeline complete; results in {results_dir}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bounded ClinicalTrials.gov landscape data pipeline")
    ap.add_argument("--input", help="compiled trials CSV (offline path)")
    ap.add_argument("--coverage", help="coverage_scope.json providing declared query provenance (offline)")
    ap.add_argument("--live", action="store_true", help="query the live ClinicalTrials.gov API v2")
    ap.add_argument("--conditions", nargs="+", help="condition terms (live path)")
    ap.add_argument("--intervention", default=None, help="intervention filter term (live path)")
    ap.add_argument("--statuses", nargs="+", default=None, help="status filters")
    ap.add_argument("--max-pages", type=int, default=query_clinicaltrials.DEFAULT_MAX_PAGES, help="maximum live-query pages; incomplete retrieval fails closed")
    ap.add_argument("--phases", nargs="+", default=None, help="registry phase filters (live path)")
    ap.add_argument("--sponsor", "--sponsor-filter", dest="sponsor_filter", default=None, help="lead sponsor filter (live path)")
    ap.add_argument("--profile", help="exact id of a bundled versioned query profile")
    ap.add_argument("--prompt", help="verbatim simple prompt to resolve against bundled profiles")
    ap.add_argument("--results-dir", default="/mnt/results", help="results root for all outputs")
    args = ap.parse_args(argv)
    if not args.live and not (args.input and args.coverage):
        ap.error("offline mode requires --input and --coverage (or use --live with --conditions)")
    if args.live and not (args.conditions or args.profile or args.prompt):
        ap.error("--live requires --conditions, --profile, or --prompt")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
