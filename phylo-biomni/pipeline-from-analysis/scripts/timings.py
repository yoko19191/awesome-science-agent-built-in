#!/usr/bin/env python3
"""Record how long each step takes, so the handoff can say where the time went.

  timings.py start <step> --reports DIR [--log PATH] [--wait]   # --wait: time spent waiting on the user
  timings.py end <step> --reports DIR [--log PATH]
  timings.py summary --reports DIR [--log PATH] [--json]

Appends one line per event to the local log (DIR/timings.jsonl by default). The summary pairs each
start with the next end of the same step, closes an open step at the next step's start while marking
it incomplete, and separates agent time from time spent waiting on the user. Malformed lines are skipped.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

LOG = "timings.jsonl"


def _log_path(reports: Path, log: Path | None) -> Path:
    return log or reports / LOG


def record(reports: Path, event: str, step: str, wait: bool = False, now: float | None = None,
           log: Path | None = None) -> dict:
    entry = {"event": event, "step": step, "t": round(time.time() if now is None else now, 3)}
    if wait:
        entry["wait"] = True
    path = _log_path(reports, log)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    return entry


def summary(reports: Path, log: Path | None = None) -> dict:
    path = _log_path(reports, log)
    steps: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        try:
            e = json.loads(line)
            event, step, t = e["event"], e["step"], float(e["t"])
        except (ValueError, TypeError, KeyError):  # a truncated or malformed line
            continue
        s = steps.setdefault(step, {"step": step, "open": None, "seconds": None, "wait": False,
                                    "incomplete": False})
        if event == "start":  # pairs with the next end; a start with no end is incomplete
            for other in steps.values():
                if other is not s and other["open"] is not None:
                    other["open"] = None
                    other["incomplete"] = True
            s["open"], s["wait"] = t, s["wait"] or bool(e.get("wait"))
        elif event == "end" and s["open"] is not None:
            s["seconds"] = (s["seconds"] or 0) + t - s["open"]
            s["open"] = None
    rows = [{"step": s["step"], "wait": s["wait"], "incomplete": s["incomplete"] or s["open"] is not None,
             "seconds": None if s["seconds"] is None else round(s["seconds"], 1)} for s in steps.values()]
    timed = [r for r in rows if r["seconds"] is not None]
    return {"steps": rows,
            "agent_seconds": round(sum(r["seconds"] for r in timed if not r["wait"]), 1),
            "wait_seconds": round(sum(r["seconds"] for r in timed if r["wait"]), 1)}


def _minutes(seconds: float | None) -> str:
    if seconds is None:
        return "open"
    return f"{seconds:.0f} s" if seconds < 60 else f"{seconds / 60:.1f} min"


def table(result: dict) -> str:
    lines = ["| Step | Time | |", "|---|---|---|"]
    lines += [f"| {r['step']} | {_minutes(r['seconds'])}{' + open' if r['incomplete'] and r['seconds'] else ''} "
              f"| {'waiting on you' if r['wait'] else ''} |"
              for r in result["steps"]]
    lines += [f"| **agent total** | {_minutes(result['agent_seconds'])} | |",
              f"| **waiting on you** | {_minutes(result['wait_seconds'])} | |"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["start", "end", "summary"])
    parser.add_argument("step", nargs="?")
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--log", type=Path, help="local timings log; use this when reports are on a read-only mount")
    parser.add_argument("--wait", action="store_true", help="the step is time spent waiting on the user")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "summary":
        result = summary(args.reports, args.log)
        print(json.dumps(result, indent=2) if args.json else table(result))
        return 0
    if not args.step:
        parser.error(f"{args.action} needs a step name")
    record(args.reports, args.action, args.step, wait=args.wait, log=args.log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
