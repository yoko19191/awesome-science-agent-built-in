---
name: pipeline-hpc-validate
description: 'Use to validate a Nextflow pipeline repo on Biomni HPC before handing
  it over: local preflight (bundle rules, platform-owned config keys, config layers),
  one hpc_run_pipeline run with bounded debugging, and a factual concordance report
  against a session''s outputs. Called by pipeline-from-analysis; also usable on any
  custom pipeline repo.'
category: data_analysis
visibility: public
starting-prompt: Validate the Nextflow pipeline I just built on HPC and compare its
  outputs with this session's results.
---
# Validate a Nextflow pipeline on Biomni HPC

Use the printed **Skill Directory** as `SKILL_DIR`. Paths, for a pipeline called `<name>`:

- `REPO`: the local build, e.g. `/workspace/<name>`. Build, lint and stub-run only here.
- `REPORTS=/workspace/<name>-reports`: local reports and timing log.
- `STAGED=/mnt/results/<name>`: the copy that is submitted to HPC and handed to the user.
- Reports go to local `/workspace/<name>-reports/`, never inside a repo; copy complete report files to `/mnt/results/<name>-reports/` at handoff.

Why two copies: `/mnt/results` is an S3-backed mount. `chmod` is silently ignored, rename and symlinks fail, and `zip` can't write there directly, so `bin/` exec bits, Nextflow's `.nextflow/` cache and `work/` links, and `zip` fail there. HPC inputs, though, must come from `/mnt/...` paths. If `REPO` is gone, rebuild it with `mkdir -p "$REPO" && cp -R "$STAGED/." "$REPO/" && if [ -d "$REPO/bin" ]; then chmod +x "$REPO"/bin/*; fi`.

## Validation levels

Read `hpc_get_pipeline_guide("custom")` once before preflight. It includes the shared
input-staging and recovery rules plus custom-pipeline runtime requirements. Use
this skill's steps to apply that guidance at the chosen validation level.

A custom repo runs on HPC with `hpc_run_pipeline(pipeline_files=…)`. The guide
catalog includes curated nf-core pipelines and the custom development guide;
it does not enumerate every custom pipeline that HPC can run.

Run only the level you were asked for (pipeline-from-analysis passes the user's choice from its approval gate). When called on its own without a level, run preflight first (it prints the pipeline's process count), then use `AskUserQuestion` to offer **Build only**, **Build + stub run** and **Full validation**, then wait for the answer. With **8 or more** processes, mark **Build + stub run** as recommended; with fewer, mark **Build only** as recommended and still offer the stub run. Put the count and the reason in the question: a stub run executes the whole workflow with placeholder files, so it catches wiring, input-count and missing-file mistakes that `nextflow lint` cannot, and each such mistake that reaches HPC costs a queue wait and a correction cycle. Never go past the chosen level without asking.

| Level                      | Steps                                                                                               |
| -------------------------- | --------------------------------------------------------------------------------------------------- |
| **Build only**       | step 1: preflight, then `nextflow lint` (no stub run)                                             |
| **Build + stub run** | step 1: preflight, then the time-boxed stub run (it parses the pipeline, so skip `nextflow lint`) |
| **Full validation**  | steps 1–4: preflight, stub run, stage, one HPC run, at most 3 correction cycles, concordance       |

## 1. Preflight, then lint or stub run (local, free; always first)

Every level starts with preflight:

```bash
export NXF_VER=26.04.6 NXF_SYNTAX_PARSER=v1
python3 "$SKILL_DIR/scripts/preflight.py" "$REPO"
```

Compose also runs the typed static wiring check before this step; keep its errors fixed. It is heuristic and static, and `unverified` means the stub run decides.

Before the stub/HPC gate, read `$REPORTS/local-module-runs.json` and include each local
module's data-run status and cheap invariant in the validation summary. Missing data runs
are explicit gate/handoff warnings, not silent passes.

Each `nextflow` start-up costs 20–40 s of JVM time, so start it only as the level needs.

**Build only:** then lint, and stop here:

```bash
cd "$REPO" && nextflow lint .
```

**Build + stub run** and **Full validation:** don't run `nextflow lint` (the stub run parses the pipeline). Stub-run the whole pipeline instead. Keep the stub inputs and the stub config in a folder **outside** the repo:

