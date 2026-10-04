"""Command line: generate matches, report realism, and measure planted effect sizes.

python -m matcheyes_synth generate --scenario S02_press_surge --seed 202
python -m matcheyes_synth generate --all --split dev --count 20
python -m matcheyes_synth realism --split dev --count 20
python -m matcheyes_synth effects --count 20
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from matcheyes_synth.effects import EFFECT_CHECKS, measure_effect
from matcheyes_synth.generator import GENERATOR_VERSION, GenerationError, generate_match
from matcheyes_synth.io import ManifestEntry, write_generated_match, write_manifest
from matcheyes_synth.realism import format_table, summarise
from matcheyes_synth.scenarios import CATALOGUE, scenario
from matcheyes_synth.seeds import development_seeds, held_out_seeds, is_held_out

DEFAULT_ROOT = Path("data/generated")


def _seeds(split: str, count: int) -> list[int]:
    return list(held_out_seeds(count) if split == "held-out" else development_seeds(count))


def _generate(args: argparse.Namespace) -> int:
    specs = list(CATALOGUE) if args.all else [scenario(args.scenario)]
    if args.seed is not None:
        seeds = [args.seed]
    elif args.all or args.count:
        seeds = _seeds(args.split, args.count or 1)
    else:
        seeds = []
    entries: list[ManifestEntry] = []
    for spec in specs:
        for seed in seeds or [spec.default_seed]:
            generated = generate_match(spec, seed)
            entries.append(write_generated_match(generated, args.out, held_out=is_held_out(seed)))
            print(
                f"{generated.observable.info.match_id}  {len(generated.observable.events)} events"
            )
    manifest = write_manifest(entries, args.out)
    print(f"wrote {len(entries)} matches; manifest: {manifest}")
    return 0


def _realism(args: argparse.Namespace) -> int:
    seeds = _seeds(args.split, args.count)
    matches = [generate_match(spec, seed).observable for spec in CATALOGUE for seed in seeds]
    print(f"generator {GENERATOR_VERSION}, split={args.split}, {len(matches)} matches")
    results = summarise(matches)
    print(format_table(results))
    return 0 if all(r.passed for r in results) else 1


def _effects(args: argparse.Namespace) -> int:
    seeds = list(development_seeds(args.count))
    print("| Scenario | Proxy | Role | Expected | Mean paired difference | Paired t | Sign |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for check in EFFECT_CHECKS:
        result = measure_effect(scenario(check.scenario_id), check, seeds)
        sign_ok = result.mean * check.expected_sign > 0
        role = "primary" if check.primary else "secondary"
        print(
            f"| {check.scenario_id} | {check.label} | {role} "
            f"| {'+' if check.expected_sign > 0 else '-'} | {result.mean:+.3f} "
            f"| {result.standardised:+.2f} | {'ok' if sign_ok else 'WRONG'} |"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m matcheyes_synth")
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="write observable matches and answer keys")
    target = generate.add_mutually_exclusive_group(required=True)
    target.add_argument("--scenario")
    target.add_argument("--all", action="store_true")
    generate.add_argument("--seed", type=int)
    generate.add_argument("--split", choices=("dev", "held-out"), default="dev")
    generate.add_argument("--count", type=int, default=0)
    generate.add_argument("--out", type=Path, default=DEFAULT_ROOT)

    realism = commands.add_parser("realism", help="realism distributions against the bands")
    realism.add_argument("--split", choices=("dev", "held-out"), default="dev")
    realism.add_argument("--count", type=int, default=20)

    effects = commands.add_parser("effects", help="planted effect sizes (development seeds)")
    effects.add_argument("--count", type=int, default=20)

    args = parser.parse_args(argv)
    handlers = {"generate": _generate, "realism": _realism, "effects": _effects}
    try:
        return handlers[args.command](args)
    except GenerationError as error:
        print(f"generation failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
