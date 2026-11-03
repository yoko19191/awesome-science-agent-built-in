# Report and integrity boundary

Default newly authored executable analyses to `scientific-report`. Choose `artifacts-only` when
the user explicitly requests no report or the approved purpose clearly requires only machine
outputs, such as a pipeline intermediate or format conversion. Ask when intended deliverables are
ambiguous or conflicting. Record `presentation_profile` explicitly; the validator does not infer it.

The artifacts-only profile can still produce analysis PNGs; the canonical runner validates and
hashes them without report code. General workflows keep task-specific deliverables. Existing
packages retain their output contracts during repair unless a change is approved.

`scientific-report` derives exactly three presentation outputs:

- `report_facts.json` with the current source analysis-output path/hash map;
- `infographic.png` as the opening visual artifact;
- `report_<slug>.pdf` rendered through the default provider or an explicitly requested compatible
  provider.

This derivation is one public contract implemented in both the authoring specification and the
shipped runner. The paths are not independently configurable, and analysis outputs may not reuse
them or `run_receipt.json`.

`report_qc.py` checks only objective facts: provider availability and explicit selection,
facts-to-analysis hash binding, exact or tolerance-aware accounting, PNG validity, PDF signature,
successful text extraction, and successful rendering of every PDF page. It does not score hierarchy,
balance, captions, scientific interpretation, or aesthetics.

The canonical runner owns every byte hash. After analysis, do not rewrite its outputs while preparing
facts or presentation artifacts. Report-only renderings use different paths. Finalization rehashes all
analysis artifacts and fails on drift before adding presentation outputs to the single receipt.

Inspect every rendered report page externally. Record scientific or visual findings in campaign or
review evidence, not as generated package gates.
