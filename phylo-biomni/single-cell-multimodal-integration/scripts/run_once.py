#!/usr/bin/env python3
"""Run the SCMI scientific pipeline exactly once from an external run workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys

sys.dont_write_bytecode = True
SCRIPTS = pathlib.Path(__file__).resolve().parent
PACKAGE = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import report_qc  # noqa: E402
import input_selection  # noqa: E402


SEAL_SCHEMA = "single-cell-multimodal-one-shot-execution/1"
INVOCATION_ID = "single-cell-multimodal-run-all-once"
SEAL_NAME = "one_shot_execution.json"
INPUT_SELECTION_NAME = "input_selection.json"
SNAPSHOT_NAME = "attached_package_snapshot.json"
LOG_NAME = "qc_run_log.json"


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _outside_package(path: pathlib.Path, label: str) -> pathlib.Path:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(PACKAGE.resolve(strict=True))
    except ValueError:
        return resolved
    raise report_qc.GateFailure(f"{label} must be outside the immutable attached package: {resolved}")


def _load_runtime(config_path: pathlib.Path) -> tuple[dict, pathlib.Path, pathlib.Path]:
    import yaml

    config = _outside_package(config_path, "runtime config")
    if not config.is_file():
        raise report_qc.GateFailure(f"runtime config is missing: {config}")
    try:
        payload = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError) as exc:
        raise report_qc.GateFailure(f"cannot read runtime config: {config}") from exc
    project = payload.get("project") if isinstance(payload, dict) else None
    if not isinstance(project, dict):
        raise report_qc.GateFailure("runtime config has no project mapping")
    outdir = _outside_package(pathlib.Path(str(project.get("outdir", ""))), "project.outdir")
    results = _outside_package(
        pathlib.Path(str(project.get("results_dir", ""))), "project.results_dir"
    )
    if not str(project.get("outdir", "")).strip() or not str(project.get("results_dir", "")).strip():
        raise report_qc.GateFailure("project.outdir and project.results_dir must be explicit")
    if outdir == results:
        raise report_qc.GateFailure("project.outdir and project.results_dir must be separate")
    input_cfg = payload.get("input")
    if not isinstance(input_cfg, dict) or input_cfg.get("execution_mode") != "analysis":
        raise report_qc.GateFailure("input.execution_mode must be analysis")
    return payload, outdir, results


def _selected_inputs(payload: dict, config_path: pathlib.Path) -> list[dict]:
    input_cfg = payload["input"]
    files = input_cfg.get("files"); provenance = input_cfg.get("provenance")
    if not isinstance(files, dict) or not files or not isinstance(provenance, dict) or set(files) != set(provenance):
        raise report_qc.GateFailure("input.files and input.provenance require exact one-to-one roles")
    bundle = pathlib.Path(str(input_cfg.get("dir", ""))).expanduser().resolve(strict=True)
    if not bundle.is_dir(): raise report_qc.GateFailure("input.dir must be an explicit existing bundle directory")
    expected_names = {"filtered_matrix": "filtered_feature_bc_matrix.h5",
                      "raw_matrix": "raw_feature_bc_matrix.h5", "metadata": "metadata.csv",
                      "rna_matrix": "rna_matrix.h5", "atac_fragments": "atac_fragments.tsv.gz"}
    if not set(files).issubset(expected_names) or "metadata" not in files:
        raise report_qc.GateFailure("input.files contains an unsupported role or lacks metadata")
    items = [{"role": "run_config", "path": str(config_path), "origin": "uploaded",
              "requested_source": "user-supplied runtime configuration", "acquisition_token": "-"}]
    for role, path in files.items():
        resolved = pathlib.Path(str(path)).expanduser().resolve(strict=True)
        if resolved.parent != bundle or resolved.name != expected_names[role]:
            raise report_qc.GateFailure(f"input.files.{role} must explicitly name {bundle / expected_names[role]}")
        source = provenance[role]
        if not isinstance(source, dict): raise report_qc.GateFailure(f"input.provenance.{role} must be a mapping")
        items.append({"role": role, "path": str(resolved), "origin": source.get("origin"),
                      "requested_source": source.get("requested_source"),
                      "acquisition_token": source.get("acquisition", "")})
    return items


def _require_fresh_results(results: pathlib.Path) -> None:
    results.mkdir(parents=True, exist_ok=True)
    unexpected = []
    for path in results.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(results).as_posix()
        if relative.startswith("execution_trace/"):
            continue
        unexpected.append(relative)
    if unexpected:
        raise report_qc.GateFailure(
            "one-shot results root is not fresh; deletion/retry is prohibited (existing: "
            + ", ".join(sorted(unexpected)) + ")"
        )


def _science_inventory(results: pathlib.Path) -> list[dict]:
    excluded = {SNAPSHOT_NAME, LOG_NAME, SEAL_NAME, INPUT_SELECTION_NAME}
    files = []
    for path in results.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(results).as_posix()
        if relative in excluded or relative.startswith("execution_trace/"):
            continue
        files.append({"path": relative, "bytes": path.stat().st_size, "sha256": _sha256(path)})
    files.sort(key=lambda item: item["path"])
    if not files:
        raise report_qc.GateFailure("scientific run produced no sealable artifacts")
    return files


def _canonical_sha(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def run(config_path: pathlib.Path) -> dict:
    payload, outdir, results = _load_runtime(config_path)
    _require_fresh_results(results)
    outdir.mkdir(parents=True, exist_ok=True)
    input_cfg = payload["input"]
    selection_path, selection = input_selection.seal(
        requested_dataset=str(input_cfg.get("requested_dataset", "")),
        inputs=_selected_inputs(payload, config_path.resolve(strict=True)), result_root=results,
        selection_evidence=str(input_cfg.get("selection_evidence", "")), package_root=PACKAGE)
    input_selection.verify(selection_path)
    report_qc.RESULTS = results
    snapshot = report_qc.write_attached_package_snapshot(SNAPSHOT_NAME)
    contract = json.loads((PACKAGE / "skill_contract.json").read_text(encoding="utf-8"))
    expected_outputs = list((contract.get("execution") or {}).get("command_output_paths") or [])
    if not expected_outputs:
        raise report_qc.GateFailure("skill contract has no command_output_paths")
    command = ["bash", str(PACKAGE / "scripts/run_all.sh"), str(config_path.resolve(strict=True))]
    os.environ["SCMI_INPUT_SELECTION"] = str(selection_path)
    completed = report_qc.run_bundled(
        command,
        "scripts/run_all.sh",
        expected_outputs,
        invocation_id=INVOCATION_ID,
        log_path=LOG_NAME,
    )
    figures = report_qc.assert_figures("figures/manifest.json")
    facts_payload = results / "facts_payload.json"
    facts = json.loads(facts_payload.read_text(encoding="utf-8"))
    facts.update({"execution_mode": "analysis", "requested_dataset": selection["requested_dataset"],
                  "package_data_used": False, "input_selection_sha256": selection["manifest_sha256"]})
    facts_payload.write_text(json.dumps(facts, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_qc.write_facts_from_artifact(
        "report_facts.json",
        source="facts_payload.json",
        figures=figures,
        contract=str(PACKAGE / "skill_contract.json"),
    )
    package_outcome = report_qc.assert_attached_package_unchanged(SNAPSHOT_NAME)
    _, events = report_qc._load_qc_run_log(LOG_NAME)
    commands = [event for event in events if event.get("type") == "command"]
    if len(commands) != 1 or commands[0].get("invocation_id") != INVOCATION_ID:
        raise report_qc.GateFailure("one-shot launcher did not produce exactly one command event")
    seal = {
        "schema": SEAL_SCHEMA,
        "generated_by": "scripts/run_once.py",
        "config": {"path": str(config_path.resolve(strict=True)), "sha256": _sha256(config_path)},
        "outdir": str(outdir),
        "results_root": str(results.resolve(strict=True)),
        "package_snapshot": pathlib.Path(snapshot).name,
        "package_snapshot_sha256": _sha256(snapshot),
        "package_tree_sha256": package_outcome["after_tree_sha256"],
        "command_event": commands[0],
        "command_event_sha256": _canonical_sha(commands[0]),
        "scientific_artifacts": _science_inventory(results),
    }
    destination = results / SEAL_NAME
    try:
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(seal, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise report_qc.GateFailure("one-shot execution seal already exists; retry is prohibited") from exc
    if completed.stdout:
        print(completed.stdout, end="")
    return seal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    seal = run(pathlib.Path(args.config))
    print(json.dumps({"status": "science_complete_once", "seal": SEAL_NAME,
                      "scientific_artifacts": len(seal["scientific_artifacts"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
