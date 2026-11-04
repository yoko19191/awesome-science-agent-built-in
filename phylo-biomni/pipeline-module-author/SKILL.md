---
name: pipeline-module-author
description: 'Use to add one pipeline step to a Nextflow repo the nf-core way: vendor
  an nf-core module or subworkflow from an offline index (GitHub, then a shipped snapshot
  if present, then container-only fallback), or scaffold and lint an nf-core-convention
  local module that wraps session code. Also sorts a tool command''s flags into structural
  inputs, flags the module already sets, and ext.args. Called by pipeline-from-analysis.'
category: data_analysis
visibility: public
starting-prompt: Add the nf-core salmon/quant module to my Nextflow pipeline with
  the flags this session used.
---
# Author one Nextflow component the nf-core way

Use the printed **Skill Directory** as `SKILL_DIR`. `REPO` is the pipeline repo's local build, e.g. `/workspace/<name>`. Never build on `/mnt/results`: it is an S3-backed mount where `chmod` is silently ignored, so `bin/` scripts can't be made executable there.
If the user points at a pipeline under `/mnt` (e.g. a staged copy in `/mnt/results/<name>`), copy it to local scratch first and work on the copy: `mkdir -p "$REPO" && cp -R /mnt/results/<name>/. "$REPO/" && if [ -d "$REPO/bin" ]; then chmod +x "$REPO"/bin/*; fi`. Re-stage it with pipeline-hpc-validate's `submission.py --stage` when you're done.
Read `references/nf-core-module-conventions.md` before writing or wiring any module.
The Nextflow Registry is used for discovery and freshness only; the shipped offline index is the discovery fallback; vendoring needs GitHub (or a shipped snapshot, if the package includes one). Read `references/offline-and-registry.md` for what each source provides and how to run fully offline (`NEXTFLOW_SKILLS_OFFLINE=1`).

## 1. Search first; never guess parameters

```bash
python3 "$SKILL_DIR/scripts/nfcore_index.py" search "salmon quant" --kind module
python3 "$SKILL_DIR/scripts/nfcore_index.py" show salmon/quant
python3 "$SKILL_DIR/scripts/nfcore_index.py" freshness salmon/quant --save /workspace/nfcore-latest
```

- `search` asks the Registry first. `"source": "offline"` means it answered from the offline index instead, and `reason` says why (offline mode, `nextflow` missing, Registry unreachable, no match). Either answer is valid. A Registry hit with `"in_index": false` is missing from the offline index: run `freshness` on it before using it.
- `show` prints the module's inputs, outputs, containers, conda pins and the flags its script already sets, from the offline index. `container_replacements` (when present) lists recorded deployment replacements for a Seqera-hosted image: a `biocontainers` entry is usable anywhere; a `mirror` entry lives in a deployment-specific ECR that a custom bundle cannot name.
- `show` and `freshness` also report `deprecated: true` and `optional_input_hazards`. Resolve either before the approval gate; do not carry a deprecated module or an unsafe empty optional input into the approved plan.
- `freshness` compares the offline index's pinned copy with the commit the Registry publishes, by file contents. Act on its `status`:
  - **`current`**, **`pinned_newer`** (the Registry's commit is older than the pin) or **`unknown`** (offline, a source was unreachable, or the commit order is unclear): use `show` and the pinned commit. Flag `unknown` at the approval gate.
  - **`removed_upstream`** (not in the index, and the Registry commit is behind the pin, so nf-core removed or renamed it): don't vendor it. Search for its replacement.
  - **`newer_available`** (the files differ and the Registry commit is ahead of the pin) or **`not_in_index`** (added after the pin): use the returned `component` in place of `show` (same fields, read from the files at `registry_sha`), and vendor with `--sha <registry_sha>`. Record the commit in the plan. These are the only statuses that lead to `--sha`.

Prefer a subworkflow (`--kind subworkflow`) when one covers several consecutive steps exactly.

When pipeline-from-analysis builds a whole plan, its `compose.py` runs 2a, 2b (scaffold only) and 3 for every component in one command, using these scripts from this skill's directory. The steps below are the per-component path, and the fallback.

## 2a. The step has an nf-core component → vendor it

```bash
python3 "$SKILL_DIR/scripts/vendor_nfcore.py" --repo "$REPO" --name salmon/quant --kind module [--sha <registry_sha>]
```

Without `--sha` it vendors the index's pinned commit. `modules.json` records the commit each component was actually vendored at; a subworkflow's modules come from the subworkflow's commit.

Check the `source` field of each result:

- **`github` or `snapshot`:** the files are in `modules/nf-core/...` and recorded in `modules.json`. Include the module as `include { SALMON_QUANT } from '../modules/nf-core/salmon/quant/main'`, using a `./` or `../` relative path, never `from 'nf-core/...'`.
  - A result with `requested_sha` got the pinned snapshot copy because GitHub couldn't serve that commit (or offline mode is on): take inputs and flags from `show`, not from the freshness `component`.
- **`container-only`** (exit 3): the source can't be reached. Go to 2b and reuse the returned nf-core `containers.docker` URI (if it is Seqera-hosted, the index's `biocontainers` replacement instead) and the index's `conda` pins (for a `not_in_index` module, the freshness `component`'s), then **flag this in the plan**.
  - For a subworkflow, vendor its modules one by one instead.

