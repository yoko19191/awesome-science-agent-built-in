"""Sealed analysis input-selection manifest (schema v2)."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib

SCHEMA_VERSION = "2.0"
EXECUTION_MODES = ("analysis",)
INPUT_ORIGINS = ("uploaded", "downloaded")
PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _file_hash(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def hash_path(path):
    resolved = pathlib.Path(path).resolve(strict=True)
    if resolved.is_file():
        return _file_hash(resolved)
    if not resolved.is_dir():
        raise ValueError(f"input is not a regular file or directory: {resolved}")
    files = sorted(candidate for candidate in resolved.rglob("*") if candidate.is_file())
    if not files:
        raise ValueError(f"input directory is empty: {resolved}")
    digest = hashlib.sha256()
    size = 0
    for candidate in files:
        target = candidate.resolve(strict=True)
        if not _within(target, resolved):
            raise ValueError(f"input directory contains symlink escape: {candidate} -> {target}")
        count, sha = _file_hash(target)
        size += count
        digest.update(_canonical([candidate.relative_to(resolved).as_posix(), count, sha]))
    return size, digest.hexdigest()


def _acquisition_record(origin, acquisition):
    if origin == "uploaded":
        if acquisition not in (None, {"status": "not_applicable"}):
            raise ValueError("uploaded input acquisition must be {status:not_applicable}")
        return {"status": "not_applicable"}
    if not isinstance(acquisition, dict) or acquisition.get("status") != "complete":
        raise ValueError("downloaded input requires complete acquisition provenance")
    required = ("source", "tool", "tool_version", "command_or_api", "retrieved_at")
    allowed = {"status", *required}
    if set(acquisition) != allowed:
        raise ValueError(f"downloaded acquisition keys must exactly equal {sorted(allowed)}")
    if any(not str(acquisition.get(key, "")).strip() for key in required):
        raise ValueError(f"downloaded acquisition requires non-empty fields: {required}")
    return {"status": "complete", **{key: str(acquisition[key]) for key in required}}


def input_record(*, role, path, origin, requested_source, execution_mode, acquisition=None):
    if execution_mode not in EXECUTION_MODES:
        raise ValueError(f"execution_mode must be one of {EXECUTION_MODES}")
    if origin not in INPUT_ORIGINS:
        raise ValueError(f"origin must be one of {INPUT_ORIGINS}")
    if not role or not requested_source:
        raise ValueError("role and requested_source must be non-empty")
    supplied = pathlib.Path(path).expanduser()
    try:
        resolved = supplied.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"selected input {role!r} does not resolve: {path}: {exc}") from exc
    lexical = pathlib.Path(os.path.abspath(os.fspath(supplied)))
    package_root = PACKAGE_ROOT.resolve()
    if _within(lexical, package_root) or _within(resolved, package_root):
        raise ValueError("analysis mode rejects package-owned input data, including symlink aliases")
    size, sha = hash_path(resolved)
    return {
        "role": role,
        "origin": origin,
        "requested_source": requested_source,
        "resolved_path": str(resolved),
        "bytes": size,
        "sha256": sha,
        "acquisition": _acquisition_record(origin, acquisition),
    }

def validate_acquisition_map(acquisitions, records):
    if not isinstance(acquisitions, dict):
        raise ValueError("acquisitions must be a JSON object keyed by downloaded input role")
    expected = {record["role"] for record in records if record["origin"] == "downloaded"}
    if set(acquisitions) != expected:
        raise ValueError(
            f"acquisition keys must exactly equal downloaded roles: expected {sorted(expected)}, "
            f"got {sorted(acquisitions)}"
        )


def build_manifest(*, execution_mode, requested_dataset, inputs, result_root, selection_evidence):
    records = list(inputs)
    if not records:
        raise ValueError("at least one explicitly selected logical input is required")
    if not requested_dataset or not selection_evidence:
        raise ValueError("requested_dataset and selection_evidence must be non-empty")
    if len({record["role"] for record in records}) != len(records):
        raise ValueError("logical input roles must be unique")
    root = pathlib.Path(result_root).expanduser().resolve(strict=False)
    package_root = PACKAGE_ROOT.resolve()
    if _within(root, package_root):
        raise ValueError(f"result_root must be outside the package: {root}")
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"result_root must be new or empty; refusing stale outputs: {root}")
    for record in records:
        selected = pathlib.Path(record["resolved_path"])
        if _within(root, selected) or _within(selected, root):
            raise ValueError(f"result_root must be separate from selected input {record['role']}: {root}")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "execution_mode": execution_mode,
        "requested_dataset": requested_dataset,
        "inputs": records,
        "package_data_used": False,
        "result_root": str(root),
        "selection_evidence": selection_evidence,
    }
    payload["manifest_sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
    return payload


def _validate_manifest_shape(manifest):
    top = {
        "schema_version", "execution_mode", "requested_dataset", "inputs",
        "package_data_used", "result_root", "selection_evidence", "manifest_sha256",
    }
    if not isinstance(manifest, dict) or set(manifest) != top:
        raise ValueError(f"manifest keys must exactly equal {sorted(top)}")
    if manifest["schema_version"] != SCHEMA_VERSION or manifest["execution_mode"] != "analysis":
        raise ValueError("manifest must use schema_version=2.0 and execution_mode=analysis")
    if manifest["package_data_used"] is not False:
        raise ValueError("manifest package_data_used must be false")
    if not all(isinstance(manifest[key], str) and manifest[key] for key in (
        "requested_dataset", "result_root", "selection_evidence", "manifest_sha256"
    )):
        raise ValueError("manifest identity strings must be non-empty")
    if not isinstance(manifest["inputs"], list) or not manifest["inputs"]:
        raise ValueError("manifest inputs must be a non-empty array")
    required = {"role", "origin", "requested_source", "resolved_path", "bytes", "sha256", "acquisition"}
    for record in manifest["inputs"]:
        if not isinstance(record, dict) or set(record) not in (required, required | {"reference_identity"}):
            raise ValueError("manifest input keys do not match schema v2")
        if record["origin"] not in INPUT_ORIGINS:
            raise ValueError("manifest input origin is invalid")
        if not all(isinstance(record[key], str) and record[key] for key in (
            "role", "requested_source", "resolved_path", "sha256"
        )):
            raise ValueError("manifest input strings must be non-empty")
        if not isinstance(record["bytes"], int) or record["bytes"] < 0:
            raise ValueError("manifest input bytes must be a non-negative integer")
        if len(record["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in record["sha256"]):
            raise ValueError("manifest input sha256 is invalid")
        _acquisition_record(record["origin"], record["acquisition"])


def verify_manifest(manifest):
    _validate_manifest_shape(manifest)
    recorded = manifest.get("manifest_sha256")
    payload = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    observed = hashlib.sha256(_canonical(payload)).hexdigest()
    if recorded != observed:
        raise ValueError("input manifest hash mismatch")


def write_manifest(manifest, path):
    verify_manifest(manifest)
    pathlib.Path(path).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def assert_inputs_unchanged(manifest):
    verify_manifest(manifest)
    for record in manifest["inputs"]:
        size, sha = hash_path(record["resolved_path"])
        if size != record["bytes"] or sha != record["sha256"]:
            raise ValueError(f"selected input changed after sealing: {record['role']}")


def bind_artifact(manifest, artifact):
    values = {
        "execution_mode": manifest["execution_mode"],
        "package_data_used": manifest["package_data_used"],
        "result_root": manifest["result_root"],
        "input_manifest_sha256": manifest["manifest_sha256"],
    }
    artifact.update(values)
    return artifact


def bind_existing_json_artifacts(manifest, result_root, names=None):
    """Revalidate selected inputs, then bind lineage into existing JSON artifacts."""
    assert_inputs_unchanged(manifest)
    root = pathlib.Path(result_root)
    candidates = names or (
        "provenance.json", "facts_payload.json", "report_facts.json",
        "run_receipt.json", "dependency_receipt.json", "basecalling_receipt.json",
    )
    for name in candidates:
        path = root / name
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"lineage target must be a JSON object: {path}")
        bind_artifact(manifest, payload)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def require_fresh_result_root(path):
    root = pathlib.Path(path).expanduser().resolve(strict=False)
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"result_root must be new or empty; refusing stale outputs: {root}")
    return root
