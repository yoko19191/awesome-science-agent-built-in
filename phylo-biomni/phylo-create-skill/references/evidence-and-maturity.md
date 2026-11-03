# Evidence and maturity

Generation, freezing, execution, and scientific review answer different questions.

1. Author one production-only package with no tests, fixtures, expected answers, oracle code, or
   evaluation scripts.
2. Inspect its scope, interface, methods, sources, and presentation decisions.
3. Freeze the exact package. Freezing records immutable identity; it does not run scientific tests
   or establish adequacy.
4. When risk justifies it, create deterministic development tests afterward in a campaign workspace
   or separate task. Bind their evidence to the candidate hash and keep it outside the package.
5. Apply the unchanged frozen package in a fresh task. Keep neutral application inputs separate from
   evaluator-owned expectations and disable automatic repair when first-run behavior matters.
6. Review scientific correctness, generalization, licenses, and every reader-facing page in
   proportion to the intended use.

A failing test or review creates evidence against the current candidate. Any approved package repair
changes its bytes and therefore creates a new candidate identity whose downstream evidence must be
re-established.

The canonical runner owns application execution records and the single `run_receipt.json` schema.
The external freeze summary owns only package and archive identity. External development-test and
review records remain separate and must never be copied into the skill.

The content-tree hash binds paths and file bytes, not executable mode bits. The archive hash also
binds the executable modes stored in the ZIP. Mode normalization can therefore change the archive
hash without changing the content-tree hash; preserve both identities and use the exact frozen ZIP.

Deterministic code can establish package identity, actual input and output bytes, runtime binding,
structured-artifact validity, provider selection, lineage, and PDF renderability. It cannot by itself
establish scientific adequacy, generalizability, commercial permission, or visual quality.
