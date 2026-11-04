#!/usr/bin/env python3
"""Regression for Review Stop 4A finding 2: SKILL.md must carry an explicit '## When to Use This
Skill' section (supported inputs/routes, outputs, and material exclusions routing to sibling skills).

The v2 source stated usage only as a bolded line inside '## Boundary' -- the static usage-trigger
check flagged it as missing. This test reproduces that: it FAILS on the frozen source (no H2 section)
and PASSES on the repaired package. Point it at any package with SKILL_PKG=<pkg-dir>.

Run:  python3 assets/eval/test_when_to_use_section.py
Exit: 0 all passed | 1 a test failed | 2 everything skipped (a failure, not a pass).
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

_PKG = pathlib.Path(os.environ.get("SKILL_PKG", pathlib.Path(__file__).resolve().parents[2]))
_SKILL = _PKG / "SKILL.md"

_SIBLINGS = ["drug-bioactivity-chembl", "large-scale-virtual-screening",
             "screen-hit-triage-interference", "matched-molecular-pairs"]


def test_when_to_use_section_present():
    text = _SKILL.read_text(encoding="utf-8", errors="replace")
    # RIGHT: an explicit H2 usage-trigger heading exists (the source had none -> this fails there).
    m = re.search(r"(?im)^##\s+When to Use This Skill\s*$", text)
    assert m, "SKILL.md has no '## When to Use This Skill' H2 section (finding 2)"
    body = text[m.end():]
    nxt = re.search(r"(?m)^##\s+", body)
    section = (body[:nxt.start()] if nxt else body).lower()
    # RIGHT: the section states when to use, and the material exclusions (when NOT to use).
    assert "use this skill when" in section, "section does not state when to USE the skill"
    assert ("do not use this skill" in section) or ("not use this skill" in section), \
        "section states no explicit 'do not use this skill' exclusion"
    # DISCRIMINATION: exclusions must route to at least two sibling skills (not just say 'don't').
    routed = [s for s in _SIBLINGS if s in section]
    assert len(routed) >= 2, f"exclusions do not route to sibling skills (found {routed})"
    return f"When-to-Use section present; routes to {len(routed)} sibling skills"


TESTS = [test_when_to_use_section_present]


def main() -> int:
    passed = failed = skipped = 0
    for t in TESTS:
        try:
            msg = t()
            print(f"PASS {t.__name__}: {msg}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:                     # missing file / unreadable package -> failure
            print(f"FAIL {t.__name__}: unexpected {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped  (package: {_PKG})")
    if failed:
        return 1
    if passed == 0:
        return 2   # nothing ran at all
    return 0


if __name__ == "__main__":
    sys.exit(main())
