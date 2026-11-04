# Foundation-model optimization

For foundation-model or large-model method development, use small, honest proxies before expensive runs.

## Proxy objectives

A good proxy predicts final usefulness under a fixed compute budget. Examples:

- Validation loss after a short fixed training window.
- Downstream validation score after frozen-encoder feature extraction.
- Few-step adaptation score on a representative subset.
- Retrieval quality against training-safe evidence.
- Calibration or uncertainty quality for the deployment task.

Track wall time, memory, examples/tokens processed, checkpoint size, validation loss, downstream metric, and seed variance.

## The scale ladder (when full training is too expensive to iterate on)

When a single target-scale run costs more than the iteration budget allows, iterate up a ladder of scales instead of at the target scale.

- **Use at least 3–4 scales** spanning roughly two orders of magnitude of compute (e.g. 1e18, 1e19, 1e20 FLOPs), with the target scale held out. Never extrapolate from a single scale.
- **Rank ideas at small scale, confirm across scales.** Accept an architecture, objective, or data-mixture change only if its ranking is consistent across at least two adjacent scales; single-scale wins frequently do not transfer.
- **Validate before committing.** Fit the scaling relationship on the small scales, predict the held-out larger scale, and run one confirmation run there before any target-scale commitment. Scale up only when the predicted target-scale gain justifies the run's cost.

## Fitting scaling laws

- Fit the Chinchilla parametric form `L(N, D) = E + A / N^alpha + B / D^beta` on final validation losses across scales (N = non-embedding parameters, D = tokens), using Huber loss on log-loss, multiple random initializations, and more than one seed per scale.
- **Design for identifiability:** vary tokens-per-parameter across runs; collinear designs (all runs at the same tokens-per-parameter ratio) make the exponents unidentifiable.
- **Report fit uncertainty** and treat any single fitting method with caution — the Chinchilla replication showed one of three original fitting approaches fails to fit the extracted data (Besiroglu et al. 2024).
- Count non-embedding parameters consistently; embedding-count conventions explain much of the Kaplan vs Chinchilla discrepancy (Pearce & Song 2024).

## Rules of thumb (with caveats)

- **Compute-optimal data:model ratio** is roughly 20 tokens per parameter (Hoffmann et al. 2022), with the replication caveats above.
- **Compute-optimal model size** scales as N* ~ C^a with a ≈ 0.57–0.60 once per-size learning rate, batch size, and AdamW β2 are tuned at each scale (Porian et al. 2024). Untuned per-size hyperparameters bias the fitted exponent.
- **Inference-aware scaling:** if the model will be queried heavily, prefer a smaller model trained on more tokens than Chinchilla-optimal (Sardana et al. 2023); overtraining ratios remain predictable well beyond 20:1 (Gadre et al. 2024).
- **Data-constrained regimes** (limited unique tokens, repeated epochs) follow modified laws — do not apply the 20:1 rule when data, not compute, is the binding constraint (Lovelace et al. 2026).

## Hyperparameter transfer

Absolute learning rate and batch size do **not** transfer across scales under standard parameterization. Use muP (maximal update parameterization) so that optimal hyperparameters stay approximately stable as width/depth grow (Yang & Hu; Ghosh et al. 2025); note that much of muP's practical benefit in AdamW comes from the embedding-layer learning rate (Kalra & Barkeshli 2026). Whatever parameterization is used, re-tune LR and batch size at each ladder scale rather than copying them upward.

## Proxy pitfalls

- Fixed small-proxy training configurations can **misrank data recipes**; tuning proxies at reduced learning rates restores agreement with tuned large-scale runs (Wang et al. 2025).
- A proxy that ignores the deployment distribution (e.g. generic web loss for a biological sequence model) can rank methods incorrectly — the proxy must measure the capability the final method needs.
- Short-window validation loss favors fast starters; check that early advantage persists across the ladder before accepting.

## Efficient adaptation options

- Frozen encoders with learned heads.
- Adapters or low-rank updates.
- Distilled features.
- Retrieval-augmented predictions.
- Mixed precision, gradient accumulation, and checkpointing.
- Smaller proxy models or reduced context during exploration.

## Integrity

Do not train or select on final evaluation labels. Keep data mixtures documented. Ensure pretrained resources are allowed and plausibly deployment-safe.
