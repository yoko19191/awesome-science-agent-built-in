# Choosing an analysis

Ask these questions one at a time, in this order, before running anything. Do not pick an answer for
the user. The only exception is when their request already answers a question without ambiguity
(for example "Bemis–Murcko scaffolds of this file, no report"). When the dataset or its SMILES
column is unclear, settle that first.

## 1. Scaffold or ring-system analysis?

Offer both options with these explanations:

- **Scaffold analysis**: the whole core framework of each molecule. That means all of its rings
  plus the acyclic linkers that join them, with substituents removed. It answers "which chemical
  series or chemotypes make up this set?" Each molecule gets a single framework, except under the
  common-core method.
- **Ring-system analysis**: the individual ring systems, with linkers removed. Rings that share
  atoms (fused or spiro) count as one system. It answers "which ring building blocks does this set
  use, and how often?" A molecule usually contributes several ring systems.

Maps to `--param analysis="scaffold"` or `--param analysis="ring_system"`.

## 2. Which method?

Show only the three methods for the analysis the user chose. Present each option with the
explanation below; do not attribute methods to named authors or tools.

### Scaffold methods

| Choice | `--param method=` | Explanation for the user |
| --- | --- | --- |
| Bemis–Murcko framework (recommended) | `"bemis_murcko"` | Removes side chains and keeps rings plus linkers with their real atom types, bond orders and exocyclic double bonds. A pyridine core and a benzene core stay separate. The standard choice. |
| Generic Bemis–Murcko | `"generic_bemis_murcko"` | The same framework reduced to a carbon skeleton: every atom becomes carbon and every bond becomes single. Frameworks that differ only in heteroatoms or aromaticity merge into one, giving coarser, larger groups. |
| Common-core fragments | `"common_core"` | Finds cores that molecules share even when their Bemis–Murcko frameworks differ. Each molecule is cut at up to three acyclic single bonds, and the fragments that keep more than a set share of its atoms (default 67%) are counted. One molecule contributes several nested cores, so counts overlap. This suits a congeneric series, e.g. before an R-group table. |

For `common_core`, also offer `min-core-fraction` (default `0.67`). Lower values admit smaller cores
that are shared more widely.

### Ring-system methods

| Choice | `--param method=` | Explanation for the user |
| --- | --- | --- |
| Ring systems (recommended) | `"ring_systems"` | Cuts every acyclic single bond. Fused and spiro rings stay together as one system, and exocyclic C=O, C=S, C=N and C=C on ring carbons (or sulfur) are kept, so a pyridone stays a pyridone. Atom types are preserved. The standard choice. |
| Generic ring systems | `"generic_ring_systems"` | The same ring systems as carbon skeletons (all carbon, single bonds, exocyclic atoms removed). It groups ring topologies, e.g. every 6-6 fused bicycle together. |
| Individual rings | `"individual_rings"` | Each smallest ring (SSSR) on its own, even inside a fused system. Indole becomes pyrrole plus benzene, and exocyclic double bonds are kept. Stereochemistry is dropped. A ring cut out of a fused aromatic system that is not aromatic on its own is written in a Kekulé form. |

## 3. Do you want a PDF report?

- **No**: finish after `finalize` and `verify-receipt`. Reply with a short summary quoting
  `analysis_summary.json` (counts and top structures) and point to the output files.
- **Yes**: after `finalize` and `verify-receipt` pass, follow `optional-pdf-report.md`.

## Mapping answers to the run

Pass `--param` for both required parameters, plus any non-default settings the user asked for:

```bash
python3 "$SKILL_DIR/scripts/run_skill.py" execute --input "$INPUT" \
  --param analysis='"ring_system"' --param method='"ring_systems"' \
  --param smiles-column='"canonical_smiles"' --param id-column='"chembl_id"'
```

Parameter values are JSON, so strings need quotes inside the shell quotes. The runner rejects a
method that belongs to the other analysis. To compare two methods, run them in separate clean
results directories; never overwrite one run with another.
