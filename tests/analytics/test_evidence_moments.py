import pytest
from pydantic import ValidationError

from matcheyes.analytics.analysis import MatchAnalysis, analyse_match
from matcheyes.analytics.evidence import (
    EvidenceLabel,
    KeyEventEvidence,
    KeyEventType,
    MetricShiftEvidence,
    MomentKind,
    PlayerInvolvementEvidence,
    RunOfPlayEvidence,
)
from matcheyes.analytics.moments import build_moments
from matcheyes.analytics.timeline import Timeline
from matcheyes.domain.claims import ClaimStrength
from matcheyes.domain.time import MatchInstant
from tests.support.builders import AWAY, HOME, minimal_match, pid


def shift(team: str, metric: str, family: str, minute: int) -> MetricShiftEvidence:
    instant = MatchInstant(period=1, clock_ms=minute * 60_000)
    return MetricShiftEvidence(
        evidence_id=f"shift-{team}-{metric}-{minute}",
        label=EvidenceLabel.ANALYSIS,
        strength=ClaimStrength.OBSERVED,
        team_id=team,
        at=instant,
        statement="x",
        event_ids=(),
        metric=metric,
        family=family,
        direction="up",
        before_start=instant,
        after_end=instant,
        before_value=0.1,
        after_value=0.2,
        before_sample=10,
        after_sample=10,
        statistic=3.0,
    )


def test_analytics_evidence_cannot_claim_more_than_associated() -> None:
    raw = shift(HOME, "shots", "attacking", 10).model_dump()
    assert MetricShiftEvidence.model_validate(raw | {"strength": ClaimStrength.ASSOCIATED})
    with pytest.raises(ValidationError, match="cannot claim"):
        MetricShiftEvidence.model_validate(raw | {"strength": ClaimStrength.HYPOTHESISED})


def test_shifts_close_in_time_form_one_moment_and_cross_families_are_associated() -> None:
    timeline = Timeline(minimal_match().events)
    evidence = [
        shift(HOME, "high_regains", "pressing", 10),
        shift(HOME, "field_tilt", "territory", 14),
        shift(HOME, "shots", "attacking", 25),
        shift(AWAY, "shots", "attacking", 12),
    ]
    moments = build_moments(evidence, timeline, minimal_match().info, merge_bins=5)
    assert [(m.team_id, m.at.clock_ms // 60_000, m.strength) for m in moments] == [
        (HOME, 10, ClaimStrength.ASSOCIATED),
        (AWAY, 12, ClaimStrength.OBSERVED),
        (HOME, 25, ClaimStrength.OBSERVED),
    ]
    assert moments[0].evidence_ids == (
        "shift-kestrel-bay-high_regains-10",
        "shift-kestrel-bay-field_tilt-14",
    )
    assert moments[0].families == ("pressing", "territory")
    assert moments[0].score == 6.0


@pytest.fixture(scope="module")
def analysis() -> MatchAnalysis:
    return analyse_match(minimal_match())


def test_key_events_are_facts(analysis: MatchAnalysis) -> None:
    keys = [e for e in analysis.evidence if isinstance(e, KeyEventEvidence)]
    assert [(k.event_type, k.team_id) for k in keys] == [
        (KeyEventType.GOAL, AWAY),
        (KeyEventType.SUBSTITUTION, HOME),
    ]
    assert all(k.label is EvidenceLabel.FACT for k in keys)
    assert keys[1].player_id == pid(HOME, 15)
    assert keys[1].related_player_id == pid(HOME, 10)


def test_goal_in_the_first_minute_has_no_run_of_play(analysis: MatchAnalysis) -> None:
    (run,) = [e for e in analysis.evidence if isinstance(e, RunOfPlayEvidence)]
    assert run.field_tilt is None
    assert not run.against_run_of_play
    assert "opening minute" in run.statement


def test_substitute_involvement_by_hand(analysis: MatchAnalysis) -> None:
    (inv,) = [e for e in analysis.evidence if isinstance(e, PlayerInvolvementEvidence)]
    assert (inv.actions, inv.share) == (1, 0.5)  # throw-in of {throw-in, shot}
    assert (inv.replaced_actions, inv.replaced_share) == (0, None)
    assert inv.label is EvidenceLabel.ANALYSIS


def test_key_event_moments_link_their_analysis(analysis: MatchAnalysis) -> None:
    moments = [m for m in analysis.moments if m.kind is MomentKind.KEY_EVENT]
    assert len(moments) == 2
    goal, sub = moments
    assert goal.evidence_ids[1].startswith("run-")
    assert sub.evidence_ids[1].startswith("involvement-")
    assert all(m.label is EvidenceLabel.ANALYSIS for m in analysis.moments)
