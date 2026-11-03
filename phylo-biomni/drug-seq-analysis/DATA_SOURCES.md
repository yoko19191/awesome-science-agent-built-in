# Data Sources & Licenses

This file is generated from `skill_contract.json`; repair the contract and regenerate all derived files.

| Name | Type | Version | URI | License | Commercial status | Evidence | Included | Verification | Notes |
|---|---|---|---|---|---|---|---|---|---|
| DESeq2 | software | Bioconductor 3.x | https://bioconductor.org/packages/DESeq2/ | LGPL (>=3) | allowed | LGPL permits commercial use with the usual copyleft obligations for modifications to the library itself. | true | scripts/de_deseq2.R | Well-pseudobulk differential expression engine. |
| Python scientific stack (numpy, pandas, scipy, scikit-learn, matplotlib, seaborn) | software | Biomni environment | https://scipy.org/ | BSD-3-Clause | allowed | BSD-3-Clause permits commercial use. | true | scripts/run_analysis.py | QC, dose-response, signatures, and figures. |
| ReportLab (via pdf-report-generation) | software | Biomni environment | https://www.reportlab.com/ | BSD-3-Clause | allowed | BSD-3-Clause permits commercial use. | true | scripts/report_qc.py | Branded PDF report rendering. |
| LINCS L1000 / CMap | dataset | GSE92742 / GSE70138 | https://clue.io/ | clue.io / CMap data-use terms | no_prohibition_found | clue.io terms permit research use; commercial use is not explicitly prohibited but must be verified before commercial deployment. | false | references/connectivity_handoff.md | Available only through optional follow-up analysis in signature-reversal-lincs; not bundled or ingested. |
| MSigDB | dataset | caller-selected release | https://www.gsea-msigdb.org/ | MSigDB registration terms (varies by collection) | not_checked | Registration and per-collection terms not verified for commercial use. | false | references/targeted_panel.md | Optional caller-supplied gene sets; the package does not acquire or redistribute MSigDB content. |
