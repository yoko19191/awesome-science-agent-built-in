# Data sources

This skill is **self-contained and offline**. Its analysis entrypoint
(`scripts/prioritize_cases.py`) reads only the case bundle supplied at run time and uses
only the Python 3 standard library plus matplotlib. It performs **no network access**,
queries **no external knowledge base**, and bundles **no patient data**.

## Runtime inputs (supplied by the caller, per invocation)

The caller provides one case-bundle directory. None of these are shipped inside the
package; they are the user's own case data.

| File | Required | Format | Role |
|------|----------|--------|------|
| `phenopacket.json` | yes | GA4GH Phenopacket, schema major version 2 | subject id, case id, present/absent phenotype terms |
| `candidates.tsv` | yes | controlled-vocabulary TSV, one row per allele | disease-gene hypotheses and their per-allele annotations |
| `evidence_manifest.tsv` | yes | TSV | declares every cited evidence identifier with source type, version/date, and verification status |
| `family.tsv` | optional | TSV | family/relatedness context (drives the relatedness hard stop) |
| `phenotype-state-companion.json` | optional | JSON | supplemental phenotype-state notes (parsed; never converts a missing term into an absent one) |
| `prior-evidence-dossier.json` | optional | this skill's dossier schema | prior run for reanalysis diffing |

All disease-gene relationships supplied in `candidates.tsv` are assumed **already
established**; the skill does not establish novel associations and does not consult any
external gene-disease resource to do so.

### Evidence source-type semantics (`evidence_manifest.tsv`)

Each cited evidence row declares a `source_type` and a `verification_status`. The engine
does not fetch, rank, or otherwise judge these sources; it uses only their **declared** type
and status to gate concordance:

- A **strong-tier** `gene_disease_validity` (`definitive`/`strong`/`moderate`) may support a
  concordant hypothesis only when its resolved, **verified** `gene_disease_source` is a
  *curated gene-disease assertion/curation* source type. Accepted curated types are
  `gene_disease_validity`, `gene_disease_curation`, `gene_disease_assertion`,
  `curated_gene_disease_assertion`, `clingen_gene_validity`, and `expert_panel_curation`.
- A source whose type is merely `publication` (or any non-curated type) is treated as
  **insufficient** to establish a disease-gene association: one publication alone cannot
  establish the association, and publication quality is never inferred.
- Any cited source (`classification_source` / `gene_disease_source`) that resolves but is
  `unverified` cannot support concordance; an absent or `unresolvable` cited source is a
  hard stop.

These are declarative semantics over caller-supplied metadata only; no external database is
queried. See `references/evidence-gating-model.md` for the full gate cascade.

## Report-time references (bundled, offline)

The reader-facing PDF report cites a small, fixed set of **methodological and standards** references
that are bundled inside the package at
[`references/bibliography.json`](references/bibliography.json) and pinned by the reporting requirements in
[`references/report-policy.md`](references/report-policy.md). These entries (the GA4GH Phenopacket schema, the Human
Phenotype Ontology, Mondo, the ClinGen gene-disease clinical-validity framework, and the ACMG/AMP
sequence-variant interpretation standards) are **fixed methodological context only**:

- They are the sole source of the report's numbered `[N]` citations; the report uses **only** these
  entries, in the pinned order, each with its version-of-record DOI.
- They are **not** a disease-specific or queryable knowledge source, are **not** used to perform
  ACMG/AMP variant classification, and are **not** used to establish or infer any gene-disease
  association. Citing the ClinGen framework paper is a methodology citation, not a query of the
  ClinGen (or any other) online service.
- The report step performs **no** literature search and **no** network retrieval of any kind:
  `LiteratureSearch`, web browsing, and any other network/retrieval tool are prohibited at report
  time, and no reference may be added, removed, reordered, or substituted. The bundled
  version-of-record DOIs are authoritative (for example, the ClinGen framework is cited as
  `10.1016/j.ajhg.2017.04.015`; the bioRxiv preprint DOI `10.1101/111039` is explicitly rejected).

The canonical report QC validator (`scripts/report_qc.py`) invokes Poppler command-line tools
`pdftotext` and `pdftoppm` for objective text extraction, page rendering and artifact-integrity
checks. It does not enforce the domain bibliography contract above. Those exact citation rules
remain mandatory in `references/report-policy.md` and are checked by the external acceptance
harness. The platform report workflow and independent acceptance review inspect visual quality
and every report page; successful rendering alone is not visual or scientific acceptance.

## Software dependencies and licenses

| Component | Version | License | Use |
|-----------|---------|---------|-----|
| Python standard library | 3 | PSF License | I/O, parsing, hashing, CLI |
| matplotlib | (environment-provided) | matplotlib/BSD-style (PSF-based) | the single evidence-completeness figure |
| Poppler command-line utilities (`pdftotext`, `pdftoppm`) | environment-provided; record installed version per run | GPL; retain the installed distribution's exact notices | external subprocesses for PDF text extraction and page rendering |

The platform-provided `pdf-report-generation` presentation workflow is not bundled in this package.
Poppler binaries are supplied by the runtime and are not included in the skill archive. Upstream
source and licensing notices: https://gitlab.freedesktop.org/poppler/poppler . This dependency
inventory describes actual use; it is not a redistribution or deployment-license approval.

## Author cross-check material (NOT shipped in this package)

During authoring, the entrypoint's Phenopacket parser was exercised against two public,
de-identified, literature-derived phenopackets from the **Phenopacket Store**
(BSD-3-Clause). These files were used only to confirm that the parser reads real
schema-v2 phenopackets (subject id, present terms, and `excluded: true` absent terms); no
case state was asserted from them, and they are **not** included in the package. The
Phenopacket Store license text is retained with the authoring materials, outside the
package. All synthetic evaluator fixtures and expected assertions are likewise retained
outside the release package.

## Explicitly not used

- No network calls of any kind at run time, including at report time: the report cites only the
  bundled offline bibliography (see "Report-time references (bundled, offline)").
- No OMIM, Orphanet, ClinGen, ClinVar, gnomAD, Exomiser, or other online
  annotation/prioritization service (citing the ClinGen gene-disease clinical-validity framework
  *paper* as bundled methodological context is not a query of the ClinGen service).
- No large language model or external inference service.
- No protected health information (PHI) is bundled, cached, or transmitted.
