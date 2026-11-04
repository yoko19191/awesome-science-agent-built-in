#!/usr/bin/env python3
"""Vendor an nf-core module or subworkflow into a pipeline repo, offline-safe.

  vendor_nfcore.py --repo REPO --name fastp [--kind module|subworkflow] [--sha COMMIT] [--offline]

Sources, in order: GitHub nf-core/modules at the chosen commit (default: the index's pin; pass
--sha with the Registry commit from `nfcore_index.py freshness` for a newer or unindexed
component), then a snapshot in assets/nf-core-snapshot/ if the package ships one (pinned commit only), then
"container-only": nothing is written and the nf-core container URIs are returned so a local
module can reuse them. modules.json records the commit each component was actually vendored
at; a subworkflow's modules default to the subworkflow's commit. With --offline or
NEXTFLOW_SKILLS_OFFLINE=1, GitHub is never called. Exit 3 when any component fell back to
container-only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import registry  # noqa: E402
from nfcore_github import (DEFAULT_SNAPSHOT, RAW_URL, component_path, contained_path, derive, fetch_component,  # noqa: E402
                           http_fetch, offline_fetch, read_snapshot, resolve_sha)
from nfcore_index import DEFAULT_INDEX, load_index, show  # noqa: E402

NFCORE_REPO = "https://github.com/nf-core/modules.git"


def _fetch(kind: str, name: str, comp: dict | None, sha: str, pin: str, fetch,
           snapshot_dir: Path) -> tuple[str, dict[str, bytes], str, dict] | None:
    """(source, files, commit, entry) from GitHub at `sha`, else the snapshot at the pin."""
    path = comp["path"] if comp else component_path(kind, name)
    try:
        if comp and sha == pin:
            return "github", {f: fetch(RAW_URL.format(sha=sha, path=f"{path}/{f}")) for f in comp["files"]}, sha, comp
        sha = resolve_sha(sha, fetch)  # modules.json records full 40-character commits
        files, _ = fetch_component(path, sha, fetch, comp["files"] if comp else None)
        return "github", files, sha, derive(kind, name, path, files)
    except Exception:  # any network or HTTP failure falls through to the snapshot
        pass
    snap = read_snapshot(comp["path"], comp["files"], snapshot_dir) if comp else None
    return ("snapshot", snap, pin, comp) if snap is not None else None


def _drop_stale(dest: Path, keep: set[str]) -> None:
    """Remove files a previous vendoring left that this commit doesn't have. Folders holding
    their own main.nf are other components (e.g. diann and diann/<sub>) and are left alone."""
    if not dest.is_dir():
        return
    for path in sorted(dest.rglob("*"), reverse=True):
        rel = path.relative_to(dest)
        if any((dest / Path(*rel.parts[:i]) / "main.nf").is_file() for i in range(1, len(rel.parts))):
            continue
        if path.is_file() and rel.as_posix() not in keep:
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def _record(repo: Path, kind: str, name: str, sha: str, installed_by: list[str],
            requested: str | None = None) -> None:
    path = repo / "modules.json"
    data = json.loads(path.read_text()) if path.exists() else {"name": repo.name, "homePage": "", "repos": {}}
    section = (data["repos"].setdefault(NFCORE_REPO, {})
               .setdefault("modules" if kind == "module" else "subworkflows", {})
               .setdefault("nf-core", {}))
    entry = section.setdefault(name, {"branch": "master", "git_sha": sha, "installed_by": []})
    entry["git_sha"] = sha
    entry["installed_by"] = sorted(set(entry["installed_by"]) | set(installed_by))
    if requested:  # a snapshot fallback for this commit, so a rerun can tell it's done
        entry["requested_sha"] = requested
    else:
        entry.pop("requested_sha", None)
    path.write_text(json.dumps(data, indent=4) + "\n")


def vendor(repo: Path, name: str, kind: str, index: dict, fetch=http_fetch,
           snapshot_dir: Path = DEFAULT_SNAPSHOT, installed_by: list[str] | None = None,
           sha: str | None = None, offline: bool = False, skip: frozenset = frozenset()) -> list[dict]:
    component_path(kind, name)  # reject unsafe names before index lookup or stale-file cleanup
    pin = index["git_sha"]
    comp = show(index, name, kind)
    sha = pin if not sha or pin.startswith(sha) else sha
    if comp is None and sha == pin:
        raise KeyError(f"{kind} {name!r} is not in the nf-core index @ {pin}; run `nfcore_index.py freshness "
                       f"{name}` and vendor it with --sha <registry_sha>")
    if registry.offline_reason(offline):
        fetch = offline_fetch
    got = _fetch(kind, name, comp, sha, pin, fetch, snapshot_dir)
    if got is None:
        return [{"kind": kind, "name": name, "source": "container-only", "files": [],
                 "containers": (comp or {}).get("containers", {}), "git_sha": pin if comp else sha}]
    source, files, used, entry = got
    dest = repo / entry["path"]
    paths = {rel: contained_path(dest, rel) for rel in files}
    if kind == "subworkflow" and used == pin:
        def validate_children(parent: dict, seen: set[tuple[str, str]]) -> None:
            for child in parent.get("components", []):
                component_path("module", child)
                child_kind = "subworkflow" if show(index, child, "subworkflow") and not show(index, child, "module") else "module"
                key = (child_kind, child)
                if key in seen:
                    continue
                seen.add(key)
                child_entry = show(index, child, child_kind)
                if child_entry is None:
                    raise KeyError(f"{child_kind} {child!r} is not in the nf-core index @ {pin}; run `nfcore_index.py freshness "
                                   f"{child}` and vendor it with --sha <registry_sha>")
                if child_kind == "subworkflow":
                    validate_children(child_entry, seen)
        validate_children(entry, set())
    _drop_stale(dest, set(files))
    for rel, content in files.items():
        paths[rel].parent.mkdir(parents=True, exist_ok=True)
        paths[rel].write_bytes(content)
    _record(repo, kind, name, used, installed_by or ["modules"], None if used.startswith(sha) else sha)
    result = {"kind": kind, "name": name, "source": source,
              "files": sorted(f"{entry['path']}/{f}" for f in files),
              "containers": entry.get("containers", {}), "git_sha": used}
    if not used.startswith(sha):
        result["requested_sha"] = sha
    results = [result]
    if kind == "subworkflow":
        for child in entry["components"]:
            nested = show(index, child, "subworkflow") and not show(index, child, "module")
            child_kind = "subworkflow" if nested else "module"
            if (child_kind, child) in skip:  # the caller vendors it on its own
                continue
            results += vendor(repo, child, child_kind, index, fetch, snapshot_dir, installed_by=[name],
                              sha=used, offline=offline, skip=skip)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--kind", choices=["module", "subworkflow"], default="module")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--sha", help="nf-core/modules commit to vendor at (default: the index's pin)")
    parser.add_argument("--offline", action="store_true", help="never call GitHub: a shipped snapshot if present, then container-only")
    args = parser.parse_args()
    results = vendor(args.repo, args.name, args.kind, load_index(args.index), snapshot_dir=args.snapshot,
                     sha=args.sha, offline=args.offline)
    print(json.dumps(results, indent=2))
    return 3 if any(r["source"] == "container-only" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
