"""Scaffold and ring-system extraction for standardized RDKit molecules.

Every extractor takes one molecule and returns the canonical SMILES of its structures, one entry per
occurrence, so a molecule with two benzene rings yields ``["c1ccccc1", "c1ccccc1"]``.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from rdkit import Chem
from rdkit.Chem import rdMMPA
from rdkit.Chem.MolStandardize import rdMolStandardize
from rdkit.Chem.Scaffolds import MurckoScaffold

Analysis = Literal["scaffold", "ring_system"]
Method = Literal[
    "bemis_murcko",
    "generic_bemis_murcko",
    "common_core",
    "ring_systems",
    "generic_ring_systems",
    "individual_rings",
]

METHODS_BY_ANALYSIS: dict[Analysis, tuple[Method, ...]] = {
    "scaffold": ("bemis_murcko", "generic_bemis_murcko", "common_core"),
    "ring_system": ("ring_systems", "generic_ring_systems", "individual_rings"),
}

# Ring C or S doubly bonded to an acyclic O, S, C or N: ring carbonyls, thiones, exocyclic alkenes and
# imines. These stay attached to their ring (useful_rdkit_utils.RingSystemFinder convention).
EXOCYCLIC_DOUBLE_BOND = Chem.MolFromSmarts("[#6R,#16R]=[OR0,SR0,CR0,NR0]")

_LARGEST_FRAGMENT = rdMolStandardize.LargestFragmentChooser(preferOrganic=True)
_UNCHARGER = rdMolStandardize.Uncharger()


def standardize(mol: Chem.Mol) -> Chem.Mol:
    """Keep the largest organic fragment and neutralize it."""
    return _UNCHARGER.uncharge(_LARGEST_FRAGMENT.choose(mol))


def has_ring(mol: Chem.Mol) -> bool:
    return mol.GetRingInfo().NumRings() > 0


def _heavy_atoms(mol: Chem.Mol) -> int:
    """Atoms heavier than hydrogen; dummy attachment points (atomic number 0) are not counted."""
    return sum(1 for atom in mol.GetAtoms() if atom.GetAtomicNum() > 1)


def _generic_framework(mol: Chem.Mol) -> Chem.Mol:
    """All-carbon, single-bond form with the exocyclic atoms it creates pruned away."""
    return MurckoScaffold.GetScaffoldForMol(MurckoScaffold.MakeScaffoldGeneric(mol))


def bemis_murcko(mol: Chem.Mol) -> list[str]:
    scaffold = MurckoScaffold.GetScaffoldForMol(mol)
    return [Chem.MolToSmiles(scaffold)] if scaffold.GetNumAtoms() else []


def generic_bemis_murcko(mol: Chem.Mol) -> list[str]:
    scaffold = MurckoScaffold.GetScaffoldForMol(mol)
    if not scaffold.GetNumAtoms():
        return []
    return [Chem.MolToSmiles(_generic_framework(scaffold), isomericSmiles=False)]


def _dummies_to_hydrogen(mol: Chem.Mol) -> Chem.Mol:
    editable = Chem.RWMol(mol)
    for atom in editable.GetAtoms():
        atom.SetAtomMapNum(0)
        if atom.GetAtomicNum() == 0:
            atom.SetAtomicNum(1)
            atom.SetIsotope(0)
    return Chem.RemoveAllHs(editable.GetMol())


def common_cores(mol: Chem.Mol, min_core_fraction: float) -> list[str]:
    """Distinct cores left after Hussain-Rea removal of up to three acyclic substituents.

    A fragment is a core when it keeps more than ``min_core_fraction`` of the parent's heavy atoms.
    The parent itself is always one of its cores, so a singleton molecule still appears.
    """
    n_heavy = _heavy_atoms(mol)
    fragments = [frag for frag in itertools.chain.from_iterable(rdMMPA.FragmentMol(mol)) if frag is not None]
    cores = {Chem.MolToSmiles(mol)}
    for fragment in fragments:
        largest = max(Chem.GetMolFrags(fragment, asMols=True), key=_heavy_atoms)
        if _heavy_atoms(largest) / n_heavy > min_core_fraction:
            cores.add(Chem.MolToSmiles(_dummies_to_hydrogen(largest)))
    return sorted(cores)


def _clear_terminal_double_bond_stereo(mol: Chem.Mol) -> Chem.Mol:
    for bond in mol.GetBonds():
        if bond.GetBondType() == Chem.BondType.DOUBLE and (
            bond.GetBeginAtom().GetDegree() == 1 or bond.GetEndAtom().GetDegree() == 1
        ):
            bond.SetStereo(Chem.BondStereo.STEREONONE)
    return mol


def _ring_system_mols(mol: Chem.Mol) -> list[Chem.Mol]:
    """Fused and spiro ring sets, keeping exocyclic double bonds; linkers and substituents removed."""
    protected = {
        mol.GetBondBetweenAtoms(begin, end).GetIdx()
        for begin, end in mol.GetSubstructMatches(EXOCYCLIC_DOUBLE_BOND)
    }
    cut = [
        bond.GetIdx()
        for bond in mol.GetBonds()
        if not bond.IsInRing() and bond.GetBondType() == Chem.BondType.SINGLE and bond.GetIdx() not in protected
    ]
    fragmented = Chem.FragmentOnBonds(mol, cut) if cut else Chem.Mol(mol)
    Chem.SanitizeMol(fragmented)
    systems = []
    for piece in Chem.GetMolFrags(fragmented, asMols=True):
        if has_ring(piece):
            systems.append(_clear_terminal_double_bond_stereo(_dummies_to_hydrogen(piece)))
    return systems


def ring_systems(mol: Chem.Mol) -> list[str]:
    return sorted(Chem.MolToSmiles(system) for system in _ring_system_mols(mol))


def generic_ring_systems(mol: Chem.Mol) -> list[str]:
    return sorted(
        Chem.MolToSmiles(_generic_framework(system), isomericSmiles=False) for system in _ring_system_mols(mol)
    )


def _ring_fragment_smiles(mol: Chem.Mol, atoms: Sequence[int]) -> str:
    """SMILES of the substructure on ``atoms``; open valences on former fusion atoms become H.

    A ring cut out of a fused aromatic system is not always aromatic on its own (the five-membered
    ring of azulene), so a Kekulé form is tried next and an unsanitized form is the last resort.
    """
    atom_set = set(atoms)
    bonds = [
        bond.GetIdx()
        for bond in mol.GetBonds()
        if bond.GetBeginAtomIdx() in atom_set and bond.GetEndAtomIdx() in atom_set
    ]
    aromatic = Chem.MolFragmentToSmiles(mol, atomsToUse=list(atoms), bondsToUse=bonds)
    parsed = Chem.MolFromSmiles(aromatic)
    if parsed is not None:
        return Chem.MolToSmiles(parsed)
    kekulized = Chem.Mol(mol)
    Chem.Kekulize(kekulized, clearAromaticFlags=True)
    kekule = Chem.MolFragmentToSmiles(kekulized, atomsToUse=list(atoms), bondsToUse=bonds, kekuleSmiles=True)
    parsed = Chem.MolFromSmiles(kekule)
    if parsed is not None:
        return Chem.MolToSmiles(parsed)
    return Chem.MolToSmiles(Chem.MolFromSmiles(aromatic, sanitize=False))


def individual_rings(mol: Chem.Mol) -> list[str]:
    """Each SSSR ring on its own, with its exocyclic double-bonded atoms and no stereochemistry."""
    exocyclic_partners: dict[int, list[int]] = {}
    for ring_atom, partner in mol.GetSubstructMatches(EXOCYCLIC_DOUBLE_BOND):
        exocyclic_partners.setdefault(ring_atom, []).append(partner)
    flat = Chem.Mol(mol)
    Chem.RemoveStereochemistry(flat)
    rings = []
    for ring in flat.GetRingInfo().AtomRings():
        atoms = [*ring, *(partner for atom in ring for partner in exocyclic_partners.get(atom, []))]
        rings.append(_ring_fragment_smiles(flat, atoms))
    return sorted(rings)


def extractor_for(method: Method, min_core_fraction: float) -> Callable[[Chem.Mol], list[str]]:
    extractors: dict[Method, Callable[[Chem.Mol], list[str]]] = {
        "bemis_murcko": bemis_murcko,
        "generic_bemis_murcko": generic_bemis_murcko,
        "common_core": lambda mol: common_cores(mol, min_core_fraction),
        "ring_systems": ring_systems,
        "generic_ring_systems": generic_ring_systems,
        "individual_rings": individual_rings,
    }
    return extractors[method]


@dataclass(frozen=True)
class StructureFrequency:
    smiles: str
    n_molecules: int
    n_occurrences: int
    n_heavy_atoms: int
    example_ids: tuple[str, ...]


def heavy_atom_count(smiles: str) -> int:
    return _heavy_atoms(Chem.MolFromSmiles(smiles) or Chem.MolFromSmiles(smiles, sanitize=False))


def count_frequencies(
    assignments: Iterable[tuple[str, Sequence[str]]], max_examples: int = 5
) -> list[StructureFrequency]:
    """Rank structures by molecules containing them, then by size, then by SMILES.

    ``assignments`` pairs each molecule identifier with its structure occurrences.
    """
    molecules: dict[str, list[str]] = {}
    occurrences: dict[str, int] = {}
    for molecule_id, structures in assignments:
        for smiles in structures:
            occurrences[smiles] = occurrences.get(smiles, 0) + 1
        for smiles in dict.fromkeys(structures):
            molecules.setdefault(smiles, []).append(molecule_id)
    frequencies = [
        StructureFrequency(
            smiles=smiles,
            n_molecules=len(ids),
            n_occurrences=occurrences[smiles],
            n_heavy_atoms=heavy_atom_count(smiles),
            example_ids=tuple(ids[:max_examples]),
        )
        for smiles, ids in molecules.items()
    ]
    return sorted(frequencies, key=lambda item: (-item.n_molecules, -item.n_heavy_atoms, item.smiles))