Then sort the session's flags for this tool. During planning (no repo yet) read the module from the offline index with `--name`; for a `newer_available` or `not_in_index` module pass `--main-nf` with the file under the `component.saved_to` folder from `freshness --save`; once it's vendored you can pass `--main-nf "$REPO/modules/nf-core/salmon/quant/main.nf"`:

```bash
python3 "$SKILL_DIR/scripts/module_flags.py" --name salmon/quant \
  --command "salmon quant -i idx -l A -1 r1.fq -2 r2.fq -p 8 --validateMappings -o out"
```

The script separates flags the module **already sets** (drop them, and record any conflict) from **remaining** flags:

- `set_by_module_as` maps a session alias to the module's spelling (salmon `-p` → `--threads`, `-i` → `--index`).
- `unparsed` lists every positional token not attached to a flag. Account for each one (usually an input file).
- Aliases, boolean and multi-value flags are built in only for common tools. For any other tool, check `<tool> --help` for short/long spellings and boolean flags before trusting the split.
- A flag the module sets only when `ext.args` doesn't already contain it (look for `args.contains('--flag')` in its `main.nf`, e.g. STAR `--outSAMtype`) may go in `ext.args`; allow it with `check_ext_args.py --allow <flag>`.

You then decide for each remaining flag:

- **structural** (a file or `val` in the module's `input:` block, from `show` or the freshness `component`): wire it in the workflow;
- **fixed option:** a literal in `ext.args`;
- **user-tunable** (thresholds, library type, strandedness): a new `params.*` with the session's value as its default, read from an `ext.args` closure.

## 2b. No nf-core component → scaffold a local module

1. Save the session code for this step (from `steps.json`) to a file.
2. Pick an **existing** image, in this order. Building an image is a last resort.

   1. **An nf-core image from the offline index** whose conda pins cover every package the code loads, including known runtime dependencies (for example, `tximport` may read Salmon inferential replicates through `jsonlite`):

      ```bash
      python3 "$SKILL_DIR/scripts/find_container.py" --package deseq2 --package limma
      ```

      Use the first `covering` image, with the module's `conda` pins for `environment.yml`. Seqera-hosted images rank last and are marked `"seqera": true`: use its `replacement` (a digest-only quay.io image) when set, otherwise try 2b.2 before settling for it. To reuse the image of a module vendored at a newer commit (or saved by `freshness --save`), read its own pins with `find_container.py --module-dir <module folder>`. Pins list top-level packages only, so a package's own dependencies are in the image too (e.g. DESeq2 brings ggplot2): if the only missing package is such a dependency, the image covers it. `partial` shows the closest images and what each lacks; if one step's code needs packages no single image has, split the step so each part fits one image.
   2. **A quay.io/biocontainers image** for the package, when no nf-core image fits:

      ```bash
      python3 "$SKILL_DIR/scripts/find_container.py" --biocontainers bioconductor-deseq2 [--version 1.42.0]
      ```

      It lists `quay.io/biocontainers/<package>:<tag>` builds, newest first, each with its `manifest_digest` and the digest-only `pinned` URI; `--version` keeps only builds of that version. Pick the build closest to the session's version and use its `pinned` URI (AWS Batch rejects `name:tag@digest`). Biocontainers hold one package each, so split the step if the code needs more than one package that isn't a dependency of the first.
   3. **Otherwise ask the user**: drop the step, keep it as a documented manual step, provide an image URI, or (for a multi-package step) split the step. Treat image builds as unavailable unless the user explicitly says builds are enabled or a no-cost status probe reports enabled; function presence is not availability. Never put a build in the default plan. If the user says it is enabled and approves, build from the module's `environment.yml` (add `conda-forge::procps-ng`; Nextflow needs `ps`) with this `Dockerfile`, then take `image_uri` from `hpc_get_build_status`:

      ```dockerfile
      FROM mambaorg/micromamba:1.5.10
      COPY --chown=$MAMBA_USER:$MAMBA_USER environment.yml /tmp/environment.yml
      RUN micromamba install -y -n base -f /tmp/environment.yml && micromamba clean --all --yes
      ENV PATH=/opt/conda/bin:$PATH
      ```

   Prefer quay.io/biocontainers or the index's recorded replacement for local modules, and never call the Seqera Containers or Wave API to build or fetch a new image. A vendored nf-core module keeps its pinned upstream image in `main.nf` (never edit it): pipeline-from-analysis's `compose.py` overrides it in `conf/modules.config` when possible and reports any retained image. Custom bundles do not receive platform image overrides, so every retained upstream image must be usable by the target HPC environment. Never install packages when the task starts (`BiocManager::install`, `pip install` in the script).
   **Never leave a module without a container.**
3. Scaffold, then lint:

```bash
python3 "$SKILL_DIR/scripts/scaffold_local_module.py" --repo "$REPO" --name deseq2_contrast --tool deseq2 \
  --version-cmd 'Rscript --version' --language r \
  --code /tmp/cells_7_8.R --container <URI> --conda bioconda::bioconductor-deseq2=1.42.0 \
  --input counts --input samplesheet --output results='*.results.tsv' --label process_medium --cells 7,8
```

4. Keep the generated argument parser in `bin/<name>.<ext>` and put the session logic below it. The parser accepts `--prefix value` and one `--<input> value` per input, matching the scaffolded `main.nf`; it must not be replaced with `--flag=value` parsing or extra shebang arguments. The session logic writes outputs that match the `--output` globs. Version commands are quote-free and pipe-free because they are embedded in Nextflow's single-quoted `eval()`; use `Rscript --version`, `python3 --version`, `bash --version`, or a helper script for package-specific reporting.
5. `python3 "$SKILL_DIR/scripts/lint_module.py" "$REPO/modules/local/<name>"` must pass, then run `python3 "$SKILL_DIR/scripts/validate_local_scripts.py" "$REPO" --module-author-dir "$SKILL_DIR"`. The validator parses every local script, render-checks version commands, checks publish patterns, and closes direct imports against environment pins/index metadata. Missing interpreters are reported as `not_checkable`, never as a silent pass.

## 3. Check

- `python3 "$SKILL_DIR/scripts/check_ext_args.py" "$REPO"` must pass after `conf/modules.config` is written.
- `python3 "$SKILL_DIR/scripts/check_wiring.py" "$REPO" --json` must report no errors after wiring. It is static and heuristic: it does not evaluate closures or operators beyond its listed forms, and `unverified` means the stub run decides.
- Unless the user chose **Build only**, each new local module gets a stub run once it is wired in: from `REPO`, with the stub inputs and stub config kept outside the repo, as **pipeline-hpc-validate** step 1 describes (`NXF_VER=26.04.6 NXF_SYNTAX_PARSER=v1 nextflow run main.nf -stub-run -c <stub.config> ...`).
- Before HPC, each local module also gets a required data smoke run with `scripts/smoke_local_module.py`. Feed it the exact upstream output when cheaply reproducible, otherwise a session intermediate reshaped to the declared output. Record command status, table IDs, and a cheap invariant such as row count in `$REPORTS/local-module-runs.json`; a missing report is a gate warning.

## Critical Rules

1. Search before choosing a component; take inputs and flags from `show` (or the freshness `component` for the Registry commit), never from memory.
2. The Registry is for discovery and freshness only. Always vendor; never use Registry-managed includes (`from 'nf-core/x'`) and never run `nextflow module install` into the repo.
3. Never edit a vendored nf-core file in place.
4. Never pass a flag the module already sets; tool options go through `ext.args`.
5. Every local module passes `lint_module.py` and has an existing container: an nf-core image from `find_container.py`, then quay.io/biocontainers, then a recorded mirror, then the pinned upstream image. Custom image builds may be unavailable; never assume they can be used. Never call the Seqera Containers or Wave API, and never install packages at task start.
6. Flag every `container-only` fallback, every `unknown` freshness, every non-pinned commit and every version difference from the session in the plan.
7. With `NEXTFLOW_SKILLS_OFFLINE=1` (or `--offline`) nothing touches the network: search reads the offline index and vendoring uses a shipped snapshot if present, otherwise container-only. Never work around offline mode.

## Attribution

"Search first, never guess parameters" and the per-module validation idea are adapted from Seqera's `run-module` and `create-workflow` skills (nextflow-io/agent-skills, Apache-2.0). Vendored components come from nf-core/modules (MIT).
