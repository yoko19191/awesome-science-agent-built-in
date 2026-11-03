# Clustering and reviewed resolution selection

## Primary clustering and rare-mode stability

Cluster all post-QC events on type/lineage markers. State or functional markers do not define the
population partition. `CATALYST::cluster()` trains one primary FlowSOM and stores its SOM codes plus
ConsensusClusterPlus partitions. Broad, standard, and fine analyses do not train a second FlowSOM merely
to choose the resolution. Rare-population refinement is different: after the primary clustering is
preserved, it compares candidate event sets against two additional deterministic FlowSOM seeds because a
small branch that exists only under one random initialization is not adequate evidence.

Before clustering, establish these in separate, plain-language questions:

1. Is the objective broad lineages, standard subsets, fine subsets, or rare-population discovery?
2. Is there a particular population that must not be merged?
3. Which measured markers should be positive and negative for that population?
4. Is there a minimum plausible frequency, and should it recur in any, most, or all samples?
5. Should merge/keep decisions be reviewed now, or may the run continue provisionally?

Ask these as separate, plain-language questions. `--resolution-goal broad|standard|fine|rare` records the
first answer. The metacluster search ceiling is data-adaptive: it is the smaller of occupied SOM nodes and
the nodes present in each consensus resample. With a 10x10 SOM and 80% resampling this evaluates through
`k=80`. A lower explicit `--maxK` remains authoritative; an infeasible higher value stops rather than being
silently accepted.

## Why delta area is not enough

CATALYST's delta-area ordinate already represents the change in consensus-CDF area from the preceding
resolution. Do not apply `diff()` to it again. Delta area remains useful provenance but is not the sole
selection rule: an elbow can prefer an overly coarse partition, especially when population sizes are
imbalanced or a rare subset is important.

The skill reruns only the inexpensive ConsensusClusterPlus calculation on the stored SOM codes, with
CATALYST's identical parameters and seed. Every reconstructed partition must match CATALYST's stored
partition up to arbitrary label names. A mismatch is
`INCOMPLETE_resolution_diagnostic_mismatch`.

For every `k`, `resolution_candidates.csv` records:

- CATALYST's original delta-area value;
- PAC, the fraction of off-diagonal consensus values in the published 0.1–0.9 ambiguity interval;
- median and minimum within-cluster consensus;
- event-weighted and cluster-balanced silhouette plus Davies–Bouldin index (DBI) over SOM codes;
- total/minimum event counts, minimum frequency, and minimum sample coverage.

A candidate is stable only when both event-weighted and cluster-balanced silhouette are positive, median
cluster consensus is above 0.5, and it is not Pareto-dominated across PAC (lower), median/minimum consensus
(higher), both silhouette summaries (higher), and DBI (lower). A rare-preserving global candidate must
also have minimum cluster consensus above 0.5, preventing one unstable small cluster from hiding behind a
strong median. The combined score is the equal-weight median metric rank; delta area is deliberately not
ranked a second time.

- Every run reports distinct broad, balanced, fine, and rare-preserving global recommendations.
- Broad considers the lower third of evaluated resolutions and breaks ties toward smaller `k`.
- Balanced considers the full data-adaptive range; fine considers its upper half.
- Rare-preserving considers the upper quarter, requires minimum-consensus protection, and remains a
  global starting point rather than evidence that a named rare population was recovered.
- Standard, fine, and rare-preserving choices use all quality metrics and break exact ties toward larger
  `k`; rare mode no longer blindly chooses the maximum stable `k`.
- Automatic application always remains `REVIEW_auto_applied`; only an exact reviewed `k` can pass.

The diagnostic and delta-area figures identify the evidence across `k` and mark the proposed or selected
`k` with a labelled vertical line. UMAP/t-SNE are qualitative visual review only and never select `k`.

## Recovering from ambiguity without retraining

Proposal mode saves `sce_cluster_candidates.rds`, `resolution_candidates.csv`, the three best combined-
rank options, and the finest defensible option when distinct. The user may select an exact `k`, accept
the finest defensible fallback, or stop and revise markers/granularity.

The finest defensible fallback is the largest candidate with positive weighted silhouette and a
combined rank in the better half of evaluated resolutions. Applying it unattended produces
`REVIEW_ambiguous_auto_applied`, never pass. If no candidate has positive silhouette, no automatic
fallback is allowed: the state is `INCOMPLETE_weak_structure`. An exact user override then requires a
recorded `--decision-reason` and produces `REVIEW_weak_structure_user_override`.

