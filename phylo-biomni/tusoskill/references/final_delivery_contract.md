# Final delivery contract

The final deliverable should be easy for the user to run, inspect, and extend.

## Required artifacts

- Final method code.
- `README.md` with installation and usage.
- `reproduce.sh` or equivalent one-command reproduction script.
- `run_manifest.json` with commands, versions, and expected outputs.
- `benchmark_card.md` describing the frozen evaluator.
- `method_card.md` describing the final method, assumptions, inputs, outputs, and limitations.
- `final_report.md` — a detailed technical report of the final method and everything attempted along the way. Generate the skeleton with `python scripts/biomni_method_state.py render-report --root <run_dir>` (which pre-fills the complete attempt history and derivation history from the ledgers), then complete the prose sections. The report must cover rejected and archived candidates with their reasons, not only accepted ones.
- Dependency notes and any data-source manifests.

## Final audit

Before packaging:

1. Re-run the selected method from a clean working copy.
2. Re-run at least one baseline.
3. Verify protected hashes or file access boundaries.
4. Confirm auxiliary data provenance and leakage status.
5. Trim unused code, flags, dependencies, and files.
6. Run smoke tests.
7. Keep total file count manageable.
8. Create a ZIP or repository bundle only after validation.
