#!/usr/bin/env python3
"""Compare session outputs with pipeline outputs. Reports facts, never a verdict.

  concordance.py SPEC.json --out-json report.json --out-md report.md

SPEC: {"comparisons": [{"type": "table"|"vcf"|"checksum"|"none", "label": ...,
       "session": PATH, "pipeline": PATH,
       "key": COL | "session_key": COL, "pipeline_key": COL,   (default: each table's first column)
       "columns": {SESSION_COL: PIPELINE_COL},                   (renamed columns)
       "rank_col": COL, "top_n": N, "sig_col": COL, "sig_threshold": X,   (session column names)
       "reason": TEXT}]}
Statuses: identical | differs | not_compared. A comparison that can't be made
(missing file, unknown column, unreadable table) is not_compared with the reason.
Checksums compare raw bytes (decompressed for .gz).
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path

_MISSING = {"", "na", "nan", "null", "none"}


def _bytes(path: Path) -> bytes:
    if path.name.endswith(".gz"):
        with gzip.open(path, "rb") as fh:
            return fh.read()
    return path.read_bytes()


def _text(path: Path) -> str:
    return _bytes(path).decode("utf-8-sig")


def _sha256(path: Path) -> str:
    return hashlib.sha256(_bytes(path)).hexdigest()


def _float(value: str) -> float | None:
    if value.strip().lower() in _MISSING:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return None if math.isnan(number) else number


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list, ys: list) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    rx, ry = _ranks([p[0] for p in pairs]), _ranks([p[1] for p in pairs])
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if vx == 0 or vy == 0:
        return None
    return round(cov / (vx * vy), 6)


def _read_table(path: Path) -> tuple[list[str], list[list[str]]]:
    name = path.name.removesuffix(".gz")
    delimiter = "," if name.endswith(".csv") else "\t"
    rows = [r for r in csv.reader(io.StringIO(_text(path)), delimiter=delimiter) if r]
    return rows[0], rows[1:]


def _cell(row: list[str], header: list[str], col: str) -> str:
    i = header.index(col)
    return row[i] if i < len(row) else ""


def _need(header: list[str], col: str, side: str) -> None:
    if col not in header:
        raise ValueError(f"column {col!r} is not in the {side} table (columns: {header})")


def compare_tables(session: Path, pipeline: Path, key: str | None = None, rank_col: str | None = None,
                   top_n: int = 100, sig_col: str | None = None, sig_threshold: float = 0.05,
                   session_key: str | None = None, pipeline_key: str | None = None,
                   columns: dict[str, str] | None = None) -> dict:
    hs, rs = _read_table(session)
    hp, rp = _read_table(pipeline)
    s_key = session_key if session_key is not None else (key if key is not None else hs[0])
    p_key = pipeline_key if pipeline_key is not None else (key if key is not None else hp[0])
    _need(hs, s_key, "session")
    _need(hp, p_key, "pipeline")
    mapped = {c: (columns or {}).get(c, c) for c in hs if c != s_key}  # session column -> pipeline column
    for col in (rank_col, sig_col):
        if col:
            _need(hs, col, "session")
            _need(hp, mapped.get(col, col), "pipeline")
    s_rows = {_cell(r, hs, s_key): r for r in rs}
    p_rows = {_cell(r, hp, p_key): r for r in rp}
    shared = sorted(set(s_rows) & set(p_rows))
    shared_cols = [c for c in mapped if mapped[c] in hp and mapped[c] != p_key]
    correlations = {}
    for col in shared_cols:
        xs = [_float(_cell(s_rows[k], hs, col)) for k in shared]
        ys = [_float(_cell(p_rows[k], hp, mapped[col])) for k in shared]
        if sum(x is not None for x in xs) >= 3 and sum(y is not None for y in ys) >= 3:
            correlations[col] = spearman(xs, ys)
    matched_pipeline = {p_key} | {mapped[c] for c in shared_cols}
    result = {
        "type": "table", "session": str(session), "pipeline": str(pipeline),
        "key": s_key if s_key == p_key else f"{s_key} / {p_key}", "session_key": s_key, "pipeline_key": p_key,
        "shape_session": [len(rs), len(hs)], "shape_pipeline": [len(rp), len(hp)],
        "keys_shared": len(shared), "keys_only_session": len(set(s_rows) - set(p_rows)),
        "keys_only_pipeline": len(set(p_rows) - set(s_rows)),
        "columns_only_session": [c for c in mapped if c not in shared_cols],
        "columns_only_pipeline": [c for c in hp if c not in matched_pipeline],
        "spearman": correlations,
    }
    if rank_col:
        def top(rows, header, col):
            scored = [(abs(v), k) for k, r in rows.items() if (v := _float(_cell(r, header, col))) is not None]
            return {k for _, k in sorted(scored, reverse=True)[:top_n]}
        ts, tp = top(s_rows, hs, rank_col), top(p_rows, hp, mapped.get(rank_col, rank_col))
        result["top_n"] = {"column": rank_col, "n": min(top_n, len(ts), len(tp)), "shared": len(ts & tp)}
    if sig_col:
        def sig(rows, header, col):
            return {k for k, r in rows.items()
                    if (v := _float(_cell(r, header, col))) is not None and v < sig_threshold}
        ss, sp = sig(s_rows, hs, sig_col), sig(p_rows, hp, mapped.get(sig_col, sig_col))
        result["significant"] = {"column": sig_col, "threshold": sig_threshold,
                                 "session": len(ss), "pipeline": len(sp), "shared": len(ss & sp)}
    result["status"] = "identical" if _sha256(session) == _sha256(pipeline) else "differs"
    return result


def _sites(path: Path) -> set[tuple[str, str, str, str]]:
    sites = set()
    for line in _text(path).splitlines():
        if line and not line.startswith("#"):
            f = line.split("\t")
            sites.add((f[0], f[1], f[3], f[4]))
    return sites


def compare_vcf(session: Path, pipeline: Path) -> dict:
    s, p = _sites(session), _sites(pipeline)
    return {"type": "vcf", "session": str(session), "pipeline": str(pipeline),
            "sites_shared": len(s & p), "sites_only_session": len(s - p), "sites_only_pipeline": len(p - s),
            "status": "identical" if s == p else "differs"}


def compare_checksum(session: Path, pipeline: Path) -> dict:
    same = _sha256(session) == _sha256(pipeline)
    return {"type": "checksum", "session": str(session), "pipeline": str(pipeline),
            "status": "identical" if same else "differs"}


_TABLE_OPTS = ("key", "session_key", "pipeline_key", "columns", "rank_col", "top_n", "sig_col", "sig_threshold")


def _compare(item: dict) -> dict:
    kind = item.get("type")
    if kind == "none":
        return {"type": "none", "session": item.get("session"), "status": "not_compared",
                "reason": item.get("reason", "")}
    if kind not in {"table", "vcf", "checksum"}:
        raise ValueError(f"unknown comparison type {kind!r} (use table, vcf, checksum or none)")
    session, pipeline = Path(item["session"]), Path(item["pipeline"])
    if kind == "table":
        return compare_tables(session, pipeline, **{k: item[k] for k in _TABLE_OPTS if k in item})
    if kind == "vcf":
        return compare_vcf(session, pipeline)
    return compare_checksum(session, pipeline)


def run(spec: dict) -> dict:
    results = []
    for item in spec["comparisons"]:
        try:
            result = _compare(item)
        except Exception as error:  # one bad item must not abort the whole report
            result = {"type": item.get("type"), "session": item.get("session"), "pipeline": item.get("pipeline"),
                      "status": "not_compared", "reason": f"could not compare: {type(error).__name__}: {error}"}
        result["label"] = item.get("label", Path(item.get("session") or "").name)
        results.append(result)
    return {"comparisons": results}


def _metrics(c: dict) -> list[str]:
    if c["status"] == "not_compared":
        return [f"reason: {c.get('reason', '')}"]
    if c["type"] == "table":
        lines = [f"rows × cols: session {c['shape_session']}, pipeline {c['shape_pipeline']}",
                 f"keys ({c['key']}): shared {c['keys_shared']}, only session {c['keys_only_session']}, "
                 f"only pipeline {c['keys_only_pipeline']}"]
        for col, rho in c["spearman"].items():
            lines.append(f"Spearman on {col}: {rho}")
        if "top_n" in c:
            t = c["top_n"]
            lines.append(f"top {t['n']} by |{t['column']}|: {t['shared']} shared")
        if "significant" in c:
            s = c["significant"]
            lines.append(f"{s['column']} < {s['threshold']}: session {s['session']}, pipeline {s['pipeline']}, "
                         f"shared {s['shared']}")
        return lines
    if c["type"] == "vcf":
        return [f"sites: shared {c['sites_shared']}, only session {c['sites_only_session']}, "
                f"only pipeline {c['sites_only_pipeline']}"]
    return []


def render_markdown(report: dict) -> str:
    out = ["# Concordance report", "",
           "This report compares files. It does not judge whether a difference matters "
           "scientifically; that call belongs to the reviewer.", ""]
    for status, title in (("identical", "Identical"), ("differs", "Differs"), ("not_compared", "Not compared")):
        items = [c for c in report["comparisons"] if c["status"] == status]
        if not items:
            continue
        out += [f"## {title}", ""]
        for c in items:
            out.append(f"- **{c['label']}** ({c['type']})")
            out += [f"  - {line}" for line in _metrics(c)]
        out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()
    report = run(json.loads(args.spec.read_text()))
    for out in (args.out_json, args.out_md):
        out.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2) + "\n")
    args.out_md.write_text(render_markdown(report))
    print(args.out_md.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