```bash
STUB=/workspace/<name>-stub; mkdir -p "$STUB/reads"
touch "$STUB/reads/S1_R1.fastq.gz" "$STUB/reads/S1_R2.fastq.gz"   # placeholders so checkIfExists passes
printf 'sample,fastq_1,fastq_2\nS1,%s/reads/S1_R1.fastq.gz,%s/reads/S1_R2.fastq.gz\n' "$STUB" "$STUB" > "$STUB/samplesheet.csv"
printf 'process {\n    resourceLimits = [ cpus: 2, memory: 4.GB, time: 1.h ]\n}\n' > "$STUB/stub.config"
cd "$REPO" && timeout 300 nextflow run main.nf -stub-run -c "$STUB/stub.config" --input "$STUB/samplesheet.csv" --outdir "$STUB/results"
```

- The stub config clamps the `process_*` labels (e.g. `process_medium` asks for 6 CPU / 36 GB) to what the sandbox has. Without it, the local executor refuses tasks larger than the machine, even in stub mode. Keep its numbers at or below `nproc` and free memory.
- The stub config never goes in the repo or in any submitted config: `resourceLimits` is platform-owned.
- No `-profile`: the sandbox can't run containers, and stub blocks only create files. Never swap in a `conda` or PATH-based run (tools installed in the sandbox) to get "real" outputs: that tests different software from the shipped containers. Real execution is the HPC run. Run a local smoke run only if the user asks; label it "local run, not the shipped containers" and never call it validation.
- Never put stub inputs, samplesheets or reads in the repo.
- A stub run of this tiny input finishes in a minute or two; `timeout 300` (exit 124) stops a hang. A timeout or a stall means a process never started or a channel never closed. That is a pipeline bug, not a stub-mode quirk, and a real run would hang the same way. Find the last process that started, and the first that didn't, in `.nextflow.log`, then check these known causes:

  1. a process emitting to a topic it reads (e.g. a versions collector with a `topic: versions` output), so the topic never closes;
  2. a `collect`/`toList`/`groupTuple` on a queue channel that never ends (e.g. one built from a topic or a `channel.watchPath`);
  3. a missing or empty input, so a process never gets a task (a wrong samplesheet column, a `filter` that drops everything, a value channel passed where a queue was expected).

  Make one fix, rerun the stub once. If it still fails or hangs, stop and report the process that never started, the last `.nextflow.log` lines and the fix you tried. **Never write throwaway pipelines to probe Nextflow's behaviour**; debug the real pipeline.

Fix every preflight error before submitting. The rules exist because the HPC API rejects:

