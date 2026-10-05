"""Audience views carry the verified truth unchanged; audiences differ only in framing."""

import pytest

from matcheyes.agents.contracts import EvidenceIntegrity, Verdict
from matcheyes.analytics.moments import Names
from matcheyes.personalization.contracts import (
    Audience,
    PersonalizationProfile,
    SectionKind,
    ViewSection,
    fingerprint,
    lead_claim,
    truth_violations,
    verification_label,
)
from matcheyes.personalization.glossary import GLOSSARY
from matcheyes.personalization.render import FAN_VERDICT, format_view, personalize, trigger
from tests.personalization.support import (
    Sample,
    all_samples,
    explained,
    insufficient,
    natural_variation,
    profiles,
)

K = SectionKind
SAMPLES = {s.name: s for s in all_samples()}
EFFECT_WORDS = ("mattered", "matters", "impact", "decisive", "turning point", "won the", "cost")


def _texts(sections: tuple[ViewSection, ...], kind: SectionKind) -> list[str]:
    return [s.text for s in sections if s.kind is kind]


def _plain(audience: Audience) -> PersonalizationProfile:
    return PersonalizationProfile(audience=audience)


@pytest.mark.parametrize("name", sorted(SAMPLES))
@pytest.mark.parametrize("audience", list(Audience))
def test_every_preference_shape_renders_a_valid_view(name: str, audience: Audience) -> None:
    sample = SAMPLES[name]
    before = fingerprint(sample.final)
    for profile in profiles(sample, audience).values():
        view = personalize(sample.final, profile, sample.ws)
        assert truth_violations(view) == []
        assert view.source is sample.final
    assert fingerprint(sample.final) == before


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_the_mandatory_core_carries_the_same_truth_for_every_audience(name: str) -> None:
    sample = SAMPLES[name]
    label, lead = verification_label(sample.final), lead_claim(sample.final)
    cores = []
    for audience in Audience:
        view = personalize(sample.final, _plain(audience), sample.ws)
        cores.append(
            [
                (s.kind, s.text if s.kind is not K.INTERPRETATION else None)
                for s in view.sections
                if s.mandatory
            ]
        )
        if label is not None and lead is not None:
            (interpretation,) = _texts(view.sections, K.INTERPRETATION)
            assert label in interpretation and lead.text in interpretation
    assert cores[0] == cores[1] == cores[2]


@pytest.mark.parametrize("name", sorted(SAMPLES))
@pytest.mark.parametrize("audience", list(Audience))
def test_preferences_change_only_relevance_and_the_involvement_line(
    name: str, audience: Audience
) -> None:
    sample = SAMPLES[name]
    shapes = profiles(sample, audience)
    baseline = personalize(sample.final, shapes["none"], sample.ws)
    for profile in shapes.values():
        view = personalize(sample.final, profile, sample.ws)
        without = tuple(s for s in view.sections if s.kind is not K.INVOLVEMENT)
        assert without == baseline.sections
        assert view.omitted == baseline.omitted


def test_only_a_player_in_the_cited_events_gets_an_involvement_line() -> None:
    sample = explained()
    shapes = profiles(sample, Audience.FAN)
    for name, profile in shapes.items():
        view = personalize(sample.final, profile, sample.ws)
        lines = _texts(view.sections, K.INVOLVEMENT)
        assert (len(lines) == 1) == (name == "player_cited")
    (line,) = _texts(
        personalize(sample.final, shapes["player_cited"], sample.ws).sections, K.INVOLVEMENT
    )
    assert line.endswith("of the events behind this insight.")


@pytest.mark.parametrize("name", sorted(SAMPLES))
@pytest.mark.parametrize("audience", list(Audience))
def test_no_player_is_named_outside_the_factual_involvement_line(
    name: str, audience: Audience
) -> None:
    sample = SAMPLES[name]
    players = [p.name for side in (sample.ws.info.home, sample.ws.info.away) for p in side.squad]
    for profile in profiles(sample, audience).values():
        view = personalize(sample.final, profile, sample.ws)
        for section in view.sections:
            if section.kind is K.INVOLVEMENT:
                continue
            assert not [p for p in players if p in section.text], section


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_the_analyst_view_is_complete(name: str) -> None:
    final = SAMPLES[name].final
    view = personalize(final, _plain(Audience.ANALYST), SAMPLES[name].ws)
    assert view.sections[0].kind is K.NARRATIVE
    assert view.sections[0].text == final.narrative
    evidence = [s.evidence_ids for s in view.sections if s.kind is K.EVIDENCE]
    assert evidence == [(e.evidence_id,) for e in final.evidence]
    assert bool(_texts(view.sections, K.QUARANTINE)) == bool(final.quarantined)
    assert bool(_texts(view.sections, K.ALTERNATIVES)) == bool(final.alternatives)
    details = " ".join(_texts(view.sections, K.DETAIL))
    assert f"Evidence integrity: {final.evidence_integrity.value}." in details
    assert f"Stage 3 level: {final.stage3_level}" in details
    claims = [s for s in view.sections if s.kind is K.DETAIL and "claim:" in s.text]
    assert len(claims) == len(final.claims)
    for downgrade in final.downgrades:
        assert downgrade in details
    assert not {K.NARRATIVE, K.EVIDENCE, K.DETAIL} & set(view.omitted)


