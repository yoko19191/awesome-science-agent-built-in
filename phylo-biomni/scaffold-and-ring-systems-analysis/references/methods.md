# Methods

All methods run on standardized molecules: RDKit parse, largest organic fragment
(`LargestFragmentChooser(preferOrganic=True)`), then neutralization (`Uncharger`). Molecules without
a ring are counted as `acyclic` and contribute no structure. Structures are compared by RDKit
canonical SMILES.

## Frequency accounting

- `n_molecules`: the number of molecules that contain the structure at least once. Ranking uses
  this count, then heavy-atom count (larger first), then SMILES.
- `n_occurrences`: the total count, including repeats inside one molecule. For example, biphenyl
  contributes two benzene ring systems.
- `fraction_of_molecules` = `n_molecules / n_analyzed`, where `n_analyzed` counts all valid
  molecules, acyclic ones included.
- Duplicate input molecules are counted once per row. `n_duplicate_structures` in the summary
  reports how many rows repeat an earlier standardized structure.

## Scaffold methods

- **`bemis_murcko`**: RDKit `MurckoScaffold.GetScaffoldForMol`. It removes terminal side chains
  repeatedly until only ring atoms and linker atoms remain. Atom types, bond orders and
  stereochemistry are kept, and so are atoms double-bonded to the framework. This is the framework
  Walters prefers for exploratory analysis because it "maintains the atom types and bond orders".
- **`generic_bemis_murcko`**: `MakeScaffoldGeneric` on the framework (all atoms become carbon,
  all bonds single), then `GetScaffoldForMol` again to prune the exocyclic carbons that the generic
  conversion creates from C=O and similar groups. The result is a pure carbon skeleton of rings and
  linkers.
- **`common_core`**: follows `scaffold_finder` (Walters) and the core definition of Naveja et al.
  2019. RDKit `rdMMPA.FragmentMol` (Hussain–Rea fragmentation: up to three cuts, acyclic single
  bonds only) produces fragments. The largest piece of each fragment is kept as a core when its
  heavy-atom count exceeds `min_core_fraction` of the parent's (default 0.67). Attachment points
  do not count toward that size (`scaffold_finder` counts them, which admits slightly smaller
  cores) and become hydrogen in the reported core. The parent molecule itself is always included, and each molecule lists each
  distinct core once. Because cores nest, a molecule appears under several cores. The most
  frequent large core is the natural anchor for an R-group table.

## Ring-system methods

- **`ring_systems`**: reimplements `useful_rdkit_utils.RingSystemFinder`. Bonds matching
  `[#6R,#16R]=[OR0,SR0,CR0,NR0]` are protected, so ring carbonyls, thiones, exocyclic imines and
  alkenes stay attached. Every other acyclic single bond is cut. Fragments without a ring are
  discarded, dummy atoms become hydrogen, and stereo on terminal double bonds is cleared. Fused
  rings (sharing a bond) and spiro rings (sharing an atom) stay together as one system. Acyclic
  double or triple bonds between rings are not single bonds, so they are not cut, which matches
  the reference implementation.
- **`generic_ring_systems`**: each ring system passed through `MakeScaffoldGeneric` and
  `GetScaffoldForMol`. The result is ring topology only, with no exocyclic atoms.
- **`individual_rings`**: each ring in RDKit's SSSR, taken with its protected exocyclic partners.
  Former fusion atoms get implicit hydrogens. When the isolated ring cannot be kekulized as
  aromatic (for example the rings of azulene), the Kekulé form is used, and if that also fails the
  unsanitized SMILES is used. Stereochemistry is removed. SSSR is not unique for some cage
  systems, so ring counts for cages follow RDKit's choice.

## References

- Bemis GW, Murcko MA. The properties of known drugs. 1. Molecular frameworks. *J Med Chem*
  1996;39:2887–2893. doi:10.1021/jm9602928
- Hussain J, Rea C. Computationally efficient algorithm to identify matched molecular pairs (MMPs)
  in large data sets. *J Chem Inf Model* 2010;50:339–348. doi:10.1021/ci900450m
- Naveja JJ, Vogt M, Stumpfe D, Medina-Franco JL, Bajorath J. Systematic extraction of analogue
  series from large compound collections using a new computational compound–core relationship
  method. *ACS Omega* 2019;4:1027–1032. doi:10.1021/acsomega.8b03390
- Taylor RD, MacCoss M, Lawson ADG. Rings in drugs. *J Med Chem* 2014;57:5845–5859.
  doi:10.1021/jm4017625
- Walters P. Exploratory data analysis with mols2grid (2021).
  https://practicalcheminformatics.blogspot.com/2021/10/exploratory-data-analysis-with.html
- Walters P. Identifying ring systems in molecules (2022).
  https://practicalcheminformatics.blogspot.com/2022/12/identifying-ring-systems-in-molecules.html
- Walters P. `practical_cheminformatics_tutorials`: `misc/ChEMBL_ring_system_analysis.ipynb` and
  `sar_analysis/find_scaffolds.ipynb` (MIT). https://github.com/PatWalters/practical_cheminformatics_tutorials
