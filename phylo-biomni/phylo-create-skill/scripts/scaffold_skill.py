#!/usr/bin/env python3
"""Scaffold a generated skill from one validated phylo-analysis-spec/1 document."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import shutil
import sys

sys.dont_write_bytecode = True

import skill_spec  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
TEMPLATES = ROOT / "templates"
EXAMPLES = {
    "artifacts": ROOT / "references" / "design-analysis.example.json",
    "scientific-report": ROOT / "references" / "design-report-analysis.example.json",
}


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _table_cell(value: object) -> str:
    return (
        str(value)
        .replace("|", r"\|")
        .replace("\r\n", "<br>")
        .replace("\r", "<br>")
        .replace("\n", "<br>")
    )


def _parameter_table(spec: dict) -> list[str]:
    parameters = spec["analysis"].get("parameters", [])
    if not parameters:
        return ["The entrypoint accepts only the required `--input` path."]
    lines = [
        "| Parameter | Type | Allowed values | Required | Default | Scientific effect |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in parameters:
        default = "—" if item.get("required") else json.dumps(item.get("default"))
        allowed = ", ".join(f"`{json.dumps(value)}`" for value in item.get("choices", [])) or "declared type"
        cells = [
            f"`{item['name']}` / `{item['flag']}`",
            item["type"],
            allowed,
            str(item["required"]).lower(),
            f"`{default}`",
            item["description"],
        ]
        lines.append("| " + " | ".join(_table_cell(cell) for cell in cells) + " |")
    return lines


def _output_table(spec: dict) -> list[str]:
    lines = ["| Output | Kind | Stage |", "| --- | --- | --- |"]
    for output in skill_spec.all_outputs(spec):
        cells = [f"`{output['path']}`", output["kind"], output.get("stage", "analysis")]
        lines.append("| " + " | ".join(_table_cell(cell) for cell in cells) + " |")
    return lines


def _execute_command(parameters: list[dict]) -> list[str]:
    """The execute call, followed by every parameter that declares an example value."""
    examples = [
        f"  --param {item['name']}={shlex.quote(json.dumps(item['example']))}"
        for item in parameters
        if "example" in item
    ]
    command = "python3 \"$SKILL_DIR/scripts/run_skill.py\" execute --input \"$INPUT\""
    if not examples:
        return [command]
    return [f"{command} \\", *[f"{line} \\" for line in examples[:-1]], examples[-1]]


def render_skill(spec: dict) -> str:
    entrypoint = spec["analysis"]["entrypoint"]
    outputs = _output_table(spec)
    report = spec["presentation_profile"] == "scientific-report"
    lines = [
        "---",
        f"id: {_quote(spec['id'])}",
        f"name: {_quote(spec['slug'])}",
        f"description: {_quote(spec['description'])}",
        f"category: {_quote(spec['category'])}",
        f"visibility: {_quote(spec['visibility'])}",
        f"starting-prompt: {_quote(spec['starting_prompt'])}",
        "---",
        "",
        "<!-- spec: phylo-analysis-spec/1 -->",
        "",
        f"# {spec['slug'].replace('-', ' ').title()}",
        "",
        "## When to use",
        "",
        spec["description"],
        "",
        "## Scope",
        "",
        spec["objective"],
        "",
        "## Inputs and prerequisites",
        "",
        spec["input"]["summary"],
        "",
        "Requirements:",
        "",
        *[f"- {item}" for item in spec["input"]["requirements"]],
        "",
        "Reject the input without success artifacts when:",
        "",
        *[f"- {item}" for item in spec["input"]["reject_if"]],
        "",
        "Use the printed **Skill Directory** as `SKILL_DIR`. Do not edit the installed package.",
        "Run once in a clean results directory; do not repair or retry in place.",
        "",
        "## Analysis workflow",
        "",
        "The canonical runner invokes one entrypoint, which performs these scientific steps in order:",
        "",
        *[f"{index}. {step}" for index, step in enumerate(spec["analysis"]["workflow"], 1)],
        "",
        "## Runtime interface",
        "",
        f"The only executable analysis entrypoint is `{entrypoint}`. Invoke it only through",
        "`scripts/run_skill.py`; smoke validation and applications use this same path.",
        "Outside the domain process, treat packaged Python modules as CLI-only implementation files.",
        "Do not import them for inspection or presentation: imports can create cache files and",
        "invalidate the frozen package. Use canonical finalization or independent standard tools.",
        "",
        *_parameter_table(spec),
        "",
        "Map user-requested variations only to these declared parameters. A bounded method or tool",
        "choice remains an internal implementation detail of the one entrypoint. If the requested",
        "deviation is not declared here or would change inputs, workflow semantics, or output meaning,",
        "stop and request a new package revision rather than bypassing the runner or editing the package.",
        "",
        "## Scientific caveats and limitations",
        "",
        *[f"- **{item['kind'].replace('-', ' ').title()}:** {item['text']}" for item in spec["caveats"]],
        "",
        "Source, software, and licensing details are recorded in `DATA_SOURCES.md`.",
    ]
    guidance = spec.get("guidance", [])
    if guidance:
        lines.extend([
            "",
            "## Additional guidance",
            "",
            *[f"- Read [`{item['path']}`]({item['path']}) when {item['use_when']}." for item in guidance],
        ])
    lines.extend([
        "",
        "## Outputs",
        "",
        *outputs,
        "",
        "## Execute",
        "",
        "Resolve the actual user input. Then run the canonical runner, adding one",
        "`--param name=<JSON value>` argument for each non-default parameter:",
        "",
        "```bash",
        *_execute_command(spec["analysis"]["parameters"]),
        "```",
        "",
        "The runner verifies the frozen package, constructs the entrypoint command, binds the actual",
        "input and parameter values, rejects dirty output paths, and hashes every analysis output.",
    ])
    if report:
        lines.extend([
            "",
            "## Build the scientific report",
            "",
            "Read the selected report provider. Default to `pdf-report-generation`; use another",
            "compatible provider only when the user explicitly requested it. Create:",
            "",
            "- `report_facts.json`, including `source_outputs` copied exactly from the canonical",
            "  runner's analysis-output path/hash map;",
            "- `infographic.png` as the opening visual;",
            f"- `report_{spec['slug']}.pdf` as the reader-facing report.",
            "",
            "Styling may change presentation but never the canonical analysis artifacts or facts.",
            "Subjective scientific and visual quality remains an external review responsibility.",
        ])
    lines.extend([
        "",
        "## Finalize and verify",
        "",
        "```bash",
        "python3 \"$SKILL_DIR/scripts/run_skill.py\" finalize",
        "python3 \"$SKILL_DIR/scripts/run_skill.py\" verify-receipt",
        "```",
        "",
        "A terminal pass is valid only while the frozen package, entrypoint, input, and every declared",
        "output retain the hashes recorded in the single `run_receipt.json`.",
        "",
        "Return the requested scientific artifacts and a concise result summary. Keep package and",
        "receipt internals out of the normal user-facing response.",
        "",
    ])
    return "\n".join(lines)


def render_sources(spec: dict) -> str:
    lines = ["# Data sources", ""]
    sources = spec.get("sources", [])
    if not sources:
        lines.append("No external source is required by the package contract.")
    else:
        lines.extend(["| Source | Use | Reference |", "| --- | --- | --- |"])
        for source in sources:
            cells = [
                source.get("name", ""),
                source.get("use", ""),
                source.get("reference", ""),
            ]
            lines.append("| " + " | ".join(_table_cell(cell) for cell in cells) + " |")
    return "\n".join(lines) + "\n"


def _python_stub(spec: dict) -> str:
    parameters = spec["analysis"].get("parameters", [])
    argument_lines = []
    for item in parameters:
        kind = {"string": "str", "integer": "int", "number": "float"}.get(item["type"])
        if item["type"] == "boolean":
            argument_lines.append(f"    parser.add_argument({item['flag']!r}, action='store_true')")
        else:
            required = ", required=True" if item["required"] else f", default={item.get('default')!r}"
            choices = f", choices={item['choices']!r}" if item.get("choices") is not None else ""
            argument_lines.append(f"    parser.add_argument({item['flag']!r}, type={kind}{required}{choices})")
    arguments = "\n".join(argument_lines)
    return f'''#!/usr/bin/env python3
"""{spec["analysis"].get("purpose", "Generated analysis entrypoint")}"""

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
{arguments}
    _args = parser.parse_args()
    raise NotImplementedError("TODO(author): implement validated analysis and every declared output")


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _r_stub(spec: dict) -> str:
    return f'''#!/usr/bin/env Rscript
# {spec["analysis"].get("purpose", "Generated analysis entrypoint")}
stop("TODO(author): implement --input, declared parameters, and every output", call. = FALSE)
'''


def write_package(spec: dict, destination: pathlib.Path) -> pathlib.Path:
    package = destination / spec["slug"]
    if package.exists():
        raise FileExistsError(f"refusing to overwrite existing package: {package}")
    (package / "scripts").mkdir(parents=True)
    (package / "SKILL.md").write_text(render_skill(spec), encoding="utf-8")
    (package / "skill_spec.json").write_text(skill_spec.canonical_json(spec), encoding="utf-8")
    (package / "DATA_SOURCES.md").write_text(render_sources(spec), encoding="utf-8")
    shutil.copyfile(TEMPLATES / "run_skill.py", package / "scripts" / "run_skill.py")
    if spec["presentation_profile"] == "scientific-report":
        shutil.copyfile(TEMPLATES / "report_qc.py", package / "scripts" / "report_qc.py")
    entrypoint = package / spec["analysis"]["entrypoint"]
    entrypoint.parent.mkdir(parents=True, exist_ok=True)
    content = _python_stub(spec) if spec["analysis"]["language"] == "python" else _r_stub(spec)
    entrypoint.write_text(content, encoding="utf-8")
    return package


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--spec", type=pathlib.Path)
    source.add_argument("--example", choices=tuple(EXAMPLES))
    parser.add_argument("--dest", default="/mnt/results/skills", type=pathlib.Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.example:
        print(EXAMPLES[args.example].read_text(encoding="utf-8"), end="")
        return 0
    try:
        spec = skill_spec.strict_json(args.spec.expanduser())
    except (OSError, ValueError) as exc:
        parser.error(f"cannot load spec: {exc}")
    errors = skill_spec.validate_spec(spec)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(skill_spec.canonical_json(spec), end="")
        return 0
    destination = args.dest.expanduser().resolve()
    results = pathlib.Path(os.environ.get("BIOMNI_RESULTS", "/mnt/results")).expanduser().resolve()
    try:
        destination.relative_to(results)
    except ValueError:
        parser.error(f"destination must stay beneath results: {results}")
    try:
        package = write_package(spec, destination)
    except (FileExistsError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(package)
    print("next: implement the entrypoint, inspect the package, then run finalize_skill.py once")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
