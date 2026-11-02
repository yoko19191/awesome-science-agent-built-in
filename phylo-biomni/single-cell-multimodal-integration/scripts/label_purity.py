#!/usr/bin/env python3
"""
label_purity.py — independent-label evidence model for integration label purity.

Label-purity ARI/NMI is only meaningful between the joint clusters and an INDEPENDENTLY provided
reference-label vector. Scoring clusters against a label that was derived from those same clusters
(e.g. a marker annotation mapped one-per-cluster) is circular and inflates the score. This module:

  1. treats the user-provided reference label as an immutable field (the caller must pass the label
     captured BEFORE any annotation join — this module never reads or writes the annotation into the
     reference);
  2. verifies cell-level alignment, join-key uniqueness, non-missing coverage, and independence, and
     returns a numeric ARI/NMI only when all hold — otherwise `not_evaluable_independent_labels`
     with a reason and NULL metrics;
  3. scores the marker-derived annotation against the reference as a SEPARATE
     annotation-concordance/confusion result that surfaces novel/spurious labels and split/merge,
     instead of hiding them in a scalar.

Pure functions, JSON-serializable output, dependency-light (numpy / pandas / sklearn.metrics), no
file I/O. The caller owns reading inputs and writing artifacts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

STATUS_SCHEMA = "s38-label-purity/1"
CONCORDANCE_SCHEMA = "s38-annotation-concordance/1"

# Case-insensitive tokens treated as "no label" so a blank/placeholder reference cannot masquerade as
# coverage. NaN is always missing.
_MISSING_TOKENS = {"", "na", "nan", "none", "null", "unknown", "unassigned", "n/a", "unlabeled",
                   "unlabelled", "undetermined"}


def _clean(values) -> pd.Series:
    """Coerce to a string Series with missing/placeholder values set to <NA>."""
    s = pd.Series(list(values), dtype="object").astype("string").str.strip()
    low = s.str.lower()
    return s.mask(s.isna() | low.isin(_MISSING_TOKENS))


def _is_bijection(a: pd.Series, b: pd.Series) -> bool:
    """True iff a and b induce the SAME partition through a 1:1 label correspondence.

    This detects a reference that is nothing but the cluster ids relabeled (a cluster alias): every
    a-label maps to exactly one b-label and vice-versa. A coarsening (one b maps several a's, e.g. an
    over-clustered cluster set vs. fewer true types) is NOT a bijection and is allowed through.
    """
    df = pd.DataFrame({"a": a.to_numpy(), "b": b.to_numpy()}).dropna()
    if df.empty:
        return False
    a_per_b = df.groupby("b")["a"].nunique()
    b_per_a = df.groupby("a")["b"].nunique()
    return bool((b_per_a == 1).all() and (a_per_b == 1).all() and a_per_b.size == b_per_a.size)


def _round(x) -> float | None:
    return None if x is None else round(float(x), 4)


def evaluate_label_purity(clusters, reference, *, annotation=None, join_index=None,
                          reference_source=None, min_coverage=0.5) -> dict:
    """Return the machine-readable label-purity metric state.

    Parameters
    ----------
    clusters, reference : array-like, positionally aligned (same length).
    annotation : optional array-like (same length) — the marker-derived annotation, used ONLY to
        detect the overwrite/circularity condition, never as the reference.
    join_index : optional list of join keys (barcodes), same length — used for uniqueness/alignment
        evidence.
    reference_source : optional dict recorded verbatim under label_source (field/origin/file/join_key).
    min_coverage : minimum non-missing reference fraction required to evaluate (default 0.5).

    The `metrics` are numbers ONLY when state == 'evaluable'; otherwise they are null and no label
    purity is claimed. `circular_metric_emitted` is the binding invariant: 1 iff a numeric metric was
    emitted without independence established (never true in correct behavior).
    """
    clusters_s = _clean(clusters)
    reference_s = _clean(reference)
    annotation_s = _clean(annotation) if annotation is not None else None
    n_cells = int(len(clusters_s))

    src = dict(reference_source or {})
    src.setdefault("join_key", "barcode")

    guards: list[str] = []
    coverage = {
        "n_cells": n_cells,
        "n_non_missing": int(reference_s.notna().sum()),
        "n_missing": int(reference_s.isna().sum()),
        "coverage_frac": (round(float(reference_s.notna().mean()), 4) if n_cells else 0.0),
        "min_coverage_required": float(min_coverage),
        "n_unique_join_keys": (int(pd.Series(list(join_index)).nunique()) if join_index is not None else None),
        "n_duplicate_join_keys": (int(len(join_index) - pd.Series(list(join_index)).nunique())
                                  if join_index is not None else 0),
    }

    def _state(state, reason, independence, metrics, emitted):
        return {
            "schema": STATUS_SCHEMA,
            "state": state,
            "reason": reason,
            "label_source": src,
            "independence": independence,
            "coverage": coverage,
            "metrics": {"label_purity_ARI": metrics[0], "label_purity_NMI": metrics[1]},
            "n_reference_labels": int(reference_s.dropna().nunique()),
            "n_clusters": int(clusters_s.dropna().nunique()),
            "circularity_guard_violations": len(guards),
            "circularity_guards_fired": list(guards),
            "emitted_metric": bool(emitted),
            "independence_established": bool(independence.get("established", False)),
            # binding invariant: a metric was emitted while independence was NOT established.
            "circular_metric_emitted": int(bool(emitted) and not independence.get("established", False)),
        }

    independence = {
        "captured_before_annotation": bool(src.get("captured_before_annotation", True)),
        "distinct_from_annotation_field": annotation is None or src.get("field") != src.get("annotation_field"),
        "value_identical_to_annotation": False,
        "value_identical_to_clusters": False,
        "bijection_with_clusters": False,
        "established": False,
    }

    # --- fail-closed structural gates (order: alignment -> join key -> coverage -> circularity) ---
    if not (len(reference_s) == n_cells and (join_index is None or len(join_index) == n_cells)):
        guards.append("misaligned")
        return _state("not_evaluable_independent_labels", "misaligned", independence, (None, None), False)

    if coverage["n_duplicate_join_keys"] and coverage["n_duplicate_join_keys"] > 0:
        guards.append("non_unique_join_key")
        return _state("not_evaluable_independent_labels", "non_unique_join_key", independence, (None, None), False)

    if coverage["n_non_missing"] == 0:
        guards.append("absent")
        return _state("not_evaluable_independent_labels", "absent", independence, (None, None), False)

    if coverage["coverage_frac"] < min_coverage:
        guards.append("insufficient_coverage")
        return _state("not_evaluable_independent_labels", "insufficient_coverage", independence, (None, None), False)

    # circularity: reference is (a copy of) the annotation, or a relabeled cluster alias
    both = reference_s.notna()
    if annotation_s is not None:
        pair_ok = reference_s.notna() & annotation_s.notna()
        if pair_ok.any() and bool((reference_s[pair_ok] == annotation_s[pair_ok]).all()):
            independence["value_identical_to_annotation"] = True
            guards.append("overwritten")
            return _state("not_evaluable_independent_labels", "overwritten", independence, (None, None), False)

    if bool((reference_s[both] == clusters_s[both]).all()):
        independence["value_identical_to_clusters"] = True
        guards.append("derived_from_clusters")
        return _state("not_evaluable_independent_labels", "derived_from_clusters", independence, (None, None), False)

    if _is_bijection(reference_s[both], clusters_s[both]):
        independence["bijection_with_clusters"] = True
        guards.append("derived_from_clusters")
        return _state("not_evaluable_independent_labels", "derived_from_clusters", independence, (None, None), False)

    # --- evaluable: score clusters vs the INDEPENDENT reference on the covered cells ---
    independence["established"] = True
    ref = reference_s[both].to_numpy()
    clu = clusters_s[both].to_numpy()
    ari = _round(adjusted_rand_score(ref, clu))
    nmi = _round(normalized_mutual_info_score(ref, clu))
    state = _state("evaluable", None, independence, (ari, nmi), True)
    state["n_scored"] = int(both.sum())
    return state


def _norm(label: str) -> str:
    """Normalize a label for name reconciliation: lowercase, alphanumerics only."""
    return "".join(ch for ch in str(label).lower() if ch.isalnum())


def _name_compatible(a: str, b: str) -> bool:
    """Same cell type up to naming convention: equal after normalization, or an abbreviation
    prefix of length >= 3 (so 'Mono' reconciles with 'Monocyte', 'CD4_T' with 'CD4 T', but 'NK'
    matches nothing it is not, and single letters like 'B' match only by exact equality)."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long = sorted((na, nb), key=len)
    return len(short) >= 3 and long.startswith(short)


