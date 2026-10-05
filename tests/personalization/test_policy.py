"""Relevance, placement and order: preferences move views, never truth."""

from itertools import pairwise

import pytest

from matcheyes.agents.contracts import EvidenceIntegrity, Verdict
from matcheyes.analytics.strength import EvidenceLevel
from matcheyes.personalization.contracts import (
    LABELLED,
    Audience,
    PersonalizationProfile,
    SectionKind,
)
from matcheyes.personalization.involvement import Involvement, involvement
from matcheyes.personalization.policy import (
    ALL_OPTIONAL,
    COMPROMISED_PENALTY,
    MANDATORY_SECTIONS,
    OPTIONAL_SECTIONS,
    PREFERENCE_WEIGHTS,
    VERDICT_WEIGHT,
    Placement,
    omitted,
    order_key,
    placement,
    relevance,
)
from tests.personalization.support import (
    all_samples,
    compromised,
    compromised_withheld,
    explained,
    insufficient,
    tentative,
    unavailable_sample,
)

NONE = Involvement(club_role=None, player_name=None, player_events=(), metric=False)


@pytest.mark.parametrize("audience", list(Audience))
def test_each_preference_adds_its_documented_weight(audience: Audience) -> None:
    final = tentative().final
    base, _ = relevance(final, NONE, audience)
    w = PREFERENCE_WEIGHTS[audience]
    cases = [
        (NONE.model_copy(update={"club_role": "team"}), w.club_team),
        (NONE.model_copy(update={"club_role": "opponent"}), w.club_opponent),
        (NONE.model_copy(update={"player_name": "X", "player_events": ("e1",)}), w.player),
        (NONE.model_copy(update={"metric": True}), w.metric),
    ]
    for inv, weight in cases:
        score, basis = relevance(final, inv, audience)
        assert score == base + weight
        assert len(basis) == len(relevance(final, NONE, audience)[1]) + 1


def test_a_named_player_with_no_cited_events_adds_nothing() -> None:
    final = tentative().final
    named = NONE.model_copy(update={"player_name": "Somebody"})
    assert relevance(final, named, Audience.FAN) == relevance(final, NONE, Audience.FAN)


def test_the_favourite_metric_boosts_only_when_it_matches() -> None:
    s = explained()
    metric = s.ws.candidates[s.final.candidate_id].metric
    other = next(m for m in ("shots", "turnovers") if m != metric)

    def score(m: str | None) -> int:
        profile = PersonalizationProfile(audience=Audience.FAN, favourite_metric=m)
        return relevance(s.final, involvement(s.final, profile, s.ws), Audience.FAN)[0]

    assert score(metric) == score(None) + PREFERENCE_WEIGHTS[Audience.FAN].metric
    assert score(other) == score(None)


def test_relevance_is_ordered_by_verdict_before_broadcaster_preferences() -> None:
    w = PREFERENCE_WEIGHTS[Audience.BROADCASTER]
    labelled = sorted(VERDICT_WEIGHT[v] for v in LABELLED)
    smallest_gap = min(b - a for a, b in pairwise(labelled))
    top_level = max(level.rank for level in EvidenceLevel)
    assert w.club_team + w.player + w.metric + top_level < smallest_gap


def test_a_compromised_insight_ranks_below_its_intact_equivalent() -> None:
    final = compromised().final
    intact = final.model_copy(update={"evidence_integrity": EvidenceIntegrity.INTACT})
    for audience in Audience:
        low, basis = relevance(final, NONE, audience)
        high, _ = relevance(intact, NONE, audience)
        assert low == high - COMPROMISED_PENALTY
        assert any("compromised" in b for b in basis)


def test_relevance_never_changes_the_insight() -> None:
    final = explained().final
    before = final.model_dump_json()
    rich = Involvement(club_role="team", player_name="X", player_events=("e1",), metric=True)
    for audience in Audience:
        relevance(final, rich, audience)
    assert final.model_dump_json() == before


@pytest.mark.parametrize("audience", [Audience.FAN, Audience.BROADCASTER])
def test_casual_feeds_move_only_unexplained_intact_insights_to_secondary(
    audience: Audience,
) -> None:
    assert placement(explained().final, audience) is Placement.PRIMARY
    assert placement(tentative().final, audience) is Placement.PRIMARY
    assert placement(compromised().final, audience) is Placement.PRIMARY
    withheld = compromised_withheld().final
    assert withheld.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert placement(withheld, audience) is Placement.PRIMARY
    assert placement(insufficient().final, audience) is Placement.SECONDARY
    assert placement(unavailable_sample().final, audience) is Placement.SECONDARY


def test_the_analyst_sees_everything_in_the_primary_feed() -> None:
    for s in all_samples():
        assert placement(s.final, Audience.ANALYST) is Placement.PRIMARY


def test_order_is_deterministic_and_ties_break_on_candidate_id() -> None:
    a = tentative().final
    b = a.model_copy(update={"candidate_id": "zz-" + a.candidate_id})
    assert order_key(a, 30, Audience.FAN) < order_key(b, 30, Audience.FAN)
    assert order_key(b, 31, Audience.FAN) < order_key(a, 30, Audience.FAN)
    assert order_key(b, 99, Audience.ANALYST) > order_key(a, 0, Audience.ANALYST)


def test_mandatory_content_is_never_listed_as_omitted() -> None:
    for audience in Audience:
        listed = {SectionKind(k) for k in omitted(audience)}
        assert not listed & MANDATORY_SECTIONS
        assert listed == ALL_OPTIONAL - OPTIONAL_SECTIONS[audience]
    assert omitted(Audience.ANALYST) == ("context", "trigger", "glossary")
