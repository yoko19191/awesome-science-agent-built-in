---
name: pipeline-from-analysis
description: Use when a user wants to turn this analysis, notebook, or Biomni session
  into a reproducible or rerunnable Nextflow pipeline, including an nf-core-style
  pipeline. Reads the session trace, maps steps to nf-core components or local modules,
  stops for plan approval, composes a portable pipeline, validates it on Biomni HPC,
  and reports factual output concordance.
category: data_analysis
visibility: public
starting-prompt: Turn this analysis or notebook into a reproducible Nextflow pipeline
  I can rerun on new samples.
---
# Turn this session's analysis into a Nextflow pipeline

Use the printed **Skill Directory** as `SKILL_DIR`. Pick a short kebab-case `NAME`. Paths:

- `REPO=/workspace/$NAME`: the local build. Write, lint and stub-run the pipeline only here.
- `STAGED=/mnt/results/$NAME`: the copy submitted to HPC and handed to the user (step 6).
- `REPORTS=/workspace/$NAME-reports`: steps.json, plan.json and reports. Mirror whole report files to `/mnt/results` at handoff.

`/mnt/results` is an S3-backed mount: `chmod` is silently ignored, rename and symlinks fail, and `zip` can't write there directly, so building, `nextflow run` and `zip` fail there. If `REPO` is gone, rebuild it from `STAGED` (see pipeline-hpc-validate).
Read `references/step-mapping.md` before step 2.
This skill works with two others: **pipeline-module-author** (components) and **pipeline-hpc-validate** (validation). Load each when its step comes, and use its own printed Skill Directory: call them `MOD_DIR` and `VAL_DIR` below.

Whenever the workflow needs a user decision before continuing, call `AskUserQuestion` with structured choices and wait for the answer. Do not ask blocking questions as chat text.

Time each step with a local log: `python3 "$SKILL_DIR/scripts/timings.py" start <step> --reports "$REPORTS" --log "$REPORTS/timings.jsonl"` before it and `... end <step> ...` after it, for `extract`, `map`, `gate` (add `--wait` to its start: that's the user's time, not yours), and `compose`. The orchestrator owns the complete `validate` interval: start it immediately before step 5, keep it open across preflight, the stub run, staging/submission, HPC execution and any correction cycles, and end it after concordance and handoff. The handoff copies the complete local log and includes `timings.py summary`.

## 1. Extract

```bash
mkdir -p "$REPORTS"
python3 "$SKILL_DIR/scripts/extract_trace.py" /mnt/results/execution_trace/*.ipynb --transcript /mnt/results/execution_trace/transcript.jsonl --out "$REPORTS/steps.json"
```

- Confirm the final analysis deliverables with `AskUserQuestion`. Offer detected files and folders under `/mnt/results` that no later cell reads as choices, with `multiSelect: true` so the user can select multiple outputs. If candidates exceed the four-choice limit, split them across questions or collect selected paths as free text. Preserve every selected path when rerunning `extract_trace.py` with `--target`.
- Read every kept cell, every `possibly_state` cell, every `unresolved_names` entry and every `unmatched_targets` entry. Lineage is a heuristic (file paths, folders, and variables shared between python/R cells); check that no step the targets depend on was dropped.
- The notebook may not hold every step: the agent can run tools through its Bash tool, outside the notebook. `--transcript` adds Bash-tool commands as timestamped `bash` cells; cross-check `steps.json` against `NOTES.md` and the session's outputs. Any step that produced a deliverable (or an input to one) but isn't in `kept` is an open question at the gate; never drop it silently.
- If the user supplied scripts or notebooks instead, treat them the same way.

## 2. Map and write the checked plan

Group the kept cells into steps, and choose a component for each one using `references/step-mapping.md` (subworkflow → module → local). Use **pipeline-module-author** `nfcore_index.py search/show` for every choice (search asks the Nextflow Registry first and falls back to the offline index), then `nfcore_index.py freshness <name> --save /workspace/nfcore-latest` for each chosen nf-core component to decide its commit. Sort every nf-core step's command with `module_flags.py`: use `--name <module>` (the pinned index) for `current`, `pinned_newer` or `unknown`, and `--main-nf <component.saved_to>/main.nf` for `newer_available` or `not_in_index`. Neither needs a repo yet. Never pick a module or its inputs from memory.

After mapping, write `$REPORTS/plan.json` with its `components` list (schema in the reference), each local step's session code next to it (`code` is relative to `plan.json`), and the session's input file names in `session_inputs`. For DESeq2 VST, compute and record `data_checks.deseq2.vst_eligible_rows` before the gate. Run:

```bash
python3 "$SKILL_DIR/scripts/compose.py" "$REPORTS/plan.json" --check-plan
```

Fix every reported error and repeat until it is clean. A non-FASTQ `.gz` input needs nf-core `gunzip` (unless its consumer reads `.gz`: list it in `gz_ok`), and `vst_nsub` must leave a margin.

## 3. Approval gate

