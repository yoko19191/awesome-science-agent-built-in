# Environment and Troubleshooting

Concrete failures seen while building and running this skill, with fixes.
Most are **silent** — they produce a plausible-looking wrong answer rather than
an error — so each script validates its own output.

## Environment setup

```bash
# Install per run into the conda interpreter (not the .venv symlink)
uv pip install --python /opt/conda/bin/python vina meeko gemmi
export PATH=/opt/conda/bin:/usr/local/bin:$PATH
```

**Why `/opt/conda/bin/python`:** The base image ships a `/workspace/.venv`
whose `bin/python` can be a broken symlink. A bare `uv pip install` may land
packages where imports won't find them. Pin both the install target and the
script invocation to `/opt/conda/bin/python`.

## Known quirks and fixes

### 1. rdFMCS.FindMCS — `completeRings` vs `completeRingsOnly`

**Symptom:** `Boost.Python.ArgumentError: Python argument types in
rdkit.Chem.rdFMCS.FindMCS(list) did not match C++ signature`

**Cause:** The cold run used `completeRings=True` as a keyword argument. In
RDKit 2025.x, the correct parameter name is `completeRingsOnly`, not
`completeRings`. The old name is not recognized and raises a Boost.Python
argument error that is caught by the `except` block, silently setting MCS to
None for **every** series.

**Fix:**
```python
# WRONG (causes silent failure in all series):
mcs = rdFMCS.FindMCS(mols, timeout=10, threshold=0.6,
                     atomCompare=rdFMCS.AtomCompare.CompareElements,
                     bondCompare=rdFMCS.BondCompare.CompareOrder,
                     ringMatchesRingOnly=True, completeRings=True)  # ← BUG

# CORRECT:
mcs = rdFMCS.FindMCS(mols, timeout=10, threshold=0.6,
                     atomCompare=rdFMCS.AtomCompare.CompareElements,
                     bondCompare=rdFMCS.BondCompare.CompareOrder,
                     ringMatchesRingOnly=True, completeRingsOnly=True)  # ← FIX
```

**Impact:** This single bug caused MCS extraction and R-group decomposition to
fail silently in all 20 series in the original TYK2 cold run. The
`scaffold_smarts.json` file was empty (0 entries). Murcko scaffolds worked as a
fallback, but the MCS-based SAR was completely lost.

### 2. rdRGroupDecomposition.RGroupDecompose — `asRows` parameter

**Symptom:** `Boost.Python.ArgumentError` when calling RGroupDecompose.

**Cause:** The function signature requires `asRows=True` as a keyword argument
(not positional). Without it, the call fails.

**Fix:**
```python
# CORRECT:
groups, unmatched = rdRGroupDecomposition.RGroupDecompose(
    [mcs_mol], mols, asSmiles=True, asRows=True)
```

### 3. Meeko PDBQTWriterLegacy — 3-tuple return

**Symptom:** `TypeError` or garbled PDBQT output when writing ligand PDBQT.

**Cause:** `PDBQTWriterLegacy.write_string()` returns a **3-tuple**
`(pdbqt_string, atom_data, ...)` in Meeko 0.7.x, not a 2-tuple or a string.

**Fix:**
```python
result = PDBQTWriterLegacy.write_string(preparations[0])
pdbqt_string = result[0] if isinstance(result, tuple) else result
```

### 4. Meeko requires gemmi

**Symptom:** `ModuleNotFoundError: No module named 'gemmi'` at runtime when
Meeko prepares a ligand.

**Cause:** Meeko 0.7.x imports gemmi lazily (for PDB parsing), so `pip install
meeko` succeeds but the import fails at runtime.

**Fix:** Always install gemmi alongside meeko:
```bash
uv pip install --python /opt/conda/bin/python meeko gemmi
```

### 5. AutoDock Vina 1.2.5 — no `--log` flag

**Symptom:** `vina: unrecognized option '--log'`

**Cause:** Vina 1.2.5 does not support `--log`. Scores are printed to stdout
only.

**Fix:** Parse scores from stdout by detecting the `mode | affinity` table
header and extracting numeric fields:
```python
in_table = False
for line in stdout.split("\n"):
    if "mode |" in line and "affinity" in line:
        in_table = True
        continue
    if in_table:
        if "-----+" in line or line.strip() == "" or "Writing" in line:
            in_table = False
            continue
        parts = line.split()
        if len(parts) >= 4:
            mode_num = int(parts[0])
            affinity = float(parts[1])
            rmsd_lb = float(parts[2])
            rmsd_ub = float(parts[3])
```

### 6. Open Babel kekulization warning on receptor PDBQT

**Symptom:** `obabel` prints a kekulization warning when converting a protein
PDB to PDBQT.

**Status:** Non-fatal. The receptor PDBQT is valid for docking despite the
warning. However, **prefer Meeko `mk_prepare_receptor.py` or ADFR
`prepare_receptor`** to avoid the GPL v2 licensing constraint of Open Babel.

### 7. RCSB PDB text search returns wrong structures

**Symptom:** Searching RCSB for "TYK2" by title returns mostly non-TYK2
structures (PTK1B, FAK, etc.) due to substring matching.

