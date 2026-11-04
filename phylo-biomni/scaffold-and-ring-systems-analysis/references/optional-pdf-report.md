# Optional PDF report

Build a PDF only when the user said yes to the report question. Start only after
`run_skill.py finalize` and `run_skill.py verify-receipt` have both passed. The PDF sits outside the
run receipt, so it must never change or replace a declared output.

## Provider

Load and follow `pdf-report-generation`, using default Phylo styling. Use a different compatible
styling provider only when the user names it explicitly. Tenant, company or project context alone
does not select one.

## Content

Write the report to the results root as `report_scaffold-and-ring-systems-analysis.pdf`. Take every
number from `analysis_summary.json` or `frequency_table.csv`; never recompute or restate a number
from memory.

1. **Title and one-paragraph summary**: dataset file name, analysis, method, and the number of
   valid molecules analyzed (`counts.n_analyzed`).
2. **Method**: a plain-language description of the chosen method, taken from
   `choosing-an-analysis.md` and `methods.md`, with its citation. For `common_core`, give
   `min_core_fraction` and say that counts overlap.
3. **Input accounting**: a table of `n_rows`, `n_ok`, `n_acyclic`, `n_invalid_smiles` and
   `n_extraction_failed`. If any row was invalid or failed extraction, name a few examples from
   `molecule_assignments.csv`.
4. **Most frequent structures**: embed `figures/top_structures.png` as a captioned figure. Follow it
   with a table of the top 10 or 20 rows of `frequency_table.csv` (rank, SMILES, molecules,
   fraction, occurrences).
5. **Distribution**: say how concentrated the set is. Give the share of molecules covered by the
   top 1, 5 and 10 structures and the number of singletons (structures with `n_molecules == 1`),
   computed from `frequency_table.csv`. For `common_core` and ring-system methods, say that a
   molecule can be counted under several structures, so these shares are not a partition.
6. **Caveats**: the scientific caveats from `SKILL.md` that apply to the chosen method.
7. **References**: the citations for the chosen method from `methods.md`.

An infographic is optional here. Do not generate one unless the user asks for it.

## Publishing

Render in `/workspace` and then copy the PDF to the results root. Before replying, reopen it and
check that it opens, that the structure grid is visible, and that the method and counts match
`analysis_summary.json`.
