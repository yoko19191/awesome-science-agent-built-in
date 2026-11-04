# Regression evals — citation resolution

Nothing runs these automatically. They exist for the next person to edit the citation mechanism, so
a reintroduced numbering bug turns a test red instead of shipping.

**Run:** `python3 assets/eval/test_citation_resolution.py`
**Exit codes:** `0` all passed · `1` a test failed · `2` everything skipped (a failure, not a pass —
it means the skill's own modules could not be imported).

All fixtures are generated in code with synthetic identities (no real paper, DOI, PMID, author,
molecule, target, or run number), so the suite pins behaviour without leaking or hard-coding a
dataset. Each test asserts the right result is present **and** the specific wrong result is absent,
and each is discrimination-checked: reverting to the pre-fix behaviour (rewrite skipped, or gate
removed) turns it red.

| File | Defect it guards |
|---|---|
| `test_citation_resolution.py :: observed_16_to_7` | The shipped failure: bibliography renumbered to 7 while inline `[8]/[9]/[16]` kept session-global `references.jsonl` indices and resolved to nothing. |
| `… :: sparse_global_indices` | Non-contiguous session indices (e.g. `{5,12,30}`) must remap to `1..N`; the gate must reject sparse indices left in the body. |
| `… :: repeated_references` | One source cited many times must collapse to exactly one bibliography entry (not one per citation). |
| `… :: reordered_bibliography` | A bibliography whose entries are reordered but relabelled `1..N` by position — every label then carries the wrong source; caught by identity binding. |
| `… :: missing_labels` | An inline citation with no source record must fail closed at render time, and the gate must reject an inline `[N]` with no entry. |
| `… :: wrong_identity_binding` | A bibliography line under label `k` carrying a different source's identity token (the `[5]`/`[6]` wrong-paper case). |
| `… :: correct_mapped_report` | Positive control: a fully consistent report must PASS, so the gate is not a blanket rejector. |

The mechanism under test is `scripts/render_citations.py` (the single-source keyed transform) and
`scripts/report_qc.py :: assert_citations_resolve` (the fail-closed gate that re-validates the final
report against `citation_map.json`).
