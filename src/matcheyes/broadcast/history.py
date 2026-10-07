"""Read the lifecycle record as it stood on each canonical snapshot.

The lifecycle is append-only, so the record of any earlier snapshot is still in the latest state:
on an evaluated snapshot the current revisions are the latest revisions, at or before that
snapshot, of exactly the storylines established on it (`SnapshotRecord.storyline_ids`); every
other open storyline was withdrawn there. This reads that record; it decides nothing. The Stage 8
evaluation checks it against `lifecycle.feed.current_revisions` of the state after each snapshot.
"""

from matcheyes.lifecycle.contracts import (
    LifecycleState,
    Revision,
    SnapshotOutcome,
    SnapshotRecord,
    StorylineState,
)


def revision_at(state: LifecycleState, storyline_id: str, watermark: int) -> Revision | None:
    s = state.storyline(storyline_id)
    return next((r for r in reversed(s.revisions) if r.watermark <= watermark), None)


def current_at(state: LifecycleState, record: SnapshotRecord) -> tuple[Revision, ...]:
    """The current revisions on `record`'s snapshot: none unless it was evaluated."""
    if record.outcome is not SnapshotOutcome.EVALUATED:
        return ()
    current = []
    for sid in record.storyline_ids:
        r = revision_at(state, sid, record.header.watermark)
        if r is None or r.state is not StorylineState.OPEN:
            raise ValueError(f"{sid} is established on {record.header.snapshot_id} but not open")
        current.append(r)
    return tuple(current)


def established_on(state: LifecycleState, record: SnapshotRecord) -> tuple[Revision, ...]:
    """Revisions recorded on `record`'s snapshot (new, revised, reinstated or withdrawn)."""
    sid = record.header.snapshot_id
    return tuple(r for s in state.storylines for r in s.revisions if r.snapshot_id == sid)
