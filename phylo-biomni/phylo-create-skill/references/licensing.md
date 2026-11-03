# Licence and data-source constraints

Load this reference before adding a dependency, database, or dataset. User-uploaded data plus the
standard scientific Python/R stack need no extra dependency review.

Check code licences and data terms separately. A free download is not permission to ingest or
redistribute data in a commercial AI product. Read the repository licence and, for data, the
supplier terms; package-index labels and papers are insufficient.

## Rules

- Permitted code licences: MIT, Apache-2.0, BSD-2/3-Clause, ISC, MPL-2.0, PSF, Unlicense, CC0,
  CC-BY, GPL, and LGPL. Record when copyleft code is vendored or statically embedded.
- Review before use: AGPL, no licence, non-commercial/academic-only terms, or anything unclear.
- Never use Enamine catalogue data without written authorization; its terms prohibit incorporation
  into computational/AI systems. Use appropriately licensed alternatives such as limited ZINC data.
- Never vendor, import, clone, or install SCAM Detective: its repository has no licence grant.
- ChEMBL is CC BY-SA 3.0: include attribution and ShareAlike notice in the report.
- ZINC/ZINC20 is usable, but do not redistribute major portions without permission; cite the paper.
- Treat every other supplier catalogue as restricted until its terms are verified.

## Customer boundary

Public company names in repository names, copyright notices, methods, databases, or literature
citations are legitimate attribution. Never include engagement context: customer preferences,
internal projects, compounds, target IDs, file paths, example data, or customer-specific conventions.
Ask whether the statement would exist if the skill had no customers; keep public attribution and
remove relationship framing.

## Record and stop conditions

Create `DATA_SOURCES.md` with one row per dependency and data source: source, version, licence/terms,
commercial-use decision, evidence, and any reader-facing obligation. Keep excluded sources in the
table with the reason. If a required capability exists only under prohibited or unclear terms, report
the blocker and stop rather than silently substituting or assuming permission.
