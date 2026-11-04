#!/usr/bin/env python3
"""Build (or rebuild) the pipeline repo from plan.json in one deterministic command.

  compose.py plan.json --repo /workspace/<name> --module-author-dir DIR --validate-dir DIR [--offline]

DIR values are the printed Skill Directories of pipeline-module-author and pipeline-hpc-validate;
their scripts are loaded from there, never from an assumed location. In order:
  1. copy templates/pipeline into the repo if it has no main.nf yet;
  2. vendor every nf-core component in plan["components"] (deduplicated; a subworkflow's
     modules once; components already recorded in modules.json at that commit are kept);
  3. scaffold every local component, writing only files that don't exist yet (an existing
     module or an adapted bin/ script is never overwritten);
  4. replace Seqera-hosted images: for every module (vendored or local) whose
     container is Seqera-hosted, and that no plan process already sets a `container` for, take
     (a) the index's biocontainers replacement (from the platform's pipeline image overrides),
     else (b), unless offline, the newest quay.io/biocontainers build of the module's single
     versioned conda pin, pinned by digest. It becomes a `container` on that process in
     conf/modules.config (vendored files are never edited). The rest are listed in
     `retained_upstream_images` with the reason, for reports;
  5. render the config layers and README (render_configs.py);
  6. run local-script validation, preflight, lint_module.py on modules/local/*, and check_ext_args.py.
Prints one JSON summary. Exit 0 when every check passes, 1 when a check fails, 2 on a plan or
directory error, 3 when checks pass but a component fell back to container-only. Rerun it after adapting bin/ scripts
and wiring workflows/pipeline.nf: it re-renders and re-checks.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "templates" / "pipeline"
sys.path.insert(0, str(HERE))
import render_configs  # noqa: E402
import timings  # noqa: E402

NFCORE_REPO = "https://github.com/nf-core/modules.git"
NEEDS = {"module-author": ("vendor_nfcore", "scaffold_local_module", "lint_module", "check_ext_args", "check_wiring",
                           "find_container", "validate_local_scripts"),
         "validate": ("preflight",)}
_PIN_RE = re.compile(r"^([^=<>!\s]+)=+([^=<>!\s]+)")
_INCLUDE_RE = re.compile(r"^\s*include\s*\{[^}]*\}\s*from\s*['\"]([^'\"]+)['\"]", re.M)
_VERSION_RE = re.compile(r"(?<![0-9])([0-9]+\.[0-9]+)(?:\.[0-9]+)?(?![0-9])")


class ComposeError(Exception):
    """The plan or a skill directory is unusable; nothing more can be done without a fix."""


def _container_branches(images: list[str]) -> tuple[str | None, str | None]:
    """Resolve the literal docker/singularity branches used by nf-core modules."""
    docker = next((image for image in images if not image.startswith("http")), None)
    singularity = next((image for image in images if image.startswith("http")), None)
    return (docker.removeprefix("docker://") if docker else None), singularity


def load_scripts(skill_dir: Path | None, flag: str, names: tuple[str, ...]) -> dict:
    """Import `names` from <skill_dir>/scripts, the other skill's printed Skill Directory."""
    if skill_dir is None:
        raise ComposeError(f"{flag} is required: pass the other skill's printed Skill Directory")
    scripts = Path(skill_dir) / "scripts"
    missing = [f"{n}.py" for n in names if not (scripts / f"{n}.py").is_file()]
    if missing:
        raise ComposeError(f"{flag} {skill_dir}: {', '.join(missing)} not found under scripts/; "
                           "pass that skill's printed Skill Directory")
    loaded = {}
    for name in names:
        identity = hashlib.sha256(str(scripts.resolve()).encode()).hexdigest()[:12]
        module_name = f"_compose_{identity}_{name}"
        spec = importlib.util.spec_from_file_location(module_name, scripts / f"{name}.py")
        previous = sys.modules.get(spec.name)
        try:
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module  # dataclasses and pickling look the module up by name
            spec.loader.exec_module(module)  # each script puts its own folder on sys.path
        except (Exception, SystemExit) as error:  # a wrong folder can hold a script that exits
            if previous is None:
                sys.modules.pop(spec.name, None)
            else:
                sys.modules[spec.name] = previous
            raise ComposeError(f"{flag} {skill_dir}: could not load scripts/{name}.py "
                               f"({type(error).__name__}: {error}); pass that skill's printed Skill Directory") from None
        loaded[name] = module
    return loaded


