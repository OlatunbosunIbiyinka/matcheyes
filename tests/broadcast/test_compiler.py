"""The cue compiler presents the lifecycle record; it is deterministic, traceable and complete."""

import pytest

from matcheyes.broadcast.compiler import CueCompiler, WorkspaceCache, compile_timeline
from matcheyes.broadcast.contracts import (
    KICK_OFF,
    MAX_ON_SCREEN,
    RETRACTION_TEXT,
    BroadcastStatus,
    CueKind,
    InsightSource,
    MomentKind,
    MomentSource,
    RetractionReason,
    RetractionSource,
    StatusSource,
)
from matcheyes.broadcast.facts import extract_moments
from matcheyes.broadcast.history import current_at
from matcheyes.broadcast.selection import interrupts
from matcheyes.ingestion.log import DataStatus
from matcheyes.lifecycle.contracts import ChangeKind, SnapshotRecord
from matcheyes.lifecycle.engine import replay
from matcheyes.lifecycle.feed import notice_text
from matcheyes.personalization.contracts import Audience, PersonalizationProfile
from matcheyes_eval.stage7 import failing_on, tampered_on
from tests.broadcast.support import (
    BROADCASTER,
    FAN,
    cards,
    on_screen,
    states_by_snapshot,
    timeline,
)
from tests.lifecycle.support import evaluator, in_progress, reference


def test_the_timeline_opens_with_awaiting_then_the_kick_off_moment() -> None:
    tl = timeline()
    first, second = tl.cues[:2]
    assert isinstance(first.source, StatusSource)
    assert first.source.status is BroadcastStatus.AWAITING_SNAPSHOT
    assert first.show_from == KICK_OFF and first.snapshot_id is None
    assert isinstance(second.source, MomentSource)
    assert second.source.moment is MomentKind.PERIOD_START
    assert second.show_from == reference().state.snapshots[0].header.as_of
    assert tl.snapshots == len(reference().state.snapshots)


def test_compiling_is_deterministic_and_equals_snapshot_by_snapshot_compilation() -> None:
    ref = reference()
    events = ref.log.events()
    again = compile_timeline(in_progress().info, events, ref.state, FAN)
    assert again.model_dump_json() == timeline().model_dump_json()
    compiler = CueCompiler(in_progress().info, FAN)
    for state in states_by_snapshot():
        compiler.advance(state, events)
    assert compiler.timeline() == timeline()


def test_insight_cues_trace_to_a_current_revision_of_their_snapshot() -> None:
    state = reference().state
    records = {r.header.snapshot_id: r for r in state.snapshots}
    shown = [c for c in timeline().cues if isinstance(c.source, InsightSource)]
    assert shown
    for cue in shown:
        source = cue.source
        assert isinstance(source, InsightSource)
        record = records[source.snapshot_id]
        current = {r.storyline_id: r for r in current_at(state, record)}
        rev = current[source.storyline_id]
        assert (rev.number, rev.insight_fingerprint) == (
            source.revision,
            source.insight_fingerprint,
        )
        assert cue.show_from == record.header.as_of
        assert cue.audience is Audience.FAN


def test_every_moment_is_shown_once_when_its_snapshot_closes() -> None:
    ref = reference()
    events = ref.log.events()
    last = ref.state.snapshots[-1].header.watermark
    expected = [m.event_id for m in extract_moments(in_progress().info, events[:last])]
    moments = [c for c in timeline().cues if isinstance(c.source, MomentSource)]
    assert [eid for c in moments for eid in c.source.event_ids] == expected
    index = {e.event_id: i for i, e in enumerate(events)}
    for cue in moments:
        source = cue.source
        assert isinstance(source, MomentSource)
        first = next(
            r for r in ref.state.snapshots if index[source.event_ids[0]] < r.header.watermark
        )
        assert source.snapshot_id == first.header.snapshot_id
        assert cue.show_from == first.header.as_of


def _first_snapshot_with_a_card() -> SnapshotRecord:
    tl, snapshots = timeline(), reference().state.snapshots
    return next(
        r
        for i, r in enumerate(snapshots[1:], 1)
        if cards(on_screen(tl, snapshots[i - 1].header.as_of))
    )


def test_only_material_changes_other_than_reanchoring_interrupt() -> None:
    assert not interrupts(())
    assert not interrupts((ChangeKind.REANCHORED,))
    assert not interrupts((ChangeKind.EVIDENCE_CHANGED,))
    assert not interrupts((ChangeKind.REANCHORED, ChangeKind.EVIDENCE_CHANGED))
    assert interrupts((ChangeKind.REANCHORED, ChangeKind.VERDICT_CHANGED))
    assert interrupts((ChangeKind.INTEGRITY_CHANGED,))


