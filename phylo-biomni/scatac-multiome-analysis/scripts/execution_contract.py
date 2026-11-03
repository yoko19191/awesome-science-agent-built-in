#!/usr/bin/env python3
"""Derive command-output evidence requirements from selected skill branches."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence


SKILL_ROOT = Path(__file__).resolve().parents[1]


class ExecutionContractError(ValueError):
    """Raised when selected branches cannot resolve one command-output contract."""


def command_outputs_for_selected_branches(
    selected_branch_ids: Sequence[str],
    contract_path: str | Path = SKILL_ROOT / "skill_contract.json",
) -> list[str]:
    """Return branch-selected artifacts that the package command must produce."""
    try:
        contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExecutionContractError(f"cannot read skill contract: {exc}") from exc
    branches = contract.get("clarification_branches")
    execution = contract.get("execution")
    if not isinstance(branches, list) or not isinstance(execution, dict):
        raise ExecutionContractError("skill contract lacks branch or execution declarations")
    declared = execution.get("command_output_paths")
    if not isinstance(declared, list) or not all(
        isinstance(path, str) and path.strip() for path in declared
    ):
        raise ExecutionContractError("execution.command_output_paths is invalid")

    by_id = {
        f"{branch.get('question_id')}:{branch.get('choice_id')}": branch
        for branch in branches
        if isinstance(branch, dict)
    }
    selected_paths: set[str] = set()
    for branch_id in selected_branch_ids:
        branch = by_id.get(branch_id)
        if not isinstance(branch, dict):
            raise ExecutionContractError(f"unknown selected branch {branch_id!r}")
        paths = branch.get("artifact_paths")
        if not isinstance(paths, list):
            raise ExecutionContractError(f"selected branch {branch_id!r} has no artifact paths")
        selected_paths.update(str(path) for path in paths)
    outputs = [path for path in declared if path in selected_paths]
    if not outputs:
        raise ExecutionContractError("selected branches resolve to no command outputs")
    return outputs
