"""Freeze and verify the two-stage SV science outputs."""
from __future__ import annotations

import hashlib
import json
import pathlib


CORE_OUTPUTS = (
    "sv/calls_sniffles2.norm.vcf.gz",
    "sv/calls_sniffles2.norm.vcf.gz.tbi",
    "sv/calls_second.norm.vcf.gz",
    "sv/calls_second.norm.vcf.gz.tbi",
    "sv/sv_counts.tsv",
    "sv/sv_calls.tsv",
    "sv/concordance.tsv",
    "sv/provenance.json",
    "sv/identity_check.json",
    "sv/dependency_licenses.json",
    "sv/facts_payload.json",
    "sv/infographic_claims.json",
    "sv/infographic_prompt.txt",
    "figures/figure_2_call_svs.png",
    "figures/figure_2_call_svs.svg",
    "figures/figure_3_normalize_and_index.png",
    "figures/figure_3_normalize_and_index.svg",
    "figures/figure_4_compare_callers_concordance.png",
    "figures/figure_4_compare_callers_concordance.svg",
    "figures/manifest.json",
)
RECEIPT = "sv/science_stage_receipt.json"
OUTPUTS = (*CORE_OUTPUTS, RECEIPT)
SUPPORT_SOURCES = (
    "input_selection.py",
    "license_audit.py",
    "report_qc.py",
    "report_style.py",
    "science_contract.py",
    "svlib.py",
)


def sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _support_source_hashes(support_root=None):
    root = pathlib.Path(support_root) if support_root is not None else pathlib.Path(__file__).parent
    missing = [name for name in SUPPORT_SOURCES if not (root / name).is_file()]
    if missing:
        raise ValueError(f"cannot bind missing science support sources: {missing}")
    return {f"scripts/{name}": sha256(root / name) for name in SUPPORT_SOURCES}


def write_receipt(out_root, *, input_selection_sha256, support_root=None):
    root = pathlib.Path(out_root)
    missing = [name for name in CORE_OUTPUTS if not (root / name).is_file()]
    if missing:
        raise ValueError(f"cannot freeze incomplete science stage: {missing}")
    payload = {
        "schema": "phylo-sv-science-stage/2",
        "input_selection_sha256": input_selection_sha256,
        "outputs": {name: sha256(root / name) for name in CORE_OUTPUTS},
        "support_sources": _support_source_hashes(support_root),
    }
    path = root / RECEIPT
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def verify_receipt(out_root, *, input_selection_sha256, support_root=None):
    root = pathlib.Path(out_root)
    path = root / RECEIPT
    payload = json.loads(path.read_text())
    expected_keys = {"schema", "input_selection_sha256", "outputs", "support_sources"}
    if (not isinstance(payload, dict) or set(payload) != expected_keys
            or payload["schema"] != "phylo-sv-science-stage/2"):
        raise ValueError("science-stage receipt has an unsupported schema")
    if payload["input_selection_sha256"] != input_selection_sha256:
        raise ValueError("science-stage receipt disagrees with selected input manifest")
    if not isinstance(payload["outputs"], dict) or set(payload["outputs"]) != set(CORE_OUTPUTS):
        raise ValueError("science-stage receipt output inventory is incomplete or unexpected")
    for name in CORE_OUTPUTS:
        candidate = root / name
        if not candidate.is_file() or sha256(candidate) != payload["outputs"][name]:
            raise ValueError(f"science-stage output changed after freeze: {name}")
    current_support = _support_source_hashes(support_root)
    recorded_support = payload["support_sources"]
    if not isinstance(recorded_support, dict):
        raise ValueError("science-stage receipt support-source inventory is invalid")
    if recorded_support != current_support:
        changed = sorted(
            name for name in set(recorded_support) | set(current_support)
            if recorded_support.get(name) != current_support.get(name)
        )
        raise ValueError(f"science support source changed after freeze: {changed}")
    return payload
