#!/usr/bin/env python3
"""Prepare receptor and ligands for AutoDock Vina docking.

Receptor: extracts protein from PDB, converts to PDBQT using Meeko
(mk_prepare_receptor.py, LGPL-2.1) or ADFR prepare_receptor (LGPL2, verified at
https://ccsb.scripps.edu/adfr/license/ -- NOT Apache 2.0).
AVOIDS Open Babel (GPL-2.0, copyleft) except as a last resort: copyleft imposes a
source-distribution obligation on derivative works, it is not a commercial-use ban.

Ligands: RDKit 3D embed + Meeko PDBQT preparation.

Usage:
    python prepare_docking.py --hits <csv> --pdb-id 4GIH --outdir <dir> \
        --data-dir <dir> --dock-top-per-series 3 --dock-top-overall 10
"""
import argparse, os, sys, json, subprocess, shutil
import pandas as pd
import numpy as np

# rdkit and meeko are imported LAZILY (inside the functions that need them) so the pure,
# deterministic helpers below -- select_series_for_docking (finding 5) and build_box_info
# (finding 7) -- can be imported and unit-tested without a chemistry toolkit installed. See
# assets/eval/test_docking_series_coverage.py and assets/eval/test_docking_box_excluded_additives.py.


def _load_rdkit():
    """Import rdkit lazily and silence its logger. Kept out of module import for testability."""
    from rdkit import Chem
    from rdkit.Chem import AllChem
    from rdkit import RDLogger
    RDLogger.DisableLog("rdApp.*")
    return Chem, AllChem


def download_pdb(pdb_id, outdir):
    """Download a PDB file from RCSB."""
    url = f"https://files.rcsb.org/download/{pdb_id}.pdb"
    import urllib.request
    path = os.path.join(outdir, f"{pdb_id}.pdb")
    urllib.request.urlretrieve(url, path)
    return path


# --- PDB auto-selection -----------------------------------------------------
# Ligands that are crystallisation additives, buffers, cryoprotectants or ions --
# present as HETATM but never the pocket-defining co-crystal ligand.
NON_LIGAND_HETATMS = {
    "HOH", "DOD", "SO4", "PO4", "GOL", "EDO", "PEG", "PG4", "PGE", "1PE", "P6G",
    "MPD", "DMS", "TRS", "MES", "EPE", "ACT", "ACY", "FMT", "CIT", "TLA", "MLI",
    "IMD", "BME", "DTT", "NAG", "NDG", "BMA", "MAN", "GAL", "FUC", "XYS",
    "ZN", "MG", "MN", "CA", "NA", "K", "CL", "BR", "IOD", "FE", "FE2", "CU",
    "CU1", "NI", "CO", "CD", "HG", "CS", "SR", "BA", "LI", "RB", "F", "OXY",
    "NH4", "AZI", "NO3", "CO3", "SCN", "UNX", "UNL",
}
_MIN_LIGAND_MW = 150.0  # Da -- below this a HETATM is a fragment/additive, not a drug-like ligand


# --- Docking-box permissiveness diagnostics (family F10) --------------------------------------
# A docking box built from a co-crystal ligand can be oversized: when it is, the search scores poses
# across a wide region rather than one focused pocket. An oversized/permissive box is a known driver
# of weak docking-score vs experimental-potency correlation, and can signal that the box is defined
# by a co-crystal ligand whose binding mode/conformation differs from the docked ligand class (e.g.
# an allosteric-inhibitor co-crystal used to dock predominantly ATP-competitive actives). This is
# DERIVED from box geometry alone -- generalizable, tied to no specific structure -- and surfaced as
# a caveat so report_qc.assert_docking_box_diagnostics can enforce it (family F10:
# scientific_or_fixture_specific_defect; derive rather than restate). Thresholds follow standard
# docking practice: a focused pocket box is ~20-25 A per side; a max axis > 30 A (blind-docking
# regime) or a volume > ~27,000 A^3 (about a 30 A cube) is permissive.
PERMISSIVE_MAX_DIM_A = 30.0
PERMISSIVE_VOLUME_A3 = 27000.0


