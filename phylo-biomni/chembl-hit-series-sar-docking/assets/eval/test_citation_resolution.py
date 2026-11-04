#!/usr/bin/env python3
"""Regression suite for the single-source citation mechanism (scripts/render_citations.py) and its
gate (scripts/report_qc.assert_citations_resolve).

Each test guards a citation failure mode found (or directly implied) by the EGFR post-validation
review, where the bibliography was renumbered to 7 entries while inline citations kept session-global
references.jsonl indices ([8]/[9]/[16] resolved to nothing; [5]/[6] bound to the wrong papers) and
every blocking gate passed. Discrimination is built in: every test both (a) asserts the fixed
mechanism produces the RIGHT result and (b) asserts the gate REJECTS the pre-fix / broken output --
so a regression to the old behavior flips the test red.

Fixtures are generated in code with synthetic identities only -- no real paper, author, PMID, DOI,
molecule, target, or run number appears here, so the suite leaks nothing and pins no dataset.

Run:  python3 assets/eval/test_citation_resolution.py
Exit: 0 all passed | 1 a test failed | 2 everything skipped (a failure, not a pass).
"""

from __future__ import annotations

import pathlib
import sys

_PKG = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PKG / "scripts"))

try:
    from render_citations import render_citations, CitationError
    from report_qc import assert_citations_resolve, GateFailure
    _IMPORT_ERR = None
except Exception as exc:                    # a missing declared module is a failure, not a skip
    _IMPORT_ERR = exc


# --- fixtures (synthetic; validated before use) ---------------------------------------------------

def make_records(keys):
    """One synthetic record per input key, each with a unique, resolvable DOI identity."""
    recs = [{"key": str(k), "doi": f"10.5555/synthetic.{k}", "title": f"Synthetic study {k}",
             "year": 2000 + (k % 25), "authors": [f"Fabricated{k} Q", "Second R"]} for k in keys]
    # validity assertion: keys unique, identities unique -> the fixture tests the code, not itself
    assert len({r["key"] for r in recs}) == len(recs), "fixture keys not unique"
    assert len({r["doi"] for r in recs}) == len(recs), "fixture DOIs not unique"
    return recs


def assemble(rewritten_text: str, bibliography_text: str) -> str:
    return f"{rewritten_text}\n\nReferences\n{bibliography_text}\n"


def broken_16to7_report(result) -> str:
    """The pre-fix artifact: correct 7-entry bibliography, but inline text NEVER remapped (still
    carries the session-global indices). This is what shipped."""
    original_inline = "Target biology is established [1, 6, 8]. TKIs improved outcomes [2, 5, 9, 16]."
    return assemble(original_inline, result["bibliography_text"])


# --- tests ---------------------------------------------------------------------------------------

def test_observed_16_to_7():
    """The exact shipped failure: sparse session indices {1,2,5,6,8,9,16} over a 7-entry list."""
    recs = make_records([1, 2, 5, 6, 8, 9, 16])
    intro = "Target biology is established [1, 6, 8]. TKIs improved outcomes [2, 5, 9, 16]."
    r = render_citations(intro, recs)
    # RIGHT: no stale index survives; exactly 7 contiguous entries.
    for stale in ("[8]", "[9]", "[16]"):
        assert stale not in r["rewritten_text"], f"stale {stale} survived the remap"
    assert r["n_bibliography"] == 7
    assert assert_citations_resolve(assemble(r["rewritten_text"], r["bibliography_text"]), r,
                                    report_is_text=True)["identity_bound"]
    # WRONG (discrimination): the pre-fix report -- inline unremapped -- must be REJECTED.
    try:
        assert_citations_resolve(broken_16to7_report(r), r, report_is_text=True)
        raise AssertionError("gate accepted the 16->7 broken report")
    except GateFailure as e:
        assert "8, 9, 16" in str(e) or "[8, 9, 16]" in str(e), f"wrong failure reason: {e}"


def test_sparse_global_indices():
    """Non-contiguous input keys that do not overlap the final 1..N range."""
    recs = make_records([5, 12, 30])
    intro = "A [5]. B [12]. C [30]."
    r = render_citations(intro, recs)
    for stale in ("[5]", "[12]", "[30]"):
        assert stale not in r["rewritten_text"], f"{stale} not remapped"
    assert r["rewritten_text"].count("[1]") == 1 and "[2]" in r["rewritten_text"] and "[3]" in r["rewritten_text"]
    assert assert_citations_resolve(assemble(r["rewritten_text"], r["bibliography_text"]), r,
                                    report_is_text=True)
    # WRONG: sparse indices over a 3-entry bibliography must be rejected.
    try:
        assert_citations_resolve(assemble(intro, r["bibliography_text"]), r, report_is_text=True)
        raise AssertionError("gate accepted sparse indices")
    except GateFailure:
        pass


