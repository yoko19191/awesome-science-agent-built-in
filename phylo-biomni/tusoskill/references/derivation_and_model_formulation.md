# Derivation and model formulation

Use derivations when a new score, model, loss, regularizer, constraint, or approximation could improve the method.

## Derivation record

Include:

- Problem statement.
- Variables and notation.
- Data-generating or biological assumptions.
- Objective, likelihood, risk, energy, or scoring rule.
- Constraints and invariances.
- Optimization or inference algorithm.
- Complexity and memory use.
- Hyperparameters and how to estimate them.
- Theoretical properties to preserve.
- Practical approximations and their tests.

## Stepwise derivations (long or multi-part)

A lengthy derivation is a chain of numbered steps, not one monolithic argument. For each step record: the transformation applied, its justification (theorem, identity, approximation), and the check that was run. Keep the chain in a markdown record under `derivations/` (see `templates/derivation_record.md`) and register each derivation — including revisions — with `add-derivation`, linking revisions to their predecessor via `--parent-id`. Never overwrite the history of a derivation; superseded versions stay in the ledger so the final report can explain how the model evolved.

Check every step with the cheapest tool that could catch the error:

- **Units and dimensions.** Every term must balance in biological/physical units; a term that cannot be assigned units is a red flag.
- **Limiting cases.** Behavior as n→0, n→∞, noise→0, or a rate→0/∞ must match intuition or known results.
- **Special-case recovery.** The general model must reduce to the known special cases (e.g. mixture with K=1 reduces to the single-component model; your likelihood with no structure reduces to the independence model).
- **Symbolic checks.** For algebra-heavy steps (derivatives, integrals, matrix identities), verify with a symbolic tool such as SymPy rather than by eye.
- **Numerical spot-checks.** Evaluate both sides of a derived identity on random small instances; exact agreement up to float error.

## Implementation faithfulness

The implemented method must stay faithful to the derivation. Maintain an explicit mapping from each implemented function/equation to the derivation step it realizes. Where the code must diverge — numerical-stability tricks (log-sum-exp, jitter), approximations, constrained reparameterizations — record the divergence, its justification, and a test of its practical effect. Unrecorded divergence between paper math and code is one of the most common sources of silently wrong methods.

## Two classes of change — record them differently

- **Estimation/algorithm changes** (EM vs gradient descent, optimizer, initialization, step size, Monte Carlo settings): the model is unchanged. Log these as ordinary experiments (`add-experiment`); do not create a new derivation.
- **Derivation changes** (new or dropped term, relaxed assumption, different objective or likelihood): create a new derivation record with `--parent-id` pointing at the superseded one, and re-run the checks for every downstream step that depended on the changed step.

## Assumption auditing

List every data-generating and biological assumption explicitly. For each, define the check that would reveal its failure (residual structure, posterior predictive checks, simulation from the assumed generative process) and run it before relying on the model. Re-audit assumptions when the data, the derivation, or the deployment regime changes. Record audit outcomes in the derivation record's assumption table.

## Validating the derived model

Before trusting a derived statistical model or its inference implementation:

- **Simulation-based calibration (SBC).** For Bayesian inference, simulate parameters from the prior and data from the model, then verify that posterior ranks are uniform — this catches both model and implementation errors (Talts et al. 2018; Modrák et al. 2023).
- **Identifiability analysis.** Before interpreting fitted parameters, check they are learnable: profile likelihood or posterior contraction per parameter (Simpson & Maclaren 2022). Unidentifiable parameters must be fixed, reparameterized, or reported as unidentifiable.
- **Ground-truth simulation.** Fit the model to data simulated from the assumed generative process with known parameters; recovery failure invalidates the implementation before any real-data result is trusted (Mendes et al. 2024; Villaverde et al. 2021).
- **Predictive checks.** Prior and posterior predictive distributions should bracket the observed data's key summaries (Conn et al. 2018).

## Principles

- Derive only what you can implement and evaluate.
- Prefer formulations that reduce hyperparameters or make them estimable.
- Keep units and biological interpretation clear.
- If the method relies on a theorem or invariant, stay close to the conditions needed for it.
- Test whether the derived term matters with ablation.
