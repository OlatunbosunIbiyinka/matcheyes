"""Snapshot evaluation: the unchanged pipeline on the snapshot's own events, or nothing."""

from matcheyes.agents.contracts import EvidenceItem, EvidenceRequest
from matcheyes.agents.tools import ToolBox
from matcheyes.lifecycle.contracts import SnapshotOutcome
from matcheyes.lifecycle.evaluate import evaluate_snapshot
from matcheyes.lifecycle.snapshot import Snapshot, build_snapshot, prefix_digest, snapshot_schedule
from matcheyes.orchestration.investigation import investigate_match
from matcheyes.personalization.contracts import fingerprint
from tests.lifecycle.support import in_progress


def _latest() -> Snapshot:
    match = in_progress()
    entry = snapshot_schedule(match.events)[-1]
    return build_snapshot(
        match.info, match.events, entry, prefix_digest(match.events[: entry.watermark])
    )


class _Down(ToolBox):
    def run(self, request: EvidenceRequest, evidence_id: str) -> EvidenceItem:
        raise RuntimeError("backend down")


def test_evaluation_is_the_batch_pipeline_on_the_snapshot() -> None:
    snap = _latest()
    result = evaluate_snapshot(snap)
    batch = investigate_match(snap.match)
    assert result.outcome is SnapshotOutcome.EVALUATED and result.insights
    assert [fingerprint(i.final) for i in result.insights] == [
        fingerprint(r.final) for r in batch.records
    ]
    assert result.suppression_bins == 15
    for insight in result.insights:
        assert insight.final.candidate_id.endswith(f"-{insight.anchor_bin}")
        assert insight.audit_findings == ()


def test_evaluation_sees_only_the_snapshot_events() -> None:
    snap = _latest()
    known = {e.event_id for e in snap.match.events}
    for insight in evaluate_snapshot(snap).insights:
        cited = set(insight.final.event_ids) | {
            i for e in insight.final.evidence for i in e.event_ids
        }
        assert cited <= known


def test_evaluation_carries_no_trace_or_latency() -> None:
    assert "latency" not in evaluate_snapshot(_latest()).model_dump_json()


def test_a_pipeline_failure_is_failed_with_nothing_established() -> None:
    result = evaluate_snapshot(_latest(), toolbox=_Down)
    assert (result.outcome, result.failure, result.insights) == (
        SnapshotOutcome.FAILED,
        "RuntimeError",
        (),
    )


def test_an_invalid_prefix_is_not_evaluated() -> None:
    snap = _latest()
    events = list(snap.match.events)
    events[5] = events[5].model_copy(update={"clock_ms": 10**8})
    bad = Snapshot(snap.header, snap.match.model_copy(update={"events": tuple(events)}))
    result = evaluate_snapshot(bad)
    assert result.outcome is SnapshotOutcome.INVALID
    assert result.failure is not None and "clock_not_monotonic" in result.failure
    assert result.insights == ()
