#!/usr/bin/env python3
"""Extract scaffolds or ring systems from a SMILES table with one named method and rank them by frequency."""

from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

import rdkit
from rdkit import Chem, RDLogger
from rdkit.Chem import Draw
from PIL import Image, ImageDraw

import scaffold_methods as sm

RESULTS = pathlib.Path(os.environ.get("BIOMNI_RESULTS", "/mnt/results"))
TABLE_SUFFIXES = {".csv": ",", ".tsv": "\t"}
MAX_EXAMPLE_IDS = 5
GRID_COLUMNS = 5
GRID_CELL = (260, 240)

Status = Literal["ok", "invalid_smiles", "acyclic", "extraction_failed"]


class InputError(ValueError):
    """The input or parameters cannot support a valid analysis."""


@dataclass(frozen=True)
class InputRow:
    molecule_id: str
    smiles: str


@dataclass(frozen=True)
class MoleculeResult:
    molecule_id: str
    input_smiles: str
    standardized_smiles: str
    status: Status
    structures: tuple[str, ...]
    error: str


def validate_parameters(args: argparse.Namespace) -> None:
    if args.method not in sm.METHODS_BY_ANALYSIS[args.analysis]:
        allowed = ", ".join(sm.METHODS_BY_ANALYSIS[args.analysis])
        raise InputError(f"method {args.method!r} is not a {args.analysis} method; choose one of: {allowed}")
    if not 0 < args.min_core_fraction < 1:
        raise InputError("--min-core-fraction must be greater than 0 and less than 1")
    if args.top_n < 1:
        raise InputError("--top-n must be at least 1")


def _resolve_column(headers: Sequence[str], requested: str, role: str) -> str:
    matches = [header for header in headers if header.strip().lower() == requested.strip().lower()]
    if not matches:
        raise InputError(f"{role} column {requested!r} not found; columns are: {', '.join(headers)}")
    if len(matches) > 1:
        raise InputError(f"{role} column {requested!r} matches several headers: {matches}")
    return matches[0]


def _read_delimited(text: str, delimiter: str, smiles_column: str, id_column: str) -> list[InputRow]:
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    headers = reader.fieldnames or []
    smiles_key = _resolve_column(headers, smiles_column, "SMILES")
    id_key = _resolve_column(headers, id_column, "identifier") if id_column else None
    rows = []
    for index, record in enumerate(reader, 1):
        molecule_id = (record.get(id_key) or "").strip() if id_key else str(index)
        rows.append(InputRow(molecule_id=molecule_id, smiles=(record.get(smiles_key) or "").strip()))
    return rows


def _read_smi(text: str, id_column: str) -> list[InputRow]:
    if id_column:
        raise InputError("--id-column applies to CSV/TSV input; a .smi file takes identifiers from its second field")
    lines = [line.split() for line in text.splitlines() if line.strip()]
    if lines and lines[0][0].lower() == "smiles":
        lines = lines[1:]
    return [
        InputRow(molecule_id=fields[1] if len(fields) > 1 else str(index), smiles=fields[0])
        for index, fields in enumerate(lines, 1)
    ]


