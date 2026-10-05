"""Evidence levels and ranking for contextual candidates.

Levels describe how much observable support a candidate has, not how likely a cause is:

* INSUFFICIENT - the shift disappears when compared within the same game state.
* WEAK - the shift is unusual, but no other metric family corroborates it; or it is capped.
* MODERATE - unusual, plus independent support from at least one other metric family, and not
  shown to be a burst (persistence SUSTAINED, or INDETERMINATE when it cannot be measured).
* STRONG - unusual, independent support from at least two other families, sustained, and not
  contradicted by any pattern signal.

Caps at WEAK: the shift is context-aligned (the ordinary response to a coincident goal or
dismissal); it is transient or reverses within its window; or its pattern is contradicted.

Persistence is a gate, not a route to MODERATE on its own: a shift significant over 15 minutes
usually holds in each third of the window, so "sustained" alone does not separate real changes
from background variation (docs/stage3-evaluation.md, rejected approaches).

Claim strength stays within the analytics ceiling: ASSOCIATED when at least one independent
family changed with the core (two co-occurring changes beyond their baselines), else OBSERVED.
"""

from enum import StrEnum

from matcheyes.analytics.baselines import BaselineAssessment
from matcheyes.analytics.patterns import PatternAssessment
from matcheyes.analytics.persistence import Persistence, PersistenceAssessment
from matcheyes.domain.claims import ClaimStrength

STRONG_MIN_FAMILIES = 2
"""Families beyond this add nothing: related metrics are correlated, so a third family is not
a third independent witness."""


class EvidenceLevel(StrEnum):
    INSUFFICIENT = "insufficient"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"

    @property
    def rank(self) -> int:
        return _LEVELS.index(self)


_LEVELS = (
    EvidenceLevel.INSUFFICIENT,
    EvidenceLevel.WEAK,
    EvidenceLevel.MODERATE,
    EvidenceLevel.STRONG,
)


def independent_families(pattern: PatternAssessment | None) -> int:
    return 0 if pattern is None else len(pattern.independent_families)


def grade(
    baseline: BaselineAssessment,
    pattern: PatternAssessment | None,
    persistence: PersistenceAssessment,
) -> tuple[EvidenceLevel, tuple[str, ...]]:
    """Level plus the reasons for it, in plain words."""
    if not baseline.unusual:
        return EvidenceLevel.INSUFFICIENT, ("not unusual when compared within the same game state",)
    families = independent_families(pattern)
    sustained = persistence.persistence is Persistence.SUSTAINED
    contradicted = pattern is not None and pattern.contradicted
    basis = ["unusual against its baseline"]
    if pattern is not None and families:
        basis.append(f"independent support from: {', '.join(pattern.independent_families)}")
    if sustained:
        basis.append("sustained across its window")

    level = EvidenceLevel.WEAK
    if families:
        level = EvidenceLevel.MODERATE
    if families >= STRONG_MIN_FAMILIES and sustained and pattern and not pattern.contradictions:
        level = EvidenceLevel.STRONG

    caps = []
    if baseline.aligned_with is not None:
        caps.append("capped: the ordinary response to a coincident goal or dismissal")
    if persistence.persistence in (Persistence.REVERSED, Persistence.TRANSIENT):
        caps.append(f"capped: {persistence.persistence.value} within its window")
    if contradicted:
        caps.append("capped: contradicted by related signals")
    if caps and level.rank > EvidenceLevel.WEAK.rank:
        level = EvidenceLevel.WEAK
    return level, (*basis, *caps)


def claim_strength(level: EvidenceLevel, pattern: PatternAssessment | None) -> ClaimStrength:
    if level is not EvidenceLevel.INSUFFICIENT and independent_families(pattern):
        return ClaimStrength.ASSOCIATED
    return ClaimStrength.OBSERVED


def rank_key(
    level: EvidenceLevel,
    pattern: PatternAssessment | None,
    persistence: PersistenceAssessment,
    statistic: float | None,
) -> tuple[int, int, int, int, float]:
    """Sort key, best first when sorted ascending. Order of precedence: level, persistence,
    independent families (capped), fewer contradictions, contextual statistic."""
    contradictions = 0 if pattern is None else len(pattern.contradictions)
    return (
        -level.rank,
        0 if persistence.persistence is Persistence.SUSTAINED else 1,
        -min(independent_families(pattern), STRONG_MIN_FAMILIES),
        contradictions,
        -abs(statistic or 0.0),
    )
