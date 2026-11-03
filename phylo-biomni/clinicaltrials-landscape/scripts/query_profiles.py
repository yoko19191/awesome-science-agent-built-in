"""Load and resolve versioned query defaults for unattended live runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

PROFILE_SCHEMA = "clinicaltrials-landscape-query-profiles/v1"
PROFILE_PATH = Path(__file__).resolve().parents[1] / "assets" / "query_profiles.json"


def _load_registry() -> dict:
    registry = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    if registry.get("schema") != PROFILE_SCHEMA:
        raise ValueError(f"unsupported query-profile schema: {registry.get('schema')}")
    statuses = registry.get("all_registry_statuses")
    if not isinstance(statuses, list) or len(statuses) != 14 or len(set(statuses)) != 14:
        raise ValueError("query profile registry must declare 14 unique registry statuses")
    profiles = registry.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("query profile registry must contain at least one profile")
    return registry


def all_registry_statuses() -> list[str]:
    """Return a fresh list containing the package-default 14 status values."""
    return list(_load_registry()["all_registry_statuses"])


def _validated_profile(profile: dict) -> dict:
    required = {
        "id",
        "version",
        "reviewed_on",
        "conditions",
        "aliases",
        "prompt_target_triggers",
        "prompt_condition_triggers",
    }
    missing = sorted(required - set(profile))
    if missing:
        raise ValueError(f"query profile missing required fields: {', '.join(missing)}")
    if not profile["conditions"] or not profile["aliases"]:
        raise ValueError(f"query profile {profile['id']} has an empty condition or alias set")
    if len(profile["aliases"]) != len(set(profile["aliases"])):
        raise ValueError(f"query profile {profile['id']} contains duplicate aliases")
    canonical = json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("utf-8")
    validated = dict(profile)
    validated["sha256"] = hashlib.sha256(canonical).hexdigest()
    validated["alias_count"] = len(profile["aliases"])
    return validated


def get_query_profile(profile_id: str) -> dict:
    """Return one validated profile by exact id."""
    for profile in _load_registry()["profiles"]:
        if profile.get("id") == profile_id:
            return _validated_profile(profile)
    raise ValueError(f"unknown query profile: {profile_id}")


def profile_for_prompt(prompt: str) -> dict | None:
    """Resolve only prompts matching both a profile's target and condition triggers."""
    normalized = " ".join(str(prompt).casefold().split())
    if not normalized:
        return None
    matches = []
    for candidate in _load_registry()["profiles"]:
        target_match = any(
            trigger.casefold() in normalized for trigger in candidate["prompt_target_triggers"]
        )
        condition_match = any(
            trigger.casefold() in normalized for trigger in candidate["prompt_condition_triggers"]
        )
        if target_match and condition_match:
            matches.append(_validated_profile(candidate))
    if len(matches) > 1:
        raise ValueError(
            "prompt matches multiple query profiles: " + ", ".join(p["id"] for p in matches)
        )
    return matches[0] if matches else None


def boolean_or_expression(aliases: list[str]) -> str:
    """Render a quoted ClinicalTrials.gov OR expression without losing punctuation."""
    if not aliases:
        raise ValueError("at least one intervention alias is required")
    return " OR ".join('"' + alias.replace('"', '\\"') + '"' for alias in aliases)


def profile_metadata(profile: dict) -> dict:
    """Return the immutable profile fields recorded in coverage_scope.json."""
    return {
        "id": profile["id"],
        "version": int(profile["version"]),
        "reviewed_on": profile["reviewed_on"],
        "alias_count": int(profile["alias_count"]),
        "sha256": profile["sha256"],
    }