def box_diagnostics(box, n_ligand_atoms=None):
    """Derive box-permissiveness diagnostics from a box dict (family F10).

    Returns ``box_volume_A3``, ``box_max_dim_A``, ``n_ligand_atoms``, ``box_permissive`` and
    ``docking_caveats`` (a list carrying the permissiveness/conformation caveat iff the box is
    permissive). Pure and geometry-only, so it is unit-testable and encodes no structure-specific
    answer -- the same rule flags any oversized box on any target, and passes any focused box.
    """
    sx, sy, sz = float(box["size_x"]), float(box["size_y"]), float(box["size_z"])
    vol = sx * sy * sz
    max_dim = max(sx, sy, sz)
    permissive = (max_dim > PERMISSIVE_MAX_DIM_A) or (vol > PERMISSIVE_VOLUME_A3)
    caveats = []
    if permissive:
        caveats.append(
            f"Permissive docking box (max axis {max_dim:.1f} A, volume {vol:.0f} A^3, above the "
            f"{PERMISSIVE_MAX_DIM_A:.0f} A / {PERMISSIVE_VOLUME_A3:.0f} A^3 focused-pocket guide): "
            "the search scores poses across a wide region rather than one focused pocket, which "
            "weakens the expected docking-score vs experimental-potency correlation and can indicate "
            "the box is defined by a co-crystal ligand whose binding mode/conformation differs from "
            "the docked ligand class. State the receptor conformation and binding-site relevance "
            "(docking_box.json -> pdb_selection / receptor_variant) in the report, and treat docking "
            "as structural plausibility rather than affinity ranking.")
    return {
        "box_volume_A3": round(vol, 1),
        "box_max_dim_A": round(max_dim, 1),
        "n_ligand_atoms": int(n_ligand_atoms) if n_ligand_atoms is not None else None,
        "box_permissive": bool(permissive),
        "docking_caveats": caveats,
    }


# --- Docking box construction (finding 7 mechanism) -------------------------------------------
def build_box_info(ligand_coords, ligand_resnames, excluded_resnames, pdb_id,
                   receptor_variant, receptor_variant_source, pdb_selection,
                   pad=16.0, min_size=(24.0, 24.0, 24.0)):
    """Build the docking-box dict from co-crystal ligand coordinates.

    Records ``excluded_additives`` as an explicit, inspectable field (finding 7). The
    crystallisation-additive ignore list (NO3/EDO/HOH/ions/sugars, ``NON_LIGAND_HETATMS``) is
    applied when parsing HETATM records, but the v2 ``docking_box.json`` reflected that only in
    console output -- a downstream reader or gate could not confirm which additives were kept out
    of the box. It is now a field, so ``additive_ignore_list_in_both_paths`` is provable from the
    artifact and not only from code behaviour. Lists are sorted so the JSON is deterministic.
    """
    coords = np.asarray(ligand_coords, dtype=float)
    center = coords.mean(axis=0)
    extents = coords.max(axis=0) - coords.min(axis=0)
    box_size = np.maximum(extents + pad, list(min_size))
    box = {
        "center_x": float(center[0]), "center_y": float(center[1]), "center_z": float(center[2]),
        "size_x": float(box_size[0]), "size_y": float(box_size[1]), "size_z": float(box_size[2]),
        "pdb_id": pdb_id,
        "ligand_resnames": sorted(ligand_resnames),
        # Crystallisation additives/ions excluded from the box, using the SAME ignore list as
        # PDB auto-selection. Recorded so a reader/gate can confirm what was kept out of the box.
        "excluded_additives": sorted(excluded_resnames),
        "receptor_variant": receptor_variant,
        "receptor_variant_source": receptor_variant_source,
        "pdb_selection": pdb_selection,
    }
    # Derive box-permissiveness diagnostics + caveat (family F10) so the receptor-conformation/box
    # driver of a weak docking-potency correlation is provable from the artifact, not just prose.
    box.update(box_diagnostics(box, n_ligand_atoms=len(coords)))
    return box


# --- Series representative selection (SAR-priority; finding 5 mechanism) -----------------------
_REC_TIER_ORDER = ["EXPAND", "EXPLORE", "MONITOR"]  # priority; anything else ranks after these


def _recommendation_tier(rec):
    """Priority rank for a sar_results recommendation string (lower = higher priority).

    The recommendation starts with the tier keyword, e.g. 'EXPAND - High potency...'. An
    unknown/absent recommendation ranks after every named tier so it never displaces an
    EXPAND/EXPLORE/MONITOR series.
    """
    if not rec:
        return len(_REC_TIER_ORDER)
    head = str(rec).strip().upper()
    for i, kw in enumerate(_REC_TIER_ORDER):
        if head.startswith(kw):
            return i
    return len(_REC_TIER_ORDER)  # DROP or anything else


