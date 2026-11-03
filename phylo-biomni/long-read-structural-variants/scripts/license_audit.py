#!/usr/bin/env python3
"""Dependency-license commercial-use gate for long-read-structural-variants.

WHY THIS EXISTS (repair of the numpy commercial-evidence finding). The earlier commercial-use check
resolved a package license from only the legacy ``License:`` core-metadata field plus OSI trove
classifiers. Packages that adopt PEP 639 (numpy 2.x and a growing share of the scientific stack)
drop both and declare the license through ``License-Expression`` plus bundled license files, so the
old check printed ``numpy: License=N/A | Classifiers=[]`` and the commercial-use status fell back to
a self-declared contract row with no inspected evidence.

This module resolves each declared dependency's license from the *installed distribution metadata*
across ALL authoritative sources, in priority order:

    1. PEP 639 ``License-Expression`` (canonical SPDX)
    2. OSI trove ``Classifier: License :: ...`` (specific licenses only)
    3. the ``License`` core-metadata field (short SPDX token, else full-text detection)
    4. the bundled dist-info license file(s) (``LICENSE*`` / ``COPYING*`` / ``licenses/*``)

For every dependency it records the installed version, the resolution source, and the SHA-256 of the
bundled license file (the authoritative license-source identity + package fingerprint), classifies
commercial use (permissive / copyleft / noncommercial / unknown), and HARD-FAILS the run when the
runtime/source evidence is missing or conflicts with the declared contract. It writes a runtime
dependency receipt (JSON) that a run cites instead of a bare contract declaration.

Stdlib only. Import from run_sv.py or run standalone:

    python scripts/license_audit.py --contract skill_contract.json \
        --out /mnt/results/sv/dependency_licenses.json --required numpy,matplotlib,pysam,reportlab
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
from datetime import datetime, timezone

import importlib.metadata as ilmd

DEPENDENCY_RECEIPT_SCHEMA = "phylo-dependency-license/1"
#: Python libraries the pipeline actually imports; their commercial-use evidence must be inspected,
#: never taken from a self-declared contract row. CLI callers are recorded when installed but are not
#: hard-required because their presence is environment-dependent.
DEFAULT_REQUIRED = ("numpy", "matplotlib", "pysam", "reportlab")
AUTHORITATIVE_SOURCE = "https://spdx.org/licenses/"


class GateFailure(AssertionError):
    """Raised when the commercial-use gate cannot be satisfied from inspected evidence."""


# --- license families -> commercial-use class ----------------------------------------------------
# Commercial *use* is permitted for permissive and copyleft alike; copyleft additionally carries
# source-disclosure obligations when a derivative is *distributed*. Only noncommercial/unknown break
# the gate, because those cannot support a declared "allowed" status from inspected evidence.
_PERMISSIVE = {
    "MIT", "MIT-0", "BSD-2-CLAUSE", "BSD-3-CLAUSE", "BSD-3-CLAUSE-CLEAR",
    "BSD-3-CLAUSE-ATTRIBUTION", "BSD", "0BSD", "ISC", "APACHE-2.0", "ZLIB",
    "PSF-2.0", "PYTHON-2.0", "HPND", "MIT-CMU", "CC0-1.0", "UNLICENSE", "MATPLOTLIB",
    "MPL-2.0", "BSL-1.0",
}
_COPYLEFT = {
    "GPL-2.0", "GPL-3.0", "LGPL-2.1", "LGPL-3.0", "AGPL-3.0",
    "GPL-3.0-WITH-GCC-EXCEPTION", "GFDL-1.3",
}
_NONCOMMERCIAL = {
    "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "CC-BY-NC-ND-4.0", "PROPRIETARY", "COMMERCIAL",
    "NONCOMMERCIAL", "LICENSEREF-PROPRIETARY",
}

# Specific OSI trove classifiers -> canonical SPDX id. A generic "License :: OSI Approved" (no named
# license) maps to None so resolution falls through to the License field / license file.
_CLASSIFIER_SPDX = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: MIT No Attribution License (MIT-0)": "MIT-0",
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: zlib/libpng License": "Zlib",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: GNU General Public License v2 (GPLv2)": "GPL-2.0",
    "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)": "GPL-2.0",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)": "GPL-3.0",
    "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)": "GPL-3.0",
    "License :: OSI Approved :: GNU Lesser General Public License v2 (LGPLv2)": "LGPL-2.1",
    "License :: OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.1",
    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0",
    "License :: OSI Approved :: GNU Affero General Public License v3": "AGPL-3.0",
    "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)": "AGPL-3.0",
    "License :: CC0 1.0 Universal (CC0 1.0) Public Domain Dedication": "CC0-1.0",
    "License :: Free for non-commercial use": "NonCommercial",
    "License :: Other/Proprietary License": "Proprietary",
}

# Short-token aliases seen in the ``License`` core-metadata field.
_TOKEN_ALIASES = {
    "MIT": "MIT", "MIT LICENSE": "MIT", "EXPAT": "MIT",
    "BSD": "BSD-3-Clause", "BSD LICENSE": "BSD-3-Clause", "BSD-3": "BSD-3-Clause",
    "BSD 3-CLAUSE": "BSD-3-Clause", "BSD-3-CLAUSE": "BSD-3-Clause", "NEW BSD": "BSD-3-Clause",
    "BSD-2-CLAUSE": "BSD-2-Clause", "BSD 2-CLAUSE": "BSD-2-Clause", "SIMPLIFIED BSD": "BSD-2-Clause",
    "BSD-3-CLAUSE-CLEAR": "BSD-3-Clause-Clear",
    "APACHE": "Apache-2.0", "APACHE 2.0": "Apache-2.0", "APACHE-2.0": "Apache-2.0",
    "APACHE SOFTWARE LICENSE": "Apache-2.0", "APACHE LICENSE 2.0": "Apache-2.0",
    "ISC": "ISC", "ZLIB": "Zlib", "CC0": "CC0-1.0", "CC0-1.0": "CC0-1.0",
    "PSF": "PSF-2.0", "PSF-2.0": "PSF-2.0", "PYTHON SOFTWARE FOUNDATION LICENSE": "PSF-2.0",
    "MPL-2.0": "MPL-2.0", "MPL 2.0": "MPL-2.0",
    "GPL": "GPL-3.0", "GPLV3": "GPL-3.0", "GPL-3.0": "GPL-3.0", "GPL V3": "GPL-3.0",
    "GPLV2": "GPL-2.0", "GPL-2.0": "GPL-2.0",
    "LGPL": "LGPL-3.0", "LGPL-3.0": "LGPL-3.0", "LGPL-2.1": "LGPL-2.1",
    "AGPL": "AGPL-3.0", "AGPL-3.0": "AGPL-3.0",
    "PROPRIETARY": "Proprietary",
}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _family(token: str) -> str:
    """Commercial-use family for a single SPDX-ish token."""
    if not token:
        return "unknown"
    base = re.sub(r"\s+", "", token.strip().upper())
    base = base.replace("-ONLY", "").replace("-OR-LATER", "").rstrip("+")
    if base in _PERMISSIVE:
        return "permissive"
    if base in _COPYLEFT:
        return "copyleft"
    if base in _NONCOMMERCIAL:
        return "noncommercial"
    if base.startswith(("BSD", "MIT", "APACHE", "PSF", "PYTHON", "CC0")) or base in ("ISC", "ZLIB", "MATPLOTLIB", "0BSD", "BSL-1.0"):
        return "permissive"
    if base.startswith(("AGPL", "LGPL", "GPL", "GFDL")):
        return "copyleft"
    if "NONCOMMERCIAL" in base or "-NC" in token.upper() or base == "PROPRIETARY":
        return "noncommercial"
    return "unknown"


def commercial_class(spdx: str | None) -> str:
    """permissive | copyleft | noncommercial | unknown for a license id or SPDX expression."""
    if not spdx:
        return "unknown"
    parts = [p for p in re.split(r"\bOR\b|\bAND\b|\bWITH\b|/", spdx, flags=re.I) if p.strip()]
    classes = {_family(p) for p in parts} or {"unknown"}
    if re.search(r"\bOR\b", spdx, flags=re.I):  # any allowed alternative suffices
        for pref in ("permissive", "copyleft"):
            if pref in classes:
                return pref
        return "noncommercial" if "noncommercial" in classes else "unknown"
    for restrictive in ("noncommercial", "unknown", "copyleft", "permissive"):  # most restrictive governs
        if restrictive in classes:
            return restrictive
    return "unknown"


def _canonical_spdx(expr: str) -> str:
    expr = (expr or "").strip()
    if not expr:
        return ""
    alias = _TOKEN_ALIASES.get(re.sub(r"\s+", " ", expr.upper()))
    return alias or expr


def _detect_spdx_from_text(text: str) -> str | None:
    """Best-effort SPDX id from full license text (License field body or a license file)."""
    t = re.sub(r"\s+", " ", (text or "")).strip().lower()
    if not t:
        return None
    if "gnu affero general public license" in t:
        return "AGPL-3.0"
    if "gnu lesser general public license" in t:
        return "LGPL-3.0" if "version 3" in t else "LGPL-2.1"
    if "gnu general public license" in t:
        if "version 3" in t:
            return "GPL-3.0"
        if "version 2" in t:
            return "GPL-2.0"
        return "GPL-3.0"
    if "apache license" in t and "version 2.0" in t:
        return "Apache-2.0"
    if "mozilla public license" in t and ("version 2.0" in t or "v. 2.0" in t):
        return "MPL-2.0"
    if "matplotlib" in t and ("license agreement" in t or " mdt " in t):
        return "Matplotlib"
    if "python software foundation" in t and "license agreement" in t:
        return "PSF-2.0"
    if "permission is hereby granted, free of charge" in t and "the software is provided" in t:
        return "MIT"
    if "redistribution and use in source and binary forms" in t:
        return "BSD-3-Clause" if "neither the name" in t else "BSD-2-Clause"
    if "isc license" in t or ("permission to use, copy, modify" in t and "provided that the above copyright" in t):
        return "ISC"
    if ("noncommercial" in t or "non-commercial" in t) and "commercial" in t:
        return "NonCommercial"
    return None


def _license_from_field(value: str | None) -> str | None:
    """Resolve the ``License`` core-metadata field: short token -> alias; long body -> text detect."""
    if not value:
        return None
    stripped = value.strip()
    if len(stripped) <= 60 and "\n" not in stripped:
        alias = _TOKEN_ALIASES.get(re.sub(r"\s+", " ", stripped.upper()))
        if alias:
            return alias
        if re.fullmatch(r"[A-Za-z0-9.\-+]{2,}", stripped) and _family(stripped) != "unknown":
            return stripped
    return _detect_spdx_from_text(stripped)


def _find_license_files(dist: ilmd.Distribution) -> list[dict]:
    """Bundled license files inside the ``*.dist-info`` directory (authoritative source identity)."""
    out: list[dict] = []
    root_raw = getattr(dist, "_path", None)
    if not root_raw:
        return out
    root = pathlib.Path(str(root_raw))
    if not root.is_dir():
        return out
    candidates: list[pathlib.Path] = []
    for entry in sorted(root.rglob("*")):
        if not entry.is_file():
            continue
        base = entry.name.lower()
        rel = str(entry.relative_to(root)).lower()
        if (base.startswith("license") or base.startswith("copying")
                or "licenses/" in rel or base in ("notice", "authors")):
            candidates.append(entry)
    for entry in candidates[:8]:
        try:
            data = entry.read_bytes()
        except OSError:
            continue
        out.append({
            "path": str(entry),
            "sha256": _sha256_bytes(data),
            "bytes": len(data),
            "detected_spdx": _detect_spdx_from_text(data.decode("utf-8", "replace")),
        })
    return out


def _get_distribution(name_or_dist):
    if isinstance(name_or_dist, ilmd.Distribution):
        return name_or_dist
    return ilmd.distribution(str(name_or_dist))


def resolve_installed_license(name_or_dist) -> dict:
    """Resolve a dependency's license from installed distribution metadata across all sources."""
    try:
        dist = _get_distribution(name_or_dist)
    except ilmd.PackageNotFoundError:
        return {
            "name": str(name_or_dist), "installed": False, "installed_version": None,
            "resolved": False, "resolution_source": None, "resolved_license": None,
            "commercial_class": "unknown", "dist_info_path": None,
            "evidence": {"error": "distribution not installed in this environment"},
        }
    meta = dist.metadata
    try:
        version = dist.version
    except Exception:  # noqa: BLE001
        version = meta.get("Version")
    name = meta.get("Name") or str(name_or_dist)
    dist_info_path = str(getattr(dist, "_path", "") or "")

    lic_expr = (meta.get("License-Expression") or "").strip()
    classifiers = [c for c in (meta.get_all("Classifier") or []) if str(c).startswith("License")]
    lic_field = meta.get("License")
    license_files = _find_license_files(dist)

    resolved_license, source = None, None
    if lic_expr:
        resolved_license, source = _canonical_spdx(lic_expr), "license-expression"
    if resolved_license is None:
        for c in classifiers:
            spdx = _CLASSIFIER_SPDX.get(c.strip())
            if spdx:
                resolved_license, source = spdx, "classifier"
                break
    if resolved_license is None and lic_field:
        got = _license_from_field(lic_field)
        if got:
            resolved_license, source = got, "license-field"
    if resolved_license is None:
        for lf in license_files:
            if lf.get("detected_spdx"):
                resolved_license, source = lf["detected_spdx"], "license-file"
                break

    resolved = resolved_license is not None
    return {
        "name": name, "installed": True, "installed_version": version,
        "resolved": resolved, "resolution_source": source,
        "resolved_license": resolved_license,
        "commercial_class": commercial_class(resolved_license) if resolved else "unknown",
        "dist_info_path": dist_info_path,
        "evidence": {
            "license_expression": lic_expr or None,
            "license_classifiers": classifiers,
            "license_field_present": lic_field is not None,
            "license_field_sha256": _sha256_text(lic_field) if lic_field else None,
            "license_files": license_files,
        },
    }


