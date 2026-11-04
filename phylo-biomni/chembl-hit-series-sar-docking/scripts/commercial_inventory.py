#!/usr/bin/env python3
"""Dependency & commercial-use inventory -- the derive mechanism for Review Stop 4A findings 3/4/6.

WHY THIS EXISTS
    The v2 candidate copied a dependency licence into the report without verifying it against the
    dependency's own licence page: it stated ADFR ``prepare_receptor`` is "Apache 2.0" (it is LGPL2
    per https://ccsb.scripps.edu/adfr/license/), and it mislabelled Open Babel's GPL-2.0 copyleft as
    a commercial-use "prohibition" (copyleft is a source-distribution obligation on derivative works,
    not a ban). ``eval.yaml`` then self-cleared the whole package with ``license_gate: PASS`` while
    real obligations (ChEMBL CC BY-SA 3.0 share-alike) and human-decision commercial holds were open.

    Fixing the prose is not enough -- the same copy-without-verify would recur. This module is the
    general mechanism the brief asks for: DERIVE the inventory from (a) the DECLARED dependencies and
    (b) the ACTUALLY-EXECUTED imports/tools, BIND each to an authoritative licence-evidence source
    (the dependency's own licence page), distinguish included/used/optional/excluded roles, and RETAIN
    ``unknown``/obligation/``prohibited`` as an explicit HOLD -- never self-clear it.

    ``assert_commercial_inventory_consistent`` is the fail-closed gate. It fails on:
      * a licence claim in the package that CONFLICTS with the authoritative evidence,
      * a material (executed/included) dependency with no authoritative evidence URL,
      * a self-clear ("license_gate: PASS" / "commercially cleared") asserted while holds are open.
    It never returns "commercially cleared": the overall status is HOLD whenever any obligation or
    open human-decision hold exists, which is routed to Phylo/legal review.

    Stdlib only, deterministic (sorted keys/lists, relative paths only -- no timestamps, no absolute
    paths), so its JSON output is byte-identical across runs and it leaks nothing.

USAGE
    python commercial_inventory.py --package <pkg-dir> --out-json commercial_inventory.json \
        --out-md commercial_inventory.md
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import pathlib
import re


class CommercialInventoryError(AssertionError):
    """Raised by the consistency gate on a conflict, missing evidence, or a self-clear-with-holds."""


# --- authoritative licence evidence -------------------------------------------------------------
# One record per dependency this skill can touch. `authoritative_license` and `commercial_status`
# are read from the dependency's OWN licence page (`evidence_url`) -- not from a GitHub classifier or
# a copied claim. `license_conflict_tokens` / `commercial_conflict_tokens` are lowercase substrings
# that, if they appear on a line naming the dependency, contradict the authoritative evidence and are
# therefore a conflict the gate must fail on. ADFR was independently re-verified for Review Stop 4A.
def authoritative_table() -> list[dict]:
    return [
        {
            "name": "ADFR prepare_receptor", "match": ["adfr", "prepare_receptor"],
            "version": "1.0 (ADFRsuite)", "role": "receptor PDBQT preparation (fallback tool)",
            "authoritative_license": "LGPL-2.0",
            "evidence_url": "https://ccsb.scripps.edu/adfr/license/",
            "evidence_note": "page states 'provided under the the LGPL2 open source license'; "
                             "independently re-verified for Review Stop 4A (was wrongly 'Apache 2.0')",
            "commercial_status": "permitted",
            "obligation": "LGPL: use as an unmodified standalone CLI tool is unrestricted; "
                          "source availability applies only to modifications of LGPL-covered code",
            "license_conflict_tokens": ["apache"],
            "authoritative_license_tokens": ["lgpl", "lgpl2", "lgpl-2.0", "lgpl-2.1", "lgpl v2"],
            "commercial_conflict_tokens": [],
        },
        {
            "name": "Open Babel", "match": ["open babel", "openbabel", "obabel"],
            "version": "3.1.0", "role": "receptor PDBQT conversion (last-resort fallback; avoided)",
            "authoritative_license": "GPL-2.0",
            "evidence_url": "https://github.com/openbabel/openbabel/blob/master/COPYING",
            "evidence_note": "GPL-2.0 is copyleft: a source-distribution obligation on derivative "
                             "works, NOT a commercial-use prohibition",
            "commercial_status": "permitted_with_copyleft_obligation",
            "obligation": "GPL-2.0 copyleft: derivative works must be distributable under GPL-2.0. "
                          "Skill avoids Open Babel by default to sidestep this obligation (Meeko/ADFR).",
            "license_conflict_tokens": [],
            # A commercial-PROHIBITION claim about Open Babel is the finding-4 error:
            "commercial_conflict_tokens": ["prohibited", "excluded", "restricted"],
        },
        {"name": "RDKit", "match": ["rdkit"], "version": "2025.03.5",
         "role": "cheminformatics: fingerprints, clustering, MCS, R-groups, rendering",
         "authoritative_license": "BSD-3-Clause",
         "evidence_url": "https://github.com/rdkit/rdkit/blob/master/license.txt",
         "commercial_status": "permitted", "obligation": "attribution",
         "license_conflict_tokens": [], "commercial_conflict_tokens": []},
        {"name": "AutoDock Vina", "match": ["autodock vina", "vina"], "version": "1.2.5",
         "role": "molecular docking",
         "authoritative_license": "Apache-2.0",
         "evidence_url": "https://github.com/ccsb-scripps/AutoDock-Vina/blob/develop/LICENSE",
         "commercial_status": "permitted", "obligation": "attribution",
         "license_conflict_tokens": [], "commercial_conflict_tokens": []},
        {"name": "Meeko", "match": ["meeko", "mk_prepare_receptor"], "version": "0.7.1",
         "role": "receptor + ligand PDBQT preparation",
         "authoritative_license": "LGPL-2.1",
         "evidence_url": "https://github.com/forlilab/Meeko/blob/develop/LICENSE",
         "commercial_status": "permitted",
         "obligation": "LGPL: commercial use permitted when used as an unmodified library/tool",
         "license_conflict_tokens": [], "commercial_conflict_tokens": []},
        {"name": "gemmi", "match": ["gemmi"], "version": "0.7.5",
         "role": "PDB parsing (required by Meeko)",
         "authoritative_license": "MPL-2.0",
         "evidence_url": "https://github.com/project-gemmi/gemmi/blob/master/LICENSE.txt",
         "commercial_status": "permitted", "obligation": "MPL file-level copyleft; commercial use OK",
         "license_conflict_tokens": [], "commercial_conflict_tokens": []},
        {"name": "ChEMBL", "match": ["chembl"], "version": "ChEMBL_37 (runtime)",
         "role": "bioactivity + target/molecule metadata (all input data)",
         "authoritative_license": "CC BY-SA 3.0",
         "evidence_url": "https://chembl.gitbook.io/chembl-interface-documentation/about",
         "commercial_status": "permitted_with_sharealike_obligation",
         "obligation": "CC BY-SA 3.0: attribution + SHARE-ALIKE -- derived datasets (hits CSV, "
                       "docking results, variant tables) must be redistributed under CC BY-SA 3.0. "
                       "Whether this is acceptable for the intended commercial use is a HOLD.",
         "license_conflict_tokens": [], "commercial_conflict_tokens": []},
        {"name": "RCSB PDB", "match": ["rcsb", "pdb"], "version": "runtime",
         "role": "protein structures for docking",
         "authoritative_license": "CC0-1.0",
         "evidence_url": "https://www.rcsb.org/pages/usage-policy",
         "commercial_status": "permitted", "obligation": "none (citation appreciated)",
         "license_conflict_tokens": [], "commercial_conflict_tokens": []},
        {"name": "reportlab", "match": ["reportlab"], "version": "4.4.3",
         "role": "PDF rendering (delegated report)",
         "authoritative_license": "BSD-3-Clause",
         "evidence_url": "https://www.reportlab.com/software/opensource/",
         "commercial_status": "permitted", "obligation": "attribution",
         "license_conflict_tokens": [], "commercial_conflict_tokens": []},
        {"name": "pypdf", "match": ["pypdf"], "version": "6.14.2", "role": "PDF text extraction (gate)",
         "authoritative_license": "BSD-3-Clause",
         "evidence_url": "https://github.com/py-pdf/pypdf/blob/main/LICENSE",
         "commercial_status": "permitted", "obligation": "attribution",
         "license_conflict_tokens": [], "commercial_conflict_tokens": []},
        {"name": "pandas", "match": ["pandas"], "version": "2.3.1", "role": "tabular data handling",
         "authoritative_license": "BSD-3-Clause",
         "evidence_url": "https://github.com/pandas-dev/pandas/blob/main/LICENSE",
         "commercial_status": "permitted", "obligation": "attribution",
         "license_conflict_tokens": [], "commercial_conflict_tokens": []},
        {"name": "numpy", "match": ["numpy"], "version": "2.1.0", "role": "numerics",
         "authoritative_license": "BSD-3-Clause",
         "evidence_url": "https://github.com/numpy/numpy/blob/main/LICENSE.txt",
         "commercial_status": "permitted", "obligation": "attribution",
         "license_conflict_tokens": [], "commercial_conflict_tokens": []},
        {"name": "scipy", "match": ["scipy"], "version": "1.15.0", "role": "Pearson correlation, stats",
         "authoritative_license": "BSD-3-Clause",
         "evidence_url": "https://github.com/scipy/scipy/blob/main/LICENSE.txt",
         "commercial_status": "permitted", "obligation": "attribution",
         "license_conflict_tokens": [], "commercial_conflict_tokens": []},
        {"name": "matplotlib", "match": ["matplotlib"], "version": "3.10.5", "role": "figures",
         "authoritative_license": "PSF-based (matplotlib licence)",
         "evidence_url": "https://matplotlib.org/stable/project/license.html",
         "commercial_status": "permitted", "obligation": "attribution",
         "license_conflict_tokens": [], "commercial_conflict_tokens": []},
        {"name": "seaborn", "match": ["seaborn"], "version": "0.13.2", "role": "figures",
         "authoritative_license": "BSD-3-Clause",
         "evidence_url": "https://github.com/mwaskom/seaborn/blob/master/LICENSE.md",
         "commercial_status": "permitted", "obligation": "attribution",
         "license_conflict_tokens": [], "commercial_conflict_tokens": []},
    ]


# --- package scan -------------------------------------------------------------------------------
_TEXT_SUFFIXES = {".md", ".py", ".yaml", ".yml", ".json", ".txt"}


def _package_text_files(pkg_dir: pathlib.Path) -> dict[str, str]:
    out = {}
    for p in sorted(pkg_dir.rglob("*")):
        if p.is_file() and p.suffix.lower() in _TEXT_SUFFIXES:
            rel = p.relative_to(pkg_dir).as_posix()
            # Skip the eval fixtures (they name wrong strings on purpose) and this detector's own
            # source (its docstring/comments name the wrong claims and self-clear token by design).
            if "assets/eval" in rel or rel == "scripts/commercial_inventory.py":
                continue
            out[rel] = p.read_text(encoding="utf-8", errors="replace")
    return out


def scan_executed(pkg_dir: pathlib.Path) -> dict:
    """What the package actually imports / invokes, so 'used' is derived, not assumed."""
    scripts = pkg_dir / "scripts"
    imports: set[str] = set()
    tools: set[str] = set()
    for p in sorted(scripts.glob("*.py")) if scripts.exists() else []:
        src = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".")[0])
        except SyntaxError:
            pass
        low = src.lower()
        for tok, tool in (("\"vina\"", "vina"), ("'vina'", "vina"),
                          ("prepare_receptor", "adfr"), ("mk_prepare_receptor", "meeko"),
                          ("obabel", "obabel")):
            if tok in low:
                tools.add(tool)
    return {"imports": sorted(imports), "tools": sorted(tools)}


def _declared_version(text_by_file: dict[str, str], dep: dict) -> str | None:
    """Best-effort: pull a declared version for the dep from a DATA_SOURCES.md table row."""
    ds = text_by_file.get("DATA_SOURCES.md", "")
    for line in ds.splitlines():
        low = line.lower()
        if line.strip().startswith("|") and any(m in low for m in dep["match"]):
            m = re.search(r"\b(\d+\.\d+[\w.\-]*)\b", line)
            if m:
                return m.group(1)
    return None


def _dep_positions(line_low: str, table: list[dict]) -> list[tuple[int, str]]:
    """Sorted (position, dep_name) for every dependency-name match token found in the line.

    Word-boundary matching so a loose token never matches inside a longer word (e.g. 'pdb' must not
    match 'pdbqt', which would otherwise steal ownership of a licence token from the real dependency).
    """
    pts = []
    for d in table:
        for m in d["match"]:
            for mo in re.finditer(r"\b" + re.escape(m) + r"\b", line_low):
                pts.append((mo.start(), d["name"]))
    return sorted(pts)


def _owner_before(pos: int, dep_positions: list[tuple[int, str]]) -> str | None:
    """The dependency whose name most recently preceded ``pos`` on the line -- it 'owns' the token."""
    owner = None
    for p, name in dep_positions:
        if p <= pos:
            owner = name
        else:
            break
    return owner


# Cues that a wrong token is being CORRECTED or described historically, not asserted -- so a repair
# note ("LGPL2 ... not Apache 2.0", "corrected from the wrong 'Apache 2.0' claim") is not a conflict.
_CORRECTION_CUES = ("not ", "n't", "never", "wrong", "corrected", "correct ", "instead of",
                    "rather than", "history", "historically", "was ", "no longer", "≠", "not**")


def _is_correction(line_low: str, tpos: int, dep: dict, dep_positions, kind: str, window: int = 60) -> bool:
    """True if the token at tpos is a correction/comparison, not an asserted (mis)claim."""
    before = line_low[max(0, tpos - window):tpos]
    if any(cue in before for cue in _CORRECTION_CUES):
        return True
    # A licence conflict is suppressed if THIS dependency also owns its authoritative licence token
    # on the same line (e.g. 'ADFR ... LGPL2 ... not Apache 2.0').
    if kind == "license":
        for at in dep.get("authoritative_license_tokens", []):
            for mo in re.finditer(r"\b" + re.escape(at.lower()) + r"\b", line_low):
                if _owner_before(mo.start(), dep_positions) == dep["name"]:
                    return True
    return False


def _find_conflicts(text_by_file: dict[str, str], dep: dict, table: list[dict]) -> list[dict]:
    """Conflicts where a token contradicting the authoritative evidence is OWNED by this dependency
    and is ASSERTED (not corrected/compared).

    A licence/commercial token is attributed to the nearest dependency name that precedes it on the
    line -- so 'AutoDock Vina (Apache-2.0), Meeko or ADFR ...' does not misattribute Apache to ADFR --
    and a correction ('LGPL2 ... not Apache 2.0') is not counted as a conflict (see _is_correction).
    """
    conflicts = []
    checks = ([(t.lower(), "license") for t in dep.get("license_conflict_tokens", [])]
              + [(t.lower(), "commercial_status") for t in dep.get("commercial_conflict_tokens", [])])
    if not checks:
        return conflicts
    for rel, text in text_by_file.items():
        for i, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            if not any(m in low for m in dep["match"]):
                continue
            dep_positions = _dep_positions(low, table)
            for token, kind in checks:
                start = 0
                while True:
                    tpos = low.find(token, start)
                    if tpos == -1:
                        break
                    if (_owner_before(tpos, dep_positions) == dep["name"]
                            and not _is_correction(low, tpos, dep, dep_positions, kind)):
                        conflicts.append({"file": rel, "line": i, "kind": kind,
                                          "token": token, "snippet": line.strip()[:160]})
                        break
                    start = tpos + len(token)
    return conflicts


# The operative self-clear is the machine assertion 'license_gate: PASS/CLEAR/OK' (as in the source
# eval.yaml). Prose that merely discusses clearing is not matched, so a repair note explaining that
# the gate 'never returns commercially cleared' is not itself flagged.
_SELF_CLEAR_RE = re.compile(r"license_gate\s*:\s*[\"']?\s*(pass|clear|cleared|ok)\b", re.IGNORECASE)


def detect_self_clear(text_by_file: dict[str, str]) -> list[dict]:
    hits = []
    for rel, text in text_by_file.items():
        for i, line in enumerate(text.splitlines(), 1):
            if _SELF_CLEAR_RE.search(line):
                hits.append({"file": rel, "line": i, "snippet": line.strip()[:160]})
    return hits


def build_inventory(pkg_dir) -> dict:
    pkg_dir = pathlib.Path(pkg_dir)
    text_by_file = _package_text_files(pkg_dir)
    executed = scan_executed(pkg_dir)
    used_tokens = set(executed["imports"]) | set(executed["tools"])

    table = authoritative_table()
    deps = []
    for dep in table:
        # distribution role, derived from what the package actually executes
        is_executed = any(
            (m.replace(" ", "") in {t.replace(" ", "") for t in used_tokens})
            or (m in used_tokens) for m in dep["match"])
        if dep["name"] == "Open Babel":
            role = "excluded"          # referenced as last-resort fallback but avoided by default
        elif is_executed:
            role = "used"
        else:
            role = "declared"
        conflicts = _find_conflicts(text_by_file, dep, table)
        version = _declared_version(text_by_file, dep) or dep.get("version")
        has_evidence = bool(dep.get("evidence_url"))
        obligation = dep.get("obligation", "")
        is_hold = ("obligation" in dep["commercial_status"]) or (not has_evidence) or bool(conflicts)
        deps.append({
            "name": dep["name"],
            "role": dep["role"],
            "distribution_role": role,
            "version": version,
            "authoritative_license": dep["authoritative_license"],
            "evidence_url": dep.get("evidence_url"),
            "evidence_note": dep.get("evidence_note", ""),
            "commercial_status": dep["commercial_status"],
            "obligation": obligation,
            "conflicts": conflicts,
            "status": ("conflict" if conflicts else ("unknown" if not has_evidence else "consistent")),
            "hold": is_hold,
        })

    deps.sort(key=lambda d: d["name"].lower())
    self_clear = detect_self_clear(text_by_file)
    holds = sorted({d["name"] for d in deps if d["hold"]})
    conflict_deps = sorted({d["name"] for d in deps if d["conflicts"]})
    return {
        "schema": "commercial-inventory-v1",
        "executed": executed,
        "dependencies": deps,
        "conflicts": conflict_deps,
        "holds": holds,
        "self_clear_detected": self_clear,
        # NEVER "cleared": this skill carries CC BY-SA 3.0 share-alike + open human-decision holds.
        "overall_status": "HOLD",
        "overall_reason": "ChEMBL CC BY-SA 3.0 share-alike obligation on derived datasets and open "
                          "human-decision commercial findings (possible hard veto / prohibition) "
                          "require Phylo/legal review; not auto-clearable.",
    }


def assert_commercial_inventory_consistent(inventory: dict) -> dict:
    """Fail closed on a licence conflict, a material dep with no evidence, or a self-clear-with-holds.

    Passing means: the inventory is internally consistent and evidence-bound AND the package retains
    its holds rather than self-clearing. It does NOT mean commercially cleared -- overall_status
    stays HOLD.
    """
    problems = []
    for d in inventory.get("dependencies", []):
        if d["conflicts"]:
            for c in d["conflicts"]:
                problems.append(
                    f"licence conflict for {d['name']}: '{c['token']}' at {c['file']}:{c['line']} "
                    f"contradicts authoritative {d['authoritative_license']} "
                    f"({d['evidence_url']}) -- '{c['snippet']}'")
        if d["distribution_role"] in {"used", "included"} and not d.get("evidence_url"):
            problems.append(f"material dependency {d['name']} has no authoritative evidence URL "
                            "-- unknown licence must stay an explicit hold, not be inferred.")
    if inventory.get("self_clear_detected") and inventory.get("holds"):
        locs = ", ".join(f"{h['file']}:{h['line']}" for h in inventory["self_clear_detected"])
        problems.append(
            f"commercial status self-cleared ({locs}) while holds remain open "
            f"({', '.join(inventory['holds'])}). Retain the hold; route to Phylo/legal review.")
    if problems:
        raise CommercialInventoryError(
            "commercial-use inventory is not consistent:\n  - " + "\n  - ".join(problems))
    return {"n_dependencies": len(inventory.get("dependencies", [])),
            "overall_status": inventory.get("overall_status"),
            "holds": inventory.get("holds", [])}


def render_markdown(inventory: dict) -> str:
    lines = ["# Dependency & commercial-use inventory (derived)", "",
             f"**Overall status: {inventory['overall_status']}** — {inventory['overall_reason']}", "",
             "Derived from declared dependencies and actually-executed imports/tools. Each licence is "
             "bound to the dependency's own licence page (evidence URL). Holds are retained, not "
             "self-cleared.", "",
             f"Executed imports: {', '.join(inventory['executed']['imports'])}", "",
             f"Executed tools: {', '.join(inventory['executed']['tools']) or '(none detected)'}", "",
             "| Dependency | Role | Distribution | Version | Licence | Commercial status | Obligation | Evidence | Status |",
             "|---|---|---|---|---|---|---|---|---|"]
    for d in inventory["dependencies"]:
        lines.append("| {name} | {role} | {dist} | {ver} | {lic} | {com} | {ob} | {url} | {st} |".format(
            name=d["name"], role=d["role"], dist=d["distribution_role"], ver=d["version"],
            lic=d["authoritative_license"], com=d["commercial_status"],
            ob=(d["obligation"][:90] + "…") if len(d["obligation"]) > 90 else d["obligation"],
            url=d["evidence_url"] or "(none — HOLD)", st=("CONFLICT" if d["conflicts"] else d["status"])))
    if inventory["holds"]:
        lines += ["", "## Holds (require Phylo/legal review)", ""]
        lines += [f"- {h}" for h in inventory["holds"]]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Derive the dependency/commercial-use inventory")
    ap.add_argument("--package", default=str(pathlib.Path(__file__).resolve().parent.parent))
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--out-md", default=None)
    ap.add_argument("--assert", dest="do_assert", action="store_true",
                    help="Run the fail-closed consistency gate and exit non-zero on a conflict.")
    args = ap.parse_args()

    inv = build_inventory(args.package)
    if args.out_json:
        pathlib.Path(args.out_json).write_text(
            json.dumps(inv, indent=2, sort_keys=True), encoding="utf-8")
    if args.out_md:
        pathlib.Path(args.out_md).write_text(render_markdown(inv), encoding="utf-8")

    print(f"Overall status: {inv['overall_status']}")
    print(f"Conflicts: {inv['conflicts'] or 'none'}")
    print(f"Holds: {inv['holds']}")
    if args.do_assert:
        summary = assert_commercial_inventory_consistent(inv)
        print(f"Consistency gate PASSED (holds retained): {summary}")


if __name__ == "__main__":
    main()
