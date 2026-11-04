#!/usr/bin/env python3
"""Build the file list and arguments for hpc_run_pipeline from a pipeline repo.

  submission.py REPO --staged-prefix /mnt/results/NAME [--stage] [--zip /mnt/results/NAME.zip] \
      [--input ALIAS=/mnt/path ...] [--param KEY=VALUE ...]

REPO is the local build (e.g. /workspace/NAME). /mnt/results is an S3 mount that can't
chmod, symlink, rename or rewrite files in place, so build and test in local
scratch and copy the result over:
  --stage  copies the bundle plus conf/portable.config into --staged-prefix
           (plain byte copies, so it works on the S3 mount) and deletes files a
           previous --stage wrote that are no longer in the repo
  --zip    writes a zip of the same files, keeping exec bits, via a local temp file

Prints JSON with pipeline_files (bundle files mapped to --staged-prefix),
input_files, params and not_submitted. The bundle is an allowlist: main.nf,
nextflow.config, modules.json, README.md and the conf/ modules/ subworkflows/
workflows/ bin/ templates/ assets/ lib/ folders. conf/portable.config, hidden
files, tests/ folders, logs and zips are never submitted. Anything else in the
repo (samplesheets, reads, notes) is listed under not_submitted: pass input data
with --input instead.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

PORTABLE_CONFIG = "conf/portable.config"
STAGED_MANIFEST = ".staged-files.json"
BUNDLE_FILES = {"main.nf", "nextflow.config", "modules.json", "README.md"}
BUNDLE_DIRS = {"conf", "modules", "subworkflows", "workflows", "bin", "templates", "assets", "lib"}
_RUNTIME_DIRS = {"work", "results", "null"}
_EXCLUDED_SUFFIXES = (".zip", ".log")


def _repo_files(repo: Path) -> list[str]:
    """Every repo file except hidden files and Nextflow runtime folders."""
    files = []
    for path in repo.rglob("*"):
        rel = path.relative_to(repo).as_posix()
        parts = rel.split("/")
        if not path.is_file() or any(p.startswith(".") for p in parts) or parts[0] in _RUNTIME_DIRS:
            continue
        files.append(rel)
    return sorted(files)


def _in_bundle(rel: str) -> bool:
    parts = rel.split("/")
    if rel == PORTABLE_CONFIG or rel.endswith(_EXCLUDED_SUFFIXES) or "tests" in parts[:-1]:
        return False
    return rel in BUNDLE_FILES or (len(parts) > 1 and parts[0] in BUNDLE_DIRS)


def bundle_aliases(repo: Path) -> list[str]:
    return [rel for rel in _repo_files(repo) if _in_bundle(rel)]


def deliverable_files(repo: Path) -> list[str]:
    """What the user gets: the bundle plus the portable config layer."""
    aliases = bundle_aliases(repo)
    return sorted(aliases + [PORTABLE_CONFIG]) if (repo / PORTABLE_CONFIG).is_file() else aliases


def _gpu_label_errors(repo: Path, resources: dict) -> list[str]:
    declared_labels = set()
    accelerator_process_labels = []
    label_pattern = r"^\s*label\s+['\"]([^'\"]+)['\"]"
    process_pattern = re.compile(r"(?m)^\s*process\s+[A-Za-z_]\w*\s*\{")
    section_pattern = re.compile(r"(?m)^\s*(?:input|output|when|script|shell|exec|stub):")
    accelerator_pattern = r"^\s*accelerator(?:\s|$)"
    for rel in bundle_aliases(repo):
        if not rel.endswith(".nf"):
            continue
        source = (repo / rel).read_text()
        process_starts = list(process_pattern.finditer(source))
        for index, match in enumerate(process_starts):
            end = (
                process_starts[index + 1].start()
                if index + 1 < len(process_starts)
                else len(source)
            )
            process_body = source[match.end():end]
            section = section_pattern.search(process_body)
            directives = process_body[:section.start()] if section else process_body
            labels = set(re.findall(label_pattern, directives, re.MULTILINE))
            declared_labels.update(labels)
            if re.search(accelerator_pattern, directives, re.MULTILINE):
                accelerator_process_labels.append(labels)

    routed_labels = resources["gpu_process_labels"]
    errors = []
    gpu_labels = {label for label in declared_labels if "gpu" in label.lower()}
    if (gpu_labels or accelerator_process_labels) and not resources["gpu"]:
        errors.append(
            "GPU-labeled processes and accelerator directives require resources.gpu=true; "
            "the platform otherwise submits them to the CPU queue"
        )
    if routed_labels:
        for labels in accelerator_process_labels:
            if not labels.intersection(routed_labels):
                process_labels = ", ".join(sorted(labels)) or "no label"
                errors.append(
                    "accelerator process labels are missing from "
                    f"resources.gpu_process_labels: {process_labels}"
                )
    missing_routes = gpu_labels - set(routed_labels)
    if resources["gpu"] and routed_labels and missing_routes:
        errors.append(
            "GPU process labels are missing from resources.gpu_process_labels: "
            + ", ".join(sorted(missing_routes))
        )
    unknown_labels = set(routed_labels) - declared_labels
    if unknown_labels:
        errors.append(
            "resources.gpu_process_labels are not declared by a bundled process: "
            + ", ".join(sorted(unknown_labels))
        )
    return errors


def build_submission(
    repo: Path,
    staged_prefix: str,
    input_files: dict[str, str],
    params: dict,
    resources: dict,
) -> dict:
    prefix = staged_prefix.rstrip("/")
    aliases = bundle_aliases(repo)
    resource_defaults = {
        "cpus": 4,
        "memory_gb": 8,
        "gpu": False,
        "gpu_count": 0,
        "gpu_process_labels": [],
        "time_hours": 24,
    }
    resource_values = {**resource_defaults, **resources}
    if resource_values["gpu_process_labels"] and not resource_values["gpu"]:
        raise ValueError("gpu_process_labels requires resources.gpu=true")
    if not 0 <= resource_values["gpu_count"] <= 1:
        raise ValueError("gpu_count must be between 0 and 1")
    gpu_errors = _gpu_label_errors(repo, resource_values)
    if gpu_errors:
        raise ValueError("; ".join(gpu_errors))
    skipped = [rel for rel in _repo_files(repo)
               if rel not in aliases and rel != PORTABLE_CONFIG and not _excluded_by_design(rel)]
    return {
        "pipeline_files": {alias: f"{prefix}/{alias}" for alias in aliases},
        "input_files": dict(input_files),
        "params": dict(params),
        "resources": resource_values,
        "not_submitted": skipped,
    }


def _excluded_by_design(rel: str) -> bool:
    return rel.endswith(_EXCLUDED_SUFFIXES) or "tests" in rel.split("/")[:-1]


def _previously_staged(dest: Path) -> list[str]:
    try:
        return json.loads((dest / STAGED_MANIFEST).read_text())
    except FileNotFoundError:
        return []


def _remove_stale(dest: Path, stale: list[str]) -> tuple[list[str], list[str]]:
    """Delete stale staged files and the folders they leave empty."""
    removed, not_removed = [], []
    root = dest.resolve()
    for rel in stale:
        target = dest / rel
        if not target.resolve().is_relative_to(root):
            raise ValueError(f"{dest / STAGED_MANIFEST} lists {rel!r}, outside {dest}")
        try:
            target.unlink(missing_ok=True)
            removed.append(rel)
        except OSError:
            not_removed.append(rel)
            continue
        for folder in target.parents:
            if folder == dest or not folder.is_relative_to(dest):
                break
            try:
                folder.rmdir()
            except OSError:
                break
    return removed, not_removed


def stage(repo: Path, dest: Path) -> dict[str, list[str]]:
    """Copy the deliverable files to `dest` as plain byte copies (no chmod, rename or symlink).

    `dest` keeps a hidden manifest of what was staged. A re-stage first deletes files
    the previous stage wrote that are no longer in the repo, so the handed-off copy
    matches the repo and a path can change between file and folder. Files this
    script didn't stage are never deleted. Deletes the mount refuses are reported
    under `not_removed`.
    """
    repo, dest = repo.resolve(), Path(dest)
    if dest.resolve() == repo:
        return {"staged": deliverable_files(repo), "removed": [], "not_removed": []}
    if repo in dest.resolve().parents:
        raise ValueError(f"--staged-prefix {dest} is inside the repo {repo}")
    files = deliverable_files(repo)
    current = set(files)
    removed, not_removed = _remove_stale(
        dest, [rel for rel in _previously_staged(dest) if rel not in current])
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / rel, target)
    (dest / STAGED_MANIFEST).write_text(json.dumps(files, indent=1) + "\n")
    return {"staged": files, "removed": removed, "not_removed": not_removed}


def write_zip(repo: Path, out: Path) -> list[str]:
    """Zip the deliverable files under a `<zip stem>/` folder, keeping file modes.

    zipfile seeks back while writing, which an S3 mount doesn't allow, so the zip
    is written to a local temp file and then copied to `out` sequentially.
    """
    files = deliverable_files(repo)
    root = Path(out).name.removesuffix(".zip")
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel in files:
                zf.write(repo / rel, f"{root}/{rel}")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tmp, out)
    finally:
        os.unlink(tmp)
    return files


def _pairs(values: list[str], what: str) -> dict[str, str]:
    out = {}
    for value in values:
        key, sep, rest = value.partition("=")
        if not sep or not key:
            sys.exit(f"--{what} expects KEY=VALUE, got {value!r}")
        out[key] = rest
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", type=Path)
    parser.add_argument("--staged-prefix", required=True)
    parser.add_argument("--stage", action="store_true", help="copy the deliverable files to --staged-prefix")
    parser.add_argument("--zip", type=Path, help="also write a zip of the deliverable files here")
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--param", action="append", default=[])
    parser.add_argument("--cpus", type=int, default=4)
    parser.add_argument("--memory-gb", type=int, default=8)
    parser.add_argument("--time-hours", type=int, default=24)
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--gpu-count", type=int, default=0)
    parser.add_argument("--gpu-process-label", action="append", default=[])
    args = parser.parse_args()
    resources = {
        "cpus": args.cpus,
        "memory_gb": args.memory_gb,
        "time_hours": args.time_hours,
        "gpu": args.gpu,
        "gpu_count": args.gpu_count,
        "gpu_process_labels": args.gpu_process_label,
    }
    try:
        sub = build_submission(
            args.repo,
            args.staged_prefix,
            _pairs(args.input, "input"),
            _pairs(args.param, "param"),
            resources,
        )
    except ValueError as error:
        parser.error(str(error))
    if args.stage:
        sub.update(stage(args.repo, Path(args.staged_prefix)))
    if args.zip:
        write_zip(args.repo, args.zip)
        sub["zip"] = str(args.zip)
    print(json.dumps(sub, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
