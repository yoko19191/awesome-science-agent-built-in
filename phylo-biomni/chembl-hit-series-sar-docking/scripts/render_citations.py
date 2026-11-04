#!/usr/bin/env python3
"""Single-source keyed citation rendering for the delegated PDF report.

WHY THIS EXISTS
---------------
An independent review of an EGFR run found the report's bibliography renumbered to 7 entries while
inline citations kept the session-global ``references.jsonl`` indices ([8], [9], [16] resolved to
nothing; [5]/[6] pointed to the wrong papers) -- and every blocking gate still passed, because no
gate checked citation resolution. The offending remap ("label_map") was improvised in a runtime
cell and *declared but never applied*: the bibliography was renumbered, the inline text was not.

The durable fix is not to correct one PDF's prose but to remove the failure mode. Citation
numbering becomes ONE keyed transformation with a single source of truth:

    select the cited records -> assign final bibliography labels ONCE -> rewrite EVERY inline
    citation through that exact map -> build the bibliography FROM the same map -> validate that
    every inline label resolves to exactly one bibliography entry bound to the intended record
    identity -> fail closed on missing / duplicate / stale / misbound labels.

Because the rewritten inline text and the bibliography are both derived from one ``label_map``,
they cannot disagree. The map is written to ``citation_map.json`` so the report gate
(``report_qc.assert_citations_resolve``) can re-validate the FINAL rendered artifact against an
expectation the run cannot silently rewrite afterwards.

Stdlib only. Deterministic (first-appearance ordering, insertion-ordered dicts, ``sorted`` where
order matters) so a fixed input yields byte-identical ``citation_map.json`` across runs. No network.

The transform is dataset- and ordering-agnostic: it hard-codes no target, molecule, paper, count,
or session index. Session-global indices are treated as arbitrary INPUT KEYS to be remapped, never
as final labels.

USAGE (library)
    from render_citations import render_citations, write_citation_map, CitationError
    result = render_citations(intro_text, records)      # records: see below
    intro_final   = result["rewritten_text"]            # hand THIS to pdf-report-generation
    references_txt = result["bibliography_text"]         # ... and THIS as the References block
    write_citation_map("citation_map.json", result)     # single source for the gate

USAGE (CLI)
    python render_citations.py --narrative intro.txt --records records.json \
        --out-text intro_final.txt --out-biblio references.txt --out-map citation_map.json

RECORD SHAPE (one dict per candidate source; only cited ones are kept)
    {
      "key":   "8",                     # REQUIRED. how the narrative refers to it (e.g. the
                                        #   session-global references.jsonl index). int or str.
      "doi":   "10.5555/example",       # identity, in priority order: doi -> pmid -> (author,year,title)
      "pmid":  "0000000",
      "title": "....", "year": 2021, "authors": ["Smith J", "Doe A"],  # or "first_author": "Smith J"
      "citation": "Smith J, et al. (2021). Title. Journal."   # optional pre-formatted biblio line;
                                        #   if absent one is composed from the fields above
    }
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

SCHEMA = "phylo-citation-map/1"

# A citation marker: a bracketed, comma-separated group of integers -- "[8]" or "[2, 5, 9, 16]".
# Ranges ("[1-3]") and non-numeric brackets are intentionally NOT treated as citations; see the
# module docstring's scope note. The negative lookbehind/ahead keep us off "[12a]"-style tokens.
_GROUP_RE = re.compile(r"\[\s*(\d+(?:\s*,\s*\d+)*)\s*\]")
_INT_RE = re.compile(r"\d+")
# Where a References/Bibliography section starts, for splitting a full report body.
_REFHEAD_RE = re.compile(r"(?im)^[\s#>*_-]*(references|bibliography|works cited)\b.*$")


class CitationError(Exception):
    """Raised when citations cannot be rendered to a single consistent, identity-bound map.

    Fail closed: a report must never be assembled from an unresolved or misbound citation set.
    ``.code`` is one of MISSING, DUPLICATE_IDENTITY, EMPTY_IDENTITY, STALE_LABEL, MISBOUND,
    NO_CITATIONS, DUPLICATE_KEY.
    """

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


# --- identity ------------------------------------------------------------------------------------

def _norm_key(k) -> str:
    return str(k).strip()


def _surname(author) -> str:
    """First author's surname from 'Smith J' / 'Smith, J' / 'J. Smith' style tokens."""
    a = str(author or "").strip()
    if not a:
        return ""
    if "," in a:                       # "Smith, J"
        return a.split(",")[0].strip()
    parts = a.split()
    if len(parts) == 1:
        return parts[0]
    # "Smith J" -> Smith ; "J. Smith" -> Smith
    return parts[0] if len(parts[0]) > 2 and not parts[0].endswith(".") else parts[-1]


def _first_author(rec: dict) -> str:
    if rec.get("first_author"):
        return _surname(rec["first_author"])
    authors = rec.get("authors")
    if isinstance(authors, str):
        authors = [a for a in re.split(r";|\band\b", authors) if a.strip()] or [authors]
    if isinstance(authors, (list, tuple)) and authors:
        return _surname(authors[0])
    return ""