Containers are not a gate question: every step gets an existing image (biocontainers, then a recorded mirror, then
the module's pinned upstream image), chosen without asking. Only when no existing image can run a local step's code
does the gate offer: split the step, a user-supplied image URI, or drop the step. Treat custom image builds as
unavailable unless the user says builds are enabled; the presence of `hpc_build_image` is not availability, and a build
is never the default.
Show the user everything listed under "Approval gate" in `references/step-mapping.md`, including the validation-level choice (Build only / Build + stub run / Full validation).
Then call `AskUserQuestion` with separate questions for explicit plan approval (approve / request changes) and validation level (Build only / Build + stub run / Full validation), marking the recommended level and why as `references/step-mapping.md` describes. **STOP. Do not write the pipeline until the user explicitly approves.** Revise and re-present if asked.
If any component, flag, default, or wiring changes after approval, show the concise diff and stop for approval again before composing.

## 4. Compose

1. After approval, pick each local module's container with pipeline-module-author's `find_container.py` (its step 2b), then build everything in one command:

   ```bash
   python3 "$SKILL_DIR/scripts/compose.py" "$REPORTS/plan.json" --repo "$REPO" \
     --module-author-dir "$MOD_DIR" --validate-dir "$VAL_DIR" \
     --reports "$REPORTS" --timing-log "$REPORTS/timings.jsonl" > "$REPORTS/compose.json"
   ```

   It copies the template (first run only), vendors every nf-core component, scaffolds the local modules, replaces Seqera-hosted images, renders the configs and README, then runs `validate_local_scripts.py`, preflight, `lint_module.py` on `modules/local/*`, `check_ext_args.py` and the typed static wiring check. Read its JSON: `vendored[].source` (flag `container-only` in the plan; exit 3), `container_overrides` (replacements written to `conf/modules.config`), `retained_upstream_images` (images it couldn't replace, with the reason; informational), `checks` (exit 1 if any failed) and `next_steps`. The wiring check is conservative and heuristic: it does not evaluate closures or operators beyond its listed forms, and `unverified` means the stub run decides. It respects `NEXTFLOW_SKILLS_OFFLINE=1`; pass `--offline` to force offline mode (snapshot, then container-only).
2. Do the `next_steps`: keep the generated argument parser in each new `bin/` script, put the session logic below it, and wire the steps in `workflows/pipeline.nf` in plan order (includes with `../modules/...` relative paths, structural inputs passed explicitly). Adapt `subworkflows/local/samplesheet_fastq` if the analysis doesn't start from FASTQ.
   For each GPU step, give its process a GPU label (for example, `label 'process_gpu'`) and record that label in the plan's GPU resource list. The platform adds the Batch accelerator and GPU queue for labels listed in the submitted `resources.gpu_process_labels`; a process `accelerator` directive alone does not choose the queue.
3. Rerun the same `compose.py` command after wiring `workflows/pipeline.nf`. It never overwrites an existing module or `bin/` script and doesn't re-fetch vendored components; it re-renders and re-checks. Repeat until it exits 0 with no `next_steps`.

Keep the template's `modules/local/software_versions` and its `SOFTWARE_VERSIONS(channel.topic('versions')...)` wiring exactly as shipped. It reads the versions topic, so giving it a meta input or a versions output deadlocks the run.

Fallback, if `compose.py` can't be used: copy `templates/pipeline/.` into `REPO`, then run the same scripts one by one: `vendor_nfcore.py` and `scaffold_local_module.py` per component (pipeline-module-author), `render_configs.py plan.json --repo "$REPO"`, `lint_module.py`, `check_ext_args.py`, `check_wiring.py` and `preflight.py`.

Never add platform-owned settings (executor, queue, workDir, errorStrategy, maxRetries, resourceLimits, wave, fusion, …) to `nextflow.config` or `conf/base.config` / `conf/modules.config`, not even in comments. They belong only in `conf/portable.config`.

Before HPC, give every local module a data run: `python3 "$MOD_DIR/scripts/smoke_local_module.py"` on the exact
upstream output (or a session intermediate reshaped to that output), recorded in `$REPORTS/local-module-runs.json`
with a cheap invariant such as row count. Compose lists a local module without one as a warning; the stub run never
executes scripts, so this is the only check that runs them on data.

Also run the final analysis steps implemented by vendored nf-core script/template modules on
session-equivalent intermediate data when the sandbox has the required runtime packages. Render the
module template with the pipeline's effective `ext.args`/`params`, run it, and record the result and
cheap invariants in the reports. This applies to R/Python templates such as
`deseq2/differential`; a successful stub run is not evidence that their real data behavior works.

## 5. Validate (only the level the user chose)

```bash
python3 "$SKILL_DIR/scripts/timings.py" start validate --reports "$REPORTS" --log "$REPORTS/timings.jsonl"
```

Load **pipeline-hpc-validate** and run exactly the level chosen at the gate (if none was chosen, the one recommended there). Never go past it without asking.

- **Build only:** its step 1 without the stub run (preflight, then `nextflow lint`).
- **Build + stub run:** its step 1 (preflight, then the time-boxed stub run; no `nextflow lint`).
- **Full validation:** step 1, stage to `STAGED`, one HPC run on the approved data with `hpc_run_pipeline(pipeline_files=…)` (HPC runs custom repos; `hpc_search_pipelines` and `hpc_get_pipeline_guide` cover only curated pipelines), wait for the platform's message, at most 3 correction cycles (`hpc_resubmit_pipeline` for fixes, `hpc_resume_pipeline` only for infrastructure failures), then the concordance report.

For each HPC correction cycle, keep the same `validate` timing interval open; do not add timing imports or flags to the validation package scripts. End the interval after step 6 is complete.

If submission gets 429 "Pipeline capacity reached" or a busy-slot response, keep the
`validate` step open and use a bounded poll-and-retry: check capacity/status, wait a
short interval, and retry submission up to 3 times over at most 15 minutes. Do not stop
the validation turn merely because another run is in flight. If capacity is still full
at the bound, hand off the staged pipeline and report the bounded wait and last response.

## 6. Hand off

1. Re-stage the final repo and write the zip, with **pipeline-hpc-validate**'s `submission.py`:
   `python3 "$VAL_DIR/scripts/submission.py" "$REPO" --staged-prefix "$STAGED" --stage --zip "/mnt/results/$NAME.zip" > "$REPORTS/handoff.json"`
   The zip is written through a local temporary file and copied over, and it keeps `bin/` scripts executable; the `STAGED` folder can't.
2. Tell the user:
   - where the repo (`STAGED`) and the zip are, and which validation level ran and what it showed;
   - for **Full validation**: where the HPC outputs (`/mnt/results/pipeline-<run_id>/outputs/`) and the concordance report are, and what's identical, what differs (with the likely cause from the provenance table) and what wasn't compared;
   - for the other levels: that no HPC run was made, and ask whether they want one now (and on which data);
   - how to run it elsewhere (README "Run it elsewhere");
   - copy whole report files, including `timings.jsonl`, to `/mnt/results/$NAME-reports/`, then run `python3 "$SKILL_DIR/scripts/timings.py" summary --reports "$REPORTS" --log "$REPORTS/timings.jsonl"`.

```bash
python3 "$SKILL_DIR/scripts/timings.py" end validate --reports "$REPORTS" --log "$REPORTS/timings.jsonl"
```

Don't say the pipeline is validated or scientifically equivalent. Say what was run and what the comparison shows.

## Critical Rules

1. Search (Registry first, offline index as fallback) before choosing any component; never guess module inputs or flags.
2. STOP at the approval gate and wait for an explicit yes; run only the validation level chosen there.
3. nf-core first (subworkflow, then module), splitting session code at every stage a module covers; local modules only for what's left, written to nf-core conventions and run in an existing image. Resolve containers in this order: biocontainers replacement, exact-version quay.io build, a usable recorded mirror, then retain the pinned upstream image. Custom image builds may be unavailable; use one only when the user explicitly confirms that builds are enabled and approves it. Never call the Seqera Containers or Wave API to build or fetch a new image.
4. Vendor everything; the Registry is for discovery only, so never use Registry includes or `nextflow module install`; the pipeline must run with no network access to nf-core or the Registry.
5. Submitted configs contain no platform-owned keys; `conf/portable.config` is never submitted or included.
6. Build in `/workspace`, never on `/mnt/results`; only the staged copy is submitted and handed off. Keep stub inputs/config outside the repo.
7. No hard-coded `/mnt` paths in pipeline code, and no input data in the repo.
8. Concordance is evidence for the user, not a verdict.
9. The pipeline's software is its container images. Never ship or rely on a `conda` or PATH-based profile (locally installed tools) as the tested software, and never install tools in the sandbox during conversion. Stub runs stay container-less and only create files; real execution is the HPC run. A local smoke run happens only if the user asks, is labelled as such, and never counts as validation.
10. A custom pipeline always runs on HPC through `hpc_run_pipeline(pipeline_files=…)`; `hpc_search_pipelines` lists curated pipelines only.
11. For every GPU stage, record its process label and the matching `resources` in the approval plan. Use a process label such as `gpu`; the platform-generated config maps that label to the GPU queue and adds the accelerator directive. A process-level `accelerator` directive alone does not select the GPU queue. Pass the exact resource object from `pipeline-hpc-validate/scripts/submission.py` to `hpc_run_pipeline` as `resources=sub["resources"]`; do not hand-copy the values.
12. List nf-core defaults and script-added flags that differ from the session at the gate; decide keep, set to the session value via `ext.args`/`params`, or document expected drift. Never ship a known data-dependent default that fails on the validation data.

## Attribution

The plan → approval → per-component validation → compose → end-to-end run process is adapted from Seqera's `create-workflow` skill (nextflow-io/agent-skills, Apache-2.0).