def _recorded(repo: Path) -> dict:
    """modules.json entries by (kind, name): {"git_sha": ..., "installed_by": [...]}."""
    path = repo / "modules.json"
    if not path.is_file():
        return {}
    repos = json.loads(path.read_text()).get("repos", {}).get(NFCORE_REPO, {})
    return {(kind[:-1], name): entry for kind in ("modules", "subworkflows")
            for name, entry in repos.get(kind, {}).get("nf-core", {}).items()}


def _vendor_all(repo: Path, components: list[dict], vendor_mod, index: dict, fetch, snapshot_dir,
                offline: bool) -> list[dict]:
    results, done = [], set()
    direct = frozenset((c.get("type", "module"), c["name"]) for c in components if c.get("kind") == "nf-core")
    for comp in components:
        if comp.get("kind") != "nf-core":
            continue
        kind, name, sha = comp.get("type", "module"), comp["name"], comp.get("sha")
        if (kind, name) in done:
            continue
        want = sha or index["git_sha"]
        entry = _recorded(repo).get((kind, name), {})
        have = entry.get("git_sha")
        # requested_sha: the last vendoring of this commit fell back to the pinned snapshot
        asked = [s for s in (have, entry.get("requested_sha")) if s]
        folder = repo / ("modules" if kind == "module" else "subworkflows") / "nf-core" / name
        if any(s.startswith(want) or want.startswith(s) for s in asked) and (folder / "main.nf").is_file():
            results.append({"kind": kind, "name": name, "source": "already-vendored", "git_sha": have})
            done.add((kind, name))
            continue
        kwargs = {"sha": sha, "offline": offline, "skip": direct - {(kind, name)}}
        if fetch is not None:
            kwargs["fetch"] = fetch
        if snapshot_dir is not None:
            kwargs["snapshot_dir"] = snapshot_dir
        try:
            got = vendor_mod.vendor(repo, name, kind, index, **kwargs)
        except KeyError as error:
            raise ComposeError(str(error).strip('"')) from None
        for r in got:
            if (r["kind"], r["name"]) not in done:
                results.append(r)
                done.add((r["kind"], r["name"]))
    return results


def _scaffold_all(repo: Path, components: list[dict], scaffold_mod, base: Path) -> list[dict]:
    """Scaffold into a scratch repo, then copy only the files the real repo doesn't have."""
    results = []
    for comp in components:
        if comp.get("kind") != "local":
            continue
        name = comp["name"]
        with tempfile.TemporaryDirectory() as tmp:
            code = Path(comp.get("code", ""))
            code = code if code.is_absolute() else base / code
            if not code.is_file():
                raise ComposeError(f"local component {name!r}: code file {code} not found")
            try:
                made = scaffold_mod.scaffold(
                    Path(tmp), name, comp["tool"], comp["version_cmd"], comp["language"], code,
                    comp.get("container", ""), comp.get("conda", []), comp.get("inputs", []),
                    comp.get("outputs", {}), comp.get("label", "process_single"),
                    comp.get("description", ""), comp.get("cells"))
            except (KeyError, ValueError) as error:
                raise ComposeError(f"local component {name!r}: {error}") from None
            written, kept, pristine = [], [], []
            for path in made:
                rel = path.relative_to(tmp).as_posix()
                dest = repo / rel
                if dest.exists():
                    kept.append(rel)
                    if rel.startswith("bin/") and dest.read_bytes() == path.read_bytes():
                        pristine.append(rel)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)  # keeps the bin/ exec bit
                written.append(rel)
        status = "scaffolded" if not kept else "kept" if not written else "partial"
        script = next(p.relative_to(tmp).as_posix() for p in made if p.parent.name == "bin")
        results.append({"name": name, "status": status, "written": written, "kept": kept,
                        "bin_script": script, "bin_adapted": script not in written and script not in pristine})
    return results