def _series_tier(sar_results, s):
    """EXPAND=0 .. unknown=last, for series ``s`` read from a sar_results dict."""
    entry = (sar_results or {}).get(s) if isinstance(sar_results, dict) else None
    rec = entry.get("recommendation") if isinstance(entry, dict) else None
    return _recommendation_tier(rec)


def rank_series_for_docking(hits, sar_results=None, series_stats=None):
    """Return named series in DOCKING PRIORITY order (never raw table order).

    The v2 defect (Review Stop 4A, finding 5): the selector took
    ``hits[...]['series'].unique()[:15]`` -- the first 15 non-singleton series in TABLE ORDER --
    so EXPAND-recommended series appearing later were never docked (Series Q, T on the EGFR run,
    where the structural evidence then failed to support the headline 'worth expanding' claim).
    Representative selection must follow the ANALYTICAL priority produced by SAR, not the
    incidental row order of the hits table.

    Deterministic total order:
      1. SAR recommendation tier EXPAND > EXPLORE > MONITOR > (DROP/none), when sar_results given
      2. within a tier, larger series first, then series name
    Falls back to series size (from series_stats if given, else counts in ``hits``) when no
    sar_results is available -- still never raw table order.
    """
    named = [s for s in hits["series"].dropna().unique() if s != "Singleton/Other"]
    size_of = {}
    if series_stats is not None and "size" in getattr(series_stats, "columns", []):
        size_of = {s: int(series_stats.loc[s, "size"]) for s in named if s in series_stats.index}
    counts = hits[hits["series"].isin(named)]["series"].value_counts()
    for s in named:
        size_of.setdefault(s, int(counts.get(s, 0)))
    return sorted(named, key=lambda s: (_series_tier(sar_results, s), -size_of.get(s, 0), str(s)))


def _sel_entry(row):
    return {
        "chembl_id": row["molecule_chembl_id"],
        "smiles": row["smiles"],
        "series": row["series"],
        "pchembl": row["best_pchembl"],
        "hit_score": row["hit_score"],
        "variant": row.get("variant_best", "WT/unspecified"),
    }


def select_series_for_docking(hits, sar_results=None, series_stats=None,
                              dock_top_per_series=3, dock_top_overall=10, max_series=None):
    """Choose representative ligands, GUARANTEEING every EXPAND-recommended series is covered.

    Returns ``(unique_selected, provenance)``. ``unique_selected`` is a list of selection dicts;
    ``provenance`` records the ordering used and which EXPAND series were covered -- the invariant
    the v2 run violated. An EXPAND series with >=1 member is never dropped by ``max_series`` (which
    caps only NON-EXPAND series; None keeps all).
    """
    ranked = rank_series_for_docking(hits, sar_results=sar_results, series_stats=series_stats)
    expand_series = [s for s in ranked if _series_tier(sar_results, s) == 0]

    if max_series is not None:
        kept, n_non_expand, cap = [], 0, max(0, max_series - len(expand_series))
        for s in ranked:
            if s in expand_series:
                kept.append(s)
            elif n_non_expand < cap:
                kept.append(s)
                n_non_expand += 1
        chosen_series = kept
    else:
        chosen_series = ranked

    selected = []
    for s in chosen_series:
        sdf = hits[hits["series"] == s].nlargest(dock_top_per_series, "best_pchembl")
        for _, row in sdf.iterrows():
            selected.append(_sel_entry(row))
    for _, row in hits.nlargest(dock_top_overall, "best_pchembl").iterrows():
        e = _sel_entry(row)
        if e["chembl_id"] not in {x["chembl_id"] for x in selected}:
            selected.append(e)

    seen, unique_selected = set(), []
    for s in selected:
        if s["chembl_id"] not in seen:
            seen.add(s["chembl_id"])
            unique_selected.append(s)

    covered = {x["series"] for x in unique_selected}
    provenance = {
        "ordering": "sar_recommendation" if sar_results else (
            "series_size" if series_stats is not None else "series_frequency"),
        "n_series_selected": len(covered - {"Singleton/Other"}),
        "expand_series": expand_series,
        "expand_series_covered": [s for s in expand_series if s in covered],
        "expand_series_missing": [s for s in expand_series if s not in covered],
        "n_ligands": len(unique_selected),
    }
    return unique_selected, provenance


