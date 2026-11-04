# Data Sources and Licensing

This skill retrieves data from public external sources at run time. Downstream
commercial use must honor each source's license.

## Summary table

| Source | Used for | License | Commercial use | Key obligation |
|---|---|---|---|---|
| **ChEMBL** (EMBL-EBI) | All bioactivity data, target/molecule metadata | **CC BY-SA 3.0** | **Yes** | **Attribution + share-alike** |
| **RCSB PDB** | Protein structures for docking | **CC0 1.0** (public domain) | **Yes** | None (citation appreciated) |

## Software dependencies

| Package | Version | License | Commercial use |
|---|---|---|---|
| RDKit | 2025.03.5 | BSD-3-Clause | Yes |
| AutoDock Vina | 1.2.5 | Apache 2.0 | Yes |
| Meeko | 0.7.1 | LGPL-2.1 | Yes (dynamic linking) |
| gemmi | 0.7.5 | MPL-2.0 | Yes |
| Open Babel | 3.1.0 | **GPL-2.0** | **Copyleft**: source-distribution obligation for derivative works; **not** a commercial-use ban. Avoided by default (Meeko/ADFR used) to sidestep the copyleft obligation. |
| pandas | 2.3.1 | BSD-3-Clause | Yes |
| numpy | 2.1.0 | BSD-3-Clause | Yes |
| scipy | 1.15.0 | BSD-3-Clause | Yes |
| matplotlib | 3.10.5 | PSF-based | Yes |
| seaborn | 0.13.2 | BSD-3-Clause | Yes |
| reportlab | 4.4.3 | BSD (ReportLab) | Yes |
| pypdf | 6.14.2 | BSD-3-Clause | Yes |

## ChEMBL

- **URL:** https://www.ebi.ac.uk/chembl
- **API:** https://www.ebi.ac.uk/chembl/api/data (REST, no key required)
- **License:** Creative Commons Attribution-Share Alike 3.0 Unported (CC BY-SA 3.0)
- **Commercial use:** Permitted
- **Obligations:**
  1. **Attribution:** Credit ChEMBL with the resource URL and release version
     (e.g. "ChEMBL data from https://www.ebi.ac.uk/chembl, ChEMBL_37")
  2. **Share-alike:** Any redistributed dataset **derived** from ChEMBL (e.g.
     the hits CSV, docking results, SAR results) must be licensed under the
     **same CC BY-SA 3.0** terms
  3. **Preserve ChEMBL IDs** in all derived datasets. Derived CSVs also retain the
     assay-variant provenance (`variant_best`, `variant_summary`, `n_variants`,
     `assay_variant_accession`) and `variant_composition.json`; these inherit the same
     CC BY-SA 3.0 share-alike obligation.
- **Citation:** Cite the ChEMBL release used and, where possible, the underlying
  primary-literature document IDs that ChEMBL abstracts

**Required attribution string for reports:**
> ChEMBL data from https://www.ebi.ac.uk/chembl, ChEMBL release [version].
> Licensed under CC BY-SA 3.0. Derived datasets inherit the share-alike obligation.

## RCSB PDB

- **URL:** https://www.rcsb.org
- **API:** https://data.rcsb.org (REST), https://search.rcsb.org (search)
- **License:** CC0 1.0 (public domain)
- **Commercial use:** Yes
- **Obligations:** None required (citation appreciated)

## License verification (MANDATORY)

**Do not copy a dependency licence from this file, a report, or a GitHub classifier.** Before the
run states any licence, verify each dependency's licence from the dependency's **own licence page**
(the `evidence_url` in the inventory) and run the derive mechanism:

```bash
/opt/conda/bin/python scripts/commercial_inventory.py \
    --out-json /mnt/results/commercial_inventory.json \
    --out-md   /mnt/results/commercial_inventory.md --assert
```

`scripts/commercial_inventory.py` derives the inventory from the declared dependencies **and** the
actually-executed imports/tools, binds each to its authoritative licence-evidence URL, and
`assert_commercial_inventory_consistent` fails closed on (a) a licence claim that conflicts with the
authoritative evidence, (b) a material dependency with no evidence URL, or (c) a self-clear while a
hold is open. It never returns "commercially cleared": the overall status stays **HOLD** while the
ChEMBL CC BY-SA 3.0 share-alike obligation and the open human-decision commercial findings await
Phylo/legal review. (History: the v0.4.0 run copied ADFR's licence as "Apache 2.0" without checking
https://ccsb.scripps.edu/adfr/license/, which states LGPL2 — this mechanism catches that.)

## Open Babel license note

Open Babel is **GPL-2.0** (copyleft). Copyleft imposes a **source-distribution obligation on
derivative works** — it is **not** a commercial-use ban. This skill nonetheless **defaults to Meeko
`mk_prepare_receptor.py` (LGPL-2.1) or ADFR `prepare_receptor` (LGPL2, verified at
https://ccsb.scripps.edu/adfr/license/)** for receptor PDBQT preparation to sidestep the copyleft
obligation. Open Babel is only a last-resort fallback, and the report must state the copyleft
obligation if it is ever used.

## Report attribution requirements

Every report this skill produces must include:
1. A data-source line attributing **ChEMBL (CC BY-SA 3.0)** with the release version
2. A statement that derived CSVs inherit the share-alike obligation
3. Attribution for RCSB PDB if docking was performed
4. Tool version information (RDKit, Vina, Meeko, etc.)
