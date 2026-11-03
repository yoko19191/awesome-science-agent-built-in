# Combination / synergy: use an interaction model

For a combination experiment, the scientific question is the **interaction** between two
perturbations, not the marginal effect of each. Test additive vs synergistic (non-additive)
transcriptional effects with an interaction term — do not compare each arm to vehicle separately and
eyeball the overlap.

## Requirements

- **Factorial design:** the plate must contain all four cells — vehicle, A alone, B alone, and A+B
  (applies to compound x compound or gene x compound). Without A+B and both monotherapies, no
  interaction is identifiable. This is a design-stage stop, not something to model around.
- **Adequate replication and power** for the interaction term (interactions need more power than main
  effects); plan replicate wells accordingly.

## Model

Fit an interaction-term linear model on raw counts (Schrode framework):

```
~ A + B + A:B          # A:B is the interaction (non-additivity) term
```

A significant `A:B` term identifies genes whose combined response departs from additivity. Classical
pharmacology nulls (Bliss, Loewe, ZIP, HSA) frame the expectation; the combination signature is
generally **unique**, not the sum of the monotherapy signatures. Correlated single-drug responses can
predict synergy, but that is a hypothesis, not a substitute for the factorial test.

## In this skill

The default bundled engine fits per-compound contrasts vs vehicle. Synergy is a documented extension:
supply a factorial plate map (with an explicit combination arm) and fit the interaction model above;
report the interaction-term DEGs and the null model used. Do not claim synergy from monotherapy
contrasts alone.

Evidence: review §4.6, §7, §12; interaction-term framework [77], synergy signature uniqueness /
prediction [75], factorial omics driver analysis [80].
