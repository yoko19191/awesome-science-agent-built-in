# Data Sources & Licenses

Production source declarations for this bounded retrieval workflow. Preserve query provenance and inspect current source terms before expanding use.

| Name | Type | Version | URI | License | Commercial status | Evidence | Included | Verification | Notes |
|---|---|---|---|---|---|---|---|---|---|
| ClinicalTrials.gov API v2 | public API | v2 | https://clinicaltrials.gov/data-api/api | U.S. Government public data (ClinicalTrials.gov Terms and Conditions) | allowed | ClinicalTrials.gov is a U.S. NIH/NLM public resource; its Terms and Conditions permit programmatic access and reuse of the data. | true | https://clinicaltrials.gov/about-site/terms-conditions | No API key required; rate limited to roughly 50 requests per minute. |

## Runtime dependencies

Production helpers require Python 3.10+, pandas, numpy, matplotlib, requests and Pillow. PDF inspection requires the Poppler `pdftoppm` and `pdftotext` commands. Check these before the run; missing inspection tooling prevents a final visual-review pass. Follow the selected report provider for its rendering dependencies.
