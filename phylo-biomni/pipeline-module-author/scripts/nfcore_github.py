"""Read nf-core/modules component files from GitHub or the shipped snapshot, and derive the
offline index's fields (containers, conda, script flags, file list) from those files.

Used by nfcore_index.py, vendor_nfcore.py and find_container.py; not a CLI. Only GitHub raw
file reads and the GitHub contents API (to list a component folder) touch the network.
"""
from __future__ import annotations

import http.client
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from module_flags import script_flags  # noqa: E402

RAW_URL = "https://raw.githubusercontent.com/nf-core/modules/{sha}/{path}"
CONTENTS_URL = "https://api.github.com/repos/nf-core/modules/contents/{path}?ref={sha}"
COMPARE_URL = "https://api.github.com/repos/nf-core/modules/compare/{base}...{head}"
COMMIT_URL = "https://api.github.com/repos/nf-core/modules/commits/{sha}"
_FULL_SHA = re.compile(r"[0-9a-f]{40}")
DEFAULT_SNAPSHOT = Path(__file__).resolve().parent.parent / "assets" / "nf-core-snapshot"
_TEMPLATE_RE = re.compile(r"^\s*template\s+['\"]([^'\"]+)['\"]", re.M)


def http_fetch(url: str) -> bytes:
    """GET a URL; a 404 raises FileNotFoundError, anything else unreachable raises OSError
    (including http.client errors such as IncompleteRead, which aren't OSErrors)."""
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise FileNotFoundError(url) from None
        raise
    except http.client.HTTPException as error:
        raise OSError(f"{url}: {error!r}") from None


def offline_fetch(url: str) -> bytes:
    raise OSError(f"offline mode: not fetching {url}")


def component_path(kind: str, name: str) -> str:
    if not name or ".." in name or name.startswith("/") or "\\" in name:
        raise ValueError(f"invalid nf-core component name: {name!r}")
    return f"{'modules' if kind == 'module' else 'subworkflows'}/nf-core/{name}"


def contained_path(base: Path, rel: str) -> Path:
    """Resolve a component file path and reject anything outside its destination."""
    base = Path(base).resolve()
    target = (base / rel).resolve()
    if Path(rel).is_absolute() or not target.is_relative_to(base):
        raise ValueError(f"path escapes destination: {rel!r}")
    return target


def _skip(rel: str) -> bool:
    parts = rel.split("/")
    return "tests" in parts or any(p.startswith(".") for p in parts)


def list_files(path: str, sha: str, fetch) -> list[str]:
    """Every file of a component at `sha`, as the index lists them (no tests/, no dotfiles)."""
    files, queue = [], [path]
    while queue:
        folder = queue.pop()
        entries = json.loads(fetch(CONTENTS_URL.format(path=folder, sha=sha)))
        if not isinstance(entries, list):
            raise ValueError(f"not a folder listing: {folder}")
        for entry in entries:
            rel = entry["path"][len(path) + 1:]
            if _skip(rel):
                continue
            if entry["type"] == "dir":
                queue.append(entry["path"])
            elif entry["type"] == "file":
                files.append(rel)
    return sorted(files)


def commit_order(base: str, head: str, fetch) -> str:
    """GitHub's compare status of `head` relative to `base`: ahead, behind, identical or diverged.
    Raises OSError or ValueError when GitHub can't answer."""
    status = json.loads(fetch(COMPARE_URL.format(base=base, head=head))).get("status")
    if not isinstance(status, str):
        raise ValueError("GitHub compare returned no status")
    return status


def resolve_sha(sha: str, fetch) -> str:
    """The full 40-character commit for a (short) sha, as nf-core's modules.json stores it.
    Raises OSError or ValueError when GitHub can't resolve it."""
    if _FULL_SHA.fullmatch(sha):
        return sha
    full = json.loads(fetch(COMMIT_URL.format(sha=sha))).get("sha")
    if not isinstance(full, str) or not _FULL_SHA.fullmatch(full) or not full.startswith(sha):
        raise ValueError(f"GitHub did not resolve commit {sha}")
    return full


def inferred_files(main_nf: str, known: list[str] | None = None) -> list[str]:
    """The file list when the folder can't be listed: the standard files, the index's list, and
    every `template` the main.nf uses (nf-core keeps those in templates/)."""
    names = {"main.nf", "meta.yml", "environment.yml", *(known or [])}
    names |= {f"templates/{t}" for t in _TEMPLATE_RE.findall(main_nf)}
    return sorted(names)


