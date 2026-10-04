"""Requirements 2-9: scenario catalogue, activation and timing, decoys, controls, invariants."""

from itertools import pairwise

import pytest

from matcheyes.domain.events import (
    Card,
    CardType,
    FormationChange,
    MatchEvent,
    Pass,
    PassOutcome,
    Shot,
    ShotOutcome,
    Substitution,
)
from matcheyes.domain.match import ObservableMatch
from matcheyes.domain.time import MatchInstant
from matcheyes.ingestion.invariants import validate_match
from matcheyes_synth.engine import TOLERANCE_S
from matcheyes_synth.generator import GeneratedMatch
from matcheyes_synth.scenarios import scenario
from matcheyes_synth.state import match_minute
from matcheyes_synth.truth import ScriptedEventKind, StateDriver, TeamStateSegment
from tests.synth.generated import SCENARIO_IDS, sample


def seconds_between(a: MatchInstant, b: MatchInstant) -> float:
    return (match_minute(b) - match_minute(a)) * 60


def events_by_id(match: ObservableMatch) -> dict[str, MatchEvent]:
    return {e.event_id: e for e in match.events}


def segments(match: GeneratedMatch, team_id: str) -> list[TeamStateSegment]:
    return [s for s in match.truth.state_timeline if s.team_id == team_id]


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_every_generated_match_passes_all_invariants(scenario_id: str) -> None:
    for match in sample(scenario_id):
        report = validate_match(match.observable)
        assert report.ok, report.violations[:5]
        assert match.observable.info.synthetic is True


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_possession_sequences_alternate_between_both_teams(scenario_id: str) -> None:
    match = sample(scenario_id)[0].observable
    on_ball = [
        e for e in match.events if isinstance(e, Pass | Shot) or e.type in ("carry", "take_on")
    ]
    teams = [getattr(e, "team_id", "") for e in on_ball]
    changes = sum(a != b for a, b in pairwise(teams))
    assert set(teams) == set(match.info.team_ids)
    assert 120 <= changes <= 600, f"{changes} possession changes"


EXPECTED_TYPE: dict[ScriptedEventKind, type] = {
    ScriptedEventKind.GOAL: Shot,
    ScriptedEventKind.SUBSTITUTION: Substitution,
    ScriptedEventKind.FORMATION_CHANGE: FormationChange,
}


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_scripted_events_are_emitted_by_the_right_team_at_the_right_time(scenario_id: str) -> None:
    spec = scenario(scenario_id)
    for match in sample(scenario_id):
        lookup = events_by_id(match.observable)
        resolved = {r.ref: r.event_ids for r in match.truth.resolved_events}
        assert set(resolved) == {e.ref for e in spec.scripted_events}
        for scripted in spec.scripted_events:
            emitted = [lookup[event_id] for event_id in resolved[scripted.ref]]
            for event in emitted:
                assert getattr(event, "team_id", None) == scripted.team_id
                assert event.instant.sort_key >= scripted.at.sort_key
            first = emitted[0]
            if scripted.kind is ScriptedEventKind.RED_CARD:
                assert [e.card for e in emitted if isinstance(e, Card)] == [CardType.RED]
            elif scripted.kind is ScriptedEventKind.TURNOVER_CLUSTER:
                assert scripted.until is not None
                for event in emitted:
                    assert isinstance(event, Pass)
                    assert event.outcome is not PassOutcome.COMPLETE
                    assert event.instant.sort_key < scripted.until.sort_key
            else:
                assert isinstance(first, EXPECTED_TYPE[scripted.kind])
            if scripted.kind is ScriptedEventKind.GOAL:
                assert isinstance(first, Shot) and first.outcome is ShotOutcome.GOAL
            if scripted.kind in TOLERANCE_S:
                assert seconds_between(scripted.at, first.instant) <= TOLERANCE_S[scripted.kind]


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_planted_causes_begin_after_their_trigger_and_never_before(scenario_id: str) -> None:
    spec = scenario(scenario_id)
    for match in sample(scenario_id):
        lookup = events_by_id(match.observable)
        onsets = {o.intervention_id: o.start for o in match.truth.intervention_onsets}
        assert set(onsets) == {i.intervention_id for i in spec.interventions}
        resolved_at = {r.ref: lookup[r.event_ids[0]].instant for r in match.truth.resolved_events}
        for intervention in spec.interventions:
            onset = onsets[intervention.intervention_id]
            assert onset.sort_key >= intervention.start.sort_key
            if intervention.trigger_ref is not None:
                assert onset.sort_key >= resolved_at[intervention.trigger_ref].sort_key
            for segment in segments(match, intervention.team_id):
                planted = intervention.intervention_id in segment.intervention_ids
                if segment.start.sort_key < onset.sort_key:
                    assert not planted, f"{intervention.intervention_id} active before onset"
                else:
                    assert planted, f"{intervention.intervention_id} inactive after onset"


def test_control_match_has_no_planted_cause_but_has_background_dynamics() -> None:
    for match in sample("S01_control_balanced"):
        drivers = {d for s in match.truth.state_timeline for d in s.drivers}
        assert StateDriver.INTERVENTION not in drivers
        assert drivers & {StateDriver.GAME_STATE, StateDriver.FATIGUE}
        assert match.truth.resolved_events == ()
        assert match.truth.intervention_onsets == ()


@pytest.mark.parametrize(
    ("scenario_id", "decoy_team"),
    [("S07_against_the_run_of_play", "redmarsh"), ("S08_coincidence_decoy", "brightwater")],
)
def test_decoy_teams_never_receive_a_planted_cause(scenario_id: str, decoy_team: str) -> None:
    for match in sample(scenario_id):
        for segment in segments(match, decoy_team):
            assert StateDriver.INTERVENTION not in segment.drivers


def test_coincidence_decoy_noise_is_observable() -> None:
    """The turnover cluster must actually show, otherwise the decoy tests nothing."""
    for match in sample("S08_coincidence_decoy"):
        cluster = next(r for r in match.truth.resolved_events if r.ref == "T1")
        assert len(cluster.event_ids) >= 2


def test_against_the_run_goal_falls_inside_the_described_window() -> None:
    spec = scenario("S07_against_the_run_of_play")
    insight = spec.expected_insights[0]
    for match in sample(spec.scenario_id):
        goal_id = next(r for r in match.truth.resolved_events if r.ref == "G1").event_ids[0]
        goal = events_by_id(match.observable)[goal_id]
        assert goal.instant.sort_key >= insight.window_start.sort_key
        assert (
            seconds_between(insight.window_start, goal.instant)
            <= TOLERANCE_S[ScriptedEventKind.GOAL]
        )


def test_dismissed_player_takes_no_further_part() -> None:
    for match in sample("S05_red_card_reorganisation"):
        red = next(
            e for e in match.observable.events if isinstance(e, Card) and e.card is CardType.RED
        )
        later = {
            getattr(e, "player_id", None)
            for e in match.observable.events
            if e.sequence > red.sequence and getattr(e, "team_id", None) == red.team_id
        }
        assert red.player_id not in later
