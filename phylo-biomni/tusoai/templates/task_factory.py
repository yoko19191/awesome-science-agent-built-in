"""Copy into the persistent task workspace and replace the TODO sections."""
from __future__ import annotations

import os
from typing import Any

from tusoai import Tusoai


def build_ai() -> Tusoai:
    """Construct a fresh provider client on every cluster node.

    Do not override ``model_settings`` — the per-stage models (``pdf``,
    ``construction``, ``optimization``) and their ``thinking``,
    ``thinking_tokens``, and ``reasoning_mode`` must stay at the ``Tusoai`` /
    ``from_api_key`` constructor defaults. See SKILL.md invariant G.
    ``temperature`` and ``max_tokens`` remain tunable.
    """
    return Tusoai.from_api_key(
        api_key=os.environ["OPENAI_API_KEY"],
        provider="openai",
        max_tokens=15000,
    )


def build_task_bundle(ai: Tusoai) -> dict[str, Any]:
    """Build tasks exactly once, on the coordinator, then serialize them.

    Every user constraint must already be represented in the validated
    ``task_spec.json`` and propagated into task_description, target hints,
    global_hints, or evaluator assertions before this function returns.
    """
    raise NotImplementedError(
        "Implement build_task_bundle(): create MethodTask/DataTask objects and "
        "return the documented bundle fields."
    )

    # Required return shape:
    # return {
    #     "method_tasks": [method_task],
    #     "data_tasks": [],
    #     "reference_filename": "/mnt/shared-workspace/tusoai/<task>/runner.py",
    #     "task_description": "Concise scientific objective and metric.",
    #     "global_hints": ["Immutable constraints shared by every target."],
    #     "optimize_kwargs": {
    #         "timeout": 300,
    #         "bug_retries": 2,
    #         "prompt_samples": 5,
    #         "min_improvement": 0.01,
    #         # max_islands must be an explicit positive integer, never None
    #         # (SKILL.md invariant G). Default is 3.
    #         "max_islands": 3,
    #         "memory_limit_gb": 12,
    #     },
    #     "construction_cost": 0.0,
    # }
