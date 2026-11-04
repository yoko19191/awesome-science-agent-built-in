#!/usr/bin/env python3
"""Scaffold an nf-core-convention local module that wraps session code.

  scaffold_local_module.py --repo R --name deseq2_contrast --tool deseq2 \
      --version-cmd 'Rscript --version' \
      --language r --code /workspace/cells_7_8.R --container URI --conda bioconda::bioconductor-deseq2=1.42.0 \
      --input counts --input samplesheet --output results='*.results.tsv' --label process_medium --cells 7,8

Writes modules/local/<name>/{main.nf,meta.yml,environment.yml} and bin/<name>.<ext>.
The generated bin script parses --prefix and one --<input> flag per input before the
session code. Keep the session logic below that parser. Version commands are placed in
Nextflow's single-quoted eval(), so they must contain no single or double quote or shell
pipe/chaining operator. Use ``Rscript --version``, ``python3 --version`` or
``bash --version``; use a generated helper script for package-specific reporting.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

EXT = {"r": ("R", "#!/usr/bin/env Rscript"), "python": ("py", "#!/usr/bin/env python3"),
       "bash": ("sh", "#!/usr/bin/env bash")}

MAIN_NF = """process __PROCESS__ {
    tag "${meta.id}"
    label '__LABEL__'

    conda "${moduleDir}/environment.yml"
    container '__CONTAINER__'

    input:
    tuple val(meta), __PATH_INPUTS__

    output:
__OUTPUTS__
    tuple val("${task.process}"), val('__TOOL__'), eval('__VERSION_CMD__'), emit: versions___TOOL_ID__, topic: versions

    when:
    task.ext.when == null || task.ext.when

    script:
    def args = task.ext.args ?: ''
    def prefix = task.ext.prefix ?: "${meta.id}"
    \"\"\"
    __SCRIPT__ \\\\
        ${args} \\\\
        --prefix ${prefix} \\\\
        __INPUT_FLAGS__
    \"\"\"

    stub:
    def prefix = task.ext.prefix ?: "${meta.id}"
    \"\"\"
__TOUCHES__
    \"\"\"
}
"""


def _stub_line(glob: str) -> str:
    path = re.sub(r"\{([^,}]+)[^}]*\}", r"\1", glob).replace("*", "${prefix}")
    if "/" in path:
        return f"    mkdir -p {path.rsplit('/', 1)[0]} && touch {path}"
    return f"    touch {path}"


def _render(**values: str) -> str:
    text = MAIN_NF
    for key, value in values.items():
        text = text.replace(f"__{key}__", value)
    return text


def _parser(language: str, inputs: list[str]) -> str:
    flags = ["prefix", *inputs]
    if language == "r":
        names = ", ".join(f'"--{flag}"' for flag in flags)
        assignments = "\n".join(f'{flag} <- parsed[["{flag}"]]' for flag in flags)
        return f'''\n# Generated argument parser: session logic starts below this block.\n.args <- commandArgs(trailingOnly = TRUE)\n.flags <- c({names})\n.parsed <- setNames(as.list(rep(NA_character_, length(.flags))), substring(.flags, 3L))\nextra_args <- character(0)\ni <- 1L\nwhile (i <= length(.args)) {{\n    token <- .args[[i]]\n    if (!(token %in% .flags)) {{\n        extra_args <- c(extra_args, token)\n        if (i < length(.args) && !startsWith(.args[[i + 1L]], "--")) {{\n            extra_args <- c(extra_args, .args[[i + 1L]])\n            i <- i + 1L\n        }}\n        i <- i + 1L\n        next\n    }}\n    if (i == length(.args) || startsWith(.args[[i + 1L]], "--"))\n        stop("missing value for: ", token)\n    .parsed[[substring(token, 3L)]] <- .args[[i + 1L]]\n    i <- i + 2L\n}}\nif (any(vapply(.parsed, is.na, logical(1))))\n    stop("missing required argument")\nparsed <- .parsed\n{assignments}\n'''
    if language == "python":
        args = "\n".join(f'parser.add_argument("--{flag}", required=True)' for flag in flags)
        assignments = "\n".join(f"{flag} = parsed.{flag}" for flag in flags)
        return f'''\n# Generated argument parser: session logic starts below this block.\nimport argparse\nparser = argparse.ArgumentParser()\n{args}\nparsed, extra_args = parser.parse_known_args()\n{assignments}\n'''
    cases = "\n".join(f'        "--{flag}") {flag}="$2"; shift 2 ;;' for flag in flags)
    checks = "\n".join(f'[ -n "${{{flag}}}" ] || {{ echo "missing --{flag}" >&2; exit 2; }}' for flag in flags)
    initial = "\n".join(f'{flag}=""' for flag in flags)
    return f'''\n# Generated argument parser: session logic starts below this block.\n{initial}\nEXTRA_ARGS=()\nwhile [ "$#" -gt 0 ]; do\n    case "$1" in\n{cases}\n        *) EXTRA_ARGS+=("$1"); shift\n           if [ "$#" -gt 0 ] && [[ "$1" != --* ]]; then EXTRA_ARGS+=("$1"); shift; fi ;;\n    esac\ndone\n{checks}\n'''


def _meta_yml(name: str, tool: str, description: str, inputs: list[str], outputs: dict[str, str], tool_id: str) -> str:
    lines = [f"name: {name}", f"description: {description or 'Local module wrapping session code'}",
             "keywords:", "  - local", "tools:", f"  - {tool}:", f"      description: {tool}",
             "input:", "  - - meta:", "        type: map", "        description: Sample information, e.g. [ id:'test' ]"]
    for inp in inputs:
        lines += [f"    - {inp}:", "        type: file", f"        description: {inp}"]
    lines.append("output:")
    for out, glob in outputs.items():
        lines += [f"  {out}:", "    - - meta:", "          type: map", "          description: Sample information",
                  f"      - \"{glob}\":", "          type: file", f"          description: {out}"]
    lines += [f"  versions_{tool_id}:", "    - - ${task.process}:", "          type: string",
              "          description: Process name", f"      - {tool}:", "          type: string",
              "          description: Tool name", "      - version:", "          type: eval",
              "          description: Tool version"]
    return "\n".join(lines) + "\n"


def scaffold(repo: Path, name: str, tool: str, version_cmd: str, language: str, code: Path, container: str,
             conda: list[str], inputs: list[str], outputs: dict[str, str], label: str = "process_single",
             description: str = "", cells: list[int] | None = None) -> list[Path]:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError("name must be lower_snake_case")
    if "'" in version_cmd or '"' in version_cmd:
        raise ValueError("version command must not contain quotes; use a quote-free command or helper script")
    if re.search(r"\||;|&&", version_cmd):
        raise ValueError("version command must not use pipes or command chaining")
    if not inputs or not outputs:
        raise ValueError("at least one input and one output are required")
    if not container.strip():
        raise ValueError("a container URI is required; never leave a module without a container")
    ext, shebang = EXT[language]
    tool_id = re.sub(r"\W", "_", tool.lower())
    script_name = f"{name}.{ext}"
    mod = repo / "modules" / "local" / name
    mod.mkdir(parents=True, exist_ok=True)
    (repo / "bin").mkdir(parents=True, exist_ok=True)
    main = _render(
        PROCESS=name.upper(), LABEL=label, CONTAINER=container,
        PATH_INPUTS=", ".join(f"path({i})" for i in inputs),
        OUTPUTS="\n".join(f'    tuple val(meta), path("{g}"), emit: {o}' for o, g in outputs.items()),
        TOOL=tool, TOOL_ID=tool_id, VERSION_CMD=version_cmd.replace("\\", "\\\\"), SCRIPT=script_name,
        INPUT_FLAGS=" \\\\\n        ".join(f"--{i} ${{{i}}}" for i in inputs),
        TOUCHES="\n".join(_stub_line(g) for g in outputs.values()),
    )
    (mod / "main.nf").write_text(main)
    (mod / "meta.yml").write_text(_meta_yml(name, tool, description, inputs, outputs, tool_id))
    (mod / "environment.yml").write_text(
        "channels:\n  - conda-forge\n  - bioconda\ndependencies:\n" + "".join(f"  - {d}\n" for d in conda))
    provenance = f"# Adapted from session cells {', '.join(map(str, cells))}\n" if cells else ""
    script = repo / "bin" / script_name
    script.write_text(f"{shebang}\n{provenance}{_parser(language, inputs)}# End generated argument parser.\n{code.read_text()}")
    try:
        script.chmod(0o755)
    except OSError as error:
        print(f"warning: could not make {script} executable ({error}). Build the repo in local scratch "
              "(e.g. /workspace/<name>), not on /mnt/results, then run chmod +x on it.", file=sys.stderr)
    return [mod / "main.nf", mod / "meta.yml", mod / "environment.yml", script]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--version-cmd", required=True)
    parser.add_argument("--language", choices=sorted(EXT), required=True)
    parser.add_argument("--code", type=Path, required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--conda", action="append", default=[])
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--output", action="append", default=[])
    parser.add_argument("--label", default="process_single")
    parser.add_argument("--description", default="")
    parser.add_argument("--cells", default="")
    args = parser.parse_args()
    outputs = dict(o.split("=", 1) for o in args.output)
    cells = [int(c) for c in args.cells.split(",") if c]
    for path in scaffold(args.repo, args.name, args.tool, args.version_cmd, args.language, args.code,
                         args.container, args.conda, args.input, outputs, args.label, args.description, cells):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
