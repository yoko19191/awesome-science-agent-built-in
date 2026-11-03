#!/usr/bin/env python3
"""Run dual-caller SV analysis and require complete report finalization."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import sys
import zipfile

import science_contract


SCRIPTS = pathlib.Path(__file__).resolve().parent
PACKAGE = SCRIPTS.parent
REPORT_NAME = "report_long-read-structural-variants.pdf"
INFOGRAPHIC_NAME = "workflow_infographic_long_read_sv.png"
SCIENCE_OUTPUTS = science_contract.OUTPUTS
DERIVED_OUTPUTS = ("report_facts.json", "report_content.json", "report_structure.json")
MAX_PUBLISHED_ARTIFACTS = 100
OPTIONAL_EVIDENCE_DIRECTORY = "benchmark_addon"
OPTIONAL_EVIDENCE_ARCHIVE = "benchmark_addon_evidence.zip"
OPTIONAL_EVIDENCE_MANIFEST = "benchmark_addon_evidence_manifest.json"


def _science_argv(args: argparse.Namespace) -> list[str]:
    argv = [
        sys.executable,
        str(SCRIPTS / "run_sv_science.py"),
        "--execution-mode", "analysis",
        "--input-selection", args.input_selection,
        "--bam", args.bam,
        "--reference", args.reference,
        "--run-json", args.run_json,
        "--identity-check", args.identity_check,
        "--second-caller", args.second_caller,
        "--platform", args.platform,
        "--threads", str(args.threads),
        "--out-root", args.out_root,
        "--evidence-tier", args.evidence_tier,
    ]
    if args.second_caller_vcf:
        argv.extend(("--second-caller-vcf", args.second_caller_vcf))
    return argv


def _canonical_infographic(source: pathlib.Path, report_qc) -> pathlib.Path:
    if not source.is_file() or source.stat().st_size < 5_000:
        raise report_qc.GateFailure(
            "a non-empty GenerateImage workflow infographic is required before report finalization"
        )
    destination = report_qc.staged_copy(source, INFOGRAPHIC_NAME)
    report_qc.record_generated_infographic(INFOGRAPHIC_NAME, log_path="qc_run_log.json")
    return destination


def _render_report(args: argparse.Namespace, report_qc) -> tuple[list[str], dict]:
    plan = report_qc.report_render_plan()
    executed = ["scripts/run_sv_science.py", "scripts/report_content.py"]
    if not args.provider_rendered_report:
        raise report_qc.GateFailure(
            f"render report_content.json through {plan['provider']} and pass "
            "--provider-rendered-report"
        )
    report_path = pathlib.Path(args.provider_rendered_report).expanduser().resolve(strict=True)
    report_qc.staged_copy(report_path, REPORT_NAME)
    return executed, plan


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compact_optional_evidence(out: pathlib.Path, report_qc) -> list[str]:
    source = out / OPTIONAL_EVIDENCE_DIRECTORY
    if not source.exists():
        archive = out / OPTIONAL_EVIDENCE_ARCHIVE
        manifest_path = out / OPTIONAL_EVIDENCE_MANIFEST
        if not archive.exists() and not manifest_path.exists():
            return []
        if not archive.is_file() or not manifest_path.is_file():
            raise report_qc.GateFailure("optional evidence archive and manifest must appear together")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_keys = {
            "schema", "source_directory", "archive", "archive_sha256", "file_count", "entries"
        }
        if (
            not isinstance(manifest, dict)
            or set(manifest) != expected_keys
            or manifest.get("schema") != "phylo-sv-optional-evidence/1"
            or manifest.get("source_directory") != OPTIONAL_EVIDENCE_DIRECTORY
            or manifest.get("archive") != OPTIONAL_EVIDENCE_ARCHIVE
            or manifest.get("archive_sha256") != _sha256(archive)
            or not isinstance(manifest.get("entries"), list)
            or manifest.get("file_count") != len(manifest.get("entries", []))
        ):
            raise report_qc.GateFailure("optional evidence archive manifest is invalid")
        entries = manifest["entries"]
        if not all(
            isinstance(entry, dict) and set(entry) == {"path", "bytes", "sha256"}
            for entry in entries
        ):
            raise report_qc.GateFailure("optional evidence archive entries are invalid")
        try:
            with zipfile.ZipFile(archive) as bundle:
                if bundle.namelist() != [entry["path"] for entry in entries]:
                    raise report_qc.GateFailure("optional evidence archive inventory is invalid")
                for entry in entries:
                    data = bundle.read(entry["path"])
                    if (
                        len(data) != entry["bytes"]
                        or hashlib.sha256(data).hexdigest() != entry["sha256"]
                    ):
                        raise report_qc.GateFailure("optional evidence archive content is invalid")
        except (OSError, ValueError, zipfile.BadZipFile, KeyError) as exc:
            raise report_qc.GateFailure(f"optional evidence archive cannot be verified: {exc}") from exc
        return [OPTIONAL_EVIDENCE_ARCHIVE, OPTIONAL_EVIDENCE_MANIFEST]
    if not source.is_dir() or source.is_symlink():
        raise report_qc.GateFailure(
            f"optional evidence path must be a real directory: {OPTIONAL_EVIDENCE_DIRECTORY}"
        )
    members = sorted(path for path in source.rglob("*") if path.is_file())
    if not members:
        raise report_qc.GateFailure("optional benchmark evidence directory is empty")
    if any(path.is_symlink() for path in source.rglob("*")):
        raise report_qc.GateFailure("optional benchmark evidence must not contain symbolic links")

    archive = out / OPTIONAL_EVIDENCE_ARCHIVE
    archive_tmp = archive.with_suffix(archive.suffix + ".tmp")
    entries = []
    try:
        with zipfile.ZipFile(
            archive_tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as bundle:
            for member in members:
                relative = member.relative_to(source).as_posix()
                data = member.read_bytes()
                info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                bundle.writestr(info, data, compresslevel=9)
                entries.append(
                    {"path": relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                )
        with zipfile.ZipFile(archive_tmp) as bundle:
            if bundle.namelist() != [entry["path"] for entry in entries]:
                raise report_qc.GateFailure("optional evidence archive inventory changed while writing")
            for entry in entries:
                if hashlib.sha256(bundle.read(entry["path"])).hexdigest() != entry["sha256"]:
                    raise report_qc.GateFailure(
                        f"optional evidence archive content mismatch: {entry['path']}"
                    )
        archive_tmp.replace(archive)
    finally:
        archive_tmp.unlink(missing_ok=True)

    manifest = {
        "schema": "phylo-sv-optional-evidence/1",
        "source_directory": OPTIONAL_EVIDENCE_DIRECTORY,
        "archive": OPTIONAL_EVIDENCE_ARCHIVE,
        "archive_sha256": _sha256(archive),
        "file_count": len(entries),
        "entries": entries,
    }
    (out / OPTIONAL_EVIDENCE_MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    shutil.rmtree(source)
    return [OPTIONAL_EVIDENCE_ARCHIVE, OPTIONAL_EVIDENCE_MANIFEST]


def _assert_published_inventory(out: pathlib.Path, report_qc, *, pending_files: int) -> int:
    published = sum(path.is_file() for path in out.rglob("*")) + pending_files
    if published > MAX_PUBLISHED_ARTIFACTS:
        raise report_qc.GateFailure(
            f"published result inventory has {published} files; deterministic QA supports at most "
            f"{MAX_PUBLISHED_ARTIFACTS}"
        )
    return published


def _review_report(args: argparse.Namespace, report_qc) -> list[str]:
    report = pathlib.Path(args.out_root).resolve() / REPORT_NAME
    try:
        import pypdf
    except ImportError as exc:
        raise report_qc.GateFailure("pypdf is required to enumerate PDF review pages") from exc
    page_count = len(pypdf.PdfReader(str(report)).pages)
    review_pages = [f"report_review/pages/page_{number}.png" for number in range(1, page_count + 1)]
    if args.visual_review_state == "pass":
        attestation = (
            "All rendered pages were inspected by the caller. "
            + args.visual_review_notes.strip()
        )
    elif args.visual_review_state == "fail":
        attestation = (
            "The caller reported a visual defect. " + args.visual_review_notes.strip()
        )
    else:
        attestation = (
            "In-run visual quality was not independently evaluable. "
            + args.visual_review_notes.strip()
        )
    report_qc.record_pdf_review(
        REPORT_NAME,
        "report_review/report.txt",
        review_pages,
        list(range(1, page_count + 1)) if args.visual_review_state == "pass" else [],
        attestation,
        review_state=args.visual_review_state,
        log_path="qc_run_log.json",
    )
    return ["report_review/report.txt", *review_pages]


def _verify_selected_inputs(args: argparse.Namespace) -> dict:
    sys.path.insert(0, str(SCRIPTS))
    import input_selection
    paths = {"bam": args.bam, "reference": args.reference, "run_json": args.run_json,
             "upstream_identity_check": args.identity_check}
    if args.second_caller == "user_vcf":
        paths["second_caller_vcf"] = args.second_caller_vcf
    return input_selection.assert_selected(args.input_selection, paths)


def run(args: argparse.Namespace) -> pathlib.Path | None:
    out = pathlib.Path(args.out_root).expanduser().resolve()
    os.environ["BIOMNI_RESULTS"] = str(out)
    sys.path.insert(0, str(SCRIPTS))
    import report_qc

    selected = _verify_selected_inputs(args)

    if args.resume:
        science_contract.verify_receipt(
            out, input_selection_sha256=selected["manifest_sha256"],
        )
    else:
        report_qc.run_bundled(
            _science_argv(args),
            "scripts/run_sv_science.py",
            list(SCIENCE_OUTPUTS),
            invocation_id="sv-science",
        )
    figures = report_qc.assert_figures("figures/manifest.json")
    if not args.resume:
        return None
    _canonical_infographic(
        pathlib.Path(args.infographic_file).expanduser().resolve(strict=True), report_qc
    )
    report_qc.run_bundled(
        [
            sys.executable,
            str(SCRIPTS / "report_content.py"),
            "--out-root", str(out),
            "--contract", str(PACKAGE / "skill_contract.json"),
        ],
        "scripts/report_content.py",
        list(DERIVED_OUTPUTS),
        invocation_id="report-content",
    )
    executed_files, _ = _render_report(args, report_qc)
    review_outputs = _review_report(args, report_qc)
    optional_evidence_outputs = _compact_optional_evidence(out, report_qc)
    outputs = [
        "input_selection.json",
        *SCIENCE_OUTPUTS,
        *DERIVED_OUTPUTS,
        INFOGRAPHIC_NAME,
        REPORT_NAME,
        *review_outputs,
        *optional_evidence_outputs,
    ]
    pending_receipt = int(not (out / "run_receipt.json").is_file())
    _assert_published_inventory(out, report_qc, pending_files=pending_receipt)
    receipt = report_qc.write_receipt(
        REPORT_NAME,
        figures,
        bundled_files=tuple(executed_files),
        outputs=tuple(outputs),
        infographics=(INFOGRAPHIC_NAME,),
        style_transcript="execution_trace/transcript.jsonl",
        path="run_receipt.json",
        qc_run_log="qc_run_log.json",
        contract=str(PACKAGE / "skill_contract.json"),
        strict=True,
    )
    return receipt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--execution-mode", required=True, choices=["analysis"])
    result.add_argument("--input-selection", required=True)
    result.add_argument("--bam", required=True)
    result.add_argument("--reference", required=True)
    result.add_argument("--run-json", required=True)
    result.add_argument("--identity-check", required=True)
    result.add_argument("--second-caller", required=True, choices=["cutesv", "user_vcf"])
    result.add_argument("--second-caller-vcf")
    result.add_argument("--platform", default="ont", choices=["ont", "hifi"])
    result.add_argument("--threads", type=int, default=1)
    result.add_argument("--out-root", required=True)
    result.add_argument("--evidence-tier", default="hypothesis_generating")
    result.add_argument("--infographic-file")
    result.add_argument("--resume", action="store_true",
                        help="resume after GenerateImage from sv/infographic_prompt.txt")
    result.add_argument("--provider-rendered-report")
    result.add_argument(
        "--workspace-dir", default=os.environ.get("BIOMNI_WORKSPACE", "/workspace")
    )
    result.add_argument(
        "--visual-review-state", choices=["pass", "fail", "not_evaluable"], default="not_evaluable"
    )
    result.add_argument(
        "--visual-review-notes",
        default="Rendered pages and extracted text are retained for post-run visual acceptance.",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.second_caller == "user_vcf" and not args.second_caller_vcf:
        parser().error("--second-caller user_vcf requires --second-caller-vcf")
    if args.second_caller == "cutesv" and args.second_caller_vcf:
        parser().error("--second-caller-vcf is accepted only with --second-caller user_vcf")
    if args.resume and not args.infographic_file:
        parser().error("--resume requires --infographic-file generated from sv/infographic_prompt.txt")
    if args.resume and not args.provider_rendered_report:
        parser().error(
            "--resume requires --provider-rendered-report rendered through the selected report provider"
        )
    if not args.resume and args.infographic_file:
        parser().error("the science stage must run first; omit --infographic-file, then resume")
    try:
        receipt = run(args)
    except (OSError, ValueError, RuntimeError, AssertionError) as exc:
        print(f"FINALIZATION ERROR: {exc}", file=sys.stderr)
        return 5
    if receipt is None:
        print(json.dumps({"status": "awaiting_infographic",
                          "prompt": "sv/infographic_prompt.txt",
                          "claims": "sv/infographic_claims.json",
                          "next": "GenerateImage from the exact prompt, then rerun with --resume --infographic-file"}))
        return 0
    print(json.dumps({"status": "complete", "receipt": str(receipt), "report": REPORT_NAME}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
