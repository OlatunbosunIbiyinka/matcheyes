"""The twelve dangerous cases (docs/personalization.md#truth-invariants).

A view that departs from the verified truth cannot be constructed. Each case is checked on the
smallest hand-built view, and on every rendered audience view of every verdict and integrity
state.
"""

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from matcheyes.agents.contracts import (
    HYPOTHESIS_DESCRIPTIONS,
    EvidenceIntegrity,
    FinalInsight,
    Verdict,
)
from matcheyes.agents.narrative import VERIFIED_ON_REMAINING
from matcheyes.domain.claims import ClaimStrength
from matcheyes.personalization.contracts import (
    INTEGRITY_TEXT,
    Audience,
    PersonalizationProfile,
    PersonalizedInsight,
    SectionKind,
    ViewSection,
    fingerprint,
    lead_claim,
    truth_violations,
)
from tests.personalization.support import (
    Sample,
    compromised,
    compromised_withheld,
    explained,
    hand_view,
    insufficient,
    tentative,
    unavailable_sample,
)

FAN = PersonalizationProfile(audience=Audience.FAN)


def _rebuild(view: PersonalizedInsight, sections: list[ViewSection]) -> None:
    """Re-validate the view with different sections; raises if the truth no longer holds."""
    PersonalizedInsight.model_validate({**view.model_dump(), "sections": sections})


def _replace(
    view: PersonalizedInsight, kind: SectionKind, edit: Callable[[ViewSection], ViewSection]
) -> list[ViewSection]:
    return [edit(s) if s.kind is kind else s for s in view.sections]


def _text(view: PersonalizedInsight, kind: SectionKind, new: str) -> list[ViewSection]:
    return _replace(view, kind, lambda s: s.model_copy(update={"text": new}))


# --- 1. strength cannot change ------------------------------------------------------------------


@pytest.mark.parametrize("make", [explained, tentative, compromised])
def test_1_a_view_cannot_show_another_strength(make: Callable[[], Sample]) -> None:
    view = hand_view(make().final, FAN)
    lead = lead_claim(view.source)
    assert lead is not None
    for strength in ClaimStrength:
        if strength is lead.strength:
            continue
        shown = next(s for s in view.sections if s.kind is SectionKind.INTERPRETATION).text
        raised = shown.replace(f", {lead.strength.value}]", f", {strength.value}]")
        with pytest.raises(ValidationError, match="verification label"):
            _rebuild(view, _text(view, SectionKind.INTERPRETATION, raised))


def test_1_a_view_cannot_carry_a_strengthened_source() -> None:
    final = compromised().final
    forged = final.model_copy(update={"strength": ClaimStrength.SUPPORTED})
    view = hand_view(final, FAN)
    with pytest.raises(ValidationError, match="fingerprint"):
        PersonalizedInsight.model_validate({**view.model_dump(), "source": forged.model_dump()})


# --- 2. association cannot become causation -----------------------------------------------------


@pytest.mark.parametrize("make", [tentative, compromised])
def test_2_a_hedged_claim_cannot_be_shown_with_causal_wording(make: Callable[[], Sample]) -> None:
    view = hand_view(make().final, FAN)
    lead = lead_claim(view.source)
    assert lead is not None and lead.hypothesis is not None
    causal = HYPOTHESIS_DESCRIPTIONS[lead.hypothesis]
    assert causal != lead.text
    shown = next(s for s in view.sections if s.kind is SectionKind.INTERPRETATION).text
    with pytest.raises(ValidationError, match="claim text"):
        _rebuild(view, _text(view, SectionKind.INTERPRETATION, shown.replace(lead.text, causal)))


# --- 3. the integrity warning cannot disappear --------------------------------------------------


@pytest.mark.parametrize("make", [compromised, compromised_withheld])
def test_3_a_compromised_view_cannot_drop_or_soften_its_warning(make: Callable[[], Sample]) -> None:
    view = hand_view(make().final, FAN)
    dropped = [s for s in view.sections if s.kind is not SectionKind.INTEGRITY]
    with pytest.raises(ValidationError, match="integrity disclosure"):
        _rebuild(view, dropped)
    with pytest.raises(ValidationError, match="integrity disclosure"):
        _rebuild(view, _text(view, SectionKind.INTEGRITY, "Some evidence was excluded."))
    optional = _replace(
        view, SectionKind.INTEGRITY, lambda s: s.model_copy(update={"mandatory": False})
    )
    with pytest.raises(ValidationError, match="integrity disclosure"):
        _rebuild(view, optional)


def test_3_an_intact_view_cannot_carry_a_false_warning() -> None:
    view = hand_view(tentative().final, FAN)
    warning = ViewSection(kind=SectionKind.INTEGRITY, text=INTEGRITY_TEXT, mandatory=True)
    with pytest.raises(ValidationError, match="integrity"):
        _rebuild(view, [*view.sections, warning])


