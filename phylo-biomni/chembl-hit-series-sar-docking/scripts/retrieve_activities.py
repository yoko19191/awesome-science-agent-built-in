#!/usr/bin/env python3
"""Retrieve bioactivity records for a protein target from ChEMBL.

Resolves the target by name/synonym/UniProt, filters by organism and target
type, and paginates through all activity records.

Usage:
    python retrieve_activities.py --target "TYK2" --organism "Homo sapiens" \
        --target-type "SINGLE PROTEIN" --outdir <dir>
"""
import argparse, json, os, sys, time, urllib.request, urllib.error

CHEMBL_BASE = "https://www.ebi.ac.uk/chembl/api/data"


def search_targets(query, organism=None, target_type=None):
    """Search ChEMBL for targets matching the query."""
    # Try by name/synonym first
    url = f"{CHEMBL_BASE}/target.json?target_synonym={urllib.parse.quote(query)}&limit=100"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    resp = urllib.request.urlopen(req, timeout=30)
    data = json.loads(resp.read())
    targets = data.get("targets", [])

    # Also try pref_name search
    url2 = f"{CHEMBL_BASE}/target.json?pref_name={urllib.parse.quote(query)}&limit=100"
    req2 = urllib.request.Request(url2, headers={"Accept": "application/json"})
    resp2 = urllib.request.urlopen(req2, timeout=30)
    data2 = json.loads(resp2.read())
    targets2 = data2.get("targets", [])

    # Merge and deduplicate
    seen_ids = {t["target_chembl_id"] for t in targets}
    for t in targets2:
        if t["target_chembl_id"] not in seen_ids:
            targets.append(t)
            seen_ids.add(t["target_chembl_id"])

    # Filter by organism and target_type
    if organism:
        targets = [t for t in targets if t.get("organism", "").lower() == organism.lower()]
    if target_type:
        targets = [t for t in targets if t.get("target_type", "").upper() == target_type.upper()]

    return targets


def fetch_all_activities(target_chembl_id, batch_size=1000, max_records=None):
    """Paginate through all activity records for a target."""
    all_activities = []
    offset = 0
    while True:
        url = (
            f"{CHEMBL_BASE}/activity.json"
            f"?target_chembl_id={target_chembl_id}"
            f"&limit={batch_size}&offset={offset}"
        )
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            print(f"  HTTP error at offset {offset}: {e.code} {e.reason}", file=sys.stderr)
            break
        data = json.loads(resp.read())
        activities = data.get("activities", [])
        if not activities:
            break
        all_activities.extend(activities)
        total = data.get("page_meta", {}).get("total_count", 0)
        offset += batch_size
        print(f"  Retrieved {len(all_activities)}/{total} records...", flush=True)
        if max_records and len(all_activities) >= max_records:
            all_activities = all_activities[:max_records]
            break
        if offset >= total:
            break
        time.sleep(0.3)
    return all_activities


def fetch_chembl_release():
    """Return the ChEMBL release string (e.g. "ChEMBL_37"), or None.

    ChEMBL is CC BY-SA 3.0, and the attribution must name the release, not just "ChEMBL" --
    so this is a licence-compliance requirement, not a nicety.
    """
    try:
        req = urllib.request.Request(f"{CHEMBL_BASE}/status.json",
                                     headers={"Accept": "application/json"})
        st = json.loads(urllib.request.urlopen(req, timeout=30).read())
        ver = st.get("chembl_db_version") or st.get("chembl_release")
        return str(ver) if ver else None
    except Exception as exc:
        print(f"WARNING: could not resolve ChEMBL release: {exc}")
        return None


def fetch_target_details(target_chembl_id):
    """Fetch a ChEMBL target record and pull its preferred name + UniProt accession.

    The UniProt accession is what RCSB is searched by during PDB auto-selection, so
    capturing it here is what makes `prepare_docking.py --auto-pdb` possible.
    Returns (pref_name, uniprot_accession); either may be None.
    """
    url = f"{CHEMBL_BASE}/target/{target_chembl_id}.json"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        rec = json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception as exc:  # network/API degradation must not kill the run
        print(f"WARNING: could not fetch target details for {target_chembl_id}: {exc}")
        return None, None

    pref_name = rec.get("pref_name")
    accession = None
    for comp in rec.get("target_components") or []:
        if comp.get("accession"):
            accession = comp["accession"]
            break  # single-protein targets have exactly one; take the first otherwise
    n_components = len(rec.get("target_components") or [])
    if n_components > 1:
        print(f"NOTE: {target_chembl_id} has {n_components} components; "
              f"using accession {accession} for structure search")
    return pref_name, accession


