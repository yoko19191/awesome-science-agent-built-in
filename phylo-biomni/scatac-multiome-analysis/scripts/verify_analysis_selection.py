#!/usr/bin/env python3
"""Verify the sealed scATAC selection and its configured results root."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import input_selection


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    payload = input_selection.verify_manifest(args.manifest)
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if Path(config["project"]["results_dir"]).resolve() != Path(payload["result_root"]):
        raise input_selection.InputSelectionError(
            "configured results root and input selection manifest disagree"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