def stable_identity(rec: dict) -> tuple[str, str]:
    """(identity, identity_token) for a record, in priority order doi -> pmid -> author|year|title.

    ``identity`` is the canonical value stored in the map. ``identity_token`` is the shortest
    discriminating string that MUST appear in the record's bibliography line, so the gate can bind a
    rendered entry back to the intended record even though a bibliography line is free text.

    Raises EMPTY_IDENTITY if none of doi/pmid/(author+year) can be resolved: a record we cannot
    identify cannot be bound, and a citation we cannot bind must not ship.
    """
    doi = str(rec.get("doi") or "").strip().lower()
    if doi:
        doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
        return f"doi:{doi}", doi
    pmid = str(rec.get("pmid") or "").strip()
    if pmid:
        pmid = re.sub(r"\D", "", pmid)
        if pmid:
            return f"pmid:{pmid}", pmid
    surname = _first_author(rec)
    year = str(rec.get("year") or "").strip()
    if surname and year:
        title = re.sub(r"\s+", " ", str(rec.get("title") or "").strip().lower())
        return f"auth:{surname.lower()}|{year}|{title}", f"{surname} ({year})"
    raise CitationError(
        "EMPTY_IDENTITY",
        f"record key {rec.get('key')!r} has no resolvable stable identity "
        "(need a DOI, a PMID, or first-author + year). Add one before rendering citations.",
    )


def _compose_citation(rec: dict, identity: str) -> str:
    """A minimal bibliography line if the record did not ship a pre-formatted 'citation'."""
    if str(rec.get("citation") or "").strip():
        return str(rec["citation"]).strip()
    bits = []
    fa = rec.get("first_author") or (rec.get("authors") or [""])[0]
    if fa:
        bits.append(f"{_surname(fa)}{', et al.' if rec.get('authors') and len(rec['authors']) > 1 else ''}")
    if rec.get("year"):
        bits.append(f"({rec['year']})")
    if rec.get("title"):
        bits.append(str(rec["title"]).rstrip(".") + ".")
    if rec.get("journal"):
        bits.append(str(rec["journal"]).rstrip(".") + ".")
    if identity.startswith("doi:"):
        bits.append(f"doi:{identity[4:]}")
    elif identity.startswith("pmid:"):
        bits.append(f"PMID:{identity[5:]}")
    line = " ".join(b for b in bits if b).strip()
    return line or f"[unlabelled source, key {rec.get('key')}]"


# --- parsing -------------------------------------------------------------------------------------

def _split_body_refs(text: str) -> tuple[str, str]:
    """Split a full report into (body, references_block). If no heading, refs is ''."""
    m = _REFHEAD_RE.search(text)
    if not m:
        return text, ""
    return text[: m.start()], text[m.end():]


def parse_inline_keys(text: str) -> list[str]:
    """Ordered, de-duplicated list of citation keys by first appearance in ``text``."""
    seen: dict[str, None] = {}
    for g in _GROUP_RE.finditer(text):
        for tok in _INT_RE.findall(g.group(1)):
            seen.setdefault(tok, None)
    return list(seen)


def parse_inline_labels(text: str) -> list[int]:
    """Every integer inside citation groups (with repeats), as ints -- for gate validation."""
    out: list[int] = []
    for g in _GROUP_RE.finditer(text):
        out.extend(int(t) for t in _INT_RE.findall(g.group(1)))
    return out


# --- the transform -------------------------------------------------------------------------------

