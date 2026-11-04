#!/usr/bin/env python3
"""Check a local module against the nf-core module conventions this skill family uses.

  lint_module.py modules/local/<name> [...]

Lint only modules you scaffolded. Vendored nf-core modules are maintained upstream.
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

COLLECTOR_MARKER = "// nextflow-skills: versions-collector"
LABELS = frozenset({"process_single", "process_low", "process_medium", "process_high",
                    "process_long", "process_high_memory"})
_CHECKS = [
    (r"^\s*process\s+[A-Z][A-Z0-9_]*\s*\{", "process name must be UPPER_SNAKE_CASE"),
    (r'^\s*tag\s+"\$\{?meta\.id\}?"', 'missing tag "${meta.id}"'),
    (r"^\s*label\s+'(" + "|".join(sorted(LABELS)) + r")'", "missing a standard nf-core process_* label"),
    (r'^\s*conda\s+"\$\{moduleDir\}/environment\.yml"', 'missing conda "${moduleDir}/environment.yml"'),
    (r"^\s*container\s+(?!''|\"\")\S", "missing container directive (or it is empty)"),
    (r"topic:\s*versions", "missing a versions output on the versions topic"),
    (r"task\.ext\.args", "script must read task.ext.args"),
    (r"task\.ext\.prefix", "script must read task.ext.prefix"),
    (r"task\.ext\.when\s*==\s*null\s*\|\|\s*task\.ext\.when", "missing when: task.ext.when == null || task.ext.when"),
    (r"^\s*stub:\s*$", "missing stub: block"),
]


def _script_block(text: str) -> str:
    match = re.search(r"^\s*script:\s*$", text, re.M)
    if not match:
        return ""
    rest = text[match.end():]
    end = re.search(r"^\s*(?:stub|output|when|exec):\s*$", rest, re.M)
    return rest[:end.start()] if end else rest


def _script_flags(text: str) -> list[str]:
    block = "\n".join(line for line in _script_block(text).splitlines()
                       if not line.lstrip().startswith(("//", "#"))
                       and not re.search(r"\b(?:echo|error|stop|unknown)\b", line, re.I))
    return sorted(set(re.findall(r"--([A-Za-z][A-Za-z0-9_-]*)", block)))


def _dynamic_script_flags(text: str) -> list[str]:
    return sorted(set(re.findall(r"--(\$\{[^}]+\})", _script_block(text))))


def _parser_flags(script: str) -> set[str]:
    parser = script.split("# Generated argument parser:", 1)[-1].split(
        "# End generated argument parser.", 1)[0]
    parser = "\n".join(line for line in parser.splitlines()
                         if not line.lstrip().startswith(("#", "//")))
    flags = set(re.findall(r'add_argument\(\s*["\']--([A-Za-z][A-Za-z0-9_-]*)', parser))
    for vector in re.findall(r'\.flags\s*<-\s*c\((.*?)\)', parser, re.S):
        flags.update(re.findall(r'["\']--([A-Za-z][A-Za-z0-9_-]*)["\']', vector))
    flags.update(re.findall(r'case\s+["\']--([A-Za-z][A-Za-z0-9_-]*)["\']', parser))
    flags.update(re.findall(r'^\s*["\']?--([A-Za-z][A-Za-z0-9_-]*)["\']?\)', parser, re.M))
    return flags


def _section(text: str, name: str, until: tuple[str, ...]) -> list[str]:
    m = re.search(rf"^\s*{name}:\s*$", text, re.M)
    if not m:
        return []
    rest = text[m.end():]
    end = re.search(rf"^\s*(?:{'|'.join(until)}):\s*$", rest, re.M)
    return [ln for ln in (rest[: end.start()] if end else rest).splitlines() if ln.strip()]


def _collector_errors(name: str, text: str) -> list[str]:
    # The template's versions collector reads the versions topic, so the usual versions-output
    # and meta rules don't apply; emitting to the topic it reads would stop it from closing.
    errors = [f"{name}: {msg}" for pattern, msg in _CHECKS
              if msg.startswith(("process name", "missing container", "missing stub", "missing when"))
              and not re.search(pattern, text, re.M)]
    if re.search(r"topic:\s*versions", text):
        errors.append(f"{name}: a versions collector must not emit to the versions topic it reads "
                      "(the topic never closes: deadlock). Restore the template's module")
    return errors


def lint(module_dir: Path, check_bin: bool = True) -> list[str]:
    name = module_dir.name
    errors = [f"{name}: missing {f}" for f in ("main.nf", "meta.yml", "environment.yml")
              if not (module_dir / f).is_file()]
    main = module_dir / "main.nf"
    if not main.is_file():
        return errors
    text = main.read_text()
    if COLLECTOR_MARKER in text:
        return errors + _collector_errors(name, text)
    errors += [f"{name}: {msg}" for pattern, msg in _CHECKS if not re.search(pattern, text, re.M)]
    inputs = _section(text, "input", ("output", "when", "script", "exec"))
    if not inputs or not inputs[0].strip().startswith("tuple val(meta)"):
        errors.append(f"{name}: first input should be tuple val(meta), ...")
    for line in _section(text, "output", ("when", "script", "exec", "shell")):
        if "emit:" not in line:
            errors.append(f"{name}: output without a named emit: {line.strip()}")
    if re.search(r"^\s*publishDir\b", text, re.M):
        errors.append(f"{name}: publishDir belongs in conf/modules.config, not the module")
    # Vendored nf-core modules own their inline command blocks and do not use this
    # skill's generated bin/ parser convention.  Compose may also skip this narrow
    # check after a user adapts a generated bin/ script; structural module checks
    # above still apply.
    if check_bin and module_dir.parent.name == "local":
        script_match = re.search(r'"""\s*\n\s*([^\n]+)', _script_block(text))
        if script_match:
            script_name = script_match.group(1).strip().split()[0].rstrip("\\")
            script_path = module_dir.parent.parent.parent / "bin" / script_name
            if script_path.is_file():
                script_text = script_path.read_text()
                first = script_text.splitlines()[0] if script_text.splitlines() else ""
                expected = {".R": "#!/usr/bin/env Rscript", ".py": "#!/usr/bin/env python3",
                            ".sh": "#!/usr/bin/env bash"}.get(script_path.suffix)
                if first != expected:
                    errors.append(f"{name}: shebang must be exactly {expected!r}: {first!r}")
                if script_path.suffix == ".py":
                    try:
                        compile(script_text, str(script_path), "exec")
                    except SyntaxError as error:
                        errors.append(f"{name}: syntax: {error.msg or error}")
                else:
                    parser = {".R": ["Rscript", "-e", f"invisible(parse(file={str(script_path)!r}))"],
                              ".sh": ["bash", "-n", str(script_path)]}.get(script_path.suffix)
                    if parser:
                        try:
                            subprocess.run(parser, check=True, capture_output=True, text=True)
                        except FileNotFoundError:
                            pass  # validate_local_scripts.py reports this as not_checkable.
                        except subprocess.CalledProcessError as error:
                            errors.append(f"{name}: syntax: {error.stderr.strip() or error.stdout.strip() or 'parser failed'}")
                accepted = _parser_flags(script_text)
                for flag in _script_flags(text):
                    if flag not in accepted:
                        errors.append(f"{name}: bin script does not parse --{flag} in a parser position")
                for flag in _dynamic_script_flags(text):
                    errors.append(f"{name}: warning: unable to verify dynamic flag --{flag}")
            else:
                errors.append(f"{name}: missing bin script {script_path}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("modules", nargs="+", type=Path)
    args = parser.parse_args()
    errors = [e for m in args.modules for e in lint(m)]
    for error in errors:
        print(error)
    if not errors:
        print(f"lint passed: {len(args.modules)} module(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
