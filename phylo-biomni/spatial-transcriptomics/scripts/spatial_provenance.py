"""
============================================================================
PROVENANCE EXPORT (derived, cross-checked, platform-honest)
============================================================================

Builds ``provenance.json`` from the EXACT runtime parameter object used by the
platform workflow — never from independent hard-coded defaults — and refuses to
emit when the reported QC threshold disagrees across the execution log, the
provenance and the facts payload (Finding 3).

It also records, for the receipt's integrity gates:
  - ``platform`` (top level) so a source assertion can be evaluated platform-
    conditionally and a Xenium-only field is never injected into a Visium run
    (Finding 1);
  - ``code_provenance`` — the SHA-256 of every packaged module actually imported,
    so the receipt binds to the executed packaged script hashes (Finding 2);
  - ``executed_dependencies`` — the third-party modules actually imported, so the
    material dependency / commercial-use receipt is generated from executed
    imports (Finding 6).

Dependency-light: standard library only. The scientific stack is imported by the
branch workflows, not here.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional

PROVENANCE_SCHEMA = "spatial-transcriptomics-provenance/1"

# Third-party top-level modules that are material to scientific computation and data-bearing figures.
# PDF presentation is conditional and recorded separately by finalize_report.py. The dependency gate
# contract; anything imported outside this set (stdlib, the platform tools) is not a material
# dependency. matplotlib is material to the scientific visual layer and is imported during prepare.
MATERIAL_MODULES: tuple[str, ...] = (
    "numpy", "pandas", "scipy", "sklearn", "anndata", "scanpy", "squidpy",
    "matplotlib", "tifffile",
)

# Packaged modules whose executed hashes are bound into the receipt. The entrypoint is included so a
# substitution of the orchestrator itself is caught alongside the branch/QC modules. The scientific
# figure generator is included; the conditional PDF renderer/provider is bound in render provenance.
PACKAGED_SCRIPTS: tuple[str, ...] = (
    "scripts/run_spatial.py",
    "scripts/platform_router.py",
    "scripts/spatial_input.py",
    "scripts/spatial_common.py",
    "scripts/spatial_workflow.py",
    "scripts/xenium_workflow.py",
    "scripts/shared_downstream.py",
    "scripts/spatial_provenance.py",
    "scripts/outputs_manifest.py",
    "scripts/export_results.py",
    "scripts/spatial_figures.py",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_root() -> Path:
    """The installed/authoring package root (this module lives in ``<root>/scripts``)."""
    return Path(__file__).resolve().parent.parent


def capture_code_provenance(scripts: Iterable[str] = PACKAGED_SCRIPTS,
                            root: Optional[Path] = None) -> dict:
    """Map each packaged script (that exists) to its on-disk SHA-256.

    Recorded from the SAME files the run imported, so ``report_qc.assert_code_provenance`` can match
    them to the packaged files and detect unrecorded replacement code.
    """
    base = root or package_root()
    out: dict[str, str] = {}
    for rel in scripts:
        p = base / rel
        if p.is_file():
            out[rel] = _sha256_file(p)
    return out


def capture_executed_dependencies(candidates: Iterable[str] = MATERIAL_MODULES) -> list[str]:
    """Return the subset of material modules that are actually imported in this process."""
    loaded = set(sys.modules)
    return sorted(mod for mod in candidates if mod in loaded)


def tool_versions(modules: Iterable[str] = MATERIAL_MODULES) -> dict:
    """Best-effort version capture for provenance (never raises)."""
    out: dict[str, Optional[str]] = {}
    for mod in modules:
        try:
            out[mod] = sys.modules[mod].__version__ if mod in sys.modules \
                else __import__(mod).__version__
        except Exception:
            out[mod] = None
    return out


def build_provenance(
    platform: str,
    *,
    thresholds: Mapping,
    parameters: Optional[Mapping] = None,
    xenium: Optional[Mapping] = None,
    svg_significance: Optional[Mapping] = None,
    extra: Optional[Mapping] = None,
    record_code_provenance: bool = True,
    record_executed_dependencies: bool = True,
) -> dict:
    """Assemble a provenance dict from the run's own parameter objects.

    ``thresholds`` MUST be the exact applied QC parameter object (e.g. the Visium
    ``adata.uns['spatial_analysis_params']`` slice, or the Xenium QC summary). No default value is
    substituted here — a caller that passes an empty mapping gets an empty ``thresholds`` block, not a
    fabricated 200. The Xenium-only ``xenium`` block (carrying ``min_transcript_qv``) is written ONLY
    for a Xenium run, so a Visium provenance never carries a Xenium transcript-quality field.
    """
    if not isinstance(thresholds, Mapping):
        raise TypeError("thresholds must be the exact applied runtime parameter object")
    payload: dict = {
        "schema": PROVENANCE_SCHEMA,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platform": platform,
        "thresholds": {k: thresholds[k] for k in thresholds},
    }
    if parameters is not None:
        payload["parameters"] = dict(parameters)
    if platform == "Xenium":
        if xenium is None:
            raise ValueError("a Xenium run must supply the xenium provenance block")
        payload["xenium"] = dict(xenium)
    elif xenium is not None:
        raise ValueError(
            f"refusing to write a xenium provenance block for a {platform!r} run "
            "(Xenium-only fields must not enter a non-Xenium provenance)"
        )
    if svg_significance is not None:
        payload["svg_significance"] = dict(svg_significance)
    if extra:
        payload.update(dict(extra))
    payload["tool_versions"] = tool_versions()
    if record_executed_dependencies:
        payload["executed_dependencies"] = capture_executed_dependencies()
    if record_code_provenance:
        payload["code_provenance"] = capture_code_provenance()
    return payload


def write_provenance(path, provenance: Mapping) -> str:
    """Serialize a provenance dict deterministically to ``path``."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(dict(provenance), indent=2, sort_keys=True), encoding="utf-8")
    return str(p)


def assert_threshold_consistency(execution_params: Mapping,
                                 provenance: Mapping,
                                 facts: Optional[Mapping] = None,
                                 keys: Iterable[str] = ("min_genes",)) -> dict:
    """Fail if a reported QC threshold disagrees across execution log / provenance / facts.

    This is the guard the source lacked: the candidate's provenance recorded ``min_genes=200`` while
    the applied value was 18. A reader reproducing from provenance would filter out every spot. Here a
    single applied value must render identically everywhere, or export aborts.
    """
    prov_thr = provenance.get("thresholds", {}) if isinstance(provenance, Mapping) else {}
    facts_thr = {}
    if isinstance(facts, Mapping):
        facts_thr = facts.get("thresholds", {}) or facts.get("qc", {}).get("thresholds", {}) or {}
    checked = {}
    for key in keys:
        seen = {}
        if key in execution_params:
            seen["execution"] = execution_params[key]
        if key in prov_thr:
            seen["provenance"] = prov_thr[key]
        if key in facts_thr:
            seen["facts"] = facts_thr[key]
        if "execution" not in seen:
            raise ValueError(f"threshold {key!r} is missing from the execution parameter object")
        distinct = {json.dumps(v, sort_keys=True) for v in seen.values()}
        if len(distinct) > 1:
            raise ValueError(
                f"threshold {key!r} disagrees across sources {seen}; aborting export "
                "(provenance must reflect the exact applied value)"
            )
        checked[key] = seen["execution"]
    return checked
