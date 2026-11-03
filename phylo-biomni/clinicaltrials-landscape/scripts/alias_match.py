"""Shared alias-aware text matcher for query-term evidence and config literal patterns.

One rule, used by the match-evidence export (``export_all.build_match_evidence``) and the optional
config-driven mechanism labels (``classify_study._config_mechanism``), so that a short acronym such
as ``CAR`` never counts as evidence inside an ordinary word such as ``Carcinoma``.

Semantics
---------
* Matching is case-insensitive.
* A *short* alias (only letters, at most ``SHORT_ALIAS_MAX_LETTERS`` of them) must be bounded on
  both sides. A boundary is the start or end of the text, any non-letter character (space, digit,
  hyphen, slash, ...), or a letter-case transition between the alias edge and its neighbour. So
  ``PSMA`` matches ``PSMAxCD3``, ``PSMAx4-1BB``, ``177Lu-PSMA-617``, ``LuPSMA`` and lowercase prose
  (``the psma ligand``), while ``CAR`` does not match ``Carcinoma`` or ``CARCINOMA``.
* A longer alias keeps plain case-insensitive substring semantics, exactly as before: registry
  identifiers are frequently glued to isotopes or suffixes (``68Ga-gozetotide``, ``folh1-positive``)
  and a longer alias inside another word is accepted (documented, e.g. ``Pluvicto`` inside
  ``prepluvictotherapy``).
* A multi-word alias (``PSMA CAR``, ``anti-PSMA CAR``) is matched as a whole, but each *edge*
  token that is itself a short letters-only alias must be bounded on its outer side, so
  ``PSMA CAR`` does not match ``PSMA carcinoma`` while ``PSMA CAR-T cells`` still matches.
  Inner tokens and non-short edge tokens (``anti-PSMA``, ``CAR-T``) keep substring semantics.
* Blank alias or blank/missing text never matches.
* Accepted recall cost (documented): an all-caps run glued to a short alias cannot be told apart from
  an ordinary word such as ``CARCINOMA``, so ``PSMA`` does not match inside ``PSMAI&T`` or ``PSMAPET``.
"""

from __future__ import annotations

import re

SHORT_ALIAS_MAX_LETTERS = 4


def is_short_alias(alias: str) -> bool:
    """Letters-only aliases of at most SHORT_ALIAS_MAX_LETTERS characters need boundaries."""
    return bool(alias) and alias.isalpha() and len(alias) <= SHORT_ALIAS_MAX_LETTERS


def _case_transition(outside: str, edge: str) -> bool:
    return (outside.islower() and edge.isupper()) or (outside.isupper() and edge.islower())


def _bounded(outside: str | None, edge: str) -> bool:
    if outside is None or not outside.isalpha():
        return True
    return _case_transition(outside, edge)


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value != value:  # NaN from a CSV cell
        return ""
    return str(value)


def _edge_boundaries(alias: str) -> tuple[bool, bool]:
    """Which sides of ``alias`` need a boundary: both for a short alias, else per short edge token."""
    if is_short_alias(alias):
        return True, True
    tokens = alias.split()
    if len(tokens) < 2:
        return False, False
    return is_short_alias(tokens[0]), is_short_alias(tokens[-1])


def alias_matches(alias, text) -> bool:
    """Return True when ``alias`` occurs in ``text`` under the alias-aware rule above."""
    alias = _as_text(alias).strip()
    text = _as_text(text)
    if not alias or not text:
        return False
    need_left, need_right = _edge_boundaries(alias)
    if not (need_left or need_right):
        return alias.lower() in text.lower()
    for match in re.finditer(re.escape(alias), text, re.IGNORECASE):
        start, end = match.span()
        left = not need_left or _bounded(text[start - 1] if start > 0 else None, text[start])
        right = not need_right or _bounded(text[end] if end < len(text) else None, text[end - 1])
        if left and right:
            return True
    return False


__all__ = ["SHORT_ALIAS_MAX_LETTERS", "alias_matches", "is_short_alias"]
