---
name: phylo-create-skill
description: Use to create or revise reusable Phylo Biomni skills, including flexible
  scientific workflows and strictly executed file-in analyses.
category: general
visibility: public
starting-prompt: Create a reusable Biomni skill for multivariable differential expression
  on the Bioconductor airway dataset.
---
# Phylo Create Skill

Create one immediately usable, production-only skill package, not a transcript of one run. The same
package may be refined, frozen, independently tested, reviewed, and eventually shipped; never add
validation-only material to it.

Use the printed **Skill Directory** as `SKILL_DIR`. Commands start from `/workspace`.

## Boundaries

- Draft only beneath `/mnt/results/skills/<slug>/`; never alter installed or supplied packages.
- Never put test data, fixtures, expected or truth values, oracle/evaluator code, test scripts,
  receipts, campaign evidence, run outputs, environments, caches, or repository tests in a skill.
- Never register, publish, deploy, open a pull request, or upload a personal skill without explicit
  approval for that action.
- Present the package or analytical specification for review before freezing or registration unless
  the brief explicitly authorizes an unattended pass.
- Use default Phylo styling for reader-facing artifacts unless the user explicitly requests a
  compatible named provider. Tenant, company, project, audience, or instance context is not a style
  request.

## 1. Choose the authoring path

Use **executable analysis** only when a stable file or directory enters a repeatable computation,
success produces declared files, and one public domain entrypoint can own the computation.

Use **general workflow** for instruction-only guidance, protocols, literature or data retrieval,
reporting, API/tool use, and orchestration, including remote side effects. A PDF, table, figure, or
helper script does not by itself make a workflow an executable analysis.

Read `references/general-authoring.md` for general workflows and `references/interview.md` for
executable analyses. Ask only questions that materially change the package and cannot be inferred
safely.

### Repair an existing skill

Enter repair work only when the user explicitly asks to repair, revise, update, or migrate a
supplied skill. Read `references/repair-existing.md`, copy the source to
`/mnt/results/skills/<slug>/`, and inventory its identity, interface, science, scripts, references,
and presentation before editing the copy.

Preserve identity, scientific behavior, inputs, parameters, outputs, and their meanings unless the
user approves a change. Route the copied package by what it does, not by its current files. A legacy
analysis can migrate to the canonical specification and one public entrypoint while retaining useful
scripts as private helpers. Externalize any legacy validation material instead of moving it into the
repaired package.

Styling is the deliberate exception: repaired reader-facing artifacts adopt current Phylo styling
by default. A compatible enterprise provider requires an explicit named request. Styling must not
change scientific outputs, facts, figures, or analysis hashes.

## 2. Author the package

### Starting prompt

Write `starting-prompt` as one concise, concrete, natural prompt that the skill can address. It may
be a scientific question or a direct task instruction. Preserve a usable user-supplied prompt and
keep its scope consistent with the skill's supported inputs, methods, and outputs. Do not turn a
task into a question by simply appending `?` or rewrite a valid question as a task.

- Task: "Clean this molecular dataset and flag QC issues before SAR analysis."
- Question: "Which QC issues in this molecular dataset could affect SAR analysis?"

For executable analyses, set `starting_prompt` in the specification; the scaffolder renders it
unchanged into the `starting-prompt` frontmatter field.

### General workflow

Author `/mnt/results/skills/<slug>/SKILL.md` directly. Use the canonical six-field Phylo frontmatter
shown above and keep it below 500 lines. Include only relevant purpose, scope, prerequisites,
stepwise instructions or decision criteria, outputs or actions, caveats, stopping conditions, and
authorization boundaries.

Use progressive disclosure: keep shared decisions in `SKILL.md`; add `references/`, `scripts/`,
`assets/`, templates, source documentation, or licensing files only when they have a concrete use.
General packages do not need an analytical specification, canonical runner, report QC, or package
manifest.

For external API or tool mutations, require approval immediately before the mutation or cost, use
environment-based credentials, bound retries, and state when to stop. Retrieval skills may use
declared network resources and should record source, version, query, and retrieval date when needed
for interpretation or reproducibility.

### Executable analysis

Start from the matching complete `phylo-analysis-spec/1` example:

```bash
python3 "$SKILL_DIR/scripts/scaffold_skill.py" --example scientific-report \
  > /mnt/results/skill-spec.json

# Or, for an explicitly requested or clearly justified machine-output-only workflow:
python3 "$SKILL_DIR/scripts/scaffold_skill.py" --example artifacts \
  > /mnt/results/skill-spec.json
```

