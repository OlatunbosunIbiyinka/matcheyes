"""The lifecycle feed and its Stage 6 bridge: current truth only, history kept, stale views
flagged."""

from functools import cache

import pytest
from pydantic import ValidationError

from matcheyes.agents.contracts import EvidenceIntegrity
from matcheyes.agents.tools import MatchWorkspace
from matcheyes.domain.claims import ClaimStrength
from matcheyes.ingestion.log import DataStatus, LogStatus
from matcheyes.lifecycle.contracts import MATERIAL, ChangeKind, LifecycleState, StorylineState
from matcheyes.lifecycle.engine import replay
from matcheyes.lifecycle.evaluate import SnapshotEvaluation
from matcheyes.lifecycle.feed import (
    CurrentStatus,
    LifecycleFeed,
    audience_feed,
    current_revisions,
    lifecycle_feed,
    notice_text,
    view_is_current,
)
from matcheyes.lifecycle.reconcile import reconcile
from matcheyes.orchestration.audit import calibration_findings
from matcheyes.personalization.contracts import Audience, PersonalizationProfile, SectionKind
from matcheyes.personalization.render import personalize
from matcheyes_eval.stage7 import tampered_on
from tests.lifecycle.support import (
    auditor,
    evaluated,
    evaluator,
    failed,
    in_progress,
    insights,
    reference,
    reversed_direction,
)

ANALYST = PersonalizationProfile(audience=Audience.ANALYST)


def _status(state: LifecycleState, watermark: int = 0) -> LogStatus:
    return LogStatus(state.match_id, watermark, watermark, 0, DataStatus.CONTIGUOUS, 0, 0, 0, 0)


def _scripted(*evaluations: SnapshotEvaluation) -> LifecycleState:
    state = LifecycleState.empty(in_progress().info.match_id)
    for e in evaluations:
        state = reconcile(state, e)
    return state


@cache
def tampered() -> LifecycleState:
    match = in_progress()
    last = reference().state.last_snapshot
    assert last is not None
    return replay(match.info, match.events, tampered_on(last.header.watermark, evaluator())).state


def _workspace(snapshot_id: str) -> MatchWorkspace:
    return auditor().workspace(snapshot_id)


def test_statuses_distinguish_why_there_is_no_current_insight() -> None:
    a = insights()[0]
    empty = LifecycleState.empty(in_progress().info.match_id)
    assert lifecycle_feed(empty, _status(empty)).status is CurrentStatus.AWAITING_SNAPSHOT
    nothing = _scripted(evaluated(10))
    assert lifecycle_feed(nothing, _status(nothing, 10)).status is CurrentStatus.NO_CANDIDATE
    live = _scripted(evaluated(10, a))
    feed = lifecycle_feed(live, _status(live, 10))
    assert feed.status is CurrentStatus.CURRENT and len(feed.current) == 1
    down = _scripted(evaluated(10, a), failed(20))
    feed = lifecycle_feed(down, _status(down, 20))
    assert feed.status is CurrentStatus.UNAVAILABLE and feed.current == ()
    assert feed.unavailable == (down.storylines[0].storyline_id,)
    assert feed.unavailable_reason == "failed: RuntimeError"
    recovered = reconcile(down, evaluated(30, a))
    assert lifecycle_feed(recovered, _status(recovered, 30)).status is CurrentStatus.CURRENT


def test_withdrawn_storylines_stay_visible_with_a_notice() -> None:
    a = insights()[0]
    state = _scripted(evaluated(10, a), evaluated(20))
    feed = lifecycle_feed(state, _status(state, 20))
    assert feed.status is CurrentStatus.NO_CANDIDATE
    assert [r.storyline_id for r in feed.withdrawn] == [state.storylines[0].storyline_id]
    (notice,) = feed.notices
    assert notice.change_kinds == (ChangeKind.WITHDRAWN,)
    assert notice.text.startswith("Withdrawn as of ")


