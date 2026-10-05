"""Possessions of the hand-built minimal match, computed by hand from tests/support/builders.py."""

from matcheyes.analytics.possessions import EndReason, StartType, build_possessions
from matcheyes.analytics.timeline import Timeline
from tests.support.builders import AWAY, HOME, MATCH_ID, minimal_match


def eid(n: int) -> str:
    return f"{MATCH_ID}-e{n:05d}"


def test_minimal_match_has_six_possessions_with_expected_boundaries() -> None:
    possessions = build_possessions(minimal_match().events)
    summary = [(p.team_id, p.start_type, p.end_reason) for p in possessions]
    assert summary == [
        (HOME, StartType.KICK_OFF, EndReason.LOST_IN_PLAY),  # tackled after two passes
        (AWAY, StartType.REGAIN, EndReason.GOAL),  # tackle, pass, carry, goal
        (HOME, StartType.KICK_OFF, EndReason.LOST_IN_PLAY),  # free kick intercepted
        (AWAY, StartType.REGAIN, EndReason.PERIOD_END),  # interception, then half-time
        (AWAY, StartType.KICK_OFF, EndReason.OUT_OF_PLAY),  # pass out, home throw-in
        (HOME, StartType.SET_PIECE, EndReason.SHOT),  # throw-in, saved shot, full time
    ]


def test_non_control_events_belong_to_the_running_possession() -> None:
    first, second, third, *_ = build_possessions(minimal_match().events)
    # e4 is the away pressure before the away tackle (e5)
    assert first.event_ids == (eid(2), eid(3), eid(4))
    assert second.event_ids == (eid(5), eid(6), eid(7), eid(8))
    # foul (e10) and card (e11) sit inside the home possession that resumes with the free kick
    assert third.event_ids == (eid(9), eid(10), eid(11), eid(12))


def test_possession_counts_and_reach() -> None:
    first, second, third, fourth, *_ = build_possessions(minimal_match().events)
    assert (first.on_ball_actions, first.passes, first.completed_passes) == (2, 2, 2)
    assert first.end_location.x == 60  # end of the last completed pass
    assert first.duration_ms == 5_000  # kick-off at 1 s to the pressure at 6 s
    assert (second.passes, second.shots, second.goals) == (1, 1, 1)
    assert second.reached_attacking_third
    assert not second.reached_box  # x = 88 is short of the 88.5 m box line
    assert second.max_x == 88
    assert (third.passes, third.completed_passes) == (2, 1)
    assert fourth.on_ball_actions == 0


def test_timeline_covers_each_period_minute_by_minute() -> None:
    timeline = Timeline(minimal_match().events)
    assert len(timeline) == 47 + 49  # P1 ends at 46:00, P2 at 48:00
    assert timeline.index_of(2, 70_000) == 48
    assert timeline.bins[48].label == "47'"
    assert timeline.period_start_indices() == (0, 47)
    assert [b.index for b in timeline.bins] == list(range(len(timeline)))
