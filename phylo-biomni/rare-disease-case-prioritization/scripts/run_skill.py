#!/usr/bin/env python3
"""Canonical execution, package-manifest, and receipt implementation for generated skills."""

from __future__ import annotations

import argparse
import csv
import errno
import hashlib
import importlib.util
import json
import math
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
import zlib

sys.dont_write_bytecode = True

SPEC_SCHEMA = "phylo-analysis-spec/1"
MANIFEST_SCHEMA = "phylo-package-manifest/1"
RECEIPT_SCHEMA = "phylo-run-receipt/1"
SKILL_ROOT = pathlib.Path(__file__).resolve().parents[1]
RESULTS = pathlib.Path(os.environ.get("BIOMNI_RESULTS", "/mnt/results")).expanduser().resolve()
CACHE_NAMES = {".DS_Store", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}

class RunFailure(RuntimeError):
    """The package or run violated an objective execution guarantee."""

def canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"

def strict_json(path: pathlib.Path) -> object:
    def reject(value: str) -> None:
        raise ValueError(f"non-finite JSON value {value!r}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject)

def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _safe_relative(value: str) -> pathlib.PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RunFailure(f"unsafe relative path: {value!r}")
    path = pathlib.PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RunFailure(f"unsafe relative path: {value!r}")
    return path

def file_evidence(path: pathlib.Path) -> dict:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise RunFailure(f"evidence path cannot be a symlink: {path}")
    resolved = expanded.resolve(strict=True)
    if resolved.is_file():
        return {"path": str(resolved), "kind": "file", "bytes": resolved.stat().st_size, "sha256": sha256(resolved)}
    if not resolved.is_dir():
        raise RunFailure(f"evidence path is not a file or directory: {path}")
    files = []
    for child in sorted(resolved.rglob("*"), key=lambda item: item.as_posix()):
        if child.is_symlink():
            raise RunFailure(f"input directory contains a symlink: {child}")
        if child.is_file():
            files.append({
                "path": child.relative_to(resolved).as_posix(),
                "bytes": child.stat().st_size, "sha256": sha256(child),
            })
    if not files:
        raise RunFailure(f"input directory is empty: {path}")
    payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return {
        "path": str(resolved), "kind": "directory", "files": files,
        "bytes": sum(item["bytes"] for item in files), "sha256": hashlib.sha256(payload).hexdigest(),
    }

def result_path(relative: str, *, strict: bool) -> pathlib.Path:
    value = _safe_relative(relative)
    candidate = RESULTS.joinpath(*value.parts)
    try:
        candidate.resolve(strict=strict).relative_to(RESULTS.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise RunFailure(f"result path is missing or escapes results: {relative!r}") from exc
    return candidate

def write_json_atomic(path: pathlib.Path, value: object) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json(value)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = pathlib.Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        try:
            os.replace(temporary, path)
        except OSError as exc:
            if exc.errno not in {errno.ENOSYS, errno.EOPNOTSUPP}:
                raise
            with temporary.open("rb") as source, path.open("wb") as target:
                shutil.copyfileobj(source, target)
            if path.read_bytes() != payload.encode("utf-8"):
                raise RunFailure("published JSON bytes differ from the canonical payload")
    finally:
        temporary.unlink(missing_ok=True)
    return path

def load_spec(package: pathlib.Path = SKILL_ROOT) -> dict:
    value = strict_json(package / "skill_spec.json")
    if not isinstance(value, dict) or value.get("schema") != SPEC_SCHEMA:
        raise RunFailure(f"skill_spec.json must use {SPEC_SCHEMA!r}")
    return value
def _manifest_files(package: pathlib.Path) -> list[dict]:
    root = package.resolve(strict=True)
    files = []
    for path in sorted(package.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(package).as_posix()
        if relative in {"package_manifest.json", "skill_deployment.json"}:
            continue
        if path.name in CACHE_NAMES or path.suffix in {".pyc", ".pyo"}:
            raise RunFailure(f"cache artifact cannot be frozen: {relative}")
        if path.is_symlink():
            raise RunFailure(f"package symlink cannot be frozen: {relative}")
        if path.is_file():
            path.resolve(strict=True).relative_to(root)
            files.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256(path)})
    required = {"SKILL.md", "skill_spec.json", "DATA_SOURCES.md", "scripts/run_skill.py"}
    missing = sorted(required.difference(item["path"] for item in files))
    if missing:
        raise RunFailure(f"package is missing required files: {missing}")
    return files

def compute_manifest(package: pathlib.Path = SKILL_ROOT) -> dict:
    files = _manifest_files(package)
    payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return {"schema": MANIFEST_SCHEMA, "sha256": hashlib.sha256(payload).hexdigest(), "files": files}

def freeze_package(package: pathlib.Path = SKILL_ROOT) -> dict:
    manifest_path = package / "package_manifest.json"
    if manifest_path.exists():
        raise RunFailure("package is already frozen; create a new candidate instead of mutating it")
    manifest = compute_manifest(package)
    write_json_atomic(manifest_path, manifest)
    return verify_package(package)

def verify_package(package: pathlib.Path = SKILL_ROOT) -> dict:
    expected = strict_json(package / "package_manifest.json")
    if not isinstance(expected, dict) or expected.get("schema") != MANIFEST_SCHEMA:
        raise RunFailure("package_manifest.json is missing or malformed")
    current = compute_manifest(package)
    if expected != current:
        raise RunFailure("package bytes changed after freezing")
    return current

def _analysis_outputs(spec: dict) -> list[dict]:
    return list(spec.get("analysis", {}).get("outputs", []))

def _presentation_outputs(spec: dict) -> list[dict]:
    if spec.get("presentation_profile") != "scientific-report":
        return []
    return [
        {"path": "report_facts.json", "kind": "json"},
        {"path": "infographic.png", "kind": "image"},
        {"path": f"report_{spec['slug']}.pdf", "kind": "pdf"},
    ]

def _png_dimensions(path: pathlib.Path) -> tuple[int, int]:
    payload = path.read_bytes()
    if payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise RunFailure(f"declared PNG is malformed: {path}")
    cursor, dimensions, ended = 8, None, False
    while cursor < len(payload):
        if cursor + 12 > len(payload):
            break
        length = struct.unpack(">I", payload[cursor:cursor + 4])[0]
        end = cursor + 12 + length
        if end > len(payload):
            break
        kind, data = payload[cursor + 4:cursor + 8], payload[cursor + 8:cursor + 8 + length]
        expected = struct.unpack(">I", payload[cursor + 8 + length:end])[0]
        if zlib.crc32(kind + data) & 0xFFFFFFFF != expected:
            break
        if kind == b"IHDR" and length == 13:
            dimensions = struct.unpack(">II", data[:8])
        ended = kind == b"IEND" and length == 0
        cursor = end
        if ended:
            break
    if not dimensions or min(dimensions) < 1 or not ended or cursor != len(payload):
        raise RunFailure(f"declared PNG has invalid dimensions: {path}")
    return dimensions

def _validate_output(output: dict) -> dict:
    path = result_path(str(output["path"]), strict=True)
    minimum = int(output.get("min_bytes", 1))
    if not path.is_file() or path.stat().st_size < minimum:
        raise RunFailure(f"declared output is absent or too small: {output['path']}")
    kind = output.get("kind", "file")
    detail: dict[str, object] = {}
    if kind == "json":
        strict_json(path)
    elif kind == "csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            if not next(csv.reader(handle), []):
                raise RunFailure(f"declared CSV has no header: {output['path']}")
    elif kind == "image":
        width, height = _png_dimensions(path)
        detail = {"width": width, "height": height}
    evidence = file_evidence(path)
    evidence["path"] = str(output["path"])
    evidence["output_kind"] = kind
    return {**evidence, **detail}

def _parameter_values(spec: dict, provided: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    values: dict[str, object] = {}
    argv: list[str] = []
    declarations = {item["name"]: item for item in spec["analysis"].get("parameters", [])}
    unknown = sorted(set(provided).difference(declarations))
    if unknown:
        raise RunFailure(f"undeclared parameters: {unknown}")
    for name, declaration in declarations.items():
        if name in provided:
            value = provided[name]
        elif declaration.get("required") is True:
            raise RunFailure(f"required parameter is missing: {name}")
        else:
            value = declaration.get("default")
        kind = declaration["type"]
        valid = (
            (kind == "string" and isinstance(value, str))
            or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool))
            or (kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)))
            or (kind == "boolean" and isinstance(value, bool))
        )
        if not valid:
            raise RunFailure(f"parameter {name!r} does not match declared type {kind}")
        choices = declaration.get("choices")
        if choices is not None and value not in choices:
            raise RunFailure(f"parameter {name!r} is not one of its declared choices")
        values[name] = value
        if kind == "boolean":
            if value:
                argv.append(declaration["flag"])
        else:
            argv.extend([declaration["flag"], str(value)])
    return values, argv

