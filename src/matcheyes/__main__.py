"""MatchEyes CLI.

python -m matcheyes                         version banner
python -m matcheyes analyse <match_dir>     deterministic analysis of an observable match
"""

import argparse
import sys
from pathlib import Path

from matcheyes import __version__
from matcheyes.analytics.analysis import MatchAnalysis, analyse_match
from matcheyes.ingestion.io import load_observable_match

SUMMARY_ROWS = (
    ("goals", "Goals", "{:.0f}"),
    ("on_ball_share", "On-ball share", "{:.0%}"),
    ("field_tilt", "Field tilt", "{:.0%}"),
    ("pass_completion", "Open-play pass completion", "{:.0%}"),
    ("progressive_actions", "Progressive passes + carries", "{:.0f}"),
    ("attacking_third_entries", "Attacking-third entries", "{:.0f}"),
    ("box_entries", "Box entries", "{:.0f}"),
    ("shots", "Shots", "{:.0f}"),
    ("high_regains", "High regains", "{:.0f}"),
    ("pressures", "Pressures", "{:.0f}"),
    ("pressure_regain_rate", "Pressure regain rate", "{:.0%}"),
    ("ppda", "PPDA", "{:.1f}"),
    ("defensive_action_height", "Defensive action height (m)", "{:.1f}"),
    ("turnovers", "Turnovers in play", "{:.0f}"),
    ("mean_possession_s", "Mean possession (s)", "{:.1f}"),
)


def format_analysis(analysis: MatchAnalysis) -> str:
    home, away = analysis.summaries
    lines = [f"MatchEyes {analysis.analytics_version} - {analysis.match_id}", ""]
    lines.append(f"{'FACT / ANALYSIS':<32} {home.team_id:>16} {away.team_id:>16}")
    for field, title, fmt in SUMMARY_ROWS:
        values = [getattr(s, field) for s in (home, away)]
        cells = ["n/a" if v is None else fmt.format(v) for v in values]
        lines.append(f"{title:<32} {cells[0]:>16} {cells[1]:>16}")
    lines += ["", "Candidate moments (ANALYSIS; nearby events are context, not causes):"]
    evidence = analysis.evidence_by_id()
    for moment in analysis.moments:
        lines.append(f"  {moment.at.display_minute:>6} [{moment.strength.value}] {moment.headline}")
        for evidence_id in moment.evidence_ids:
            item = evidence[evidence_id]
            lines.append(f"           {item.label.value.upper()}: {item.statement}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="matcheyes")
    sub = parser.add_subparsers(dest="command")
    analyse = sub.add_parser("analyse", help="Analyse an observable match directory.")
    analyse.add_argument("match_dir", type=Path)
    analyse.add_argument("--json", type=Path, help="Also write the full analysis as JSON.")
    args = parser.parse_args(argv)

    if args.command is None:
        sys.stdout.write(f"MatchEyes {__version__} - See beyond the score.\n")
        return 0
    analysis = analyse_match(load_observable_match(args.match_dir))
    if args.json:
        args.json.write_text(analysis.model_dump_json(indent=2) + "\n", "utf-8", newline="\n")
    sys.stdout.write(format_analysis(analysis) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
