#!/usr/bin/env python3
"""Local preflight for a Biomni custom Nextflow bundle. Run before every hpc_run_pipeline.

  preflight.py REPO [--json] [--allow-seqera]

Mirrors the platform's custom pipeline bundle checks and user-config validation
and adds this skill family's config-layer rules. It also prints the process count,
which sets the recommended validation level. Exit 1 when any error is found.

It also reports every process whose effective container is Seqera-hosted: the
module's `container` directive (docker and singularity branches), unless a `withName` selector
in a submitted config sets `container`, which wins. These images are informational and do not
fail preflight. The platform applies no
image overrides to custom bundles, so this check is the only one they get.
"""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from submission import PORTABLE_CONFIG, bundle_aliases  # noqa: E402

PROTECTED_USER_CONFIG_KEYS = frozenset({
    "anonymous",
    "cloudcache", "cliPath", "errorStrategy", "executionRole", "executor", "fusion",
    "jobRole", "lineage", "maxSpotAttempts", "maxRetries", "queue", "resourceLabels",
    "resourceLimits", "terminateUnschedulableJobs", "wave", "workDir",
})
_CONFIG_IDENTIFIER = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_LOCAL_INCLUDE_RE = re.compile(r"^\s*includeConfig\s*(?:\(\s*)?['\"]([^'\"]+)['\"]", re.MULTILINE)
_LOCAL_MODULE_INCLUDE_RE = re.compile(
    r"^\s*include\s*\{.*?\}\s*from\s*['\"]([^'\"]+)['\"]", re.MULTILINE | re.DOTALL)
_COMMENT_OR_STRING_RE = re.compile(
    r"(?P<string>'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")"
    r"|(?P<block_comment>/\*.*?\*/)"
    r"|(?P<line_comment>//[^\n]*)",
    re.DOTALL,
)
_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_PROCESS_DEF_RE = re.compile(r"^\s*process\s+(\w+)\s*\{", re.MULTILINE)
_WITHNAME_RE = re.compile(r"withName\s*:\s*['\"]([^'\"]+)['\"]\s*\{")
_CONTAINER_SETTING_RE = re.compile(r"\bcontainer\s*=\s*['\"]([^'\"$]+)['\"]")
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x1f\x7f]")
# A tag followed by a digest (name:tag@sha256:...), which AWS Batch rejects. A registry port
# (host:5000/name@sha256:...) has a '/' after the colon and is not matched.
_TAG_AND_DIGEST_RE = re.compile(
    r"['\"](?P<ref>[^'\"\s@]+:[^'\"\s@/:]+@sha256:[0-9a-fA-F]+)['\"]")
_HEAD_PIPELINE_PATH_RE = re.compile(r"/work/pipeline/[\w./-]*")
_PROJECT_BIN_REF_RE = re.compile(r"\$\{?(?:workflow\.)?projectDir\}?/(?P<path>bin/[^\s'\"`;,)}\]]+)")


def _normalize(path: str) -> str:
    return posixpath.normpath(path.removeprefix("./"))


def _strip_comments(content: str) -> str:
    return _COMMENT_OR_STRING_RE.sub(lambda m: m.group("string") or "", content)


def validate_alias(path: str) -> None:
    if not path:
        raise ValueError("file path must not be empty")
    if _CONTROL_CHARACTER_RE.search(path):
        raise ValueError("file path must not contain control characters")
    if "\\" in path:
        raise ValueError("file path must use forward slashes")
    parts = path.split("/")
    if path.startswith("/") or any(p in {"", ".", ".."} for p in parts) or posixpath.normpath(path) != path:
        raise ValueError("file path must be a normalized relative path")


def _checkable(path: str) -> bool:
    return bool(path and not path.startswith("/") and not _URI_SCHEME_RE.match(path)
                and "$" not in path and "{" not in path)


def _missing_config_includes(files: dict[str, str]) -> list[str]:
    available = {_normalize(p) for p in files}
    missing = set()
    for source, content in files.items():
        src = _normalize(source)
        for m in _LOCAL_INCLUDE_RE.finditer(_strip_comments(content)):
            inc = m.group(1)
            if not _checkable(inc):
                continue
            target = _normalize(posixpath.join(posixpath.dirname(src), inc))
            if target not in available:
                missing.add(f"{src} includes missing file '{target}'")
    return sorted(missing)


def _candidates(target: str) -> set[str]:
    return {target, f"{target}.nf", f"{target}/main.nf"}


