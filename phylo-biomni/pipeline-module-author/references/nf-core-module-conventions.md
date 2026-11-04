# nf-core module conventions (as used by this skill family)

Source of truth: the nf-core module specifications (nf-co.re/docs/guidelines/components/modules) at
nf-core/modules commit 7fb83dc4. When this file and a vendored nf-core module disagree, the module wins.

## Layout

- `modules/nf-core/<tool>[/<subtool>]/{main.nf,meta.yml,environment.yml}`: vendored, never edited.
  If one must change, copy it to `modules/local/` and say so in the README provenance table.
- `modules/local/<name>/{main.nf,meta.yml,environment.yml}`: our own modules; the code they run lives in `bin/<name>.<ext>`.
- `subworkflows/nf-core/<name>/`: vendored; they include modules as `../../../modules/nf-core/<tool>/main`.
- `modules.json`: records every vendored component with `git_sha` and `installed_by`.

## Process shape

```groovy
process TOOL_SUBTOOL {
    tag "${meta.id}"
    label 'process_medium'            // process_single|low|medium|high|long|high_memory
    conda "${moduleDir}/environment.yml"
    container 'quay.io/biocontainers/<tool>:<version>--<build>'

    input:
    tuple val(meta), path(reads)      // first input carries meta
    path reference                    // structural inputs follow

    output:
    tuple val(meta), path("*.bam"), emit: bam        // every output has a named emit
    tuple val("${task.process}"), val('tool'), eval('tool --version'), emit: versions_tool, topic: versions

    when:
    task.ext.when == null || task.ext.when

    script:
    def args = task.ext.args ?: ''    // tool options arrive here, never as inputs
    def prefix = task.ext.prefix ?: "${meta.id}"
    """
    tool --threads ${task.cpus} ${args} -o ${prefix}.bam ${reads}
    """

    stub:
    def prefix = task.ext.prefix ?: "${meta.id}"
    """
    touch ${prefix}.bam
    """
}
```

Avoid a fixed `memory` directive. The label sets memory through `conf/base.config` as a multiple of `task.attempt`, so a task killed for memory retries with more. A process with no fitting label uses `memory { 16.GB * task.attempt }`. The run's `memory_gb` ceiling clamps each process's request, so set it above the largest first request you expect (a `process_high` step asks for 72 GB, so use 128 to leave room for a retry). A ceiling equal to the request gives no retry, and a lower one silently shrinks the request. The plan's cap limits the ceiling (Free is lower than Pro), so where the cap is below a label's request, reduce that process's base memory.

## Where options go

| Kind | Where | Example |
|---|---|---|
| Files, reference data, `val` switches declared in `input:` | workflow wiring | `FASTP(ch, false, false, false)` |
| Threads, output names, flags the script already sets | nowhere (the module owns them) | `--thread`, `--json` in FASTP |
| Fixed tool options | `ext.args` string in `conf/modules.config` | `ext.args = '--length_required 36'` |
| Options a user would tune | `params.*` + `ext.args` closure | `ext.args = { "--libType ${params.salmon_libtype}" }` |
| Second / third tool in a piped module | `ext.args2` / `ext.args3` | `bwa mem | samtools sort` |
| Per-sample values | `meta` | `meta.strandedness` |

Publishing is configured in `conf/modules.config` (`publishDir`), never inside a module.

## Known wiring pitfalls

- Match each module input exactly: the same tuple size, and the same channel kind. Per-sample inputs come from a queue channel; a single shared input such as a reference or index must be a value channel (use `.first()` or `.collect()`), or the process runs only once. Combine channels with operators (`.combine`, `.join`, `.map`), never by putting channels inside a list such as `[ch_a, ch_b]`.
- Optional inputs use the module's documented empty tuple shape, not a bare `[]` guessed from the call site.
- Read a subworkflow's `take:` comments or `meta.yml` before supplying `val` inputs; never infer attribute names.
- Versions outputs may be triples or a `versions.yml` path. Keep the collector's dual-shape handling and never emit a versions record back to the versions topic it consumes.
- A generated local `bin/` parser owns `--prefix value` and each declared `--input value`; keep session code below it.
