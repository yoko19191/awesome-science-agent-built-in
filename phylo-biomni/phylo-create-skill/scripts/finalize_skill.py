#!/usr/bin/env python3
"""Check, freeze, and deterministically archive one generated skill package."""

from __future__ import annotations

import argparse
import importlib.util
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import zipfile

sys.dont_write_bytecode = True

import check_skill  # noqa: E402
import skill_spec  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
RUNNER_TEMPLATE = HERE.parent / "templates" / "run_skill.py"
SUMMARY_SCHEMA = "phylo-freeze-summary/1"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class FinalizeFailure(RuntimeError):
    """The package could not be frozen without ambiguity or partial evidence."""


def _load_runner():
    module_spec = importlib.util.spec_from_file_location("_phylo_freeze_runner", RUNNER_TEMPLATE)
    if module_spec is None or module_spec.loader is None:
        raise FinalizeFailure(f"cannot load canonical runner: {RUNNER_TEMPLATE}")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def _require_unclaimed_destinations(*destinations: pathlib.Path) -> None:
    conflicts = [str(path) for path in destinations if path.exists()]
    if conflicts:
        raise FinalizeFailure(f"finalization artifacts already exist: {', '.join(conflicts)}")


def _archive(package: pathlib.Path, destination: pathlib.Path) -> None:
    if destination.exists():
        raise FinalizeFailure(f"refusing to overwrite existing archive: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
        temporary = pathlib.Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(package.rglob("*"), key=lambda item: item.as_posix()):
                if not path.is_file():
                    continue
                relative = pathlib.Path(package.name) / path.relative_to(package)
                info = zipfile.ZipInfo(relative.as_posix(), ZIP_TIMESTAMP)
                info.create_system = 3
                mode = 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644
                info.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
        with temporary.open("rb") as source:
            target = destination.open("xb")
            try:
                with target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
            except OSError:
                destination.unlink(missing_ok=True)
                raise
    finally:
        temporary.unlink(missing_ok=True)


def _publish(package: pathlib.Path, archive_path: pathlib.Path, summary_path: pathlib.Path, runner) -> dict:
    _require_unclaimed_destinations(archive_path, summary_path)
    analytical = (package / "skill_spec.json").is_file()
    manifest = runner.freeze_package(package) if analytical else None
    before = runner.file_evidence(package)
    _archive(package, archive_path)
    after = runner.file_evidence(package)
    if before != after:
        archive_path.unlink(missing_ok=True)
        raise FinalizeFailure("package bytes changed while the archive was being built")
    if analytical:
        runner.verify_package(package)
    summary = {
        "schema": SUMMARY_SCHEMA,
        "status": "frozen",
        "route": "executable-analysis" if analytical else "general-workflow",
        "freeze_method": (
            "canonical-manifest+deterministic-zip"
            if analytical
            else "external-inventory+deterministic-zip"
        ),
        "package_content_sha256": manifest["sha256"] if manifest else after["sha256"],
        "package_tree_sha256": after["sha256"],
        "package_files": after["files"],
        "package_manifest_sha256": manifest["sha256"] if manifest else None,
        "package_manifest_file_sha256": (
            runner.sha256(package / "package_manifest.json") if manifest else None
        ),
        "archive_sha256": runner.sha256(archive_path),
    }
    try:
        _require_unclaimed_destinations(summary_path)
        runner.write_json_atomic(summary_path, summary)
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=pathlib.Path)
    args = parser.parse_args()
    package = args.package.expanduser().resolve()
    results_root = pathlib.Path(os.environ.get("BIOMNI_RESULTS", "/mnt/results")).expanduser().resolve()
    try:
        package.relative_to(results_root)
    except ValueError:
        parser.error(f"package must stay beneath results: {results_root}")
    summary_path = results_root / "evidence" / package.name / "freeze-summary.json"
    archive_path = results_root / f"{package.name}.zip"
    initial_errors = check_skill.check_package(package)
    if initial_errors:
        for error in initial_errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    manifest_path = package / "package_manifest.json"
    manifest_preexisting = manifest_path.exists()
    try:
        _require_unclaimed_destinations(archive_path, summary_path)
        runner = _load_runner()
        summary = _publish(package, archive_path, summary_path, runner)
        result = {
            "status": "frozen",
            "package": str(package),
            "package_tree_sha256": summary["package_tree_sha256"],
            "package_content_sha256": summary["package_content_sha256"],
            "package_manifest_sha256": summary["package_manifest_sha256"],
            "package_manifest_file_sha256": summary["package_manifest_file_sha256"],
            "freeze_summary": str(summary_path),
            "archive": str(archive_path),
            "archive_sha256": summary["archive_sha256"],
        }
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        if not manifest_preexisting:
            manifest_path.unlink(missing_ok=True)
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(skill_spec.canonical_json(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
