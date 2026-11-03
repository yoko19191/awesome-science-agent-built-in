# Execution interface after vnext migration

The domain computation is unchanged: `scripts/prioritize_cases.py --input <case-directory> --entry-mode <mode>` still validates and ranks one case, writing under `BIOMNI_RESULTS`. Its mode remains a label-only context; it does not change ranking or evidence semantics. Prefer the canonical public `scripts/run_skill.py` workflow rendered in `SKILL.md` for package verification, execution and finalization; do not invoke the private domain script as an alternative way to bypass integrity checks.

All four analytical outputs retain their paths, formats and meanings: `hypotheses.tsv`, `evidence_dossier.json`, `reanalysis_diff.json`, and `figures/evidence_completeness.png`. The canonical scientific-report route adds `report_facts.json`, `infographic.png`, and `report_rare-disease-case-prioritization.pdf`, bound to the analysis through the runner-owned receipt. Follow the generated commands exactly.

The specification is now `phylo-analysis-spec/1`. The legacy `reporting_contract` extension and customized report helper are replaced by the unmodified vnext runner/helper plus mandatory `references/report-policy.md` and external domain report acceptance. Historical receipts and citation checks apply only to their original package versions. This replacement does not establish fresh scientific acceptance.
