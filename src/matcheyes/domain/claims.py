"""The claim-strength ladder (see docs/causal-claims.md).

Every analytical or AI statement about *why* something happened carries one of these levels.
Levels are ordered; a statement may only be presented at the level its evidence earns.
Temporal proximity alone never earns more than ASSOCIATED.
"""

from enum import StrEnum


class ClaimStrength(StrEnum):
    OBSERVED = "observed"
    """Directly present in the event data (e.g. "Team B made 9 recoveries in their final third")."""

    CORRELATED = "correlated"
    """Two metric series co-vary across the match. Symmetric; says nothing about direction."""

    ASSOCIATED = "associated"
    """Two changes co-occur in the same window beyond their baselines. Still no direction."""

    HYPOTHESISED = "hypothesised"
    """A proposed cause with a stated mechanism and temporal precedence. Not yet tested."""

    SUPPORTED = "supported"
    """A hypothesis that passed every causal test and survived alternative explanations.
    Presented as "the evidence supports", never as proven."""

    @property
    def rank(self) -> int:
        return _ORDER.index(self)

    def at_most(self, ceiling: "ClaimStrength") -> "ClaimStrength":
        return self if self.rank <= ceiling.rank else ceiling


_ORDER: tuple[ClaimStrength, ...] = (
    ClaimStrength.OBSERVED,
    ClaimStrength.CORRELATED,
    ClaimStrength.ASSOCIATED,
    ClaimStrength.HYPOTHESISED,
    ClaimStrength.SUPPORTED,
)
