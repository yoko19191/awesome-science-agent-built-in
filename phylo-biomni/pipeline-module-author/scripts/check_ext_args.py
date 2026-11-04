#!/usr/bin/env python3
"""Fail when conf/modules.config ext.args* repeat a flag the targeted module already sets.

  check_ext_args.py REPO [--allow FLAG ...]

Checks literal strings and the literal parts of closures (${...} is ignored).
Exit 1 on any duplicate. Use --allow only after confirming the duplicate is intended.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from module_flags import FLAG_RE, aliases, script_flags  # noqa: E402

_WITHNAME_RE = re.compile(r"withName\s*:\s*['\"]([^'\"]+)['\"]\s*\{")
_EXT_ARGS_RE = re.compile(r"ext\.(args\d?)\s*=\s*")
_PROCESS_RE = re.compile(r"^\s*process\s+([A-Z0-9_]+)\s*\{", re.M)
_INTERP_RE = re.compile(r"\$\{[^}]*\}")
_SHELL_META = set("|;&<>()[ ]")


def _balanced(text: str, start: int) -> str:
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1: i]
    return text[start + 1:]


def _value(text: str, start: int) -> str:
    ch = text[start]
    if ch in "'\"":
        end = start + 1
        while end < len(text) and not (text[end] == ch and text[end - 1] != "\\"):
            end += 1
        return text[start + 1: end]
    if ch == "{":
        return _balanced(text, start)
    return text[start: text.find("\n", start)]


def _process_files(repo: Path) -> dict[str, Path]:
    found = {}
    for path in sorted(repo.glob("**/*.nf")):
        for name in _PROCESS_RE.findall(path.read_text()):
            found.setdefault(name, path)
    return found


def _targets(selector: str) -> list[str]:
    names = []
    for alt in selector.split("|"):
        last = alt.split(":")[-1]
        names.append(re.sub(r"[.*^$()\[\]]", "", last))
    return [n for n in names if n]


_GROOVY_STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'')


def _rendered(raw: str, closure: bool) -> str:
    """The text the module's shell script receives: a closure's string literals, Groovy escapes undone."""
    if closure:
        raw = " ".join(a or b for a, b in _GROOVY_STRING_RE.findall(raw))
    return re.sub(r"\\(.)", r"\1", raw)


def _quote_errors(value: str) -> list[str]:
    """Reject escapes/metacharacters that would change an unquoted shell argument."""
    quote = None
    errors = []
    for char in value:
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
        elif char == "\\":
            errors.append("contains a backslash escape outside quotes; use shell quoting")
        elif char in _SHELL_META - {" "}:
            errors.append(f"contains shell metacharacter {char!r} outside quotes; use shell quoting")
    if quote:
        errors.append(f"unterminated {quote} quote; use matched shell quoting")
    return list(dict.fromkeys(errors))


def _program(repo: Path, module: Path) -> str:
    """Tool name from modules/<nf-core|local>/<tool>/..., used to look up flag aliases."""
    parts = module.relative_to(repo).parts
    return parts[2] if len(parts) > 3 and parts[0] == "modules" else ""


def check_repo(repo: Path, allow: set[str]) -> list[str]:
    config = (repo / "conf" / "modules.config").read_text()
    processes = _process_files(repo)
    errors = []
    for block in _WITHNAME_RE.finditer(config):
        body = _balanced(config, block.end() - 1)
        for name in _targets(block.group(1)):
            if name not in processes:
                continue
            module = processes[name]
            already = script_flags(module.read_text())
            program = _program(repo, module)
            for m in _EXT_ARGS_RE.finditer(body):
                raw = _value(body, m.end())
                errors.extend(f"{block.group(1)}: ext.{m.group(1)} {error}"
                              for error in _quote_errors(_rendered(raw, body[m.end()] == "{")))
                literal = _INTERP_RE.sub("", raw)
                for flag in sorted(set(FLAG_RE.findall(literal)) - allow):
                    hits = sorted(aliases(program, flag) & already)
                    if not hits:
                        continue
                    shown = flag if flag in hits else f"{flag} (= {hits[0]})"
                    errors.append(f"{block.group(1)}: ext.{m.group(1)} repeats {shown}, which "
                                  f"{module.relative_to(repo).as_posix()} already sets")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", type=Path)
    parser.add_argument("--allow", action="append", default=[])
    args = parser.parse_args()
    errors = check_repo(args.repo, set(args.allow))
    for error in errors:
        print(error)
    if not errors:
        print("ext.args check passed")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
