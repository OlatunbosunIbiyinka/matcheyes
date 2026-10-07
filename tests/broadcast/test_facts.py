"""Factual moments: Stage 3 key-event facts verbatim, period markers, and an observable score."""

from functools import cache

from matcheyes.analytics.analysis import analyse_match
from matcheyes.analytics.moments import key_event_evidence
from matcheyes.broadcast.contracts import MomentKind, Score
from matcheyes.broadcast.facts import Moment, extract_moments, scoreline
from matcheyes.domain.events import Card, PeriodEnd, PeriodStart, Substitution
from matcheyes.domain.match import ObservableMatch
from tests.synth.generated import generated

SCENARIO = "S05_red_card_reorganisation"


@cache
def match() -> ObservableMatch:
    return generated(SCENARIO).observable


@cache
def moments() -> tuple[Moment, ...]:
    m = match()
    return tuple(extract_moments(m.info, m.events))


def test_period_markers_open_and_close_both_halves_in_order() -> None:
    periods = [x for x in moments() if x.kind in (MomentKind.PERIOD_START, MomentKind.PERIOD_END)]
    assert [x.headline for x in periods] == ["Kick-off", "Half-time", "Second half", "Full time"]
    period_events = [e for e in match().events if isinstance(e, PeriodStart | PeriodEnd)]
    assert [x.event_id for x in periods] == [e.event_id for e in period_events]


def test_dismissals_and_substitutions_are_all_present() -> None:
    subs = [e.event_id for e in match().events if isinstance(e, Substitution)]
    assert [x.event_id for x in moments() if x.kind is MomentKind.SUBSTITUTION] == subs
    reds = [x for x in moments() if x.kind is MomentKind.RED_CARD]
    assert reds and all(x.team_id is not None for x in reds)
    cards = {e.event_id for e in match().events if isinstance(e, Card)}
    assert {x.event_id for x in reds} <= cards


def test_statements_are_stage_3_facts_verbatim() -> None:
    m = match()
    facts = {f.event_ids[0]: f.statement for f in key_event_evidence(m.events, m.info)}
    for x in moments():
        if x.kind not in (MomentKind.PERIOD_START, MomentKind.PERIOD_END):
            assert x.statement == facts[x.event_id]


def test_the_score_agrees_with_stage_2_goals_and_never_decreases() -> None:
    home, away = analyse_match(match()).summaries
    final = moments()[-1].score
    assert (final.home, final.away) == (home.goals, away.goals)
    scores = [(x.score.home, x.score.away) for x in moments()]
    assert scores == sorted(scores)
    assert [x.sequence for x in moments()] == sorted(x.sequence for x in moments())


def test_extracting_in_chunks_equals_extracting_at_once() -> None:
    m = match()
    found: list[Moment] = []
    score: Score | None = None
    for start in range(0, len(m.events), 250):
        chunk = extract_moments(m.info, m.events[start : start + 250], score)
        found += chunk
        score = chunk[-1].score if chunk else score
    assert tuple(found) == moments()


def test_scoreline_names_both_teams() -> None:
    m = match()
    line = scoreline(m.info, Score(home=2, away=1))
    assert "2-1" in line and line.split(" 2-1 ")[0] and line.split(" 2-1 ")[1]
