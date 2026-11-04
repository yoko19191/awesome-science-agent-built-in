# Open Targets offline eval suite

One executable test per observed defect from the structural repair brief (OT-01 through OT-12),
plus the mandatory generated-infographic addendum. Every test invokes production code from a
fresh temporary directory; no test touches the live API.

## Running

```bash
cd <package-root>
python3 -m pytest assets/eval/ -v
```

Exit codes: 0 pass / 1 one or more failures / 2 all skipped (treated as failure).

## Test files

| File | Finding | What it guards |
|---|---|---|
| `test_ledger.py` | OT-01, OT-12 | Failure-preserving ledger: success, body-error, transport-error, malformed-JSON, retry-supplements-not-replaces, drop-mutation detected |
| `test_quick_start.py` | OT-02 | No bare `requests.post().json()` outside the shipped helper; body-error fixture exits nonzero |
| `test_queries.py` | OT-03 | Nine-operation registry completeness, query hashes, semantic-nonempty checks |
| `test_pagination.py` | OT-06 | Bounded paginator: exhaustion, cursor repetition, truncation, completeness labels |
| `test_semantics.py` | OT-07, OT-08 | Discriminated record_type schema; forbidden datasource IDs; cross-labeling rejected |
| `test_budget.py` | OT-09 | Request planner: over-budget returns bulk handoff; bounded request proceeds |
| `test_visuals.py` | OT-11 | Deterministic figures: payload matches facts, swapped labels / changed scores detected |
| `test_recovery.py` | OT-12 | Audit-preserving recovery: retry_of / recovered_by links, zero-failure claim rejected |
| `test_infographic.py` | Addendum | GenerateImage lineage gate: missing trace, wrong filename, unembedded image, data-bearing prompt all rejected |
| `test_repair_r2.py` | OT-R2 | Prompt/image lineage, PDF hash binding, operation counts, disease-study concordance, strict JSON |
| `test_finalization_v1.py` | OT-R2 finalization | Ontology preservation, exact response hashes, retry linkage, typed visual-review states |
| `test_finalization_v2.py` | OT-F1-01 | Complete-prompt auto-execution, precise missing/ambiguity questions, API-envelope lineage, anonymous flattened-record rejection |
| `test_report_style_provider.py` | Styling rollout | Explicit provider selection, context-only non-selection, provider-specific PDF markers, style-free scientific content, canonical-PDF lineage, and stale-overwrite rejection |
| `test_report_image_sizing.py` | Report geometry | Wide, tall, ranking-chart, and heatmap images preserve their native aspect ratios while fitting page bounds |

## Discrimination principle

Each test must fail before the repair and pass after it. Break the code on purpose and watch it
fail before keeping it. A test that passes both before and after is not a test.
