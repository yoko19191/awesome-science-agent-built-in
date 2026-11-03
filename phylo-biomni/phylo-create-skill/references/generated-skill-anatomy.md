# Generated skill anatomy

General workflow packages require only `SKILL.md` plus resources with a concrete purpose. They do
not carry the analytical files below and remain freely structured after canonical frontmatter and
package-hygiene checks.

Executable analytical packages use this fixed responsibility map:

| Path | Responsibility |
| --- | --- |
| `SKILL.md` | User-facing use, scope, inputs, caveats, ordered workflow, and exact runner workflow |
| `skill_spec.json` | Single source for user guidance and runtime declarations |
| `package_manifest.json` | Frozen hashes for every other package file |
| `DATA_SOURCES.md` | Source and licensing disclosure |
| `scripts/run_skill.py` | Only domain launcher; package, input, parameter, output, and receipt binding |
| one domain entrypoint | Reusable scientific computation |
| optional private helpers | Imported implementation details, never extra public commands |
| optional `scripts/report_qc.py` | Objective scientific-report checks only |

`scripts/run_skill.py` and `scripts/report_qc.py` are canonical templates and must remain byte-for-byte
unchanged. Domain code may not launch itself through an alternate wrapper.

Tests, fixtures, expected answers, evaluation scripts, freeze summaries, run receipts, reports, and
application outputs stay outside the package. The specification contains no evaluation cases.

Every run starts with clean declared output paths. The runner builds the command from the declared
language, entrypoint, `--input`, and validated parameter schema. A final pass remains valid only while
the frozen package, entrypoint, input, and all outputs retain their recorded hashes.

`analysis.workflow` is explanatory, ordered scientific instruction. The scaffolder renders the
entire analytical `SKILL.md`, and the static checker requires an exact match. Optional `guidance`
entries route conditional detail to declared `references/` files without creating another launcher.
The canonical runner does not interpret these sentences: executable behavior remains entirely
behind the single declared entrypoint.

The spec's `input` and `caveats` fields are also user-facing instructions, not alternate validation
engines. They describe invariant requirements, rejection conditions, assumptions, limitations, and
watch-outs without naming test paths, hashes, row counts, known answers, or evaluation thresholds.
