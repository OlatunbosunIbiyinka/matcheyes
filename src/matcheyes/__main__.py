"""MatchEyes CLI.

python -m matcheyes                         version banner
python -m matcheyes analyse <match_dir>     deterministic analysis of an observable match
    [--contextual]                          plus Stage 3 contextual candidates
python -m matcheyes investigate <match_dir> Stage 4 investigation of each candidate
    [--llm]                                 use the configured LLM (see agents/llm.py)
    [--audience fan|broadcaster|analyst]    Stage 6 audience feed (presentation only)
    [--club ID] [--player ID] [--metric M]  preferences; need --audience
python -m matcheyes replay <match_dir>      Stage 7 lifecycle: replay the event log snapshot by
                                            snapshot (one per closed minute) into storylines
    [--json PATH]                           also write the canonical lifecycle state
    [--audience ...] [--club ...] ...       Stage 6 view of the current revisions
python -m matcheyes cues <match_dir>        Stage 8 broadcast cue timeline (offline compile)
    [--audience fan|broadcaster] [--club ID] [--json PATH]
python -m matcheyes serve <root>            Stage 8 live surface: read-only HTTP + SSE replay of
                                            one match dir, or a dir of them (observable only)
    [--host 127.0.0.1] [--port 8000] [--speed 20] [--no-loop]
    [--recordings DIR]                      replay recorded model transcripts (never live)
python -m matcheyes record <match_dir>      Stage 9: record the configured model over every
    --out DIR [--workers 16]                snapshot of a match into a pinned transcript
    [--roles model|challenger]              both roles (B) or only the Challenger (B')
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from pydantic import ValidationError

from matcheyes import __version__
from matcheyes.agents.llm import LLMSettings, live_model
from matcheyes.agents.reasoning import ReasoningModel, RuleBasedReasoner
from matcheyes.agents.recorded import RecordedModel
from matcheyes.agents.split import ROLES
from matcheyes.agents.tools import MatchWorkspace
from matcheyes.analytics.analysis import MatchAnalysis, analyse_match
from matcheyes.analytics.contextual import ContextualAnalysis, analyse_contextual
from matcheyes.analytics.moments import Names
from matcheyes.api.catalog import load_catalog
from matcheyes.api.live import Backend, CachingEvaluator
from matcheyes.api.server import BroadcastApp, BroadcastServer
from matcheyes.broadcast.compiler import compile_timeline
from matcheyes.broadcast.contracts import BROADCAST_AUDIENCES, CueTimeline
from matcheyes.domain.match import ObservableMatch
from matcheyes.ingestion.io import load_observable_match
from matcheyes.lifecycle.engine import replay
from matcheyes.lifecycle.explain import REFERENCE, Explainer, ReasonerIdentity
from matcheyes.lifecycle.feed import LifecycleFeed, audience_feed, lifecycle_feed
from matcheyes.lifecycle.recording import (
    MODEL_CONFIG,
    lifecycle_from,
    record_lifecycle,
    recorded_evaluator,
    recorded_reasoner,
    replayed_lifecycle,
    unavailable_insights,
)
from matcheyes.orchestration.investigation import MatchInvestigation, investigate_match
from matcheyes.personalization.contracts import Audience, PersonalizationProfile
from matcheyes.personalization.feed import build_feed, format_feed

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


def format_lifecycle(feed: LifecycleFeed, storylines: int, revisions: int) -> str:
    as_of = feed.as_of.display_minute if feed.as_of else "-"
    lines = [
        f"Lifecycle - {feed.match_id} (snapshot-anchored; one snapshot per closed minute)",
        f"data: {feed.data_status.value}, watermark {feed.watermark}, {feed.buffered} buffered; "
        f"latest snapshot {feed.snapshot_id or '-'} (end {as_of}); status {feed.status.value}",
        f"{storylines} storylines, {revisions} revisions",
    ]
    if feed.unavailable_reason:
        lines.append(f"current truth unavailable: {feed.unavailable_reason}")
    lines += ["", "Current:"]
    for r in feed.current:
        if r.final is not None:
            lines.append(
                f"  {r.final.at.display_minute:>6} [{r.final.verdict.value} / "
                f"{r.final.strength.value}] {r.storyline_id} r{r.number}"
            )
    lines.append("Withdrawn:")
    lines += [f"  {r.storyline_id} r{r.number} ({r.withdrawal_reason})" for r in feed.withdrawn]
    lines.append("Notices on the latest snapshot:")
    lines += [f"  {n.storyline_id}: {n.text}" for n in feed.notices]
    return "\n".join(lines)


def _replay(args: argparse.Namespace, profile: PersonalizationProfile | None) -> int:
    match = load_observable_match(args.match_dir)
    engine = replay(match.info, match.events)
    state = engine.state
    if args.json:
        args.json.write_text(state.model_dump_json(indent=2) + "\n", "utf-8", newline="\n")
    feed = lifecycle_feed(state, engine.status())
    revisions = sum(len(s.revisions) for s in state.storylines)
    output = format_lifecycle(feed, len(state.storylines), revisions)
    snapshot = engine.latest_snapshot()
    if profile is not None and snapshot is not None:
        view = audience_feed(state, profile, MatchWorkspace.build(snapshot.match))
        output += f"\n\n{format_feed(view, Names(match.info))}"
    sys.stdout.write(output + "\n")
    return 0


def format_cues(timeline: CueTimeline) -> str:
    lines = [
        f"Cue timeline {timeline.timeline_id} - {timeline.match_id} "
        f"({timeline.profile.audience.value}; {len(timeline.cues)} cues, "
        f"{timeline.snapshots} snapshots)",
    ]
    for cue in timeline.cues:
        head = cue.sections[0].text if cue.sections else ""
        lines.append(
            f"  {cue.show_from.display_minute:>6} {cue.kind.value:<10} p{cue.priority:<3} "
            f"{cue.cue_id} {head}"
        )
    return "\n".join(lines)


def _cues(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    match = load_observable_match(args.match_dir)
    try:
        profile = PersonalizationProfile(audience=args.audience, favourite_club_id=args.club)
    except ValidationError:
        parser.error("invalid preference: club")
    if args.club not in (None, match.info.home.team_id, match.info.away.team_id):
        parser.error("--club must be one of the two clubs")
    engine = replay(match.info, match.events)
    timeline = compile_timeline(
        match.info, engine.log.events(), engine.state, profile, engine.status().data_status
    )
    if args.json:
        args.json.write_text(timeline.model_dump_json(indent=2) + "\n", "utf-8", newline="\n")
    sys.stdout.write(format_cues(timeline) + "\n")
    return 0


def _serve(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if not 0 <= args.speed <= 600:
        parser.error("--speed must be between 0 and 600")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    try:
        catalog = load_catalog(args.root)
        backends = _backends(catalog, args.recordings)
    except (OSError, ValueError) as error:
        parser.error(f"cannot load matches: {error}")
    app = BroadcastApp(catalog, args.speed, loop=not args.no_loop, backends=backends)
    server = BroadcastServer((args.host, args.port), app)
    host, port = server.server_address[:2]
    sys.stdout.write(
        f"MatchEyes broadcast surface on http://{host!s}:{port} - {len(catalog)} match(es), "
        f"speed x{args.speed:g}. Ctrl+C to stop.\n"
    )
    for mid, backend in backends.items():
        r = backend.reasoner
        sha = f" transcript {r.transcript_sha256[:12]}" if r.transcript_sha256 else ""
        sys.stdout.write(f"  {mid}: {r.kind} reasoner {r.name}{sha}\n")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


PINS_FILE = "pins.json"
TRANSCRIPT_SUFFIX = ".transcript.json.gz"


def _pins(directory: Path) -> dict[str, str]:
    path = directory / PINS_FILE
    if not path.is_file():
        return {}
    pins = json.loads(path.read_text("utf-8"))
    if not isinstance(pins, dict) or not all(isinstance(v, str) for v in pins.values()):
        raise ValueError(f"{path} must map match IDs to transcript hashes")
    return pins


def _backends(catalog: dict[str, ObservableMatch], recordings: Path | None) -> dict[str, Backend]:
    """Per match: the recorded model if `recordings` pins a transcript for it (replayed, never
    live; a missing or damaged transcript makes its investigations unavailable, it never falls
    back), otherwise the reference reasoner. Either way, an explainer over the same reasoner."""
    pins = _pins(recordings) if recordings else {}
    backends: dict[str, Backend] = {}
    for mid, match in catalog.items():
        if recordings is None or mid not in pins:
            explainer = Explainer(match, RuleBasedReasoner(), REFERENCE)
            backends[mid] = Backend(None, REFERENCE, explainer)
            continue
        model = RecordedModel.load(recordings / f"{mid}{TRANSCRIPT_SUFFIX}", pins[mid])
        if not model.usable:
            sys.stderr.write(f"  {mid}: transcript unusable ({'; '.join(model.problems)})\n")
        reasoner = recorded_reasoner(model)
        identity = ReasonerIdentity(
            kind="recorded-model",
            name=reasoner.name,
            deployment=model.metadata.get("deployment"),
            served_models=model.metadata.get("served_models"),
            transcript_sha256=pins[mid],
        )
        backends[mid] = Backend(
            CachingEvaluator(recorded_evaluator(model)),
            identity,
            Explainer(match, reasoner, identity, MODEL_CONFIG),
        )
    return backends


def _record(args: argparse.Namespace) -> int:
    settings = LLMSettings.from_env(os.environ)
    if settings is None:
        sys.stderr.write("LLM not configured: set MATCHEYES_LLM_ENDPOINT, _MODEL (and _AUTH).\n")
        return 2
    live = live_model(settings)
    match = load_observable_match(args.match_dir)
    match_id = match.info.match_id
    args.out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    def progress(done: int, total: int) -> None:
        if done % 5 == 0 or done == total:
            sys.stdout.write(
                f"  {done}/{total} snapshots, {live.usage.calls} calls, "
                f"{time.perf_counter() - started:.0f}s\n"
            )
            sys.stdout.flush()

    recorder, evaluations = record_lifecycle(match, live, args.workers, progress, args.roles)
    transcript = recorder.transcript(
        {
            "deployment": settings.model,
            "served_models": ",".join(sorted(live.usage.served)),
            "roles": args.roles,
        }
    )
    path = args.out / f"{match_id}{TRANSCRIPT_SUFFIX}"
    sha = transcript.write(path)
    pins = {**_pins(args.out), match_id: sha}
    (args.out / PINS_FILE).write_text(
        json.dumps(dict(sorted(pins.items())), indent=2) + "\n", "utf-8", newline="\n"
    )
    replayed = RecordedModel.load(path, expected_sha256=sha)
    identical = (
        lifecycle_from(match, evaluations).model_dump_json()
        == replayed_lifecycle(match, replayed).model_dump_json()
    )
    insights = sum(len(e.insights) for e in evaluations.values())
    summary = {
        "match_id": match_id,
        "transcript": str(path),
        "transcript_sha256": sha,
        "reasoner": transcript.reasoner,
        "metadata": transcript.metadata,
        "snapshots": len(evaluations),
        "investigations": insights,
        "unavailable": unavailable_insights(list(evaluations.values())),
        "entries": len(transcript.entries),
        "calls": live.usage.calls,
        "prompt_tokens": live.usage.prompt_tokens,
        "completion_tokens": live.usage.completion_tokens,
        "usage_reported": live.usage.reported,
        "seconds": round(time.perf_counter() - started, 1),
        "replay_identical": identical,
        "transcript_problems": replayed.problems,
    }
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    return 0 if identical and not replayed.problems else 1


def _model(use_llm: bool) -> ReasoningModel | None:
    if not use_llm:
        return RuleBasedReasoner()
    settings = LLMSettings.from_env(os.environ)
    return None if settings is None else live_model(settings)


def _profile(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> PersonalizationProfile | None:
    preferences = {
        "favourite_club_id": args.club,
        "favourite_player_id": args.player,
        "favourite_metric": args.metric,
    }
    if args.audience is None:
        if any(v is not None for v in preferences.values()):
            parser.error("--club, --player and --metric need --audience")
        return None
    try:
        return PersonalizationProfile.model_validate(
            {"audience": args.audience, **{k: v for k, v in preferences.items() if v is not None}}
        )
    except ValidationError as error:
        fields = sorted({str(e["loc"][0]) for e in error.errors() if e["loc"]})
        parser.error(f"invalid preference: {', '.join(fields) or 'profile'}")


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
    investigate.add_argument(
        "--audience", choices=[a.value for a in Audience], help="Show a Stage 6 audience feed."
    )
    investigate.add_argument("--club", help="Favourite club ID (needs --audience).")
    investigate.add_argument("--player", help="Favourite player ID (needs --audience).")
    investigate.add_argument("--metric", help="Favourite metric name (needs --audience).")
    lifecycle = sub.add_parser("replay", help="Replay the event log into insight storylines.")
    lifecycle.add_argument("match_dir", type=Path)
    lifecycle.add_argument("--json", type=Path, help="Also write the lifecycle state as JSON.")
    lifecycle.add_argument(
        "--audience", choices=[a.value for a in Audience], help="Show a Stage 6 audience feed."
    )
    lifecycle.add_argument("--club", help="Favourite club ID (needs --audience).")
    lifecycle.add_argument("--player", help="Favourite player ID (needs --audience).")
    lifecycle.add_argument("--metric", help="Favourite metric name (needs --audience).")
    cues = sub.add_parser("cues", help="Compile the broadcast cue timeline of a match.")
    cues.add_argument("match_dir", type=Path)
    cues.add_argument(
        "--audience",
        choices=sorted(a.value for a in BROADCAST_AUDIENCES),
        default=Audience.FAN.value,
    )
    cues.add_argument("--club", help="Favourite club ID.")
    cues.add_argument("--json", type=Path, help="Also write the cue timeline as JSON.")
    serve = sub.add_parser("serve", help="Serve the live broadcast surface (read-only).")
    serve.add_argument("root", type=Path, help="An observable match dir, or a dir of them.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--speed", type=float, default=20.0, help="Match seconds per second.")
    serve.add_argument("--no-loop", action="store_true", help="Replay once, then stay idle.")
    serve.add_argument(
        "--recordings",
        type=Path,
        help="Dir of recorded model transcripts + pins.json; pinned matches replay them.",
    )
    record = sub.add_parser(
        "record", help="Record the configured model over every snapshot of a match (live calls)."
    )
    record.add_argument("match_dir", type=Path)
    record.add_argument("--out", type=Path, required=True, help="Recordings directory.")
    record.add_argument("--workers", type=int, default=16, help="Parallel snapshot evaluations.")
    record.add_argument(
        "--roles",
        choices=ROLES,
        default="model",
        help="The model as Investigator and Challenger, or only as Challenger.",
    )
    args = parser.parse_args(argv)

    if args.command is None:
        sys.stdout.write(f"MatchEyes {__version__} - See beyond the score.\n")
        return 0
    if args.command == "replay":
        return _replay(args, _profile(parser, args))
    if args.command == "cues":
        return _cues(parser, args)
    if args.command == "serve":
        return _serve(parser, args)
    if args.command == "record":
        return _record(args)
    profile = _profile(parser, args) if args.command == "investigate" else None
    match = load_observable_match(args.match_dir)
    if args.command == "investigate":
        model = _model(args.llm)
        if model is None:
            sys.stderr.write("LLM not configured: set MATCHEYES_LLM_ENDPOINT, _API_KEY, _MODEL.\n")
            return 2
        if profile is None:
            investigation = investigate_match(match, model)
        else:
            stage2 = analyse_match(match)
            stage3 = analyse_contextual(match, stage2)
            investigation = investigate_match(match, model, stage2=stage2, stage3=stage3)
        if args.json:
            args.json.write_text(
                investigation.model_dump_json(indent=2) + "\n", "utf-8", newline="\n"
            )
        if profile is None:
            sys.stdout.write(format_investigation(investigation) + "\n")
            return 0
        ws = MatchWorkspace.build(match, stage2, stage3)
        feed = build_feed(investigation.records, profile, ws)
        header = format_investigation(investigation).splitlines()[0]
        sys.stdout.write(f"{header}\n\n{format_feed(feed, Names(match.info))}\n")
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