def _rcsb_json(url, payload=None, timeout=30):
    """GET or POST JSON against an RCSB endpoint. Returns None on any failure."""
    import urllib.request, urllib.error
    try:
        if payload is None:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
        else:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "Accept": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except Exception as exc:
        print(f"  RCSB request failed ({url.split('/')[-1][:40]}): {exc}")
        return None


def _entry_candidates(uniprot_accession, limit=40):
    """Search RCSB for X-ray entries of this UniProt accession that carry a ligand.

    Sorted by resolution ascending, so the best-resolution structures come first.
    """
    query = {
        "query": {
            "type": "group",
            "logical_operator": "and",
            "nodes": [
                {"type": "terminal", "service": "text", "parameters": {
                    "attribute": "rcsb_polymer_entity_container_identifiers"
                                 ".reference_sequence_identifiers.database_accession",
                    "operator": "exact_match", "value": uniprot_accession}},
                {"type": "terminal", "service": "text", "parameters": {
                    "attribute": "rcsb_entry_info.nonpolymer_entity_count",
                    "operator": "greater", "value": 0}},
            ],
        },
        "return_type": "entry",
        "request_options": {
            "paginate": {"start": 0, "rows": limit},
            "sort": [{"sort_by": "rcsb_entry_info.resolution_combined",
                      "direction": "asc"}],
        },
    }
    data = _rcsb_json("https://search.rcsb.org/rcsbsearch/v2/query", payload=query)
    if not data:
        return []
    return [hit["identifier"] for hit in data.get("result_set", [])]


def _drug_like_ligand(entry_id):
    """Return (comp_id, mw) of the largest drug-like co-crystal ligand, or (None, None)."""
    entry = _rcsb_json(f"https://data.rcsb.org/rest/v1/core/entry/{entry_id}")
    if not entry:
        return None, None
    comp_ids = (entry.get("rcsb_entry_container_identifiers", {})
                     .get("non_polymer_entity_ids") or [])
    best = (None, None)
    for ent_id in comp_ids:
        np_ent = _rcsb_json(
            f"https://data.rcsb.org/rest/v1/core/nonpolymer_entity/{entry_id}/{ent_id}")
        if not np_ent:
            continue
        comp = (np_ent.get("pdbx_entity_nonpoly", {}) or {}).get("comp_id")
        if not comp or comp.upper() in NON_LIGAND_HETATMS:
            continue
        chem = _rcsb_json(f"https://data.rcsb.org/rest/v1/core/chemcomp/{comp}")
        mw = ((chem or {}).get("chem_comp", {}) or {}).get("formula_weight")
        if mw is None or mw < _MIN_LIGAND_MW:
            continue
        if best[1] is None or mw > best[1]:
            best = (comp, mw)
    return best


def autoselect_pdb(uniprot_accession, max_examine=12):
    """Pick the best-resolution X-ray structure with a drug-like co-crystal ligand.

    Implements the procedure documented in SKILL.md section 5:
      1. search RCSB by UniProt accession
      2. keep entries carrying a co-crystal ligand
      3. rank by resolution ascending
      4. take the best-resolution entry with a drug-like ligand
      5. print the top candidates and the selection rationale

    Returns (pdb_id, rationale) or (None, reason) so the caller can fail loudly.
    """
    print(f"Auto-selecting a PDB structure for UniProt {uniprot_accession}...")
    entry_ids = _entry_candidates(uniprot_accession)
    if not entry_ids:
        return None, (f"RCSB returned no ligand-bound entries for {uniprot_accession} "
                      f"(or the search API was unreachable)")

    print(f"  {len(entry_ids)} ligand-bound candidates; examining up to {max_examine} "
          f"in resolution order")
    considered = []
    for entry_id in entry_ids[:max_examine]:
        entry = _rcsb_json(f"https://data.rcsb.org/rest/v1/core/entry/{entry_id}")
        if not entry:
            continue
        res = (entry.get("rcsb_entry_info", {}) or {}).get("resolution_combined") or []
        resolution = res[0] if res else None
        methods = [m.get("method", "") for m in (entry.get("exptl") or [])]
        method = methods[0] if methods else "unknown"
        comp, mw = _drug_like_ligand(entry_id)
        considered.append({"pdb_id": entry_id, "resolution": resolution,
                           "method": method, "ligand": comp, "ligand_mw": mw})
        if comp and resolution is not None:
            break  # candidates are resolution-sorted, so the first qualifying one wins

    print("  Candidates examined (best resolution first):")
    for c in considered[:5]:
        res_s = f"{c['resolution']:.2f} A" if c["resolution"] is not None else "n/a"
        lig_s = f"{c['ligand']} ({c['ligand_mw']:.0f} Da)" if c["ligand"] else "no drug-like ligand"
        print(f"    {c['pdb_id']}  {res_s:>8}  {c['method']:<28} {lig_s}")

    qualifying = [c for c in considered if c["ligand"] and c["resolution"] is not None]
    if not qualifying:
        return None, (f"examined {len(considered)} entries for {uniprot_accession}; none had "
                      f"both a resolution and a drug-like co-crystal ligand "
                      f"(>= {_MIN_LIGAND_MW:.0f} Da, not a buffer/ion)")

    pick = qualifying[0]
    rationale = (f"{pick['pdb_id']} selected: best-resolution {pick['method']} structure "
                 f"({pick['resolution']:.2f} A) carrying drug-like co-crystal ligand "
                 f"{pick['ligand']} ({pick['ligand_mw']:.0f} Da), which defines the docking box")
    print(f"  -> {rationale}")
    return pick["pdb_id"], rationale


