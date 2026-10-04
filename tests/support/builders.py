"""Hand-built observable match used as the canonical small fixture.

Regenerate the committed copy after changing it:
    uv run python -m tests.support.builders
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from matcheyes.domain.entities import Club, MatchInfo, Player, Position, TeamSheet
from matcheyes.domain.events import (
    BodyPart,
    Card,
    CardType,
    Carry,
    DuelOutcome,
    Foul,
    Interception,
    Pass,
    PassHeight,
    PassKind,
    PassOutcome,
    PeriodEnd,
    PeriodStart,
    Pressure,
    Shot,
    ShotKind,
    ShotOutcome,
    Substitution,
    Tackle,
)
from matcheyes.domain.match import MatchEvent, ObservableMatch
from matcheyes.domain.pitch import Location

MATCH_ID = "fixture-minimal-001"
HOME = "kestrel-bay"
AWAY = "redmarsh"
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "minimal_match"

_XI_POSITIONS = (
    Position.GK,
    Position.RB,
    Position.CB,
    Position.CB,
    Position.LB,
    Position.DM,
    Position.CM,
    Position.CM,
    Position.RW,
    Position.LW,
    Position.ST,
)
_BENCH_POSITIONS = (
    Position.GK,
    Position.CB,
    Position.CM,
    Position.RW,
    Position.ST,
    Position.LB,
    Position.AM,
)


def pid(team: str, number: int) -> str:
    return f"{team}-{number:02d}"


def _sheet(club: Club, formation: str) -> TeamSheet:
    team = club.club_id
    xi = tuple(
        Player(
            player_id=pid(team, n),
            name=f"{club.short_name} Player {n}",
            shirt_number=n,
            position=pos,
        )
        for n, pos in enumerate(_XI_POSITIONS, start=1)
    )
    bench = tuple(
        Player(
            player_id=pid(team, n),
            name=f"{club.short_name} Player {n}",
            shirt_number=n,
            position=pos,
        )
        for n, pos in enumerate(_BENCH_POSITIONS, start=12)
    )
    return TeamSheet(club=club, formation=formation, starting_xi=xi, bench=bench)


def match_info() -> MatchInfo:
    home = Club(
        club_id=HOME,
        name="Kestrel Bay FC",
        short_name="KBY",
        primary_colour="#0B3D91",
        secondary_colour="#F2C14E",
    )
    away = Club(
        club_id=AWAY,
        name="Redmarsh Town",
        short_name="RMT",
        primary_colour="#B22222",
        secondary_colour="#F5F5F5",
    )
    return MatchInfo(
        match_id=MATCH_ID,
        competition="Meridian League",
        season="2026-27",
        matchday=1,
        kickoff=datetime(2026, 8, 15, 15, 0, tzinfo=UTC),
        venue="The Lighthouse Ground",
        referee="A. Referee",
        home=_sheet(home, "4-3-3"),
        away=_sheet(away, "4-3-3"),
    )


class EventStream:
    def __init__(self, match_id: str = MATCH_ID) -> None:
        self.match_id = match_id
        self.events: list[MatchEvent] = []

    def add(self, cls: type[Any], period: int, clock_s: float, **fields: Any) -> MatchEvent:
        sequence = len(self.events) + 1
        event: MatchEvent = cls(
            event_id=f"{self.match_id}-e{sequence:05d}",
            match_id=self.match_id,
            sequence=sequence,
            period=period,
            clock_ms=round(clock_s * 1000),
            **fields,
        )
        self.events.append(event)
        return event


def at(x: float, y: float) -> Location:
    return Location(x=x, y=y)


def _pass(**fields: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "height": PassHeight.GROUND,
        "kind": PassKind.OPEN_PLAY,
        "outcome": PassOutcome.COMPLETE,
    }
    return defaults | fields


def minimal_match() -> ObservableMatch:
    s = EventStream()
    h = lambda n: pid(HOME, n)  # noqa: E731
    a = lambda n: pid(AWAY, n)  # noqa: E731

    s.add(PeriodStart, 1, 0)
    s.add(
        Pass,
        1,
        1,
        team_id=HOME,
        player_id=h(11),
        location=at(52.5, 34),
        end_location=at(45, 30),
        recipient_id=h(7),
        **_pass(kind=PassKind.KICK_OFF),
    )
    s.add(
        Pass,
        1,
        4,
        team_id=HOME,
        player_id=h(7),
        location=at(45, 30),
        end_location=at(60, 50),
        recipient_id=h(9),
        ball_speed_kmh=48.0,
        **_pass(),
    )
    s.add(
        Pressure, 1, 6, team_id=AWAY, player_id=a(8), location=at(44, 20), pressured_player_id=h(9)
    )
    s.add(
        Tackle,
        1,
        7,
        team_id=AWAY,
        player_id=a(8),
        location=at(44, 19),
        outcome=DuelOutcome.WON,
        opponent_id=h(9),
    )
    s.add(
        Pass,
        1,
        9,
        team_id=AWAY,
        player_id=a(8),
        location=at(44, 19),
        end_location=at(70, 40),
        recipient_id=a(10),
        **_pass(),
    )
    s.add(Carry, 1, 12, team_id=AWAY, player_id=a(10), location=at(70, 40), end_location=at(88, 36))
    s.add(
        Shot,
        1,
        14,
        team_id=AWAY,
        player_id=a(10),
        location=at(88, 36),
        end_location=at(105, 35),
        outcome=ShotOutcome.GOAL,
        kind=ShotKind.OPEN_PLAY,
        body_part=BodyPart.RIGHT_FOOT,
        goalkeeper_id=h(1),
        ball_speed_kmh=96.5,
    )
    s.add(
        Pass,
        1,
        60,
        team_id=HOME,
        player_id=h(11),
        location=at(52.5, 34),
        end_location=at(48, 40),
        recipient_id=h(8),
        **_pass(kind=PassKind.KICK_OFF),
    )
    s.add(Foul, 1, 65, team_id=AWAY, player_id=a(4), location=at(55, 42), fouled_player_id=h(8))
    s.add(Card, 1, 70, team_id=AWAY, player_id=a(4), card=CardType.YELLOW)
    s.add(
        Pass,
        1,
        90,
        team_id=HOME,
        player_id=h(8),
        location=at(50, 26),
        end_location=at(80, 30),
        recipient_id=None,
        **_pass(kind=PassKind.FREE_KICK, height=PassHeight.HIGH, outcome=PassOutcome.INCOMPLETE),
    )
    s.add(Interception, 1, 92, team_id=AWAY, player_id=a(5), location=at(25, 38))
    s.add(PeriodEnd, 1, 2760)

    s.add(PeriodStart, 2, 0)
    s.add(
        Pass,
        2,
        1,
        team_id=AWAY,
        player_id=a(11),
        location=at(52.5, 34),
        end_location=at(45, 34),
        recipient_id=a(7),
        **_pass(kind=PassKind.KICK_OFF),
    )
    s.add(Substitution, 2, 30, team_id=HOME, player_id=h(10), replacement_id=h(15))
    s.add(
        Pass,
        2,
        40,
        team_id=AWAY,
        player_id=a(7),
        location=at(45, 34),
        end_location=at(60, 68),
        recipient_id=None,
        **_pass(outcome=PassOutcome.OUT_OF_PLAY),
    )
    s.add(
        Pass,
        2,
        55,
        team_id=HOME,
        player_id=h(15),
        location=at(45, 0),
        end_location=at(50, 8),
        recipient_id=h(2),
        **_pass(kind=PassKind.THROW_IN),
    )
    s.add(
        Shot,
        2,
        70,
        team_id=HOME,
        player_id=h(2),
        location=at(85, 20),
        end_location=at(105, 33),
        outcome=ShotOutcome.SAVED,
        kind=ShotKind.OPEN_PLAY,
        body_part=BodyPart.LEFT_FOOT,
        goalkeeper_id=a(1),
        ball_speed_kmh=88.0,
    )
    s.add(PeriodEnd, 2, 2880)

    return ObservableMatch(info=match_info(), events=tuple(s.events))


if __name__ == "__main__":
    from matcheyes.ingestion.io import write_observable_match

    write_observable_match(minimal_match(), FIXTURE_DIR)
    print(f"wrote {FIXTURE_DIR}")
