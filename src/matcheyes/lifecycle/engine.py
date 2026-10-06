"""Lifecycle engine: event log -> contiguous watermark -> canonical snapshots -> reconciliation.

Events may arrive in any order, duplicated or late. The log admits them; whenever the contiguous
watermark advances, every minute that has closed since is snapshotted, evaluated by the existing
pipeline and reconciled, in schedule order. Because snapshots depend only on the contiguous
prefix, the canonical lifecycle state after a set of events is the same whatever order they
arrived in, and `replay` reproduces it byte for byte.

The engine keeps the log and the state in memory only.
"""

from collections.abc import Iterable

from matcheyes.domain.entities import MatchInfo
from matcheyes.domain.events import MatchEvent
from matcheyes.ingestion.log import EventLog, IngestOutcome, LogStatus
from matcheyes.lifecycle.contracts import LifecycleState
from matcheyes.lifecycle.evaluate import Evaluator, evaluate_snapshot
from matcheyes.lifecycle.reconcile import reconcile
from matcheyes.lifecycle.snapshot import ScheduledSnapshot, Scheduler, Snapshot, build_snapshot


class LifecycleEngine:
    def __init__(self, info: MatchInfo, evaluator: Evaluator = evaluate_snapshot) -> None:
        self.info = info
        self.evaluator = evaluator
        self.log = EventLog(info.match_id)
        self.state = LifecycleState.empty(info.match_id)
        self._scheduler = Scheduler()
        self._latest: ScheduledSnapshot | None = None

    def ingest(self, event: MatchEvent) -> IngestOutcome:
        outcome = self.log.append(event)
        if outcome is IngestOutcome.ACCEPTED:
            self._advance()
        return outcome

    def ingest_json(self, line: str) -> IngestOutcome:
        outcome = self.log.append_json(line)
        if outcome is IngestOutcome.ACCEPTED:
            self._advance()
        return outcome

    def _advance(self) -> None:
        events = self.log.events()
        due = [
            entry
            for event in events[self._scheduler.seen :]
            for entry in self._scheduler.feed(event)
        ]
        for entry in due:
            snapshot = build_snapshot(self.info, events, entry, self.log.digest(entry.watermark))
            self.state = reconcile(self.state, self.evaluator(snapshot))
            self._latest = entry

    def status(self) -> LogStatus:
        return self.log.status()

    def latest_snapshot(self) -> Snapshot | None:
        """The snapshot the current state was last reconciled on, rebuilt from the log."""
        if self._latest is None:
            return None
        entry = self._latest
        return build_snapshot(self.info, self.log.events(), entry, self.log.digest(entry.watermark))


def replay(
    info: MatchInfo, events: Iterable[MatchEvent], evaluator: Evaluator = evaluate_snapshot
) -> LifecycleEngine:
    """Feed `events` in the given order to a fresh engine."""
    engine = LifecycleEngine(info, evaluator)
    for event in events:
        engine.ingest(event)
    return engine
