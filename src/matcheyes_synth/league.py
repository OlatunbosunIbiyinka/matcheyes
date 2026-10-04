"""The Meridian League: an entirely fictional competition.

Each club has an observable identity (name, colours, ground, default formation) and a hidden
style profile that drives the generator. Names are invented; any resemblance to a real club
is unintentional, and no real crests, kits or player names are used.
"""

from pydantic import Field

from matcheyes.domain.entities import Club
from matcheyes_synth.truth import HiddenTeamState, TruthModel

LEAGUE_NAME = "Meridian League"
SEASON = "2026-27"


class ClubProfile(TruthModel):
    club: Club
    ground: str
    default_formation: str
    style: HiddenTeamState
    squad_quality: float = Field(ge=0.0, le=1.0)
    squad_stamina: float = Field(ge=0.0, le=1.0)
    identity: str = Field(description="One-line tactical identity, for humans only")


def _club(club_id: str, name: str, short: str, primary: str, secondary: str) -> Club:
    return Club(
        club_id=club_id,
        name=name,
        short_name=short,
        primary_colour=primary,
        secondary_colour=secondary,
    )


def _style(
    press: float, line: float, tempo: float, direct: float, width: float, risk: float
) -> HiddenTeamState:
    return HiddenTeamState(
        press_intensity=press,
        defensive_line=line,
        tempo=tempo,
        directness=direct,
        width=width,
        risk_appetite=risk,
        execution=0.5,
    )


CLUBS: tuple[ClubProfile, ...] = (
    ClubProfile(
        club=_club("kestrel-bay", "Kestrel Bay FC", "KBY", "#0B3D91", "#F2C14E"),
        ground="The Lighthouse Ground",
        default_formation="4-3-3",
        style=_style(press=0.80, line=0.75, tempo=0.70, direct=0.35, width=0.60, risk=0.60),
        squad_quality=0.80,
        squad_stamina=0.75,
        identity="High press, possession, aggressive defensive line",
    ),
    ClubProfile(
        club=_club("northmoor", "Northmoor Athletic", "NMA", "#7A1F2B", "#E8E1D3"),
        ground="Moorgate Park",
        default_formation="4-4-2",
        style=_style(press=0.35, line=0.35, tempo=0.55, direct=0.75, width=0.50, risk=0.40),
        squad_quality=0.60,
        squad_stamina=0.70,
        identity="Direct, compact mid-block, quick to go long",
    ),
    ClubProfile(
        club=_club("saltmarsh", "Saltmarsh Rovers", "SMR", "#1E6B52", "#FFFFFF"),
        ground="Estuary Road",
        default_formation="4-2-3-1",
        style=_style(press=0.55, line=0.55, tempo=0.75, direct=0.50, width=0.80, risk=0.55),
        squad_quality=0.65,
        squad_stamina=0.65,
        identity="Wide, high-tempo, overlapping full-backs",
    ),
    ClubProfile(
        club=_club("thornvale", "Thornvale City", "TVC", "#5B2C83", "#C0C0C0"),
        ground="Thornvale Arena",
        default_formation="4-2-3-1",
        style=_style(press=0.50, line=0.55, tempo=0.50, direct=0.45, width=0.55, risk=0.50),
        squad_quality=0.70,
        squad_stamina=0.70,
        identity="Balanced, adaptable, control through midfield",
    ),
    ClubProfile(
        club=_club("elderfield", "Elderfield United", "EFU", "#D35400", "#1C1C1C"),
        ground="Elder Lane",
        default_formation="3-5-2",
        style=_style(press=0.40, line=0.40, tempo=0.65, direct=0.70, width=0.45, risk=0.45),
        squad_quality=0.60,
        squad_stamina=0.75,
        identity="Counter-attacking, wing-backs, fast transitions",
    ),
    ClubProfile(
        club=_club("brightwater", "Brightwater Albion", "BWA", "#00A6D6", "#FFFFFF"),
        ground="Harbourside Stadium",
        default_formation="4-3-3",
        style=_style(press=0.30, line=0.60, tempo=0.40, direct=0.25, width=0.55, risk=0.35),
        squad_quality=0.70,
        squad_stamina=0.70,
        identity="Patient possession, low press, slow build-up",
    ),
    ClubProfile(
        club=_club("corran-valley", "Corran Valley", "CRV", "#2E4057", "#F25F5C"),
        ground="Valley Works",
        default_formation="4-1-4-1",
        style=_style(press=0.85, line=0.65, tempo=0.70, direct=0.55, width=0.50, risk=0.65),
        squad_quality=0.60,
        squad_stamina=0.45,
        identity="Relentless early press, thin squad, fades late",
    ),
    ClubProfile(
        club=_club("redmarsh", "Redmarsh Town", "RMT", "#B22222", "#F5F5F5"),
        ground="Marsh End",
        default_formation="5-3-2",
        style=_style(press=0.25, line=0.25, tempo=0.45, direct=0.65, width=0.40, risk=0.30),
        squad_quality=0.55,
        squad_stamina=0.70,
        identity="Deep block, set pieces, opportunistic counters",
    ),
)

_BY_ID = {profile.club.club_id: profile for profile in CLUBS}


def club_profile(club_id: str) -> ClubProfile:
    return _BY_ID[club_id]