def pdb_mutation_annotation(pdb_id):
    """Best-effort: read engineered mutations annotated on a PDB entry's polymer entities.

    Returns a string like "L858R, T790M" or None. This is only an ORIENTATION aid: RCSB's
    ``pdbx_mutation`` lists all engineered point mutations, which can include crystallisation
    constructs (e.g. surface entropy reduction) as well as the disease variant of interest, and
    a wild-type catalytic domain may still carry such mutations. It is therefore labelled as
    "PDB-annotated" and never treated as authoritative; use --receptor-variant to declare the
    biologically relevant variant explicitly.
    """
    entry = _rcsb_json(f"https://data.rcsb.org/rest/v1/core/entry/{pdb_id}")
    if not entry:
        return None
    ent_ids = (entry.get("rcsb_entry_container_identifiers", {}) or {}).get("polymer_entity_ids") or []
    muts = []
    counts = []
    for ent_id in ent_ids:
        pe = _rcsb_json(f"https://data.rcsb.org/rest/v1/core/polymer_entity/{pdb_id}/{ent_id}")
        if not pe:
            continue
        m = (pe.get("rcsb_polymer_entity", {}) or {}).get("pdbx_mutation")
        if m:
            muts.append(str(m).strip())
        c = (pe.get("entity_poly", {}) or {}).get("rcsb_mutation_count")
        if c is not None:
            counts.append(c)
    if muts:
        # De-duplicate tokens while preserving order.
        seen, out = set(), []
        for chunk in muts:
            for tok in chunk.replace(";", ",").split(","):
                tok = tok.strip()
                if tok and tok not in seen:
                    seen.add(tok)
                    out.append(tok)
        return ", ".join(out) if out else None
    # No engineered mutations listed AND RCSB reports zero mutations on every entity -> WT.
    if counts and all(c == 0 for c in counts):
        return "WT"
    return None


