"""Requirement 13: invalid matches fail explicitly and are never silently repaired."""

import pytest

from matcheyes.ingestion.invariants import ValidationReport, Violation, ViolationCode
from matcheyes_synth import engine, generator
from matcheyes_synth.generator import GenerationError, generate_match
from matcheyes_synth.scenarios import m, scenario
from matcheyes_synth.truth import ScenarioSpec, ScriptedEvent, ScriptedEventKind


def test_invariant_violation_raises_instead_of_returning_a_repaired_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = ValidationReport(
        (Violation(ViolationCode.RESTART_INVALID, "injected for the test", sequence=7),)
    )
    monkeypatch.setattr(generator, "validate_match", lambda _match: broken)
    with pytest.raises(GenerationError, match="restart_invalid@7"):
        generate_match(scenario("S01_control_balanced"), 1)


def test_impossible_scripted_event_fails_explicitly() -> None:
    """Six scripted substitutions exceed the limit; the generator must refuse, not skip one."""
    subs = tuple(
        ScriptedEvent(
            ref=f"S{n}", kind=ScriptedEventKind.SUBSTITUTION, team_id="thornvale", at=m(50 + n)
        )
        for n in range(6)
    )
    spec = ScenarioSpec(
        scenario_id="S99_impossible_subs",
        title="test",
        purpose="test",
        home_club_id="thornvale",
        away_club_id="brightwater",
        default_seed=1,
        scripted_events=subs,
    )
    with pytest.raises(GenerationError, match="cannot substitute"):
        generate_match(spec, 1)


def test_runaway_simulation_is_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "MAX_EVENTS", 50)
    with pytest.raises(GenerationError, match="runaway"):
        generate_match(scenario("S01_control_balanced"), 1)


def test_unreachable_tolerance_fails_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(engine.TOLERANCE_S, ScriptedEventKind.GOAL, -1)
    with pytest.raises(GenerationError, match="not produced within"):
        generate_match(scenario("S03_game_state_deep_block"), 1)
