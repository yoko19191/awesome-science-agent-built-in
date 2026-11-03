# QC and reviewed pre-gating workflow

### QC / pre-gating (do this — real FCS is not pre-cleaned)
Benchmark data (HDCytoData) ships pre-cleaned; **real customer FCS does not**. Skipping QC lets
debris, doublets, dead cells, and beads leak into clustering and create junk clusters. QC is a
**first-class, modality-aware stage**. Cutoffs are **data-driven, reviewable, and multivariate**
(see `references/threshold_selection.md`); per-stage counts are logged and **each gate is drawn** to
`figures/gate_<sample>_<gate>.png` with the actual cutoff (1D) or robust density contours (2D):
- **Threshold meaning is explicit.** Viability, DNA-intact, and bead thresholds use a supported
  **density valley** (antimode) when one exists. FSC/SSC debris floors intentionally remain permissive
  lower percentiles; their figures label the applied percentile line and show any defensible valley as
  a dashed diagnostic alternative, never as the applied cutoff. Selectors are
  `--gate-method valley|gmm|otsu|percentile|control|auto` (default `auto`). An **honesty
  guard** (Hartigan dip test `--dip-alpha` + minimum valley depth `--valley-min-depth`) refuses to
  invent a cutoff on a unimodal or shallow channel and falls back to a conservative percentile with a
  **REVIEW** flag.
- **Debris gates:** permissive one-dimensional FSC and SSC lower-tail floors are always applied for
  flow/already-unmixed spectral data. Robust FSC × SSC density contours and envelope are a diagnostic proposal
  (`apply=N`) unless a reviewer enables it. flowClust never discards all but its largest component.
- **Singlets:** conventional-flow runs write an FSC-A × FSC-H density diagnostic with both applied
  ratio boundaries drawn. CyTOF runs use both detected Ir191/Ir193 DNA channels when present: the low
  bound requires signal in both channels, the high bound excludes events high in either channel, and a
  separate DNA1 × DNA2 density diagnostic is review-only. `--dna-channels` provides an exact override.
- **Viability × lineage-marker diagnostic** (`--lineage-markers CD15,CD66b,...`):
  in addition to the viability × scatter / Pt × DNA 2D diagnostic above, a **viability × lineage-marker**
  biaxial plot (`gate_<sample>_live_dead_lineage_<marker>.png`) is emitted for each resolved marker.
  **Rationale:** neutrophils take up viability dye without being dead, so a viability × scatter (FSC)
  view alone can misgate live neutrophils as dead; plotting viability against a granulocyte lineage
  marker (CD15/CD66b/CD16/CD11b) makes the "dye-bright but live" population obvious to a reviewer.
  `--lineage-markers` takes comma-separated antigen names, resolved against the panel antigens
  (word-boundary match, so `CD15` matches `CD15 FITC`); if omitted, auto-detects the granulocyte-first
  defaults `CD15,CD66b,CD16,CD11b` present in the panel. In flow/already-unmixed spectral data,
  disproportionate removal from a confidently resolved lineage raises `REVIEW_lineage_bias` and
  blocks automatic viability application. `--lineage-bias-delta-pp` and `--lineage-min-events` are
  provisional, configurable, and recorded. CyTOF keeps this as a diagnostic only.
- **Control/FMO anchoring** (`--controls controls.csv`): `$PnN` and `$PnS` use one ambiguity-checking
  resolver. Controls reuse sample compensation/transformation and every supplied row must be `USED`
  in `control_usage_state.csv`; otherwise the run stops non-pass.
- **Time-based acquisition QC** (`--time-qc off|report|remove|auto`, default `auto`):
  data-driven **flow-rate**, **signal-stability**, and **margin-event** checks over the acquisition
  Time channel flag unstable acquisition (fluidics clogs, bubbles, drift, boundary pile-ups) and draw
  `figures/time_qc_<sample>.png`. Default `auto` = **report** for flow (flags but **removes 0 events** —
  diagnostics-on); `remove` opts in to dropping flagged events; `off` disables. `--time-qc-backend`
  supports only `native`; unsupported backends and invalid modes stop explicitly. Rate and signal
  checks use 100 bins and the requested `--time-qc-mad-k`. Margin checks use acquisition-scale
  values and acquisition bounds before compensation, with event correspondence enforced. Reported
  percentages pool flagged-event counts over all events rather than averaging sample percentages.
