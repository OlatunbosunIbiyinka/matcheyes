"""Storyline reconciliation on scripted snapshots of genuine pipeline insights."""

from itertools import pairwise

import pytest

from matcheyes.agents.contracts import EvidenceIntegrity, HypothesisKind, Verdict
from matcheyes.domain.claims import ClaimStrength
from matcheyes.lifecycle.contracts import (
    GENESIS,
    MATERIAL,
    ChangeKind,
    LifecycleState,
    SnapshotOutcome,
    StorylineState,
    WithdrawalReason,
    chain_fingerprint,
)
from matcheyes.lifecycle.evaluate import InsightOutcome, SnapshotEvaluation
from matcheyes.lifecycle.reconcile import change_kinds, reconcile
from matcheyes.personalization.contracts import fingerprint
from tests.lifecycle.support import (
    evaluated,
    failed,
    in_progress,
    insights,
    moved,
    reversed_direction,
)

K = ChangeKind


def _run(*evaluations: SnapshotEvaluation) -> list[LifecycleState]:
    states = [LifecycleState.empty(in_progress().info.match_id)]
    for evaluation in evaluations:
        states.append(reconcile(states[-1], evaluation))
    return states[1:]


@pytest.fixture
def a() -> InsightOutcome:
    return insights()[0]


def _other_key(a: InsightOutcome) -> InsightOutcome:
    key = (a.final.team_id, a.metric, a.direction)
    return next(i for i in insights() if (i.final.team_id, i.metric, i.direction) != key)


def _with_final(insight: InsightOutcome, **update: object) -> InsightOutcome:
    return insight.model_copy(update={"final": insight.final.model_copy(update=update)})


def test_a_new_insight_creates_an_open_storyline(a: InsightOutcome) -> None:
    (state,) = _run(evaluated(10, a))
    (s,) = state.storylines
    f = a.final
    key = f"{in_progress().info.match_id}-{f.team_id}-{a.metric}-{a.direction}"
    assert s.storyline_id == f"sl-{key}-{a.anchor_bin}"
    assert (s.state, s.first_anchor, s.ordinal, s.linked_to) == (
        StorylineState.OPEN,
        a.anchor_bin,
        1,
        None,
    )
    (r,) = s.revisions
    assert r.change_kinds == (K.CREATED,) and r.number == 1
    assert r.insight_fingerprint == fingerprint(f)
    assert r.previous_chain_fingerprint == GENESIS
    assert r.chain_fingerprint == chain_fingerprint(
        GENESIS, r.snapshot_id, r.insight_fingerprint, r.state
    )
    assert s.verified_through == state.snapshots[0].header.snapshot_id
    assert state.snapshots[0].storyline_ids == (s.storyline_id,)


def test_an_unchanged_insight_is_verified_again_without_a_revision(a: InsightOutcome) -> None:
    first, second = _run(evaluated(10, a), evaluated(20, a))
    s = second.storylines[0]
    assert len(s.revisions) == 1
    assert s.verified_through == second.snapshots[-1].header.snapshot_id
    assert first.storylines[0].revisions == s.revisions


def test_a_moved_onset_reanchors_the_same_storyline(a: InsightOutcome) -> None:
    _, state = _run(evaluated(10, a), evaluated(20, moved(a, 3)))
    (s,) = state.storylines
    assert s.revisions[-1].change_kinds[0] is K.REANCHORED
    assert (s.first_anchor, s.anchor) == (a.anchor_bin, a.anchor_bin + 3)
    assert s.storyline_id.endswith(f"-{a.anchor_bin}")


def test_an_onset_beyond_the_suppression_distance_is_a_new_storyline(a: InsightOutcome) -> None:
    _, state = _run(evaluated(10, a), evaluated(20, moved(a, 16)))
    assert [s.state for s in state.storylines] == [StorylineState.WITHDRAWN, StorylineState.OPEN]


def test_change_kinds_come_from_verified_fields_not_prose(a: InsightOutcome) -> None:
    f = a.final
    verdict = (
        Verdict.NATURAL_VARIATION
        if f.verdict is not Verdict.NATURAL_VARIATION
        else Verdict.TENTATIVE
    )
    strength = (
        ClaimStrength.SUPPORTED
        if f.strength is not ClaimStrength.SUPPORTED
        else ClaimStrength.OBSERVED
    )
    assert change_kinds(f, f.model_copy(update={"verdict": verdict})) == (K.VERDICT_CHANGED,)
    assert change_kinds(f, f.model_copy(update={"strength": strength})) == (K.STRENGTH_CHANGED,)
    leading = next(k for k in HypothesisKind if k is not f.leading)
    assert change_kinds(f, f.model_copy(update={"leading": leading})) == (K.EXPLANATION_CHANGED,)
    flipped = next(i for i in EvidenceIntegrity if i is not f.evidence_integrity)
    assert change_kinds(f, f.model_copy(update={"evidence_integrity": flipped})) == (
        K.INTEGRITY_CHANGED,
    )
    assert change_kinds(f, f.model_copy(update={"narrative": f.narrative + " Reworded."})) == ()
    assert change_kinds(f, f.model_copy(update={"event_ids": f.event_ids[:-1]})) == (
        K.EVIDENCE_CHANGED,
    )
    relabelled = tuple(
        e.model_copy(update={"evidence_id": f"ev-{i + 50}"}) for i, e in enumerate(f.evidence)
    )
    assert change_kinds(f, f.model_copy(update={"evidence": relabelled})) == ()


