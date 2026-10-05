"""Evidence levels, claim strength and ranking, on constructed assessments."""

import pytest

from matcheyes.analytics.baselines import BaselineAssessment, BaselineKind
from matcheyes.analytics.patterns import PatternAssessment, Signal, SignalReading
from matcheyes.analytics.persistence import Persistence, PersistenceAssessment
from matcheyes.analytics.strength import EvidenceLevel, claim_strength, grade, rank_key
from matcheyes.domain.claims import ClaimStrength
from tests.support.builders import HOME


def _baseline(
    kind: BaselineKind = BaselineKind.UNCHANGED, aligned: str | None = None
) -> BaselineAssessment:
    return BaselineAssessment(
        kind=kind,
        before=(0, 30),
        after=(30, 45),
        statistic=3.0,
        transition_ids=(),
        aligned_with=aligned,
    )


def _reading(family: str, status: str = "support", independent: bool = True) -> SignalReading:
    return SignalReading.model_validate(
        {
            "signal": Signal(side="team", metric="shots", direction="up"),
            "team_id": HOME,
            "family": family,
            "statistic": 2.0 if status == "support" else -2.0,
            "status": status,
            "independent": independent,
        }
    )


def _pattern(*readings: SignalReading) -> PatternAssessment:
    families = sorted({r.family for r in readings if r.status == "support" and r.independent})
    return PatternAssessment(
        pattern="p",
        concept="c",
        subject_team_id=HOME,
        readings=readings,
        independent_families=tuple(families),
    )


def _persistence(p: Persistence = Persistence.SUSTAINED) -> PersistenceAssessment:
    return PersistenceAssessment(persistence=p, sub_windows=(), continues=None)


TWO = _pattern(_reading("passing"), _reading("territory"))
ONE = _pattern(_reading("passing"))


@pytest.mark.parametrize(
    ("baseline", "pattern", "persistence", "level"),
    [
        (_baseline(BaselineKind.REMOVED), TWO, _persistence(), EvidenceLevel.INSUFFICIENT),
        (_baseline(), None, _persistence(), EvidenceLevel.WEAK),
        (_baseline(), _pattern(), _persistence(), EvidenceLevel.WEAK),
        (_baseline(), ONE, _persistence(), EvidenceLevel.MODERATE),
        (_baseline(), ONE, _persistence(Persistence.INDETERMINATE), EvidenceLevel.MODERATE),
        (_baseline(), ONE, _persistence(Persistence.TRANSIENT), EvidenceLevel.WEAK),
        (_baseline(), ONE, _persistence(Persistence.REVERSED), EvidenceLevel.WEAK),
        (_baseline(), TWO, _persistence(), EvidenceLevel.STRONG),
        (_baseline(), TWO, _persistence(Persistence.INDETERMINATE), EvidenceLevel.MODERATE),
        (_baseline(BaselineKind.COINCIDENT, aligned="e1"), TWO, _persistence(), EvidenceLevel.WEAK),
        (_baseline(BaselineKind.SAME_REGIME), TWO, _persistence(), EvidenceLevel.STRONG),
    ],
)
def test_levels(
    baseline: BaselineAssessment,
    pattern: PatternAssessment | None,
    persistence: PersistenceAssessment,
    level: EvidenceLevel,
) -> None:
    assert grade(baseline, pattern, persistence)[0] is level


def test_corroborating_same_family_support_does_not_raise_the_level() -> None:
    pattern = _pattern(
        _reading("pressing", independent=False), _reading("defending", "support", False)
    )
    assert grade(_baseline(), pattern, _persistence())[0] is EvidenceLevel.WEAK


def test_contradictions() -> None:
    one_against_two = _pattern(
        _reading("passing"), _reading("territory"), _reading("x", "contradiction")
    )
    level, basis = grade(_baseline(), one_against_two, _persistence())
    assert level is EvidenceLevel.MODERATE  # not contradicted overall, but no longer STRONG
    contradicted = _pattern(_reading("passing"), _reading("x", "contradiction"))
    level, basis = grade(_baseline(), contradicted, _persistence())
    assert level is EvidenceLevel.WEAK
    assert "capped: contradicted by related signals" in basis


def test_basis_explains_the_level() -> None:
    _, basis = grade(_baseline(), TWO, _persistence())
    assert basis == (
        "unusual against its baseline",
        "independent support from: passing, territory",
        "sustained across its window",
    )
    _, removed = grade(_baseline(BaselineKind.REMOVED), TWO, _persistence())
    assert removed == ("not unusual when compared within the same game state",)


def test_claim_strength_never_exceeds_associated() -> None:
    assert claim_strength(EvidenceLevel.STRONG, TWO) is ClaimStrength.ASSOCIATED
    assert claim_strength(EvidenceLevel.WEAK, None) is ClaimStrength.OBSERVED
    assert claim_strength(EvidenceLevel.INSUFFICIENT, TWO) is ClaimStrength.OBSERVED


def test_ranking_prefers_level_over_statistic_and_caps_family_count() -> None:
    weak_but_huge = rank_key(EvidenceLevel.WEAK, None, _persistence(), 9.0)
    moderate = rank_key(EvidenceLevel.MODERATE, ONE, _persistence(), 2.1)
    assert moderate < weak_but_huge
    three = _pattern(_reading("passing"), _reading("territory"), _reading("attacking"))
    two_high_z = rank_key(EvidenceLevel.STRONG, TWO, _persistence(), 4.0)
    three_low_z = rank_key(EvidenceLevel.STRONG, three, _persistence(), 3.0)
    assert two_high_z < three_low_z  # a third family is not a third witness
    sustained = rank_key(EvidenceLevel.MODERATE, ONE, _persistence(), 2.0)
    indeterminate = rank_key(
        EvidenceLevel.MODERATE, ONE, _persistence(Persistence.INDETERMINATE), 5.0
    )
    assert sustained < indeterminate
