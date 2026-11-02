#!/usr/bin/env python3
"""Explicit unsupported adapter for unpaired/diagonal GLUE requests."""

from __future__ import annotations

import argparse
import json
import os


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    config = __import__("yaml").safe_load(open(parser.parse_args().config))
    table_dir = os.path.join(config["project"]["outdir"], "tables")
    os.makedirs(table_dir, exist_ok=True)
    payload = {
        "ran": False,
        "method": "GLUE",
        "capability_status": "unsupported",
        "reason": (
            "unsupported: no packaged guidance-graph construction, GLUE model training, "
            "or genuine label-transfer implementation"
        ),
    }
    with open(os.path.join(table_dir, "glue_status.json"), "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2)
    print("glue_status:", payload["reason"])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
