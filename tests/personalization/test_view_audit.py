"""The independent view audit: clean on genuine views, and it catches every presentation fault."""

from collections import Counter

import pytest

from matcheyes.personalization import audit as audit_module
from matcheyes.personalization.audit import VIEW_CHECKS, audit_view
from matcheyes.personalization.contracts import Audience, PersonalizationProfile, SectionKind
from matcheyes.personalization.render import personalize
from matcheyes_eval.stage6 import PRESENTATION_FAULTS, caught
from tests.personalization.support import all_samples, explained, profiles, tentative

SAMPLES = {s.name: s for s in all_samples()}


@pytest.mark.parametrize("name", sorted(SAMPLES))
@pytest.mark.parametrize("audience", list(Audience))
def test_genuine_views_audit_clean_under_every_preference(name: str, audience: Audience) -> None:
    sample = SAMPLES[name]
    for profile in profiles(sample, audience).values():
        view = personalize(sample.final, profile, sample.ws)
        assert audit_view(view, sample.ws, sample.final) == []


@pytest.mark.parametrize("fault", sorted(PRESENTATION_FAULTS))
def test_every_presentation_fault_is_caught_by_its_check(fault: str) -> None:
    """Injected into every genuine view it applies to (at least one); every injection caught."""
    expected, inject = PRESENTATION_FAULTS[fault]
    outcomes: Counter[bool] = Counter()
    for sample in SAMPLES.values():
        for audience in Audience:
            view = personalize(sample.final, PersonalizationProfile(audience=audience), sample.ws)
            forged = inject(view, sample.ws)
            if forged is None or forged == view:
                continue
            findings = audit_view(forged, sample.ws, sample.final)
            assert caught(findings, expected), (sample.name, audience, findings)
            outcomes[True] += 1
    assert outcomes[True] > 0


def test_every_expected_check_is_a_real_check() -> None:
    for expected, _ in PRESENTATION_FAULTS.values():
        assert expected <= set(VIEW_CHECKS)


def test_digits_in_evidence_fact_names_are_not_invented_numbers() -> None:
    """The analyst view prints facts as name=value; a digit in a fact's name is upstream text."""
    sample = explained()
    first = sample.final.evidence[0]
    item = first.model_copy(update={"facts": {**first.facts, "window7319_count": "n/a"}})
    final = sample.final.model_copy(update={"evidence": (item, *sample.final.evidence[1:])})
    view = personalize(final, PersonalizationProfile(audience=Audience.ANALYST), sample.ws)
    assert "window7319_count=n/a" in " ".join(s.text for s in view.sections)
    assert audit_view(view, sample.ws, final) == []
    fan = personalize(final, PersonalizationProfile(audience=Audience.FAN), sample.ws)
    forged = fan.model_copy(
        update={
            "sections": tuple(
                s.model_copy(update={"text": f"{s.text} 8641 times."})
                if s.kind is SectionKind.CONTEXT
                else s
                for s in fan.sections
            )
        }
    )
    assert any(f.startswith("numbers:") for f in audit_view(forged, sample.ws, final))


def test_a_view_of_a_superseded_insight_is_stale() -> None:
    """Real time: when the upstream insight changes, an old view no longer passes."""
    old, new = tentative(), explained()
    view = personalize(old.final, PersonalizationProfile(audience=Audience.FAN), old.ws)
    assert audit_view(view, old.ws, old.final) == []
    findings = audit_view(view, new.ws, new.final)
    assert any(f.startswith("source:") for f in findings)


def test_the_audit_does_not_use_the_renderer() -> None:
    source = audit_module.__file__
    assert source is not None
    with open(source, encoding="utf-8") as f:
        text = f.read()
    assert "render" not in {
        line.split()[1].rsplit(".", 1)[-1]
        for line in text.splitlines()
        if line.startswith(("from ", "import "))
    }
    assert "personalize" not in text and "BUILDERS" not in text