def _wired(repo: Path, rel_dir: str) -> bool:
    """Does any workflow file include modules/.../<name> or subworkflows/.../<name>? (not a comment)"""
    for path in [*repo.glob("workflows/**/*.nf"), *repo.glob("subworkflows/local/**/*.nf"), repo / "main.nf"]:
        if not path.is_file():
            continue
        for target in _INCLUDE_RE.findall(path.read_text()):
            resolved = (path.parent / target).resolve()
            if resolved in {(repo / rel_dir).resolve(), (repo / rel_dir / "main").resolve()}:
                return True
    return False


def _next_steps(repo: Path, vendored: list[dict], scaffolded: list[dict]) -> list[str]:
    steps = [f"adapt {s['bin_script']}: read --prefix and one --<input> flag per input, write the "
             "declared outputs, keep the session's logic" for s in scaffolded if not s["bin_adapted"]]
    recorded = _recorded(repo)  # a subworkflow's modules are wired by the subworkflow
    top = [v for v in vendored if v["source"] != "container-only" and not
           set(recorded.get((v["kind"], v["name"]), {}).get("installed_by", [])) - {"modules"}]
    targets = [("modules" if v["kind"] == "module" else "subworkflows") + f"/nf-core/{v['name']}" for v in top]
    targets += [f"modules/local/{s['name']}" for s in scaffolded]
    steps += [f"wire {t} in workflows/pipeline.nf (include from '../{t}/main')"
              for t in targets if not _wired(repo, t)]
    steps += [f"{v['name']}: container-only fallback; add it as a local component reusing "
              f"{v.get('containers', {}).get('docker', 'its nf-core container')}, and flag it in the plan"
              for v in vendored if v["source"] == "container-only"]
    return steps


def _names(proc: dict, targets) -> set[str]:
    return {proc["name"], *targets(proc.get("selector") or proc["name"])}


def _quay_pin(module_dir: Path, find, offline: bool, quay_fetch) -> tuple[str | None, str]:
    """(digest-only image, reason when None) from the module's single versioned conda pin."""
    conda = find.from_module_dir(module_dir)["conda"]
    if find.registry.offline_reason(offline):
        return None, "offline"
    if len(conda) != 1:
        return None, f"multi-package ({len(conda)} conda pins)"
    pin = _PIN_RE.match(conda[0].split("::")[-1])
    if not pin:
        return None, f"no exact version in {conda[0]}"
    try:
        image = find.pin_biocontainer(pin.group(1), pin.group(2), **({"fetch": quay_fetch} if quay_fetch else {}))
    except (OSError, ValueError, KeyError) as error:
        return None, f"quay.io lookup failed ({error})"
    return image, "" if image else f"no quay.io/biocontainers build of {pin.group(1)} {pin.group(2)}"


def _replace_seqera(repo: Path, plan: dict, index: dict, find, pre, offline: bool, quay_fetch):
    """(processes with container overrides, overrides made, reasons for the ones not made)."""
    processes = [dict(p) for p in plan["processes"]]
    explicit = {n for p in processes if p.get("container") for n in _names(p, pre.selector_targets)}
    known = find.replacements(index)
    overrides, reasons = [], {}
    for name, info in sorted(pre.process_containers(pre.bundle_files(repo)).items()):
        if name in explicit or not any(pre.is_seqera(i) for i in info["images"]):
            continue
        docker, singularity = _container_branches(info["images"])
        if docker and not pre.is_seqera(docker):  # only the singularity branch is Seqera-hosted
            image, source, why = docker, "module docker image", ""
        else:
            image, source, why = find.biocontainers_replacement(known.get(docker)), "index", ""
            if not image:
                image, why = _quay_pin(repo / Path(info["file"]).parent, find, offline, quay_fetch)
                source = "quay.io"
                if known.get(docker):
                    why = f"mirror-only in {known[docker][0]['from']}; {why}"
        if not image:
            reasons[name] = why
            continue
        target = next((p for p in processes if name in _names(p, pre.selector_targets)), None)
        if target is None:
            processes.append({"name": name, "container": image})
        else:
            target["container"] = image
        overrides.append({"process": name, "replaces": docker, "image": image, "source": source})
    return processes, overrides, reasons


