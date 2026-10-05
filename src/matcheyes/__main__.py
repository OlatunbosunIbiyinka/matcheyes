"""MatchEyes CLI.

python -m matcheyes                         version banner
python -m matcheyes analyse <match_dir>     deterministic analysis of an observable match
    [--contextual]                          plus Stage 3 contextual candidates
python -m matcheyes investigate <match_dir> Stage 4 investigation of each candidate
    [--llm]                                 use the configured LLM (see agents/llm.py)
"""

import argparse
import os
import sys
from pathlib import Path

from matcheyes import __version__
from matcheyes.agents.llm import LLMSettings, OpenAICompatibleModel
from matcheyes.agents.reasoning import ReasoningModel, RuleBasedReasoner
from matcheyes.analytics.analysis import MatchAnalysis, analyse_match
from matcheyes.analytics.contextual import ContextualAnalysis, analyse_contextual
from matcheyes.ingestion.io import load_observable_match
from matcheyes.orchestration.investigation import MatchInvestigation, investigate_match

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


def format_contextual(contextual: ContextualAnalysis) -> str:
    lines = [
        "",
        f"Contextual candidates {contextual.analytics_version} (ANALYSIS; ranked; claims capped "
        "at associated; context is never evidence):",
    ]
    if not contextual.candidates:
        lines.append("  none: no Stage 2 metric shifts to assess.")
    for c in contextual.candidates:
        lines.append(
            f"  #{c.rank:<3} {c.at.display_minute:>6} [{c.level.value}/{c.strength.value}] "
            f"{c.statement}"
        )
        lines.append(f"         basis: {'; '.join(c.level_basis)}")
    lines.append("Contextual moments:")
    lines += [
        f"  {m.at.display_minute:>6} [{m.level.value}] {m.headline}" for m in contextual.moments
    ]
    return "\n".join(lines)


def format_investigation(investigation: MatchInvestigation) -> str:
    lines = [
        f"Investigations {investigation.investigation_version} - {investigation.match_id} "
        f"(reasoning: {investigation.model}; {len(investigation.not_investigated)} candidates "
        "below the minimum level not investigated)",
    ]
    for record in investigation.records:
        final = record.final
        lines += [
            "",
            f"{final.at.display_minute:>6} [{final.verdict.value} / {final.strength.value}] "
            f"{final.candidate_id}",
            *(f"         {line}" for line in final.narrative.splitlines()),
        ]
    return "\n".join(lines)


def _model(use_llm: bool) -> ReasoningModel | None:
    if not use_llm:
        return RuleBasedReasoner()
    settings = LLMSettings.from_env(os.environ)
    return None if settings is None else OpenAICompatibleModel(settings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="matcheyes")
    sub = parser.add_subparsers(dest="command")
    analyse = sub.add_parser("analyse", help="Analyse an observable match directory.")
    analyse.add_argument("match_dir", type=Path)
    analyse.add_argument("--json", type=Path, help="Also write the full analysis as JSON.")
    analyse.add_argument(
        "--contextual", action="store_true", help="Add Stage 3 contextual candidates."
    )
    investigate = sub.add_parser("investigate", help="Investigate each Stage 3 candidate.")
    investigate.add_argument("match_dir", type=Path)
    investigate.add_argument("--llm", action="store_true", help="Use the configured LLM.")
    investigate.add_argument("--json", type=Path, help="Also write investigations as JSON.")
    args = parser.parse_args(argv)

    if args.command is None:
        sys.stdout.write(f"MatchEyes {__version__} - See beyond the score.\n")
        return 0
    match = load_observable_match(args.match_dir)
    if args.command == "investigate":
        model = _model(args.llm)
        if model is None:
            sys.stderr.write("LLM not configured: set MATCHEYES_LLM_ENDPOINT, _API_KEY, _MODEL.\n")
            return 2
        investigation = investigate_match(match, model)
        if args.json:
            args.json.write_text(
                investigation.model_dump_json(indent=2) + "\n", "utf-8", newline="\n"
            )
        sys.stdout.write(format_investigation(investigation) + "\n")
        return 0
    analysis = analyse_match(match)
    if args.json:
        args.json.write_text(analysis.model_dump_json(indent=2) + "\n", "utf-8", newline="\n")
    output = format_analysis(analysis)
    if args.contextual:
        output += "\n" + format_contextual(analyse_contextual(match, analysis))
    sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
