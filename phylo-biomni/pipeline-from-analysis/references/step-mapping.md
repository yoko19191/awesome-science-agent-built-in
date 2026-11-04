# Step mapping, flag groups, plan.json and the approval gate

## From cells to steps

A **step** is one tool invocation or one coherent block of custom code that turns inputs into outputs.
- Group consecutive kept cells that operate on the same files into one step.
- Split a cell that runs two tools on different data into two steps.
- Split any block of custom code at every stage an nf-core module covers, even when the session ran it all in one cell. For example, one R cell doing tximport → DESeq2 → shrinkage → plots becomes `tximeta/tximport` → `deseq2/differential` (its options cover the contrast and shrinkage) → a small local module for anything left, such as plots. Only the part no module covers becomes a local module, and it reuses an existing image (see pipeline-module-author 2b).
- `possibly_state` cells may define in-memory values (thresholds, filtered objects) that kept cells use, or wrote files a kept cell uses through a path the script couldn't match. Read them. Either turn their values into `params.*`, or fold their code into the step that uses them.
- `unresolved_names` lists names a kept python/R cell uses that no earlier cell visibly assigns (e.g. `coldata` built in a cell that failed, or a column name in a formula). Find where each comes from: it becomes a samplesheet column, a `params.*`, an input file, or an open question at the gate.
- Kept cells linked only by a folder (e.g. `salmon -o /mnt/results/salmon/$s`, later read as `file.path('/mnt/results/salmon', …)`) usually mean a per-sample output collected by a later step: model it as a channel of per-sample outputs, not a shared folder.
- Every `hardcoded_paths` entry must become an input (samplesheet column or `params.*`) or a published output. None may stay in pipeline code.

## Choosing a component (in this order)

1. **nf-core subworkflow** covering a run of consecutive steps exactly (`nfcore_index.py search ... --kind subworkflow`).
2. **nf-core module** for the tool (`nfcore_index.py search`, then `show` for inputs/outputs/flags).
3. **Local module** wrapping the session code (`scaffold_local_module.py`).

`search` asks the Nextflow Registry first; `"source": "offline"` means it answered from the shipped index (the `reason` says why), which is fine. Search both modules and subworkflows before the gate. For each nf-core component you choose, run `nfcore_index.py freshness <name> --save /workspace/nfcore-latest`. If it reports `newer_available` or `not_in_index`, take inputs from its `component` and flags from `module_flags.py --main-nf <component.saved_to>/main.nf` and record `nf-core/modules@<registry_sha>` in that row's provenance `note` (modules.json also records it); `current`, `pinned_newer` or `unknown` means use the pinned index entry, and flag an `unknown` at the gate; `removed_upstream` means pick another component. See pipeline-module-author's `references/offline-and-registry.md`.

When a module exists but its version differs from the session's, still use the module. Record both versions in the plan's provenance table, and give the reason in the note column. If `show` or `freshness` reports `deprecated: true` or `optional_input_hazards`, resolve that before showing the approval gate; never carry the affected component into an approved plan.

## Flag groups (nf-core steps)