def _missing_module_includes(files: dict[str, str]) -> list[str]:
    available = {_normalize(p) for p in files}
    missing = set()
    for source, content in files.items():
        if not source.endswith(".nf"):
            continue
        src = _normalize(source)
        src_dir = posixpath.dirname(src)
        for m in _LOCAL_MODULE_INCLUDE_RE.finditer(_strip_comments(content)):
            inc = m.group(1)
            if inc.startswith("plugin/") or not _checkable(inc):
                continue
            if inc.startswith(("./", "../")):
                target = _normalize(posixpath.join(src_dir, inc))
                if _candidates(target).isdisjoint(available):
                    missing.add(f"{src} includes missing module '{target}.nf'")
                continue
            for base in (src_dir, ""):
                target = _normalize(posixpath.join(base, inc))
                if not _candidates(target).isdisjoint(available):
                    suggestion = posixpath.relpath(target, src_dir or ".")
                    if not suggestion.startswith("../"):
                        suggestion = f"./{suggestion}"
                    missing.add(
                        f"{src} includes bundled module '{inc}' without a './' or '../' prefix; "
                        f"Nextflow resolves it as a remote registry module. Use '{suggestion}'")
                    break
    return sorted(missing)


def _tag_and_digest_errors(files: dict[str, str]) -> list[str]:
    errors = set()
    for source, content in files.items():
        if source.endswith((".nf", ".config")):
            for m in _TAG_AND_DIGEST_RE.finditer(_strip_comments(content)):
                errors.add(f"{_normalize(source)} uses container '{m['ref']}': AWS Batch rejects a tag "
                           "combined with a digest. Use the tag alone or the digest alone")
    return sorted(errors)


def _missing_project_bin_files(files: dict[str, str]) -> list[str]:
    available = {_normalize(p) for p in files}
    missing = set()
    for source, content in files.items():
        if source.endswith((".nf", ".config")):
            for m in _PROJECT_BIN_REF_RE.finditer(_strip_comments(content)):
                target = _normalize(m["path"])
                if target not in available:
                    missing.add(f"{_normalize(source)} references missing file '{target}'")
    return sorted(missing)


def _head_only_path_errors(files: dict[str, str]) -> list[str]:
    errors = set()
    for source, content in files.items():
        if source.endswith((".nf", ".config")):
            for m in _HEAD_PIPELINE_PATH_RE.finditer(_strip_comments(content)):
                fix = ("Call the script by name: bin/ is on the task's PATH" if m[0].startswith("/work/pipeline/bin/")
                       else "Pass the file as a `path` input and use its task-local filename")
                errors.add(f"{_normalize(source)} uses '{m[0]}', which exists only on the head job, not in "
                           f"task containers. {fix}")
    return sorted(errors)


def bundle_errors(files: dict[str, str]) -> list[str]:
    errors = []
    if "main.nf" not in files:
        errors.append("pipeline_files must include main.nf")
    elif not files["main.nf"].strip():
        errors.append("main.nf is empty")
    return (errors + _missing_config_includes(files) + _missing_module_includes(files)
            + _tag_and_digest_errors(files) + _missing_project_bin_files(files)
            + _head_only_path_errors(files))


def protected_key_errors(files: dict[str, str]) -> list[str]:
    errors = []
    for alias, content in sorted(files.items()):
        if not alias.endswith(".config"):
            continue
        hits = sorted(set(_CONFIG_IDENTIFIER.findall(content)) & PROTECTED_USER_CONFIG_KEYS)
        if hits:
            errors.append(f"{alias}: User Nextflow config cannot override platform-owned settings: {', '.join(hits)}")
    return errors


def is_seqera(image: str | None) -> bool:
    """Registry host seqera.io or *.seqera.io: covers the platform's Seqera host list
    (community.wave.seqera.io, community-cr-prod.seqera.io, wave.seqera.io). Same rule as
    pipeline-module-author's find_container.is_seqera; keep the two in step."""
    host = _URI_SCHEME_RE.sub("", (image or "").strip()).split("/", 1)[0].lower()
    return host == "seqera.io" or host.endswith(".seqera.io")


def _directive_images(block: str) -> list[str]:
    """Literal image URIs in a process's `container` directive (both branches of nf-core's
    engine ternary), as pipeline-module-author's nfcore_github.containers reads them."""
    m = re.search(r"^\s*container\s+(.*?)^\s*(?:input|output):", block, re.S | re.M)
    return [u for u in re.findall(r"['\"]([^'\"$\s{}]+)['\"]", m.group(1) if m else "") if "/" in u and ":" in u]


