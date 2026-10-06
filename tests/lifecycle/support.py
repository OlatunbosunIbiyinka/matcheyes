"""Shared fixtures for lifecycle tests.

`in_progress()` is a real match 40 minutes in (events delivered so far, the first half not yet
over): fast to replay, and its storylines already re-anchor, change verdict and are withdrawn.
Scripted evaluations reuse genuine pipeline insights from it, so reconciliation is tested on
the truth it actually receives.
"""

from collections.abc import Sequence
from functools import cache

from matcheyes.domain.events import MatchEvent
from matcheyes.domain.match import ObservableMatch
from matcheyes.domain.time import MatchInstant
from matcheyes.lifecycle.audit import LifecycleAuditor
from matcheyes.lifecycle.contracts import Direction, SnapshotHeader, SnapshotOutcome
from matcheyes.lifecycle.engine import LifecycleEngine, replay
from matcheyes.lifecycle.evaluate import InsightOutcome, SnapshotEvaluation
from matcheyes.personalization.contracts import fingerprint
from matcheyes_eval.stage7 import CachedEvaluator
from tests.synth.generated import generated

SCENARIO = "S03_game_state_deep_block"
MINUTES = 40
DIGEST = "a" * 64


@cache
def in_progress() -> ObservableMatch:
    match = generated(SCENARIO).observable
    events = tuple(e for e in match.events if e.period == 1 and e.clock_ms < MINUTES * 60_000)
    return match.model_copy(update={"events": events})


@cache
def evaluator() -> CachedEvaluator:
    return CachedEvaluator()


@cache
def reference() -> LifecycleEngine:
    match = in_progress()
    return replay(match.info, match.events, evaluator())


@cache
def auditor() -> LifecycleAuditor:
    """Independent auditor of the reference run (cached evaluations, cached workspaces)."""
    match = in_progress()
    return LifecycleAuditor(match.info, reference().log.events(), evaluator())


def engine_for(events: Sequence[MatchEvent]) -> LifecycleEngine:
    return replay(in_progress().info, events, evaluator())


@cache
def insights() -> tuple[InsightOutcome, ...]:
    """Distinct genuine insights from the reference run, in snapshot order."""
    reference()
    seen: dict[str, InsightOutcome] = {}
    for evaluation in evaluator().cache.values():
        for insight in evaluation.insights:
            seen.setdefault(fingerprint(insight.final), insight)
    return tuple(seen.values())


def moved(insight: InsightOutcome, bins: int) -> InsightOutcome:
    """The same phenomenon detected with its onset `bins` later (a new candidate ID)."""
    anchor = insight.anchor_bin + bins
    final = insight.final
    candidate = final.candidate_id.rsplit("-", 1)[0] + f"-{anchor}"
    match_id = in_progress().info.match_id
    final = final.model_copy(
        update={"candidate_id": candidate, "investigation_id": f"inv-{match_id}-{candidate}"}
    )
    return insight.model_copy(update={"final": final, "anchor_bin": anchor})


def reversed_direction(insight: InsightOutcome) -> InsightOutcome:
    flipped: Direction = "down" if insight.direction == "up" else "up"
    return insight.model_copy(update={"direction": flipped})


def header(watermark: int, match_id: str | None = None) -> SnapshotHeader:
    return SnapshotHeader(
        snapshot_id=f"snap-{watermark:05d}-test",
        match_id=match_id or in_progress().info.match_id,
        watermark=watermark,
        period=1,
        minute=watermark,
        as_of=MatchInstant(period=1, clock_ms=(watermark + 1) * 60_000),
        log_digest=DIGEST,
        info_digest=DIGEST,
    )


def evaluated(watermark: int, *outcomes: InsightOutcome) -> SnapshotEvaluation:
    return SnapshotEvaluation(
        header=header(watermark),
        outcome=SnapshotOutcome.EVALUATED,
        suppression_bins=15,
        insights=outcomes,
    )


def failed(watermark: int) -> SnapshotEvaluation:
    return SnapshotEvaluation(
        header=header(watermark), outcome=SnapshotOutcome.FAILED, failure="RuntimeError"
    )
