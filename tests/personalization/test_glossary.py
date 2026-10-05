"""Glossary entries define metrics; they add no causal, certainty or intent language."""

import pytest

from matcheyes.analytics.metrics import METRIC_BY_NAME
from matcheyes.domain.claims import ClaimStrength
from matcheyes.orchestration.audit import CAUSAL_TERMS, calibration_findings
from matcheyes.personalization.glossary import GLOSSARY


def test_every_metric_has_exactly_one_definition() -> None:
    assert set(GLOSSARY) == set(METRIC_BY_NAME)


@pytest.mark.parametrize("metric", sorted(GLOSSARY))
def test_definitions_pass_the_calibration_lexicon_at_the_weakest_strength(metric: str) -> None:
    assert calibration_findings(GLOSSARY[metric], ClaimStrength.OBSERVED) == []


@pytest.mark.parametrize("metric", sorted(GLOSSARY))
def test_definitions_say_what_is_counted_not_why_it_matters(metric: str) -> None:
    text = GLOSSARY[metric].lower()
    for phrase in ("matters", "important", "because", "so that", "which means", "led", "result"):
        assert phrase not in text, phrase
    assert not [t for t in CAUSAL_TERMS if t in text]
    assert text.endswith(".")


def test_the_calibration_check_would_catch_a_causal_definition() -> None:
    forged = GLOSSARY["field_tilt"] + " A rise caused the goal."
    assert calibration_findings(forged, ClaimStrength.OBSERVED)