Run `module_flags.py --name <module> --command "<session command>"` (from pipeline-module-author; it reads the offline index, so it works before the repo exists). For a `newer_available` or `not_in_index` module, pass `--main-nf <component.saved_to>/main.nf` instead of `--name`. Check its output:
- `already_set` flags are dropped; `set_by_module_as` shows when the session used an alias (e.g. salmon `-p` = the module's `--threads`).
- `unparsed` lists positional tokens attached to no flag (input files, or a value the parser didn't expect). Every one must end up somewhere.
- Aliases, boolean flags and multi-value flags are known only for common tools (salmon, fastp, samtools, STAR, bwa, fastqc, trim_galore, featureCounts, hisat2, bowtie2, multiqc). For any other tool, check `<tool> --help` (or its docs) for short/long spellings of the flags the module sets and for boolean flags that may have swallowed the next token.

Then assign every flag to exactly one group:

| Group | Meaning | Goes to |
|---|---|---|
| structural | file or `val` declared in the module's `input:` | workflow wiring |
| already set | the module's script already passes it | dropped; conflicts noted |
| fixed | a tool option nobody would tune | `ext.args` literal |
| tunable | thresholds, library type, strandedness, species | `params.*` default = session value, `ext.args` closure |

## plan.json (input to compose.py and render_configs.py)

```json
{"name": "rnaseq-from-session", "description": "…", "version": "0.1.0",
 "params": {"input": null, "outdir": "results", "salmon_libtype": "A"},
 "processes": [{"name": "FASTP", "selector": ".*:FASTP", "ext_args": "--length_required ${params.min_len}",
                "ext_args_closure": true, "ext_args2": null, "ext_prefix": null,
                "publish_subdir": "fastp", "publish_pattern": "*.{json,html}"}],
 "provenance": [{"process": "FASTP", "component": "nf-core/fastp", "cells": [1],
                 "session_version": "0.23.4", "pipeline_version": "1.3.6", "note": ""}],
 "resources": {"cpus": 8, "memory_gb": 32, "time_hours": 12, "gpu": false,
               "gpu_count": 0, "gpu_process_labels": []}}
```

- `ext_args_closure: true` when the value contains `${...}`.

Read-group arguments must preserve shell escapes as one argument. Use the nf-core
convention `-R '@RG\tID:${meta.id}\tSM:${meta.id}'` (single quotes); an unquoted
backslash escape is rejected before HPC.
- `ext_prefix` is a raw Groovy expression, e.g. `"\"${meta.id}.trimmed\""`.
- `params` must include `input` and `outdir`.

`compose.py` also reads `components` (optional, so an older plan still renders) and `ext_args_allow`:

```json
{"components": [
   {"kind": "nf-core", "name": "salmon/quant", "type": "module"},
   {"kind": "nf-core", "name": "fastq_trim_fastp_fastqc", "type": "subworkflow", "sha": "<registry_sha>"},
   {"kind": "local", "name": "deseq2_plots", "tool": "deseq2",
    "version_cmd": "Rscript --version", "language": "r",
    "code": "cells_9_10.R", "container": "<URI from find_container.py>",
    "conda": ["bioconda::bioconductor-deseq2=1.42.0"], "inputs": ["results"],
    "outputs": {"plots": "*.pdf"}, "label": "process_single", "cells": [9, 10], "description": "…"}],
 "ext_args_allow": []}
```

- nf-core: `type` defaults to `module`; `sha` only for `newer_available` / `not_in_index` (the freshness `registry_sha`). List a subworkflow once; its modules come with it.
- `session_inputs`: the session's input file names (from extract). `gz_ok`: names of `.gz` inputs whose consumer reads gzip (its `nfcore_index.py show` input pattern ends in `.gz`). `compose.py --check-plan` fails on any other non-FASTQ `.gz` input unless the plan has a gunzip/decompress component; run it before the gate.
- At the gate, list each selected nf-core module/subworkflow's effective defaults and script-added flags that differ from the session, with a decision to keep, set to the session value through `ext.args`/`params`, or document expected drift. Include data-dependent defaults such as `vst_nsub`, shrinkage, and adapter detection when present.
- For `deseq2/differential` with `vs_method=vst`, compute `vst_eligible_rows` from the session-equivalent counts input (rows with mean normalized count > 5) before approval and record it in `plan.json` (top-level or `data_checks.deseq2`). The total row count is not a substitute (fewer rows pass the filter), so if you can't compute it, use `--vs_method rlog`. The pipeline's quantification can differ from the session's (a decoy-aware index, other tool versions), so its count can be lower: `compose.py` fails when `vst_nsub` is above half the recorded count or the count is missing. For small test data prefer `--vs_method rlog`; do not defer this check to HPC.
- local: the fields are `scaffold_local_module.py`'s flags. `code` is a path relative to `plan.json` (write the step's session code next to it). `version_cmd` must contain no quotes, pipes or command chaining because it is rendered inside Nextflow's single-quoted `eval()`; use `Rscript --version`, `python3 --version`, `bash --version`, or a helper script. `language` is `r`, `python` or `bash`.
- `dependencies_accepted` may list an explicitly gate-accepted import, normalized conda name, or `module:import`; unresolved direct or known runtime dependencies otherwise fail composition. The extracted dependencies are checked against the chosen image/environment, and unresolved ones block compose unless accepted. Picking an image that covers them is the agent's job via `find_container.py`.
- When session custom code and an nf-core candidate behave differently on the session's data, prefer a local wrapper preserving the session code, or flag the difference and its data evidence at the approval gate.
- `ext_args_allow`: flags `check_ext_args.py --allow` may accept (only after you confirmed the module sets them conditionally).
- `processes[].container`: an image for that process, rendered as a `withName` `container` override. `compose.py` sets it itself for Seqera-hosted images it can replace (the index's biocontainers replacement, else the exact-version quay.io build); set it only when the user chose another image at the gate. It wins over compose's choice.
- `seqera_accepted`: accepted as a legacy plan field and ignored. Unresolved upstream images are retained and reported in `retained_upstream_images`.

For every edge that passes a serialized object between processes, record it before the
gate in `serialized_objects`: `pattern` (for example `*.h5ad`, `*.rds`, or `*.pkl`),
`producer`, `consumers`, and the serializing `library` (`anndata`, `R`, `pickle`, or
another explicitly identified library). Add `runtime_versions` to each participating
process, keyed by that library. Compose compares major.minor versions and blocks a
known mismatch; if the versions cannot be established, keep the steps on one image or
raise that uncertainty as an open gate item. This applies to any serialized object
exchanged between steps, not only single-cell data.

## Approval gate: what to show

1. Step table:

| # | Step | Session cells | Component | Session version | Pipeline version | Flags: structural / dropped / ext.args / params |
|---|---|---|---|---|---|---|

2. The DAG as an indented list: step → step.
3. The samplesheet columns, and one example row built from the session's files.
4. New `params.*` with defaults.
5. **Validation level: ask the user to pick one** (it's asked once, here):

| Level | What runs | Rough time after the build |
|---|---|---|
| **Build only** | preflight, lint, ext.args check, `nextflow lint`; hand off the repo and zip | fastest (no extra run) |
| **Build + stub run** | also a local `-stub-run` that checks parsing and wiring, not bin-script parsing, module defaults, or real data behavior | a few minutes |
| **Full validation** | stub run, one HPC run on the approved data, concordance report | stub run + the HPC run (often an hour or more, depending on data and queue) |

   Count the processes in the approved plan (one per module or local-process row; for a subworkflow row, count the processes it brings in instead of the row itself). With **8 or more**, mark **Build + stub run** as recommended; with fewer, mark **Build only** as recommended and still offer the stub run. The user always chooses. Put the count and the reason in the question: a stub run executes the whole workflow with placeholder files, so it catches channel wiring, input-count and missing-file mistakes that `nextflow lint` cannot, and the more processes a pipeline has, the more places those mistakes can hide. Failed HPC runs are mostly of this kind, and each costs a queue wait plus a correction cycle, while a stub run takes a few minutes locally. If the user doesn't choose, run the recommended level, and ask about the HPC run in the handoff. For **Full validation**, also show the validation data (the full session data, or a named subset) and the expected HPC resources. For GPU stages, name the process label and show `gpu=true`, `gpu_count`, and the matching `gpu_process_labels` in the resource object.
   Any component, flag, default, parameter, or wiring change after approval requires a concise diff and a second approval before composition.
6. Open questions: hard-coded paths, `possibly_state` cells, session steps that produced a deliverable but aren't in the trace's `kept` cells (e.g. run through the Bash tool), `container-only` fallbacks, conflicts with flags the module already sets, and steps that can't be containerised.
7. Containers: resolve in order using a biocontainers replacement, an exact-version quay.io build, a usable recorded mirror, then retain the pinned upstream image. Record retained images in `retained_upstream_images`; do not turn them into gate questions. Never call the Seqera Containers or Wave API to build or fetch a new image.
