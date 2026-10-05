"""Club, player and metric involvement come from the match and the insight's cited events only."""

from matcheyes.personalization.contracts import Audience, PersonalizationProfile
from matcheyes.personalization.involvement import actors, involvement
from matcheyes_eval.stage6 import cited_player, uncited_player
from tests.personalization.support import explained


def _profile(**preferences: str) -> PersonalizationProfile:
    return PersonalizationProfile.model_validate({"audience": Audience.FAN, **preferences})


def test_no_preferences_means_no_involvement() -> None:
    s = explained()
    inv = involvement(s.final, _profile(), s.ws)
    assert (inv.club_role, inv.player_name, inv.player_events, inv.metric) == (
        None,
        None,
        (),
        False,
    )


def test_a_club_in_the_match_is_the_team_or_the_opponent() -> None:
    s = explained()
    opponent = s.ws.info.opponent_of(s.final.team_id)
    assert (
        involvement(s.final, _profile(favourite_club_id=s.final.team_id), s.ws).club_role == "team"
    )
    assert involvement(s.final, _profile(favourite_club_id=opponent), s.ws).club_role == "opponent"


def test_a_club_not_in_the_match_is_neutral() -> None:
    s = explained()
    assert "nowhere-town" not in s.ws.info.team_ids
    assert involvement(s.final, _profile(favourite_club_id="nowhere-town"), s.ws).club_role is None


def test_a_player_not_in_the_match_is_neutral() -> None:
    s = explained()
    inv = involvement(s.final, _profile(favourite_player_id="nowhere-town-09"), s.ws)
    assert (inv.player_name, inv.player_events) == (None, ())


def test_a_squad_player_absent_from_the_cited_events_has_no_involvement() -> None:
    s = explained()
    player = uncited_player(s.ws, s.final)
    inv = involvement(s.final, _profile(favourite_player_id=player), s.ws)
    assert inv.player_name is not None
    assert inv.player_events == ()


def test_a_player_in_the_cited_events_is_involved_exactly_in_those_events() -> None:
    s = explained()
    player = cited_player(s.ws, s.final)
    assert player is not None
    inv = involvement(s.final, _profile(favourite_player_id=player), s.ws)
    expected = tuple(e for e in s.final.event_ids if player in actors(s.ws.events[e]))
    assert inv.player_events == expected and expected
    assert set(inv.player_events) <= set(s.final.event_ids)


def test_the_favourite_metric_matches_only_the_candidates_metric() -> None:
    s = explained()
    metric = s.ws.candidates[s.final.candidate_id].metric
    other = next(m for m in ("shots", "turnovers") if m != metric)
    assert involvement(s.final, _profile(favourite_metric=metric), s.ws).metric
    assert not involvement(s.final, _profile(favourite_metric=other), s.ws).metric