def test_repeated_references():
    """One source cited several times collapses to exactly one bibliography entry."""
    recs = make_records([7, 2, 9])
    intro = "First [7]. Again [7]. Third [2]. Once more [7]. And [9]."
    r = render_citations(intro, recs)
    assert r["n_bibliography"] == 3, f"repeats not collapsed: {r['n_bibliography']}"
    assert r["n_bibliography"] != 5                      # the wrong value (one entry per citation)
    assert r["rewritten_text"].count("[1]") == 3        # key 7 -> label 1, cited 3x
    assert assert_citations_resolve(assemble(r["rewritten_text"], r["bibliography_text"]), r,
                                    report_is_text=True)["n_bibliography"] == 3


def test_reordered_bibliography():
    """A bibliography whose entry order does not match the labels (relabelled by position)."""
    recs = make_records([4, 8, 15])
    intro = "One [4]. Two [8]. Three [15]."
    r = render_citations(intro, recs)
    assert assert_citations_resolve(assemble(r["rewritten_text"], r["bibliography_text"]), r,
                                    report_is_text=True)          # correct order passes
    # WRONG: reverse the entry TEXTS but keep labels 1..3 in place -> label k now carries k's wrong
    # identity token. The gate must catch the misbinding.
    lines = [e["citation"] for e in r["bibliography"]]
    reordered = "\n".join(f"[{i+1}] {lines[::-1][i]}" for i in range(len(lines)))
    try:
        assert_citations_resolve(assemble(r["rewritten_text"], reordered), r, report_is_text=True)
        raise AssertionError("gate accepted a reordered/misbound bibliography")
    except GateFailure as e:
        assert "wrong source" in str(e) or "misbound" in str(e).lower(), f"wrong reason: {e}"


def test_missing_labels():
    """A cited key with no record fails closed at render time; the gate rejects an unresolved [N]."""
    recs = make_records([1, 2, 3])
    try:
        render_citations("Cite [1] and [99].", recs)
        raise AssertionError("render accepted a citation with no record")
    except CitationError as e:
        assert e.code == "MISSING", f"wrong code: {e.code}"
    # gate side: a body citing [4] over a 3-entry bibliography.
    r = render_citations("A [1]. B [2]. C [3].", recs)
    try:
        assert_citations_resolve(assemble("A [1]. B [2]. C [3]. D [4].", r["bibliography_text"]), r,
                                 report_is_text=True)
        raise AssertionError("gate accepted an inline [4] with 3 entries")
    except GateFailure:
        pass


def test_wrong_identity_binding():
    """A bibliography line under label k carrying a different source's identity is rejected."""
    recs = make_records([6, 11, 20])
    intro = "X [6]. Y [11]. Z [20]."
    r = render_citations(intro, recs)
    assert assert_citations_resolve(assemble(r["rewritten_text"], r["bibliography_text"]), r,
                                    report_is_text=True)          # correct binding passes
    # WRONG: swap the identity tokens of labels 1 and 2 (keep the labels) -> both misbound.
    e0, e1 = r["bibliography"][0], r["bibliography"][1]
    swapped = (f"[1] {e1['citation']}\n[2] {e0['citation']}\n"
               f"[3] {r['bibliography'][2]['citation']}")
    try:
        assert_citations_resolve(assemble(r["rewritten_text"], swapped), r, report_is_text=True)
        raise AssertionError("gate accepted a wrong identity binding")
    except GateFailure as e:
        assert "wrong source" in str(e), f"wrong reason: {e}"


def test_correct_mapped_report():
    """Positive control: a fully consistent report must PASS (guards against a false-positive gate)."""
    recs = make_records([2, 9, 4, 40, 1])
    intro = ("Background [2, 9]. Method precedent [4]. Prior result [40]. Synthesis [1, 9]. "
             "Repeat of the first [2].")
    r = render_citations(intro, recs)
    assert r["n_bibliography"] == 5
    audit = assert_citations_resolve(assemble(r["rewritten_text"], r["bibliography_text"]), r,
                                     report_is_text=True)
    assert audit["n_bibliography"] == 5 and audit["n_inline_distinct"] == 5
    assert audit["labels_contiguous"] and audit["identity_bound"]


TESTS = [
    ("observed_16_to_7", test_observed_16_to_7),
    ("sparse_global_indices", test_sparse_global_indices),
    ("repeated_references", test_repeated_references),
    ("reordered_bibliography", test_reordered_bibliography),
    ("missing_labels", test_missing_labels),
    ("wrong_identity_binding", test_wrong_identity_binding),
    ("correct_mapped_report", test_correct_mapped_report),
]


def main() -> int:
    if _IMPORT_ERR is not None:
        print(f"SKIP-ALL: could not import the skill's own modules: {_IMPORT_ERR}")
        print("0/{n} passed ({n} skipped)".format(n=len(TESTS)))
        return 2
    passed, failed = 0, 0
    for name, fn in TESTS:
        try:
            fn()
            passed += 1
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
        except Exception as e:                                    # a crash is a failure
            failed += 1
            print(f"FAIL  {name}: unexpected {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed ({failed} failed, 0 skipped)")
    return 0 if failed == 0 and passed == len(TESTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
