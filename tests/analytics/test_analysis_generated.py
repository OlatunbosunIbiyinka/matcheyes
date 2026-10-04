"""Properties of the analysis that must hold on any match, checked on generated matches."""

import pytest

from matcheyes.analytics.analysis import MatchAnalysis, analyse_match
from matcheyes.analytics.evidence import ANALYTICS_CLAIM_CEILING, MetricShiftEvidence
from matcheyes.analytics.possessions import ON_BALL, StartType
from matcheyes.domain.events import Pass, PassKind
from tests.synth.generated import SCENARIO_IDS, generated

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", params=SCENARIO_IDS)
def case(request: pytest.FixtureRequest) -> tuple[object, MatchAnalysis]:
    match = generated(request.param).observable
    return match, analyse_match(match)


def test_every_on_ball_event_belongs_to_exactly_one_possession(
    case: tuple[object, MatchAnalysis],
) -> None:
    match, analysis = case
    owner: dict[str, int] = {}
    for p in analysis.possessions:
        for event_id in p.event_ids:
            assert event_id not in owner
            owner[event_id] = p.index
    on_ball = [e for e in match.events if isinstance(e, ON_BALL)]  # type: ignore[attr-defined]
    assert all(e.event_id in owner for e in on_ball)


def test_possessions_alternate_unless_restarted_by_kick_off(
    case: tuple[object, MatchAnalysis],
) -> None:
    _, analysis = case
    for prev, nxt in zip(analysis.possessions, analysis.possessions[1:], strict=False):
        if prev.team_id == nxt.team_id:
            assert (
                nxt.start_type is StartType.KICK_OFF
                or prev.period != nxt.period
                or (prev.end_reason.value in {"goal", "stoppage", "period_end"})
            )


def test_kick_offs_always_start_possessions(case: tuple[object, MatchAnalysis]) -> None:
    match, analysis = case
    starts = {p.event_ids[0] for p in analysis.possessions}
    kick_offs = [
        e
        for e in match.events  # type: ignore[attr-defined]
        if isinstance(e, Pass) and e.kind is PassKind.KICK_OFF
    ]
    assert all(e.event_id in starts for e in kick_offs)


def test_evidence_is_traceable_and_within_the_claim_ceiling(
    case: tuple[object, MatchAnalysis],
) -> None:
    match, analysis = case
    known = {e.event_id for e in match.events}  # type: ignore[attr-defined]
    ids = [e.evidence_id for e in analysis.evidence]
    assert len(ids) == len(set(ids))
    for item in analysis.evidence:
        assert set(item.event_ids) <= known
        assert item.strength.rank <= ANALYTICS_CLAIM_CEILING.rank
    for moment in analysis.moments:
        assert set(moment.evidence_ids) <= set(ids)
        assert set(moment.context_evidence_ids) <= set(ids)
        assert moment.strength.rank <= ANALYTICS_CLAIM_CEILING.rank


def test_shift_statements_quote_their_own_numbers(case: tuple[object, MatchAnalysis]) -> None:
    _, analysis = case
    for item in analysis.evidence:
        if isinstance(item, MetricShiftEvidence):
            assert ("rose" in item.statement) == (item.direction == "up")
            assert (item.after_value > item.before_value) == (item.direction == "up")


def test_moments_are_in_match_order(case: tuple[object, MatchAnalysis]) -> None:
    _, analysis = case
    keys = [m.at.sort_key for m in analysis.moments]
    assert keys == sorted(keys)


def test_analysis_is_deterministic_and_round_trips() -> None:
    match = generated(SCENARIO_IDS[1]).observable
    first, second = analyse_match(match), analyse_match(match)
    assert first.model_dump_json() == second.model_dump_json()
    assert MatchAnalysis.model_validate_json(first.model_dump_json()) == first
