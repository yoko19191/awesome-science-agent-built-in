"""
============================================================================
RESULTS-ROOT OUTPUTS MANIFEST (deterministic two-platform layout)
============================================================================

The sample prompt asks one question of a bundle that may contain BOTH a Visium
and a Xenium dataset, yet the output contract lists a single set of artifact
names. Writing both runs' artifacts to the results root would collide; writing
them only under ``visium_run/`` and ``xenium_run/`` leaves the root-containment
gate unable to resolve them without basename guessing (Finding 5 / issue_008).

The fix is a deterministic layout plus a results-root INDEX:
  - a single-platform run writes its artifacts at the results root (as the
    clarification branches declare);
  - a two-platform run writes each run under its own namespaced subdirectory AND
    emits ``spatial_outputs_manifest.json`` at the results root that binds every
    required artifact to its explicit per-platform path.

``assert_outputs_manifest`` resolves both runs from the manifest alone — every
artifact path is explicit, so nothing downstream has to guess a basename.

Dependency-light: standard library only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence

MANIFEST_SCHEMA = "spatial-outputs-manifest/1"
MANIFEST_NAME = "spatial_outputs_manifest.json"

# The per-run data artifacts the contract requires from each platform run.
REQUIRED_ARTIFACTS: tuple[str, ...] = (
    "platform_decision.json",
    "qc.csv",
    "niches.csv",
    "spatially_variable_genes.csv",
    "differential_expression.csv",
    "cci.csv",
    "provenance.json",
    "adata_processed.h5ad",
    "facts_payload.json",
    "dependency_receipt.json",
    "report_facts.json",
    "report_spatial_transcriptomics.pdf",
)


def build_outputs_manifest(results_root,
                           platform_subdirs: Mapping[str, str],
                           required_artifacts: Sequence[str] = REQUIRED_ARTIFACTS,
                           *, require_exists: bool = True) -> dict:
    """Write ``spatial_outputs_manifest.json`` binding each required artifact to a per-platform path.

    ``platform_subdirs`` maps a platform label (e.g. ``"Visium"``) to its results-relative
    subdirectory (e.g. ``"visium_run"``; ``""`` means the results root). When ``require_exists`` is
    true the artifact file must already be present, so the manifest cannot promise an output the run
    never produced.
    """
    root = Path(results_root)
    runs: dict[str, dict] = {}
    for platform, subdir in platform_subdirs.items():
        sub = str(subdir).strip("/")
        artifacts: dict[str, str] = {}
        for name in required_artifacts:
            rel = f"{sub}/{name}" if sub else name
            target = root / rel
            if require_exists and not target.is_file():
                raise FileNotFoundError(
                    f"outputs manifest cannot bind {platform} artifact {name!r}: {target} is absent"
                )
            artifacts[name] = rel
        runs[platform] = {"subdir": sub, "artifacts": artifacts}
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "layout": "per_platform_subdirectory",
        "platforms": sorted(platform_subdirs),
        "required_artifacts": list(required_artifacts),
        "runs": runs,
    }
    out = root / MANIFEST_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def assert_outputs_manifest(manifest_path,
                            required_artifacts: Sequence[str] = REQUIRED_ARTIFACTS,
                            expected_platforms: Sequence[str] | None = None,
                            *, results_root=None, require_exists: bool = True) -> dict:
    """Resolve every required artifact for every platform run from the manifest, no basename guessing.

    Fails if the manifest is malformed, a platform is missing, an artifact is unbound, a bound path
    escapes the results root, or (when ``require_exists``) the bound file is absent.
    """
    mpath = Path(manifest_path)
    root = Path(results_root) if results_root is not None else mpath.parent
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"outputs manifest must be a {MANIFEST_SCHEMA!r} object")
    runs = manifest.get("runs")
    if not isinstance(runs, dict) or not runs:
        raise ValueError("outputs manifest has no runs")
    platforms = expected_platforms if expected_platforms is not None else list(runs)
    resolved: dict[str, dict] = {}
    for platform in platforms:
        run = runs.get(platform)
        if not isinstance(run, dict):
            raise ValueError(f"outputs manifest is missing platform run {platform!r}")
        artifacts = run.get("artifacts", {})
        run_resolved: dict[str, str] = {}
        for name in required_artifacts:
            rel = artifacts.get(name)
            if not isinstance(rel, str) or not rel.strip():
                raise ValueError(f"{platform} artifact {name!r} is not bound in the manifest")
            portable = Path(rel)
            if portable.is_absolute() or ".." in portable.parts:
                raise ValueError(f"{platform} artifact {name!r} path escapes results: {rel!r}")
            target = root / rel
            if require_exists and not target.is_file():
                raise FileNotFoundError(f"{platform} artifact {name!r} bound to absent file {target}")
            run_resolved[name] = rel
        resolved[platform] = run_resolved
    return {"platforms": list(resolved), "resolved": resolved,
            "method": "explicit per-platform artifact paths resolved from the results-root manifest"}