Resume with:

```bash
Rscript scripts/02_cluster.R \
  --resume-candidates /mnt/results/run/sce_cluster_candidates.rds \
  --outdir /mnt/results/run \
  --resolution-review apply --k 18 --resolution-goal fine --seed 1234 \
  --decision-reason "Preserves the reviewed subsets without unsupported splitting" \
  --review-receipt /mnt/results/run/resolution_receipt.json
```

The resume contract validates cell, marker, transform, sample, analysis goal, seed, effective-maxK, and
candidate-table fingerprints, plus `clustering_fingerprint` over SOM codes, metacluster codes,
and per-event cluster IDs. Checkpoints predating this fingerprint must be re-clustered.
An omitted goal inherits the stored goal; an explicit mismatch stops the
run. A successful resume applies stored metacluster codes and
writes `sce_clustered.rds`; it does not rerun FlowSOM or ConsensusClusterPlus. A reviewed pass requires
a matching external receipt bound to the exact candidate-table fingerprint, filled
`resolution_decision.csv`, and selected `k`.

## Marker-aware rare-population refinement

Run `02b_refine_rare.R` only when the declared objective is rare-population discovery. It requires a
population name, at least two measured positive/negative defining markers, an optional minimum frequency,
and a recurrence choice. The script searches distinct metacluster branches across the stored hierarchy;
identical event sets found at several `k` values are collapsed so they cannot crowd the shortlist.

Marker contrasts are calculated against the nearest competing SOM nodes, not the entire dataset. This
local comparison prevents abundant unrelated lineages from making a weak rare branch look artificially
distinct, but it does not define biological marker positivity or prove identity. Candidate ranking
combines marker direction, contrast magnitude, local separation, frequency,
sample recurrence, and event-set Jaccard stability against two additional fixed seeds. Reference labels
are not an input.

The primary global clustering is never overwritten. The refinement writes `sce_rare_candidates.rds`,
`rare_population_candidates.csv`, a concise shortlist, review figure, and state file. A fully supported
automatic choice is `REVIEW_rare_auto_proposed`. Any persisted candidate may be selected. A supported
shortlist choice is `REVIEW_rare_user_selected`; non-shortlisted and partial-marker choices require a
recorded reason plus external receipt and remain `REVIEW_rare_user_override`. Neither state proves
identity. Rejecting all candidates records `INCOMPLETE_rare_resolution` and suggests revised phenotype,
control-derived thresholds, a manual event-level gate, orthogonal validation, or retaining only the global
clustering.

Rare resume validates event, candidate, clustering, and positive/negative marker-role fingerprints.
Explicit `--population`, `--positive-markers`, `--negative-markers`, `--recurrence`, and
`--minimum-frequency-pct` must match the stored contract; omitted options inherit it. Checkpoints
with a legacy marker fingerprint require fresh refinement from the clustered SCE.

Resume with `--resume-candidates sce_rare_candidates.rds`; applying a saved candidate does not repeat any
FlowSOM fit. Fill `rare_population_decision.csv` and bind its hash into the receipt. A selected branch
records `PASS_rare_deliverable` for workflow output, a separate `REVIEW_rare_*` selection state, and
`NOT_EVALUABLE_rare_identity` without independent event-level evidence.

## Merge review

Annotation review records one row per cluster with its proposed label, marker evidence, abundance,
coverage, final population, `merge_decision` (`merge` or `keep`), and `decision_reason`. Repeated
population names merge reproducibly only when marked `merge`. A complete, consistent decision for every
cluster can be `PASS_reviewed`. Legacy two-column maps remain readable but are `REVIEW_merge_pending`.

## References

- Van Gassen et al. FlowSOM. Cytometry A. 2015. doi:10.1002/cyto.a.22625.
- Wilkerson and Hayes. ConsensusClusterPlus. Bioinformatics. 2010. doi:10.1093/bioinformatics/btq170.
- Şenbabaoğlu et al. Critical limitations of consensus clustering in class discovery. Scientific Reports. 2014. doi:10.1038/srep06207.
- Nowicka et al. CyTOF workflow. F1000Research. 2017. doi:10.12688/f1000research.11622.1.
- Liu et al. Cyclone: accurate and robust cell clustering for single-cell flow cytometry data. Briefings in Bioinformatics. 2023. doi:10.1093/bib/bbad284.
