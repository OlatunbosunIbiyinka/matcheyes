"""Seeded synthetic match generator: `generate_match(spec, seed) -> GeneratedMatch`.

The same `(spec, seed, GENERATOR_VERSION)` always produces identical output. A match that breaks
any domain or ingestion invariant raises `GenerationError`; output is never silently repaired.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from matcheyes.domain.entities import MatchInfo
from matcheyes.domain.match import ObservableMatch
from matcheyes.ingestion.invariants import validate_match
from matcheyes_synth.engine import GenerationError, MatchSimulator
from matcheyes_synth.identity import opaque_match_id
from matcheyes_synth.league import LEAGUE_NAME, SEASON, club_profile
from matcheyes_synth.rng import Streams
from matcheyes_synth.squads import build_squad
from matcheyes_synth.truth import GroundTruth, ResolvedScriptedEvent, ScenarioSpec

GENERATOR_VERSION = "0.1.0"

_REFEREE_FIRST = ("A.", "C.", "D.", "J.", "M.", "P.", "R.", "S.")
_REFEREE_LAST = ("Halloway", "Brennick", "Ostley", "Marrow", "Tennant", "Quayle", "Ferrand")
_SEASON_START = datetime(2026, 8, 15, 15, 0, tzinfo=UTC)

__all__ = ["GENERATOR_VERSION", "GeneratedMatch", "GenerationError", "generate_match"]


@dataclass(frozen=True)
class GeneratedMatch:
    observable: ObservableMatch
    truth: GroundTruth


def generate_match(spec: ScenarioSpec, seed: int) -> GeneratedMatch:
    if seed < 0:
        raise GenerationError("seeds must be non-negative")
    match_id = opaque_match_id(spec.scenario_id, seed, GENERATOR_VERSION)
    streams = Streams("match", GENERATOR_VERSION, spec.scenario_id, seed)

    home_profile = club_profile(spec.home_club_id)
    away_profile = club_profile(spec.away_club_id)
    home = build_squad(home_profile, GENERATOR_VERSION)
    away = build_squad(away_profile, GENERATOR_VERSION)

    meta = streams["meta"]
    matchday = meta.randint(1, 38)
    kickoff = _SEASON_START + timedelta(days=7 * (matchday - 1) + meta.choice((0, 1, 2)))
    info = MatchInfo(
        match_id=match_id,
        competition=LEAGUE_NAME,
        season=SEASON,
        matchday=matchday,
        kickoff=kickoff,
        venue=home_profile.ground,
        referee=f"{meta.choice(_REFEREE_FIRST)} {meta.choice(_REFEREE_LAST)}",
        home=home.sheet,
        away=away.sheet,
    )

    simulator = MatchSimulator(spec, match_id, streams, (home_profile, home), (away_profile, away))
    simulator.run()

    observable = ObservableMatch(info=info, events=tuple(simulator.events))
    report = validate_match(observable)
    if not report.ok:
        detail = "; ".join(f"{v.code}@{v.sequence}: {v.message}" for v in report.violations[:10])
        count = len(report.violations)
        raise GenerationError(f"{spec.scenario_id} seed {seed}: {count} violations: {detail}")

    attributes = {**home.attributes, **away.attributes}
    truth = GroundTruth(
        generator_version=GENERATOR_VERSION,
        scenario=spec,
        seed=seed,
        match_id=match_id,
        player_attributes=tuple(
            attributes[p.player_id] for p in (*home.sheet.squad, *away.sheet.squad)
        ),
        state_timeline=tuple(simulator.segments),
        resolved_events=tuple(
            ResolvedScriptedEvent(ref=e.ref, event_ids=tuple(simulator.resolved_events[e.ref]))
            for e in spec.scripted_events
        ),
        intervention_onsets=simulator.onsets_record(),
    )
    return GeneratedMatch(observable=observable, truth=truth)
