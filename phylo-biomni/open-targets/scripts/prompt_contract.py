#!/usr/bin/env python3
"""Resolve Open Targets task inputs before asking a clarification question.

This module deliberately separates prompt completeness from scientific execution.
It never invents an identifier.  It applies documented defaults only when they are
unambiguous and returns the precise missing or conflicting field otherwise.
"""

from __future__ import annotations

import argparse
import json
import re


_DISEASE_ID_RE = re.compile(r"\b(?:MONDO|EFO)[_:]\d+\b", re.IGNORECASE)
_STUDY_ID_RE = re.compile(r"\bGCST\d+\b", re.IGNORECASE)
_TOP_N_PATTERNS = (
    re.compile(r"\btop\s*[- ]?\s*(\d+)\b", re.IGNORECASE),
    re.compile(r"\b(?:first|rank)\s+(\d+)\s+targets?\b", re.IGNORECASE),
)
_CREDIBLE_RE = re.compile(r"\b(?:credible\s*[- ]?sets?|l2g|gwas\s+stud(?:y|ies))\b", re.IGNORECASE)
_EVIDENCE_ONLY_RE = re.compile(r"\b(?:evidence[ -]only|without\s+(?:gwas|credible\s*[- ]?sets?))\b", re.IGNORECASE)


def _unique_normalized(pattern: re.Pattern[str], text: str, *, colon_to_underscore: bool = False) -> list[str]:
    values = []
    for match in pattern.findall(text):
        value = str(match).upper()
        if colon_to_underscore:
            value = value.replace(":", "_")
        if value not in values:
            values.append(value)
    return values


def _top_values(text: str) -> list[int]:
    values: list[int] = []
    for pattern in _TOP_N_PATTERNS:
        for match in pattern.findall(text):
            value = int(match)
            if value not in values:
                values.append(value)
    return values


def resolve_prompt(prompt: str) -> dict:
    """Return a deterministic ready/clarify decision for a task prompt.

    Defaults are part of the public skill contract: top_n defaults to 10 and a
    task with no GWAS/credible-set request uses the evidence-only branch.  A GWAS
    study id selects the credible-set branch.  Questions are emitted only for
    missing required identifiers or genuinely conflicting values.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        prompt = ""

    diseases = _unique_normalized(_DISEASE_ID_RE, prompt, colon_to_underscore=True)
    studies = _unique_normalized(_STUDY_ID_RE, prompt)
    top_values = _top_values(prompt)
    credible_requested = bool(_CREDIBLE_RE.search(prompt) or studies)
    evidence_only_requested = bool(_EVIDENCE_ONLY_RE.search(prompt))

    questions: list[dict] = []
    if not diseases:
        questions.append({
            "field": "disease_id",
            "reason": "missing required EFO or MONDO disease identifier",
        })
    elif len(diseases) > 1:
        questions.append({
            "field": "disease_id",
            "reason": f"ambiguous disease identifiers: {', '.join(diseases)}",
        })

    if len(top_values) > 1:
        questions.append({
            "field": "top_n",
            "reason": f"ambiguous top-N values: {', '.join(map(str, top_values))}",
        })

    if len(studies) > 1:
        questions.append({
            "field": "study_id",
            "reason": f"ambiguous GWAS study identifiers: {', '.join(studies)}",
        })

    if credible_requested and evidence_only_requested:
        questions.append({
            "field": "scope",
            "reason": "prompt requests both evidence-only and GWAS/credible-set analysis",
        })
    elif credible_requested and not studies:
        questions.append({
            "field": "study_id",
            "reason": "GWAS/credible-set analysis was requested but no GCST study identifier was supplied",
        })

    scope = "with_credible_sets" if credible_requested and not evidence_only_requested else "evidence_only"
    decision = {
        "status": "clarify" if questions else "ready",
        "inputs": {
            "disease_id": diseases[0] if len(diseases) == 1 else None,
            "top_n": top_values[0] if len(top_values) == 1 else (10 if not top_values else None),
            "study_id": studies[0] if len(studies) == 1 else None,
            "scope": scope,
        },
        "selected_branch_ids": [f"scope:{scope}"],
        "questions": questions,
    }
    return decision


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve Open Targets prompt inputs")
    parser.add_argument("--prompt", required=True, help="complete user task prompt")
    args = parser.parse_args()
    decision = resolve_prompt(args.prompt)
    print(json.dumps(decision, indent=2, sort_keys=True))
    return 0 if decision["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
