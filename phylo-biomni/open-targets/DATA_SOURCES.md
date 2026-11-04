# Data sources and dependency ledger

## External data sources

| Source | URI | License | Auth | Access date | Role |
|---|---|---|---|---|---|
| Open Targets Platform GraphQL API | https://api.platform.opentargets.org/api/v4/graphql | CC0 1.0 (data); API free, no auth | None | 2026-08-16 | Target-disease associations, evidence, study/credible-set metadata |
| Open Targets bulk data (not used by this skill) | https://platform-docs.opentargets.org/data-access/datasets | CC0 1.0 | None | 2026-08-16 | Referenced in bulk-handoff guidance only; this skill does not download bulk data |

This skill retrieves data exclusively through the public GraphQL API. It does not download raw GWAS
summary statistics, controlled-access data, or participant-level data. Bulk data is referenced only
in the over-budget handoff message as the recommended route for genome-scale work.

Every production run declares exactly one source mode: `live`, `versioned_snapshot`, `user_upload`,
or `fixture`. The operation ledger, provenance, facts, result-table rows, report status strip, and
receipt must agree. Fixture replay is validation evidence and cannot be relabeled as a live result.

## Python dependencies

All dependencies are pre-installed on the Biomni platform. No `pip install` is required at runtime.

| Package | Version observed | License | Role |
|---|---|---|---|
| urllib (stdlib) | Python 3.11 | PSF | HTTP client (no `requests` dependency) |
| matplotlib | 3.10.5 | PSF-based (Matplotlib license) | Deterministic figure rendering |
| numpy | (matplotlib dep) | BSD-3-Clause | Numerical arrays for figures |
| pandas | 2.3.1 | BSD-3-Clause | Not imported by production code; available for downstream analysis |
| reportlab | 4.4.3 | BSD-3-Clause | PDF report generation |
| pypdf | 6.14.2 | BSD-3-Clause | PDF text extraction and image-count embedding check |
| Pillow | 11.3.0 | HPND (PIL Software License) | Figure blank-check and fallback page rendering |
| pyyaml | 6.0.2 | MIT | eval.yaml parsing (eval runner only) |

The report gates prefer `pypdf`/PyMuPDF and use Poppler's `pdfinfo`, `pdfimages`, `pdftotext`, and
`pdftoppm` as deterministic system fallbacks. If neither route is present, the affected mandatory
check returns `not_evaluable`; it never becomes a success.

## Commercial-use classification

| Component | Commercial use | Basis |
|---|---|---|
| Open Targets data | Permitted | CC0 1.0 Public Domain Dedication |
| Open Targets API | Permitted (read-only, no auth) | No documented restriction; no SLA |
| urllib / matplotlib / numpy / pandas / reportlab / pypdf / Pillow / pyyaml | Permitted | Permissive open-source licenses (PSF, BSD-3-Clause, HPND, MIT) |

No component is copyleft (GPL/AGPL) or restricted-use. No component requires a paid license,
registration, or commercial agreement for the use this skill makes of it.

## What this skill does NOT retrieve

- Raw GWAS summary statistics (effect sizes, standard errors, allele frequencies per variant)
- Controlled-access or dbGaP-style data
- Participant-level or individual-level data
- Causal inference results (Mendelian randomization, colocalisation as causal proof)
- Clinical recommendations or prescribing guidance

These are handed to source-specific skills when needed.
