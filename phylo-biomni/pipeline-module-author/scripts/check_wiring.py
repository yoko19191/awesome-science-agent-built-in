#!/usr/bin/env python3
"""Conservatively check typed Nextflow workflow wiring.

This is a static heuristic.  It intentionally leaves expressions it cannot
prove as ``unverified``; the Nextflow stub run remains authoritative.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Shape:
    kind: str
    arity: int | None = None
    meta_first: bool | None = None
    wrapped: bool = False


@dataclass
class Interface:
    name: str
    file: Path
    inputs: list[Shape] = field(default_factory=list)
    outputs: dict[str, Shape] = field(default_factory=dict)
    output_globs: dict[str, str] = field(default_factory=dict)
    workflow: bool = False


@dataclass
class Include:
    alias: str
    target: Interface | None
    file: Path
    line: int


def _lexical_mask(text: str) -> str:
    """Blank comments and literals while preserving offsets and newlines."""
    chars = list(text)
    i = 0
    quote = None
    while i < len(text):
        if text.startswith("//", i):
            end = text.find("\n", i)
            end = len(text) if end < 0 else end
            for j in range(i, end):
                chars[j] = " "
            i = end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = len(text) if end < 0 else end + 2
            for j in range(i, end):
                if text[j] != "\n":
                    chars[j] = " "
            i = end
            continue
        if text.startswith("'''", i) or text.startswith('"""', i):
            quote = text[i:i + 3]
            end = text.find(quote, i + 3)
            end = len(text) if end < 0 else end + 3
            for j in range(i, end):
                if text[j] != "\n":
                    chars[j] = " "
            i = end
            continue
        if text[i] in "'\"":
            quote = text[i]
            j = i + 1
            while j < len(text):
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == quote:
                    j += 1
                    break
                j += 1
            for k in range(i, min(j, len(text))):
                if text[k] != "\n":
                    chars[k] = " "
            i = j
            continue
        if text[i] == "/" and i + 1 < len(text) and text[i + 1] not in "/*":
            j = i + 1
            while j < len(text):
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "/":
                    j += 1
                    break
                if text[j] == "\n":
                    break
                j += 1
            if j > i + 1 and j <= len(text) and (j == len(text) or text[j - 1] == "/"):
                for k in range(i, j):
                    if text[k] != "\n":
                        chars[k] = " "
                i = j
                continue
        i += 1
    return "".join(chars)


def _matching(text: str, start: int, opening: str = "(") -> int:
    closing = {"(": ")", "[": "]", "{": "}"}[opening]
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return i
    return -1


