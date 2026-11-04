"""Read the Nextflow Registry through the documented `nextflow module` CLI (discovery only).

Used by nfcore_index.py and vendor_nfcore.py; not a CLI. Every call goes through `nextflow module search|view ... -output json` with a timeout. Any
failure (nextflow missing, non-zero exit, timeout, unparsable output) raises RegistryError so
callers fall back to the offline index. Offline mode (NEXTFLOW_SKILLS_OFFLINE=1 or --offline)
means no Registry or GitHub call is made at all.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

OFFLINE_ENV = "NEXTFLOW_SKILLS_OFFLINE"
TIMEOUT = 60
_VERSION_SHA = re.compile(r"-([0-9a-f]{7,40})$")


class RegistryError(Exception):
    """The Registry could not be read; the caller should use the offline index."""


def offline_reason(flag: bool = False) -> str | None:
    """Why network sources are disabled, or None when they may be used."""
    if flag:
        return "offline mode (--offline)"
    if os.environ.get(OFFLINE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
        return f"offline mode ({OFFLINE_ENV}={os.environ[OFFLINE_ENV]})"
    return None


def run_nextflow(args: list[str], timeout: int = TIMEOUT) -> str:
    return subprocess.run(["nextflow", *args], capture_output=True, text=True, timeout=timeout,
                          check=True).stdout


def _call(args: list[str], runner) -> dict:
    try:
        out = runner(args)
    except FileNotFoundError:
        raise RegistryError("nextflow is not installed") from None
    except subprocess.TimeoutExpired:
        raise RegistryError(f"`nextflow {' '.join(args)}` timed out") from None
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip().splitlines()
        raise RegistryError(f"`nextflow {' '.join(args)}` exited {error.returncode}"
                            + (f": {detail[-1]}" if detail else "")) from None
    except OSError as error:
        raise RegistryError(f"could not run nextflow: {error}") from None
    start = out.find("{")  # the CLI prints a "Searching for ..." line before the JSON
    try:
        data, _ = json.JSONDecoder().raw_decode(out[start:]) if start >= 0 else (None, 0)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise RegistryError(f"`nextflow {' '.join(args)}` printed no JSON object")
    return data


def registry_name(name: str) -> str:
    return name if name.startswith("nf-core/") else f"nf-core/{name}"


def search(query: str, limit: int = 10, runner=run_nextflow) -> list[dict]:
    """Registry hits in the nf-core namespace, revoked ones removed, in Registry order."""
    data = _call(["module", "search", query, "-limit", str(limit), "-output", "json"], runner)
    if not isinstance(data.get("results"), list):
        raise RegistryError("Registry search output has no results list")
    hits = []
    try:
        for r in data["results"]:
            path = r.get("repositoryPath") or ""
            if r.get("revoked") or not (r.get("name") or "").startswith("nf-core/"):
                continue
            hits.append({"kind": "subworkflow" if path.startswith("subworkflows/") else "module",
                         "name": r["name"].removeprefix("nf-core/"), "path": path,
                         "description": ((r.get("description") or "").strip().splitlines() or [""])[0],
                         "tools": [str(t) for t in r.get("tools") or []],
                         "keywords": [str(k) for k in r.get("keywords") or []],
                         "score": r.get("relevanceScore")})
    except (AttributeError, TypeError, KeyError) as error:
        raise RegistryError(f"unexpected Registry search output: {error!r}") from None
    return hits


def view(name: str, runner=run_nextflow) -> dict:
    data = _call(["module", "view", registry_name(name), "-output", "json"], runner)
    if "version" not in data:
        raise RegistryError(f"Registry has no version for {registry_name(name)}")
    _check_view(data)
    return data


def _check_view(data: dict) -> None:
    """Reject a view whose fields don't have the shape derive() reads."""
    ok = (isinstance(data.get("input", []), list)
          and all(isinstance(c, dict) and isinstance(c.get("items", []), list)
                  and all(isinstance(i, dict) for i in c.get("items", [])) for c in data.get("input", []))
          and isinstance(data.get("output", {}), dict)
          and isinstance(data.get("description") or "", str)
          and isinstance(data.get("keywords") or [], list)
          and isinstance(data.get("tools") or [], list))
    if not ok:
        raise RegistryError("unexpected Registry view output shape")


def commit(view_data: dict) -> str | None:
    """The nf-core/modules commit a Registry version was published from (`0.0.0-81b7daa` -> 81b7daa)."""
    m = _VERSION_SHA.search(str(view_data.get("version") or ""))
    return m.group(1) if m else None