def render_citations(narrative_text: str, records, *, order: str = "first_appearance") -> dict:
    """Render inline citations + bibliography from ONE label map. See module docstring.

    Returns a dict with: rewritten_text, bibliography (list of entries), bibliography_text,
    label_map (input key -> final label), identity_by_label, token_by_label, and audit counts.
    Raises CitationError, failing closed, on any unresolved or ambiguous state.
    """
    if order != "first_appearance":
        raise CitationError("STALE_LABEL", f"unsupported order {order!r}; only 'first_appearance'")

    # Index records by input key; a duplicate key makes the map ambiguous.
    rec_by_key: dict[str, dict] = {}
    for rec in records:
        if "key" not in rec:
            raise CitationError("MISSING", f"record has no 'key' field: {rec!r}")
        k = _norm_key(rec["key"])
        if k in rec_by_key:
            raise CitationError("DUPLICATE_KEY", f"two records share input key {k!r}")
        rec_by_key[k] = rec

    cited_keys = parse_inline_keys(narrative_text)
    if not cited_keys:
        raise CitationError(
            "NO_CITATIONS",
            "no inline [N] citations found in the narrative. A literature-grounded introduction "
            "must carry inline citations; do not assemble a References section with no in-text anchors.",
        )

    # Select cited records; a cited key with no record is a broken citation.
    missing = [k for k in cited_keys if k not in rec_by_key]
    if missing:
        raise CitationError(
            "MISSING",
            f"inline citation(s) {missing} have no matching source record. Every cited key must "
            "map to a record in references.jsonl (or the supplied records).",
        )

    # Resolve identities up front; reject empty or colliding identities before assigning labels.
    identity_by_key: dict[str, str] = {}
    token_by_key: dict[str, str] = {}
    seen_identity: dict[str, str] = {}
    for k in cited_keys:
        ident, token = stable_identity(rec_by_key[k])
        if ident in seen_identity and seen_identity[ident] != k:
            raise CitationError(
                "DUPLICATE_IDENTITY",
                f"input keys {seen_identity[ident]!r} and {k!r} resolve to the same source "
                f"identity ({ident}). Deduplicate the reference list before rendering.",
            )
        seen_identity[ident] = k
        identity_by_key[k] = ident
        token_by_key[k] = token

    # Assign final labels ONCE, first-appearance order. This is the single label_map.
    label_map: dict[str, int] = {k: i + 1 for i, k in enumerate(cited_keys)}
    n = len(cited_keys)

    # Rewrite every inline group through the map: dedupe within a group, sort by final label.
    def _sub(m: re.Match) -> str:
        labels = sorted({label_map[t] for t in _INT_RE.findall(m.group(1))})
        return "[" + ", ".join(str(x) for x in labels) + "]"

    rewritten_text = _GROUP_RE.sub(_sub, narrative_text)

    # Build the bibliography FROM the same map, in label order.
    bibliography = []
    for k in cited_keys:
        label = label_map[k]
        rec = rec_by_key[k]
        bibliography.append({
            "label": label,
            "input_key": k,
            "identity": identity_by_key[k],
            "identity_token": token_by_key[k],
            "citation": _compose_citation(rec, identity_by_key[k]),
        })
    bibliography.sort(key=lambda e: e["label"])
    bibliography_text = "\n".join(f"[{e['label']}] {e['citation']}" for e in bibliography)

    # --- self-check: fail closed if the rewrite did not converge to a resolvable 1..N map --------
    out_labels = parse_inline_labels(rewritten_text)
    label_set = set(out_labels)
    biblio_labels = [e["label"] for e in bibliography]
    if sorted(set(biblio_labels)) != list(range(1, n + 1)):
        raise CitationError("STALE_LABEL", f"bibliography labels are not contiguous 1..{n}: {biblio_labels}")
    if len(set(biblio_labels)) != len(biblio_labels):
        raise CitationError("MISBOUND", "a bibliography label is used more than once")
    stale = sorted(x for x in label_set if x < 1 or x > n)
    if stale:
        raise CitationError(
            "STALE_LABEL",
            f"after rewrite, inline label(s) {stale} fall outside 1..{n} -- a session-global index "
            "survived the remap (this is the 16->7 failure). The rewrite did not converge.",
        )
    unresolved = sorted(x for x in label_set if x not in set(biblio_labels))
    if unresolved:
        raise CitationError("STALE_LABEL", f"inline label(s) {unresolved} have no bibliography entry")

    return {
        "schema": SCHEMA,
        "order": order,
        "rewritten_text": rewritten_text,
        "bibliography": bibliography,
        "bibliography_text": bibliography_text,
        "label_map": label_map,
        "identity_by_label": {str(e["label"]): e["identity"] for e in bibliography},
        "token_by_label": {str(e["label"]): e["identity_token"] for e in bibliography},
        "n_cited": n,
        "n_bibliography": len(bibliography),
        "input_keys_first_appearance": cited_keys,
    }


def write_citation_map(path, result: dict):
    """Persist the single-source map/audit the gate re-validates against. Deterministic bytes."""
    payload = {
        "schema": result["schema"],
        "order": result["order"],
        "n_cited": result["n_cited"],
        "n_bibliography": result["n_bibliography"],
        "label_map": result["label_map"],
        "identity_by_label": result["identity_by_label"],
        "token_by_label": result["token_by_label"],
        "bibliography": result["bibliography"],
    }
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return p


# --- CLI -----------------------------------------------------------------------------------------

def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render inline citations + bibliography from one label map.")
    ap.add_argument("--narrative", required=True, help="text file with the narrative + inline [N] markers")
    ap.add_argument("--records", required=True, help="JSON list of source records (see module docstring)")
    ap.add_argument("--out-text", required=True, help="write the rewritten narrative here")
    ap.add_argument("--out-biblio", required=True, help="write the References block here")
    ap.add_argument("--out-map", required=True, help="write citation_map.json here")
    args = ap.parse_args(argv)

    narrative = pathlib.Path(args.narrative).read_text(encoding="utf-8")
    records = json.loads(pathlib.Path(args.records).read_text(encoding="utf-8"))
    try:
        result = render_citations(narrative, records)
    except CitationError as exc:
        print(f"CITATION RENDER FAILED {exc}", file=sys.stderr)
        return 2
    pathlib.Path(args.out_text).write_text(result["rewritten_text"], encoding="utf-8")
    pathlib.Path(args.out_biblio).write_text(result["bibliography_text"] + "\n", encoding="utf-8")
    write_citation_map(args.out_map, result)
    print(f"OK: {result['n_cited']} cited -> {result['n_bibliography']} bibliography entries, "
          f"labels 1..{result['n_bibliography']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
