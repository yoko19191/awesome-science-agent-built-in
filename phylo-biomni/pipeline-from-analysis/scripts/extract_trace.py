#!/usr/bin/env python3
"""Turn Biomni execution-trace notebooks into ordered cells with file lineage.

  extract_trace.py /mnt/results/execution_trace/*.ipynb [--transcript PATH]
      [--target /mnt/results/x.tsv ...] --out steps.json

Keeps only successful cells that contributed to the target files (default: every
file or folder written under /mnt/results/, outside execution_trace/, that no later
cell reads). Cells are linked by file paths (a folder links to the files under it)
and, for python and R cells, by variables assigned in one cell and used in a later
one. Names a kept cell uses that no earlier cell assigns are listed in
unresolved_names. The I/O, error and variable detection are heuristics; review
steps.json before planning.
"""
from __future__ import annotations

import argparse
import builtins
import json
import keyword
import re
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path

ERROR_MARKERS = ("Traceback (most recent call last)", "Error in ", "Execution halted",
                 "command not found", "No such file or directory", "Segmentation fault")
_ERROR_LINE_RE = re.compile(r"^\w*(?:Error|Exception):", re.M)
_EXT = (r"(?:fastq\.gz|fq\.gz|vcf\.gz|fa\.gz|fasta\.gz|tsv\.gz|csv\.gz|fastq|fq|bam|sam|cram|bai|vcf|bcf|bed|"
        r"gtf|gff3|gff|fasta|fa|fna|csv|tsv|txt|json|h5ad|h5|rds|RData|mtx|parquet|xlsx|pdf|png|svg|html|zip)")
# A /mnt path, or a relative file name with a known extension. `(?![\w(])` stops
# function names such as `write.csv(` from being read as file names.
_PATH_RE = re.compile(r"(?<![\w/.-])(?P<quote>['\"]?)(?P<path>/mnt/[^\s'\"`),;\]]+|(?:[\w.-]+/)*[\w.-]+\."
                      + _EXT + r")(?![\w(])")
_WRITE_HINT_RE = re.compile(
    r"(>>?|(?<![\w-])-o(?![\w-])|(?<![\w-])-O(?![\w-])|--out\w*|--output\w*|\.to_\w+\(|"
    r"write\.\w+|write_\w+|writeLines|savefig|saveRDS|save\(|ggsave|pdf\(|png\(|sink\(|\.write|"
    r"open\([^)]*['\"][wax]b?['\"])")
_OPEN_BEFORE_RE = re.compile(r"\bopen\(\s*$")
_OPEN_MODE_AFTER_RE = re.compile(r"^\s*,\s*(?:mode\s*=\s*)?['\"][wax]")
_SHELL_LINE_RE = re.compile(r"^\s*!|\bsystem2?\(|\bsubprocess\.|\bos\.system\(")
_PY_CMD_RE = re.compile(r"\b(?:os\.system|subprocess\.\w+)\((.*)\)")
_R_CMD_RE = re.compile(r"\bsystem2?\((.*)\)")