- a `.config` file that mentions a platform-owned key **anywhere, comments included**. The scan matches whole words, so a tool option like `--min-fusion-reads` or `--queue-size` in `conf/modules.config` is rejected too. Move such an option out of `.config` files: define `params.extra_args = '--min-fusion-reads 3'` in `main.nf` (`.nf` files aren't scanned) and use `ext.args = { params.extra_args }`, or set it inside a local module's `bin/` script.
- a local include without a `./` or `../` prefix
- an include of a file that isn't bundled

Preflight also requires `conf/portable.config` to exist, and checks that no submitted file references it. It is for running elsewhere; it is never submitted and never included.

Preflight reports, but does not fail on, any process whose effective container is Seqera-hosted (`*.seqera.io`). The effective container is the module's `container` directive unless a `withName` block in `conf/modules.config` sets `container`. Resolve images upstream when possible; never edit a vendored module. `--allow-seqera` remains accepted as a compatibility no-op. Custom bundles receive no platform image overrides, so every retained image must be usable by the target HPC environment; the report is informational.

## 2. Stage and submit

1. Stage inputs: use `/mnt/user-uploads/...` directly, or copy data under `/mnt/results/<name>-inputs/`.
   Write the samplesheet to `/mnt/results/<name>-inputs/samplesheet.csv`, with file columns set to the **input aliases** (relative names), not `/mnt` paths.
2. Copy the bundle to `STAGED` and build the arguments:

```bash
python3 "$SKILL_DIR/scripts/submission.py" "$REPO" --staged-prefix "/mnt/results/<name>" --stage \
  --cpus 8 --memory-gb 32 --time-hours 12 \
  --input samplesheet.csv=/mnt/results/<name>-inputs/samplesheet.csv \
  --input reads/S1_R1.fastq.gz=/mnt/user-uploads/S1_R1.fastq.gz \
  --param input=samplesheet.csv --param outdir=results > "$REPORTS/submission.json"
```

- `--stage` copies the bundle plus `conf/portable.config` as plain byte copies, which the S3 mount accepts, and deletes files a previous `--stage` wrote that are no longer in `REPO` (listed under `removed`); it never deletes files it didn't stage. Re-run it after every fix. If `not_removed` is non-empty, the mount refused those deletes: stage to a new `/mnt/results/<name>-v2` folder and use that as `STAGED` from then on.
- `pipeline_files` is an allowlist: `main.nf`, `nextflow.config`, `modules.json`, `README.md`, and the `conf/ modules/ subworkflows/ workflows/ bin/ templates/ assets/ lib/` folders.
- Read `not_submitted`: repo files outside the bundle. Input data goes in `--input`, never in the repo. Anything the pipeline itself needs goes in `assets/`, `bin/` or `templates/`.

```python
sub = json.load(open("$REPORTS/submission.json"))
run = hpc_run_pipeline(pipeline_files=sub["pipeline_files"], input_files=sub["input_files"],
                       params=sub["params"], resources=sub["resources"])
```

The submission JSON carries the resource object as well as staged files and params. Always pass `resources=sub["resources"]`; do not hand-reconstruct it in the `hpc_run_pipeline` call. For a mixed CPU/GPU workflow, every GPU process must declare a label containing `gpu` (for example, `label 'process_gpu'`), and that exact label must appear in both `--gpu-process-label` and the resulting `resources.gpu_process_labels`. Add `--gpu --gpu-count 1 --gpu-process-label process_gpu` to the staging command for that example. The process label selects the platform-generated GPU queue and accelerator requirement; a Nextflow `accelerator` directive by itself does not select that queue. `submission.py` rejects GPU-named labels or `accelerator` directives when GPU routing is disabled, verifies that each accelerator process has a routed label when routing is label-specific, and rejects mappings to labels absent from the bundle. For a workflow where every process uses a GPU, set `--gpu` and omit `--gpu-process-label` so the platform routes all processes to its GPU queue.

Use the data subset the user approved. Resource caps depend on the user's plan: Free allows 16 CPUs, 32 GB and 24 h; Pro allows 32 CPUs, 128 GB and 24 h; Enterprise allows 32 CPUs, 128 GB and 72 h. Request what the session's data needs and no more. If submission is rejected with `NEXTFLOW_CPU_LIMIT_EXCEEDED`, `NEXTFLOW_MEMORY_LIMIT_EXCEEDED` or `NEXTFLOW_TIME_LIMIT_EXCEEDED`, lower that resource to the plan's cap and submit again; that is not a correction cycle. Never pass `nextflow_config` or `profile` values: the platform runs on AWS Batch, and platform-owned keys are rejected.

**429 "Pipeline capacity reached"** means the platform's shared run limit is full; nothing was submitted and there is no `run_id`. It is not a problem with the pipeline. Keep validation active and perform a bounded poll-and-retry: check the platform's capacity/status, wait a short interval, and retry `hpc_run_pipeline` up to 3 attempts over at most 15 minutes. This is part of the validate step, not a correction cycle. If the bound is reached, tell the user the staged pipeline is ready and report the last capacity response; do not spin or silently stop because another run is in flight.

## 3. Wait, then debug (at most 3 correction cycles)

After `hpc_run_pipeline` returns, tell the user the `run_id` and **end your turn**. The platform posts a message in the session when the run completes or fails. Don't poll `hpc_get_pipeline_status` in a loop; one call is fine if the user asks for progress.

**Completed:** the platform has already copied the run's outputs (`--outdir`) to `/mnt/results/pipeline-<run_id>/outputs/`, and Nextflow's reports to `/mnt/results/pipeline-<run_id>/reports/`, before it notifies you. Use them in place.

**Failed:**

1. `hpc_get_pipeline_status(run_id)`: read `error_message` (the failing process, exit status and command error), `suggested_action`, `can_resume` and `resume_available`.
2. `hpc_get_pipeline_logs(run_id, tail=500)`: the head job's log. Its `Command error:` and `Command output:` blocks are the failing task's `.command.err` and `.command.out`.
3. Then choose one:
   - **Infrastructure failure and `can_resume` is true** (Spot reclaim, container pull, platform staging): `hpc_resume_pipeline(run_id)`. It keeps the same `run_id` and re-runs the bundle that is **already staged**, so it never picks up changes to the repo.
   - **Anything you fix** (code, config, params, resources, inputs): apply the custom guide's recovery advice through this procedure. Edit code and process configuration in `REPO`, retaining other required overrides. Re-run preflight and the stub run, re-stage with `submission.py --stage`, then call `hpc_resubmit_pipeline(run_id, pipeline_files=sub["pipeline_files"], ...)`. If GPU routing changed, pass `resources=sub["resources"]`; otherwise add only the `params`, `resources` or `input_files` that changed, merged with the original run. Always pass the full `pipeline_files` after a code or config change. It returns a **new** `run_id`, and unchanged tasks reuse the cache. Continue with the new `run_id`.
   - **`resume_available` is false** (the cache is gone): submit a fresh corrected run with `hpc_run_pipeline`.

After 3 cycles, **stop**. Show the user the failing command, the error text and what you tried. Never loop.

## 4. Concordance (evidence, not a verdict)

Write a spec to `/mnt/results/<name>-reports/concordance-spec.json` that pairs each session output with its pipeline output:

```json
{"comparisons": [
  {"type": "table", "label": "DESeq2 results",
   "session": "/mnt/results/deseq2_results.csv",
   "pipeline": "/mnt/results/pipeline-<run_id>/outputs/deseq2/treated_vs_control.deseq2.results.tsv",
   "session_key": "", "pipeline_key": "gene_id",
   "rank_col": "log2FoldChange", "top_n": 100, "sig_col": "padj", "sig_threshold": 0.05},
  {"type": "checksum", "label": "salmon quant S1",
   "session": "/mnt/results/salmon/S1/quant.sf", "pipeline": "/mnt/results/pipeline-<run_id>/outputs/salmon/S1/quant.sf"},
  {"type": "vcf", "label": "calls", "session": "/mnt/results/calls.vcf.gz",
   "pipeline": "/mnt/results/pipeline-<run_id>/outputs/calls/S1.vcf.gz"},
  {"type": "none", "label": "MA plot", "session": "/mnt/results/ma_plot.png", "reason": "image"}
]}
```

- **tables** (`.csv`/`.tsv`, optionally `.gz`): each side's key defaults to its first column. Set `session_key`/`pipeline_key` when they differ (R `write.csv` row names have the header `""`), or `key` when they match. `columns` maps renamed columns, session name → pipeline name (e.g. `{"log2FC": "log2FoldChange"}`). `rank_col`/`sig_col` use session column names.
- **VCFs:** type `vcf` compares sites.
- **other files:** `checksum` (raw bytes, decompressed for `.gz`), or `none` with a reason (e.g. images).
- A comparison that can't be made (a missing file or column) comes back `not_compared` with the reason. Fix the spec and re-run rather than reporting it that way.

```bash
python3 "$SKILL_DIR/scripts/concordance.py" /mnt/results/<name>-reports/concordance-spec.json \
  --out-json /mnt/results/<name>-reports/concordance.json --out-md /mnt/results/<name>-reports/concordance.md
```

When you summarise the report:

- Say what is identical, what differs and what was not compared.
- For each difference, give the likely cause using the plan's version and parameter table, e.g. "fastp 0.23.4 in the session vs 1.3.6 in the nf-core container".
- **Never** say the pipeline is "validated", "correct" or "scientifically equivalent". The user judges whether a difference matters.

## Critical Rules

1. Run only the validation level asked for. Build, lint and stub-run in local scratch; submit only the staged copy under `/mnt/results`.
2. Preflight and the stub run pass before every submission and every resubmission. Local stub reruns are capped at 3 within the existing time-box. HPC correction cycles are capped at 3, counted uniformly: each resubmit after a failed HPC run is one cycle. At the limit, stop and report; do not ask to continue unless the user explicitly asks for more.
3. Never submit `conf/portable.config` or the stub config; never add platform-owned keys to submitted configs.
4. After submitting, end the turn and wait for the platform's message; never poll in a loop.
5. Fixes go through `hpc_resubmit_pipeline` with the re-staged `pipeline_files`; `hpc_resume_pipeline` only when `can_resume` is true.
6. At most 3 HPC correction cycles, counted uniformly, then stop and report.
7. Concordance reports facts only: `identical`, `differs`, `not_compared`.
8. Reports live outside the repo.
9. Custom repos run through `hpc_run_pipeline(pipeline_files=…)`; the curated-pipeline tools don't limit that.
10. Validate the shipped containers, never a `conda` or PATH-based profile; don't install tools in the sandbox. A local smoke run is only on request and is never validation.

## Attribution

The debugging protocol is adapted from Seqera's `create-workflow` skill (nextflow-io/agent-skills, Apache-2.0).
