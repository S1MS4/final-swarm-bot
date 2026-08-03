"""Score a session log, so tuning is driven by numbers instead of impressions.

Reads runs/<timestamp>/log.jsonl and reports the metrics that actually matter:
how fast picks happen, how often clicks land, how often a pick can be named and
measured, and where the wall-clock went.

    python -m tools.diagnose                 # newest run
    python -m tools.diagnose runs/2026...    # a specific run
    python -m tools.diagnose --all           # compare every run, oldest first
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from swarmbot import config


def load(directory: Path) -> list[dict]:
    path = directory / "log.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def _t(event: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(event["t"])
    except (KeyError, ValueError):
        return None


def score(events: list[dict]) -> dict:
    kinds: dict[str, int] = {}
    for e in events:
        kinds[e.get("kind", "?")] = kinds.get(e.get("kind", "?"), 0) + 1

    stamps = [t for t in (_t(e) for e in events) if t]
    duration = (max(stamps) - min(stamps)).total_seconds() if len(stamps) > 1 else 0.0

    offers = [e for e in events if e.get("kind") == "offers"]
    measured = [e for e in events if e.get("kind") == "measured"]
    good = [e for e in measured if e.get("confidence") in ("ok", "ambiguous")]
    errors = [e for e in events if e.get("kind") == "error"]
    traces = [e for e in events if e.get("kind") == "trace"]

    # Titles unreadable -> the pick cannot be recorded at all.
    unnamed = sum(1 for e in offers if "card0(" in str(e.get("options", "")))

    # Time from the animation-skip click to the card click, per pick.
    latencies = []
    pending = None
    for e in events:
        if e.get("kind") == "click" and e.get("target") == "skip-animation":
            pending = _t(e)
        elif e.get("kind") == "click" and str(e.get("target", "")).startswith("card"):
            if pending is not None:
                latencies.append((_t(e) - pending).total_seconds())
                pending = None

    by_error: dict[str, int] = {}
    for e in errors:
        key = str(e.get("message", "?"))[:44]
        by_error[key] = by_error.get(key, 0) + 1

    landed = [e for e in traces if e.get("landed")]

    return {
        "duration_s": round(duration, 1),
        "waves": max([e.get("n", 0) for e in events if e.get("kind") == "wave"] or [0]),
        "offers": len(offers),
        "picks_per_min": round(len(offers) / duration * 60, 1) if duration else 0.0,
        "pick_latency_med": round(sorted(latencies)[len(latencies) // 2], 2) if latencies else None,
        "pick_latency_max": round(max(latencies), 2) if latencies else None,
        "named_pct": round(100 * (len(offers) - unnamed) / len(offers)) if offers else None,
        "measured": len(measured),
        "usable_pct": round(100 * len(good) / len(measured)) if measured else None,
        "gear_retries": kinds.get("gear-retry", 0),
        "clicks": kinds.get("click", 0),
        "click_landed_pct": round(100 * len(landed) / len(traces)) if traces else None,
        "errors": len(errors),
        "top_errors": sorted(by_error.items(), key=lambda kv: -kv[1])[:4],
    }


def report(directory: Path, verbose: bool = True) -> dict | None:
    events = load(directory)
    if not events:
        return None
    s = score(events)
    if not verbose:
        return s

    print(f"\n=== {directory.name} ===")
    print(f"  duration            {s['duration_s']}s, reached wave {s['waves']}")
    print(f"  offers handled      {s['offers']}  ({s['picks_per_min']}/min)")
    print(f"  pick latency        median {s['pick_latency_med']}s   worst {s['pick_latency_max']}s")
    print(f"  titles readable     {s['named_pct']}%")
    print(f"  measurements        {s['measured']} taken, {s['usable_pct']}% usable")
    if s["click_landed_pct"] is not None:
        print(f"  clicks landed       {s['click_landed_pct']}%  (--trace)")
    print(f"  gear retries        {s['gear_retries']}")
    print(f"  errors              {s['errors']}")
    for message, count in s["top_errors"]:
        print(f"      {count:3d}x  {message}")
    return s


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.diagnose", description=__doc__.splitlines()[0])
    parser.add_argument("run", nargs="?", type=Path)
    parser.add_argument("--all", action="store_true", help="every run, oldest first")
    args = parser.parse_args(argv)

    runs = sorted(d for d in config.RUNS.iterdir() if d.is_dir() and (d / "log.jsonl").exists())
    if not runs:
        print("No runs found.")
        return 1

    targets = runs if args.all else [args.run or runs[-1]]
    for directory in targets:
        report(directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
