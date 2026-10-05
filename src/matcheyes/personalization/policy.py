"""The deterministic audience policy: relevance, feed placement, order and depth.

Relevance decides only where a view sits and in which order. It is computed from the verified
verdict, the Stage 3 level, the profile's preferences and integrity, and it is never an input
to anything that decides truth. Every term is recorded in `relevance_basis`.
"""

from dataclasses import dataclass
from enum import StrEnum

from matcheyes.agents.contracts import EvidenceIntegrity, FinalInsight, Verdict
from matcheyes.analytics.strength import EvidenceLevel
from matcheyes.personalization.contracts import LABELLED, Audience, SectionKind
from matcheyes.personalization.involvement import Involvement

VERDICT_WEIGHT = {
    Verdict.EXPLAINED: 40,
    Verdict.TENTATIVE: 30,
    Verdict.NATURAL_VARIATION: 10,
    Verdict.INSUFFICIENT_EVIDENCE: 5,
    Verdict.UNAVAILABLE: 0,
}
COMPROMISED_PENALTY = 15
"""Ranks a compromised insight below an intact equivalent; it is never removed."""


@dataclass(frozen=True)
class Weights:
    club_team: int
    club_opponent: int
    player: int
    metric: int


PREFERENCE_WEIGHTS = {
    Audience.FAN: Weights(club_team=20, club_opponent=5, player=10, metric=10),
    Audience.BROADCASTER: Weights(club_team=2, club_opponent=1, player=2, metric=2),
    Audience.ANALYST: Weights(club_team=20, club_opponent=5, player=10, metric=10),
}
"""Fans follow their club; broadcasters lead with the strongest verified insight, so their
preferences plus the Stage 3 level stay below the 10-point gap between verdicts."""

OPTIONAL_SECTIONS: dict[Audience, frozenset[SectionKind]] = {
    Audience.FAN: frozenset({SectionKind.CONTEXT, SectionKind.GLOSSARY, SectionKind.INVOLVEMENT}),
    Audience.BROADCASTER: frozenset(
        {SectionKind.CONTEXT, SectionKind.TRIGGER, SectionKind.INVOLVEMENT}
    ),
    Audience.ANALYST: frozenset(
        {
            SectionKind.INVOLVEMENT,
            SectionKind.ALTERNATIVES,
            SectionKind.EVIDENCE,
            SectionKind.QUARANTINE,
            SectionKind.DETAIL,
            SectionKind.NARRATIVE,
        }
    ),
}
MANDATORY_SECTIONS = frozenset(
    {
        SectionKind.FACT,
        SectionKind.INTEGRITY,
        SectionKind.INTERPRETATION,
        SectionKind.CAVEAT,
        SectionKind.NO_INSIGHT,
    }
)
ALL_OPTIONAL = frozenset(SectionKind) - MANDATORY_SECTIONS


class Placement(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


def omitted(audience: Audience) -> tuple[str, ...]:
    """Optional content this audience's view leaves out, stated rather than silently dropped."""
    return tuple(k.value for k in SectionKind if k in ALL_OPTIONAL - OPTIONAL_SECTIONS[audience])


def relevance(
    final: FinalInsight, inv: Involvement, audience: Audience
) -> tuple[int, tuple[str, ...]]:
    weights = PREFERENCE_WEIGHTS[audience]
    score = VERDICT_WEIGHT[final.verdict]
    basis = [f"verdict {final.verdict.value} (+{score})"]
    level = EvidenceLevel(final.stage3_level).rank
    score += level
    basis.append(f"Stage 3 level {final.stage3_level} (+{level})")
    if inv.club_role == "team":
        score += weights.club_team
        basis.append(f"favourite club is this insight's team (+{weights.club_team})")
    elif inv.club_role == "opponent":
        score += weights.club_opponent
        basis.append(f"favourite club is the opponent (+{weights.club_opponent})")
    if inv.player_events:
        score += weights.player
        basis.append(
            f"favourite player acts in {len(inv.player_events)} cited event(s) (+{weights.player})"
        )
    if inv.metric:
        score += weights.metric
        basis.append(f"favourite metric (+{weights.metric})")
    if final.evidence_integrity is EvidenceIntegrity.COMPROMISED:
        score -= COMPROMISED_PENALTY
        basis.append(f"evidence integrity compromised (-{COMPROMISED_PENALTY})")
    return score, tuple(basis)


def placement(final: FinalInsight, audience: Audience) -> Placement:
    """Fans and broadcasters see explanations first; insights with no verified explanation move
    to an explicit secondary list. A compromised insight always stays in the primary feed."""
    if audience is Audience.ANALYST:
        return Placement.PRIMARY
    if final.verdict in LABELLED or final.evidence_integrity is EvidenceIntegrity.COMPROMISED:
        return Placement.PRIMARY
    return Placement.SECONDARY


def order_key(
    final: FinalInsight, score: int, audience: Audience
) -> tuple[int, tuple[int, int], str]:
    """Analysts read a timeline; fans and broadcasters a ranking. Ties break on candidate ID."""
    if audience is Audience.ANALYST:
        return (0, final.at.sort_key, final.candidate_id)
    return (-score, final.at.sort_key, final.candidate_id)