def read_input(path: pathlib.Path, smiles_column: str, id_column: str) -> list[InputRow]:
    suffix = path.suffix.lower()
    if suffix not in {*TABLE_SUFFIXES, ".smi"}:
        raise InputError(f"unsupported input type {suffix or '(none)'!r}; use .csv, .tsv or .smi")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"cannot read {path.name} as UTF-8 text: {exc}") from exc
    if suffix == ".smi":
        rows = _read_smi(text, id_column)
    else:
        rows = _read_delimited(text, TABLE_SUFFIXES[suffix], smiles_column, id_column)
    if not rows:
        raise InputError(f"{path.name} contains no molecule rows")
    ids = [row.molecule_id for row in rows]
    if any(not molecule_id for molecule_id in ids):
        raise InputError("every row needs a non-empty identifier")
    duplicates = sorted(molecule_id for molecule_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise InputError(f"duplicate identifiers: {duplicates[:10]}")
    return rows


def analyze_row(row: InputRow, extract: Callable[[Chem.Mol], list[str]]) -> MoleculeResult:
    mol = Chem.MolFromSmiles(row.smiles) if row.smiles else None
    if mol is None:
        return MoleculeResult(row.molecule_id, row.smiles, "", "invalid_smiles", (), "RDKit could not parse the SMILES")
    try:
        mol = sm.standardize(mol)
    except (RuntimeError, ValueError) as exc:
        return MoleculeResult(row.molecule_id, row.smiles, "", "invalid_smiles", (), f"standardization failed: {exc}")
    standardized = Chem.MolToSmiles(mol)
    if not sm.has_ring(mol):
        return MoleculeResult(row.molecule_id, row.smiles, standardized, "acyclic", (), "")
    try:
        structures = tuple(extract(mol))
    except (RuntimeError, ValueError) as exc:
        print(f"extraction failed for {row.molecule_id}: {exc}", file=sys.stderr)
        return MoleculeResult(row.molecule_id, row.smiles, standardized, "extraction_failed", (), str(exc))
    return MoleculeResult(row.molecule_id, row.smiles, standardized, "ok", structures, "")


def summarize(
    args: argparse.Namespace,
    input_path: pathlib.Path,
    results: Sequence[MoleculeResult],
    frequencies: Sequence[sm.StructureFrequency],
) -> dict[str, object]:
    status_counts = {status: sum(1 for r in results if r.status == status) for status in Status.__args__}
    n_analyzed = status_counts["ok"] + status_counts["acyclic"]
    standardized = [r.standardized_smiles for r in results if r.standardized_smiles]
    return {
        "analysis": args.analysis,
        "method": args.method,
        "parameters": {
            "smiles_column": args.smiles_column,
            "id_column": args.id_column,
            "min_core_fraction": args.min_core_fraction if args.method == "common_core" else None,
            "top_n": args.top_n,
        },
        "input_file": input_path.name,
        "rdkit_version": rdkit.__version__,
        "counts": {
            "n_rows": len(results),
            "n_ok": status_counts["ok"],
            "n_acyclic": status_counts["acyclic"],
            "n_invalid_smiles": status_counts["invalid_smiles"],
            "n_extraction_failed": status_counts["extraction_failed"],
            "n_analyzed": n_analyzed,
            "n_duplicate_structures": len(standardized) - len(set(standardized)),
            "n_unique_structures": len(frequencies),
            "n_structure_occurrences": sum(item.n_occurrences for item in frequencies),
        },
        "fraction_denominator": "n_analyzed (valid molecules with or without rings)",
        "top_structures": [
            {"rank": rank, "smiles": item.smiles, "n_molecules": item.n_molecules, "n_occurrences": item.n_occurrences}
            for rank, item in enumerate(frequencies[:10], 1)
        ],
    }


def write_frequency_table(path: pathlib.Path, frequencies: Sequence[sm.StructureFrequency], n_analyzed: int) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rank", "structure_smiles", "n_molecules", "fraction_of_molecules", "n_occurrences", "n_heavy_atoms", "example_ids"])
        for rank, item in enumerate(frequencies, 1):
            writer.writerow([
                rank,
                item.smiles,
                item.n_molecules,
                f"{item.n_molecules / n_analyzed:.6f}",
                item.n_occurrences,
                item.n_heavy_atoms,
                ";".join(item.example_ids),
            ])


def write_assignments(path: pathlib.Path, results: Sequence[MoleculeResult]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "input_smiles", "standardized_smiles", "status", "n_structures", "structures", "error"])
        for r in results:
            writer.writerow([r.molecule_id, r.input_smiles, r.standardized_smiles, r.status, len(r.structures), ";".join(r.structures), r.error])


def _drawable(smiles: str) -> Chem.Mol:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        mol = Chem.MolFromSmiles(smiles, sanitize=False)
        mol.UpdatePropertyCache(strict=False)
    return mol


def write_structure_grid(path: pathlib.Path, frequencies: Sequence[sm.StructureFrequency], n_analyzed: int, top_n: int) -> None:
    shown = frequencies[:top_n]
    legends = [f"#{rank}  n={item.n_molecules} ({100 * item.n_molecules / n_analyzed:.1f}%)" for rank, item in enumerate(shown, 1)]
    image = Draw.MolsToGridImage(
        [_drawable(item.smiles) for item in shown],
        molsPerRow=min(GRID_COLUMNS, len(shown)),
        subImgSize=GRID_CELL,
        legends=legends,
    )
    if not isinstance(image, Image.Image):
        raise RuntimeError("RDKit did not return a PIL image for the structure grid")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=pathlib.Path)
    parser.add_argument("--analysis", required=True, choices=list(sm.METHODS_BY_ANALYSIS))
    parser.add_argument("--method", required=True, choices=[m for methods in sm.METHODS_BY_ANALYSIS.values() for m in methods])
    parser.add_argument("--smiles-column", default="smiles")
    parser.add_argument("--id-column", default="")
    parser.add_argument("--min-core-fraction", type=float, default=0.67)
    parser.add_argument("--top-n", type=int, default=30)
    args = parser.parse_args()
    RDLogger.DisableLog("rdApp.*")

    try:
        validate_parameters(args)
        rows = read_input(args.input, args.smiles_column, args.id_column)
        extract = sm.extractor_for(args.method, args.min_core_fraction)
        results = [analyze_row(row, extract) for row in rows]
        if not any(r.status == "ok" for r in results):
            raise InputError("no row yields a valid molecule that contains a ring")
    except InputError as exc:
        print(f"input rejected: {exc}", file=sys.stderr)
        return 2

    frequencies = sm.count_frequencies(((r.molecule_id, r.structures) for r in results), MAX_EXAMPLE_IDS)
    summary = summarize(args, args.input, results, frequencies)
    n_analyzed = summary["counts"]["n_analyzed"]
    write_frequency_table(RESULTS / "frequency_table.csv", frequencies, n_analyzed)
    write_assignments(RESULTS / "molecule_assignments.csv", results)
    write_structure_grid(RESULTS / "figures" / "top_structures.png", frequencies, n_analyzed, args.top_n)
    (RESULTS / "analysis_summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"{summary['counts']['n_unique_structures']} unique structures from {summary['counts']['n_ok']} molecules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
