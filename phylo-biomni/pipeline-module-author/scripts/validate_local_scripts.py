#!/usr/bin/env python3
"""Validate generated local-module scripts without running their tools.

The checker is deliberately static: it parses scripts, shell command strings, declared
outputs and direct imports.  It never installs packages or executes a version command.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from dependency_metadata import provided_by, runtime_dependencies

LANGUAGES = {".R": "r", ".py": "python", ".sh": "bash"}
SHEBANGS = {"r": "#!/usr/bin/env Rscript", "python": "#!/usr/bin/env python3", "bash": "#!/usr/bin/env bash"}
BASE_R = {"stats", "utils", "methods", "grDevices", "graphics", "tools", "parallel", "grid", "splines"}
R_BIOC = {"deseq2", "tximport", "tximeta", "limma", "edgeR", "biomaRt", "GenomicRanges"}
R_CRAN = {"ggplot2", "svglite", "ggrepel", "pheatmap", "data.table", "readr", "dplyr", "tidyr"}
PYTHON_NAMES = {"sklearn": "scikit-learn", "yaml": "pyyaml", "PIL": "pillow"}
COLLECTOR_MARKER = "// nextflow-skills: versions-collector"


def _script_block(text: str) -> str:
    match = re.search(r'^\s*script:\s*$[\s\S]*?^\s*"""([\s\S]*?)^\s*"""', text, re.M)
    return match.group(1) if match else ""


def _script_path(repo: Path, module: Path) -> Path | None:
    main = module / "main.nf"
    if not main.is_file():
        return None
    match = re.search(r'(?<![\w./-])([A-Za-z0-9_.-]+\.(?:R|py|sh))(?![\w.-])',
                      _script_block(main.read_text()))
    if not match:
        return None
    return repo / "bin" / Path(match.group(1)).name


def _language(path: Path) -> str | None:
    return LANGUAGES.get(path.suffix)


def _parse_dependencies(path: Path, language: str) -> list[str]:
    text = path.read_text()
    if language == "r":
        without_comments = _strip_r_comments(text)
        strings = _r_string_spans(without_comments)
        names = [match.group(1) for match in re.finditer(
            r"requireNamespace\s*\(\s*['\"]([A-Za-z][A-Za-z0-9.]*)", without_comments)
            if not any(start <= match.start() < end for start, end in strings)]
        code = _strip_r_strings(without_comments)
        names += re.findall(r"\b(?:library|require)\s*\(\s*([A-Za-z][A-Za-z0-9.]*)", code)
        names += re.findall(r"\b([A-Za-z][A-Za-z0-9.]*)::", code)
        return sorted(set(names))
    if language == "python":
        try:
            tree = ast.parse(text, str(path))
        except SyntaxError:
            return []
        names = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                names.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.append(node.module.split(".")[0])
        stdlib = getattr(sys, "stdlib_module_names", frozenset())
        return sorted({n for n in names if n not in stdlib})
    return []


def _r_string_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    quote = None
    escaped = False
    start = 0
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                spans.append((start, index + 1))
                quote = None
        elif char in "'\"":
            quote = char
            start = index
    return spans


def _strip_r_comments(text: str) -> str:
    """Blank R comments while retaining strings for requireNamespace extraction."""
    out = []
    quote = None
    comment = False
    escaped = False
    for char in text:
        if comment:
            out.append("\n" if char == "\n" else " ")
            if char == "\n":
                comment = False
        elif quote:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            out.append(char)
        elif char == "#":
            out.append(" ")
            comment = True
        else:
            out.append(char)
    return "".join(out)


def _strip_r_strings(text: str) -> str:
    """Blank R string literals so package-looking text cannot become a dependency."""
    out = []
    quote = None
    escaped = False
    for char in text:
        if quote:
            out.append("\n" if char == "\n" else " ")
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            out.append(" ")
        else:
            out.append(char)
    return "".join(out)


def conda_name(package: str, language: str) -> str | None:
    if language == "r":
        if package in BASE_R:
            return None
        if package in R_BIOC or package.lower() in {p.lower() for p in R_BIOC}:
            return "bioconductor-" + package.lower()
        if package in R_CRAN or package.lower() in {p.lower() for p in R_CRAN}:
            return "r-" + package.lower().replace(".", "-")
        return "r-" + package.lower().replace(".", "-")
    if language == "python":
        return PYTHON_NAMES.get(package, package).lower().replace("_", "-")
    return None


def _pins(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    result = set()
    in_deps = False
    for line in path.read_text().splitlines():
        if line.strip() == "dependencies:":
            in_deps = True
            continue
        if in_deps and line and not line.startswith(" "):
            break
        if in_deps and line.lstrip().startswith("-"):
            value = line.split("-", 1)[1].strip().split("=", 1)[0].split(">", 1)[0].split("<", 1)[0]
            result.add(value.split("::")[-1].lower())
    return result


def _covered(conda: str, pin: str) -> bool:
    c, p = conda.lower(), pin.lower()
    return c == p or p in {c, "r-" + c, "bioconductor-" + c, "py-" + c, "python-" + c}


def _indexed_pins(index: dict | None, image: str) -> set[str]:
    if not index:
        return set()
    for component in index.get("components", []):
        if (component.get("containers") or {}).get("docker") == image:
            return {p.split("::")[-1].split("=", 1)[0].lower() for p in component.get("conda", [])}
    return set()


def _container(module: Path) -> str:
    match = re.search(r"^\s*container\s+['\"]([^'\"]+)", (module / "main.nf").read_text(), re.M)
    return match.group(1) if match else ""


def _glob_variants(pattern: str) -> list[str]:
    match = re.search(r"\{([^{}]+)\}", pattern)
    if not match:
        return [pattern]
    return [pattern[:match.start()] + item + pattern[match.end():] for item in match.group(1).split(",")]


def _glob_matches(pattern: str, output: str) -> bool | None:
    if any(ch in pattern for ch in "${()"):
        return None
    return any(fnmatch.fnmatch(output, p) or fnmatch.fnmatch(p, output) for p in _glob_variants(pattern))


def _outputs(module: Path) -> list[str]:
    text = (module / "main.nf").read_text()
    section = re.search(r"^\s*output:\s*$([\s\S]*?)(?=^\s*(?:when|script|stub|exec):\s*$)", text, re.M)
    return re.findall(r'path\(\s*["\']([^"\']+)', section.group(1) if section else "")


def _publish_patterns(repo: Path, module: Path) -> list[tuple[str, str]]:
    patterns = []
    config = repo / "conf" / "modules.config"
    if config.is_file():
        text = config.read_text()
        process_match = re.search(r"^\s*process\s+(\w+)\s*\{", (module / "main.nf").read_text(), re.M)
        process = process_match.group(1) if process_match else module.name
        blocks = re.findall(r"withName:\s*['\"]([^'\"]+)['\"]\s*\{(.*?)\n\s*\}", text, re.S)
        for selector, body in blocks:
            if _selector_matches(selector, process):
                for value in re.findall(r"pattern:\s*['\"]([^'\"]+)", body):
                    patterns.append((value, "modules.config"))
        default = re.search(r"process\s*\{(.*?)(?=\n\s*withName:|\Z)", text, re.S)
        if not patterns and default and "publishDir" in default.group(1):
            patterns.append(("*", "default publishDir"))
    return patterns


def _selector_matches(selector: str, process: str) -> bool:
    """Mirror preflight's withName regex matching for local process names."""
    candidates = (process, f"<ANY>:{process}", f"workflow:{process}")
    try:
        return any(re.fullmatch(selector, candidate) for candidate in candidates)
    except re.error:
        return selector in candidates