# --- contract wiring -----------------------------------------------------------------------------
_DIST_NAME_OVERRIDES = {
    "pysam (htslib/bcftools/samtools)": "pysam",
    "minimap2 (mappy binding)": "mappy",
    "sniffles2": "sniffles",
    "cutesv": "cuteSV",
    "truvari": "truvari",
    "numpy": "numpy",
    "matplotlib": "matplotlib",
    "reportlab": "reportlab",
}


def _dist_name_for(row_name: str) -> str:
    key = re.sub(r"\s+", " ", str(row_name).strip()).lower()
    for k, v in _DIST_NAME_OVERRIDES.items():
        if k.lower() == key:
            return v
    return re.split(r"[ (]", str(row_name).strip())[0]


def _software_rows(contract: dict) -> list[dict]:
    ri = contract.get("runtime_instructions", {}) if isinstance(contract, dict) else {}
    rows = ri.get("data_sources") or contract.get("data_sources") or []
    return [r for r in rows if isinstance(r, dict)
            and str(r.get("type", "")).startswith("software") and r.get("included") is True]


def audit_dependency(row: dict) -> dict:
    dist_name = _dist_name_for(row.get("name", ""))
    res = resolve_installed_license(dist_name)
    declared_status = row.get("commercial_status")
    declared_version = row.get("version")
    if not res["installed"]:
        status, reason = "not_installed", f"{dist_name} not installed in this environment"
    elif not res["resolved"]:
        status, reason = "missing_evidence", "license not resolvable from any inspected metadata source"
    else:
        cc = res["commercial_class"]
        if cc in ("noncommercial", "unknown"):
            status = "conflict"
            reason = (f"resolved license {res['resolved_license']!r} is {cc}; cannot support declared "
                      f"commercial_status={declared_status!r}")
        elif declared_status != "allowed":
            status = "conflict"
            reason = (f"declared commercial_status={declared_status!r} but resolved "
                      f"{res['resolved_license']!r} ({cc})")
        else:
            status = "verified"
            reason = f"resolved {res['resolved_license']!r} ({cc}) via {res['resolution_source']}"
    entry = {
        "declared_name": row.get("name"), "dist_name": dist_name,
        "declared_license": row.get("license"), "declared_commercial_status": declared_status,
        "declared_version": declared_version, "status": status, "reason": reason,
    }
    entry.update({k: res[k] for k in (
        "installed", "installed_version", "resolved", "resolution_source",
        "resolved_license", "commercial_class", "dist_info_path", "evidence")})
    inst = res.get("installed_version")
    entry["version_match"] = (declared_version == inst) if (declared_version and inst) else None
    return entry


