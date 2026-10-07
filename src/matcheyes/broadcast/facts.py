"""Match facts: the observable moments a broadcast surface shows as they happen.

Goals, dismissals and substitutions are Stage 3's own key-event facts (`key_event_evidence`,
label FACT), reused verbatim, so there is one fact path, not two. Period starts and ends are added
here from the period events. The score is a count of observable goal events. Nothing here
interprets a moment: no cause, intent or consequence is stated.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from matcheyes.analytics.evidence import KeyEventType
from matcheyes.analytics.moments import Names, key_event_evidence
from matcheyes.broadcast.contracts import MomentKind, Score
from matcheyes.domain.entities import Identifier, MatchInfo
from matcheyes.domain.events import MatchEvent, PeriodEnd, PeriodStart
from matcheyes.domain.time import MatchInstant

KEY_EVENT_MOMENTS = {
    KeyEventType.GOAL: MomentKind.GOAL,
    KeyEventType.DISMISSAL: MomentKind.RED_CARD,
    KeyEventType.SUBSTITUTION: MomentKind.SUBSTITUTION,
}
HEADLINES = {
    MomentKind.GOAL: "Goal",
    MomentKind.RED_CARD: "Red card",
    MomentKind.SUBSTITUTION: "Substitution",
}
PERIOD_TEXT = {
    (MomentKind.PERIOD_START, 1): ("Kick-off", "The first half has started."),
    (MomentKind.PERIOD_END, 1): ("Half-time", "The first half has ended."),
    (MomentKind.PERIOD_START, 2): ("Second half", "The second half has started."),
    (MomentKind.PERIOD_END, 2): ("Full time", "The second half has ended."),
}


@dataclass(frozen=True)
class Moment:
    event_id: Identifier
    sequence: int
    kind: MomentKind
    at: MatchInstant
    team_id: Identifier | None
    headline: str
    statement: str
    score: Score


def scoreline(info: MatchInfo, score: Score) -> str:
    names = Names(info)
    return (
        f"{names.team(info.home.team_id)} {score.home}-{score.away} {names.team(info.away.team_id)}"
    )


def extract_moments(
    info: MatchInfo, events: Sequence[MatchEvent], start: Score | None = None
) -> list[Moment]:
    """The moments in `events` (consecutive log events in sequence order), in sequence order.
    `start` is the score before the first of them."""
    facts = {f.event_ids[0]: f for f in key_event_evidence(events, info)}
    home, away = (start.home, start.away) if start is not None else (0, 0)
    found = []
    for event in events:
        kind: MomentKind | None = None
        team: Identifier | None = None
        fact = facts.get(event.event_id)
        if fact is not None and fact.event_type in KEY_EVENT_MOMENTS:
            kind, team = KEY_EVENT_MOMENTS[fact.event_type], fact.team_id
            headline, statement = HEADLINES[kind], fact.statement
            if kind is MomentKind.GOAL:
                home += team == info.home.team_id
                away += team == info.away.team_id
        elif isinstance(event, PeriodStart | PeriodEnd):
            kind = (
                MomentKind.PERIOD_START if isinstance(event, PeriodStart) else MomentKind.PERIOD_END
            )
            headline, statement = PERIOD_TEXT[(kind, event.period)]
        if kind is None:
            continue
        found.append(
            Moment(
                event_id=event.event_id,
                sequence=event.sequence,
                kind=kind,
                at=event.instant,
                team_id=team,
                headline=headline,
                statement=statement,
                score=Score(home=home, away=away),
            )
        )
    return found
