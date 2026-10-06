"""Evaluation CLI.

python -m matcheyes_eval stage2 --split development --seeds 20
python -m matcheyes_eval tune --seeds 20
python -m matcheyes_eval stage3 --split development --seeds 20
python -m matcheyes_eval stage4 --split development --seeds 20 --fault-seeds 5
python -m matcheyes_eval redteam --split development --seeds 5
python -m matcheyes_eval stage6 --split development --seeds 2
python -m matcheyes_eval stage7 --split development --seeds 1 [--matches N] [--fresh-replays N]
python -m matcheyes_eval stage7-attribution --split development --seeds 1 [--matches N]
python -m matcheyes_eval llm --profile live            (needs MATCHEYES_LLM_* in the environment)
python -m matcheyes_eval llm --profile overclaiming    (SIMULATED; not a language model)
"""

import argparse
import os
import sys

from matcheyes.agents.llm import LLMSettings, OpenAICompatibleModel
from matcheyes.agents.reasoning import ReasoningModel
from matcheyes.analytics.changepoints import DetectionConfig
from matcheyes_eval.llm_eval import Item, build_items, evaluate_llm, format_llm
from matcheyes_eval.redteam import evaluate_redteam, format_redteam
from matcheyes_eval.simulated import PROFILES, SimulatedModel
from matcheyes_eval.stage2 import (
    build_cases,
    config_with,
    evaluate,
    format_results,
    format_tuning,
    seeds_for,
    tune,
)
from matcheyes_eval.stage3 import evaluate_stage3, format_stage3
from matcheyes_eval.stage4 import evaluate_stage4, format_stage4
from matcheyes_eval.stage6 import evaluate_stage6, format_stage6
from matcheyes_eval.stage7 import evaluate_stage7, format_stage7
from matcheyes_eval.stage7_attribution import evaluate_attribution, format_attribution

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
    ctx = sub.add_parser("stage3", help="Contextual evidence vs the frozen Stage 2 baseline.")
    ctx.add_argument("--split", choices=["development", "held-out"], default="development")
    ctx.add_argument("--seeds", type=int, default=20)
    inv = sub.add_parser("stage4", help="Investigations against planted truth (offline).")
    inv.add_argument("--split", choices=["development", "held-out"], default="development")
    inv.add_argument("--seeds", type=int, default=20)
    inv.add_argument("--fault-seeds", type=int, default=5, help="Seeds for fault injection.")
    red = sub.add_parser("redteam", help="Stage 5 fault injection, false rejection and decoys.")
    red.add_argument("--split", choices=["development", "held-out"], default="development")
    red.add_argument("--seeds", type=int, default=5)
    red.add_argument("--fault-matches", type=int, default=None)
    per = sub.add_parser("stage6", help="Stage 6 audience views and feeds vs verified insights.")
    per.add_argument("--split", choices=["development", "held-out"], default="development")
    per.add_argument("--seeds", type=int, default=2)
    per.add_argument("--tamper-matches", type=int, default=None)
    life = sub.add_parser("stage7", help="Stage 7 lifecycle under delivery perturbations.")
    life.add_argument("--split", choices=["development", "held-out"], default="development")
    life.add_argument("--seeds", type=int, default=1)
    life.add_argument("--matches", type=int, default=None, help="Only the first N matches.")
    life.add_argument("--fresh-replays", type=int, default=1, help="Uncached determinism runs.")
    att = sub.add_parser(
        "stage7-attribution", help="Stage 7: lost insights vs upstream detection limits."
    )
    att.add_argument("--split", choices=["development", "held-out"], default="development")
    att.add_argument("--seeds", type=int, default=1)
    att.add_argument("--matches", type=int, default=None, help="Only the first N matches.")
    llm = sub.add_parser("llm", help="Stage 5 LLM-path evaluation (live or SIMULATED profile).")
    llm.add_argument("--profile", choices=["live", *PROFILES], default="live")
    llm.add_argument("--split", choices=["development", "held-out"], default="development")
    llm.add_argument("--seeds", type=int, default=2)
    llm.add_argument("--matches", type=int, default=7, help="Matches per dataset.")
    llm.add_argument("--top", type=int, default=3, help="Candidates per match.")
    llm.add_argument("--repeats", type=int, default=3)
    llm.add_argument("--repeat-items", type=int, default=10)
    llm.add_argument("--ablation-items", type=int, default=10)
    llm.add_argument("--price-in", type=float, default=None, help="Price per 1k prompt tokens.")
    llm.add_argument("--price-out", type=float, default=None, help="Price per 1k output tokens.")
    args = parser.parse_args(argv)

    if args.command == "llm":
        return _llm(args)

    if args.command == "stage7":
        cases = build_cases(seeds_for(args.split, args.seeds))[: args.matches]
        stage7 = evaluate_stage7(cases, args.split, args.seeds, args.fresh_replays)
        sys.stdout.write(format_stage7(stage7) + "\n")
        return 0
    if args.command == "stage7-attribution":
        cases = build_cases(seeds_for(args.split, args.seeds))[: args.matches]
        attribution = evaluate_attribution(cases, args.split, args.seeds)
        sys.stdout.write(format_attribution(attribution) + "\n")
        return 0
    if args.command == "stage6":
        cases = build_cases(seeds_for(args.split, args.seeds))
        stage6 = evaluate_stage6(cases, args.split, args.seeds, args.tamper_matches)
        sys.stdout.write(format_stage6(stage6) + "\n")
        return 0
    if args.command == "redteam":
        cases = build_cases(seeds_for(args.split, args.seeds))
        red_results = evaluate_redteam(cases, args.split, args.seeds, args.fault_matches)
        sys.stdout.write(format_redteam(red_results) + "\n")
        return 0
    if args.command == "stage4":
        cases = build_cases(seeds_for(args.split, args.seeds))
        stage4 = evaluate_stage4(cases, args.split, args.seeds, fault_seeds=args.fault_seeds)
        sys.stdout.write(format_stage4(stage4) + "\n")
        return 0
    if args.command == "stage3":
        cases = build_cases(seeds_for(args.split, args.seeds))
        stage3 = evaluate_stage3(cases, args.split, args.seeds)
        sys.stdout.write(format_stage3(stage3) + "\n")
        return 0
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


def _llm(args: argparse.Namespace) -> int:
    simulated = args.profile != "live"
    if simulated:
        profile = PROFILES[args.profile]

        def model_for(item: Item, repeat: int) -> ReasoningModel:
            return SimulatedModel(profile, f"{profile.name}/{item.item_id}/{repeat}")

        name = f"SIMULATED-{profile.name}"
    else:
        settings = LLMSettings.from_env(os.environ)
        if settings is None:
            sys.stdout.write(
                "Live LLM not configured (MATCHEYES_LLM_ENDPOINT / _API_KEY / _MODEL unset): "
                "the LLM path was NOT evaluated.\n"
            )
            return 2
        live = OpenAICompatibleModel(settings)

        def model_for(item: Item, repeat: int) -> ReasoningModel:
            return live

        name = live.name
    cases = build_cases(seeds_for(args.split, args.seeds))
    items = build_items(cases, args.matches, args.top)
    price = (args.price_in, args.price_out) if args.price_in and args.price_out else None
    results = evaluate_llm(
        items,
        model_for,
        name,
        simulated,
        repeats=args.repeats,
        repeat_items=args.repeat_items,
        ablation_items=args.ablation_items,
        price_per_1k=price,
    )
    sys.stdout.write(format_llm(results) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
