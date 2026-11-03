#!/usr/bin/env python3
"""Derive and gate scATAC/Multiome report claims from current-run artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import re


class ReportGroundingError(ValueError):
    """Raised when report claims do not match current-run artifacts."""


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: pathlib.Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReportGroundingError(f"cannot load report evidence {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReportGroundingError(f"report evidence must be a JSON object: {path}")
    return value


def _count(path: pathlib.Path, field: str) -> int:
    value = _json(path).get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ReportGroundingError(f"{path.name} lacks positive integer {field}")
    return value


def _weights(path: pathlib.Path) -> dict[str, dict[str, float]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        raise ReportGroundingError(f"cannot load modality weights {path}: {exc}") from exc
    got: dict[str, dict[str, float]] = {}
    for row in rows:
        modality = str(row.get("modality", "")).strip().upper()
        if modality not in {"RNA", "ATAC"} or modality in got:
            continue
        try:
            mean = float(row["mean_weight"])
            median = float(row["median_weight"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ReportGroundingError(f"invalid {modality} modality weights") from exc
        if not (0.0 <= mean <= 1.0 and 0.0 <= median <= 1.0):
            raise ReportGroundingError(f"{modality} modality weights are outside [0,1]")
        got[modality] = {"mean": mean, "median": median}
    if set(got) != {"RNA", "ATAC"}:
        raise ReportGroundingError("modality_weights_summary.csv must contain RNA and ATAC rows")
    return got


def _plural(value: int, noun: str) -> str:
    return f"{value} {noun}" + ("" if value == 1 else "s")


def derive_report_grounding(results: pathlib.Path) -> dict:
    """Return the only supported weight and cluster statements for the run."""
    results = results.resolve(strict=True)
    weights_path = results / "modality_weights_summary.csv"
    initial_path = results / "atac_initial_cluster_summary.json"
    recalled_path = results / "atac_recalled_cluster_summary.json"
    joint_path = results / "joint_qc_scorecard.csv"
    weights = _weights(weights_path)
    initial = _count(initial_path, "n_initial_clusters")
    recalled = _count(recalled_path, "n_recalled_clusters")
    try:
        with joint_path.open(encoding="utf-8", newline="") as handle:
            joint_rows = list(csv.DictReader(handle))
        joint = int(joint_rows[0]["n_clusters"])
    except (OSError, IndexError, KeyError, TypeError, ValueError) as exc:
        raise ReportGroundingError("joint_qc_scorecard.csv lacks integer n_clusters") from exc
    if joint < 1:
        raise ReportGroundingError("joint cluster count must be positive")
    weight_sentence = (
        "Mean WNN modality weights were "
        f"RNA {weights['RNA']['mean']:.4f} and ATAC {weights['ATAC']['mean']:.4f}; "
        "median WNN modality weights were "
        f"RNA {weights['RNA']['median']:.4f} and ATAC {weights['ATAC']['median']:.4f}."
    )
    cluster_sentence = (
        "At the ATAC-only stages, initial clustering produced "
        f"{_plural(initial, 'cluster')} and recalled-peak clustering produced "
        f"{_plural(recalled, 'cluster')}; the later WNN integration produced "
        f"{_plural(joint, 'joint cluster')}."
    )
    return {
        "weights": weights,
        "clusters": {"initial_atac": initial, "recalled_atac": recalled, "joint_wnn": joint},
        "sentences": {"modality_weights": weight_sentence, "cluster_stages": cluster_sentence},
        "evidence": {
            path.name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
            for path in (weights_path, initial_path, recalled_path, joint_path)
        },
    }


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def _assert_interpretations_supported(text: str) -> None:
    """Reject comparative and stability claims not computed by this package."""

    def negated(sentence: str, claim_start: int) -> bool:
        prefix = sentence[max(0, claim_start - 72):claim_start]
        return re.search(
            r"\b(?:cannot|can't|does not|doesn't|do not|don't|not)\b[^,;]{0,64}$",
            prefix,
        ) is not None

    for sentence in re.split(r"[.!?]+", text):
        joint_subject = re.search(r"\b(?:wnn|joint (?:embedding|representation|graph)|weights?)\b", sentence)
        comparative = re.search(
            r"\b(?:preferred|superior|more informative|better than|outperform\w*)\b", sentence
        )
        unimodal_target = re.search(
            r"\b(?:single[- ]modality|unimodal|either modality|modality alone)\b", sentence
        )
        if (
            joint_subject
            and comparative
            and unimodal_target
            and not negated(sentence, comparative.start())
        ):
            raise ReportGroundingError(
                "report claims WNN superiority without an explicit unimodal comparator"
            )

        peak_recall = re.search(r"\b(?:peak recall\w*|recall(?:ed)?[- ]peak)\b", sentence)
        structure = re.search(r"\b(?:cluster\w*|structure)\b", sentence)
        stability = re.search(r"\b(?:stabl\w*|robust\w*|preserv\w*|conserv\w*)\b", sentence)
        if peak_recall and structure and stability and not negated(sentence, stability.start()):
            raise ReportGroundingError(
                "report claims peak-recall structural stability without a cellwise stability metric"
            )


def assert_report_grounding(report_text: str, results: pathlib.Path) -> dict:
    facts = derive_report_grounding(results)
    text = _normalized(report_text)
    _assert_interpretations_supported(text)
    for name, sentence in facts["sentences"].items():
        if _normalized(sentence) not in text:
            raise ReportGroundingError(f"report omits canonical {name} sentence")

    weights = facts["weights"]
    for modality in ("RNA", "ATAC"):
        mean = f"{weights[modality]['mean']:.4f}"
        median = f"{weights[modality]['median']:.4f}"
        if mean != median and re.search(
            rf"\bmean\w*\b[^.\n]{{0,140}}\b{re.escape(median)}\b", text
        ):
            raise ReportGroundingError(
                f"report labels the {modality} median {median} as a mean"
            )

    labels = {
        "initial": (
            r"\b(?:initial|pre-recall)\b",
            facts["clusters"]["initial_atac"],
        ),
        "recalled": (
            r"\b(?:recalled(?:-peak)?|re-clustering|reclustering)\b",
            facts["clusters"]["recalled_atac"],
        ),
    }
    number_words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    for sentence in re.split(r"[.!?]+", text):
        for label, (label_pattern, expected) in labels.items():
            if not re.search(label_pattern, sentence) or not re.search(
                r"\b(?:cluster|state|structure)", sentence
            ):
                continue
            candidates = [int(value) for value in re.findall(r"\bn\s*=\s*(\d+)\b", sentence)]
            candidates += [
                number_words[word]
                for word in re.findall(r"\b(one|two|three|four|five)\b", sentence)
            ]
            if candidates and expected not in candidates:
                raise ReportGroundingError(
                    f"report contradicts the {label} ATAC cluster count {expected}"
                )
    return facts