def test_a_text_only_change_is_history_but_not_material(a: InsightOutcome) -> None:
    reworded = _with_final(a, narrative=a.final.narrative + " Reworded.")
    _, state = _run(evaluated(10, a), evaluated(20, reworded))
    r = state.storylines[0].revisions[-1]
    assert r.change_kinds == () and not MATERIAL & set(r.change_kinds)
    assert r.insight_fingerprint == fingerprint(reworded.final)


def test_an_audit_only_change_records_the_fresh_audit_without_a_notice(a: InsightOutcome) -> None:
    flagged = a.model_copy(update={"audit_findings": ("claim: flagged on this snapshot",)})
    _, state, cleared = _run(evaluated(10, a), evaluated(20, flagged), evaluated(30, a))
    s = state.storylines[0]
    r = s.revisions[-1]
    assert [x.number for x in s.revisions] == [1, 2]
    assert r.change_kinds == () and r.insight_fingerprint == s.revisions[0].insight_fingerprint
    assert r.audit_findings == flagged.audit_findings
    assert r.snapshot_id == s.verified_through == state.snapshots[-1].header.snapshot_id
    assert cleared.storylines[0].current.audit_findings == ()


def test_a_verdict_change_appends_a_revision(a: InsightOutcome) -> None:
    other = (
        Verdict.NATURAL_VARIATION
        if a.final.verdict is not Verdict.NATURAL_VARIATION
        else Verdict.TENTATIVE
    )
    _, state = _run(evaluated(10, a), evaluated(20, _with_final(a, verdict=other)))
    s = state.storylines[0]
    assert [r.number for r in s.revisions] == [1, 2]
    assert s.revisions[1].change_kinds == (K.VERDICT_CHANGED,)
    assert s.revisions[1].previous_chain_fingerprint == s.revisions[0].chain_fingerprint


def test_a_missing_insight_withdraws_and_a_returning_one_is_reinstated(a: InsightOutcome) -> None:
    _, gone, back = _run(evaluated(10, a), evaluated(20), evaluated(30, a))
    s = gone.storylines[0]
    assert s.state is StorylineState.WITHDRAWN and s.verified_through is None
    w = s.revisions[-1]
    assert (w.change_kinds, w.withdrawal_reason, w.final) == (
        (K.WITHDRAWN,),
        WithdrawalReason.NOT_DETECTED,
        None,
    )
    (s,) = back.storylines
    assert s.state is StorylineState.OPEN and s.storyline_id == gone.storylines[0].storyline_id
    assert s.revisions[-1].change_kinds == (K.REINSTATED,)
    assert s.revisions[-1].insight_fingerprint == s.revisions[0].insight_fingerprint


def test_an_opposite_change_withdraws_and_links(a: InsightOutcome) -> None:
    opposite = reversed_direction(moved(a, 2))
    _, state = _run(evaluated(10, a), evaluated(20, opposite))
    old, new = state.storylines
    assert old.state is StorylineState.WITHDRAWN
    assert old.revisions[-1].withdrawal_reason is WithdrawalReason.OPPOSITE_DIRECTION
    assert new.linked_to == old.storyline_id and new.direction == opposite.direction
    assert new.ordinal == 2 and state.snapshots[-1].storyline_ids == (new.storyline_id,)


def test_a_failed_snapshot_is_recorded_and_changes_no_storyline(a: InsightOutcome) -> None:
    first, failure = _run(evaluated(10, a), failed(20))
    assert failure.storylines == first.storylines
    record = failure.snapshots[-1]
    assert (record.outcome, record.insights, record.storyline_ids) == (
        SnapshotOutcome.FAILED,
        0,
        (),
    )


def test_two_storylines_with_their_own_keys(a: InsightOutcome) -> None:
    b = _other_key(a)
    (state,) = _run(evaluated(10, a, b))
    assert len({s.storyline_id for s in state.storylines}) == 2
    assert [s.ordinal for s in state.storylines] == [1, 2]


def test_history_is_append_only(a: InsightOutcome) -> None:
    b = _other_key(a)
    other = (
        Verdict.NATURAL_VARIATION
        if a.final.verdict is not Verdict.NATURAL_VARIATION
        else Verdict.TENTATIVE
    )
    states = _run(
        evaluated(10, a),
        evaluated(20, a, b),
        evaluated(30, _with_final(a, verdict=other)),
        failed(40),
        evaluated(50, moved(a, 4), b),
        evaluated(60, reversed_direction(b)),
    )
    for earlier, later in pairwise(states):
        assert later.snapshots[: len(earlier.snapshots)] == earlier.snapshots
        by_id = {s.storyline_id: s for s in later.storylines}
        for s in earlier.storylines:
            assert by_id[s.storyline_id].revisions[: len(s.revisions)] == s.revisions


def test_reconciliation_refuses_other_matches_and_old_snapshots(a: InsightOutcome) -> None:
    (state,) = _run(evaluated(10, a))
    with pytest.raises(ValueError, match="watermark"):
        reconcile(state, evaluated(10, a))
    foreign = evaluated(20, a)
    foreign = foreign.model_copy(
        update={"header": foreign.header.model_copy(update={"match_id": "x"})}
    )
    with pytest.raises(ValueError, match="another match"):
        reconcile(state, foreign)
    with pytest.raises(ValueError, match="suppression"):
        reconcile(state, evaluated(20, a).model_copy(update={"suppression_bins": None}))