def test_opposite_direction_notices_name_the_successor() -> None:
    a = insights()[0]
    state = _scripted(evaluated(10, a), evaluated(20, reversed_direction(a)))
    texts = [n.text for n in lifecycle_feed(state, _status(state, 20)).notices]
    successor = state.storylines[1].storyline_id
    assert any(f"replaced by an opposite-direction change ({successor})" in t for t in texts)
    assert any(t.startswith("New as of ") for t in texts)


def test_evidence_only_changes_are_history_not_notices() -> None:
    a = insights()[0]
    trimmed = a.model_copy(
        update={"final": a.final.model_copy(update={"event_ids": a.final.event_ids[:-1]})}
    )
    state = _scripted(evaluated(10, a), evaluated(20, trimmed))
    r = state.storylines[0].revisions[-1]
    assert r.change_kinds == (ChangeKind.EVIDENCE_CHANGED,)
    assert lifecycle_feed(state, _status(state, 20)).notices == ()


def test_every_notice_is_factual_and_never_causal() -> None:
    for state in (reference().state, tampered()):
        for s in state.storylines:
            for r in s.revisions:
                if MATERIAL & set(r.change_kinds):
                    text = notice_text(state, r)
                    assert calibration_findings(text, ClaimStrength.OBSERVED) == [], text


def test_feed_validators_reject_inconsistent_feeds() -> None:
    feed = lifecycle_feed(reference().state, reference().status())
    assert feed.status is CurrentStatus.CURRENT
    with pytest.raises(ValidationError):
        LifecycleFeed.model_validate({**feed.model_dump(), "current": ()})
    with pytest.raises(ValidationError):
        LifecycleFeed.model_validate(
            {**feed.model_dump(), "withdrawn": feed.model_dump()["current"]}
        )


def test_the_audience_feed_shows_only_current_revisions() -> None:
    state = reference().state
    last = state.last_snapshot
    assert last is not None
    ws = _workspace(last.header.snapshot_id)
    current = {r.insight_fingerprint for r in current_revisions(state)}
    for audience in Audience:
        feed = audience_feed(state, PersonalizationProfile(audience=audience), ws)
        views = [*feed.primary, *feed.secondary]
        assert feed.withheld == ()
        assert {v.source_fingerprint for v in views} == current
        assert all(view_is_current(v, state) for v in views)


def test_the_audience_feed_needs_the_latest_snapshot_workspace() -> None:
    state = reference().state
    first = state.snapshots[0].header.snapshot_id
    with pytest.raises(ValueError, match="latest snapshot"):
        audience_feed(state, ANALYST, _workspace(first))


def test_a_view_of_a_superseded_revision_is_stale() -> None:
    state = reference().state
    s = next(x for x in state.storylines if len(x.revisions) > 1 and x.revisions[0].final)
    old = s.revisions[0]
    assert old.final is not None
    view = personalize(old.final, ANALYST, _workspace(old.snapshot_id))
    assert not view_is_current(view, state)
    forged = view.model_copy(update={"source_fingerprint": "0" * 64})
    assert not view_is_current(forged, state)


def test_compromised_integrity_propagates_on_the_same_snapshot() -> None:
    state = tampered()
    last = state.last_snapshot
    assert last is not None
    feed = lifecycle_feed(state, reference().status())
    compromised = [r for r in feed.current if r.evidence_integrity is EvidenceIntegrity.COMPROMISED]
    assert compromised
    for r in compromised:
        assert r.snapshot_id == last.header.snapshot_id
        assert {ChangeKind.INTEGRITY_CHANGED, ChangeKind.CREATED} & set(r.change_kinds)
        assert any(n.storyline_id == r.storyline_id for n in feed.notices)
    ws = _workspace(last.header.snapshot_id)
    for audience in Audience:
        view_feed = audience_feed(state, PersonalizationProfile(audience=audience), ws)
        for v in view_feed.primary:
            if v.source.evidence_integrity is EvidenceIntegrity.COMPROMISED:
                assert any(x.kind is SectionKind.INTEGRITY and x.mandatory for x in v.sections)
    assert all(
        s.state is StorylineState.OPEN
        for s in state.storylines
        if s.storyline_id in {r.storyline_id for r in compromised}
    )
