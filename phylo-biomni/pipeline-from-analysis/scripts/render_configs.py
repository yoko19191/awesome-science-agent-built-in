#!/usr/bin/env python3
"""Render the pipeline's config layers and README from plan.json.

  render_configs.py plan.json --repo /workspace/<name>

Submitted layer: nextflow.config, conf/base.config, conf/modules.config (no
platform-owned settings anywhere, comments included). Portable layer:
conf/portable.config (never submitted, never included).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BASE_CONFIG = """process {
    cpus   = { 1     * task.attempt }
    memory = { 6.GB  * task.attempt }
    time   = { 4.h   * task.attempt }

    withLabel: process_single {
        cpus   = { 1 }
        memory = { 6.GB * task.attempt }
        time   = { 4.h  * task.attempt }
    }
    withLabel: process_low {
        cpus   = { 2     * task.attempt }
        memory = { 12.GB * task.attempt }
        time   = { 4.h   * task.attempt }
    }
    withLabel: process_medium {
        cpus   = { 6     * task.attempt }
        memory = { 36.GB * task.attempt }
        time   = { 8.h   * task.attempt }
    }
    withLabel: process_high {
        cpus   = { 12    * task.attempt }
        memory = { 72.GB * task.attempt }
        time   = { 16.h  * task.attempt }
    }
    withLabel: process_long {
        time   = { 20.h  * task.attempt }
    }
    withLabel: process_high_memory {
        memory = { 200.GB * task.attempt }
    }
}
"""

PORTABLE_CONFIG = """// For running this pipeline outside Biomni. Never submitted to Biomni HPC.
// Use: nextflow run . -profile docker -c conf/portable.config --input samplesheet.csv --outdir results
process {
    errorStrategy = { task.exitStatus in ((130..145) + 104 + (175..177)) ? 'retry' : 'finish' }
    maxRetries    = 1
    resourceLimits = [ cpus: 8, memory: 32.GB, time: 24.h ]   // set to your machine or cluster
}

// Cluster example (uncomment and edit):
// process.executor = 'slurm'
// process.queue    = 'standard'
"""

PROFILES = """profiles {
    docker {
        docker.enabled    = true
        docker.runOptions = '-u $(id -u):$(id -g)'
    }
    singularity {
        singularity.enabled    = true
        singularity.autoMounts = true
    }
    apptainer {
        apptainer.enabled    = true
        apptainer.autoMounts = true
    }
}
"""

DEFAULT_PUBLISH = """    publishDir = [
        path: { "${params.outdir}/${task.process.tokenize(':')[-1].tokenize('_')[0].toLowerCase()}" },
        mode: 'copy',
        saveAs: { filename -> filename.equals('versions.yml') ? null : filename }
    ]
"""


def groovy(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(groovy(v) for v in value) + "]"
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _nextflow_config(plan: dict) -> str:
    width = max(len(k) for k in plan["params"])
    params = "\n".join(f"    {k.ljust(width)} = {groovy(v)}" for k, v in plan["params"].items())
    return (f"manifest {{\n    name            = {groovy(plan['name'])}\n"
            f"    description     = {groovy(plan.get('description', ''))}\n"
            f"    version         = {groovy(plan.get('version', '0.1.0'))}\n"
            f"    nextflowVersion = '!>=26.04.0'\n}}\n\n"
            f"params {{\n{params}\n}}\n\n"
            "includeConfig 'conf/base.config'\nincludeConfig 'conf/modules.config'\n\n" + PROFILES)


def _gstring(value: str) -> str:
    """A Groovy double-quoted string that keeps ${...} interpolation."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _args_line(key: str, value: str, closure: bool) -> str:
    return f"        ext.{key} = {{ {_gstring(value)} }}\n" if closure else f"        ext.{key} = {groovy(value)}\n"


def _modules_config(plan: dict) -> str:
    out = ["process {\n", DEFAULT_PUBLISH]
    for proc in plan["processes"]:
        out.append(f"\n    withName: '{proc.get('selector') or proc['name']}' {{\n")
        closure = bool(proc.get("ext_args_closure"))
        for key in ("args", "args2", "args3"):
            if proc.get(f"ext_{key}"):
                out.append(_args_line(key, proc[f"ext_{key}"], closure))
        if proc.get("ext_prefix"):
            out.append(f"        ext.prefix = {{ {proc['ext_prefix']} }}\n")
        if proc.get("container"):  # replaces a Seqera-hosted image; vendored files are never edited
            out.append(f"        container = {groovy(proc['container'])}\n")
        if proc.get("publish_subdir"):
            pattern = f",\n            pattern: {groovy(proc['publish_pattern'])}" if proc.get("publish_pattern") else ""
            out.append("        publishDir = [\n"
                       f"            path: {{ \"${{params.outdir}}/{proc['publish_subdir']}\" }},\n"
                       f"            mode: 'copy'{pattern}\n        ]\n")
        out.append("    }\n")
    out.append("}\n")
    return "".join(out)


def _readme(plan: dict) -> str:
    rows = "\n".join(
        f"| {p['process']} | {p['component']} | {', '.join(map(str, p.get('cells', [])))} | "
        f"{p.get('session_version', '')} | {p.get('pipeline_version', '')} | {p.get('note', '')} |"
        for p in plan.get("provenance", []))
    params = "\n".join(f"| `--{k}` | `{json.dumps(v)}` |" for k, v in plan["params"].items())
    return f"""# {plan['name']}

{plan.get('description', '')}

Generated from a Biomni session by the `pipeline-from-analysis` skill.

## Run on Biomni HPC

Submit with `hpc_run_pipeline(pipeline_files=...)`, using every file except `conf/portable.config`.

The submitted `nextflow.config` includes `conf/base.config` and `conf/modules.config`.
`conf/base.config` holds shared process defaults, while `conf/modules.config` holds module
arguments, publishing rules and container overrides. `conf/portable.config` is separate: it
holds retry, resource and executor settings for running elsewhere and is never submitted or
included by the pipeline.

## Run it elsewhere

```bash
nextflow run . -profile docker -c conf/portable.config --input samplesheet.csv --outdir results
```

Edit `conf/portable.config` for your machine or cluster (resource limits, retries, scheduler).
The zip keeps `bin/` scripts executable; if you copied the folder another way, run `chmod +x bin/*` first.
Profiles: `docker`, `singularity`, `apptainer` run the pinned container images, the software that was
tested. Some steps may retain the module's pinned upstream image when no portable replacement is
available. Requires Nextflow 26.04 or later.

## Parameters

| Parameter | Default |
|---|---|
{params}

## Provenance

Each process, the session cells it reproduces, and tool versions in the session vs the pipeline container.

| Process | Component | Session cells | Session version | Pipeline version | Note |
|---|---|---|---|---|---|
{rows}

## Attribution

Components under `modules/nf-core/` and `subworkflows/nf-core/` come from
[nf-core/modules](https://github.com/nf-core/modules), at the commits listed in `modules.json`.
"""


def render(plan: dict) -> dict[str, str]:
    for key in ("input", "outdir"):
        if key not in plan["params"]:
            raise ValueError(f"plan params must include {key!r}")
    return {"nextflow.config": _nextflow_config(plan), "conf/base.config": BASE_CONFIG,
            "conf/modules.config": _modules_config(plan), "conf/portable.config": PORTABLE_CONFIG,
            "README.md": _readme(plan)}


def write(repo: Path, plan: dict) -> list[Path]:
    written = []
    for rel, content in render(plan).items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    for path in write(args.repo, json.loads(args.plan.read_text())):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