def fetch_component(path: str, sha: str, fetch, known: list[str] | None = None) -> tuple[dict[str, bytes], str]:
    """(files, how) at `sha`: `how` is "listed" (GitHub contents API) or "inferred" (standard
    files + templates, used when the API is rate-limited or down; absent optional files are
    skipped). Raises OSError when GitHub can't serve the component."""
    try:
        names, how = list_files(path, sha, fetch), "listed"
    except (OSError, ValueError, KeyError) as error:
        if isinstance(error, FileNotFoundError):
            raise
        main = fetch(RAW_URL.format(sha=sha, path=f"{path}/main.nf"))
        names, how = inferred_files(main.decode(errors="replace"), known), "inferred"
    if "main.nf" not in names:
        raise FileNotFoundError(f"{path}/main.nf at {sha}")
    for rel in names:
        contained_path(Path("/nf-core-component"), rel)
    files = {}
    for rel in names:
        try:
            files[rel] = fetch(RAW_URL.format(sha=sha, path=f"{path}/{rel}"))
        except FileNotFoundError:
            if how == "listed" or rel == "main.nf":
                raise
    return files, how


def read_snapshot(path: str, files: list[str], snapshot_dir: Path = DEFAULT_SNAPSHOT) -> dict[str, bytes] | None:
    snap = Path(snapshot_dir) / path
    if files and all((snap / f).is_file() for f in files):
        return {f: (snap / f).read_bytes() for f in files}
    return None


def containers(main_nf: str) -> dict:
    m = re.search(r"^\s*container\s+(.*?)^\s*(?:input|output):", main_nf, re.S | re.M)
    uris = [u for u in re.findall(r"['\"]([^'\"$\s{}]+)['\"]", m.group(1) if m else "") if "/" in u and ":" in u]
    return {"docker": next((u for u in uris if not u.startswith("http")), None),
            "singularity": next((u for u in uris if u.startswith("http")), None)}


def yaml_list(text: str, key: str, keep_maps: bool = False) -> list[str]:
    """The items of a top-level YAML list (`key:` then `- item` lines), without a YAML parser.
    Map items (`- name:`) are kept as their key only when keep_maps; nested lists are skipped."""
    items, inside, indent = [], False, None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace() and not line.startswith("-"):
            inside, indent = line.startswith(f"{key}:"), None
            continue
        m = re.match(r"^(\s*)-\s+(.*?)\s*(?:\s#.*)?$", line)
        if not inside or not m:
            continue
        indent = len(m.group(1)) if indent is None else indent
        value = m.group(2).strip("'\"")
        if len(m.group(1)) != indent or not value:
            continue
        if re.match(r"^[^\s:]+:(\s|$)", value):
            if keep_maps:
                items.append(value.split(":", 1)[0].strip("'\""))
            continue
        items.append(value)
    return items


def derive(kind: str, name: str, path: str, files: dict[str, bytes], view: dict | None = None) -> dict:
    """An index-shaped entry for a component, from its files (and the Registry view, if any,
    for description, keywords, tools, inputs and outputs)."""
    main = files["main.nf"].decode(errors="replace")
    meta = files.get("meta.yml", b"").decode(errors="replace")
    env = files.get("environment.yml", b"").decode(errors="replace")
    process = re.search(r"^\s*(?:process|workflow)\s+(\w+)", main, re.M)
    view = view or {}
    outputs = view.get("output")
    return {
        "kind": kind, "name": name, "process": process.group(1) if process else None, "path": path,
        "description": ((view.get("description") or "").strip().splitlines() or [""])[0][:200],
        "keywords": [str(k) for k in view.get("keywords") or []],
        "tools": [t["name"] if isinstance(t, dict) else str(t) for t in view.get("tools") or []],
        "inputs": [[i.get("name") for i in c.get("items") or []] for c in view["input"]] if "input" in view else None,
        "outputs": list(outputs) if isinstance(outputs, dict) else None,
        "containers": containers(main) if kind == "module" else {},
        "conda": yaml_list(env, "dependencies") if kind == "module" else [],
        "script_flags": sorted(script_flags(main)) if kind == "module" else [],
        "files": sorted(files),
        "components": yaml_list(meta, "components", keep_maps=True),
    }
