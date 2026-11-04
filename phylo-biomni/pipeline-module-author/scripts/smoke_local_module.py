#!/usr/bin/env python3
"""Run a local module script on representative data and check cheap output invariants."""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path


def _table_error(path: Path) -> str | None:
    if path.stat().st_size == 0:
        return f"{path.name}: output is empty"
    if path.suffix.lower() not in {".tsv", ".csv", ".txt"}:
        return None
    delimiter = "," if path.suffix.lower() == ".csv" else "\t"
    with path.open(newline="") as handle:
        rows = list(csv.reader(handle, delimiter=delimiter))
    if len(rows) < 2 or not rows[0]:
        return f"{path.name}: tabular output has no data rows or header"
    width = len(rows[0])
    if any(len(row) != width for row in rows[1:]):
        return f"{path.name}: tabular rows have inconsistent column counts"
    ids = [row[0].strip() for row in rows[1:]]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        return f"{path.name}: first-column IDs must be non-empty and unique"
    for column in zip(*rows[1:]):
        if all(not value.strip() or value.strip().lower() in {"na", "nan", "null"} for value in column):
            return f"{path.name}: a column is entirely NA/empty"
    return None


def run_smoke(script: Path, command_args: list[str], extra_args: list[str], outputs: list[Path],
              timeout: float = 300, expected_rows: int | None = None) -> dict:
    try:
        shebang = script.read_text(errors="replace").splitlines()[:1]
        interpreter = shebang[0][2:].split() if shebang and shebang[0].startswith("#!") else [sys.executable]
        command = [str(script)] if os.access(script, os.X_OK) else [*interpreter, str(script)]
        completed = subprocess.run([*command, *command_args, *extra_args],
                                   capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "errors": [f"local module timed out after {timeout:g}s"]}
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        return {"ok": False, "errors": [f"local module exited {completed.returncode}: {detail[-1] if detail else 'no output'}"]}
    errors = []
    row_count = None
    for output in outputs:
        if not output.is_file() or output.stat().st_size == 0:
            errors.append(f"{output}: output is missing or empty")
            continue
        error = _table_error(output)
        if error:
            errors.append(error)
        elif output.suffix.lower() in {".tsv", ".csv", ".txt"}:
            delimiter = "," if output.suffix.lower() == ".csv" else "\t"
            with output.open(newline="") as handle:
                row_count = max(0, sum(1 for _ in csv.reader(handle, delimiter=delimiter)) - 1)
    if expected_rows is not None and row_count is not None and row_count != expected_rows:
        errors.append(f"{outputs[0].name}: expected {expected_rows} data rows, found {row_count}")
    return {"ok": not errors, "errors": errors, "outputs": [str(path) for path in outputs],
            "row_count": row_count}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--input", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--args-json", type=Path)
    parser.add_argument("--output", action="append", type=Path, default=[])
    parser.add_argument("--prefix", type=Path, default=Path("smoke"))
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--expected-rows", type=int)
    parser.add_argument("--module")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    values = {item.split("=", 1)[0]: item.split("=", 1)[1] for item in args.input}
    if args.args_json:
        values.update(json.loads(args.args_json.read_text()))
    command = ["--prefix", str(args.prefix)]
    for name, value in values.items():
        command.extend([f"--{name}", str(value)])
    result = run_smoke(args.script, command, [], args.output, args.timeout, args.expected_rows)
    if args.report:
        records = json.loads(args.report.read_text()) if args.report.is_file() else []
        records = [row for row in records if row.get("module") != args.module]
        records.append({"module": args.module or args.script.stem, **result})
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(records, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
