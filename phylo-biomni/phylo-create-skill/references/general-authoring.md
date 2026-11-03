# General workflow authoring

Use this route when reusable scientific behavior is primarily instructions, decisions, retrieval,
reporting, tool/API use, or orchestration rather than one deterministic file-in computation.

## Discoverability and scope

Write a short, discriminating description using the task, relevant formats, tools, or databases
that should trigger the skill. Add a nearby exclusion only when it prevents likely misrouting. The
body should make the supported outcome, boundary, and stopping conditions clear without claiming
that every request follows one fixed sequence.

When users approve inclusion or exclusion criteria, make them hard workflow constraints. Apply them
before synthesis or action. Keep included results distinct from background/context and from an
exclusion log; do not place an excluded item in the primary result merely because it is relevant.
Ask before an exception that would change the approved scope.

## Conditional interview

Ask only about factors that change the package. Depending on the workflow, these may include:

- input format, size, batching, identifier systems, reconciliation rules, missingness, and relevant
  genome, database, model, or resource versions;
- compute/runtime constraints, HPC needs, installed tools, external services, and reproducibility;
- authentication location, licensing, rate limits, monetary cost, network access, and remote side
  effects;
- scientific assumptions, silent-failure risks, limitations, evidence standards, expected outputs
  or actions, authorization boundaries, and conditions that require stopping or asking the user.

Do not ask for database, infrastructure, or error-handling detail when it cannot change the skill.
Do not turn one example, fixture, or reviewer preference into a universal generated instruction.

## Package design

Keep shared purpose, essential workflow, decision criteria, and authorization boundaries in
`SKILL.md`. Route substantial conditional detail to focused references and say when each reference
should be read. Add deterministic scripts only for repeated concrete operations; add assets or
templates only when they belong in outputs. Credentials remain in environment variables or the
platform credential store and never in package files.

Externally mutating workflows must request approval immediately before the mutation or charge,
bound retries to avoid duplicate side effects, and stop with the observed state when success is
uncertain. Retrieval workflows may use declared network resources and should capture source,
version, query, and retrieval date when those facts affect interpretation or reproducibility.

Define recovery semantics only when failures are plausible or consequential. Distinguish:

- a bounded retry of the same idempotent retrieval after a transient failure;
- a visible correction to disposable run-local code that preserves the approved inputs, scope,
  methods, and output meaning; and
- a package edit, relaxed scientific constraint, changed method, suppressed result, or fresh task.

The first two are ordinary execution unless the user forbids them. The third is a new scientific or
authoring decision: stop, disclose it, and obtain the authority appropriate to that change. Never
describe a replacement task or modified package as the original first-run result.

The general route needs no generated evaluation framework. Validate its structure and helper
syntax, then review its decisions with the user. Test behavior independently only when the task and
risk justify it; do not bake evaluator-owned answers or test-specific assumptions into the skill.

Before registration, confirm that the package still belongs on the general route. A helper may
support a workflow, but a stable file/directory-in computation with declared outputs and one public
domain entrypoint belongs on the executable-analysis route.
