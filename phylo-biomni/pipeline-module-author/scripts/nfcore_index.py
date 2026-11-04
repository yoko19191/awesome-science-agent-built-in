#!/usr/bin/env python3
"""Find nf-core components: the Nextflow Registry first, the shipped offline index as fallback.

  nfcore_index.py search "salmon quant" [--kind module|subworkflow] [--limit 10] [--offline]
  nfcore_index.py show salmon/quant [--kind module]
  nfcore_index.py freshness salmon/quant [fastp ...] [--save DIR] [--offline]

`search` asks the Registry (`nextflow module search`) and falls back to the offline index when
the Registry can't be used; the output's "source" says which answered and "reason" says why the
Registry was not used. `show` always reads the offline index.

`freshness` asks the Registry which nf-core/modules commit it publishes for each component and
compares the component's files at that commit with our pinned copy (the shipped snapshot, else
GitHub at the pin), byte for byte. When they differ, GitHub's compare API says which commit is
newer. Status: `current` (same files: use the index); `newer_available` (files differ and the
Registry commit is ahead of the pin) or `not_in_index`, both with a `component` entry derived
from the files at the Registry commit (--save writes those files under DIR); `pinned_newer`
(files differ but the Registry commit is behind the pin: keep the pin); `removed_upstream` (not
in the index and the Registry commit is behind the pin: removed or renamed before the pin, don't
vendor); or `unknown` (offline, Registry or GitHub unreachable, or the commits diverged: keep
the pin).

With --offline or NEXTFLOW_SKILLS_OFFLINE=1 nothing touches the network.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import registry  # noqa: E402
from nfcore_github import (DEFAULT_SNAPSHOT, RAW_URL, commit_order, component_path, contained_path, derive,  # noqa: E402
                           fetch_component, http_fetch, read_snapshot, resolve_sha)

DEFAULT_INDEX = Path(__file__).resolve().parent.parent / "references" / "nf-core-index.json"


def _component_flags(component: dict) -> dict:
    """Expose hazards that must be resolved before the approval gate."""
    description = str(component.get("description") or "")
    deprecated = bool(re.search(r"\bdeprecated\b", description, re.I))
    hazards = []
    path = DEFAULT_SNAPSHOT / str(component.get("path") or "")
    main = path / "main.nf"
    meta = path / "meta.yml"
    input_patterns = component.get("input_patterns", {})
    if not input_patterns and meta.is_file():
        input_patterns = _input_patterns_from_meta(meta.read_text(errors="replace"))
    if "optional_input_hazards" in component:  # recorded when the index was built
        hazards = list(component["optional_input_hazards"])
    elif main.is_file() and meta.is_file():  # older index: read the snapshot copy, if one is shipped
        hazards = optional_input_hazards(meta.read_text(errors="replace"), main.read_text(errors="replace"))
    return {"deprecated": deprecated, "optional_input_hazards": sorted(hazards),
            "input_patterns": input_patterns}


def optional_input_hazards(meta_text: str, main_text: str) -> list[str]:
    """Optional inputs the script tests with `[ -n "$x" ]`, which is true for an empty list `[]` too."""
    hazards = []
    for match in re.finditer(r"^\s*-\s+([A-Za-z][A-Za-z0-9_]*)\s*:\s*$([\s\S]*?)(?=^\s*-\s+|\Z)", meta_text, re.M):
        name, body = match.groups()
        if re.search(r"\boptional\b", body, re.I) and re.search(
                rf"\[\s*-n\s+['\"]?\$?\{{?{re.escape(name)}\}}?['\"]?\s*\]", main_text, re.I):
            hazards.append(name)
    return sorted(set(hazards))


def _input_patterns_from_meta(text: str) -> dict[str, dict[str, str]]:
    """Read input patterns from a shipped meta.yml without adding a YAML dependency."""
    section = re.search(r"^input:\s*$([\s\S]*?)(?=^output:\s*$)", text, re.M)
    if not section:
        return {}
    out, current = {}, None
    for line in section.group(1).splitlines():
        match = re.match(r"^\s*-\s*-?\s*([A-Za-z][A-Za-z0-9_]*)\s*:\s*$", line)
        if match:
            current = match.group(1)
            continue
        if current:
            match = re.match(r"^\s+(pattern|description):\s*(.*)$", line)
            if match and match.group(2).strip():
                value = match.group(2).strip().strip('"\'')
                out.setdefault(current, {})[match.group(1)] = value
    return out


def load_index(path: Path = DEFAULT_INDEX) -> dict:
    return json.loads(Path(path).read_text())


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def search(index: dict, query: str, kind: str | None = None, limit: int = 10) -> list[dict]:
    q = _tokens(query)
    scored = []
    for c in index["components"]:
        if kind and c["kind"] != kind:
            continue
        tools = set().union(*(_tokens(t) for t in c["tools"])) if c["tools"] else set()
        keywords = set().union(*(_tokens(k) for k in c["keywords"])) if c["keywords"] else set()
        score = (3 * len(q & _tokens(c["name"])) + 3 * len(q & tools)
                 + 2 * len(q & keywords) + len(q & _tokens(c["description"])))
        if score:
            scored.append((score, c["name"], c))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [{"kind": c["kind"], "name": c["name"], "description": c["description"], **_component_flags(c),
             "tools": c["tools"], "components": c["components"], "score": s} for s, _, c in scored[:limit]]


def show(index: dict, name: str, kind: str | None = None) -> dict | None:
    for c in index["components"]:
        if c["name"] == name and (kind is None or c["kind"] == kind):
            return {**c, **_component_flags(c)}
    return None


def discover(index: dict, query: str, kind: str | None = None, limit: int = 10,
             runner=registry.run_nextflow, offline: bool = False) -> dict:
    """Registry search first; the offline index when offline, unreachable, or it has no match."""
    reason = registry.offline_reason(offline)
    if reason is None:
        try:
            hits = [h for h in registry.search(query, limit, runner) if not kind or h["kind"] == kind]
        except registry.RegistryError as error:
            reason = str(error)
        else:
            for hit in hits:
                comp = show(index, hit["name"], hit["kind"])
                hit["in_index"] = comp is not None
                hit["components"] = comp["components"] if comp else []
            if hits:
                return {"source": "registry", "query": query, "results": hits}
            reason = f"the Registry returned no {kind or 'nf-core'} match"
    return {"source": "offline", "reason": reason, "index_sha": index["git_sha"], "query": query,
            "results": search(index, query, kind, limit)}


def _pinned_files(comp: dict, pin: str, fetch, snapshot_dir: Path) -> tuple[dict[str, bytes], str]:
    snap = read_snapshot(comp["path"], comp["files"], snapshot_dir)
    if snap is not None:
        return snap, "snapshot"
    return {f: fetch(RAW_URL.format(sha=pin, path=f"{comp['path']}/{f}")) for f in comp["files"]}, "github"


def _files_differ(comp: dict, pinned: dict[str, bytes], sha: str, fetch) -> list[str]:
    differ = []
    for f in comp["files"]:
        try:
            latest = fetch(RAW_URL.format(sha=sha, path=f"{comp['path']}/{f}"))
        except FileNotFoundError:
            latest = None
        if latest != pinned[f]:
            differ.append(f)
    return differ


def freshness(index: dict, name: str, kind: str | None = None, runner=registry.run_nextflow, fetch=http_fetch,
              snapshot_dir: Path = DEFAULT_SNAPSHOT, offline: bool = False, save: Path | None = None) -> dict:
    """Is our pinned copy of `name` what the Registry publishes? Decided by comparing file bytes.
    Without `kind`, the index decides whether `name` is a module or a subworkflow."""
    if kind is None:
        kind = "subworkflow" if show(index, name, "subworkflow") and not show(index, name, "module") else "module"
    component_path(kind, name)
    comp, pin = show(index, name, kind), index["git_sha"]
    out = {"name": name, "kind": kind, "status": "unknown", "pinned_sha": pin, "registry_sha": None,
           "in_index": comp is not None, **_component_flags(comp or {})}
    reason = registry.offline_reason(offline)
    if reason:
        return {**out, "reason": reason}
    try:
        view = registry.view(name, runner)
    except registry.RegistryError as error:
        return {**out, "reason": str(error)}
    sha = registry.commit(view)
    if not sha:
        return {**out, "reason": f"Registry version {view.get('version')!r} names no nf-core/modules commit"}
    out.update(registry_sha=sha, registry_version=view.get("version"))
    if comp and pin.startswith(sha):
        return {**out, "status": "current", "compared_with": "pinned commit"}
    path = comp["path"] if comp else component_path(kind, name)
    try:
        if comp:
            pinned, where = _pinned_files(comp, pin, fetch, snapshot_dir)
            differ = _files_differ(comp, pinned, sha, fetch)
            if not differ:
                return {**out, "status": "current", "compared_with": where}
            out.update(compared_with=where, differs=differ)
        # Files differ, or the index lacks it: only a commit ahead of the pin is worth vendoring.
        # Behind the pin, an unindexed module was removed or renamed before the pin.
        try:
            order = commit_order(pin, sha, fetch)
        except (OSError, ValueError) as error:
            return {**out, "reason": f"could not order {sha} against the pin: {error}"}
        if order == "behind":
            return {**out, "status": "pinned_newer" if comp else "removed_upstream"}
        if order != "ahead":
            return {**out, "reason": f"GitHub compare says {sha} is {order!r} relative to the pin"}
        try:
            sha = out["registry_sha"] = resolve_sha(sha, fetch)  # modules.json needs the full sha
        except (OSError, ValueError) as error:
            return {**out, "reason": f"could not resolve the full commit for {sha}: {error}"}
        files, how = fetch_component(path, sha, fetch, comp["files"] if comp else None)
    except OSError as error:
        return {**out, "reason": f"GitHub could not serve {path}: {error}"}
    component = {**derive(kind, name, path, files, view), "git_sha": sha, "file_list": how}
    if save:
        destinations = {rel: contained_path(Path(save) / path, rel) for rel in files}
        for rel, content in files.items():
            destinations[rel].parent.mkdir(parents=True, exist_ok=True)
            destinations[rel].write_bytes(content)
        component["saved_to"] = str(Path(save) / path)
    return {**out, "status": "newer_available" if comp else "not_in_index", "component": component}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["search", "show", "freshness"])
    parser.add_argument("term", nargs="+")
    parser.add_argument("--kind", choices=["module", "subworkflow"])
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--offline", action="store_true", help="never call the Registry or GitHub")
    parser.add_argument("--save", type=Path, help="freshness: write files fetched at the Registry commit here")
    args = parser.parse_args()
    index = load_index(args.index)
    if args.action == "search":
        print(json.dumps(discover(index, " ".join(args.term), args.kind, args.limit, offline=args.offline),
                         indent=2))
        return 0
    if args.action == "freshness":
        print(json.dumps([freshness(index, n, args.kind, offline=args.offline, save=args.save)
                          for n in args.term], indent=2))
        return 0
    found = show(index, args.term[0], args.kind)
    print(json.dumps(found, indent=2) if found else f"not in index (nf-core/modules @ {index['git_sha']})")
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())