Tailor identity, objective, invariant input guidance, ordered scientific workflow, one public
entrypoint, parameters, declared outputs, scientific caveats, sources, presentation, and optional
conditional guidance. The specification contains runtime declarations only—never examples,
fixtures, assertions, expected answers, or evaluation routes.

- Default newly authored analyses to `scientific-report`: analysis outputs plus canonical facts,
  an opening infographic, a PDF, provider binding, and objective report validation.
- Choose `artifacts-only` when explicitly requested or clearly appropriate for machine-output-only
  work, such as pipeline intermediates or format conversion. It permits machine outputs and optional
  PNG figures without report code. Ask when intended deliverables are ambiguous or conflicting.
- Record `presentation_profile` explicitly; missing values are not defaults. This authoring default
  does not change existing package contracts or require reports for general workflows.
- Parameters state their scientific effect. Represent supported method, algorithm, or tool choices
  as bounded values dispatched behind the one entrypoint.
- Optional `guidance` entries link substantial conditional domain instructions under `references/`.
- Snapshot remote inputs when practical; otherwise declare provenance outputs covering provider,
  query, version, and raw inputs. Use the general route for side-effectful remote workflows.

Scaffold the package:

```bash
python3 "$SKILL_DIR/scripts/scaffold_skill.py" \
  --spec /mnt/results/skill-spec.json \
  --dest /mnt/results/skills
```

Implement the generated entrypoint so it:

- accepts `--input` plus only declared parameters;
- writes every declared output beneath `BIOMNI_RESULTS`;
- validates the complete input before writing success artifacts;
- rejects missing, ambiguous, unsafe, or scientifically invalid inputs clearly;
- contains no customer name, one-off filename, fixed result, hidden dependency installation,
  validation-only route, or undeclared network dependency;
- derives behavior from the declared input contract rather than recognizing example data; and
- writes strict machine-readable outputs alongside figures or reports.

Private helpers are allowed, but there is one public domain entrypoint. Do not copy or modify the
canonical runner or report helper. At application time, map user requests only to declared
parameters; an unsupported method or changed interface requires a new package revision.

## 3. Inspect and freeze

Run the route-aware static checker:

```bash
python3 "$SKILL_DIR/scripts/check_skill.py" /mnt/results/skills/<slug>
```

Review the checked package with the user when required. Then freeze it once:

```bash
python3 "$SKILL_DIR/scripts/finalize_skill.py" /mnt/results/skills/<slug>
```

Finalization performs structural checks and records immutable identity. For an analytical package it
adds the canonical package manifest; for a general package it keeps the package unchanged and writes
an external inventory. It then creates a deterministic ZIP and an external freeze summary containing
tree, manifest when applicable, and archive hashes. It reports `frozen`, never scientific approval,
and refuses to overwrite an archive or summary for the same slug in that results directory.

## 4. Keep testing downstream and external

The generated skill must work without its own tests. If the user or a Phylo campaign needs stronger
evidence, create proportional tests afterward in a campaign workspace or separate task. Bind them to
the exact candidate hash and keep their inputs, invalid cases, expected answers, oracle code,
evaluation scripts, and outputs outside the package and outside later neutral application prompts.

A failing external test does not authorize an invisible edit. Obtain approval for the repair, create
a new candidate identity, and rerun the required evidence. Apply frozen packages in fresh tasks with
automatic repair disabled when first-run behavior matters.

Code can establish package identity, runtime binding, hashes, structured-artifact validity, report
lineage, and renderability. Scientific adequacy, generalizability, licensing sufficiency, and visual
quality require proportionate external review; see `references/evidence-and-maturity.md`.

## 5. Present the result

Return the package path, route, capability, explicit limits, and any pending external review. If it
was frozen, also return the ZIP, archive SHA-256, package tree hash, external freeze-summary path, and
analytical manifest hash when applicable.

To show the Save Skill / View Skill card, finish by calling the `Skill` tool with `action="create"`
and `folder_paths=["skills/<slug>"]` for a new package. For a repaired owned skill, use
`action="update"` with its ID (or exact owned name) and the package path. Put the complete result
summary in `final_response` and make this tool call alone. The card lets the user save or apply the
draft; do not report it as saved or updated until the user completes that action.

Do not imply that generation or freezing registered, published, deployed, scientifically validated,
or approved the skill.