def _split_top(text: str, delimiter: str = ",") -> list[str]:
    result, begin = [], 0
    stack: list[str] = []
    masked = _lexical_mask(text)
    pairs = {"(": ")", "[": "]", "{": "}"}
    for i, ch in enumerate(masked):
        if ch in pairs:
            stack.append(pairs[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
        elif not stack and ch == delimiter:
            result.append(text[begin:i].strip())
            begin = i + 1
    tail = text[begin:].strip()
    if tail:
        result.append(tail)
    return result


def _blocks(text: str, kind: str, masked: str | None = None) -> list[tuple[str, str, int, int]]:
    found = []
    masked = masked or _lexical_mask(text)
    pattern = (re.compile(rf"\b{kind}\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{{") if kind != "workflow"
               else re.compile(r"\bworkflow(?:\s+([A-Za-z_][A-Za-z0-9_]*))?\s*\{"))
    for match in pattern.finditer(masked):
        end = _matching(masked, match.end() - 1, "{")
        if end < 0:
            end = len(text)
        found.append((match.group(1) or "<entry>", text[match.end():end], match.start(), end))
    return found


def _sections(body: str, masked: str | None = None) -> dict[str, str]:
    masked = masked or _lexical_mask(body)
    matches = list(re.finditer(r"(?m)^\s*(input|output|take|emit|main|when|script|stub|exec):\s*$", masked))
    return {m.group(1): body[m.end(): matches[i + 1].start() if i + 1 < len(matches) else len(body)]
            for i, m in enumerate(matches)}


def _shape(text: str) -> Shape:
    text = re.sub(r"\b(?:optional|stageAs)\s*:\s*[^,]+", "", text).strip()
    if text.startswith("tuple"):
        bits = _split_top(text[len("tuple"):].strip())
        return Shape("tuple", len(bits), bool(bits and re.match(r"\s*val\s*\(", bits[0])))
    if re.match(r"(?:path|file)\b", text) or text.startswith("path("):
        return Shape("path")
    if re.match(r"val\b", text):
        return Shape("val")
    if re.match(r"each\b", text):
        return Shape("each")
    return Shape("unknown")


def _decls(section: str) -> list[Shape]:
    declarations = []
    for line in section.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        declarations.append(_shape(line.rstrip(",")))
    return declarations


def _parse_file(path: Path) -> list[Interface]:
    text = path.read_text()
    masked = _lexical_mask(text)
    result = []
    for kind in ("process", "workflow"):
        for name, body, offset, _ in _blocks(text, kind, masked):
            sections = _sections(body)
            interface = Interface(name, path, workflow=kind == "workflow")
            if kind == "process":
                interface.inputs = _decls(sections.get("input", ""))
                for line in sections.get("output", "").splitlines():
                    emit = re.search(r"\bemit\s*:\s*([A-Za-z_][A-Za-z0-9_]*)", line)
                    if emit:
                        declaration = line[:emit.start()].rstrip(" ,")
                        declaration = re.sub(r",\s*optional\s*:\s*true\s*$", "", declaration)
                        interface.outputs[emit.group(1)] = _shape(declaration)
                        glob = re.search(r"\bpath\s*\(\s*['\"]([^'\"]+)['\"]", declaration)
                        if glob:
                            interface.output_globs[emit.group(1)] = glob.group(1)
            else:
                interface.inputs = [Shape("unknown") for line in sections.get("take", "").splitlines()
                                    if line.strip() and not line.strip().startswith("//")]
                for line in sections.get("emit", "").splitlines():
                    match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\b", line)
                    if match:
                        interface.outputs[match.group(1)] = Shape("unknown")
            result.append(interface)
    return result


def _candidate_files(repo: Path) -> list[Path]:
    paths = [repo / "main.nf"] if (repo / "main.nf").is_file() else []
    paths += sorted((repo / "workflows").glob("*.nf")) if (repo / "workflows").is_dir() else []
    paths += sorted((repo / "subworkflows").glob("**/main.nf")) if (repo / "subworkflows").is_dir() else []
    return [p.resolve() for p in paths if p.is_file()]


def _resolve(repo: Path, source: Path, raw: str) -> Path | None:
    path = (source.parent / raw).resolve()
    candidates = [path, path.with_suffix(".nf"), path / "main.nf"]
    return next((p for p in candidates if p.is_file() and p.is_relative_to(repo.resolve())), None)


def _includes(repo: Path, path: Path, interfaces: dict[Path, list[Interface]]) -> list[Include]:
    result = []
    text = path.read_text()
    masked = _lexical_mask(text)
    pattern = re.compile(r"(?ms)^\s*include\s*\{(.*?)\}\s*from")
    for match in pattern.finditer(masked):
        raw_tail = text[match.end():]
        path_match = re.match(r"\s*['\"]([^'\"]+)['\"]", raw_tail)
        if not path_match:
            continue
        target_path = _resolve(repo, path, path_match.group(1))
        by_name = {i.name: i for i in interfaces.get(target_path, [])} if target_path else {}
        line = text.count("\n", 0, match.start()) + 1
        for item in re.split(r"[,;\n]+", text[match.start(1):match.end(1)]):
            alias_match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)(?:\s+as\s+([A-Za-z_][A-Za-z0-9_]*))?", item)
            if alias_match:
                result.append(Include(alias_match.group(2) or alias_match.group(1),
                                      by_name.get(alias_match.group(1)), path, line))
    return result


def _literal_shape(expr: str) -> Shape | None:
    expr = expr.strip()
    if re.search(r"\.(?:collect|toList)\s*\(", expr):
        return Shape("val", wrapped=True)
    if re.match(r"^(?:true|false|[-+]?\d+(?:\.\d+)?|['\"].*['\"]|\[\])$", expr, re.S):
        return Shape("val")
    masked_expr = _lexical_mask(expr)
    closures = list(re.finditer(r"\b(?:map|flatMap)\s*\{", masked_expr))
    closure = closures[-1] if closures else None  # the last closure decides the shape
    if closure:
        start = _matching(masked_expr, closure.end() - 1, "{")
        if start >= 0 and not expr[start + 1:].strip():
            body = expr[closure.end():start]
            arrow = body.find("->")
            returned = body[arrow + 2:].strip() if arrow >= 0 else body
            if returned.startswith("tuple("):
                inner = returned[len("tuple("):]
                bits = _split_top(inner[:-1] if inner.endswith(")") else inner)
                meta = bool(bits and re.match(r"\s*(?:meta\b|\[[^]]*:\s*[^]]*\])", bits[0]))
                return Shape("tuple", len(bits), meta)
            if returned.startswith("["):
                end = _matching(returned, 0, "[")
                bits = _split_top(returned[1:end] if end >= 0 else returned[1:])
                if bits:
                    first = bits[0]
                    meta = bool(re.match(r"\s*(?:meta\b|\[[^]]*:\s*[^]]*\])", first))
                    return Shape("tuple", len(bits), meta)
    if re.search(r"\b(?:channel\.)?fromPath\s*\(", expr) or re.search(r"\bfile\s*\(", expr):
        return Shape("path")
    return None


_RESHAPING = r"\.(?:map|flatMap|combine|join|groupTuple|collect|toList|flatten|transpose|cross|multiMap|branch)\b"


def _infer(expr: str, outputs: dict[str, Shape]) -> Shape | None:
    ref = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\.out\.([A-Za-z_][A-Za-z0-9_]*)", expr)
    literal = _literal_shape(expr)
    if ref:
        if literal:
            return literal
        # an operator after X.out.<emit> may reshape it; without a final literal, leave it to the stub run
        if re.search(_RESHAPING, expr[ref.end():]):
            return None
        return outputs.get(ref.group(2))
    return literal


def check_repo(repo: Path) -> dict:
    repo = Path(repo).resolve()
    files = _candidate_files(repo)
    parsed_files = sorted(set(files + [p.resolve() for p in repo.rglob("*.nf")]))
    interfaces: dict[Path, list[Interface]] = {p: _parse_file(p) for p in parsed_files}
    includes_by_file = {path: _includes(repo, path, interfaces) for path in files}
    errors: list[str] = []
    warnings: list[str] = []
    calls = []
    edges = []
    unverified = 0

    for path in files:
        text = path.read_text()
        masked = _lexical_mask(text)
        symbols = {inc.alias: inc for inc in includes_by_file[path] if inc.target}
        workflows = _blocks(text, "workflow", masked)
        for workflow_name, body, offset, _ in workflows:
            body_masked = _lexical_mask(body)
            call_matches = []
            for alias, inc in symbols.items():
                for match in re.finditer(rf"\b{re.escape(alias)}\s*\(", body_masked):
                    end = _matching(body_masked, match.end() - 1)
                    if end < 0:
                        continue
                    call_matches.append((match.start(), alias, body[match.end():end], inc))
            call_matches.sort()
            called: set[str] = set()
            for start, alias, args_text, inc in call_matches:
                line = text.count("\n", 0, offset + start) + 1
                args = _split_top(args_text)
                expected = len(inc.target.inputs) if inc.target else 0
                checks = []
                if len(args) != expected:
                    msg = f"{path.relative_to(repo)}:{line}: {alias} expects {expected} inputs, got {len(args)}"
                    errors.append(msg)
                    checks.append("argument_count:error")
                else:
                    checks.append("argument_count:ok")
                for index, arg in enumerate(args):
                    ref = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\.out\.([A-Za-z_][A-Za-z0-9_]*)", arg)
                    if ref:
                        producer, emit = ref.groups()
                        producer_inc = symbols.get(producer)
                        if producer not in called:
                            errors.append(f"{path.relative_to(repo)}:{line}: {producer}.out used before {producer} is called")
                        if producer_inc and emit not in producer_inc.target.outputs:
                            errors.append(f"{path.relative_to(repo)}:{line}: unknown emit '{emit}' of {producer}")
                        checks.append(f"emit:{producer}.{emit}")
                        producer_inc = symbols.get(producer)
                        consumer_target = inc.target
                        if producer_inc and producer_inc.target and consumer_target:
                            edges.append({
                                "producer": producer,
                                "consumer": alias,
                                "emit": emit,
                                "pattern": producer_inc.target.output_globs.get(emit),
                                "producer_module": str(producer_inc.target.file.relative_to(repo)),
                                "consumer_module": str(consumer_target.file.relative_to(repo)),
                                "file": f"{path.relative_to(repo)}:{line}",
                            })
                    producer_outputs = {}
                    if ref and symbols.get(ref.group(1)) and symbols[ref.group(1)].target:
                        producer_outputs = symbols[ref.group(1)].target.outputs
                    shape = _infer(arg, producer_outputs)  # X.out.<emit> takes X's output shape
                    if index < expected and shape and inc.target and not inc.target.workflow:
                        declared = inc.target.inputs[index]
                        if (declared.kind == "tuple" and
                                re.search(r"\b(?:channel\.)?value\s*\(", arg) and
                                not re.search(r"\.(?:first|collect|toList)\s*\(", arg)):
                            warnings.append(f"{path.relative_to(repo)}:{line}: single-item value passed to per-sample tuple input of {alias}")
                        if declared.kind == "tuple" and shape.kind == "tuple":
                            if declared.arity != shape.arity:
                                errors.append(f"{path.relative_to(repo)}:{line}: {alias} input {index + 1} tuple arity "
                                               f"{shape.arity} does not match declared {declared.arity}")
                            elif declared.meta_first and shape.meta_first is False:
                                errors.append(f"{path.relative_to(repo)}:{line}: {alias} input {index + 1} meta must be first")
                            else:
                                checks.append(f"shape:{index + 1}:ok")
                        elif declared.kind == "tuple" and shape.kind != "tuple":
                            unverified += 1
                            warnings.append(f"{path.relative_to(repo)}:{line}: {alias} input {index + 1} shape unverified")
                    elif index < expected and shape is None:
                        unverified += 1
                        warnings.append(f"{path.relative_to(repo)}:{line}: {alias} input {index + 1} shape unverified")
                if any("collect()" in arg or "toList()" in arg for arg in args):
                    if inc.target and inc.target.inputs and inc.target.inputs[0].kind == "tuple":
                        warnings.append(f"{path.relative_to(repo)}:{line}: collected value passed to per-sample tuple input of {alias}")
                calls.append({"call": alias, "file": f"{path.relative_to(repo)}:{line}",
                              "inputs_expected": expected, "args": args, "checks": checks})
                called.add(alias)

    return {"ok": not errors, "errors": errors, "warnings": warnings, "calls": calls,
            "edges": edges, "unverified": unverified}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = check_repo(args.repo)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for error in result["errors"]:
            print(f"ERROR: {error}")
        for warning in result["warnings"]:
            print(f"WARNING: {warning}")
        print(f"wiring check: {'passed' if result['ok'] else 'failed'} ({result['unverified']} unverified)")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