def test_a_shown_card_is_revised_with_the_lifecycle_notice() -> None:
    target = _first_snapshot_with_a_card()
    run = replay(
        in_progress().info, in_progress().events, tampered_on(target.header.watermark, evaluator())
    )
    state = run.state
    out = compile_timeline(in_progress().info, run.log.events(), state, FAN)
    revisions = [c for c in out.cues if c.kind is CueKind.REVISION]
    assert revisions
    for cue in revisions:
        source = cue.source
        assert isinstance(source, InsightSource)
        loud = interrupts(source.change_kinds)
        assert cue.interrupt is loud
        assert (cue.sections[0].kind == "notice") is loud
        rev = state.storyline(source.storyline_id).revisions[source.revision - 1]
        assert rev.insight_fingerprint == source.insight_fingerprint
        if loud:
            assert cue.sections[0].text == notice_text(state, rev)
    assert any(c.interrupt for c in revisions)


def test_cards_never_exceed_the_limit_and_retractions_end_a_shown_card() -> None:
    tl = timeline()
    by_id = {c.cue_id: c for c in tl.cues}
    for record in reference().state.snapshots:
        assert len(cards(on_screen(tl, record.header.as_of))) <= MAX_ON_SCREEN
    superseded: set[str] = set()
    for cue in tl.cues:
        for target in cue.supersedes:
            assert target not in superseded
            superseded.add(target)
        if isinstance(cue.source, RetractionSource):
            assert by_id[cue.source.retracts].kind in (CueKind.INSIGHT, CueKind.REVISION)
            assert cue.sections[0].text == RETRACTION_TEXT[cue.source.reason]


def test_a_failed_snapshot_retracts_every_card_and_reports_unavailable() -> None:
    tl = timeline()
    state = reference().state
    target = _first_snapshot_with_a_card()
    before = state.snapshots[state.snapshots.index(target) - 1].header.as_of
    shown = {c.cue_id for c in cards(on_screen(tl, before))}
    failing = failing_on(target.header.watermark, evaluator())
    run = replay(in_progress().info, in_progress().events, failing)
    out = compile_timeline(in_progress().info, run.log.events(), run.state, FAN)
    at = [c for c in out.cues if c.snapshot_id == target.header.snapshot_id]
    retracted = {
        c.source.retracts
        for c in at
        if isinstance(c.source, RetractionSource)
        and c.source.reason is RetractionReason.UNAVAILABLE
    }
    assert retracted == shown
    status = [c.source for c in at if isinstance(c.source, StatusSource)]
    assert [s.status for s in status] == [BroadcastStatus.UNAVAILABLE]
    assert status[0].detail is not None and status[0].detail.startswith("failed")
    assert not cards(on_screen(out, target.header.as_of))
    after = [c.source for c in out.cues if isinstance(c.source, StatusSource)]
    assert after[-1].status is not BroadcastStatus.UNAVAILABLE


def test_data_incomplete_is_reported_while_a_gap_is_open() -> None:
    ref = reference()
    compiler = CueCompiler(in_progress().info, FAN)
    compiler.advance(ref.state, ref.log.events(), DataStatus.DATA_INCOMPLETE)
    gap = compiler.cues[-1].source
    assert isinstance(gap, StatusSource) and gap.status is BroadcastStatus.DATA_INCOMPLETE
    resumed = compiler.advance(ref.state, ref.log.events())
    assert len(resumed) == 1 and resumed[0].supersedes == (compiler.cues[-2].cue_id,)
    final = [c.source for c in timeline().cues if isinstance(c.source, StatusSource)][-1]
    back = compiler.cues[-1].source
    assert isinstance(back, StatusSource) and (back.status, back.detail) == (final.status, None)
    assert compiler.advance(ref.state, ref.log.events()) == ()


def test_the_compiler_refuses_foreign_or_altered_logs() -> None:
    ref = reference()
    info, events = in_progress().info, list(ref.log.events())
    with pytest.raises(ValueError, match="another match"):
        CueCompiler(info, FAN).advance(ref.state.model_copy(update={"match_id": "x"}), events)
    with pytest.raises(ValueError, match="shorter"):
        CueCompiler(info, FAN).advance(ref.state, events[:10])
    events[5] = events[5].model_copy(update={"clock_ms": events[5].clock_ms + 1})
    with pytest.raises(ValueError, match="not the log"):
        CueCompiler(info, FAN).advance(ref.state, events)
    with pytest.raises(ValueError, match="fans and broadcasters"):
        CueCompiler(info, PersonalizationProfile(audience=Audience.ANALYST))


def test_surfaces_sharing_workspaces_compile_the_same_cues() -> None:
    ref = reference()
    shared: WorkspaceCache = {}
    info, events = in_progress().info, ref.log.events()
    for profile in (FAN, BROADCASTER):
        assert compile_timeline(info, events, ref.state, profile, workspaces=shared) == timeline(
            profile
        )
    assert shared
    assert {c.audience for c in timeline(BROADCASTER).cues} <= {None, Audience.BROADCASTER}
