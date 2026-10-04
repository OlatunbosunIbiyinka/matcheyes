"""Persistent squads with hidden attributes, and the development / held-out seed split."""

import statistics

import pytest

from matcheyes.domain.entities import Position
from matcheyes_synth.league import CLUBS
from matcheyes_synth.scenarios import CATALOGUE
from matcheyes_synth.seeds import (
    HELD_OUT_SEED_BASE,
    development_seeds,
    held_out_seeds,
    is_held_out,
)
from matcheyes_synth.squads import ATTACKERS, DEFENDERS, FORMATION_POSITIONS, build_squad


@pytest.mark.parametrize("profile", CLUBS, ids=lambda p: p.club.club_id)
def test_squad_fits_the_club_formation_and_covers_every_player(profile: object) -> None:
    from matcheyes_synth.league import ClubProfile

    assert isinstance(profile, ClubProfile)
    squad = build_squad(profile, "0.1.0")
    positions = tuple(p.position for p in squad.sheet.starting_xi)
    assert positions == FORMATION_POSITIONS[profile.default_formation]
    assert squad.sheet.formation == profile.default_formation
    assert set(squad.attributes) == {p.player_id for p in squad.sheet.squad}
    assert any(p.position is Position.GK for p in squad.sheet.bench)
    assert all(p.player_id.startswith(profile.club.club_id) for p in squad.sheet.squad)


def test_squads_are_stable_for_a_version_and_change_with_it() -> None:
    profile = CLUBS[0]
    assert build_squad(profile, "0.1.0") == build_squad(profile, "0.1.0")
    assert build_squad(profile, "0.1.0").sheet != build_squad(profile, "9.9.9").sheet


def test_attributes_reflect_roles() -> None:
    finishing = {"att": [], "def": []}  # type: dict[str, list[float]]
    for profile in CLUBS:
        squad = build_squad(profile, "0.1.0")
        for player in squad.sheet.squad:
            if player.position in ATTACKERS:
                finishing["att"].append(squad.attributes[player.player_id].finishing)
            elif player.position in DEFENDERS:
                finishing["def"].append(squad.attributes[player.player_id].finishing)
    assert statistics.fmean(finishing["att"]) > statistics.fmean(finishing["def"]) + 0.2


def test_development_and_held_out_seeds_are_disjoint() -> None:
    dev, held = set(development_seeds(1000)), set(held_out_seeds(1000))
    assert not dev & held
    assert all(is_held_out(s) for s in held)
    assert not any(is_held_out(s) for s in dev)


def test_catalogue_default_seeds_are_development_seeds() -> None:
    assert not any(is_held_out(spec.default_seed) for spec in CATALOGUE)
    assert all(spec.default_seed < HELD_OUT_SEED_BASE for spec in CATALOGUE)


@pytest.mark.parametrize("count", [0, 1001])
def test_seed_counts_are_bounded(count: int) -> None:
    with pytest.raises(ValueError, match="count"):
        development_seeds(count)
