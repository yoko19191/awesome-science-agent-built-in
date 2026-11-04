"""Markdown and self-contained HTML reports; they only render numbers already in the summary and tables."""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pandas as pd
from rdkit import Chem
from rdkit.Chem import rdDepictor
from rdkit.Chem.Draw import rdMolDraw2D

TEMPLATE = pathlib.Path(__file__).resolve().parent.parent / "templates" / "filters_report.html"
DATA_MARKER = "__REPORT_DATA__"
ROWS_EMBEDDED = 2000
ROWS_DRAWN = 300
SCORE_COLUMNS = ("calc_qed", "nibr_severity", "lilly_demerits")
HISTOGRAM_BINS = 20


def qc_lines(qc: dict | None) -> list[str]:
    """Rows removed or quarantined by molecular-dataset-qc before filtering, apart from filter failures."""
    if qc is None:
        return []
    set_aside = {k: v for k, v in qc["row_partition"].items() if k != "retained" and v}
    detail = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in set_aside.items()) or "none"
    steps = ", ".join(qc["steps"])
    retained = qc["row_partition"]["retained"]
    return [
        (
            f"- Cleaned beforehand by molecular-dataset-qc ({steps}; status {qc['status']}): "
            f"{qc['n_records']} raw records, {retained} retained. "
            f"Set aside by QC, not in this table: {detail} (see `qc_details/removed_data.csv`)."
        )
    ]


def panel_lines(panel: dict | None) -> list[str]:
    """Agreement across the expert rule sets: how many molecules each number of sets flags."""
    if panel is None:
        return []
    return [
        "",
        "## Agreement across expert rule sets",
        "",
        (
            "Rule sets are expert opinion and often disagree; their lists also overlap, so "
            "agreement is a heuristic, not independent corroboration. Voting sets "
            f"({len(panel['sets'])}): {', '.join(panel['sets'])}. Consensus mode rejects molecules "
            f"flagged by at least {panel['min_agree']} of them ({panel['n_consensus_fail']} here)."
        ),
        "",
        "| Sets flagging | Parsable molecules |",
        "| ---: | ---: |",
        *[f"| {votes} | {count} |" for votes, count in panel["vote_distribution"].items()],
    ]


def threshold_line(summary: dict) -> str:
    cutoff = summary["thresholds"]["lilly_engine_cutoff"]
    lilly_scores = (
        f"engine cutoff {cutoff}: demerits are exact below it, and a rule that reaches it is a "
        "reject scored at the cutoff"
    )
    if not summary["pass_fail"]:
        return f"- Lilly demerits: {lilly_scores}. Nothing is rejected."
    return (
        f"- Thresholds: NIBR severity >= {summary['thresholds']['nibr_reject_at']} rejects; "
        f"Lilly rejects hard rejects and demerits >= {summary['thresholds']['lilly_reject_at']} "
        f"({lilly_scores})"
    )


def filter_table(summary: dict) -> list[str]:
    if not summary["pass_fail"]:
        return [
            "| Filter | Molecules flagged |",
            "| --- | ---: |",
            *[f"| {f['token']} | {f['n_hit']} |" for f in summary["filters"]],
        ]
    return [
        "| Filter | Role | Molecules flagged | Failed because of it |",
        "| --- | --- | ---: | ---: |",
        *[
            f"| {f['token']} | {f['role']} | {f['n_hit']} | {f['n_fail_reason']} |"
            for f in summary["filters"]
        ],
    ]


def legend_lines(summary: dict) -> list[str]:
    if not summary["pass_fail"]:
        return [
            (
                "Nothing is rejected: every molecule keeps its scores and matched alerts "
                "(columns `hit_details`, `n_filters_hit`)."
            ),
            "Alerts are prompts for chemist review, not verdicts.",
            "",
            "Files: `annotated_all.csv`, `summary.json`, `report.html`.",
        ]
    return [
        "Roles: `reject` filters decide pass/fail; `flag` filters only annotate (column `flags`);",
        "`vote` filters count toward the consensus (column `n_expert_sets_hit`) but do not reject alone.",
        "Alerts are prompts for chemist review, not verdicts.",
        "",
        (
            "Files: `filtered_pass.csv`, `filtered_fail.csv`, `annotated_all.csv`, "
            "`summary.json`, `report.html`."
        ),
    ]


def write_markdown(summary: dict, path: pathlib.Path) -> None:
    lines = [
        "# Med-chem filter report",
        "",
        f"- Input: `{summary['input']}` ({summary['n_input']} rows, {summary['n_unparsable']} unparsable)",
        f"- Mode: **{summary['mode']}**",
        *([f"- Expected chemistry: {summary['context']}"] if summary["context"] else []),
        *(
            [f"- Passed: **{summary['n_pass']}**, failed: **{summary['n_fail']}**"]
            if summary["pass_fail"]
            else []
        ),
        threshold_line(summary),
        *qc_lines(summary["qc"]),
        *panel_lines(summary["expert_panel"]),
        "",
        "## Filters",
        "",
        *filter_table(summary),
        "",
        "## Molecules flagged per group",
        "",
        *[f"- {group}: {count}" for group, count in summary["groups"].items()],
        "",
        *legend_lines(summary),
        "",
        "Versions: " + ", ".join(f"{k} {v}" for k, v in summary["versions"].items()),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _svg(mol) -> str:
    rdDepictor.SetPreferCoordGen(True)
    drawer = rdMolDraw2D.MolDraw2DSVG(220, 160)
    drawer.drawOptions().clearBackground = False
    drawer.DrawMolecule(mol)
    drawer.FinishDrawing()
    text = drawer.GetDrawingText()
    return text[text.index("<svg") :]


def _number(value: object) -> float | None:
    return None if pd.isna(value) else float(value)


def _row(position: int, row: pd.Series, id_col: str | None) -> dict:
    mol = row["mol"]
    return {
        "input_row": int(row["input_row"]),
        "id": None if id_col is None else str(row[id_col]),
        "smiles": "" if mol is None else Chem.MolToSmiles(mol),
        "pass": bool(row["pass_filters"]),
        "votes": int(row["n_expert_sets_hit"])
        if "n_expert_sets_hit" in row and mol is not None
        else None,
        "hit_details": row["hit_details"],
        "fail_reasons": row["fail_reasons"],
        "n_hit": int(row["n_filters_hit"]),
        "scores": {c: _number(row[c]) for c in SCORE_COLUMNS if c in row},
        "svg": _svg(mol) if position < ROWS_DRAWN and mol is not None else None,
    }


def _histogram(values: pd.Series) -> dict:
    finite = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    counts, edges = np.histogram(finite, bins=HISTOGRAM_BINS)
    return {"n": len(finite), "counts": counts.tolist(), "edges": edges.tolist()}


def write_html(summary: dict, df: pd.DataFrame, path: pathlib.Path) -> None:
    shown = df.head(ROWS_EMBEDDED)
    payload = {
        "summary": summary,
        "rows": [_row(i, row, summary["id_col"]) for i, (_, row) in enumerate(shown.iterrows())],
        "histograms": {c: _histogram(df[c]) for c in SCORE_COLUMNS if c in df},
        "limits": {"rows_embedded": ROWS_EMBEDDED, "rows_drawn": ROWS_DRAWN},
    }
    data = json.dumps(payload, allow_nan=False).replace("</", "<\\/")
    path.write_text(
        TEMPLATE.read_text(encoding="utf-8").replace(DATA_MARKER, data), encoding="utf-8"
    )