def compose(plan: dict, repo: Path, module_author_dir: Path | None, validate_dir: Path | None,
            base: Path = Path("."), offline: bool = False, index: dict | None = None, fetch=None,
            snapshot_dir: Path | None = None, template: Path = TEMPLATE, quay_fetch=None,
            reports: Path | None = None, timing_log: Path | None = None) -> dict:
    if reports:
        timings.record(reports, "start", "compose", log=timing_log)
    mod = load_scripts(module_author_dir, "--module-author-dir", NEEDS["module-author"])
    val = load_scripts(validate_dir, "--validate-dir", NEEDS["validate"])
    for key in ("name", "params", "processes"):
        if key not in plan:
            raise ComposeError(f"plan.json has no {key!r}")
    components = plan.get("components", [])
    for comp in components:
        if comp.get("kind") not in ("nf-core", "local") or not comp.get("name"):
            raise ComposeError(f"component {comp!r} needs a name and a kind of 'nf-core' or 'local'")
        if comp["kind"] == "nf-core" and comp.get("type", "module") not in ("module", "subworkflow"):
            raise ComposeError(f"nf-core component {comp['name']!r}: type must be 'module' or 'subworkflow'")
    repo = Path(repo)
    template_copied = not (repo / "main.nf").is_file()
    if template_copied:  # only files the repo doesn't have yet
        for src in sorted(template.rglob("*")):
            dest = repo / src.relative_to(template)
            if src.is_file() and not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
    if index is None:
        index = mod["vendor_nfcore"].load_index()
    vendored = _vendor_all(repo, components, mod["vendor_nfcore"], index, fetch, snapshot_dir, offline)
    scaffolded = _scaffold_all(repo, components, mod["scaffold_local_module"], base)
    pre = val["preflight"]
    processes, overrides, reasons = _replace_seqera(repo, plan, index, mod["find_container"], pre, offline, quay_fetch)
    try:
        configs = [p.relative_to(repo).as_posix() for p in render_configs.write(repo, {**plan, "processes": processes})]
    except ValueError as error:
        raise ComposeError(str(error)) from None
    retained = [{"process": s["process"], "image": s["image"],
                 "reason": reasons.get(s["process"], "")}
                for s in pre.seqera_images(pre.bundle_files(repo))]

    local = sorted(p for p in (repo / "modules" / "local").glob("*") if p.is_dir())
    adapted = {repo / s["bin_script"] for s in scaffolded if s["bin_adapted"]}
    local_validation = mod["validate_local_scripts"].validate(
        repo, index, set(plan.get("dependencies_accepted", [])))
    validation_errors = [f"{result['module']}: {error}" for result in local_validation for error in result["errors"]]
    checks = {
        "preflight": pre.preflight(repo),
        "lint_module": [e for m in local for e in mod["lint_module"].lint(m, check_bin=m not in adapted)],
        "validate_local_scripts": validation_errors,
        "check_ext_args": mod["check_ext_args"].check_repo(repo, set(plan.get("ext_args_allow", []))),
    }
    wiring = mod["check_wiring"].check_repo(repo)
    checks["check_wiring"] = wiring["errors"]
    serialized_errors, serialized_warnings = _serialized_compatibility(
        plan, wiring, repo, index)
    checks["serialized_compatibility"] = serialized_errors
    gzip_errors, gzip_warnings = _gzipped_input_audit(plan, index)  # also run before the gate: --check-plan
    checks["gzipped_inputs"] = gzip_errors
    data_warnings = _data_warnings(plan)
    checks["data_dependent_parameters"] = data_warnings
    checks["unresolved_dependencies"] = [
        error for error in validation_errors if "unresolved dependency" in error]
    local_data_runs, local_data_warnings = _local_data_runs(plan, components, reports)
    ok = not any(checks.values())
    result = {"ok": ok, "repo": str(repo), "template": "copied" if template_copied else "present",
            "vendored": vendored, "local_modules": scaffolded, "configs": configs,
            "container_overrides": overrides, "retained_upstream_images": retained,
            "checks": {k: {"ok": not v, "errors": v} for k, v in checks.items()},
            "local_script_results": local_validation,
            "warnings": [w for r in local_validation for w in r["warnings"]]
                        + [w for r in local_validation for w in r["not_checkable"]]
                        + data_warnings + local_data_warnings + wiring["warnings"] + serialized_warnings
                        + gzip_warnings,
            "local_data_runs": local_data_runs,
            "next_steps": _next_steps(repo, vendored, scaffolded) +
                [f"wiring warning: {warning}" for warning in wiring["warnings"]
                 if "unverified" not in warning],  # unverified shapes are left to the stub run
            "wiring_unverified": sum("unverified" in w for w in wiring["warnings"])}
    if reports:
        timings.record(reports, "end", "compose", log=timing_log)
    return result


