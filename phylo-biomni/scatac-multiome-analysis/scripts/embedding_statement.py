#!/usr/bin/env python3
"""
Derive-and-gate the infographic embedding/identity claim from the run receipt.

Why this module exists
----------------------
The native review of this skill found a hallucination: the authoring run's narrative
and deliverables stated the infographic was embedded "with pixel-identical content,"
citing the count-only `figures_embedded` heuristic as support -- but the decoded-pixel
lineage gate had returned "found 0" and never ran, so pixel identity was never
verified. The prose out-ran the executed evidence.

`scripts/report_qc.py` (the system-template production gate) already writes an honest
receipt and already owns the real decoded-pixel comparison machinery. It is deliberately
left untouched. This module is the *reader* that sits between that honest receipt and any
human-facing prose: it derives the embedding/identity sentence *only* from what the
receipt's `infographic_lineage_verified` outcome and its evidence object actually prove,
and refuses to let strong pixel-identity language be emitted otherwise.

Contract with the receipt schema (`phylo-run-receipt/2`, written by
`report_qc.write_receipt`):

  receipt["infographic_lineage_verified"]            -> True | False | "not_applicable"
  receipt["infographic_lineage_verified_reason"]     -> str (present when not True)
  receipt["figures_embedded"]                        -> "pass"|"fail"|"not_evaluable"|"not_applicable"
  receipt["evidence"]["infographic_lineage_verified"]["items"][i] -> {
      "requested_filename": str,
      "generated_decoded_pixel_sha256": str,   # source image, decoded pixels
      "embedded_decoded_pixel_sha256":  str,   # the one PDF embedding it was bound to
      "embedded_page": int, "embedded_image_index": int,
      "final_pdf_sha256": str, ...
  }

Pixel identity is a *completed decoded-pixel comparison*: an evidence item whose generated
and embedded decoded-pixel hashes are both present and equal, bound to exactly the first
image on page 1. Anything else -- a false/not_evaluable outcome, a missing evidence
object, disagreeing hashes, or an off-page/off-index binding -- is NOT pixel identity, and
this module will not describe it as such.

Public API
----------
  evaluate(receipt)                      -> dict   (state + reason + hashes, no prose)
  derive_embedding_statement(receipt)    -> dict   (state + one honest sentence)
  assert_receipt_consistent(receipt)     -> None   (raise if a True claim lacks the evidence)
  assert_embedding_prose_supported(text, receipt) -> None
                                                   (raise if strong prose is unproven)

CLI
---
  python embedding_statement.py <run_receipt.json>       # print state + derived sentence
  python embedding_statement.py --check-text <file> <run_receipt.json>   # gate a text file

Stdlib only (json, re, hashlib not required here, argparse, pathlib, sys). No third-party
imports, so it runs anywhere the receipt does and adds no dependency to the skill.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

__all__ = [
    "EmbeddingClaimError",
    "FORBIDDEN_PATTERNS",
    "evaluate",
    "derive_embedding_statement",
    "assert_receipt_consistent",
    "assert_embedding_prose_supported",
]

# States the embedding/identity claim can be in, derived from the receipt.
STATE_VERIFIED = "verified"            # completed decoded-pixel comparison, hashes equal, page1/img1
STATE_INCONSISTENT = "inconsistent"   # receipt claims True but evidence does not back it (fail closed)
STATE_NOT_EVALUABLE = "not_evaluable"  # outcome False: the comparison did not complete (records reason)
STATE_NOT_APPLICABLE = "not_applicable"  # infographic not required for this run (contract-authorized)
STATE_MISSING = "missing"             # no infographic_lineage_verified outcome recorded at all

# Strong claims that ONLY a completed decoded-pixel comparison may license. Kept as verbatim
# case-insensitive patterns, including the exact phrase the review flagged ("pixel-identical
# content"). Add equivalents here, not a looser regex, so the guard stays legible and auditable.
FORBIDDEN_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"pixel[\s\-]*identical",   # "pixel-identical", "pixel identical"
        r"pixel[\s\-]*identity",    # "pixel identity"
        r"pixel[\s\-]*perfect",     # "pixel-perfect"
        r"pixel[\s\-]*exact",       # "pixel-exact"
        r"bit[\s\-]*identical",     # "bit-identical" (of an image)
        r"identical\s+content",     # "...embedded with identical content"
        r"byte[\s\-]*for[\s\-]*byte\s+identical\s+image",
    )
]


class EmbeddingClaimError(Exception):
    """Raised when prose or a receipt asserts pixel identity the evidence does not establish."""


# --------------------------------------------------------------------------- receipt access ----
def _load(receipt) -> dict:
    """Accept a receipt as a dict, a JSON string, or a path to a JSON file."""
    if isinstance(receipt, dict):
        return receipt
    if isinstance(receipt, (str, pathlib.Path)):
        p = pathlib.Path(receipt)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        # A bare JSON string (not a path) is also accepted.
        if isinstance(receipt, str):
            return json.loads(receipt)
    raise EmbeddingClaimError(f"cannot load receipt from {type(receipt).__name__}: {receipt!r}")


def _lineage_items(receipt: dict) -> list:
    """The per-infographic evidence items produced by assert_infographic_pdf_lineage, or []."""
    ev = receipt.get("evidence")
    if not isinstance(ev, dict):
        return []
    node = ev.get("infographic_lineage_verified")
    if not isinstance(node, dict):
        return []
    items = node.get("items")
    return items if isinstance(items, list) else []


def _item_is_pixel_identical(item: dict) -> bool:
    """One evidence item proves pixel identity iff a real decoded-pixel comparison bound it.

    Both decoded-pixel hashes must be present and EQUAL (the comparison outcome), and the
    embedding must be the first image on page 1 -- exactly what assert_infographic_pdf_lineage
    records when it succeeds. Missing or disagreeing hashes are treated as no proof.
    """
    if not isinstance(item, dict):
        return False
    gen = item.get("generated_decoded_pixel_sha256")
    emb = item.get("embedded_decoded_pixel_sha256")
    if not (isinstance(gen, str) and isinstance(emb, str) and gen and emb):
        return False
    if gen != emb:
        return False
    return item.get("embedded_page") == 1 and item.get("embedded_image_index") == 1


# --------------------------------------------------------------------------- evaluation --------
def evaluate(receipt) -> dict:
    """Return the structured embedding-identity state read from the receipt (no prose).

    Keys: state, outcome (raw), reason, items (verified evidence items), figures_embedded.
    """
    data = _load(receipt)
    outcome = data.get("infographic_lineage_verified", "__absent__")
    reason = str(data.get("infographic_lineage_verified_reason", "") or "").strip()
    figures_embedded = data.get("figures_embedded")
    items = _lineage_items(data)
    verified_items = [it for it in items if _item_is_pixel_identical(it)]

    if outcome == "__absent__":
        state = STATE_MISSING
    elif outcome is True:
        # A True outcome must be backed by a completed decoded-pixel comparison. If the evidence
        # object does not carry equal hashes bound to page 1 / image 1, the receipt out-runs its
        # own evidence -> flag it rather than trusting the boolean (fail closed).
        state = STATE_VERIFIED if verified_items else STATE_INCONSISTENT
    elif outcome == STATE_NOT_APPLICABLE:
        state = STATE_NOT_APPLICABLE
    elif outcome is False:
        state = STATE_NOT_EVALUABLE
    else:
        # Any unexpected encoding is treated as unproven, never as verified.
        state = STATE_INCONSISTENT

    return {
        "state": state,
        "outcome": outcome,
        "reason": reason,
        "items": verified_items,
        "n_evidence_items": len(items),
        "figures_embedded": figures_embedded,
    }


def assert_receipt_consistent(receipt) -> None:
    """Raise if the receipt claims verified lineage but the evidence object does not back it.

    This is the "a receipt may not out-run its evidence" guard. It catches a receipt whose
    boolean was set True while the decoded-pixel hashes are absent, disagree, or point away
    from the first image on page 1 -- the signature of a post-snapshot image mutation or a
    swapped PDF embedding smuggled past the boolean.
    """
    info = evaluate(receipt)
    if info["outcome"] is True and info["state"] != STATE_VERIFIED:
        raise EmbeddingClaimError(
            "run_receipt.json records infographic_lineage_verified=true but its evidence does "
            "not contain a completed decoded-pixel comparison (equal generated/embedded decoded-"
            "pixel hashes bound to page 1, image 1). Refusing to treat the claim as proven."
        )


# --------------------------------------------------------------------------- prose derivation --
def derive_embedding_statement(receipt) -> dict:
    """Derive one honest embedding/identity sentence from the receipt.

    Returns {state, sentence, hashes, reason}. Strong pixel-identity language appears in
    `sentence` ONLY when state == verified. For a self-contradictory (inconsistent) receipt
    this raises, because there is no honest sentence to write for a claim the evidence denies.
    """
    info = evaluate(receipt)
    state = info["state"]

    if state == STATE_INCONSISTENT:
        assert_receipt_consistent(receipt)  # raises with the specific reason

    if state == STATE_VERIFIED:
        item = info["items"][0]
        gen = item["generated_decoded_pixel_sha256"]
        emb = item["embedded_decoded_pixel_sha256"]
        name = item.get("requested_filename", "the infographic")
        sentence = (
            f"The GenerateImage infographic ({name}) is embedded as the first image on page 1 "
            f"of the report and verified pixel-identical by a decoded-pixel comparison: the "
            f"generated source pixels and the single embedded PDF image share one decoded-pixel "
            f"hash (generated_decoded_pixel_sha256={gen} == embedded_decoded_pixel_sha256={emb})."
        )
        return {"state": state, "sentence": sentence,
                "hashes": {"generated_decoded_pixel_sha256": gen,
                           "embedded_decoded_pixel_sha256": emb},
                "reason": ""}

    if state == STATE_NOT_APPLICABLE:
        reason = info["reason"] or "the run declared no infographic requirement"
        sentence = (
            f"No infographic was required for this run ({reason}); no embedding comparison is claimed."
        )
        return {"state": state, "sentence": sentence, "hashes": {}, "reason": reason}

    if state == STATE_MISSING:
        sentence = (
            "run_receipt.json records no infographic lineage outcome; no embedding comparison is claimed."
        )
        return {"state": state, "sentence": sentence, "hashes": {}, "reason": ""}

    # STATE_NOT_EVALUABLE: an embedding may exist, but the decoded-pixel comparison did not run.
    # The sentence is deliberately written WITHOUT any of the strong claim tokens so the honest
    # disclaimer never trips the (fail-closed) gate below.
    reason = info["reason"] or "the decoded-pixel comparison did not complete"
    fe = info["figures_embedded"]
    embed_clause = (
        f" The report's images passed only a count-only embedding check (figures_embedded={fe!r}), "
        "which compares image counts and does not establish which image was embedded."
        if fe is not None else ""
    )
    sentence = (
        "Infographic image lineage was not verified for this run "
        f"(infographic_lineage_verified={info['outcome']!r}: {reason})." + embed_clause +
        " This run therefore does not assert that the embedded image reproduces the generated "
        "infographic."
    )
    return {"state": state, "sentence": sentence, "hashes": {}, "reason": reason}


# --------------------------------------------------------------------------- prose gate ---------
def _forbidden_hits(text: str) -> list:
    return sorted({m.group(0) for pat in FORBIDDEN_PATTERNS for m in pat.finditer(text or "")})


def assert_embedding_prose_supported(text, receipt) -> None:
    """Raise EmbeddingClaimError if `text` makes a pixel-identity claim the receipt does not prove.

    Run this over the report text, the run receipt-adjacent narrative, and the closing/authoring
    summary before finishing. Strong pixel-identity phrasing is permitted only when the receipt's
    state is `verified`; a self-contradictory receipt is rejected outright.
    """
    info = evaluate(receipt)
    state = info["state"]

    # A receipt that claims True without backing evidence can never license prose.
    if info["outcome"] is True and state != STATE_VERIFIED:
        assert_receipt_consistent(receipt)  # raises

    if state == STATE_VERIFIED:
        # Belt and suspenders: verified prose is allowed, but re-confirm the evidence backs it.
        assert_receipt_consistent(receipt)
        return

    hits = _forbidden_hits(text if isinstance(text, str) else str(text))
    if hits:
        raise EmbeddingClaimError(
            "text asserts pixel identity ("
            + ", ".join(repr(h) for h in hits)
            + f") but the receipt does not prove it: state={state}"
            + (f" ({info['reason']})" if info["reason"] else "")
            + ". Reserve pixel-identity language for a completed decoded-pixel comparison; "
            "otherwise state the honest embedding status and its reason."
        )


# --------------------------------------------------------------------------- CLI ----------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1] if __doc__ else "")
    ap.add_argument("receipt", help="path to run_receipt.json")
    ap.add_argument("--check-text", metavar="FILE",
                    help="gate this text file against the receipt (exit 3 if it over-claims)")
    args = ap.parse_args(argv)

    try:
        derived = derive_embedding_statement(args.receipt)
    except EmbeddingClaimError as exc:
        print(f"RECEIPT INCONSISTENT: {exc}", file=sys.stderr)
        return 2
    print(f"state: {derived['state']}")
    print(f"statement: {derived['sentence']}")

    if args.check_text:
        text = pathlib.Path(args.check_text).read_text(encoding="utf-8")
        try:
            assert_embedding_prose_supported(text, args.receipt)
        except EmbeddingClaimError as exc:
            print(f"PROSE REJECTED: {exc}", file=sys.stderr)
            return 3
        print("prose OK: no unproven pixel-identity claim")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
