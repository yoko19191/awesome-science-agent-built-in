---
name: scaffold-and-ring-systems-analysis
description: 'Use to find the scaffolds or ring systems in a small-molecule dataset
  (CSV/TSV/SMI of SMILES) and count how often each occurs. Scaffold analysis covers
  the whole core framework: Bemis-Murcko, generic Bemis-Murcko, or common-core fragments.
  Ring-system analysis covers fused or spiro ring sets, generic ring systems, or individual
  rings. Returns a ranked frequency table, per-molecule assignments, and a structure
  grid. Not for matched molecular pairs, R-group SAR, clustering, or property prediction.'
category: drug_discovery
visibility: public
starting-prompt: Can you download all FDA-approved small-molecule drugs and analyze
  their ring systems?
---
<!-- spec: phylo-analysis-spec/1 -->

# Scaffold And Ring Systems Analysis

## When to use

Use to find the scaffolds or ring systems in a small-molecule dataset (CSV/TSV/SMI of SMILES) and count how often each occurs. Scaffold analysis covers the whole core framework: Bemis-Murcko, generic Bemis-Murcko, or common-core fragments. Ring-system analysis covers fused or spiro ring sets, generic ring systems, or individual rings. Returns a ranked frequency table, per-molecule assignments, and a structure grid. Not for matched molecular pairs, R-group SAR, clustering, or property prediction.

## Scope

Reduce every valid molecule in a SMILES dataset to its scaffolds or ring systems with one chosen, named method, and rank the resulting structures by how many molecules contain them. Invalid and acyclic molecules are reported, never dropped silently.

## Inputs and prerequisites

One molecule table: a CSV or TSV with a SMILES column, or a headerless .smi file of SMILES with optional identifiers.

Requirements:

- Before running, ask the user the questions in references/choosing-an-analysis.md, in order: scaffold or ring-system analysis, then the method for that analysis, then whether they want a PDF report.
- A CSV or TSV must have a header row containing the SMILES column named by --smiles-column; a .smi file has SMILES in the first whitespace-separated field and an optional identifier in the second.
- When --id-column is given it must exist and hold a unique, non-empty identifier on every row; otherwise the 1-based row number is the identifier.
- Salts and mixtures are reduced to their largest organic fragment and neutralized before analysis.

Reject the input without success artifacts when:

- The file is missing, empty, not UTF-8, or not a .csv, .tsv, or .smi table.
- The SMILES or identifier column is absent or matches more than one header, or identifiers are empty or duplicated.
- No row yields a valid RDKit molecule that contains a ring.
- The chosen method does not belong to the chosen analysis, --min-core-fraction is outside (0, 1), or --top-n is below 1.

Use the printed **Skill Directory** as `SKILL_DIR`. Do not edit the installed package.
Run once in a clean results directory; do not repair or retry in place.

## Analysis workflow

The canonical runner invokes one entrypoint, which performs these scientific steps in order:

1. Validate the table, the SMILES and identifier columns, and that the method belongs to the chosen analysis before writing any output.
2. Parse every SMILES with RDKit, keep the largest organic fragment, neutralize charges, and mark rows that fail to parse or have no ring.
3. Reduce each valid molecule to its structures with the chosen method: Bemis-Murcko, generic Bemis-Murcko or common-core fragments for scaffolds; ring systems, generic ring systems or individual SSSR rings for ring systems.
4. Count, for every distinct canonical structure, the molecules that contain it and its total occurrences, then rank by molecule count and size.
5. Write the ranked frequency table, per-molecule assignments, a run summary, and a grid image of the most frequent structures.

## Runtime interface

The only executable analysis entrypoint is `scripts/analyze_scaffolds.py`. Invoke it only through
`scripts/run_skill.py`; smoke validation and applications use this same path.
Outside the domain process, treat packaged Python modules as CLI-only implementation files.
Do not import them for inspection or presentation: imports can create cache files and
invalidate the frozen package. Use canonical finalization or independent standard tools.