def annotation_concordance(reference, annotation, *, clusters=None, join_index=None) -> dict:
    """Score the marker-derived annotation against the independent reference (a separate result).

    Surfaces the reference x annotation confusion, split/merge patterns, and annotation labels that
    do NOT reconcile to any reference type — the biology the v3 scalar hid (e.g. an out-of-panel
    'NK' call splitting true monocytes). Novelty is assessed structurally: an annotation label is
    'unreconciled' when its dominant reference type is not name-compatible with it, so a mere
    renaming ('Mono' -> 'Monocyte') is not miscalled novel while a genuinely absent type ('NK') is.
    """
    reference_s = _clean(reference)
    annotation_s = _clean(annotation)
    both = reference_s.notna() & annotation_s.notna()
    ref = reference_s[both]
    ann = annotation_s[both]

    out: dict = {
        "schema": CONCORDANCE_SCHEMA,
        "n_cells_scored": int(both.sum()),
        "reference_labels": sorted(ref.dropna().unique().tolist()),
        "annotation_labels": sorted(ann.dropna().unique().tolist()),
        "annotation_labels_absent_from_reference": sorted(
            {a for a in ann.unique() if not any(_name_compatible(a, r) for r in ref.unique())}),
        "unreconciled_annotation_labels": [],
        "missing_reference_labels": sorted(
            {r for r in ref.unique() if not any(_name_compatible(r, a) for a in ann.unique())}),
        "confusion_long": [],
        "splits": {},
        "merges": {},
        "concordance_frac": None,
    }
    if out["n_cells_scored"] == 0:
        return out

    ct = pd.crosstab(ref, ann)
    # unreconciled = annotation label whose DOMINANT reference type is not name-compatible with it
    unrec = []
    for c in ct.columns:
        dominant_ref = ct[c].idxmax()
        if not _name_compatible(c, dominant_ref):
            unrec.append({"annotation": str(c), "dominant_reference": str(dominant_ref),
                          "n_cells": int(ct[c].sum()),
                          "frac_from_dominant": round(int(ct.loc[dominant_ref, c]) / int(ct[c].sum()), 4)})
    out["unreconciled_annotation_labels"] = sorted(unrec, key=lambda d: d["annotation"])
    for r in ct.index:
        for c in ct.columns:
            n = int(ct.loc[r, c])
            if n:
                out["confusion_long"].append({"reference": str(r), "annotation": str(c), "n_cells": n})
    # split: one reference label distributed across >1 annotation label
    for r in ct.index:
        hit = ct.loc[r][ct.loc[r] > 0]
        if len(hit) > 1:
            out["splits"][str(r)] = {str(c): int(hit[c]) for c in hit.index}
    # merge: one annotation label absorbing >1 reference label
    for c in ct.columns:
        hit = ct[c][ct[c] > 0]
        if len(hit) > 1:
            out["merges"][str(c)] = {str(r): int(hit[r]) for r in hit.index}
    # best-match concordance: for each reference label, its most frequent annotation partner
    best = int(ct.max(axis=1).sum())
    out["concordance_frac"] = round(best / out["n_cells_scored"], 4)

    if clusters is not None:
        clusters_s = _clean(clusters)
        pc = pd.DataFrame({"cluster": clusters_s, "reference": reference_s, "annotation": annotation_s})
        rows = []
        for cl, g in pc.groupby("cluster"):
            ref_mode = g["reference"].dropna().mode()
            ann_mode = g["annotation"].dropna().mode()
            rows.append({
                "cluster": str(cl),
                "modal_reference": (str(ref_mode.iloc[0]) if len(ref_mode) else None),
                "annotation": (str(ann_mode.iloc[0]) if len(ann_mode) else None),
                "n_cells": int(len(g)),
            })
        out["per_cluster"] = rows
    return out