def test_the_broadcaster_trigger_appears_only_when_the_verified_explanation_cites_one() -> None:
    sample = explained()
    found = trigger(sample.final, sample.ws)
    assert found is not None
    assert found.text.startswith("Preceded by a goal at ")
    lead = lead_claim(sample.final)
    assert lead is not None and set(found.evidence_ids) <= set(lead.supporting_evidence_ids)
    view = personalize(sample.final, _plain(Audience.BROADCASTER), sample.ws)
    assert _texts(view.sections, K.TRIGGER) == [found.text]
    for other in (natural_variation(), insufficient(), SAMPLES["compromised_withheld"]):
        assert trigger(other.final, other.ws) is None
        view = personalize(other.final, _plain(Audience.BROADCASTER), other.ws)
        assert _texts(view.sections, K.TRIGGER) == []


def test_the_broadcaster_leads_with_minute_and_team() -> None:
    sample = explained()
    view = personalize(sample.final, _plain(Audience.BROADCASTER), sample.ws)
    team = Names(sample.ws.info).team(sample.final.team_id)
    assert view.sections[0].text == f"{sample.final.at.display_minute} | {team}"


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_the_fan_view_defines_the_metric_and_states_the_game_state(name: str) -> None:
    sample = SAMPLES[name]
    view = personalize(sample.final, _plain(Audience.FAN), sample.ws)
    candidate = sample.ws.candidates[sample.final.candidate_id]
    assert _texts(view.sections, K.GLOSSARY) == [GLOSSARY[candidate.metric]]
    (context,) = _texts(view.sections, K.CONTEXT)
    assert context.startswith(f"At {sample.final.at.display_minute}, ")
    assert context.endswith((" leading.", " level.", " trailing."))
    assert not _texts(view.sections, K.EVIDENCE) and not _texts(view.sections, K.NARRATIVE)
    if sample.final.verdict in FAN_VERDICT:
        (interpretation,) = _texts(view.sections, K.INTERPRETATION)
        assert interpretation.startswith(FAN_VERDICT[sample.final.verdict])


@pytest.mark.parametrize("name", sorted(SAMPLES))
@pytest.mark.parametrize("audience", [Audience.FAN, Audience.BROADCASTER])
def test_no_view_invents_an_effect_on_the_match(name: str, audience: Audience) -> None:
    sample = SAMPLES[name]
    view = personalize(sample.final, _plain(audience), sample.ws)
    text = " ".join(s.text for s in view.sections).lower()
    assert not [w for w in EFFECT_WORDS if w in text]


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_integrity_problems_are_disclosed_in_every_audience(name: str) -> None:
    final = SAMPLES[name].final
    for audience in Audience:
        view = personalize(final, _plain(audience), SAMPLES[name].ws)
        integrity = [s for s in view.sections if s.kind is K.INTEGRITY]
        flagged = final.evidence_integrity is EvidenceIntegrity.COMPROMISED or final.quarantined
        assert bool(integrity) == bool(flagged)
        assert all(s.mandatory for s in integrity)


def test_unexplained_insights_say_so_plainly() -> None:
    for sample in (insufficient(), SAMPLES["unavailable"], SAMPLES["compromised_withheld"]):
        for audience in Audience:
            view = personalize(sample.final, _plain(audience), sample.ws)
            assert not _texts(view.sections, K.INTERPRETATION)
            (text,) = _texts(view.sections, K.NO_INSIGHT)
            assert text.startswith("No verified explanation")
    assert insufficient().final.verdict is Verdict.INSUFFICIENT_EVIDENCE


def _render_all(sample: Sample) -> list[str]:
    names = Names(sample.ws.info)
    return [
        format_view(personalize(sample.final, profile, sample.ws), names)
        for audience in Audience
        for profile in profiles(sample, audience).values()
    ]


def test_a_view_depends_only_on_its_insight_profile_and_match() -> None:
    """Real time: no state carries between calls, so rendering order cannot change a view."""
    samples = list(SAMPLES.values())
    profile = _plain(Audience.FAN)
    forward = {s.name: personalize(s.final, profile, s.ws) for s in samples}
    backward = {s.name: personalize(s.final, profile, s.ws) for s in reversed(samples)}
    assert forward == backward


def test_rendering_is_deterministic() -> None:
    for sample in (explained(), SAMPLES["compromised"]):
        assert _render_all(sample) == _render_all(sample)


def test_format_view_labels_every_section() -> None:
    sample = SAMPLES["compromised"]
    text = format_view(
        personalize(sample.final, _plain(Audience.ANALYST), sample.ws), Names(sample.ws.info)
    )
    for label in ("CANONICAL NARRATIVE:", "FACT:", "AI INTERPRETATION:", "EVIDENCE:", "ANALYSIS:"):
        assert label in text