def _command(spec: dict, input_path: pathlib.Path, parameters: dict[str, object]) -> tuple[list[str], dict]:
    entrypoint = SKILL_ROOT.joinpath(*_safe_relative(spec["analysis"]["entrypoint"]).parts).resolve(strict=True)
    entrypoint.relative_to(SKILL_ROOT.resolve(strict=True))
    values, parameter_argv = _parameter_values(spec, parameters)
    if spec["analysis"]["language"] == "python":
        argv = [sys.executable, str(entrypoint)]
    else:
        interpreter = shutil.which("Rscript")
        if not interpreter:
            raise RunFailure("Rscript is unavailable")
        argv = [interpreter, str(entrypoint)]
    return [*argv, "--input", str(input_path), *parameter_argv], values

def _stream_evidence(value: str) -> dict:
    payload = value.encode("utf-8")
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}

def _receipt_path(value: str | pathlib.Path) -> pathlib.Path:
    path = pathlib.Path(value)
    if path.is_absolute():
        resolved = path.expanduser().resolve(strict=False)
        try:
            resolved.relative_to(RESULTS.resolve(strict=True))
        except ValueError as exc:
            raise RunFailure("receipt must stay beneath the results directory") from exc
        return resolved
    return result_path(path.as_posix(), strict=False)