| Parameter                                       | Type    | Allowed values                                                                                                                            | Required | Default      | Scientific effect                                                                                                                                                                                                                                                                                                                                                                                                     |
| ----------------------------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------- | -------- | ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `analysis` / `--analysis`                   | string  | `"scaffold"`, `"ring_system"`                                                                                                         | true     | `—`       | scaffold reduces each molecule to its whole core framework (rings plus the linkers between them); ring_system cuts linkers and reports each fused or spiro set of connected rings separately.                                                                                                                                                                                                                         |
| `method` / `--method`                       | string  | `"bemis_murcko"`, `"generic_bemis_murcko"`, `"common_core"`, `"ring_systems"`, `"generic_ring_systems"`, `"individual_rings"` | true     | `—`       | Scaffold methods: bemis_murcko keeps atom and bond types; generic_bemis_murcko turns atoms to carbon and bonds to single; common_core finds shared cores by cutting acyclic single bonds (Hussain-Rea fragments). Ring-system methods: ring_systems merges fused and spiro rings and keeps exocyclic double bonds; generic_ring_systems is its all-carbon, single-bond form; individual_rings reports each SSSR ring. |
| `smiles-column` / `--smiles-column`         | string  | declared type                                                                                                                             | false    | `"smiles"` | Header of the SMILES column in a CSV or TSV (matched case-insensitively); ignored for .smi files.                                                                                                                                                                                                                                                                                                                     |
| `id-column` / `--id-column`                 | string  | declared type                                                                                                                             | false    | `""`       | Header of a unique molecule identifier column; empty uses the .smi name field or the 1-based row number.                                                                                                                                                                                                                                                                                                              |
| `min-core-fraction` / `--min-core-fraction` | number  | declared type                                                                                                                             | false    | `0.67`     | common_core only: a fragment counts as a core only when it keeps more than this fraction of the parent molecule's heavy atoms. Lower values admit smaller, more widely shared cores.                                                                                                                                                                                                                                  |
| `top-n` / `--top-n`                         | integer | declared type                                                                                                                             | false    | `30`       | Number of most frequent structures drawn in the structure grid; the frequency table always lists every structure.                                                                                                                                                                                                                                                                                                     |

Map user-requested variations only to these declared parameters. A bounded method or tool
choice remains an internal implementation detail of the one entrypoint. If the requested
deviation is not declared here or would change inputs, workflow semantics, or output meaning,
stop and request a new package revision rather than bypassing the runner or editing the package.

## Scientific caveats and limitations

- **Assumption:** Frequencies describe this dataset only. They reflect how the set was assembled (a single series, a vendor library, ChEMBL drugs) and are not a measure of drug-likeness or novelty.
- **Limitation:** Generic forms (generic Bemis-Murcko, generic ring systems) turn every atom into carbon and every bond into a single bond, so distinct chemotypes such as benzene, pyridine and cyclohexane merge into one entry.
- **Limitation:** Common-core fragments overlap: one molecule contributes to many cores, including the molecule itself, so counts do not add up to the number of molecules and fractions are not a partition.
- **Watch Out:** A shared scaffold or ring system is a structural grouping, not a chemical series, a binding mode or SAR evidence. Stereochemistry is kept only by the atom-typed methods, and acyclic molecules have no scaffold.

Source, software, and licensing details are recorded in `DATA_SOURCES.md`.

## Additional guidance

- Read [`references/choosing-an-analysis.md`](references/choosing-an-analysis.md) when before running: it lists the three questions to ask the user, what each method means, and how the answers map to parameters.
- Read [`references/optional-pdf-report.md`](references/optional-pdf-report.md) when the user asked for a PDF report, after the run is finalized and verified.
- Read [`references/methods.md`](references/methods.md) when you need the algorithm details, the differences between methods, or citations.

## Outputs

| Output                         | Kind  | Stage    |
| ------------------------------ | ----- | -------- |
| `frequency_table.csv`        | csv   | analysis |
| `molecule_assignments.csv`   | csv   | analysis |
| `analysis_summary.json`      | json  | analysis |
| `figures/top_structures.png` | image | analysis |

## Execute

Resolve the actual user input. Then run the canonical runner, adding one
`--param name=<JSON value>` argument for each non-default parameter:

```bash
python3 "$SKILL_DIR/scripts/run_skill.py" execute --input "$INPUT"
```

The runner verifies the frozen package, constructs the entrypoint command, binds the actual
input and parameter values, rejects dirty output paths, and hashes every analysis output.

## Finalize and verify

```bash
python3 "$SKILL_DIR/scripts/run_skill.py" finalize
python3 "$SKILL_DIR/scripts/run_skill.py" verify-receipt
```

A terminal pass is valid only while the frozen package, entrypoint, input, and every declared
output retain the hashes recorded in the single `run_receipt.json`.

Return the requested scientific artifacts and a concise result summary. Keep package and
receipt internals out of the normal user-facing response.
