"""Investigation traces: one record per step, for observability and audit.

Records carry identifiers, tool names and validated tool arguments, result references, statuses
and latencies. They never carry prompts, model output text, credentials or anything outside the
observable match, so a trace can be stored or shown without leaking secrets.
"""

import time
from collections.abc import Callable
from typing import Literal

from pydantic import Field

from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier

Component = Literal[
    "match_analyst",
    "investigator",
    "challenger",
    "toolbox",
    "verifier",
    "narrative",
    "orchestrator",
]
TraceStatus = Literal["ok", "retry", "failed", "skipped", "downgraded"]


class TraceRecord(DomainModel):
    investigation_id: Identifier
    candidate_id: Identifier
    sequence: int = Field(ge=1)
    component: Component
    action: str = Field(max_length=60)
    status: TraceStatus
    hypothesis: str | None = None
    tool: str | None = None
    inputs: dict[str, int | float | str] = Field(default_factory=dict)
    result_ref: Identifier | None = None
    detail: str = Field(default="", max_length=600)
    latency_ms: float = Field(ge=0)


class Tracer:
    def __init__(
        self,
        investigation_id: Identifier,
        candidate_id: Identifier,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.investigation_id = investigation_id
        self.candidate_id = candidate_id
        self.clock = clock
        self.records: list[TraceRecord] = []

    def record(
        self,
        component: Component,
        action: str,
        status: TraceStatus,
        latency_ms: float = 0.0,
        *,
        hypothesis: str | None = None,
        tool: str | None = None,
        inputs: dict[str, int | float | str] | None = None,
        result_ref: Identifier | None = None,
        detail: str = "",
    ) -> None:
        self.records.append(
            TraceRecord(
                investigation_id=self.investigation_id,
                candidate_id=self.candidate_id,
                sequence=len(self.records) + 1,
                component=component,
                action=action,
                status=status,
                hypothesis=hypothesis,
                tool=tool,
                inputs=inputs or {},
                result_ref=result_ref,
                detail=detail[:600],
                latency_ms=max(0.0, latency_ms),
            )
        )