# `name = "/mnt/..."` (python / R) and `NAME=/mnt/...` (bash)
_PATH_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*(?:=|<-)\s*(['\"])(/mnt/[^'\"]+)\2\s*;?\s*(?:#.*)?$")
_BASH_PATH_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_]\w*)=(['\"]?)(/mnt/[^\s'\"]+)\2\s*$")

# Simple def-use between python / R cells (not dataflow analysis).
_STRIP_RE = re.compile(r"'''.*?'''|\"\"\".*?\"\"\"|'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"|#[^\n]*", re.S)
_ASSIGN_RE = {"python": re.compile(r"^\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*(?:[-+*/]?=)(?!=)"),
              "r": re.compile(r"^\s*([A-Za-z.][\w.]*)\s*(?:<<-|<-|=(?!=))")}
_R_RIGHT_ASSIGN_RE = re.compile(r"->\s*([A-Za-z.][\w.]*)")
_IDENT_RE = {"python": re.compile(r"(?<![\w.])[A-Za-z_]\w*"), "r": re.compile(r"(?<![\w.$@])[A-Za-z.][\w.]*")}
_LOCAL_BINDERS = {
    "python": [re.compile(r"\bfor\s+([\w\s,()]+?)\s+in\b"), re.compile(r"\bas\s+(\w+)"),
               re.compile(r"\bdef\s+(\w+)\s*\(([^)]*)\)"), re.compile(r"\blambda\s+([^:]*):")],
    "r": [re.compile(r"\bfor\s*\(\s*([A-Za-z.][\w.]*)\s+in\b"), re.compile(r"\bfunction\s*\(([^)]*)\)")],
}
# Imports are environment, not variables: they never link cells and are never unresolved.
_IMPORTS = {
    "python": [re.compile(r"^\s*import\s+(.+)$", re.M), re.compile(r"^\s*from\s+[\w.]+\s+import\s+\(?([^)\n]+)", re.M)],
    "r": [re.compile(r"\b(?:library|require|requireNamespace)\s*\(\s*['\"]?([A-Za-z.][\w.]*)")],
}
_KNOWN = {
    "python": set(keyword.kwlist) | set(getattr(keyword, "softkwlist", [])) | set(dir(builtins)) | {"self", "cls"},
    "r": {"TRUE", "FALSE", "T", "F", "NULL", "NA", "NaN", "Inf", "NA_integer_", "NA_real_", "NA_character_",
          "if", "else", "for", "in", "while", "repeat", "function", "return", "next", "break", "pi",
          "LETTERS", "letters", "month.name", "month.abb"},
}
_ROOTS = {"/mnt", "/mnt/results", "/mnt/user-uploads", "/mnt/user-results", "/mnt/project-shared"}


def _cell_timestamp(raw: dict) -> str | None:
    metadata = raw.get("metadata") or {}
    execution = metadata.get("execution") or {}
    return raw.get("timestamp") or execution.get("timestamp") or metadata.get("timestamp")


@dataclass
class Cell:
    index: int
    cell_id: str
    language: str
    source: str
    output: str
    ok: bool
    error_markers: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    uses: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    source_type: str = "notebook"
    timestamp: str | None = None


def _join(value) -> str:
    return "".join(value) if isinstance(value, list) else (value or "")


def _commands(language: str, source: str) -> list[str]:
    if language == "bash":
        text = re.sub(r"\\\n\s*", " ", source)
        return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    cmds = [m.group(1).strip() for m in re.finditer(r"^\s*!(.+)$", source, re.M)]
    pattern = _R_CMD_RE if language == "r" else _PY_CMD_RE
    return cmds + [m.group(1).strip() for m in pattern.finditer(source)]


def _substitute(line: str, language: str, path_vars: dict[str, str]) -> str:
    for name, path in path_vars.items():
        if language == "bash":
            line = re.sub(r"\$\{?" + re.escape(name) + r"\}?(?!\w)", lambda _m, p=path: p, line)
        else:
            line = re.sub(r"(?<![\w.$'\"])" + re.escape(name) + r"(?![\w.(])(?!\s*(?:=[^=]|<-))",
                          lambda _m, p=path: f"'{p}'", line)
    return line


def _io(source: str, language: str = "bash", path_vars: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
    """Files a cell reads and writes. `path_vars` maps variables to /mnt paths and is updated in place."""
    path_vars = {} if path_vars is None else path_vars
    reads, writes = [], []
    for line in re.sub(r"\\\n\s*", " ", source).splitlines():
        assign = (_BASH_PATH_ASSIGN_RE if language == "bash" else _PATH_ASSIGN_RE).match(line)
        if assign:
            path_vars[assign.group(1)] = assign.group(3)
            continue
        line = _substitute(line, language, path_vars)
        shell = language == "bash" or bool(_SHELL_LINE_RE.search(line))
        last = 0
        for m in _PATH_RE.finditer(line):
            path = m.group("path")
            if "/" not in path and not m.group("quote") and not shell:
                continue  # a bare word in python / R code is not a file name
            before = line[last: m.start()]
            is_write = bool(_WRITE_HINT_RE.search(before)) or (
                bool(_OPEN_BEFORE_RE.search(before))
                and bool(_OPEN_MODE_AFTER_RE.match(line[m.end():].removeprefix(m.group("quote")))))
            target = writes if is_write else reads
            if path not in target:
                target.append(path)
            last = m.end()
    return reads, writes


def _names(text: str, language: str) -> list[str]:
    return [n for n in re.findall(r"[A-Za-z_.][\w.]*", text)
            if not n[0].isdigit() and n not in {"as", "import"} and (language == "r" or "." not in n)]


def _imports(source: str, language: str) -> list[str]:
    names = []
    for pattern in _IMPORTS.get(language, []):
        for m in pattern.finditer(source):
            for part in m.group(1).split(","):
                words = part.split()
                if words:
                    name = words[-1] if "as" in words else words[0]
                    names.append(name.split(".")[0] if language == "python" else name)
    return names


def _def_use(source: str, language: str) -> tuple[list[str], list[str]]:
    """Names a python / R cell assigns, and names it uses before assigning them."""
    if language not in _ASSIGN_RE:
        return [], []
    defines: list[str] = []
    uses: list[str] = []
    local: set[str] = set(_imports(source, language))
    for line in _STRIP_RE.sub(" ", source).splitlines():
        if language == "python" and re.match(r"\s*(?:import|from)\s", line):
            continue
        assigned, rhs = [], line
        m = _ASSIGN_RE[language].match(line)
        if m:
            assigned = [n.strip() for n in m.group(1).split(",")]
            rhs = line[m.end():]
        if language == "r":
            assigned += _R_RIGHT_ASSIGN_RE.findall(line)
        for binder in _LOCAL_BINDERS[language]:
            for bm in binder.finditer(line):
                for group in bm.groups():
                    local.update(_names(group or "", language))
        for im in _IDENT_RE[language].finditer(rhs):
            name, after = im.group(0), rhs[im.end():]
            if (name in local or name in _KNOWN[language] or name in uses
                    or re.match(r"\s*(?:\(|=(?!=)|::)", after) or re.search(r"~\s*$", rhs[:im.start()])):
                continue
            uses.append(name)
        for name in assigned:
            local.add(name)
            if name not in defines:
                defines.append(name)
    return defines, uses


def load_cells(path: Path) -> list[Cell]:
    nb = json.loads(Path(path).read_text())
    fallback = ((nb.get("metadata") or {}).get("phylo") or {}).get("legacy_language_fallback") or "python"
    cells = []
    path_vars: dict[str, dict[str, str]] = {"python": {}, "r": {}}
    for i, raw in enumerate(nb.get("cells") or []):
        if raw.get("cell_type") != "code":
            continue
        language = (((raw.get("metadata") or {}).get("phylo") or {}).get("language") or fallback).lower()
        source = _join(raw.get("source"))
        output = "".join(_join(o.get("text")) for o in raw.get("outputs") or [])
        markers = [m for m in ERROR_MARKERS if m in output]
        if _ERROR_LINE_RE.search(output):
            markers.append("ErrorLine")
        # Each bash cell is its own shell; python and R variables persist between cells.
        reads, writes = _io(source, language, path_vars.setdefault(language, {}) if language != "bash" else {})
        defines, uses = _def_use(source, language)
        cells.append(Cell(i, raw.get("id", f"cell-{i}"), language, source, output, not markers, markers,
                          _commands(language, source), reads, writes, defines, uses, _imports(source, language),
                          timestamp=_cell_timestamp(raw)))
    return cells


def _transcript_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_transcript_text(item) for item in value)
    if isinstance(value, dict):
        return _transcript_text(value.get("text") or value.get("content") or value.get("output") or "")
    return ""


def load_transcript(path: Path, start_index: int = 0) -> list[Cell]:
    """Extract Bash tool calls from the native message transcript.

    This is deliberately heuristic: only assistant ``tool_use`` blocks named Bash are
    included, and tool results are matched by tool-call id when available. Other tools,
    shell state, heredocs, and commands split across unrelated messages are not reconstructed.
    """
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    results: dict[str, str] = {}
    uses = []
    skipped_truncated = 0
    for ordinal, record in enumerate(records):
        call_id = record.get("tool_call_id")
        if call_id and isinstance(record.get("content"), str):
            value = record["content"]
            results[call_id] = results.get(call_id, "") + value if "content_chunk" in record else value
        for block in record.get("content", []) if isinstance(record.get("content"), list) else []:
            if block.get("type") == "tool_result" and block.get("tool_use_id"):
                results[block["tool_use_id"]] = _transcript_text(block.get("content"))
        for block in record.get("content", []) if isinstance(record.get("content"), list) else []:
            if block.get("type") == "tool_use" and str(block.get("name", "")).lower() == "bash":
                payload = block.get("input") or {}
                command = payload.get("command") or payload.get("cmd") or payload.get("script")
                if command:
                    uses.append((record.get("created_at", ""), ordinal, block.get("id", ""), str(command)))
        for call in record.get("tool_calls", []) if isinstance(record.get("tool_calls"), list) else []:
            if str(call.get("name", "")).lower() != "bash":
                continue
            args = call.get("args", "")
            command = None
            try:
                payload = json.loads(args) if isinstance(args, str) else args
                if isinstance(payload, dict):
                    command = payload.get("command") or payload.get("cmd") or payload.get("script")
            except (json.JSONDecodeError, TypeError):
                match = re.search(r'"command"\s*:\s*"((?:\\.|[^"\\])*)', args) if isinstance(args, str) else None
                if match:
                    try:
                        command = json.loads('"' + match.group(1) + '"')
                    except json.JSONDecodeError:
                        command = match.group(1).replace(r"\\\"", '"').replace(r"\\\\", "\\")
                else:
                    skipped_truncated += 1
            if command:
                uses.append(("", record.get("i", ordinal), call.get("id", ""), str(command)))
    if skipped_truncated:
        warnings.warn(f"skipped {skipped_truncated} Bash tool call(s) with truncated, unparseable args", RuntimeWarning)
    cells = []
    for offset, (timestamp, _order, call_id, source) in enumerate(sorted(uses, key=lambda item: (item[0], item[1]))):
        output = results.get(call_id, "")
        markers = [m for m in ERROR_MARKERS if m in output]
        reads, writes = _io(source, "bash")
        cells.append(Cell(start_index + offset, f"transcript-{call_id or offset}", "bash", source, output,
                          not markers, markers, _commands("bash", source), reads, writes, [], [], [],
                          "transcript", timestamp or None))
    return cells


def _merge_cells(notebook: list[Cell], transcript: list[Cell]) -> list[Cell]:
    """Merge by timestamp; cells without one retain stable source order first."""
    cells = notebook + transcript
    ordered = sorted(enumerate(cells), key=lambda item: (item[1].timestamp is None,
                                                          item[1].timestamp or "", item[0]))
    return [Cell(i, cell.cell_id, cell.language, cell.source, cell.output, cell.ok,
                 cell.error_markers, cell.commands, cell.reads, cell.writes, cell.defines,
                 cell.uses, cell.imports, cell.source_type, cell.timestamp)
            for i, (_, cell) in enumerate(ordered)]


def _folder(path: str) -> str:
    """Drop everything from the first templated part: /mnt/results/salmon/$s -> /mnt/results/salmon."""
    parts = path.rstrip("/").split("/")
    for i, part in enumerate(parts):
        if "$" in part or "{" in part or "*" in part:
            return "/".join(parts[:i])
    return "/".join(parts)


def _same(a: str, b: str) -> bool:
    if a == b:
        return True
    if a.startswith("/") and b.startswith("/"):
        return False
    return Path(a).name == Path(b).name


def _related(a: str, b: str) -> bool:
    """One path is inside the other (a folder and the files under it)."""
    fa, fb = _folder(a), _folder(b)
    if not fa or not fb or fa in _ROOTS or fb in _ROOTS:
        return False
    return fa == fb or fa.startswith(fb + "/") or fb.startswith(fa + "/")


def _matches(a: str, b: str) -> bool:
    return _same(a, b) or _related(a, b)


def _producers(ok: list[Cell], path: str, before: int | None) -> list[Cell]:
    earlier = [c for c in ok if before is None or c.index < before]
    exact = [c for c in earlier if any(_same(w, path) for w in c.writes)]
    found = exact[-1:] + [c for c in earlier if c not in exact[-1:] and any(_related(w, path) for w in c.writes)]
    if not found and path.startswith("/mnt/results/"):
        # A results file nobody visibly writes was made by the first cell that names it
        # (e.g. `fastp --json /mnt/results/x.json` has no write hint in front of the path).
        found = [c for c in earlier if any(_matches(r, path) for r in c.reads)][:1]
    return found


def _definer(ok: list[Cell], consumer: Cell, name: str) -> Cell | None:
    return next((c for c in reversed(ok) if c.index < consumer.index and c.language == consumer.language
                 and name in c.defines), None)


def lineage(cells: list[Cell], targets: list[str]) -> dict:
    ok = [c for c in cells if c.ok]
    imported = {(c.language, n) for c in cells for n in c.imports}
    kept: set[int] = set()
    unresolved: dict[int, list[str]] = {}
    paths: list[tuple[str, int | None]] = [(t, None) for t in targets]
    todo: list[Cell] = []
    seen: set[tuple[str, int | None]] = set()

    def keep(cell: Cell) -> None:
        if cell.index not in kept:
            kept.add(cell.index)
            paths.extend((r, cell.index) for r in cell.reads)
            todo.append(cell)

    while paths or todo:
        while paths:
            item = paths.pop()
            if item not in seen:
                seen.add(item)
                for producer in _producers(ok, *item):
                    keep(producer)
        if todo:
            cell = todo.pop()
            for name in cell.uses:
                if (cell.language, name) in imported:
                    continue
                definer = _definer(ok, cell, name)
                if definer is not None:
                    keep(definer)
                else:
                    unresolved.setdefault(cell.index, []).append(name)
    ordered = sorted(kept)
    by_index = {c.index: c for c in cells}
    produced = [p for k in ordered for p in by_index[k].writes + by_index[k].reads if p.startswith("/mnt/results/")]
    last = ordered[-1] if ordered else -1
    # Cells skipped before the last kept one that may still matter: python / R cells that
    # write nothing (in-memory state), and any cell that wrote under /mnt/results.
    possibly_state = [c.index for c in ok if c.index not in kept and c.index < last
                      and ((not c.writes and c.language in {"python", "r"})
                           or any(w.startswith("/mnt/results/") for w in c.writes))]
    return {"kept": ordered, "possibly_state": possibly_state,
            "unresolved_names": [{"cell": i, "names": unresolved[i]} for i in sorted(unresolved)],
            "unmatched_targets": [t for t in targets if not any(_matches(p, t) for p in produced)]}


def default_targets(cells: list[Cell]) -> list[str]:
    """Files and folders written under /mnt/results that no later successful cell reads."""
    ok = [c for c in cells if c.ok]
    return sorted({w for c in ok for w in c.writes
                   if w.startswith("/mnt/results/") and "/execution_trace/" not in w
                   and not any(d.index > c.index and any(_matches(r, w) for r in d.reads) for d in ok)})


def extract(paths: list[Path], targets: list[str] | None = None, transcript: Path | None = None) -> dict:
    cells = [c for p in paths for c in load_cells(p)]
    if transcript is None and paths:
        sibling = paths[0].parent / "transcript.jsonl"
        transcript = sibling if sibling.is_file() else None
    if transcript is not None and transcript.is_file():
        cells = _merge_cells(cells, load_transcript(transcript))
    else:
        cells = _merge_cells(cells, [])
    if targets is None:
        targets = default_targets(cells)
    lin = lineage(cells, targets)
    kept = set(lin["kept"])
    hardcoded = sorted({p for c in cells if c.index in kept for p in c.reads + c.writes if p.startswith("/mnt/")})
    return {"notebooks": [str(p) for p in paths], "transcript": str(transcript) if transcript else None,
            "targets": targets,
            "cells": [asdict(c) | {"kept": c.index in kept} for c in cells],
            **lin, "hardcoded_paths": hardcoded}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("notebooks", nargs="+", type=Path)
    parser.add_argument("--transcript", type=Path)
    parser.add_argument("--target", action="append")
    parser.add_argument("--out", type=Path, default=Path("steps.json"))
    args = parser.parse_args()
    steps = extract(args.notebooks, args.target, args.transcript)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(steps, indent=2) + "\n")
    print(f"{len(steps['kept'])} of {len(steps['cells'])} cells contribute to {len(steps['targets'])} target(s); "
          f"possibly in-memory state: {steps['possibly_state']}; unresolved names: "
          f"{ {u['cell']: u['names'] for u in steps['unresolved_names']} }; unmatched: {steps['unmatched_targets']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