def process_containers(files: dict[str, str]) -> dict[str, dict]:
    """Every process defined in a bundled .nf file: {name: {"file": alias, "images": [...]}},
    docker branch first."""
    found = {}
    for alias, content in sorted(files.items()):
        if not alias.endswith(".nf"):
            continue
        starts = list(_PROCESS_DEF_RE.finditer(content))
        for i, m in enumerate(starts):
            block = content[m.start(): starts[i + 1].start() if i + 1 < len(starts) else len(content)]
            images = sorted(_directive_images(block), key=lambda u: u.startswith("http"))
            found.setdefault(m.group(1), {"file": alias, "images": images})
    return found


def _balanced(text: str, start: int) -> str:
    depth = 0
    for i in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[start + 1: i]
    return text[start + 1:]


def selector_targets(selector: str) -> list[str]:
    """Process names a withName selector names: the last ':' segment of each '|' alternative,
    regex characters removed (as pipeline-module-author's check_ext_args does)."""
    return [n for n in (re.sub(r"[.*^$()\[\]]", "", alt.split(":")[-1]) for alt in selector.split("|")) if n]


def _selector_matches(selector: str, process: str) -> bool:
    """Match a Nextflow withName regex against plausible qualified process names."""
    candidates = (process, f"<ANY>:{process}", f"workflow:{process}")
    try:
        return any(re.fullmatch(selector, candidate) for candidate in candidates)
    except re.error:
        return selector in candidates


def config_containers(files: dict[str, str]) -> dict[str, str]:
    """{process: image} set by a `withName` block's `container = '...'` in any submitted config."""
    found = {}
    for alias, content in sorted(files.items()):
        if not alias.endswith(".config"):
            continue
        text = _strip_comments(content)
        for block in _WITHNAME_RE.finditer(text):
            setting = _CONTAINER_SETTING_RE.search(_balanced(text, block.end() - 1))
            if setting:
                found[block.group(1)] = setting.group(1)
    return found


def seqera_images(files: dict[str, str]) -> list[dict]:
    """Processes whose effective container is Seqera-hosted."""
    overrides = config_containers(files)
    found = []
    for name, proc in sorted(process_containers(files).items()):
        override = next((image for selector, image in overrides.items()
                         if _selector_matches(selector, name)), None)
        images = [override] if override else proc["images"]
        hits = [i for i in images if is_seqera(i)]
        if hits:
            found.append({"process": name, "file": proc["file"], "image": hits[0],
                          "overridden": override is not None})
    return found


def seqera_errors(files: dict[str, str]) -> list[str]:
    return []


def _layer_errors(repo: Path, aliases: list[str]) -> list[str]:
    errors = []
    if not (repo / PORTABLE_CONFIG).is_file():
        errors.append(f"{PORTABLE_CONFIG} is missing; the portable layer is required")
    if PORTABLE_CONFIG in aliases:
        errors.append(f"{PORTABLE_CONFIG} must not be submitted")
    for alias in aliases:
        if alias.endswith((".config", ".nf")) and "portable.config" in (repo / alias).read_text():
            errors.append(f"{alias} references portable.config; submitted files must not")
    return errors


def _exec_bit_errors(repo: Path, aliases: list[str]) -> list[str]:
    # Mode bits, not os.access: os.access(X_OK) is always true for root.
    return [f"{a}: bin/ script is not executable" for a in aliases
            if a.startswith("bin/") and not (repo / a).stat().st_mode & 0o111]


def bundle_files(repo: Path) -> dict[str, str]:
    """The submitted files by alias; only .nf and .config contents are read."""
    return {a: (repo / a).read_text() if a.endswith((".nf", ".config")) else "" for a in bundle_aliases(repo)}


def preflight(repo: Path, allow_seqera: bool = False) -> list[str]:
    aliases = bundle_aliases(repo)
    errors = []
    for alias in aliases:
        try:
            validate_alias(alias)
        except ValueError as error:
            errors.append(f"{alias}: {error}")
    files = bundle_files(repo)
    return (errors + bundle_errors(files) + protected_key_errors(files)
            + _layer_errors(repo, aliases) + _exec_bit_errors(repo, aliases))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--allow-seqera", action="store_true",
                        help="accepted for compatibility; Seqera-hosted images are informational")
    args = parser.parse_args()
    errors = preflight(args.repo)
    files = bundle_files(args.repo)
    retained = seqera_images(files)
    process_count = len(process_containers(files))
    if args.json:
        print(json.dumps({"ok": not errors, "errors": errors, "process_count": process_count,
                          "retained_upstream_images": retained}, indent=2))
    elif errors:
        print("preflight FAILED:")
        for error in errors:
            print(f"  - {error}")
    else:
        print(f"preflight passed: {len(bundle_aliases(args.repo))} files, {process_count} processes")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
