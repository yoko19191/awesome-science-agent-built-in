#!/usr/bin/env python3
"""Freeze a report provider, render one PDF, and finalize its conditional lineage."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unicodedata


SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import report_content  # noqa: E402
import report_qc  # noqa: E402


REPORT_NAME = "report_spatial_transcriptomics.pdf"
PLAN_NAME = "report_render_plan.json"
RENDER_PROVENANCE_NAME = "report_render_provenance.json"
SCIENTIFIC_VISUAL_MANIFEST = "scientific_visual_manifest.json"
VISUAL_MANIFEST = "visual_manifest.json"
PLAN_SCHEMA = "spatial-report-render-plan/1"
RENDER_PROVENANCE_SCHEMA = "spatial-report-render-provenance/1"
VISUAL_MANIFEST_SCHEMA = "spatial-visual-manifest/2"
CONTROLLER_SCRIPTS = (
    "scripts/report_content.py",
    "scripts/report_style.py",
    "scripts/finalize_report.py",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_evidence(path: Path, *, display_path: str | None = None) -> dict:
    return {
        "path": display_path or str(path),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _package_script_evidence(relpaths: tuple[str, ...]) -> list[dict]:
    evidence = []
    for rel in relpaths:
        path = SKILL_ROOT / rel
        if not path.is_file():
            raise RuntimeError(f"packaged presentation controller is missing: {path}")
        evidence.append(_file_evidence(path, display_path=rel))
    return evidence


def _content_evidence(results_dir: Path) -> tuple[dict, dict]:
    content_path, structure_path, content = report_content.load_verified_report_content(results_dir)
    return content, {
        "content": _file_evidence(content_path, display_path=content_path.name),
        "structure": _file_evidence(structure_path, display_path=structure_path.name),
    }


def _style_snapshot(resolved: dict) -> dict:
    return {
        "provider": resolved["provider"],
        "selection": resolved["selection"],
        "selection_evidence": resolved["selection_evidence"],
        "profile": resolved["profile"],
        "profile_source": resolved["profile_source"],
        "profile_source_evidence": resolved["profile_source_evidence"],
        "render_with_bundled": resolved["render_with_bundled"],
    }


def _expected_plan(
    results_dir: Path,
    *,
    transcript: str | Path | None,
    roots: tuple[Path, ...] | None,
) -> tuple[dict, dict, dict]:
    content, bound_content = _content_evidence(results_dir)
    resolved = report_qc.report_render_plan(transcript=transcript, roots=roots)
    branch = "packaged_default" if resolved["render_with_bundled"] else "explicit_provider"
    renderer = (
        {
            "kind": "packaged_default_renderer",
            "source": _file_evidence(
                SKILL_ROOT / "scripts/spatial_report.py",
                display_path="scripts/spatial_report.py",
            ),
            "material_dependency": "reportlab",
        }
        if resolved["render_with_bundled"]
        else {
            "kind": "installed_provider",
            "source": resolved["profile_source_evidence"],
        }
    )
    plan = {
        "schema": PLAN_SCHEMA,
        "report": REPORT_NAME,
        "render_branch": branch,
        "report_content": bound_content,
        "provider": _style_snapshot(resolved),
        "controllers": _package_script_evidence(CONTROLLER_SCRIPTS),
        "renderer": renderer,
    }
    return plan, resolved, content


def create_render_plan(
    results_dir: str | Path,
    *,
    transcript: str | Path | None = None,
    roots: tuple[Path, ...] | None = None,
) -> Path:
    """Write once, or confirm byte-equivalent, so provider/source selection cannot drift."""
    root = Path(results_dir)
    plan, _resolved, _content = _expected_plan(root, transcript=transcript, roots=roots)
    destination = root / PLAN_NAME
    serialized = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    if destination.exists() or destination.is_symlink():
        if not destination.is_file() or destination.read_text(encoding="utf-8") != serialized:
            raise RuntimeError(
                f"{PLAN_NAME} already exists and disagrees with the current immutable selection"
            )
        return destination
    destination.write_text(serialized, encoding="utf-8")
    return destination


def load_verified_render_plan(
    results_dir: str | Path,
    *,
    transcript: str | Path | None = None,
    roots: tuple[Path, ...] | None = None,
) -> tuple[dict, dict, dict]:
    root = Path(results_dir)
    plan_path = root / PLAN_NAME
    if not plan_path.is_file():
        raise RuntimeError(f"create {PLAN_NAME} before rendering the PDF")
    try:
        frozen = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot read {PLAN_NAME}: {exc}") from exc
    expected, resolved, content = _expected_plan(root, transcript=transcript, roots=roots)
    if frozen != expected:
        raise RuntimeError(
            "the frozen report provider, installed provider source, packaged controller, "
            "or report content changed after report_render_plan.json was written"
        )
    return frozen, resolved, content


def _load_scientific_visual_manifest(results_dir: Path, content: dict) -> dict:
    manifest_path = results_dir / SCIENTIFIC_VISUAL_MANIFEST
    if not manifest_path.is_file():
        raise RuntimeError(f"scientific figure lineage is missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot read {SCIENTIFIC_VISUAL_MANIFEST}: {exc}") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "spatial-scientific-visual-manifest/1"
        or manifest.get("generated_by") != "run_spatial.run_prepare"
    ):
        raise RuntimeError(f"{SCIENTIFIC_VISUAL_MANIFEST} has an invalid producer or schema")
    pinned = {
        item.get("path"): item.get("sha256")
        for item in manifest.get("artifacts", [])
        if isinstance(item, dict)
    }
    expected = {
        item["file"]: item["sha256"]
        for item in content["figure_placements"]
        if item.get("file")
    }
    if pinned != expected:
        raise RuntimeError(
            f"{SCIENTIFIC_VISUAL_MANIFEST} does not exactly bind the report-content figures"
        )
    for rel, digest in expected.items():
        path = results_dir / rel
        if not path.is_file() or _sha256(path) != digest:
            raise RuntimeError(f"scientific figure changed after preparation: {rel}")
    return {
        "path": SCIENTIFIC_VISUAL_MANIFEST,
        "bytes": manifest_path.stat().st_size,
        "sha256": _sha256(manifest_path),
    }


def _extract_pdf_text(report: Path) -> str:
    try:
        import pypdf

        reader = pypdf.PdfReader(str(report))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except ImportError:
        executable = shutil.which("pdftotext")
        if executable is None:
            raise RuntimeError("neither pypdf nor pdftotext is available for content verification")
        completed = subprocess.run(
            [executable, str(report), "-"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode:
            raise RuntimeError(f"pdftotext failed: {completed.stderr[-300:]}")
        text = completed.stdout
    except Exception as exc:
        raise RuntimeError(f"cannot parse provider-rendered PDF: {exc}") from exc
    if not text.strip():
        raise RuntimeError("provider-rendered PDF contains no extractable text")
    return unicodedata.normalize("NFKC", text)


def assert_pdf_content(report: Path, content: dict) -> dict:
    """Require every bound section and claim label/value in the candidate before publication."""
    text = _extract_pdf_text(report)
    normalized = re.sub(r"\s+", " ", text).casefold()
    lines = [re.sub(r"\s+", " ", line).strip().casefold() for line in text.splitlines()]
    section_positions = []
    missing_sections = []
    for section in content["required_sections"]:
        try:
            section_positions.append(lines.index(section.casefold()))
        except ValueError:
            missing_sections.append(section)
    missing_claims = []
    for claim in content["required_claims"]:
        label = str(claim["label"]).casefold()
        value = str(claim["value"]).casefold()
        if label not in normalized or value not in normalized:
            missing_claims.append(claim)
    if missing_sections or missing_claims:
        raise RuntimeError(
            f"provider-rendered PDF is incomplete; missing sections={missing_sections!r}, "
            f"missing bound claims={missing_claims!r}"
        )
    if section_positions != sorted(section_positions):
        raise RuntimeError("provider-rendered PDF does not preserve the required section order")
    return {
        "required_sections": list(content["required_sections"]),
        "required_claims": list(content["required_claims"]),
        "extracted_characters": len(text),
    }


def staged_copy(source: str | Path, destination: str | Path) -> None:
    """Publish a complete PDF with exclusive-create semantics; never replace prior bytes."""
    source = Path(source)
    destination = Path(destination)
    if not destination.is_absolute():
        destination = report_qc.RESULTS / destination
    if destination.exists() or destination.is_symlink():
        raise RuntimeError(f"refusing to overwrite pre-existing canonical PDF: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with source.open("rb") as reader, destination.open("xb") as writer:
            created = True
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
    except Exception:
        if created and destination.exists():
            destination.unlink()
        raise
    if _sha256(source) != _sha256(destination):
        destination.unlink()
        raise RuntimeError("canonical PDF copy changed bytes")


def _write_new_json(path: Path, payload: dict) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to overwrite pre-existing finalization artifact: {path}")
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def finalize(
    results_dir: str | Path,
    *,
    provider_rendered_report: str | Path | None = None,
    workspace_dir: str | Path = "/workspace",
    infographic: str | Path | None = None,
    transcript: str | Path | None = None,
    roots: tuple[Path, ...] | None = None,
) -> Path:
    """Validate a staged PDF fully, then publish it exactly once at the results root."""
    root = Path(results_dir)
    canonical = root / REPORT_NAME
    final_sidecars = (root / RENDER_PROVENANCE_NAME, root / VISUAL_MANIFEST)
    if canonical.exists() or canonical.is_symlink():
        raise RuntimeError(f"refusing to overwrite pre-existing canonical PDF: {canonical}")
    if any(path.exists() or path.is_symlink() for path in final_sidecars):
        raise RuntimeError("refusing to reuse pre-existing report finalization sidecars")

    plan, resolved, content = load_verified_render_plan(
        root, transcript=transcript, roots=roots
    )
    prepared_manifest = _load_scientific_visual_manifest(root, content)
    if plan["render_branch"] == "packaged_default":
        if provider_rendered_report is not None:
            raise RuntimeError("the package-default branch does not accept a provider-rendered PDF")
        import spatial_report

        workspace = Path(workspace_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        candidate = workspace / (REPORT_NAME + ".candidate")
        if candidate.exists() or candidate.is_symlink():
            raise RuntimeError(f"refusing to overwrite pre-existing staged PDF: {candidate}")
        candidate = spatial_report.build_report(
            root,
            out_pdf=candidate,
            infographic_path=infographic,
            content_path=root / "report_content.json",
        )
        presentation_dependency = {
            "module": "reportlab",
            "version": importlib.metadata.version("reportlab"),
        }
    else:
        if provider_rendered_report is None:
            raise RuntimeError(
                "the explicit provider must render the frozen report_content.json before finalization"
            )
        candidate = Path(provider_rendered_report)
        if candidate.resolve() == canonical.resolve():
            raise RuntimeError("the explicit provider must render to a staging path, not the canonical PDF")
        if not candidate.is_file() or candidate.stat().st_size == 0:
            raise RuntimeError(f"provider-rendered PDF is missing or empty: {candidate}")
        presentation_dependency = {
            "provider_source": resolved["profile_source_evidence"],
        }

    content_check = assert_pdf_content(candidate, content)
    style_check = report_qc.assert_report_styled(candidate, resolved_style=resolved)
    candidate_sha = _sha256(candidate)
    figure_evidence = [
        {
            "path": item["file"],
            "bytes": item["bytes"],
            "sha256": item["sha256"],
            "role": "figure",
        }
        for item in content["figure_placements"] if item.get("file")
    ]
    render_provenance = {
        "schema": RENDER_PROVENANCE_SCHEMA,
        "report": {"path": REPORT_NAME, "bytes": candidate.stat().st_size, "sha256": candidate_sha},
        "render_branch": plan["render_branch"],
        "provider": plan["provider"],
        "renderer": plan["renderer"],
        "presentation_dependency": presentation_dependency,
        "controllers": plan["controllers"],
        "report_content": plan["report_content"],
        "scientific_sources": content["sources"],
        "scientific_visual_manifest": prepared_manifest,
        "figures": figure_evidence,
        "content_check": content_check,
        "style_check": style_check,
    }

    staged_copy(candidate, canonical)
    try:
        _write_new_json(root / RENDER_PROVENANCE_NAME, render_provenance)
        visual_manifest = {
            "schema": VISUAL_MANIFEST_SCHEMA,
            "generated_by": "finalize_report.finalize",
            "render_branch": plan["render_branch"],
            "provider": resolved["provider"],
            "report_content_sha256": plan["report_content"]["content"]["sha256"],
            "render_provenance": {
                "path": RENDER_PROVENANCE_NAME,
                "sha256": _sha256(root / RENDER_PROVENANCE_NAME),
            },
            "scientific_visual_manifest": prepared_manifest,
            "artifacts": [
                *figure_evidence,
                {
                    "path": REPORT_NAME,
                    "bytes": canonical.stat().st_size,
                    "sha256": _sha256(canonical),
                    "role": "report",
                },
            ],
        }
        _write_new_json(root / VISUAL_MANIFEST, visual_manifest)
    except Exception:
        for path in (root / VISUAL_MANIFEST, root / RENDER_PROVENANCE_NAME, canonical):
            if path.exists() and path.is_file():
                path.unlink()
        raise
    return canonical


def _roots(values: list[str] | None) -> tuple[Path, ...] | None:
    return tuple(Path(value) for value in values) if values else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "finalize"):
        command = sub.add_parser(name)
        command.add_argument("--results-dir", required=True)
        command.add_argument("--transcript")
        command.add_argument("--provider-root", action="append")
    finalize_parser = sub.choices["finalize"]
    finalize_parser.add_argument("--provider-rendered-report")
    finalize_parser.add_argument("--workspace-dir", default=os.environ.get("BIOMNI_WORKSPACE", "/workspace"))
    finalize_parser.add_argument("--infographic")
    args = parser.parse_args(argv)
    roots = _roots(args.provider_root)
    if args.command == "plan":
        print(create_render_plan(args.results_dir, transcript=args.transcript, roots=roots))
        return 0
    print(
        finalize(
            args.results_dir,
            provider_rendered_report=args.provider_rendered_report,
            workspace_dir=args.workspace_dir,
            infographic=args.infographic,
            transcript=args.transcript,
            roots=roots,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
