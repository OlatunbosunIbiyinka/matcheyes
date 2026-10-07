"""Reading the lifecycle record per snapshot agrees with the lifecycle's own current revisions."""

import pytest

from matcheyes.broadcast.history import current_at, established_on, revision_at
from matcheyes.lifecycle.contracts import StorylineState
from matcheyes.lifecycle.feed import current_revisions
from tests.broadcast.support import states_by_snapshot
from tests.lifecycle.support import reference


def test_current_at_matches_current_revisions_after_every_snapshot() -> None:
    final = reference().state
    for state in states_by_snapshot():
        record = final.snapshots[len(state.snapshots) - 1]
        assert state.snapshots[-1] == record
        assert current_at(final, record) == current_revisions(state)


def test_revisions_on_a_snapshot_are_those_it_recorded() -> None:
    state = reference().state
    recorded = [r for record in state.snapshots for r in established_on(state, record)]
    assert sorted(recorded, key=lambda r: (r.storyline_id, r.number)) == sorted(
        (r for s in state.storylines for r in s.revisions),
        key=lambda r: (r.storyline_id, r.number),
    )


def test_revision_at_takes_the_latest_revision_not_after_the_watermark() -> None:
    state = reference().state
    for s in state.storylines:
        first = s.revisions[0]
        assert revision_at(state, s.storyline_id, first.watermark - 1) is None
        assert revision_at(state, s.storyline_id, first.watermark) == first
        assert revision_at(state, s.storyline_id, 10**9) == s.revisions[-1]


def test_a_record_naming_a_withdrawn_storyline_is_refused() -> None:
    state = reference().state
    withdrawn = next(
        s
        for s in state.storylines
        for r in s.revisions
        if r.state is StorylineState.WITHDRAWN and r is s.revisions[-1]
    )
    record = state.snapshots[-1]
    bad = record.model_copy(update={"storyline_ids": (withdrawn.storyline_id,)})
    with pytest.raises(ValueError, match="not open"):
        current_at(state, bad)
