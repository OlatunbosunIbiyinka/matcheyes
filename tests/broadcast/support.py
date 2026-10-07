"""Shared broadcast fixtures over the lifecycle reference run (a real match, 40 minutes in)."""

from collections.abc import Callable, Iterator
from functools import cache

from matcheyes.broadcast.compiler import compile_timeline
from matcheyes.broadcast.contracts import Cue, CueKind, CueTimeline
from matcheyes.domain.time import MatchInstant
from matcheyes.lifecycle.contracts import LifecycleState
from matcheyes.lifecycle.engine import LifecycleEngine
from matcheyes.lifecycle.evaluate import SnapshotEvaluation
from matcheyes.lifecycle.snapshot import Snapshot
from matcheyes.personalization.contracts import Audience, PersonalizationProfile
from tests.lifecycle.support import evaluator, in_progress, reference

FAN = PersonalizationProfile(audience=Audience.FAN)
BROADCASTER = PersonalizationProfile(audience=Audience.BROADCASTER)


@cache
def timeline(profile: PersonalizationProfile = FAN) -> CueTimeline:
    ref = reference()
    return compile_timeline(in_progress().info, ref.log.events(), ref.state, profile)


def states_by_snapshot(
    evaluate: Callable[[Snapshot], SnapshotEvaluation] | None = None,
) -> Iterator[LifecycleState]:
    """The lifecycle state right after each canonical snapshot, by incremental ingestion."""
    match = in_progress()
    engine = LifecycleEngine(match.info, evaluate or evaluator())
    seen = 0
    for event in match.events:
        engine.ingest(event)
        if len(engine.state.snapshots) > seen:
            seen = len(engine.state.snapshots)
            yield engine.state


def on_screen(tl: CueTimeline, at: MatchInstant) -> list[Cue]:
    """The cues a surface shows at `at`, by the contract's display semantics."""
    superseded = {s for c in tl.cues if c.show_from.sort_key <= at.sort_key for s in c.supersedes}
    return [
        c
        for c in tl.cues
        if c.show_from.sort_key <= at.sort_key
        and c.cue_id not in superseded
        and (c.expires_at is None or at.sort_key < c.expires_at.sort_key)
    ]


def cards(cues: list[Cue]) -> list[Cue]:
    return [c for c in cues if c.kind in (CueKind.INSIGHT, CueKind.REVISION)]
