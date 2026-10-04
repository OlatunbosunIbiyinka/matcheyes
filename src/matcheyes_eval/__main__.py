"""Evaluation CLI.

python -m matcheyes_eval stage2 --split development --seeds 20
python -m matcheyes_eval tune --seeds 20
"""

import argparse
import sys

from matcheyes.analytics.changepoints import DetectionConfig
from matcheyes_eval.stage2 import (
    build_cases,
    config_with,
    evaluate,
    format_results,
    format_tuning,
    seeds_for,
    tune,
)

TUNING_GRID = [
    (z, w, b) for w, b in ((10, 10), (10, 20), (15, 15), (15, 30)) for z in (2.0, 2.5, 3.0)
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="matcheyes_eval")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("stage2", help="Score analytics against planted truth.")
    run.add_argument("--split", choices=["development", "held-out"], default="development")
    run.add_argument("--seeds", type=int, default=20)
    defaults = DetectionConfig()
    run.add_argument("--z", type=float, default=defaults.z_threshold)
    run.add_argument("--window", type=int, default=defaults.window_bins)
    run.add_argument("--baseline", type=int, default=defaults.baseline_bins)
    grid = sub.add_parser("tune", help="Grid search on development seeds only.")
    grid.add_argument("--seeds", type=int, default=20)
    args = parser.parse_args(argv)

    if args.command == "tune":
        cases = build_cases(seeds_for("development", args.seeds))
        sys.stdout.write(format_tuning(tune(cases, TUNING_GRID, args.seeds)) + "\n")
        return 0
    cases = build_cases(seeds_for(args.split, args.seeds))
    results = evaluate(
        cases, args.split, args.seeds, config_with(args.z, args.window, args.baseline)
    )
    sys.stdout.write(format_results(results) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