def _version_commands(text: str) -> list[str]:
    return [m.group(1).replace("\\'", "'").replace("\\\\", "\\")
            for m in re.finditer(r"\beval\(\s*'((?:\\.|[^'])*)'\s*\)", text)]


def _check_version(command: str) -> str | None:
    if re.search(r"\||;|&&", command):
        return "version command must not use pipes or command chaining"
    try:
        subprocess.run(["bash", "-n", "-c", command], check=True, capture_output=True, text=True)
    except FileNotFoundError:
        return "bash is not on PATH"
    except subprocess.CalledProcessError as error:
        return f"bash syntax: {error.stderr.strip() or 'invalid command'}"
    try:
        words = shlex.split(command)
    except ValueError as error:
        return f"shell quoting: {error}"
    if words and words[0] in {"Rscript", "python", "python3", "bash"} and not shutil.which(words[0]):
        return f"not_checkable: {words[0]} is not on PATH"
    if len(words) >= 3 and ((words[0] == "Rscript" and words[1] == "-e") or
                            (words[0] in {"python", "python3"} and words[1] in {"-e", "-c"})):
        interpreter = words[0]
        code = words[2]
        if interpreter == "Rscript":
            probe = "invisible(parse(text=" + repr(code) + "))"
            argv = [interpreter, "-e", probe]
        else:
            argv = [interpreter, "-c", "compile(" + repr(code) + ", '<version>', 'exec')"]
        try:
            subprocess.run(argv, check=True, capture_output=True, text=True)
        except FileNotFoundError:
            return f"not_checkable: {interpreter} is not on PATH"
        except subprocess.CalledProcessError as error:
            return f"{interpreter} syntax: {error.stderr.strip() or 'invalid code'}"
    return None