def execute(
    input_value: str | pathlib.Path,
    parameters: dict[str, object] | None = None,
    *,
    receipt_path: str | pathlib.Path = "run_receipt.json",
    timeout_seconds: int = 1800,
) -> dict:
    spec = load_spec()
    manifest = verify_package()
    input_source = pathlib.Path(input_value).expanduser()
    input_evidence = file_evidence(input_source)
    input_path = pathlib.Path(input_evidence["path"])
    declared = [*_analysis_outputs(spec), *_presentation_outputs(spec)]
    dirty = [item["path"] for item in declared if result_path(item["path"], strict=False).exists()]
    if dirty:
        raise RunFailure(f"declared output paths are not clean: {dirty}")
    argv, parameter_values = _command(spec, input_path, parameters or {})
    entrypoint = SKILL_ROOT / spec["analysis"]["entrypoint"]
    entrypoint_evidence = file_evidence(entrypoint)
    entrypoint_evidence["path"] = spec["analysis"]["entrypoint"]
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "run_id": uuid.uuid4().hex,
        "state": "running",
        "status": "pending",
        "package_manifest_sha256": manifest["sha256"],
        "entrypoint": entrypoint_evidence,
        "input": input_evidence,
        "parameters": parameter_values,
        "command": argv,
    }
    destination = _receipt_path(receipt_path)
    for item in declared:
        output = result_path(item["path"], strict=False).resolve()
        if destination.is_relative_to(output) or output.is_relative_to(destination):
            raise RunFailure(f"receipt path overlaps declared output: {item['path']}")
    if destination.exists():
        raise RunFailure(f"receipt path is not clean: {destination.name}")
    write_json_atomic(destination, receipt)
    environment = {**os.environ, "BIOMNI_RESULTS": str(RESULTS), "SKILL_DIR": str(SKILL_ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        completed = subprocess.run(
            argv,
            cwd=RESULTS,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        receipt.update({"state": "terminal", "status": "fail", "failures": [f"execution exceeded {timeout_seconds} seconds"]})
        write_json_atomic(destination, receipt)
        raise RunFailure(receipt["failures"][0]) from exc
    receipt["exit_status"] = completed.returncode
    receipt["stdout"] = _stream_evidence(completed.stdout)
    receipt["stderr"] = _stream_evidence(completed.stderr)
    receipt["stderr_tail"] = completed.stderr[-1000:]
    if completed.returncode != 0:
        receipt.update({"state": "terminal", "status": "fail", "analysis_outputs": [], "failures": [f"entrypoint exited {completed.returncode}"]})
        write_json_atomic(destination, receipt)
        return receipt
    try:
        receipt["analysis_outputs"] = [_validate_output(output) for output in _analysis_outputs(spec)]
    except (OSError, ValueError, RunFailure) as exc:
        receipt.update({"state": "terminal", "status": "fail", "failures": [str(exc)]})
        write_json_atomic(destination, receipt)
        return receipt
    receipt.update({"state": "analysis_complete", "status": "pending", "failures": []})
    write_json_atomic(destination, receipt)
    return receipt

def _same_evidence(expected: dict, current: dict) -> bool:
    return expected.get("sha256") == current.get("sha256") and expected.get("bytes") == current.get("bytes")

def _verify_execution(spec: dict, receipt: dict) -> None:
    if receipt.get("package_manifest_sha256") != verify_package()["sha256"]:
        raise RunFailure("receipt is bound to another package manifest")
    entrypoint = SKILL_ROOT / spec["analysis"]["entrypoint"]
    if not _same_evidence(receipt.get("entrypoint", {}), file_evidence(entrypoint)):
        raise RunFailure("entrypoint bytes changed after execution")
    input_path = pathlib.Path(str(receipt.get("input", {}).get("path", "")))
    if not _same_evidence(receipt.get("input", {}), file_evidence(input_path)):
        raise RunFailure("input bytes changed after execution")
    by_path = {item.get("path"): item for item in receipt.get("analysis_outputs", []) if isinstance(item, dict)}
    declared_paths = {item["path"] for item in _analysis_outputs(spec)}
    if set(by_path) != declared_paths:
        raise RunFailure("receipt does not cover every declared analysis output")
    for output in _analysis_outputs(spec):
        current = _validate_output(output)
        if not _same_evidence(by_path[output["path"]], current):
            raise RunFailure(f"analysis output changed after execution: {output['path']}")


def _report_module():
    path = SKILL_ROOT / "scripts" / "report_qc.py"
    spec = importlib.util.spec_from_file_location("_phylo_report_qc", path)
    if spec is None or spec.loader is None:
        raise RunFailure("scientific-report package has no report_qc.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def finalize(
    *,
    receipt_path: str | pathlib.Path = "run_receipt.json",
    provider: str | None = None,
    explicit_provider: bool = False,
) -> dict:
    destination = _receipt_path(receipt_path)
    receipt = strict_json(destination)
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        raise RunFailure("run receipt is missing or malformed")
    if receipt.get("state") != "analysis_complete":
        raise RunFailure("only an analysis_complete receipt can be finalized")
    spec = load_spec()
    try:
        _verify_execution(spec, receipt)
        presentation = {"profile": "artifacts-only"}
        if spec.get("presentation_profile") == "scientific-report":
            sources = {
                item["path"]: item["sha256"]
                for item in receipt["analysis_outputs"]
                if item["path"] in spec.get("presentation", {}).get("source_outputs", [])
            }
            presentation = _report_module().validate_objective_report(
                spec,
                results=RESULTS,
                provider=provider,
                explicit_provider=explicit_provider,
                source_evidence=sources,
            )
        receipt["final_outputs"] = [
            _validate_output(output) if output.get("kind") != "pdf" else {
                **file_evidence(result_path(output["path"], strict=True)),
                "path": output["path"],
                "output_kind": "pdf",
            }
            for output in [*_analysis_outputs(spec), *_presentation_outputs(spec)]
        ]
        receipt.update({"state": "terminal", "status": "pass", "presentation": presentation, "failures": []})
    except (OSError, ValueError, RuntimeError) as exc:
        receipt.update({"state": "terminal", "status": "fail", "failures": [str(exc)]})
    write_json_atomic(destination, receipt)
    return receipt


def verify_receipt(receipt_path: str | pathlib.Path = "run_receipt.json") -> dict:
    destination = _receipt_path(receipt_path)
    receipt = strict_json(destination)
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        raise RunFailure("run receipt is missing or malformed")
    if receipt.get("state") != "terminal" or receipt.get("status") != "pass":
        raise RunFailure("run receipt is not a terminal pass")
    spec = load_spec()
    _verify_execution(spec, receipt)
    expected = {item["path"]: item for item in receipt.get("final_outputs", []) if isinstance(item, dict)}
    declared = [*_analysis_outputs(spec), *_presentation_outputs(spec)]
    if set(expected) != {item["path"] for item in declared}:
        raise RunFailure("final receipt does not cover every declared output")
    for output in declared:
        path = result_path(output["path"], strict=True)
        current = file_evidence(path)
        if not _same_evidence(expected[output["path"]], current):
            raise RunFailure(f"final output changed after receipt: {output['path']}")
    return receipt


def _parse_params(items: list[str]) -> dict[str, object]:
    values: dict[str, object] = {}
    for item in items:
        if "=" not in item:
            raise RunFailure(f"parameter must use name=value: {item!r}")
        name, raw = item.split("=", 1)
        if name in values:
            raise RunFailure(f"duplicate parameter: {name}")
        try:
            values[name] = json.loads(raw)
        except json.JSONDecodeError:
            values[name] = raw
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    execute_parser = subparsers.add_parser("execute")
    execute_parser.add_argument("--input", required=True, type=pathlib.Path)
    execute_parser.add_argument("--param", action="append", default=[])
    execute_parser.add_argument("--receipt", default="run_receipt.json")
    execute_parser.add_argument("--timeout", type=int, default=1800)
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--receipt", default="run_receipt.json")
    finalize_parser.add_argument("--provider")
    finalize_parser.add_argument("--explicit-provider", action="store_true")
    verify_parser = subparsers.add_parser("verify-receipt")
    verify_parser.add_argument("--receipt", default="run_receipt.json")
    args = parser.parse_args()
    try:
        if args.command == "execute":
            result = execute(args.input, _parse_params(args.param), receipt_path=args.receipt, timeout_seconds=args.timeout)
            print(canonical_json(result), end="")
            return 0 if result.get("state") == "analysis_complete" else 1
        if args.command == "finalize":
            result = finalize(receipt_path=args.receipt, provider=args.provider, explicit_provider=args.explicit_provider)
            print(canonical_json(result), end="")
            return 0 if result.get("status") == "pass" else 1
        result = verify_receipt(args.receipt)
        print(canonical_json(result), end="")
        return 0
    except (OSError, ValueError, RunFailure) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
