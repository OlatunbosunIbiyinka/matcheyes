"""Model-backed lifecycles: record a real model over every snapshot once, replay it anywhere.

A live model inside every snapshot of a public replay is impractical (hundreds of investigations
per match, each request distinct) and would put the model behind public traffic. Instead:

1. `record_lifecycle` evaluates every snapshot the engine would evaluate, in parallel, with the
   live model wrapped in a `RecordingModel`. Snapshots are independent (each is evaluated from
   scratch on its own prefix), so the order of evaluation cannot change any result.
2. The transcript is written once, content-addressed (agents/recorded.py).
3. `recorded_evaluator` replays it: `evaluate_snapshot` with a `RecordedModel`, which has no
   endpoint and no credentials. The engine, reconciliation and broadcast are unchanged.

`MODEL_CONFIG` is the investigation configuration of every model-backed run, recorded or replayed.
Its only difference from the reference default is the deadline: a hosted model takes tens of
seconds per call, and a deadline that fired while recording could not fire on replay, so the
replay would differ from what was recorded.
"""

import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from matcheyes.agents.reasoning import ReasoningModel
from matcheyes.agents.recorded import RecordedModel, RecordingModel
from matcheyes.agents.split import with_roles
from matcheyes.domain.events import MatchEvent
from matcheyes.domain.match import ObservableMatch
from matcheyes.lifecycle.contracts import LifecycleState, SnapshotOutcome
from matcheyes.lifecycle.engine import replay
from matcheyes.lifecycle.evaluate import Evaluator, SnapshotEvaluation, evaluate_snapshot
from matcheyes.lifecycle.snapshot import Snapshot
from matcheyes.orchestration.investigation import InvestigationConfig

MODEL_CONFIG = InvestigationConfig(deadline_s=3600.0)


def ordered_events(match: ObservableMatch) -> tuple[MatchEvent, ...]:
    return tuple(sorted(match.events, key=lambda e: e.sequence))


def match_snapshots(match: ObservableMatch) -> list[Snapshot]:
    """Every snapshot a replay of `match` in sequence order evaluates, in schedule order."""
    seen: list[Snapshot] = []

    def collect(snapshot: Snapshot) -> SnapshotEvaluation:
        seen.append(snapshot)
        return SnapshotEvaluation(header=snapshot.header, outcome=SnapshotOutcome.FAILED)

    replay(match.info, ordered_events(match), collect)
    return seen


def record_lifecycle(
    match: ObservableMatch,
    model: ReasoningModel,
    workers: int = 8,
    progress: Callable[[int, int], None] | None = None,
    roles: str = "model",
) -> tuple[RecordingModel, dict[str, SnapshotEvaluation]]:
    """Evaluates every snapshot with `model` in `roles`, recording each distinct request to it
    once (the reference reasoner's answers in the other roles are recomputed, not recorded)."""
    snapshots = match_snapshots(match)
    recorder = RecordingModel(model)
    reasoner = with_roles(roles, recorder)
    done = 0
    lock = threading.Lock()

    def evaluate(snapshot: Snapshot) -> SnapshotEvaluation:
        nonlocal done
        result = evaluate_snapshot(snapshot, reasoner, MODEL_CONFIG)
        with lock:
            done += 1
            if progress is not None:
                progress(done, len(snapshots))
        return result

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(evaluate, snapshots))
    return recorder, {r.header.snapshot_id: r for r in results}


def recorded_reasoner(model: RecordedModel) -> ReasoningModel:
    """The recorded model in the roles its transcript was recorded in."""
    return with_roles(model.metadata.get("roles", "model"), model)


def recorded_evaluator(model: RecordedModel) -> Evaluator:
    """Snapshot evaluation that answers every model request from a transcript."""
    return partial(evaluate_snapshot, model=recorded_reasoner(model), config=MODEL_CONFIG)


def lifecycle_from(
    match: ObservableMatch, evaluations: dict[str, SnapshotEvaluation]
) -> LifecycleState:
    """The lifecycle state reconciled from already-computed snapshot evaluations."""

    def lookup(snapshot: Snapshot) -> SnapshotEvaluation:
        return evaluations[snapshot.header.snapshot_id]

    return replay(match.info, ordered_events(match), lookup).state


def replayed_lifecycle(match: ObservableMatch, model: RecordedModel) -> LifecycleState:
    return replay(match.info, ordered_events(match), recorded_evaluator(model)).state


def unavailable_insights(evaluations: Sequence[SnapshotEvaluation]) -> int:
    return sum(i.final.verdict.value == "unavailable" for e in evaluations for i in e.insights)
