from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from matcheyes.domain.base import DomainModel

Identifier = str
HexColour = str

MAX_BENCH_SIZE = 12


class Position(StrEnum):
    GK = "GK"
    RB = "RB"
    CB = "CB"
    LB = "LB"
    RWB = "RWB"
    LWB = "LWB"
    DM = "DM"
    CM = "CM"
    AM = "AM"
    RM = "RM"
    LM = "LM"
    RW = "RW"
    LW = "LW"
    ST = "ST"


class Club(DomainModel):
    club_id: Identifier = Field(min_length=1)
    name: str = Field(min_length=1)
    short_name: str = Field(pattern=r"^[A-Z]{3}$")
    primary_colour: HexColour = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    secondary_colour: HexColour = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class Player(DomainModel):
    player_id: Identifier = Field(min_length=1)
    name: str = Field(min_length=1)
    shirt_number: int = Field(ge=1, le=99)
    position: Position


class TeamSheet(DomainModel):
    """Published team sheet: what any broadcaster would know at kick-off."""

    club: Club
    formation: str = Field(pattern=r"^[1-9](-[1-9]){2,4}$")
    starting_xi: tuple[Player, ...] = Field(min_length=11, max_length=11)
    bench: tuple[Player, ...] = Field(max_length=MAX_BENCH_SIZE)

    @property
    def team_id(self) -> Identifier:
        return self.club.club_id

    @property
    def squad(self) -> tuple[Player, ...]:
        return self.starting_xi + self.bench

    @model_validator(mode="after")
    def _check_sheet(self) -> Self:
        squad = self.squad
        if len({p.player_id for p in squad}) != len(squad):
            raise ValueError(f"{self.team_id}: duplicate player_id in squad")
        if len({p.shirt_number for p in squad}) != len(squad):
            raise ValueError(f"{self.team_id}: duplicate shirt number in squad")
        keepers = sum(1 for p in self.starting_xi if p.position is Position.GK)
        if keepers != 1:
            raise ValueError(f"{self.team_id}: starting XI must contain exactly one GK")
        if sum(int(n) for n in self.formation.split("-")) != 10:
            raise ValueError(f"{self.team_id}: formation {self.formation} must sum to 10")
        return self


class MatchInfo(DomainModel):
    match_id: Identifier = Field(min_length=1)
    competition: str = Field(min_length=1)
    season: str = Field(min_length=1)
    matchday: int = Field(ge=1)
    kickoff: datetime
    venue: str = Field(min_length=1)
    referee: str = Field(min_length=1)
    home: TeamSheet
    away: TeamSheet
    synthetic: Literal[True] = True

    @property
    def team_ids(self) -> tuple[Identifier, Identifier]:
        return (self.home.team_id, self.away.team_id)

    def sheet(self, team_id: Identifier) -> TeamSheet:
        if team_id == self.home.team_id:
            return self.home
        if team_id == self.away.team_id:
            return self.away
        raise KeyError(team_id)

    def opponent_of(self, team_id: Identifier) -> Identifier:
        home, away = self.team_ids
        if team_id == home:
            return away
        if team_id == away:
            return home
        raise KeyError(team_id)

    @model_validator(mode="after")
    def _check_teams(self) -> Self:
        if self.home.team_id == self.away.team_id:
            raise ValueError("home and away must be different clubs")
        home_ids = {p.player_id for p in self.home.squad}
        away_ids = {p.player_id for p in self.away.squad}
        if home_ids & away_ids:
            raise ValueError("a player_id appears in both squads")
        return self