_FASTQ_GZ = re.compile(r"\.(fastq|fq)\.gz$", re.I)


def _gzipped_input_audit(plan: dict, index: dict) -> tuple[list[str], list[str]]:
    """Session .gz inputs other than FASTQ need a decompression step unless the plan says the consumer reads .gz.

    nf-core meta.yml rarely gives an input pattern (custom/tx2gene's GTF has none), so the rule is default-deny:
    round-2 and round-4 Eval A both discovered after approval that the GTF had to be gunzipped.
    """
    files = plan.get("session_inputs")
    if not isinstance(files, list):
        return [], ["plan.json has no session_inputs (the session's input file names), so gzipped inputs are unchecked"]
    readers = set(plan.get("gz_ok") or [])  # file names whose consumer reads .gz (checked with nfcore_index.py show)
    has_gunzip = any(re.search(r"gunzip|decompress", str(c.get("name", "")), re.I)
                     for c in plan.get("components") or [])
    errors = [f"{name} is gzipped and isn't FASTQ: add nf-core gunzip before its consumer, or list it in gz_ok "
              "if the consumer reads .gz (its meta.yml pattern ends in .gz)"
              for name in map(str, files)
              if name.lower().endswith(".gz") and not _FASTQ_GZ.search(name)
              and Path(name).name not in readers and name not in readers and not has_gunzip]
    return errors, []


def _data_warnings(plan: dict) -> list[str]:
    """Reject known data-dependent settings that cannot run on the planned data."""
    rows = plan.get("vst_eligible_rows")
    if not isinstance(rows, int):
        checks = plan.get("data_checks", {})
        deseq2 = checks.get("deseq2", {}) if isinstance(checks, dict) else {}
        rows = deseq2.get("vst_eligible_rows") if isinstance(deseq2, dict) else None
    eligible = isinstance(rows, int)
    if not eligible:
        rows = plan.get("expected_rows")
    if isinstance(rows, dict):
        row_values = list(rows.values())
    elif isinstance(rows, int):
        row_values = [rows]
    else:
        row_values = [c.get("expected_rows") for c in plan.get("components", [])
                      if isinstance(c.get("expected_rows"), int)]
    if isinstance(rows, int):
        row_values = [rows]
    row_values = [r for r in row_values if isinstance(r, int)]
    text = json.dumps(plan)
    match = re.search(r"vst_nsub[^0-9]*(\d+)", text)
    method = re.search(r"vs_method[^A-Za-z]*(rlog(?:,vst)?|vst)", text, re.I)
    if method and method.group(1).lower() == "rlog":
        return []
    nsub = int(match.group(1)) if match else (
        1000 if re.search(r"deseq2[/_]differential", text, re.I) else None)
    if nsub is not None and not eligible:
        # total rows are only an upper bound: round-3 A had 125 rows but 78 eligible for vst(nsub=100)
        return [f"vst_nsub={nsub} is unverified: record data_checks.deseq2.vst_eligible_rows (rows with mean "
                "normalized count > 5 in the session's DESeq2 object) or use --vs_method rlog"]
    # The pipeline's quantification differs from the session's (round 4: 79 eligible rows in the session, 78 on HPC),
    # so an nsub at the session's count has no margin. Require half of it, or rlog.
    if nsub is not None and row_values and nsub > min(row_values) // 2:
        return [f"vst_nsub={nsub} leaves no margin under the session's {min(row_values)} VST-eligible rows (the "
                f"pipeline's count can be lower); use --vs_method rlog or vst_nsub <= {min(row_values) // 2}"]
    if "vst_nsub" in text and not row_values:
        return ["data-dependent parameters require gate review: vst_nsub vs gene rows, min counts vs samples"]
    return []


