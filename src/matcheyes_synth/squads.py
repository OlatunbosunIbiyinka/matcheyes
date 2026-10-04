"""Persistent club squads.

A club's squad (names, shirt numbers, positions) and its hidden player attributes depend only
on the club and the generator version, never on the match seed. Players therefore keep a
stable identity across every match, which later enables favourite-player experiences.
"""

import random
from dataclasses import dataclass

from matcheyes.domain.entities import Player, Position, TeamSheet
from matcheyes_synth.league import ClubProfile
from matcheyes_synth.rng import Streams
from matcheyes_synth.truth import PlayerAttributes

P = Position

FORMATION_POSITIONS: dict[str, tuple[Position, ...]] = {
    "4-3-3": (P.GK, P.RB, P.CB, P.CB, P.LB, P.DM, P.CM, P.CM, P.RW, P.LW, P.ST),
    "4-4-2": (P.GK, P.RB, P.CB, P.CB, P.LB, P.RM, P.CM, P.CM, P.LM, P.ST, P.ST),
    "4-2-3-1": (P.GK, P.RB, P.CB, P.CB, P.LB, P.DM, P.DM, P.AM, P.RW, P.LW, P.ST),
    "3-5-2": (P.GK, P.CB, P.CB, P.CB, P.RWB, P.LWB, P.DM, P.CM, P.CM, P.ST, P.ST),
    "4-1-4-1": (P.GK, P.RB, P.CB, P.CB, P.LB, P.DM, P.RM, P.CM, P.CM, P.LM, P.ST),
    "5-3-2": (P.GK, P.RWB, P.CB, P.CB, P.CB, P.LWB, P.CM, P.CM, P.CM, P.ST, P.ST),
}

BENCH_POSITIONS: tuple[Position, ...] = (P.GK, P.CB, P.RB, P.CM, P.AM, P.RW, P.ST)

DEFENDERS = frozenset({P.RB, P.CB, P.LB, P.RWB, P.LWB})
MIDFIELDERS = frozenset({P.DM, P.CM, P.AM, P.RM, P.LM})
ATTACKERS = frozenset({P.RW, P.LW, P.ST})
WIDE = frozenset({P.RB, P.LB, P.RWB, P.LWB, P.RM, P.LM, P.RW, P.LW})

_FIRST_INITIALS = "ABCDEFGHJKLMNPRSTVW"
_NAME_STARTS = (
    "Ash",
    "Bel",
    "Cor",
    "Dra",
    "Elm",
    "Fen",
    "Gal",
    "Hal",
    "Ivo",
    "Jar",
    "Kel",
    "Lor",
    "Mar",
    "Nor",
    "Orr",
    "Pel",
    "Quin",
    "Ros",
    "Sel",
    "Tor",
    "Ul",
    "Var",
    "Wen",
    "Yar",
    "Zel",
)
_NAME_ENDS = (
    "bourne",
    "dale",
    "ford",
    "gate",
    "hart",
    "ley",
    "lowe",
    "mere",
    "nash",
    "ock",
    "ridge",
    "rowe",
    "shaw",
    "stead",
    "ton",
    "vane",
    "well",
    "wick",
    "worth",
    "by",
)


@dataclass(frozen=True)
class Squad:
    sheet: TeamSheet
    attributes: dict[str, PlayerAttributes]


def _clamp(value: float) -> float:
    return round(min(1.0, max(0.05, value)), 3)


def build_squad(profile: ClubProfile, generator_version: str) -> Squad:
    club_id = profile.club.club_id
    streams = Streams("squad", generator_version, club_id)
    names, numbers, attrs = streams["names"], streams["numbers"], streams["attributes"]

    xi_positions = FORMATION_POSITIONS[profile.default_formation]
    positions = xi_positions + BENCH_POSITIONS
    shirt_numbers = [1, *numbers.sample(range(2, 40), len(positions) - 1)]
    used_names: list[str] = []

    players: list[Player] = []
    attributes: dict[str, PlayerAttributes] = {}
    for position, shirt in zip(positions, shirt_numbers, strict=True):
        name = _unique_name(names, used_names)
        player_id = f"{club_id}-{shirt:02d}"
        players.append(
            Player(player_id=player_id, name=name, shirt_number=shirt, position=position)
        )
        attributes[player_id] = _attributes(player_id, position, profile, attrs)

    sheet = TeamSheet(
        club=profile.club,
        formation=profile.default_formation,
        starting_xi=tuple(players[: len(xi_positions)]),
        bench=tuple(players[len(xi_positions) :]),
    )
    return Squad(sheet=sheet, attributes=attributes)


def _unique_name(rng: random.Random, used: list[str]) -> str:
    while True:
        name = f"{rng.choice(_FIRST_INITIALS)}. {rng.choice(_NAME_STARTS)}{rng.choice(_NAME_ENDS)}"
        if name not in used:
            used.append(name)
            return name


def _attributes(
    player_id: str, position: Position, profile: ClubProfile, rng: random.Random
) -> PlayerAttributes:
    q, stamina = profile.squad_quality, profile.squad_stamina

    def around(centre: float, spread: float = 0.08) -> float:
        return _clamp(rng.gauss(centre, spread))

    finishing = q - 0.25
    pace = q - 0.05
    pressing = q - 0.05
    if position in ATTACKERS:
        finishing, pace = q + 0.1, q + 0.08
    elif position in MIDFIELDERS:
        finishing = q - 0.1
    elif position in DEFENDERS:
        pressing = q + 0.05
    if position is Position.GK:
        finishing, pace, pressing = 0.1, 0.4, 0.2
    if position in WIDE:
        pace += 0.07

    return PlayerAttributes(
        player_id=player_id,
        passing=around(q + (0.05 if position in MIDFIELDERS else 0.0)),
        finishing=around(finishing),
        pace=around(pace),
        stamina=around(stamina),
        pressing=around(pressing),
        composure=around(q),
    )
