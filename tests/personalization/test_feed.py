"""Feed filtering moves unexplained insights to a secondary list; it never hides integrity."""

import pytest

from matcheyes.agents.contracts import EvidenceIntegrity, FinalInsight, Verdict
from matcheyes.analytics.moments import Names
from matcheyes.orchestration.investigation import InvestigationRecord
from matcheyes.personalization import feed as feed_module
from matcheyes.personalization.contracts import (
    INTEGRITY_TEXT,
    LABELLED,
    NO_VERIFIED_INSIGHT,
    Audience,
    PersonalizationProfile,
    PersonalizedInsight,
    SectionKind,
)
from matcheyes.personalization.feed import build_feed, format_feed
from matcheyes.personalization.policy import PREFERENCE_WEIGHTS
from matcheyes.personalization.render import personalize
from tests.personalization.support import (
    Sample,
    compromised,
    compromised_withheld,
    control_records,
    explained,
)


def _profile(audience: Audience) -> PersonalizationProfile:
    return PersonalizationProfile(audience=audience)


def _renamed(sample: Sample, suffix: str) -> InvestigationRecord:
    final = sample.final.model_copy(
        update={"investigation_id": f"{sample.final.investigation_id}-{suffix}"}
    )
    return InvestigationRecord(final=final, verification=sample.verification, trace=())


def _ids(views: tuple[PersonalizedInsight, ...]) -> list[str]:
    return [v.source.investigation_id for v in views]


@pytest.mark.parametrize("audience", [Audience.FAN, Audience.BROADCASTER])
def test_unexplained_insights_move_to_the_secondary_list(audience: Audience) -> None:
    samples = control_records()
    feed = build_feed([s.record for s in samples], _profile(audience), samples[0].ws)
    assert not feed.withheld
    assert all(v.source.verdict in LABELLED for v in feed.primary)
    assert all(v.source.verdict not in LABELLED for v in feed.secondary)
    assert len(feed.primary) + len(feed.secondary) == len(samples)
    assert any(v.source.verdict is Verdict.INSUFFICIENT_EVIDENCE for v in feed.secondary)


def test_the_analyst_sees_every_insight_on_one_timeline() -> None:
    samples = control_records()
    feed = build_feed([s.record for s in samples], _profile(Audience.ANALYST), samples[0].ws)
    assert feed.secondary == () and feed.withheld == ()
    assert len(feed.primary) == len(samples)
    keys = [(v.source.at.sort_key, v.source.candidate_id) for v in feed.primary]
    assert keys == sorted(keys)


def test_an_empty_primary_feed_says_no_verified_insight_is_available() -> None:
    samples = [s for s in control_records() if s.final.verdict not in LABELLED]
    feed = build_feed([s.record for s in samples], _profile(Audience.FAN), samples[0].ws)
    assert feed.primary == ()
    assert feed.notice == NO_VERIFIED_INSIGHT
    assert len(feed.secondary) == len(samples)
    assert NO_VERIFIED_INSIGHT in format_feed(feed, Names(samples[0].ws.info))


@pytest.mark.parametrize("audience", list(Audience))
def test_a_compromised_insight_stays_primary_with_its_warning(audience: Audience) -> None:
    for sample in (compromised(), compromised_withheld()):
        feed = build_feed([sample.record], _profile(audience), sample.ws)
        assert feed.withheld == () and feed.secondary == ()
        (view,) = feed.primary
        assert view.source.evidence_integrity is EvidenceIntegrity.COMPROMISED
        warnings = [s for s in view.sections if s.kind is SectionKind.INTEGRITY]
        assert [(w.text, w.mandatory) for w in warnings] == [(INTEGRITY_TEXT, True)]


@pytest.mark.parametrize("audience", [Audience.FAN, Audience.BROADCASTER])
def test_a_compromised_insight_ranks_below_an_intact_equivalent(audience: Audience) -> None:
    intact, flagged = explained(), compromised()
    assert intact.final.candidate_id == flagged.final.candidate_id
    records = [_renamed(flagged, "flagged"), _renamed(intact, "intact")]
    feed = build_feed(records, _profile(audience), intact.ws)
    assert _ids(feed.primary) == [r.final.investigation_id for r in reversed(records)]


def test_an_insight_failing_the_claim_audit_is_withheld_not_dropped() -> None:
    sample = explained()
    forged = sample.final.model_copy(update={"narrative": sample.final.narrative + "\nFACT: x"})
    record = InvestigationRecord(final=forged, verification=sample.verification, trace=())
    feed = build_feed([record], _profile(Audience.FAN), sample.ws)
    assert feed.withheld == (forged.investigation_id,)
    assert feed.primary == () and feed.notice == NO_VERIFIED_INSIGHT
    assert "Withheld after failing audit" in format_feed(feed, Names(sample.ws.info))


def test_a_view_failing_the_view_audit_is_withheld(monkeypatch: pytest.MonkeyPatch) -> None:
    sample = compromised()

    def dropped_warning(
        final: FinalInsight, profile: PersonalizationProfile, ws: object
    ) -> PersonalizedInsight:
        view = personalize(final, profile, sample.ws)
        kept = tuple(s for s in view.sections if s.kind is not SectionKind.INTEGRITY)
        return view.model_copy(update={"sections": kept})

    monkeypatch.setattr(feed_module, "personalize", dropped_warning)
    feed = build_feed([sample.record], _profile(Audience.FAN), sample.ws)
    assert feed.withheld == (sample.final.investigation_id,)


def test_a_view_that_cannot_be_constructed_is_withheld(monkeypatch: pytest.MonkeyPatch) -> None:
    sample = explained()

    def broken(final: FinalInsight, profile: PersonalizationProfile, ws: object) -> None:
        raise ValueError("invariant violated")

    monkeypatch.setattr(feed_module, "personalize", broken)
    feed = build_feed([sample.record], _profile(Audience.BROADCASTER), sample.ws)
    assert feed.withheld == (sample.final.investigation_id,)


def test_preferences_reorder_the_feed_but_never_move_insights_between_lists() -> None:
    samples = control_records()
    ws = samples[0].ws
    records = [s.record for s in samples]
    plain = build_feed(records, _profile(Audience.FAN), ws)
    for club in ws.info.team_ids:
        preferred = build_feed(
            records, PersonalizationProfile(audience=Audience.FAN, favourite_club_id=club), ws
        )
        assert sorted(_ids(preferred.primary)) == sorted(_ids(plain.primary))
        assert sorted(_ids(preferred.secondary)) == sorted(_ids(plain.secondary))
        before = {v.source.investigation_id: v.relevance for v in plain.primary + plain.secondary}
        weights = PREFERENCE_WEIGHTS[Audience.FAN]
        for view in preferred.primary + preferred.secondary:
            gain = view.relevance - before[view.source.investigation_id]
            expected = weights.club_team if view.source.team_id == club else weights.club_opponent
            assert gain == expected


def test_feeds_are_deterministic() -> None:
    samples = control_records()
    records = [s.record for s in samples]
    for audience in Audience:
        assert build_feed(records, _profile(audience), samples[0].ws) == build_feed(
            records, _profile(audience), samples[0].ws
        )