def prepare_receptor_pdbqt(protein_pdb, outdir):
    """Convert receptor PDB to PDBQT using Meeko or ADFR (NOT Open Babel)."""
    pdbqt_path = os.path.join(outdir, "receptor.pdbqt")

    # Try Meeko mk_prepare_receptor.py first (LGPL, commercial-safe)
    mk_script = shutil.which("mk_prepare_receptor.py")
    if mk_script:
        result = subprocess.run(
            [mk_script, "--read_pdb", protein_pdb, "-o", os.path.join(outdir, "receptor"),
             "-p", pdbqt_path],
            capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and os.path.exists(pdbqt_path):
            print(f"Receptor PDBQT prepared with Meeko: {pdbqt_path}")
            return pdbqt_path
        else:
            print(f"Meeko receptor prep failed: {result.stderr[:200]}")

    # Fallback: ADFR prepare_receptor (LGPL2, commercial-safe -- https://ccsb.scripps.edu/adfr/license/)
    adfr = shutil.which("prepare_receptor")
    if adfr:
        result = subprocess.run(
            [adfr, "-r", protein_pdb, "-o", pdbqt_path],
            capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and os.path.exists(pdbqt_path):
            print(f"Receptor PDBQT prepared with ADFR: {pdbqt_path}")
            return pdbqt_path
        else:
            print(f"ADFR receptor prep failed: {result.stderr[:200]}")

    # Last resort: Open Babel (GPL-2.0, copyleft). Copyleft is a source-distribution obligation
    # on derivative works, NOT a commercial-use ban -- but the skill avoids it to sidestep that
    # obligation, so its use is flagged loudly and must be disclosed in the report.
    obabel = shutil.which("obabel")
    if obabel:
        print("WARNING: Using Open Babel (GPL-2.0, copyleft) for receptor prep. Copyleft imposes "
              "a source-distribution obligation on derivative works; disclose this in the report "
              "and prefer Meeko/ADFR.", file=sys.stderr)
        result = subprocess.run(
            [obabel, protein_pdb, "-O", pdbqt_path, "-xr", "-xd"],
            capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and os.path.exists(pdbqt_path):
            print(f"Receptor PDBQT prepared with Open Babel: {pdbqt_path}")
            return pdbqt_path

    print("ERROR: No receptor preparation tool available.", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Prepare docking receptor and ligands")
    parser.add_argument("--hits", required=True, help="Path to hits_scored_clustered.csv")
    parser.add_argument("--pdb-id", default=None, help="PDB ID (auto-selected if omitted)")
    parser.add_argument("--pdb-file", default=None, help="Local PDB file (alternative to --pdb-id)")
    parser.add_argument("--uniprot", default=None,
                        help="UniProt accession for PDB auto-selection. If omitted, read from "
                             "<data-dir>/target_metadata.json (written by retrieve_activities.py)")
    parser.add_argument("--receptor-variant", default=None,
                        help="Declare the biologically relevant variant the docked structure "
                             "represents (e.g. 'WT', 'L858R', 'L858R/T790M'). Recorded in "
                             "docking_box.json and surfaced in the reports so the mismatch "
                             "against pooled activity variants is explicit. If omitted, a "
                             "best-effort PDB-annotated mutation string is used, else 'unspecified'.")
    parser.add_argument("--outdir", required=True, help="Output directory for tmp files")
    parser.add_argument("--data-dir", required=True, help="Data directory for outputs")
    parser.add_argument("--dock-top-per-series", type=int, default=3)
    parser.add_argument("--dock-top-overall", type=int, default=10)
    parser.add_argument("--sar-results", default=None,
                        help="Path to sar_results.json (default: <data-dir>/sar_results.json). "
                             "Representatives are prioritised by SAR recommendation (EXPAND first) "
                             "so every EXPAND-recommended series gets a docked representative.")
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(args.data_dir, exist_ok=True)

    # ---- 1. Get PDB structure ----
    pdb_selection = {"mode": None, "rationale": None}
    if args.pdb_file:
        pdb_path = args.pdb_file
        pdb_id = os.path.basename(pdb_path).replace(".pdb", "")
        pdb_selection = {"mode": "user_file", "rationale": f"user-supplied file {pdb_path}"}
    elif args.pdb_id:
        pdb_id = args.pdb_id.upper()
        print(f"Downloading PDB {pdb_id}...")
        pdb_path = download_pdb(pdb_id, args.data_dir)
        pdb_selection = {"mode": "user_pdb_id", "rationale": f"user-specified PDB ID {pdb_id}"}
    else:
        # Auto-select. Needs a UniProt accession -- either passed in, or read from the
        # target metadata that retrieve_activities.py wrote.
        uniprot = args.uniprot
        if not uniprot:
            meta_path = os.path.join(args.data_dir, "target_metadata.json")
            if os.path.exists(meta_path):
                with open(meta_path) as f:
                    uniprot = (json.load(f) or {}).get("uniprot_accession")
        if not uniprot:
            print("ERROR: no PDB specified and no UniProt accession available for "
                  "auto-selection.\n"
                  "  Pass --pdb-id / --pdb-file, or --uniprot, or ensure "
                  "<data-dir>/target_metadata.json contains 'uniprot_accession'.",
                  file=sys.stderr)
            sys.exit(1)

        pdb_id, rationale = autoselect_pdb(uniprot)
        if not pdb_id:
            print(f"ERROR: PDB auto-selection failed: {rationale}\n"
                  f"  Supply a structure explicitly with --pdb-id or --pdb-file.",
                  file=sys.stderr)
            sys.exit(1)
        print(f"Downloading auto-selected PDB {pdb_id}...")
        pdb_path = download_pdb(pdb_id, args.data_dir)
        pdb_selection = {"mode": "auto", "uniprot": uniprot, "rationale": rationale}

    print(f"Using PDB structure: {pdb_id}")

    # ---- Resolve which variant this receptor represents ----
    # Priority: explicit --receptor-variant > best-effort PDB annotation > "unspecified".
    if args.receptor_variant:
        receptor_variant = args.receptor_variant
        receptor_variant_source = "user (--receptor-variant)"
    else:
        annotated = pdb_mutation_annotation(pdb_id)
        if annotated == "WT":
            receptor_variant = "WT"
            receptor_variant_source = "RCSB: 0 engineered mutations annotated on the polymer entity"
        elif annotated:
            receptor_variant = f"PDB-annotated: {annotated}"
            receptor_variant_source = ("RCSB pdbx_mutation (may include engineered/"
                                       "crystallisation mutations, not only the disease variant)")
        else:
            receptor_variant = "unspecified"
            receptor_variant_source = "not declared and no PDB mutation annotation found"
    print(f"Docked receptor represents variant: {receptor_variant}  [{receptor_variant_source}]")

    # ---- 2. Extract protein and co-crystal ligand ----
    with open(pdb_path) as f:
        pdb_lines = f.readlines()

    protein_lines = []
    ligand_lines = []
    ligand_resnames = set()
    excluded_resnames = set()
    for line in pdb_lines:
        if line.startswith("ATOM"):
            protein_lines.append(line)
        elif line.startswith("HETATM"):
            resname = line[17:20].strip()
            # Use the same additive ignore-list as PDB auto-selection. A short water/ion list
            # is not enough: crystallisation additives such as NO3 and EDO survive it, get
            # treated as co-crystal ligands, and inflate the docking box while shifting its
            # centre off the true pocket. Observed on 8A27, where the ligand set came out as
            # {KY9, NO3, EDO} and the box grew to ~55x39x50 A.
            if resname.upper() not in NON_LIGAND_HETATMS:
                ligand_lines.append(line)
                ligand_resnames.add(resname)
            else:
                excluded_resnames.add(resname)

    protein_path = os.path.join(args.outdir, f"{pdb_id}_protein.pdb")
    with open(protein_path, "w") as f:
        f.writelines(protein_lines)
    print(f"Protein atoms: {len(protein_lines)}")
    print(f"Co-crystal ligand residues: {ligand_resnames}")
    if excluded_resnames:
        print(f"Excluded crystallisation additives/ions from the box: "
              f"{sorted(excluded_resnames)}")
    print(f"Co-crystal ligand atoms: {len(ligand_lines)}")

    if not ligand_lines:
        print("WARNING: No co-crystal ligand found. Docking box cannot be auto-defined.",
              file=sys.stderr)
        print("Please supply docking box coordinates via --box-json.", file=sys.stderr)

    # ---- 3. Define docking box from co-crystal ligand ----
    ligand_coords = []
    for line in ligand_lines:
        x = float(line[30:38])
        y = float(line[38:46])
        z = float(line[46:54])
        ligand_coords.append([x, y, z])
    box_info = build_box_info(
        ligand_coords, ligand_resnames, excluded_resnames, pdb_id,
        receptor_variant, receptor_variant_source, pdb_selection)
    box_path = os.path.join(args.data_dir, "docking_box.json")
    with open(box_path, "w") as f:
        json.dump(box_info, f, indent=2)
    print(f"Box: center=({box_info['center_x']:.1f},{box_info['center_y']:.1f},"
          f"{box_info['center_z']:.1f}), size=({box_info['size_x']:.1f},"
          f"{box_info['size_y']:.1f},{box_info['size_z']:.1f})")
    if box_info["excluded_additives"]:
        print(f"Recorded excluded additives in docking_box.json: "
              f"{box_info['excluded_additives']}")

    # ---- 4. Prepare receptor PDBQT ----
    print("\nPreparing receptor PDBQT...")
    receptor_pdbqt = prepare_receptor_pdbqt(protein_path, args.outdir)

    # ---- 5. Select representative ligands (SAR-priority; guarantees EXPAND coverage) ----
    print("\nSelecting representative ligands...")
    hits = pd.read_csv(args.hits)

    # Read SAR recommendations so docking follows the ANALYTICAL priority (EXPAND first), not the
    # incidental row order of the hits table (the v2 defect: EXPAND Series Q/T were never docked).
    sar_results = None
    sar_path = args.sar_results or os.path.join(args.data_dir, "sar_results.json")
    if os.path.exists(sar_path):
        try:
            with open(sar_path) as f:
                sar_results = json.load(f)
            print(f"Loaded SAR recommendations from {sar_path} ({len(sar_results)} series); "
                  f"docking will prioritise EXPAND-recommended series.")
        except Exception as exc:
            print(f"WARNING: could not read {sar_path} ({exc}); ordering series by size instead.",
                  file=sys.stderr)
    else:
        print(f"NOTE: {sar_path} not found; ordering series by size, not SAR recommendation. Run "
              f"sar_analysis.py before prepare_docking.py so EXPAND series are prioritised.",
              file=sys.stderr)

    series_stats = None
    stats_path = os.path.join(args.data_dir, "series_stats.csv")
    if os.path.exists(stats_path):
        try:
            series_stats = pd.read_csv(stats_path, index_col=0)
        except Exception:
            series_stats = None

    unique_selected, selection_provenance = select_series_for_docking(
        hits, sar_results=sar_results, series_stats=series_stats,
        dock_top_per_series=args.dock_top_per_series,
        dock_top_overall=args.dock_top_overall)

    print(f"Selected {len(unique_selected)} ligands for docking")
    print(f"Series represented: {selection_provenance['n_series_selected']} "
          f"(ordering: {selection_provenance['ordering']})")
    if selection_provenance["expand_series"]:
        print(f"EXPAND series: {selection_provenance['expand_series']}")
        print(f"EXPAND series with a docked representative: "
              f"{selection_provenance['expand_series_covered']}")
        if selection_provenance["expand_series_missing"]:
            print(f"WARNING: EXPAND series with NO dockable representative: "
                  f"{selection_provenance['expand_series_missing']}", file=sys.stderr)

    sel_path = os.path.join(args.data_dir, "docking_selection.json")
    with open(sel_path, "w") as f:
        json.dump(unique_selected, f, indent=2)
    prov_path = os.path.join(args.data_dir, "docking_selection_provenance.json")
    with open(prov_path, "w") as f:
        json.dump(selection_provenance, f, indent=2, sort_keys=True)

    # ---- 6. Prepare ligand PDBQT files ----
    print("\nPreparing ligand PDBQT files...")
    lig_dir = os.path.join(args.outdir, "ligands")
    os.makedirs(lig_dir, exist_ok=True)

    Chem, AllChem = _load_rdkit()
    from meeko import MoleculePreparation, PDBQTWriterLegacy
    preparer = MoleculePreparation()
    prepared_count = 0
    failed = []

    for sel in unique_selected:
        cid = sel["chembl_id"]
        mol = Chem.MolFromSmiles(sel["smiles"])
        if mol is None:
            failed.append((cid, "invalid SMILES"))
            continue
        mol = Chem.AddHs(mol)
        try:
            AllChem.EmbedMolecule(mol, randomSeed=42)
            AllChem.MMFFOptimizeMolecule(mol, maxIters=500)
        except Exception as e:
            failed.append((cid, f"3D embed failed: {e}"))
            continue

        try:
            preparations = preparer.prepare(mol)
            result = PDBQTWriterLegacy.write_string(preparations[0])
            # CRITICAL: result is a 3-tuple in Meeko 0.7.x, use result[0]
            pdbqt_string = result[0] if isinstance(result, tuple) else result
            with open(os.path.join(lig_dir, f"{cid}.pdbqt"), "w") as f:
                f.write(pdbqt_string)
            prepared_count += 1
        except Exception as e:
            failed.append((cid, f"PDBQT write failed: {e}"))

    print(f"Prepared {prepared_count} ligand PDBQT files")
    if failed:
        print(f"Failed: {len(failed)}")
        for cid, reason in failed[:5]:
            print(f"  {cid}: {reason}")

    print(f"\nDocking preparation complete.")
    print(f"Receptor: {receptor_pdbqt}")
    print(f"Ligands: {lig_dir}/")


if __name__ == "__main__":
    main()
