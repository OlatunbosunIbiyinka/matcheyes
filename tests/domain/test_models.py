import pytest
from pydantic import ValidationError

from matcheyes.domain.claims import ClaimStrength
from matcheyes.domain.entities import Player, Position, TeamSheet
from matcheyes.domain.pitch import Location
from matcheyes.domain.time import MatchInstant
from tests.support.builders import match_info


def test_location_is_bounded_to_the_pitch() -> None:
    with pytest.raises(ValidationError):
        Location(x=105.1, y=10)
    with pytest.raises(ValidationError):
        Location(x=10, y=-0.1)


def test_mirroring_switches_attacking_frame() -> None:
    assert Location(x=10, y=8).mirrored() == Location(x=95, y=60)


@pytest.mark.parametrize(
    ("period", "clock_ms", "expected"),
    [
        (1, 0, "1'"),
        (1, 59_999, "1'"),
        (1, 44 * 60_000, "45'"),
        (1, 45 * 60_000, "45+1'"),
        (1, 46 * 60_000 + 30_000, "45+2'"),
        (2, 0, "46'"),
        (2, 17 * 60_000, "63'"),
        (2, 45 * 60_000, "90+1'"),
    ],
)
def test_display_minute_follows_broadcast_convention(
    period: int, clock_ms: int, expected: str
) -> None:
    assert MatchInstant.model_validate({"period": period, "clock_ms": clock_ms}).display_minute == (
        expected
    )


def test_at_minute_maps_onto_periods() -> None:
    assert MatchInstant.at_minute(30) == MatchInstant(period=1, clock_ms=1_800_000)
    assert MatchInstant.at_minute(45) == MatchInstant(period=2, clock_ms=0)
    with pytest.raises(ValueError, match="0-90"):
        MatchInstant.at_minute(91)


def _sheet_with(**changes: object) -> dict[str, object]:
    return match_info().home.model_dump() | changes


def test_team_sheet_needs_exactly_one_goalkeeper() -> None:
    sheet = match_info().home
    outfield_gk = sheet.starting_xi[1].model_copy(update={"position": Position.GK})
    xi = (sheet.starting_xi[0], outfield_gk, *sheet.starting_xi[2:])
    with pytest.raises(ValidationError, match="exactly one GK"):
        TeamSheet.model_validate(_sheet_with(starting_xi=[p.model_dump() for p in xi]))


def test_formation_must_account_for_ten_outfield_players() -> None:
    with pytest.raises(ValidationError, match="must sum to 10"):
        TeamSheet.model_validate(_sheet_with(formation="4-4-3"))


def test_shirt_numbers_are_unique_within_a_squad() -> None:
    sheet = match_info().home
    clash = Player(player_id="extra", name="Extra", shirt_number=1, position=Position.ST)
    with pytest.raises(ValidationError, match="shirt number"):
        TeamSheet.model_validate(
            _sheet_with(bench=[p.model_dump() for p in (*sheet.bench[:-1], clash)])
        )


def test_home_and_away_must_differ() -> None:
    info = match_info()
    with pytest.raises(ValidationError, match="different clubs"):
        type(info).model_validate(info.model_dump() | {"away": info.home.model_dump()})


def test_models_are_immutable() -> None:
    with pytest.raises(ValidationError):
        Location(x=1, y=1).x = 2  # type: ignore[misc]


def test_claim_strength_ladder_is_ordered() -> None:
    ladder = list(ClaimStrength)
    assert [c.rank for c in ladder] == list(range(len(ladder)))
    assert ClaimStrength.SUPPORTED.at_most(ClaimStrength.ASSOCIATED) is ClaimStrength.ASSOCIATED
    assert ClaimStrength.OBSERVED.at_most(ClaimStrength.ASSOCIATED) is ClaimStrength.OBSERVED