def _local_data_runs(plan: dict, components: list[dict], reports: Path | None) -> tuple[list[dict], list[str]]:
    """Read recorded local-module smoke results; absence is an explicit handoff warning."""
    recorded = {}
    if reports and (reports / "local-module-runs.json").is_file():
        try:
            recorded = {row["module"]: row for row in json.loads(
                (reports / "local-module-runs.json").read_text())}
        except (ValueError, KeyError, TypeError):
            recorded = {}
    rows, warnings = [], []
    for component in components:
        if component.get("kind") != "local":
            continue
        row = recorded.get(component["name"], {"module": component["name"], "ok": False,
                                                   "status": "missing"})
        rows.append(row)
        if row.get("status") == "missing" or "ok" not in row:
            warnings.append(f"{component['name']}: local module data run missing; pre-HPC gate/handoff warning")
    return rows, warnings


def _runtime_version(pins: list[str], library: str) -> str | None:
    for pin in pins:
        match = re.match(r"(?:[^:]+::)?([^=<>!\s]+)=+([^=<>!\s]+)", pin.strip())
        if match and match.group(1).lower() == library.lower():
            version = _VERSION_RE.search(match.group(2))
            return version.group(1) if version else None
    return None


def _module_pins(repo: Path, relative: str, index: dict) -> list[str]:
    module = repo / relative
    env = module.parent / "environment.yml"
    if env.is_file():
        return [m.group(1) for m in re.finditer(r"^\s*-\s+([^#\n]+)", env.read_text(), re.M)]
    parts = Path(relative).parts
    if len(parts) >= 4 and parts[0] == "modules" and parts[1] == "nf-core":
        name = "/".join(parts[2:-1])  # modules/nf-core/scanpy/pca/main.nf -> scanpy/pca
        for component in index.get("components", []):
            if component.get("kind") == "module" and component.get("name") == name:
                return component.get("conda", [])
    return []


def _serialized_library(pattern: str) -> str | None:
    suffix = pattern.lower()
    if re.search(r"(?:^|[*.])h5ad$", suffix):
        return "anndata"
    if re.search(r"(?:^|[*.])rds$", suffix):
        return "r-base"
    if re.search(r"(?:^|[*.])(?:pickle|pkl)$", suffix):
        return "python"
    return None


