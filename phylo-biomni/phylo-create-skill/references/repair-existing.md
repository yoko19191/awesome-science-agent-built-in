# Repairing an existing skill

Use this reference only after the user explicitly asks to repair, revise, update, or migrate a
supplied skill. An attached package without that request is context, not permission to alter it.

## Protect the source and establish the baseline

Copy the source package to `/mnt/results/skills/<slug>/` and work only on the copy. Reject unsafe
archive paths and symlinks during extraction. Do not copy caches, receipts, run outputs, repository
tests, credentials, or evaluation artifacts into the candidate.

Before editing, inventory:

- frontmatter identity and discovery behavior;
- accepted inputs, public parameters or commands, output names, formats, and meanings;
- scientific methods, assumptions, limitations, stopping conditions, and external dependencies;
- scripts and the callers that make them public or private;
- references, assets, report outputs, current style defaults, and provider-selection behavior.

Treat identity, scientific behavior, and the public interface as compatibility requirements. Split
the brief into requested changes, preserved behavior, and changes that would require approval. Ask
before changing a method, input contract, parameter meaning, output path or meaning, or externally
visible action that the repair did not authorize.

Preserve effective defaults and precedence as part of parameter handling. If an existing option is
resolved from the input when no override is supplied, represent that state explicitly (for example
with a `config` sentinel) rather than replacing it with a fixed default.

Before asking for approval, name each proposed change to public commands, input or parameter
handling, output paths or meanings, conditional outputs, and the analysis/presentation boundary.
Show the old-to-new mapping and distinguish compatibility-preserving internal changes from changes
that callers will observe. Do not start those observable changes until the user approves them.

## Apply the existing authoring routes

Do not select a route from the source package's file names. In particular, no root
`skill_spec.json` leaves the route undetermined: the package may be a general workflow or a legacy
analysis. Classify its behavior using the criteria in `SKILL.md`. A legacy package is an executable
analysis when a stable file or directory enters a repeatable computation with declared outputs and
one public entrypoint can own it, even if its current instructions expose multiple commands, its
implementation is split across scripts, or its main deliverable is a report.
Do not use `check_skill.py` to choose the legacy source's route: it validates the resulting candidate
structure after that behavior-based decision; it does not classify a pre-specification source.

For a general workflow, make the narrow requested edits and retain its useful free-form structure.
Do not add a specification, canonical runner, report QC, manifest, receipt, or
evaluation framework merely because the package is being repaired.

For an analysis using an older analytical specification, migrate its runtime declarations to
`phylo-analysis-spec/1`, externalize any cases, fixtures, assertions, expected answers, or evaluator
logic, and regenerate the analytical `SKILL.md`. Never hand-edit around exact rendering or modify
the canonical runner and report helper.

For a legacy stable file/directory-in analysis, describe the existing public contract in the
canonical specification and choose one public domain entrypoint. Keep bounded existing scripts as
private imported helpers when that preserves proven scientific behavior more safely than a rewrite.
Remove alternate public launchers and duplicate validation or receipt owners only after their real
callers and responsibilities have been accounted for. If the legacy behavior cannot be represented
without changing its scientific method or public interface, stop and present that migration decision
for approval.

Audit presentation code before replacing or restyling it. A renderer owns scientific behavior when
it selects headline results, interprets gates or thresholds, assigns scientific labels, distinguishes
hypothesis modes, interprets operating characteristics, or writes data-dependent conclusions. Move
that behavior into a declared, analysis-owned structured output while preserving its meaning; make
the presentation layer consume those recorded facts. Do not silently discard such logic as styling,
and do not let the replacement renderer recompute it independently.

Remove the legacy custom renderer after extracting any scientific decisions it owns; renaming it or
retaining it as a private helper does not complete an analytical repair. The public domain entrypoint
and its private helpers write only declared `analysis.outputs`: they do not create
`report_facts.json`, the opening infographic, or the PDF, and they do not invoke a presentation
provider. Only after `run_skill.py execute` has hashed the analysis outputs does the selected provider
create the presentation trio, following the generated `SKILL.md` workflow.

Declare complete presentation lineage. `presentation.source_outputs` must list every declared
analysis artifact that the provider reads, cites, summarizes, or renders. Analysis outputs that are
not used in the presentation need not be listed. Do not hide a report dependency behind an
undeclared file, a private helper, or a presentation-time recomputation.

Canonical analytical outputs are complete rather than conditionally absent. When a legacy output
depends on a supported parameter or input branch, either declare a stable artifact with a truthful,
schema-valid empty or `not requested` representation, or present a different contract for approval.
Never fabricate measurements for an inactive branch. Keep this migration rule general: validation
examples remain external evidence, not branches or expected values in the repaired skill.

Do not demote an existing meaningful output to an undeclared extra merely to avoid representing its
inactive state. Preserve it as a declared output using a truthful representation appropriate to its
type—for example status-bearing JSON, a schema-valid empty table, a clearly labelled figure panel,
or an explicit report section—or obtain the user's approval to remove it from the public contract.
Incidental diagnostics that were never part of the public contract may remain optional when their
absence and lack of receipt coverage are documented.

## Normalize reader-facing styling

Styling is updated on every repair instead of preserving a legacy default:

- Reader-facing reports, documents, slides, infographics, and similar artifacts use Phylo-branded
  styling by default.
- A compatible named enterprise provider is used only when the user explicitly requests it. Tenant,
  company, instance, project, or audience context alone never selects enterprise styling.
- An explicit request for enterprise styling without a provider name requires clarification before
  rendering.
- Analytical scientific reports declare `pdf-report-generation` as the default provider and allow
  an explicit compatible override. Application finalization records that override with
  `--provider <provider> --explicit-provider`.
- Styling may change presentation artifacts, but it must not rewrite analysis outputs,
  `report_facts.json`, canonical figures, scientific values, or their recorded hashes.

Do not add report files or helpers to a repaired skill that has no reader-facing artifact. For a
general workflow that can produce one, express the Phylo-first and explicit-override rule in its
instructions without importing analytical report machinery.

## Validate and present the repair

Run the checker appropriate to the selected route. Keep every development or independent validation
input, answer, assertion, and evaluator outside the repaired package. Compare preserved inputs,
parameters, outputs, and scientific results against the baseline in a separate workspace or task.
Test default Phylo styling and any requested enterprise override on the same canonical facts so
presentation cannot conceal scientific drift.

Derive removed commands, renderer names, and moved paths from the source-to-candidate inventory.
Before returning the repair, inspect all surviving instructions, references, examples, script
comments, and help text against that list. Active guidance must use the current interface and paths;
past names may remain only when clearly labelled as historical. Confirm that current commands and
linked package resources resolve. In a canonical analytical repair, every operational user or
application command invokes `scripts/run_skill.py`; show the domain entrypoint directly only when it
is clearly labelled as an internal developer diagnostic. Reconcile every stated output name and
count with `skill_spec.json`.

Return the new candidate and summarize what was preserved, changed, removed, and left for external
review. Registration, upload, publication, deployment, and replacement of an installed skill remain
separate approved actions.
