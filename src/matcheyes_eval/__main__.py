"""Evaluation CLI.

python -m matcheyes_eval stage2 --split development --seeds 20
python -m matcheyes_eval tune --seeds 20
python -m matcheyes_eval stage3 --split development --seeds 20
python -m matcheyes_eval stage4 --split development --seeds 20 --fault-seeds 5
python -m matcheyes_eval redteam --split development --seeds 5
python -m matcheyes_eval stage6 --split development --seeds 2
python -m matcheyes_eval stage7 --split development --seeds 1 [--matches N] [--fresh-replays N]
python -m matcheyes_eval stage7-attribution --split development --seeds 1 [--matches N]
python -m matcheyes_eval stage8 --split development --seeds 1 [--matches N] [--live-matches N]
python -m matcheyes_eval llm --profile live            (needs MATCHEYES_LLM_* in the environment)
python -m matcheyes_eval llm --profile overclaiming    (SIMULATED; not a language model)
python -m matcheyes_eval stage9 --transcript T --record (live calls; needs MATCHEYES_LLM_*)
python -m matcheyes_eval stage9 --transcript T          (re-score a recorded run offline)
python -m matcheyes_eval stage9-lifecycle --scenario S --recordings DIR  (Stage 7/8 on a recording)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from matcheyes.agents.llm import LLMSettings, live_model
from matcheyes.agents.reasoning import ReasoningModel
from matcheyes.agents.recorded import read_transcript
from matcheyes.agents.split import ROLES
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
from matcheyes_eval.stage8 import evaluate_stage8, format_stage8
from matcheyes_eval.stage9 import (
    evaluate_model_lifecycle,
    evaluate_stage9,
    format_model_lifecycle,
    format_stage9,
    record_items,
    transcript_metadata,
)
from matcheyes_synth.scenarios import scenario

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
    cue = sub.add_parser("stage8", help="Stage 8 broadcast cues and the live surface.")
    cue.add_argument("--split", choices=["development", "held-out"], default="development")
    cue.add_argument("--seeds", type=int, default=1)
    cue.add_argument("--matches", type=int, default=None, help="Only the first N matches.")
    cue.add_argument("--live-matches", type=int, default=1, help="Server + HTTP checks.")
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
    s9 = sub.add_parser("stage9", help="Stage 9: the real model, recorded then scored offline.")
    s9.add_argument("--transcript", type=Path, required=True, help="Transcript (.json[.gz]).")
    s9.add_argument("--record", action="store_true", help="Make live calls and (re)write it.")
    s9.add_argument("--split", choices=["development", "held-out"], default="held-out")
    s9.add_argument("--seeds", type=int, default=2)
    s9.add_argument("--matches", type=int, default=7, help="Matches per dataset.")
    s9.add_argument("--top", type=int, default=3, help="Candidates per match.")
    s9.add_argument("--repeats", type=int, default=3)
    s9.add_argument("--repeat-items", type=int, default=10)
    s9.add_argument("--ablation-items", type=int, default=10)
    s9.add_argument("--tamper-items", type=int, default=8)
    s9.add_argument("--workers", type=int, default=16)
    s9.add_argument(
        "--roles",
        choices=ROLES,
        default="model",
        help="When recording: the model as both roles (B) or only as Challenger (B').",
    )
    s9.add_argument("--price-in", type=float, default=None, help="Price per 1k prompt tokens.")
    s9.add_argument("--price-out", type=float, default=None, help="Price per 1k output tokens.")
    s9l = sub.add_parser(
        "stage9-lifecycle", help="Stage 7/8 properties on a recorded model-backed lifecycle."
    )
    s9l.add_argument("--scenario", required=True, help="Scenario of the recorded demo match.")
    s9l.add_argument("--seed", type=int, default=None, help="Default: the scenario's own.")
    s9l.add_argument("--recordings", type=Path, required=True, help="Dir with pins.json.")
    s9l.add_argument("--no-live", action="store_true", help="Skip the live server checks.")
    args = parser.parse_args(argv)

    if args.command == "stage9-lifecycle":
        return _stage9_lifecycle(args)
    if args.command == "llm":
        return _llm(args)
    if args.command == "stage9":
        return _stage9(args)

    if args.command == "stage7":
        cases = build_cases(seeds_for(args.split, args.seeds))[: args.matches]
        stage7 = evaluate_stage7(cases, args.split, args.seeds, args.fresh_replays)
        sys.stdout.write(format_stage7(stage7) + "\n")
        return 0
    if args.command == "stage8":
        cases = build_cases(seeds_for(args.split, args.seeds))[: args.matches]
        stage8 = evaluate_stage8(cases, args.split, args.seeds, args.live_matches)
        sys.stdout.write(format_stage8(stage8) + "\n")
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
                "Live LLM not configured (MATCHEYES_LLM_ENDPOINT / _MODEL, and _API_KEY unless "
                "_AUTH=entra, unset): the LLM path was NOT evaluated.\n"
            )
            return 2
        live = live_model(settings)

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


def _stage9(args: argparse.Namespace) -> int:
    cases = build_cases(seeds_for(args.split, args.seeds))
    items = build_items(cases, args.matches, args.top)
    shape = {
        "repeats": args.repeats,
        "repeat_items": args.repeat_items,
        "ablation_items": args.ablation_items,
        "tamper_items": args.tamper_items,
    }
    if args.record:
        settings = LLMSettings.from_env(os.environ)
        if settings is None:
            sys.stdout.write("Live LLM not configured: the real model was NOT evaluated.\n")
            return 2
        live = live_model(settings)
        started = time.perf_counter()

        def progress(done: int, total: int) -> None:
            if done % 10 == 0 or done == total:
                sys.stdout.write(
                    f"  {done}/{total} runs, {live.usage.calls} calls, "
                    f"{time.perf_counter() - started:.0f}s\n"
                )
                sys.stdout.flush()

        recorder, runs = record_items(
            items, live, workers=args.workers, progress=progress, roles=args.roles, **shape
        )
        metadata = transcript_metadata(settings.model, live.usage.served, runs, live.usage)
        recorder.transcript({**metadata, "roles": args.roles}).write(args.transcript)
    transcript = read_transcript(args.transcript)
    price = (args.price_in, args.price_out) if args.price_in and args.price_out else None
    roles = transcript.metadata.get("roles", "model")
    results = evaluate_stage9(items, transcript, price_per_1k=price, roles=roles, **shape)
    sys.stdout.write(format_stage9(results) + "\n")
    return 0


def _stage9_lifecycle(args: argparse.Namespace) -> int:
    spec = scenario(args.scenario)
    seed = spec.default_seed if args.seed is None else args.seed
    case = next(c for c in build_cases([seed], [spec]) if c.variant == "planted")
    match_id = case.match.info.match_id
    pins = json.loads((args.recordings / "pins.json").read_text("utf-8"))
    if match_id not in pins:
        sys.stdout.write(f"No recording pinned for {match_id}: nothing evaluated.\n")
        return 2
    transcript = read_transcript(args.recordings / f"{match_id}.transcript.json.gz")
    if transcript.sha256 != pins[match_id]:
        sys.stdout.write(f"Transcript for {match_id} does not match its pin: not evaluated.\n")
        return 1
    results = evaluate_model_lifecycle(case, transcript, live=not args.no_live)
    sys.stdout.write(format_model_lifecycle(results) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
