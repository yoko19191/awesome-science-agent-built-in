#!/usr/bin/env python3
"""Process raw ChEMBL bioactivity records into a clean hit table.

Filters to potency-type endpoints, standardizes to pChEMBL, validates SMILES,
computes physicochemical properties, deduplicates per molecule.

Target-variant handling (see SKILL.md scientific caveat #1)
-----------------------------------------------------------
ChEMBL activity records carry the structured fields ``assay_variant_mutation``
(e.g. "L858R") and ``assay_variant_accession``. A single target therefore pools
assays run against the wild type and against distinct mutants. This script:

  * retains both variant fields through processing;
  * reports the variant composition of the activity set (counts + percentages,
    with wild-type/unspecified as its own bucket) to the console and to
    ``variant_composition.json``;
  * warns prominently when more than one variant is pooled;
  * offers an opt-in ``--variant`` filter (WT / a named mutation / any) so a user
    can restrict scoring to one variant. The default (``any``) is backward
    compatible but is never silent about pooling;
  * carries the variant of each molecule's best measurement, the set of variants
    it was measured against, and the count, into the per-compound output table.

Usage:
    python process_activities.py --input <raw.json> --outdir <dir> \
        [--variant any|WT|<MUTATION>]
"""
import argparse, json, os, sys
import pandas as pd
import numpy as np
from rdkit import Chem
from rdkit.Chem import Descriptors, Crippen, rdMolDescriptors
from rdkit import RDLogger

RDLogger.DisableLog("rdApp.*")

POTENCY_TYPES = {"IC50", "Ki", "Kd", "EC50", "AC50", "Potency", "pIC50", "pKi", "pKd"}

WT_LABEL = "WT/unspecified"


def norm_variant(v):
    """Normalize a raw ``assay_variant_mutation`` value to a display label.

    Anything empty/None/NaN becomes the explicit wild-type/unspecified bucket so
    that "no mutation recorded" is a first-class category rather than silently
    dropped or lumped with mutants.
    """
    if v is None:
        return WT_LABEL
    s = str(v).strip()
    if s == "" or s.lower() in ("nan", "none", "null"):
        return WT_LABEL
    return s


def matches_variant_filter(variant_label, selector):
    """Return True if a record's normalized variant matches the --variant selector."""
    sel = selector.strip()
    if sel.lower() == "any":
        return True
    if sel.lower() in ("wt", "wildtype", "wild-type", "wild_type", WT_LABEL.lower()):
        return variant_label == WT_LABEL
    # Named mutation: case-insensitive exact match on the normalized label.
    return variant_label.lower() == sel.lower()


def to_pchembl(row):
    """Convert a bioactivity record to pChEMBL value."""
    st = str(row["standard_type"])
    val = row["standard_value"]
    if st.startswith("p"):  # already log scale
        return float(val)
    if row["standard_units"] in ("nM", "nanomolar"):
        return -np.log10(val * 1e-9)
    return np.nan


def build_composition(frame, variant_col="variant"):
    """Build a variant-composition record list (counts + percentages), WT bucket included."""
    counts = frame[variant_col].value_counts(dropna=False)
    total = int(counts.sum())
    comp = []
    for label, n in counts.items():
        comp.append({
            "variant": label,
            "n_records": int(n),
            "pct_records": round(100.0 * n / total, 2) if total else 0.0,
            "n_molecules": int(frame.loc[frame[variant_col] == label, "molecule_chembl_id"].nunique()),
        })
    return comp, total


def build_variant_report(composition, total_records, n_variants, n_mutant_variants,
                         pooled, filter_selector, n_records_after_filter,
                         n_molecules_after_filter, warning_text):
    """Assemble the machine-readable variant-composition report (the variant_composition.json body).

    This is the SINGLE source of that artifact's shape, so the emitted schema cannot drift from the
    output contract. The contract (assets/output_contract.json) and its fail-closed gate
    (report_qc.assert_output_contract) require the selector under the key ``applied_filter``.

    Review Stop 4A recovery, family F04 (final_delivery_and_output_manifest_drift): the
    v2-recovery-r1 writer emitted ``variant_filter_applied`` while the contract required
    ``applied_filter``, so a faithful run failed assert_output_contract on variant_composition.json
    (7/8 artifacts passed) even though the eval fixture -- which hard-coded ``applied_filter`` --
    stayed green. Emitting the contract key here closes that drift at its source; the pure signature
    lets a regression exercise the real emitted schema instead of a synthetic stand-in.
    """
    return {
        "variant_field_source": "ChEMBL structured fields assay_variant_mutation / assay_variant_accession",
        "denominator": "exact-measurement, in-range, RDKit-valid records feeding per-molecule scoring",
        "n_records_total": total_records,
        "n_variants": n_variants,
        "n_mutant_variants": n_mutant_variants,
        "pooled": bool(pooled),
        "composition": composition,
        # Contract key (assets/output_contract.json -> variant_composition.json.required_keys).
        "applied_filter": filter_selector,
        "n_records_after_filter": n_records_after_filter,
        "n_molecules_after_filter": n_molecules_after_filter,
        "warning": warning_text,
    }