def validate_module(repo: Path, module: Path, index: dict | None = None, accepted: set[str] | None = None) -> dict:
    accepted = accepted or set()
    errors: list[str] = []
    warnings: list[str] = []
    not_checkable: list[str] = []
    checks: dict[str, str] = {}
    main_text = (module / "main.nf").read_text()
    if COLLECTOR_MARKER in main_text:
        return {"module": module.name, "checks": {"skipped": "versions-collector"}, "errors": [],
                "warnings": [], "not_checkable": []}

    script = _script_path(repo, module)
    language = _language(script) if script else None
    if script:
        if not script.is_file():
            errors.append("missing generated bin script")
        else:
            expected = SHEBANGS.get(language or "")
            first = script.read_text().splitlines()[0] if script.read_text().splitlines() else ""
            if first != expected:
                errors.append(f"shebang must be exactly {expected!r}, got {first!r}")
            else:
                checks["shebang"] = "ok"
            command = None
            if language == "python":
                try:
                    compile(script.read_text(), str(script), "exec")
                    checks["syntax"] = "ok"
                except SyntaxError as error:
                    errors.append(f"syntax: {error.msg or error}")
            elif language == "r":
                command = ["Rscript", "-e", f"invisible(parse(file={str(script)!r}))"]
            elif language == "bash":
                command = ["bash", "-n", str(script)]
            else:
                errors.append(f"unsupported script extension {script.suffix!r}")
                command = None
            if command:
                try:
                    subprocess.run(command, check=True, capture_output=True, text=True)
                    checks["syntax"] = "ok"
                except FileNotFoundError:
                    not_checkable.append(f"{command[0]} is not on PATH")
                except subprocess.CalledProcessError as error:
                    errors.append(f"syntax: {error.stderr.strip() or error.stdout.strip() or 'parser failed'}")

    version_errors = []
    for command_text in _version_commands(main_text):
        problem = _check_version(command_text)
        if problem:
            (not_checkable if problem.startswith("not_checkable:") else version_errors).append(problem)
    errors.extend(version_errors)
    checks["version_commands"] = "ok" if not version_errors else "error"
    outputs = _outputs(module)
    if (repo / "conf" / "modules.config").is_file() and "publishDir" in (repo / "conf" / "modules.config").read_text():
        if outputs:
            checks["default_publish"] = "ok"
        else:
            errors.append("default publishDir has no declared output glob to match")
    for pattern, source in _publish_patterns(repo, module):
        matches = [_glob_matches(pattern, output) for output in outputs]
        if any(m is True for m in matches):
            continue
        if any(m is None for m in matches):
            warnings.append(f"publish pattern {pattern!r} in {source} could not be compared conservatively")
        else:
            errors.append(f"publish pattern {pattern!r} matches no declared output glob")
    if not script or not script.is_file():
        return {"module": module.name, "checks": checks, "errors": errors, "warnings": warnings,
                "not_checkable": not_checkable}
    deps = _parse_dependencies(script, language or "")
    deps = sorted(set(deps) | {
        dep for package in deps for dep in runtime_dependencies(package, language or "")
    })
    pins = _pins(module / "environment.yml")
    image_pins = _indexed_pins(index, _container(module))
    unresolved = []
    for dependency in deps:
        conda = conda_name(dependency, language or "")
        provided_conda = {conda_name(package, language or "") for package in provided_by(dependency, language or "")}
        if conda and not any(candidate and _covered(candidate, pin)
                             for candidate in {conda} | provided_conda
                             for pin in pins | image_pins):
            key = f"{module.name}:{dependency}"
            if dependency not in accepted and key not in accepted and conda not in accepted:
                unresolved.append(f"{dependency} ({conda})")
    if unresolved:
        errors.extend(f"unresolved dependency: {item}" for item in unresolved)
    checks["dependencies"] = "ok" if not unresolved else "error"
    return {"module": module.name, "checks": checks, "errors": errors, "warnings": warnings,
            "not_checkable": not_checkable}


def validate(repo: Path, index: dict | None = None, accepted: set[str] | None = None) -> list[dict]:
    return [validate_module(repo, module, index, accepted)
            for module in sorted((repo / "modules" / "local").glob("*")) if module.is_dir()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("--module-author-dir", type=Path, help="skill directory; accepted for compose symmetry")
    parser.add_argument("--index", type=Path)
    parser.add_argument("--plan", type=Path)
    args = parser.parse_args(argv)
    index = json.loads(args.index.read_text()) if args.index else None
    plan = json.loads(args.plan.read_text()) if args.plan else {}
    result = validate(args.repo, index, set(plan.get("dependencies_accepted", [])))
    print(json.dumps(result, indent=2))
    return 1 if any(r["errors"] for r in result) else 0


if __name__ == "__main__":
    raise SystemExit(main())