- **Reviewable, two-pass by default.** Every proposed cutoff is written to an
  **editable template** (`gating_thresholds_template.csv`; see
  `assets/gating_thresholds_template.csv`). A normal run (`--gate-review propose`, the
  default) writes the template + per-gate figures and **stops before writing the SCE** for human
  review; edit `final_cutoff`/`apply`, then rerun with `--gate-review apply --thresholds
  <edited.csv> --review-receipt <receipt.json>` to produce the SCE. The receipt must identify an external
  human decision and match the SHA-256 fingerprints of the proposal and edited threshold file; the
  agent cannot review its own proposal. `gating_state.csv` is the canonical state read by the manifest and
  report. `--gate-review auto` persists the proposal and continues only when `qc_uncertainty.csv`
  has zero data rows; otherwise it stops at the proposal checkpoint (exit 3) with the reason.
  A successful unattended application records `REVIEW_auto_applied` and a receipt-state row with
  `source=auto`, reason `no QC uncertainties`; it does not claim human approval. Same-input auto
  re-entry is permitted only when the persisted proposal fingerprint matches. Supplied builtin
  `--thresholds` require reviewed `apply`. `--qc off` remains visibly non-pass.
  OpenCyto uses the same proposal/input/receipt contract: review `opencyto_template_proposed.csv`,
  then pass the reviewed CSV through `--gate-template` with `--gate-review apply --review-receipt`.
  Keep the terminal-population choice and scientific inputs unchanged between proposal and apply;
  generate a fresh proposal if those change. Both engines write a receipt state whenever an SCE is produced.
  `--threshold-scope per_sample|pooled|batch` chooses per-sample cutoffs (default), a harmonized
  across-sample median (`pooled`), or **within-batch** confidence-weighted harmonization (`batch`,
  which shrinks each gate's cutoff toward its within-batch consensus by
  `(1 − valley_confidence)·--harmonize-shrink`, so confident valleys stay put and only uncertain
  ones are pulled together).
- **QC uncertainty is a separate checkpoint.** Ill/singular compensation, acquisition anomalies,
  shallow/unimodal thresholds, excessive scatter/singlet removal, viability over-removal, or lineage
  bias are written to `qc_uncertainty.csv`. Ask about these only after the general gate review. The
  receipt must explicitly acknowledge every uncertainty ID before the run can continue as reviewed.
- **Modality gates.** Flow: FSC/SSC debris → **FSC-A/FSC-H singlet** → live/dead. CyTOF: **DNA
  intercalator** (Ir191/Ir193 intact) → **Gaussian-parameter doublet** (event length) →
  **cisplatin/viability** (Pt195) → **bead handling**. Both modalities also emit the
  **viability × lineage-marker diagnostic** (`--lineage-markers`) described above. By
  default beads are gated out; opt-in **bead-based signal normalization** — `--cytof-norm on|auto` runs
  `CATALYST::normCytof` (`--beads dvs`, `--cytof-norm-k`), which **skips** the bead-removal gate so bead
  events survive for normalization and are then removed by `normCytof` (`auto` = on when a bead channel
  is present). When a flow file has **height-only scatter** (FSC-H/SSC-H, no `-A` channels — e.g.
  older FACSCalibur exports), debris gating **falls back to the height channels** instead of silently
  removing nothing; the singlet gate (which needs A-vs-H) is then correctly skipped.
- Gates are automated with tunable defaults; if data is pre-cleaned, state that QC was skipped and why.
- **GATE GENTLY (hard rule).** Scatter/singlet gates default to **permissive** (debris ≈2nd pct;
  singlet band = median ± **4·MAD**, exposed via `--singlet-mad-k`/`--debris-pct`). **Over-gating is a
  silent failure mode** — a too-tight scatter/singlet gate discards *real* cells non-randomly and
  biases every downstream number without ever erroring. `01` logs in→out per gate and raises a loud
  **OVER-GATING ALARM** when scatter+singlet jointly remove >30% (`--overgate-alarm`). Never use a
  hardcoded ratio window (e.g. 0.75–1.25).
- **If results disagree with manual gating, determine the direction from the reconciliation table.**
  Inspect the specific gate responsible; do not assume every mismatch means the scatter gate is too
  tight, and do not recalibrate viability to paper over an upstream problem.
- See `references/qc_gating.md` and `references/threshold_selection.md`.

