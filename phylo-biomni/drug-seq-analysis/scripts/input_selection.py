"""Caller-selected input manifest (schema v2).

The manifest SHA-256 is computed from canonical UTF-8 JSON with sorted keys,
comma/colon separators, and manifest_sha256 omitted.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping

SCHEMA_VERSION = "2.0"
ORIGINS = {"uploaded", "downloaded"}
PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class InputSelectionError(ValueError):
    """Raised when input selection cannot be sealed without ambiguity."""


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _canonical_hash(payload: Mapping[str, object]) -> str:
    unhashed = dict(payload)
    unhashed.pop("manifest_sha256", None)
    encoded = json.dumps(
        unhashed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_path(path: str | Path) -> str:
    """Return the SHA-256 of one regular file for tool provenance."""
    return _file_sha256(Path(path))


def _path_identity(path: Path) -> tuple[int, str]:
    if path.is_file():
        return path.stat().st_size, _file_sha256(path)
    if path.is_dir():
        members = []
        total = 0
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            resolved = child.resolve(strict=True)
            if not _inside(resolved, path):
                raise InputSelectionError(
                    f"input directory contains a symlink escape: {child}"
                )
            size = resolved.stat().st_size
            total += size
            members.append(
                {
                    "relative_path": child.relative_to(path).as_posix(),
                    "bytes": size,
                    "sha256": _file_sha256(resolved),
                }
            )
        digest = hashlib.sha256(
            json.dumps(
                members, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()
        return total, digest
    raise InputSelectionError(f"input must resolve to a regular file or directory: {path}")


def select_input(
    *,
    role: str,
    origin: str,
    requested_source: str,
    path: str | Path,
    reference_identity: str | None = None,
    acquisition: Mapping[str, str] | None = None,
) -> dict[str, object]:
    if not role.strip():
        raise InputSelectionError("input role is required")
    if origin not in ORIGINS:
        raise InputSelectionError("origin must be explicitly uploaded or downloaded")
    if not requested_source.strip():
        raise InputSelectionError("requested_source is required; source inference is forbidden")
    candidate = Path(path).expanduser()
    if not candidate.exists():
        if origin == "downloaded":
            raise InputSelectionError(
                "download acquisition is unsupported here; materialize and verify the requested source first"
            )
        raise InputSelectionError(f"selected input does not exist: {candidate}")
    resolved = candidate.resolve(strict=True)
    if _inside(resolved, PACKAGE_ROOT):
        raise InputSelectionError("package-owned data and symlink escapes into the package are forbidden")
    byte_count, digest = _path_identity(resolved)
    item: dict[str, object] = {
        "role": role,
        "origin": origin,
        "requested_source": requested_source,
        "resolved_path": str(resolved),
        "bytes": byte_count,
        "sha256": digest,
    }
    if reference_identity is not None:
        item["reference_identity"] = reference_identity
    supplied_acquisition = dict(acquisition or {})
    if origin == "uploaded":
        if supplied_acquisition and supplied_acquisition != {"status": "not_applicable"}:
            raise InputSelectionError("uploaded inputs require acquisition status not_applicable")
        item["acquisition"] = {"status": "not_applicable"}
    else:
        required = {"status", "source", "tool", "tool_version", "command_or_api", "retrieved_at"}
        if set(supplied_acquisition) != required or supplied_acquisition.get("status") != "complete":
            raise InputSelectionError(
                "downloaded inputs require complete acquisition provenance: "
                "status, source, tool, tool_version, command_or_api, retrieved_at"
            )
        if any(not str(supplied_acquisition[key]).strip() for key in required - {"status"}):
            raise InputSelectionError("downloaded acquisition provenance fields cannot be empty")
        item["acquisition"] = supplied_acquisition
    return item


def parse_acquisition(value: str | None, origin: str) -> dict[str, str] | None:
    """Parse caller-attested acquisition JSON from inline text or an external file."""
    if origin == "uploaded":
        if value:
            raise InputSelectionError("uploaded input does not accept acquisition provenance")
        return None
    if not value:
        raise InputSelectionError("downloaded input requires --*-acquisition-json")
    candidate = Path(value).expanduser()
    raw = candidate.read_text(encoding="utf-8") if candidate.is_file() else value
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InputSelectionError(f"invalid acquisition JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise InputSelectionError("acquisition JSON must be an object")
    return {str(key): str(item) for key, item in parsed.items()}


def seal_manifest(
    *,
    requested_dataset: str,
    inputs: Iterable[Mapping[str, object]],
    result_root: str | Path,
    selection_evidence: str,
    output_path: str | Path | None = None,
) -> dict[str, object]:
    if not requested_dataset.strip():
        raise InputSelectionError("requested_dataset is required")
    if not selection_evidence.strip():
        raise InputSelectionError("selection_evidence is required")
    selected = [dict(item) for item in inputs]
    if not selected:
        raise InputSelectionError("at least one selected input is required")
    root = Path(result_root).expanduser().resolve()
    if _inside(root, PACKAGE_ROOT):
        raise InputSelectionError("result_root must be outside the package")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise InputSelectionError("result_root must be nonexistent or empty before sealing")
    for item in selected:
        source = Path(str(item["resolved_path"])).resolve(strict=True)
        if _inside(root, source) or _inside(source, root):
            raise InputSelectionError("input and result roots must be separate")
    root.mkdir(parents=True, exist_ok=True)
    destination = Path(output_path).expanduser() if output_path else root / "input_selection.json"
    destination = destination.resolve()
    if not _inside(destination, root):
        raise InputSelectionError("input_selection.json must be written inside result_root")
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "execution_mode": "analysis",
        "requested_dataset": requested_dataset,
        "inputs": selected,
        "package_data_used": False,
        "result_root": str(root),
        "selection_evidence": selection_evidence,
    }
    payload["manifest_sha256"] = _canonical_hash(payload)
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def verify_manifest(path: str | Path) -> dict[str, object]:
    manifest_path = Path(path).expanduser().resolve(strict=True)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    required_root = {
        "schema_version", "execution_mode", "requested_dataset", "inputs",
        "package_data_used", "result_root", "selection_evidence", "manifest_sha256",
    }
    if set(payload) != required_root:
        raise InputSelectionError("input selection manifest root fields disagree with schema v2")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise InputSelectionError("input selection schema_version must be 2.0")
    if payload.get("execution_mode") != "analysis":
        raise InputSelectionError("execution_mode must be analysis")
    if payload.get("package_data_used") is not False:
        raise InputSelectionError("package_data_used must be false")
    if not str(payload.get("requested_dataset", "")).strip() or not str(
        payload.get("selection_evidence", "")
    ).strip():
        raise InputSelectionError("requested_dataset and selection_evidence are required")
    if not isinstance(payload.get("inputs"), list) or not payload["inputs"]:
        raise InputSelectionError("manifest requires at least one input")
    if payload.get("manifest_sha256") != _canonical_hash(payload):
        raise InputSelectionError("input selection manifest hash mismatch")
    root = Path(str(payload.get("result_root", ""))).resolve()
    if _inside(root, PACKAGE_ROOT):
        raise InputSelectionError("manifest result_root resolves inside the package")
    if manifest_path != root / "input_selection.json":
        raise InputSelectionError("manifest path and sealed result_root disagree")
    for item in payload.get("inputs", []):
        required_input = {
            "role", "origin", "requested_source", "resolved_path", "bytes", "sha256",
            "acquisition",
        }
        if set(item) not in (required_input, required_input | {"reference_identity"}):
            raise InputSelectionError("manifest input fields disagree with schema v2")
        if not str(item.get("role", "")).strip() or not str(item.get("requested_source", "")).strip():
            raise InputSelectionError("manifest input role and requested_source are required")
        if item.get("origin") not in ORIGINS:
            raise InputSelectionError("manifest input origin is unsupported")
        acquisition = item.get("acquisition")
        if not isinstance(acquisition, dict):
            raise InputSelectionError("manifest input acquisition provenance is required")
        if item["origin"] == "uploaded" and acquisition != {"status": "not_applicable"}:
            raise InputSelectionError("uploaded manifest input has invalid acquisition provenance")
        if item["origin"] == "downloaded":
            required_acquisition = {
                "status", "source", "tool", "tool_version", "command_or_api", "retrieved_at",
            }
            if set(acquisition) != required_acquisition or acquisition.get("status") != "complete":
                raise InputSelectionError("downloaded manifest input acquisition is incomplete")
        resolved = Path(str(item.get("resolved_path", ""))).resolve(strict=True)
        if _inside(resolved, PACKAGE_ROOT):
            raise InputSelectionError("manifest resolves to package-owned data")
        size, digest = _path_identity(resolved)
        if size != item.get("bytes") or digest != item.get("sha256"):
            raise InputSelectionError(f"input identity changed after sealing: {resolved}")
        if _inside(root, resolved) or _inside(resolved, root):
            raise InputSelectionError("manifest input and result roots overlap")
    return payload
