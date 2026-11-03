#!/usr/bin/env python3
"""Static gate for analytical and lightweight general generated skill packages."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import pathlib
import re
import sys

sys.dont_write_bytecode = True

import scaffold_skill  # noqa: E402
import skill_spec  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
TEMPLATES = ROOT / "templates"
CANONICAL_KEYS = ("id", "name", "description", "category", "visibility", "starting-prompt")
FRONTMATTER_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
PLACEHOLDER_RE = re.compile(r"\b(?:TODO|TBD|FIXME)(?:\(author\))?\b|<[^>\n]*(?:fill|replace|todo)[^>\n]*>", re.I)
ANALYTICAL_TOP_LEVEL = {
    "SKILL.md", "deploy.yaml", "skill_spec.json", "package_manifest.json", "DATA_SOURCES.md",
    "LICENSE", "NOTICE", "scripts", "references", "assets", "templates",
}
GENERAL_TOP_LEVEL = {
    "SKILL.md", "deploy.yaml", "DATA_SOURCES.md", "LICENSE", "NOTICE", "scripts", "references",
    "assets", "templates",
}
CACHE_NAMES = {
    ".DS_Store", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".ipynb_checkpoints",
}
ENVIRONMENT_NAMES = {".venv", ".pixi", "node_modules", "uv.lock", "poetry.lock"}
RESIDUE_NAMES = {
    "run_receipt.json", "validation-summary.json", "style_selection.json", "report_review.json",
}
ANALYTICAL_ONLY_NAMES = {
    "skill_spec.json", "package_manifest.json", "run_skill.py", "report_qc.py", "eval.yaml",
    "validation-cases.json", "eval.py", "evaluator.py", "oracle.py",
}
EVALUATION_DIRECTORIES = {
    "eval", "evals", "evaluation", "fixtures", "held-out", "held_out", "tests",
}
EVALUATION_FILE_PREFIXES = (
    "test_", "expected_", "expected-", "truth_", "truth-", "ground_truth", "ground-truth",
)
EVALUATION_FILE_NAMES = {
    "eval.py", "eval.yaml", "eval.yml", "evaluator.py", "expected.csv", "expected.json",
    "ground-truth.json", "oracle.py", "truth.csv", "truth.json", "validation-cases.json",
}
EVALUATION_CODE_SUFFIXES = {".py", ".r", ".sh", ".js", ".ts"}
EVALUATION_DATA_SUFFIXES = {".csv", ".json", ".jsonl", ".tsv", ".txt", ".yaml", ".yml"}


def is_evaluation_material(relative: pathlib.Path) -> bool:
    """Recognize packaged evaluator paths while allowing explanatory Markdown."""
    if {part.lower() for part in relative.parts[:-1]} & EVALUATION_DIRECTORIES:
        return True
    name, stem, suffix = relative.name.lower(), relative.stem.lower(), relative.suffix.lower()
    if name in EVALUATION_FILE_NAMES:
        return True
    if suffix in EVALUATION_CODE_SUFFIXES and stem.endswith("_test"):
        return True
    return suffix in EVALUATION_CODE_SUFFIXES | EVALUATION_DATA_SUFFIXES and (
        name.startswith(EVALUATION_FILE_PREFIXES) or "held-out" in stem or "held_out" in stem
    )
REMOVED_MODULES = {
    "contract_v2.py", "analysis_contract.py", "evidence_contract.py", "smoke_skill.py",
    "receipt_validation.py", "freeze_skill.py", "runtime_support.py", "selfcheck.py",
    "validate_style_provider.py",
}


def _frontmatter(path: pathlib.Path) -> tuple[list[str], dict[str, str], list[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    errors: list[str] = []
    if not lines or lines[0] != "---":
        return [], {}, ["SKILL.md has no opening frontmatter delimiter"]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return [], {}, ["SKILL.md frontmatter is not closed"]
    keys: list[str] = []
    values: dict[str, str] = {}
    for line in lines[1:end]:
        match = FRONTMATTER_RE.fullmatch(line)
        if not match:
            errors.append(f"invalid frontmatter line: {line!r}")
            continue
        key, raw = match.groups()
        keys.append(key)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            errors.append(f"frontmatter value for {key!r} must be JSON-quoted")
            continue
        if not isinstance(value, str):
            errors.append(f"frontmatter value for {key!r} must be text")
        else:
            values[key] = value
    return keys, values, errors


def _python_errors(path: pathlib.Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return [f"Python does not parse: {path.name}: {exc}"]
    errors = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            function = node.exc.func
            if isinstance(function, ast.Name) and function.id == "NotImplementedError":
                errors.append(f"Python contains NotImplementedError: {path.name}")
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and node.value.value is Ellipsis:
            errors.append(f"Python contains standalone Ellipsis: {path.name}")
    return errors


def _content_errors(package: pathlib.Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(package.rglob("*")):
        relative = path.relative_to(package)
        if path.is_symlink() or not path.is_file() or relative.as_posix() in {
            "scripts/run_skill.py", "scripts/report_qc.py",
        }:
            continue
        if path.suffix == ".py":
            errors.extend(_python_errors(path))
        if path.suffix.lower() in {".md", ".py", ".r"} and PLACEHOLDER_RE.search(
            path.read_text(encoding="utf-8", errors="replace")
        ):
            errors.append(f"unfinished placeholder cannot ship: {relative.as_posix()}")
    return errors


def _load_runner(package: pathlib.Path):
    path = package / "scripts" / "run_skill.py"
    module_spec = importlib.util.spec_from_file_location("_phylo_canonical_runner_check", path)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"cannot import canonical runner: {path}")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def _shape_errors(package: pathlib.Path, allowed_top_level: set[str]) -> list[str]:
    errors: list[str] = []
    unknown = sorted(path.name for path in package.iterdir() if path.name not in allowed_top_level)
    if unknown:
        errors.append(f"undeclared top-level package residue: {unknown}")
    for path in sorted(package.rglob("*")):
        relative = path.relative_to(package)
        if "\\" in relative.as_posix():
            errors.append(f"unsafe package path cannot ship: {relative.as_posix()}")
        if path.is_symlink():
            errors.append(f"symlink cannot ship: {relative.as_posix()}")
        if path.name in CACHE_NAMES or path.suffix in {".pyc", ".pyo"}:
            errors.append(f"cache artifact cannot ship: {relative.as_posix()}")
        if path.name in ENVIRONMENT_NAMES:
            errors.append(f"project environment cannot ship: {relative.as_posix()}")
        if path.name == "SKILL.md" and relative.as_posix() != "SKILL.md":
            errors.append(f"nested SKILL.md cannot ship: {relative.as_posix()}")
        if path.name in RESIDUE_NAMES or path.name.endswith("-receipt.json") or path.name.endswith("_receipt.json"):
            errors.append(f"run-specific evidence cannot ship: {relative.as_posix()}")
        if (path.is_dir() and path.name.lower() in EVALUATION_DIRECTORIES) or (
            path.is_file() and is_evaluation_material(relative)
        ):
            errors.append(f"evaluation material cannot ship: {relative.as_posix()}")
    return errors


def _frontmatter_errors(package: pathlib.Path, skill_path: pathlib.Path) -> list[str]:
    keys, values, errors = _frontmatter(skill_path)
    if tuple(keys) != CANONICAL_KEYS:
        errors.append(f"frontmatter keys/order {tuple(keys)!r} != {CANONICAL_KEYS!r}")
    if values.get("name") != package.name:
        errors.append("SKILL.md name must match its package folder")
    if not skill_spec.ID_RE.fullmatch(values.get("name", "")):
        errors.append("SKILL.md name must use lowercase letters/numbers separated by single hyphens")
    if not skill_spec.SKILL_ID_RE.fullmatch(values.get("id", "")):
        errors.append("SKILL.md id must be skill_ followed by 32 lowercase hex characters")
    if values.get("category") not in skill_spec.CATEGORIES:
        errors.append("SKILL.md category is not a live catalog value")
    if values.get("visibility") not in skill_spec.VISIBILITIES:
        errors.append("SKILL.md visibility must be internal, public, or shared")
    for field in ("description", "starting-prompt"):
        if not values.get(field, "").strip():
            errors.append(f"SKILL.md {field} must be non-empty text")
    return errors


def _check_general(package: pathlib.Path, *, require_frozen: bool) -> list[str]:
    errors = _shape_errors(package, GENERAL_TOP_LEVEL)
    if require_frozen:
        errors.append("--require-frozen is available only for executable analytical packages")
    skill_path = package / "SKILL.md"
    if not skill_path.is_file():
        return [*errors, "general package needs root SKILL.md"]
    errors.extend(_frontmatter_errors(package, skill_path))
    if len(skill_path.read_text(encoding="utf-8").splitlines()) > 500:
        errors.append("general SKILL.md exceeds the 500-line limit")
    for path in sorted(package.rglob("*")):
        if path.is_symlink():
            continue
        relative = path.relative_to(package)
        if (
            path.name in ANALYTICAL_ONLY_NAMES
        ):
            errors.append(f"general package contains analytical-only machinery: {relative.as_posix()}")
    errors.extend(_content_errors(package))
    return errors


def _check_analytical(package: pathlib.Path, *, require_frozen: bool) -> list[str]:
    errors = _shape_errors(package, ANALYTICAL_TOP_LEVEL)
    skill_path = package / "SKILL.md"
    spec_path = package / "skill_spec.json"
    if not skill_path.is_file() or not spec_path.is_file():
        return [*errors, "analytical package needs root SKILL.md and skill_spec.json"]
    try:
        value = skill_spec.strict_json(spec_path)
    except (OSError, ValueError) as exc:
        return [*errors, f"skill_spec.json is unreadable: {exc}"]
    spec_errors = skill_spec.validate_spec(value)
    errors.extend(spec_errors)
    if spec_errors:
        errors.append("use references/repair-existing.md to re-specify this analysis; source bytes are not rewritten")
    if isinstance(value, dict) and value.get("slug") != package.name:
        errors.append("analytical package folder must match skill_spec.json slug")
    if isinstance(value, dict) and not spec_errors:
        if skill_path.read_bytes() != scaffold_skill.render_skill(value).encode("utf-8"):
            errors.append("analytical SKILL.md differs from the canonical rendering of skill_spec.json")
        for item in value.get("guidance", []):
            if isinstance(item, dict) and not (package / str(item.get("path", ""))).is_file():
                errors.append(f"declared guidance file is missing: {item.get('path')}")
    scripts = package / "scripts"
    runner = scripts / "run_skill.py"
    if not runner.is_file() or runner.read_bytes() != (TEMPLATES / "run_skill.py").read_bytes():
        errors.append("scripts/run_skill.py is missing or differs from the canonical runner")
    report = scripts / "report_qc.py"
    report_required = isinstance(value, dict) and value.get("presentation_profile") == "scientific-report"
    if report_required:
        if not report.is_file() or report.read_bytes() != (TEMPLATES / "report_qc.py").read_bytes():
            errors.append("scientific-report needs the unmodified canonical report_qc.py")
    elif report.exists():
        errors.append("artifacts-only packages must not bundle report_qc.py")
    if any(path.name in REMOVED_MODULES for path in package.rglob("*.py")):
        errors.append("package contains a removed validator/runtime module")
    errors.extend(_content_errors(package))
    if isinstance(value, dict) and not spec_errors:
        entrypoint_value = value.get("analysis", {}).get("entrypoint", "")
        entrypoint = package / str(entrypoint_value)
        if not entrypoint.is_file():
            errors.append(f"declared entrypoint is missing: {entrypoint_value}")
    manifest_path = package / "package_manifest.json"
    if require_frozen and not manifest_path.is_file():
        errors.append("package is not frozen")
    runner_matches = runner.is_file() and runner.read_bytes() == (TEMPLATES / "run_skill.py").read_bytes()
    if manifest_path.is_file() and runner_matches:
        try:
            _load_runner(package).verify_package(package)
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(f"package manifest is stale or invalid: {exc}")
    return errors


def check_package(package: pathlib.Path, *, require_frozen: bool = False) -> list[str]:
    if not package.is_dir():
        return [f"not a package directory: {package}"]
    if (package / "skill_spec.json").is_file():
        return _check_analytical(package, require_frozen=require_frozen)
    return _check_general(package, require_frozen=require_frozen)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=pathlib.Path)
    parser.add_argument("--require-frozen", action="store_true")
    args = parser.parse_args()
    package = args.package.expanduser().resolve()
    analytical = (package / "skill_spec.json").is_file()
    errors = check_package(package, require_frozen=args.require_frozen)
    route = "executable-analysis" if analytical else "general-workflow"
    print(f"{package.name}  ({route})")
    if errors:
        for error in errors:
            print(f"  FAIL  {error}")
        print(f"RESULT: blocked by {len(errors)} finding(s)")
        return 1
    print("  no findings")
    print("GATE PASSED — package declarations and shape agree; runtime and science are separate gates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
