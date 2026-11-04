#!/usr/bin/env python3
"""Find an existing container for a local module's packages, without building one.

  find_container.py --package deseq2 --package limma          # nf-core images in the offline index
  find_container.py --biocontainers bioconductor-deseq2 [--version 1.42.0]   # quay.io tags (network; not with --offline)
  find_container.py --module-dir REPO/modules/nf-core/salmon/quant   # a vendored module's own pins

Index mode lists the pinned images of vendored nf-core modules whose conda pins cover every
requested package, then partial matches with what they lack. Ranking: quay.io/biocontainers
images (and Seqera-hosted images the index records a biocontainers replacement for) first, then
other registries, then Seqera-hosted images; within each, the smallest environment first. A
Seqera-hosted entry is marked "seqera": true with its "replacement" (a digest-only quay.io image,
or null). A package matches a pin by its conda name, with or without a
bioconductor-/r-/py-/python- prefix. Pins list top-level packages only; their dependencies are in
the image too.
Biocontainers mode lists tags newest first, each with its manifest digest and the digest-only
"pinned" URI (AWS Batch rejects name:tag@digest); --version keeps only builds of that version.
Module-dir mode reads the container URIs from a module's main.nf and the conda pins from its
environment.yml, for a module vendored at a commit the offline index doesn't describe.
Never calls the Seqera Containers or Wave API.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import registry  # noqa: E402
from nfcore_github import containers, yaml_list  # noqa: E402
from nfcore_index import DEFAULT_INDEX, load_index  # noqa: E402
from dependency_metadata import provided_by, runtime_dependencies  # noqa: E402

QUAY_TAGS = "https://quay.io/api/v1/repository/biocontainers/{name}/tag/?limit={limit}&onlyActiveTags=true"
_PREFIXES = ("bioconductor-", "r-", "py-", "python-")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def is_seqera(image: str | None) -> bool:
    """Is this image served by Seqera (Seqera Containers, Wave, community-cr-prod blobs)?
    The registry host is seqera.io or any *.seqera.io host. Keep in step with the copy in
    pipeline-hpc-validate's preflight.py."""
    host = _SCHEME_RE.sub("", (image or "").strip()).split("/", 1)[0].lower()
    return host == "seqera.io" or host.endswith(".seqera.io")


def replacements(index: dict) -> dict[str, list[dict]]:
    """Seqera image -> the container_replacements the index records for it (from the platform's
    pipeline image overrides; see dev/tools/build_nfcore_index.py)."""
    found: dict[str, list[dict]] = {}
    for comp in index.get("components", []):
        image = (comp.get("containers") or {}).get("docker")
        if image and comp.get("container_replacements"):
            found.setdefault(image, comp["container_replacements"])
    return found


def biocontainers_replacement(entries: list[dict] | None) -> str | None:
    return next((r["image"] for r in entries or [] if r.get("source") == "biocontainers"), None)


def _registry_rank(image: str, replacement: str | None) -> int:
    """0 quay.io/biocontainers, 1 Seqera with a biocontainers replacement, 2 other registries,
    3 Seqera with none. Never alphabetical: "community.wave..." sorts before "quay.io"."""
    if not is_seqera(image):
        return 0 if image.startswith("quay.io/biocontainers/") else 2
    return 1 if replacement else 3


def _pin_name(pin: str) -> str:
    return re.split(r"[=<>!\s]", pin.split("::")[-1], maxsplit=1)[0].lower()


def _matches(package: str, pin: str) -> bool:
    package = package.lower()
    return pin == package or any(pin == p + package for p in _PREFIXES)


