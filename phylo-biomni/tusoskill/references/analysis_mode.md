# Analysis mode

Analysis mode is for tasks **without a clear, robust performance metric** — typically generative tasks such as molecular design, synthetic DNA/sequence generation, or structure generation, where no single number can decide whether one candidate is better than another. It is a special mode: if a defensible metric exists, stay in metric mode.

## Activation and budget

- Activate only at run start: `python scripts/biomni_method_state.py init --root <run_dir> --mode analysis ...`. The mode is recorded in run state and cannot change mid-run.
- Default budget is 20 iterations (vs 50 in metric mode) because each iteration includes an in-depth analysis that may run dozens of steps and several hours. Override with `--max-iterations` when the task justifies it.
- The termination gate (`stop-check`) works exactly as in metric mode: iteration budget reached or a hard blocker. Do not stop early because a candidate "looks good" — the budget exists because analysis-mode judgments are noisy.

## The per-iteration protocol

Every substantive iteration adds one step to the usual loop: after building the candidate, run an **in-depth comparative analysis** of the candidate against the current best (and the baseline), then record a verdict.

1. **Plan the analysis before inspecting results.** Write down which comparisons and checks you will run — the same battery for every candidate. This is the main defense against rationalizing a preferred candidate.
2. **Run the analysis battery** (open-ended; dozens of steps and hours is expected and fine). Store all artifacts under `candidates/<candidate_id>/analysis/` and summarize them in an analysis record (`templates/analysis_record.md`).
3. **Decide with a written verdict**: `improved`, `not-improved`, or `inconclusive`, with confidence and justification referencing the artifacts. Record it:
   ```bash
   python scripts/biomni_method_state.py add-experiment --root <run_dir> --candidate-id <id> \
     --intervention <type> --decision <accept|reject|archive> \
     --analysis-verdict <improved|not-improved|inconclusive> --analysis-ref candidates/<id>/analysis/ ...
   ```
   The helper refuses to record a substantive analysis-mode experiment without a verdict.
4. **Inconclusive is not accept.** If the analysis cannot distinguish the candidate from the current best, either gather more evidence (a deeper analysis iteration) or reject/archive. Accept requires positive evidence of improvement.

## Building the analysis battery

Choose analyses that a skeptical domain expert would trust; combine several independent lenses rather than stacking near-duplicate proxies. Proxy metrics are welcome as *evidence*, but no proxy is the sole arbiter — every verdict must also rest on direct examination of what the method produces.

Example battery for **molecular design** (adapt to the task):

- Validity and feasibility: chemical validity, synthesizability, stability of generated structures.
- Distribution comparison: property distributions (MW, logP, TPSA, etc.) of generated sets vs the comparator's set and vs the target profile — not just means.
- Novelty and diversity: distance to training/known molecules; internal diversity of each generated set.
- Independent predictors: property/activity estimates from predictors not used in generation or selection (an ensemble, or a model from a different modality).
- Task-specific functional checks: docking scores, predicted binding, developability flags — interpreted with their known error rates.
- Direct inspection: look at a sample of generated molecules yourself; note failure patterns a metric would miss.

For **sequence generation** (DNA/RNA/protein), the analogous battery: compositional and k-mer distributions vs natural sequences, motif/regulatory-element content, predicted structure or function, homology/novelty, constraint satisfaction, and direct inspection of samples.

## Anti-self-deception rules

- Same battery, every candidate, including the baseline in every comparison.
- Pre-registered analysis plan (step 1) before looking at outputs.
- Report effect sizes and variability across the generated set, not cherry-picked best examples.
- When proxies disagree, say so in the verdict and weigh the most independent evidence most.
- Track seed/stochastic variance where generation is stochastic: compare distributions across seeds, not single samples.

## Guardrails and integrity in analysis mode

- Validity/integrity checks remain mandatory guardrails (e.g. a generator whose validity collapses is regressing regardless of other evidence).
- Leakage rules are unchanged: never select on held-out evaluation data; document any external predictors used in the battery.
- `metric_analysis` is still recorded every iteration — here it summarizes what the analysis battery showed and why the verdict follows.

## Final selection and report in analysis mode

- Build the finalist set from candidates with `improved` verdicts across different families; run the full battery head-to-head on finalists, with multiple seeds where stochastic.
- `render-report` includes the analysis-verdict table automatically; the final report must link each verdict to its analysis artifacts so the user can audit every judgment.