**Fix:** Search by UniProt accession instead of protein name:
```python
# Use the UniProt accession (e.g. P29597 for TYK2)
query = {
    "query": {
        "type": "terminal",
        "service": "text",
        "parameters": {
            "attribute": "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_accession",
            "operator": "in",
            "value": ["P29597"]
        }
    },
    "return_type": "entry",
    "request_options": {"return_all_hits": True}
}
```

**Note:** The `operator` must be `"in"` with a list value, not `"equals"` with
a string value — the latter returns HTTP 400.

### 8. ChEMBL API pagination

**Symptom:** Only 20 records returned when expecting thousands.

**Cause:** ChEMBL API defaults to `limit=20`. Must paginate with `offset`.

**Fix:**
```python
import urllib.request, json, time

def fetch_all_activities(target_chembl_id, batch_size=1000):
    all_activities = []
    offset = 0
    while True:
        url = (f"https://www.ebi.ac.uk/chembl/api/data/activity.json"
               f"?target_chembl_id={target_chembl_id}"
               f"&limit={batch_size}&offset={offset}")
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read())
        activities = data.get("activities", [])
        if not activities:
            break
        all_activities.extend(activities)
        total = data.get("page_meta", {}).get("total_count", 0)
        offset += batch_size
        if offset >= total:
            break
        time.sleep(0.3)  # be polite
    return all_activities
```

### 9. ChEMBL target resolution — avoid homonym confusion

**Symptom:** Searching ChEMBL for "TYK2" returns both TYK2 (CHEMBL3553) and
PTK2B/FAK (unrelated kinase) due to synonym matching.

**Fix:** Filter by `target_type="SINGLE PROTEIN"` and `organism="Homo sapiens"`
to narrow to the intended target. If ambiguity remains, search by UniProt
accession via the `target_components` endpoint.

### 10. Docking-potency correlation overclaiming

**Symptom:** The original cold run reported "Pearson r = 0.39 (moderate)" for
the docking-potency correlation, but the actual computed value was r = 0.133
(p = 0.353, not significant).

**Cause:** The r = 0.39 value was never printed to tool output — it was
hallucinated in the narrative text and rendered in the figure/PDF without
verification. The actual value was only discovered during scientific review.

**Fix:** Always compute the correlation with `scipy.stats.pearsonr` and print
it to stdout before writing it into any report or figure. Never state a
correlation value that was not printed to tool output. Use honest language:
- r < 0.3: "weak" or "not significant"
- r 0.3–0.5: "moderate" (only if p < 0.05)
- r > 0.5: "strong" (only if p < 0.05)

### 11. Butina clustering — bulk distances, and `array.array` is rejected

**Symptom (performance):** the original nested `for i: for j: TanimotoSimilarity(fps[i], fps[j])`
loop makes ~54.4M Python-level calls at EGFR scale and needs a provisioned machine.

**Fix:** compute distances with `DataStructs.BulkTanimotoSimilarity(fps[i], fps[:i])` (one C++
call per row). Bit-identical to the loop (max abs diff 0.0; SHA256 of the full 54.4M-pair vector
matches; identical clusters). Measured on a 1-CPU / 16 GB worker: ~6 s / ~1.4 GB vs ~85 s / ~5.1 GB.

**Gotcha:** `rdkit.ML.Cluster.Butina.ClusterData(..., isDistData=True)` in RDKit 2025.03.5 accepts
`list`, `tuple`, or `numpy.ndarray` but **raises `TypeError` for `array.array`**. Pass a NumPy
array. Passing a **dense n×n matrix** (not the 1-D condensed vector) uses Butina's matrix code path
and skips its internal 1-D→dense expansion (a `zeros((n,n))` + `tril_indices` + transpose
temporary), which is what drops peak RSS from ~5.5 GB to ~1.4 GB. See
`scripts/score_and_cluster.bulk_distance_matrix` and `scripts/benchmark_clustering.py`.

### 12. Meeko receptor preparation can fail with an empty error → ADFR fallback

**Symptom:** `mk_prepare_receptor.py` returns non-zero with an empty stderr; no receptor PDBQT.

**Status:** non-fatal. `prepare_docking.py` falls back to ADFR `prepare_receptor` (LGPL2,
commercial-safe — https://ccsb.scripps.edu/adfr/license/). Observed on the EGFR run (8A27): Meeko failed, ADFR produced a valid receptor,
the licence gate held (Open Babel never used). If ADFR is also unavailable, install it or pass a
pre-prepared receptor PDBQT.

## Version reference (verified 2026-08-04)

| Tool | Version | License |
|---|---|---|
| RDKit | 2025.03.5 | BSD-3-Clause |
| AutoDock Vina | 1.2.5 | Apache 2.0 |
| Open Babel | 3.1.0 | GPL v2 |
| Meeko | 0.7.1 | LGPL-2.1 |
| gemmi | 0.7.5 | MPL-2.0 |
| pandas | 2.3.1 | BSD-3-Clause |
| numpy | 2.1.0 | BSD-3-Clause |
| scipy | 1.15.0 | BSD-3-Clause |
| matplotlib | 3.10.5 | PSF-based |
| seaborn | 0.13.2 | BSD-3-Clause |
| reportlab | 4.4.3 | BSD (ReportLab) |
| pypdf | 6.14.2 | BSD-3-Clause |
| Python | 3.11 | PSF |
| ChEMBL API | ChEMBL_37 (2026-05-01) | CC BY-SA 3.0 |
| RCSB PDB | live | CC0 1.0 |