def main():
    parser = argparse.ArgumentParser(description="Retrieve ChEMBL bioactivity for a target")
    parser.add_argument("--target", required=True, help="Target name, synonym, or ChEMBL ID")
    parser.add_argument("--organism", default="Homo sapiens", help="Filter by organism")
    parser.add_argument("--target-type", default="SINGLE PROTEIN", help="Filter by target type")
    parser.add_argument("--outdir", required=True, help="Output directory")
    parser.add_argument("--max-records", type=int, default=None, help="Max records (for testing)")
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    target_name = None  # set below in whichever branch resolves the target

    # If target looks like a ChEMBL ID, use it directly
    if args.target.upper().startswith("CHEMBL"):
        target_id = args.target.upper()
        print(f"Using provided ChEMBL target ID: {target_id}")
    else:
        # Search for the target
        print(f"Searching ChEMBL for target: '{args.target}'")
        targets = search_targets(args.target, args.organism, args.target_type)
        print(f"Found {len(targets)} candidate targets:")
        for t in targets:
            print(f"  {t['target_chembl_id']}: {t.get('pref_name', 'N/A')} "
                  f"({t.get('target_type', '')}) organism={t.get('organism', '')}")

        if not targets:
            print(f"\nERROR: No targets found for '{args.target}' with "
                  f"organism='{args.organism}', target_type='{args.target_type}'",
                  file=sys.stderr)
            print("Suggestions: try a different name, check the organism, "
                  "or search by UniProt accession.", file=sys.stderr)
            sys.exit(1)

        # Pick the first SINGLE PROTEIN target for the organism
        target_id = targets[0]["target_chembl_id"]
        target_name = targets[0].get("pref_name", "N/A")
        print(f"\nSelected: {target_id} ({target_name})")

    # Fetch all activities
    print(f"\nFetching bioactivity records for {target_id}...")
    activities = fetch_all_activities(target_id, max_records=args.max_records)
    print(f"Total records retrieved: {len(activities)}")

    if not activities:
        print(f"ERROR: No activity records found for {target_id}", file=sys.stderr)
        sys.exit(1)

    # Confirm the structured variant fields are present and retained through retrieval.
    # (The full records are dumped verbatim below, so nothing is discarded; this is a
    #  visibility check — see process_activities.py for the scored-set composition.)
    from collections import Counter
    has_mut = sum(1 for a in activities if a.get("assay_variant_mutation"))
    has_acc = sum(1 for a in activities if a.get("assay_variant_accession"))
    vc = Counter((a.get("assay_variant_mutation") or "WT/unspecified") for a in activities)
    print(f"Variant fields retained: assay_variant_mutation on {has_mut}/{len(activities)} records, "
          f"assay_variant_accession on {has_acc}/{len(activities)}")
    if len(vc) > 1:
        top = ", ".join(f"{k}={n}" for k, n in vc.most_common(6))
        print(f"Raw variant labels ({len(vc)} distinct, top): {top}")
        print("NOTE: this target pools multiple assay variants; process_activities.py reports "
              "the full composition, warns, and offers --variant to restrict scoring.")

    # Save
    out_path = os.path.join(args.outdir, "activities_raw.json")
    with open(out_path, "w") as f:
        json.dump(activities, f)
    print(f"Saved to {out_path}")

    # Save target metadata, including the UniProt accession that PDB auto-selection needs
    pref_name, uniprot_accession = fetch_target_details(target_id)
    chembl_release = fetch_chembl_release()
    meta = {
        "target_chembl_id": target_id,
        "target_name": pref_name or target_name or args.target,
        "uniprot_accession": uniprot_accession,
        "chembl_release": chembl_release,
    }
    if chembl_release:
        print(f"ChEMBL release: {chembl_release} (required in the CC BY-SA attribution)")
    if uniprot_accession:
        print(f"UniProt accession: {uniprot_accession} "
              f"(enables PDB auto-selection in prepare_docking.py)")
    else:
        print("WARNING: no UniProt accession resolved - PDB auto-selection will be "
              "unavailable; pass --pdb-id explicitly to prepare_docking.py")
    meta_path = os.path.join(args.outdir, "target_metadata.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Target metadata saved to {meta_path}")


if __name__ == "__main__":
    import urllib.parse
    main()