def audit_dependencies(contract, required=DEFAULT_REQUIRED) -> dict:
    if not isinstance(contract, dict):
        contract = json.loads(pathlib.Path(contract).read_text(encoding="utf-8"))
    entries = [audit_dependency(r) for r in _software_rows(contract)]
    required_l = {str(r).lower() for r in required}
    violations = [
        {"dist_name": e["dist_name"], "status": e["status"], "reason": e["reason"]}
        for e in entries
        if e["dist_name"].lower() in required_l and e["status"] in ("missing_evidence", "conflict", "not_installed")
    ]
    return {
        "schema": DEPENDENCY_RECEIPT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "resolver": "scripts/license_audit.py",
        "authoritative_source": AUTHORITATIVE_SOURCE,
        "required": sorted(required_l),
        "commercial_evidence_violations": len(violations),
        "violations": violations,
        "dependencies": entries,
    }


def audit_and_gate(contract, out_path, required=DEFAULT_REQUIRED, strict=True) -> dict:
    """Audit declared dependencies, write the runtime dependency receipt, fail closed on violations."""
    receipt = audit_dependencies(contract, required=required)
    outp = pathlib.Path(out_path)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    if strict and receipt["commercial_evidence_violations"] > 0:
        detail = "; ".join(f"{v['dist_name']}:{v['status']}" for v in receipt["violations"])
        raise GateFailure(
            f"commercial-use gate failed: {receipt['commercial_evidence_violations']} dependency "
            f"license-evidence violation(s) [{detail}]. A declared commercial status must rest on "
            f"inspected installed metadata, not a self-declared contract row. See {outp}.")
    return receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Dependency-license commercial-use gate.")
    ap.add_argument("--contract", default="skill_contract.json")
    ap.add_argument("--out", default="/mnt/results/sv/dependency_licenses.json")
    ap.add_argument("--required", default=",".join(DEFAULT_REQUIRED))
    ap.add_argument("--no-strict", action="store_true", help="record violations without raising")
    args = ap.parse_args(argv)
    required = tuple(x.strip() for x in args.required.split(",") if x.strip())
    try:
        receipt = audit_and_gate(args.contract, args.out, required=required, strict=not args.no_strict)
    except GateFailure as exc:
        print(f"COMMERCIAL GATE FAILED: {exc}", file=sys.stderr)
        return 4
    print(json.dumps({
        "commercial_evidence_violations": receipt["commercial_evidence_violations"],
        "required": receipt["required"], "out": str(args.out),
        "dependencies_audited": len(receipt["dependencies"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
