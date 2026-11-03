# Skill specification interview

Start from one complete `phylo-analysis-spec/1` example. Ask only questions whose answers change the
package and cannot be inferred safely.

## Required decisions

1. What exact user objective triggers this skill, and what nearby task is excluded? Make the
   description discriminating with relevant task, format, tool, or database vocabulary.
2. What file or directory is the actual input? Write an invariant summary, accepted requirements,
   and fail-closed rejection conditions covering relevant fields, aliases, identifiers, units,
   encodings, scale, batching, missing values, identifier reconciliation, and genome/database/model
   or other resource versions. Do not describe a fixture.
3. Which assumptions, limitations, and interpretation watch-outs would prevent a scientifically
   plausible but wrong application or conclusion?
4. What ordered scientific steps must the analysis perform, including validation and stop
   conditions? Keep them concise, domain-specific, and independent of example or evaluation data.
5. What is the one executable domain entrypoint? Which private helpers, if any, does it import?
6. Which parameters materially change the analysis? Give each a type, CLI flag, required/default
   rule, concise scientific effect, and choices where bounded. Model supported alternative methods,
   algorithms, or tools as explicit string choices implemented behind the one entrypoint. Do not
   expose inert generic options, arbitrary commands, or free-form configuration languages. Give
   each required parameter an `example` value so the generated Execute command includes it.
7. Which analysis outputs always exist after success? Classify each as CSV, JSON, PNG image, or
   other file and set a meaningful minimum size.
8. Default a new analysis to `scientific-report`: analysis outputs, canonical facts, an opening
   infographic, and a reader-facing PDF. Choose `artifacts-only` for an explicit no-report request
   or a clearly machine-output-only purpose, such as pipeline intermediates or format conversion.
   Ask when deliverables are ambiguous or conflicting; record the chosen profile explicitly.
   Preserve an existing package's output contract during repair unless a change is approved.
9. What important wrong default or failure mode must the entrypoint reject before writing outputs?
10. Which sources or software are required, and what evidence supports commercial use?
11. When relevant, what compute, memory, runtime, accelerator, HPC/scheduler, installed-tool, or
    environment constraints change implementation, batching, or whether this skill is feasible?

Render the existing description as when-to-use guidance and the objective as scope. Do not add
duplicate fields for those concepts. Keep detailed source and license records in `DATA_SOURCES.md`.

Do not interview the package author for test fixtures, expected answers, assertions, oracle logic,
or evaluator routes. When proportional development testing is warranted, create and retain that
evidence outside the completed package in a separate downstream activity.

## Scientific reports

The report profile fixes three presentation outputs: `report_facts.json`, `infographic.png`, and
`report_<slug>.pdf`. Declare which analysis outputs are their sources. Facts must carry the current
source-output path/hash map. For accounting groups, declare `value_type: integer` for exact counts
or `value_type: floating` plus an explicit absolute and/or relative tolerance.

Subjective scientific and visual quality remains an external review decision. Do not add a field to
the specification merely to make code claim that a report is attractive or scientifically sound.

## Application-time variation

The generated `SKILL.md` tells the application agent how each declared parameter changes the
analysis. Natural-language requests map to those parameter values, and the canonical receipt records
the resolved values. A bounded tool choice may dispatch to a private helper or library from the one
entrypoint; it does not become another public command. Requests outside the declared choices or
those that alter input/output meaning require a new package revision and fresh evidence.
