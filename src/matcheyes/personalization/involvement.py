"""Favourite club, player and metric: what the observable match says about this insight.

Everything here is a lookup. A club is relevant only if it plays in this match; a player only if
they act in an event the insight cites (`FinalInsight.event_ids`); a metric only if it is the
candidate's metric. Being in the squad, or on the pitch, is not involvement.
"""

from typing import Literal

from matcheyes.agents.contracts import FinalInsight
from matcheyes.agents.tools import MatchWorkspace
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier
from matcheyes.domain.events import MatchEvent
from matcheyes.personalization.contracts import PersonalizationProfile

ClubRole = Literal["team", "opponent"]


class Involvement(DomainModel):
    club_role: ClubRole | None
    player_name: str | None
    player_events: tuple[Identifier, ...]
    metric: bool


def actors(event: MatchEvent) -> frozenset[Identifier]:
    """Players who perform the event: the actor, and an incoming substitute."""
    ids = {getattr(event, "player_id", None), getattr(event, "replacement_id", None)}
    return frozenset(i for i in ids if isinstance(i, str))


def involvement(
    final: FinalInsight, profile: PersonalizationProfile, ws: MatchWorkspace
) -> Involvement:
    club = profile.favourite_club_id
    role: ClubRole | None = None
    if club in ws.info.team_ids:
        role = "team" if club == final.team_id else "opponent"
    squads = {p.player_id: p.name for s in (ws.info.home, ws.info.away) for p in s.squad}
    player = profile.favourite_player_id
    events: tuple[Identifier, ...] = ()
    if player is not None and player in squads:
        events = tuple(
            e for e in final.event_ids if e in ws.events and player in actors(ws.events[e])
        )
    candidate = ws.candidates.get(final.candidate_id)
    return Involvement(
        club_role=role,
        player_name=squads.get(player) if player is not None else None,
        player_events=events,
        metric=candidate is not None and profile.favourite_metric == candidate.metric,
    )