def main():
    parser = argparse.ArgumentParser(description="Process ChEMBL bioactivity records")
    parser.add_argument("--input", required=True, help="Path to raw activities JSON")
    parser.add_argument("--outdir", required=True, help="Output directory")
    parser.add_argument("--relation-filter", default="exact_only",
                        choices=["exact_only", "include_bounds"],
                        help="How to handle censored (>/<) records")
    parser.add_argument("--pchembl-min", type=float, default=5.0,
                        help="Minimum pChEMBL to include")
    parser.add_argument("--pchembl-max", type=float, default=11.0,
                        help="Maximum pChEMBL (sanity ceiling)")
    parser.add_argument("--variant", default="any",
                        help="Restrict scoring to one assay variant: 'any' (default, "
                             "keeps all variants but reports composition and warns), "
                             "'WT' (wild-type/unspecified only), or a named mutation "
                             "string such as 'L858R' or 'T790M'.")
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    with open(args.input) as f:
        raw = json.load(f)
    df = pd.DataFrame(raw)
    print(f"Raw records: {len(df)}")

    # Retain the structured variant fields (they may be absent on non-mutant targets).
    if "assay_variant_mutation" not in df.columns:
        df["assay_variant_mutation"] = None
    if "assay_variant_accession" not in df.columns:
        df["assay_variant_accession"] = None
    df["variant"] = df["assay_variant_mutation"].apply(norm_variant)

    # Filter to potency-type endpoints
    df = df[df["standard_type"].isin(POTENCY_TYPES)].copy()
    df["standard_value"] = pd.to_numeric(df["standard_value"], errors="coerce")
    df = df[df["standard_value"].notna() & (df["standard_value"] > 0)]
    print(f"After potency-type + numeric filter: {len(df)}")

    # Keep nM units for concentration-based endpoints
    df = df[df["standard_units"].isin(["nM", "nanomolar"]) | df["standard_type"].str.startswith("p")].copy()
    print(f"After unit filter: {len(df)}")

    # Standardize to pChEMBL
    df["pchembl"] = df.apply(to_pchembl, axis=1)
    df = df[df["pchembl"].notna()].copy()
    # Apply sanity ceiling
    df = df[(df["pchembl"] >= args.pchembl_min) & (df["pchembl"] <= args.pchembl_max)]
    print(f"After pChEMBL computation + range filter: {len(df)}")

    # Relation handling
    df["relation"] = df["standard_relation"].fillna("=")
    print(f"Relation distribution:\n{df['relation'].value_counts()}")

    if args.relation_filter == "exact_only":
        exact = df[df["relation"] == "="].copy()
        n_dropped = len(df) - len(exact)
        n_dropped_mols = len(set(df[~df.index.isin(exact.index)]["molecule_chembl_id"]) - set(exact["molecule_chembl_id"]))
        print(f"Exact-measurement records: {len(exact)}")
        print(f"Dropped {n_dropped} censored records ({n_dropped_mols} molecules with only censored values)")
        df = exact
    else:
        # Keep bounds but flag them
        df["is_censored"] = df["relation"].isin([">", "<", ">=", "<="])
        print(f"Including {df['is_censored'].sum()} censored records (flagged)")

    # Require canonical SMILES
    df = df[df["canonical_smiles"].notna() & (df["canonical_smiles"] != "")].copy()
    print(f"After SMILES filter: {len(df)}")

    # Validate SMILES with RDKit and compute properties
    props = []
    valid_idx = []
    smiles_list = df["canonical_smiles"].tolist()
    for i, smi in enumerate(smiles_list):
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            props.append(None)
            continue
        valid_idx.append(i)
        props.append({
            "mol_wt": Descriptors.MolWt(mol),
            "logp": Crippen.MolLogP(mol),
            "tpsa": Descriptors.TPSA(mol),
            "hbd": rdMolDescriptors.CalcNumHBD(mol),
            "hba": rdMolDescriptors.CalcNumHBA(mol),
            "rot_bonds": rdMolDescriptors.CalcNumRotatableBonds(mol),
            "heavy_atoms": mol.GetNumHeavyAtoms(),
            "rings": rdMolDescriptors.CalcNumRings(mol),
            "aromatic_rings": rdMolDescriptors.CalcNumAromaticRings(mol),
            "canonical_smiles_rdkit": Chem.MolToSmiles(mol),
        })

    print(f"Valid SMILES: {len(valid_idx)} / {len(smiles_list)}")
    df = df.iloc[valid_idx].reset_index(drop=True)
    prop_df = pd.DataFrame([p for p in props if p is not None]).reset_index(drop=True)
    df = pd.concat([df.reset_index(drop=True), prop_df], axis=1)

    # The records that will actually be aggregated into a per-molecule pChEMBL.
    exact = df[df["relation"] == "="].copy() if args.relation_filter == "include_bounds" else df.copy()

    # ---- VARIANT COMPOSITION (reported BEFORE any --variant filter) ----
    composition, total_records = build_composition(exact, "variant")
    n_variants = len(composition)
    n_mutant_variants = sum(1 for c in composition if c["variant"] != WT_LABEL)
    pooled = n_variants > 1
    print("\n" + "=" * 72)
    print("VARIANT COMPOSITION of the activity set feeding per-molecule scoring")
    print("(structured ChEMBL field assay_variant_mutation; WT/unspecified is its own bucket)")
    print("-" * 72)
    for c in composition:
        print(f"  {c['variant']:<24} {c['n_records']:>7} records "
              f"({c['pct_records']:>5.1f}%)  {c['n_molecules']:>6} molecules")
    print("-" * 72)

    warning_text = None
    if pooled:
        variant_names = ", ".join(c["variant"] for c in composition if c["variant"] != WT_LABEL) or "none"
        warning_text = (
            f"WARNING: this activity set POOLS {n_variants} assay variants "
            f"({n_mutant_variants} mutant + wild-type/unspecified). Mutant variants present: "
            f"{variant_names}. With --variant any (the default) these are aggregated into a "
            f"single pChEMBL per molecule, and downstream docking uses ONE structure, so a "
            f"compound optimised against a mutant is scored against whichever receptor is "
            f"docked. Series clustering can also merge chemotypes developed against different "
            f"variants. Re-run with --variant WT or --variant <MUTATION> to score one variant, "
            f"and pass a matching --pdb-id / --receptor-variant to prepare_docking.py."
        )
        print("!" * 72)
        print(warning_text)
        print("!" * 72)
    else:
        only = composition[0]["variant"] if composition else WT_LABEL
        print(f"Single variant only ({only}); no pooling across variants.")
    print("=" * 72 + "\n")

    # ---- OPT-IN VARIANT FILTER ----
    filter_selector = args.variant
    if filter_selector.strip().lower() != "any":
        before = len(exact)
        keep_mask = exact["variant"].apply(lambda v: matches_variant_filter(v, filter_selector))
        exact = exact[keep_mask].copy()
        print(f"--variant '{filter_selector}': kept {len(exact)}/{before} records "
              f"matching the selected variant.")
        if len(exact) == 0:
            available = ", ".join(c["variant"] for c in composition)
            print(f"\nERROR: --variant '{filter_selector}' matched no records. "
                  f"Available variants: {available}", file=sys.stderr)
            sys.exit(1)

    n_records_after_filter = len(exact)
    n_molecules_after_filter = int(exact["molecule_chembl_id"].nunique())
    print(f"\nExact-measurement records feeding scoring: {n_records_after_filter}")
    print(f"Unique molecules: {n_molecules_after_filter}")

    # Per-molecule aggregate potency (pooled across whatever variants survive the filter)
    agg = exact.groupby("molecule_chembl_id").agg(
        best_pchembl=("pchembl", "max"),
        median_pchembl=("pchembl", "median"),
        n_measurements=("pchembl", "count"),
        std_pchembl=("pchembl", "std"),
        best_value_nm=("standard_value", "min"),
        standard_type=("standard_type", lambda x: x.mode().iloc[0] if len(x.mode()) else x.iloc[0]),
    ).reset_index()

    # Per-molecule variant summary: how many distinct variants and which ones.
    var_grp = exact.groupby("molecule_chembl_id")["variant"]
    var_summary = var_grp.agg(lambda s: "; ".join(sorted(set(s)))).rename("variant_summary")
    var_nunique = var_grp.nunique().rename("n_variants")
    var_meta = pd.concat([var_summary, var_nunique], axis=1).reset_index()

    # Representative record = the single best-pChEMBL measurement. Its variant is the
    # variant that produced best_pchembl (the potency reported per compound), so carrying
    # it makes the per-compound potency variant explicit.
    exact_sorted = exact.sort_values("pchembl", ascending=False)
    rep = exact_sorted.drop_duplicates("molecule_chembl_id", keep="first")
    rep_cols = ["molecule_chembl_id", "canonical_smiles_rdkit", "canonical_smiles",
                "molecule_pref_name", "mol_wt", "logp", "tpsa", "hbd", "hba",
                "rot_bonds", "heavy_atoms", "rings", "aromatic_rings",
                "assay_description", "variant", "assay_variant_accession",
                "document_journal", "document_year",
                "document_chembl_id", "assay_chembl_id"]
    rep_cols = [c for c in rep_cols if c in rep.columns]
    hits = agg.merge(rep[rep_cols], on="molecule_chembl_id", how="left")
    hits = hits.merge(var_meta, on="molecule_chembl_id", how="left")
    hits = hits.rename(columns={"canonical_smiles_rdkit": "smiles",
                                "variant": "variant_best"})

    # Ligand efficiency and LipE
    hits["ligand_efficiency"] = 1.37 * hits["best_pchembl"] / hits["heavy_atoms"]
    hits["lipe"] = hits["best_pchembl"] - hits["logp"]

    # Lipinski Ro5
    hits["ro5_pass"] = ((hits["mol_wt"] <= 500) & (hits["logp"] <= 5) &
                        (hits["hbd"] <= 5) & (hits["hba"] <= 10)).astype(int)

    hits = hits.sort_values("best_pchembl", ascending=False).reset_index(drop=True)

    print(f"\nFinal hit table: {len(hits)} unique molecules")
    print(f"pChEMBL range: {hits['best_pchembl'].min():.2f} - {hits['best_pchembl'].max():.2f}")
    print(f"Median pChEMBL: {hits['best_pchembl'].median():.2f}")
    print(f"Molecules measured against >1 variant: {(hits['n_variants'] > 1).sum()}")
    print(f"Molecules with pChEMBL >= 6 (<=1uM): {(hits['best_pchembl']>=6).sum()}")
    print(f"Molecules with pChEMBL >= 7 (<=100nM): {(hits['best_pchembl']>=7).sum()}")
    print(f"Molecules with pChEMBL >= 8 (<=10nM): {(hits['best_pchembl']>=8).sum()}")

    out_path = os.path.join(args.outdir, "hits_clean.csv")
    hits.to_csv(out_path, index=False)
    print(f"\nSaved to {out_path}")

    # ---- Machine-readable variant composition ----
    # Built by the single-source helper so the emitted schema (contract key 'applied_filter')
    # cannot drift from assets/output_contract.json (family F04).
    variant_report = build_variant_report(
        composition, total_records, n_variants, n_mutant_variants, pooled,
        filter_selector, n_records_after_filter, n_molecules_after_filter, warning_text)
    var_path = os.path.join(args.outdir, "variant_composition.json")
    with open(var_path, "w") as f:
        json.dump(variant_report, f, indent=2)
    print(f"Variant composition saved to {var_path}")

    # Save provenance
    provenance = {
        "raw_records": len(raw),
        "after_potency_filter": int(len(df)),
        "final_unique_molecules": len(hits),
        "relation_filter": args.relation_filter,
        "pchembl_min": args.pchembl_min,
        "pchembl_max": args.pchembl_max,
        "variant_filter": filter_selector,
        "n_variants_pooled": n_variants,
        "variant_pooled": bool(pooled),
        "n_dropped_censored": int(n_dropped) if args.relation_filter == "exact_only" else 0,
        "n_dropped_mols_censored_only": int(n_dropped_mols) if args.relation_filter == "exact_only" else 0,
    }
    prov_path = os.path.join(args.outdir, "processing_provenance.json")
    with open(prov_path, "w") as f:
        json.dump(provenance, f, indent=2)
    print(f"Provenance saved to {prov_path}")


if __name__ == "__main__":
    main()
