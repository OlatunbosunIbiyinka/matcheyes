"""The independent lifecycle audit: genuine states are clean; every injected fault is caught."""

from functools import cache

import pytest

from matcheyes.lifecycle.audit import LIFECYCLE_CHECKS, LifecycleAuditor, audit_lifecycle
from matcheyes.lifecycle.contracts import LifecycleState, StorylineState
from matcheyes.lifecycle.engine import replay
from matcheyes.lifecycle.evaluate import Evaluator, SnapshotEvaluation
from matcheyes.lifecycle.reconcile import reconcile
from matcheyes.lifecycle.snapshot import Snapshot
from matcheyes_eval.stage7 import LIFECYCLE_FAULTS, caught, tampered_on
from tests.lifecycle.support import (
    auditor,
    evaluated,
    evaluator,
    in_progress,
    insights,
    reference,
    reversed_direction,
)


def _dropping(after: int) -> Evaluator:
    """The cached pipeline, but from `after` on the first storyline's key is no longer detected,
    so a genuine run has withdrawn storylines."""
    first = reference().state.storylines[0]
    key = (first.team_id, first.metric, first.direction)

    def evaluate(snapshot: Snapshot) -> SnapshotEvaluation:
        result = evaluator()(snapshot)
        if snapshot.header.watermark < after:
            return result
        kept = tuple(i for i in result.insights if (i.final.team_id, i.metric, i.direction) != key)
        return result.model_copy(update={"insights": kept})

    return evaluate


@cache
def genuine() -> tuple[tuple[LifecycleState, LifecycleAuditor], ...]:
    match = in_progress()
    events = reference().log.events()
    last = reference().state.snapshots[-1].header.watermark
    runs: list[tuple[LifecycleState, LifecycleAuditor]] = [(reference().state, auditor())]
    for ev in (tampered_on(last, evaluator()), _dropping(len(events) * 3 // 4)):
        state = replay(match.info, match.events, ev).state
        runs.append(
            (state, LifecycleAuditor(match.info, events, ev, workspaces=auditor().workspaces))
        )
    return tuple(runs)


def test_genuine_states_audit_clean() -> None:
    for state, a in genuine():
        assert a.audit(state) == []
    assert any(
        s.state is StorylineState.WITHDRAWN for state, _ in genuine() for s in state.storylines
    )


def test_the_audit_without_reproduction_is_also_clean() -> None:
    match = in_progress()
    findings = audit_lifecycle(
        match.info, reference().log.events(), reference().state, evaluator(), reproduce=False
    )
    assert findings == []


def test_a_state_of_another_match_is_refused() -> None:
    forged = reference().state.model_copy(update={"match_id": "other"})
    assert auditor().audit(forged) == ["snapshot: state belongs to another match"]


@pytest.mark.parametrize("name", [n for n in LIFECYCLE_FAULTS if n != "unlinked_opposite"])
def test_every_fault_is_caught_by_its_check(name: str) -> None:
    expected, fault = LIFECYCLE_FAULTS[name]
    events = reference().log.events()
    for state, a in genuine():
        forged = fault(state, events)
        if forged is None or forged == state:
            continue
        findings = a.audit(forged)
        assert caught(findings, expected), (name, findings)
        assert {f.split(":", 1)[0] for f in findings} <= set(LIFECYCLE_CHECKS)
        return
    pytest.fail(f"{name} applies to no genuine state")


def _flagging(watermark: int) -> Evaluator:
    """The cached pipeline, except that on the snapshot at `watermark` the Stage 5 audit of every
    insight reports a finding (the insights themselves are unchanged)."""

    def evaluate(snapshot: Snapshot) -> SnapshotEvaluation:
        result = evaluator()(snapshot)
        if snapshot.header.watermark != watermark:
            return result
        flagged = tuple(
            i.model_copy(update={"audit_findings": ("claim: flagged on this snapshot",)})
            for i in result.insights
        )
        return result.model_copy(update={"insights": flagged})

    return evaluate


def test_stale_audit_findings_presented_as_current_are_caught() -> None:
    match = in_progress()
    events = reference().log.events()
    last = reference().state.snapshots[-1].header.watermark
    a = LifecycleAuditor(match.info, events, _flagging(last), workspaces=auditor().workspaces)
    findings = a.audit(reference().state)
    assert "current: current audit findings differ from a fresh audit" in findings
    assert caught(findings, {"current"})


def test_an_unlinked_opposite_storyline_is_caught() -> None:
    a = insights()[0]
    state = LifecycleState.empty(in_progress().info.match_id)
    for e in (evaluated(10, a), evaluated(20, reversed_direction(a))):
        state = reconcile(state, e)
    expected, fault = LIFECYCLE_FAULTS["unlinked_opposite"]
    forged = fault(state, reference().log.events())
    assert forged is not None
    before = set(auditor().audit(state))
    new = set(auditor().audit(forged)) - before
    assert caught(sorted(new), expected), new
