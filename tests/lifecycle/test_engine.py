"""The lifecycle engine under realistic delivery: order, duplicates, gaps, conflicts, failures."""

from matcheyes.domain.match import EVENT_ADAPTER
from matcheyes.ingestion.log import DataStatus, IngestOutcome
from matcheyes.lifecycle.contracts import LifecycleState, SnapshotOutcome
from matcheyes.lifecycle.engine import LifecycleEngine, replay
from matcheyes.lifecycle.feed import CurrentStatus, lifecycle_feed
from matcheyes.lifecycle.snapshot import snapshot_schedule
from matcheyes_eval import transport
from matcheyes_eval.stage7 import canonical
from tests.lifecycle.support import engine_for, evaluator, in_progress, reference
from tests.support.builders import minimal_match


def _ref() -> bytes:
    return canonical(reference().state)


def _history_is_prefix(earlier: LifecycleState, later: LifecycleState) -> bool:
    if later.snapshots[: len(earlier.snapshots)] != earlier.snapshots:
        return False
    by_id = {s.storyline_id: s for s in later.storylines}
    return all(
        s.storyline_id in by_id
        and by_id[s.storyline_id].revisions[: len(s.revisions)] == s.revisions
        for s in earlier.storylines
    )


def test_the_reference_run_has_living_storylines() -> None:
    state = reference().state
    events = in_progress().events
    assert len(state.snapshots) == len(snapshot_schedule(events))
    assert all(s.outcome is SnapshotOutcome.EVALUATED for s in state.snapshots)
    assert state.storylines and any(len(s.revisions) > 1 for s in state.storylines)


def test_a_fresh_replay_is_byte_identical() -> None:
    match = in_progress()
    first = replay(match.info, match.events).state
    second = replay(match.info, match.events).state
    assert canonical(first) == canonical(second) == _ref()


def test_arrival_order_does_not_change_the_state() -> None:
    events = in_progress().events
    assert canonical(engine_for(transport.bounded_reorder(events, 30, 3)).state) == _ref()
    assert canonical(engine_for(transport.shuffled(events, 4)).state) == _ref()


def test_duplicates_are_no_ops() -> None:
    delivery, count = transport.duplicated(in_progress().events, every=5, delay=9)
    engine = engine_for(delivery)
    assert canonical(engine.state) == _ref()
    assert engine.status().duplicates == count


def test_conflicts_corrections_and_foreign_events_are_rejected() -> None:
    events = in_progress().events
    for delivery, field in (
        (transport.conflicting(events, 100), "conflicts"),
        (transport.conflicting(events, 100, delay=len(events)), "conflicts"),
        (transport.reused_sequence(events, 100), "conflicts"),
        (transport.cross_match(events, 100, "another-match"), "rejected"),
    ):
        engine = engine_for(delivery)
        assert canonical(engine.state) == _ref()
        assert getattr(engine.status(), field) == 1


def test_a_gap_reports_incomplete_data_and_stops_snapshots_at_the_watermark() -> None:
    events = in_progress().events
    missing = 400
    engine = engine_for(transport.withheld(events, missing))
    status = engine.status()
    assert status.data_status is DataStatus.DATA_INCOMPLETE
    assert (status.watermark, status.missing_from) == (missing, missing + 1)
    assert status.buffered == len(events) - missing - 1
    assert all(s.header.watermark <= missing for s in engine.state.snapshots)
    assert canonical(engine.state) == canonical(engine_for(events[:missing]).state)
    feed = lifecycle_feed(engine.state, status)
    assert feed.data_status is DataStatus.DATA_INCOMPLETE and feed.buffered == status.buffered


def test_a_late_fill_catches_up_to_the_reference() -> None:
    events = in_progress().events
    engine = engine_for(transport.withheld(events, 400))
    assert engine.ingest(events[400]) is IngestOutcome.ACCEPTED
    assert engine.status().data_status is DataStatus.CONTIGUOUS
    assert canonical(engine.state) == _ref()


def test_history_never_depends_on_later_events() -> None:
    events = in_progress().events
    full = reference().state
    for cut in (200, 450, 700, len(events) - 50):
        assert _history_is_prefix(engine_for(events[:cut]).state, full)


def test_json_lines_ingest_like_events() -> None:
    match = in_progress()
    engine = LifecycleEngine(match.info, evaluator())
    for event in match.events:
        engine.ingest_json(EVENT_ADAPTER.dump_json(event).decode())
    assert engine.ingest_json("not an event") is IngestOutcome.REJECTED
    assert canonical(engine.state) == _ref()
    latest = engine.latest_snapshot()
    assert latest is not None and engine.state.last_snapshot is not None
    assert latest.header == engine.state.last_snapshot.header


def test_a_complete_match_without_candidates_has_no_current_insight() -> None:
    match = minimal_match()
    engine = replay(match.info, match.events)
    assert len(engine.state.snapshots) == len(snapshot_schedule(match.events))
    feed = lifecycle_feed(engine.state, engine.status())
    assert feed.status is CurrentStatus.NO_CANDIDATE and not feed.current
    assert LifecycleEngine(match.info).latest_snapshot() is None


def test_invalid_data_makes_current_truth_unavailable() -> None:
    match = minimal_match()
    events = list(match.events)
    events[4] = events[4].model_copy(update={"player_id": "nobody-99"})
    engine = replay(match.info, events)
    outcomes = {s.outcome for s in engine.state.snapshots}
    assert SnapshotOutcome.INVALID in outcomes
    feed = lifecycle_feed(engine.state, engine.status())
    assert feed.status is CurrentStatus.UNAVAILABLE and not feed.current
    assert feed.unavailable_reason is not None and "player_not_on_pitch" in feed.unavailable_reason