# --- 4. evidence IDs cannot be invented, dropped or laundered -----------------------------------


def test_4_evidence_ids_cannot_be_invented_or_dropped() -> None:
    view = hand_view(explained().final, FAN)
    invented = _replace(
        view,
        SectionKind.INTERPRETATION,
        lambda s: s.model_copy(update={"evidence_ids": (*s.evidence_ids, "ev-77")}),
    )
    with pytest.raises(ValidationError, match="ev-77"):
        _rebuild(view, invented)
    dropped = _replace(
        view, SectionKind.INTERPRETATION, lambda s: s.model_copy(update={"evidence_ids": ()})
    )
    with pytest.raises(ValidationError, match="supporting evidence"):
        _rebuild(view, dropped)
    cited = ViewSection(kind=SectionKind.EVIDENCE, text="ev-77 shows a surge.")
    with pytest.raises(ValidationError, match="ev-77"):
        _rebuild(view, [*view.sections, cited])


def test_4_quarantined_evidence_cannot_be_shown_as_valid() -> None:
    view = hand_view(compromised().final, FAN)
    bad = view.source.quarantined[0]
    shown = ViewSection(kind=SectionKind.EVIDENCE, text=f"{bad} supports it.", evidence_ids=(bad,))
    with pytest.raises(ValidationError, match=bad):
        _rebuild(view, [*view.sections, shown])
    disclosed = ViewSection(
        kind=SectionKind.QUARANTINE, text=f"Excluded: {bad}.", evidence_ids=(bad,)
    )
    _rebuild(view, [*view.sections, disclosed])


# --- 7. the source insight is never changed -----------------------------------------------------


@pytest.mark.parametrize("make", [explained, compromised, insufficient, unavailable_sample])
def test_7_building_views_leaves_the_insight_unchanged(make: Callable[[], Sample]) -> None:
    final = make().final
    before = (final.model_dump_json(), fingerprint(final))
    for audience in Audience:
        view = hand_view(final, PersonalizationProfile(audience=audience))
        assert view.source is final or view.source == final
    assert (final.model_dump_json(), fingerprint(final)) == before
    with pytest.raises(ValidationError):
        final.strength = ClaimStrength.SUPPORTED  # type: ignore[misc]


# --- 9 / 10 / 11. language and audience cannot change truth -------------------------------------


def test_9_only_reviewed_languages_exist() -> None:
    with pytest.raises(ValidationError):
        PersonalizationProfile.model_validate({"audience": "fan", "language": "es"})


@pytest.mark.parametrize("make", [explained, compromised, compromised_withheld, insufficient])
def test_10_11_every_audience_holds_the_same_verdict_strength_and_integrity(
    make: Callable[[], Sample],
) -> None:
    final = make().final
    views = [hand_view(final, PersonalizationProfile(audience=a)) for a in Audience]
    assert {(v.source.verdict, v.source.strength, v.source.evidence_integrity) for v in views} == {
        (final.verdict, final.strength, final.evidence_integrity)
    }


def test_11_a_compromised_view_cannot_claim_full_verification() -> None:
    view = hand_view(compromised().final, FAN)
    shown = next(s for s in view.sections if s.kind is SectionKind.INTERPRETATION).text
    full = shown.replace(VERIFIED_ON_REMAINING, "verified")
    with pytest.raises(ValidationError, match="verification label"):
        _rebuild(view, _text(view, SectionKind.INTERPRETATION, full))
    assert view.source.evidence_integrity is EvidenceIntegrity.COMPROMISED


# --- 12. no verified explanation stays unexplained ----------------------------------------------


@pytest.mark.parametrize("make", [insufficient, unavailable_sample, compromised_withheld])
def test_12_an_unexplained_insight_cannot_gain_an_explanation(make: Callable[[], Sample]) -> None:
    final = make().final
    assert final.verdict in (Verdict.INSUFFICIENT_EVIDENCE, Verdict.UNAVAILABLE)
    view = hand_view(final, FAN)
    claim = ViewSection(
        kind=SectionKind.INTERPRETATION,
        text="[verified, hypothesised] The change followed a goal.",
        mandatory=True,
    )
    with pytest.raises(ValidationError, match="explanation shown"):
        _rebuild(view, [*view.sections, claim])
    labelled = _text(view, SectionKind.NO_INSIGHT, "[verified, supported] Explained.")
    with pytest.raises(ValidationError, match="label"):
        _rebuild(view, labelled)
    with pytest.raises(ValidationError, match="no-verified-explanation"):
        _rebuild(view, [s for s in view.sections if s.kind is not SectionKind.NO_INSIGHT])


def test_the_violation_list_is_empty_for_a_valid_view() -> None:
    for make in (explained, tentative, compromised, insufficient):
        final: FinalInsight = make().final
        assert truth_violations(hand_view(final, FAN)) == []
