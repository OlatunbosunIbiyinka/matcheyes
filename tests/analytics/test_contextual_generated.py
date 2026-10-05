"""Properties of Stage 3 contextual analysis that must hold on any match."""

import pytest

from matcheyes.__main__ import format_contextual
from matcheyes.analytics.analysis import MatchAnalysis, analyse_match
from matcheyes.analytics.baselines import BaselineKind
from matcheyes.analytics.contextual import ContextualAnalysis, analyse_contextual
from matcheyes.analytics.evidence import ANALYTICS_CLAIM_CEILING, MetricShiftEvidence
from matcheyes.analytics.strength import EvidenceLevel, rank_key
from matcheyes.domain.match import ObservableMatch
from tests.support.builders import minimal_match
from tests.synth.generated import SCENARIO_IDS, generated

pytestmark = pytest.mark.integration

Case = tuple[ObservableMatch, MatchAnalysis, ContextualAnalysis]


@pytest.fixture(scope="module", params=SCENARIO_IDS)
def case(request: pytest.FixtureRequest) -> Case:
    match = generated(request.param).observable
    stage2 = analyse_match(match)
    return match, stage2, analyse_contextual(match, stage2)


def test_one_candidate_per_stage2_shift(case: Case) -> None:
    _, stage2, s3 = case
    shifts = [e.evidence_id for e in stage2.evidence if isinstance(e, MetricShiftEvidence)]
    assert sorted(c.shift_evidence_id for c in s3.candidates) == sorted(shifts)
    assert s3.stage2_version == stage2.analytics_version


def test_every_candidate_traces_to_observed_events(case: Case) -> None:
    match, _, s3 = case
    known = {e.event_id for e in match.events}
    for c in s3.candidates:
        assert c.event_ids
        assert set(c.event_ids) <= known
        assert set(c.context.nearby_key_event_ids) <= known


def test_claims_stay_within_the_ceiling(case: Case) -> None:
    _, _, s3 = case
    for item in (*s3.candidates, *s3.moments):
        assert item.strength.rank <= ANALYTICS_CLAIM_CEILING.rank


def test_ranks_follow_the_documented_order(case: Case) -> None:
    _, _, s3 = case
    assert [c.rank for c in s3.candidates] == list(range(1, len(s3.candidates) + 1))
    keys = [
        rank_key(c.level, c.pattern, c.persistence, c.baseline.statistic) for c in s3.candidates
    ]
    assert keys == sorted(keys)


def test_levels_respect_their_definitions(case: Case) -> None:
    _, _, s3 = case
    for c in s3.candidates:
        removed = c.baseline.kind is BaselineKind.REMOVED
        assert (c.level is EvidenceLevel.INSUFFICIENT) == removed
        if c.baseline.aligned_with is not None:
            assert c.level.rank <= EvidenceLevel.WEAK.rank
        if c.level.rank >= EvidenceLevel.MODERATE.rank:
            assert c.pattern is not None and c.pattern.independent_families


def test_moments_hold_exactly_the_candidates_at_moment_level(case: Case) -> None:
    _, _, s3 = case
    by_id = s3.candidate_by_id()
    in_moments = [i for m in s3.moments for i in m.candidate_ids]
    assert len(in_moments) == len(set(in_moments))
    expected = {
        c.candidate_id for c in s3.candidates if c.level.rank >= s3.config.moment_level.rank
    }
    assert set(in_moments) == expected
    for m in s3.moments:
        lead = by_id[m.candidate_ids[0]]
        assert all(by_id[i].team_id == m.team_id for i in m.candidate_ids)
        assert all(
            abs(by_id[i].bin_index - lead.bin_index) <= s3.config.moment_merge_bins
            for i in m.candidate_ids
        )


def test_windows_match_the_stage2_configuration(case: Case) -> None:
    _, stage2, s3 = case
    d = stage2.config.detection
    for c in s3.candidates:
        lo, t = c.baseline.before
        assert t == c.bin_index
        assert t - lo <= d.baseline_bins
        assert c.baseline.after[1] - c.baseline.after[0] <= d.window_bins
        assert c.context.workload.team_id == c.team_id


def test_analysis_is_deterministic_and_leaves_stage2_untouched(case: Case) -> None:
    match, stage2, s3 = case
    before = stage2.model_dump_json()
    again = analyse_contextual(match, stage2)
    assert again.model_dump_json() == s3.model_dump_json()
    assert stage2.model_dump_json() == before
    assert analyse_contextual(match).model_dump_json() == s3.model_dump_json()


def test_report_lists_every_candidate(case: Case) -> None:
    _, _, s3 = case
    text = format_contextual(s3)
    assert text.count("basis:") == len(s3.candidates)


def test_match_without_shifts_has_context_but_no_candidates() -> None:
    s3 = analyse_contextual(minimal_match())
    assert s3.candidates == ()
    assert s3.moments == ()
    assert len(s3.context.bins) == 96