def from_index(index: dict, packages: list[str]) -> dict:
    requested = sorted(set(packages) | {
        dep for package in packages for dep in runtime_dependencies(package, "r")
    })
    images: dict[str, dict] = {}
    known = replacements(index)
    for comp in index["components"]:
        image = (comp.get("containers") or {}).get("docker")
        if comp["kind"] != "module" or not image or not comp.get("conda"):
            continue
        pins = [_pin_name(p) for p in comp["conda"]]
        entry = images.setdefault(image, {"image": image, "modules": [], "conda": comp["conda"],
                                          "missing": [p for p in requested if not any(
                                              _matches(candidate, n)
                                              for candidate in {p} | provided_by(p, "r")
                                              for n in pins)]})
        entry["modules"].append(comp["name"])
    for entry in images.values():
        if is_seqera(entry["image"]):
            entry["seqera"], entry["replacement"] = True, biocontainers_replacement(known.get(entry["image"]))
    ranked = sorted(images.values(), key=lambda e: (
        len(e["missing"]), _registry_rank(e["image"], e.get("replacement")), len(e["conda"]), e["image"]))
    return {"packages": packages, "runtime_packages": requested,
            "covering": [e for e in ranked if not e["missing"]],
            "partial": [e for e in ranked if e["missing"] and len(e["missing"]) < len(packages)][:5]}


def from_module_dir(module_dir: Path) -> dict:
    env = Path(module_dir) / "environment.yml"
    return {"module_dir": str(module_dir),
            "containers": containers((Path(module_dir) / "main.nf").read_text()),
            "conda": yaml_list(env.read_text(), "dependencies") if env.is_file() else []}


def http_fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=20) as response:
        return response.read()


def biocontainers(name: str, fetch=http_fetch, limit: int = 20, offline: bool = False,
                  version: str | None = None) -> list[dict]:
    """quay.io/biocontainers/<name> tags, newest first; with `version`, only `<version>--<build>`
    tags (and a bare `<version>` tag). Raises OSError when offline or quay.io is unreachable."""
    reason = registry.offline_reason(offline)
    if reason:
        raise OSError(reason)
    url = QUAY_TAGS.format(name=name, limit=limit)
    if version:
        url += f"&filter_tag_name=like:{version}"
    tags = json.loads(fetch(url)).get("tags", [])
    if version:
        tags = [t for t in tags if t["name"] == version or t["name"].startswith(f"{version}--")]
    tags = sorted(tags, key=lambda t: (t.get("start_ts") or 0, t["name"]), reverse=True)
    return [{"image": f"quay.io/biocontainers/{name}:{t['name']}", "tag": t["name"],
             "manifest_digest": t.get("manifest_digest"),
             "pinned": f"quay.io/biocontainers/{name}@{t['manifest_digest']}" if t.get("manifest_digest") else None}
            for t in tags]


def pin_biocontainer(name: str, version: str, fetch=http_fetch, offline: bool = False) -> str | None:
    """The digest-only URI of the newest quay.io/biocontainers build of exactly `version`, or None.
    Raises OSError when offline or quay.io is unreachable."""
    builds = [b for b in biocontainers(name, fetch, limit=100, offline=offline, version=version)
              if b["tag"].startswith(f"{version}--") and b["pinned"]]
    return builds[0]["pinned"] if builds else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--package", action="append", help="package the module's code loads (repeat)")
    group.add_argument("--biocontainers", metavar="CONDA_NAME", help="list quay.io/biocontainers tags")
    parser.add_argument("--version", help="with --biocontainers: only builds of this exact version")
    group.add_argument("--module-dir", type=Path, help="read a vendored module's container and conda pins")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--offline", action="store_true", help="never query quay.io")
    args = parser.parse_args()
    if args.module_dir:
        result = from_module_dir(args.module_dir)
        print(json.dumps(result, indent=2))
        return 0 if result["containers"]["docker"] else 1
    if args.biocontainers:
        try:
            uris = biocontainers(args.biocontainers, offline=args.offline, version=args.version)
        except OSError as error:
            print(f"quay.io unreachable ({error}); ask the user for an image", file=sys.stderr)
            return 2
        print(json.dumps(uris, indent=2))
        return 0 if uris else 1
    result = from_index(load_index(args.index), args.package)
    print(json.dumps(result, indent=2))
    return 0 if result["covering"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