def _serialized_compatibility(plan: dict, wiring: dict | None, repo: Path | None,
                              index: dict | None) -> tuple[list[str], list[str]]:
    """Compare serializer pins on actual wiring edges, returning errors and warnings."""
    if wiring is None or repo is None or index is None:
        return _serialized_compatibility_errors_from_plan(plan), []
    errors, warnings = [], []
    for edge in wiring.get("edges", []):
        library = _serialized_library(str(edge.get("pattern") or ""))
        if not library:
            continue
        producer_pins = _module_pins(repo, edge["producer_module"], index)
        consumer_pins = _module_pins(repo, edge["consumer_module"], index)
        producer_version = _runtime_version(producer_pins, library)
        if not producer_version and library == "anndata":
            producer_version = _runtime_version(producer_pins, "scanpy")
            producer_library = "scanpy" if producer_version else library
        else:
            producer_library = library
        consumer_version = _runtime_version(consumer_pins, producer_library)
        if not consumer_version and library == "anndata":
            consumer_version = _runtime_version(consumer_pins, "scanpy")
        label = f"{edge.get('file')}: {edge.get('producer')}.out.{edge.get('emit')} -> {edge.get('consumer')}"
        if not producer_version or not consumer_version:
            missing = edge.get("producer") if not producer_version else edge.get("consumer")
            warnings.append(f"serialized edge {label} is unverified: {missing} has no pinned {producer_library}")
        elif producer_version != consumer_version:
            errors.append(f"serialized edge {label} ({edge.get('pattern')}): {edge.get('producer')} pins "
                          f"{producer_library} {producer_version}, {edge.get('consumer')} pins {producer_library} "
                          f"{consumer_version}; connected steps must use one compatible major.minor version")
    return sorted(errors), sorted(warnings)


def _serialized_compatibility_errors_from_plan(plan: dict) -> list[str]:
    """Compatibility fallback for callers without a composed repository."""
    processes = {p.get("name"): p for p in plan.get("processes", []) if p.get("name")}
    errors = []
    for edge in plan.get("serialized_objects", []):
        producer = processes.get(edge.get("producer"))
        library = edge.get("library")
        if not producer or not library:
            continue
        producer_version = next((m.group(1) for value in producer.get("runtime_versions", {}).values()
                                 for m in [_VERSION_RE.search(str(value))] if m), None)
        for name in edge.get("consumers", []):
            consumer = processes.get(name, {})
            consumer_version = next((m.group(1) for value in consumer.get("runtime_versions", {}).values()
                                     for m in [_VERSION_RE.search(str(value))] if m), None)
            if producer_version and consumer_version and producer_version != consumer_version:
                errors.append(f"serialized {edge.get('pattern', library)}: {producer['name']} pins "
                              f"{library} {producer_version}, but {name} pins {library} {consumer_version}")
    return sorted(errors)


def _serialized_compatibility_errors(plan: dict, wiring: dict | None = None,
                                     repo: Path | None = None, index: dict | None = None) -> list[str]:
    return _serialized_compatibility(plan, wiring, repo, index)[0]


def exit_code(summary: dict) -> int:
    if not summary["ok"]:
        return 1
    return 3 if any(v["source"] == "container-only" for v in summary["vendored"]) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--check-plan", action="store_true",
                        help="before the gate: run only the plan-level checks (gzipped inputs, data-dependent "
                             "parameters); writes nothing")
    parser.add_argument("--module-author-dir", type=Path, help="pipeline-module-author's Skill Directory")
    parser.add_argument("--validate-dir", type=Path, help="pipeline-hpc-validate's Skill Directory")
    parser.add_argument("--offline", action="store_true",
                        help="never call GitHub (also NEXTFLOW_SKILLS_OFFLINE=1): a shipped snapshot if present, then container-only")
    parser.add_argument("--reports", type=Path, help="record compose timing events in this reports directory")
    parser.add_argument("--timing-log", type=Path, help="timing log path, for read-only reports mounts")
    args = parser.parse_args(argv)
    try:
        plan = json.loads(args.plan.read_text())
        if args.check_plan:
            errors = {"gzipped_inputs": _gzipped_input_audit(plan, {})[0], "data_dependent_parameters": _data_warnings(plan)}
            warnings = _gzipped_input_audit(plan, {})[1]
            print(json.dumps({"ok": not any(errors.values()), "checks": errors, "warnings": warnings}, indent=2))
            return 0 if not any(errors.values()) else 1
        if args.repo is None:
            raise ComposeError("--repo is required unless --check-plan")
        summary = compose(plan, args.repo, args.module_author_dir, args.validate_dir,
                          base=args.plan.resolve().parent, offline=args.offline,
                          reports=args.reports, timing_log=args.timing_log)
    except (ComposeError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, indent=2))
        print(f"compose.py: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2))
    return exit_code(summary)


if __name__ == "__main__":
    raise SystemExit(main())
