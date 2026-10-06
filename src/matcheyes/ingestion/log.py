"""Event log for a match in progress: idempotent, conflict-rejecting, with a contiguous watermark.

Events are identified by (match_id, event_id) and ordered by the producer's `sequence`. The log:

* accepts a new event;
* ignores an exact duplicate (same event_id, byte-identical canonical payload);
* rejects as a conflict a different payload under an existing event_id, or a different event
  under an existing sequence number - nothing is ever overwritten;
* rejects an event of another match, or a line that is not a valid event.

Conflict handling is a temporary Stage 7 ingestion policy ("first writer wins"): corrections are
intentionally deferred, conflicting payloads are rejected, and canonical state stays based on the
first accepted version. That version is not thereby assumed to be correct. A future correction
mechanism must rebuild every snapshot, evaluation and revision derived from a corrected prefix.

The watermark is the highest sequence up to which every event has arrived. Events beyond a gap
are kept but are not part of the contiguous prefix, and the log reports DATA_INCOMPLETE until the
gap is filled. The status is reported immediately: nothing waits for a missing event.

Every prefix has a digest chained over canonical event JSON, so identical prefixes have identical
digests whatever order the events arrived in.
"""

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from pydantic import ValidationError

from matcheyes.domain.entities import Identifier
from matcheyes.domain.events import MatchEvent
from matcheyes.domain.match import EVENT_ADAPTER

EMPTY_PREFIX_DIGEST = "0" * 64


class IngestOutcome(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    REJECTED = "rejected"


class DataStatus(StrEnum):
    """Availability of the received data, independent of any insight."""

    CONTIGUOUS = "contiguous"
    DATA_INCOMPLETE = "data_incomplete"


@dataclass(frozen=True)
class LogStatus:
    match_id: Identifier
    watermark: int
    highest_sequence: int
    buffered: int
    data_status: DataStatus
    accepted: int
    duplicates: int
    conflicts: int
    rejected: int

    @property
    def missing_from(self) -> int | None:
        """First missing sequence when data is incomplete."""
        return self.watermark + 1 if self.data_status is DataStatus.DATA_INCOMPLETE else None


def chain_digest(previous: str, payload: bytes) -> str:
    return hashlib.sha256(previous.encode("ascii") + b"\n" + payload).hexdigest()


class EventLog:
    def __init__(self, match_id: Identifier) -> None:
        self.match_id = match_id
        self._payloads: dict[Identifier, bytes] = {}
        self._by_sequence: dict[int, MatchEvent] = {}
        self._prefix: list[MatchEvent] = []
        self._digests: list[str] = [EMPTY_PREFIX_DIGEST]
        self._counts = dict.fromkeys(IngestOutcome, 0)

    def append(self, event: MatchEvent) -> IngestOutcome:
        outcome = self._admit(event)
        self._counts[outcome] += 1
        return outcome

    def append_json(self, line: str) -> IngestOutcome:
        try:
            event = EVENT_ADAPTER.validate_json(line)
        except ValidationError:
            self._counts[IngestOutcome.REJECTED] += 1
            return IngestOutcome.REJECTED
        return self.append(event)

    def _admit(self, event: MatchEvent) -> IngestOutcome:
        if event.match_id != self.match_id:
            return IngestOutcome.REJECTED
        payload = EVENT_ADAPTER.dump_json(event)
        prior = self._payloads.get(event.event_id)
        if prior is not None:
            return IngestOutcome.DUPLICATE if prior == payload else IngestOutcome.CONFLICT
        if event.sequence in self._by_sequence:
            return IngestOutcome.CONFLICT
        self._payloads[event.event_id] = payload
        self._by_sequence[event.sequence] = event
        while (nxt := self._by_sequence.get(len(self._prefix) + 1)) is not None:
            self._prefix.append(nxt)
            self._digests.append(chain_digest(self._digests[-1], EVENT_ADAPTER.dump_json(nxt)))
        return IngestOutcome.ACCEPTED

    @property
    def watermark(self) -> int:
        return len(self._prefix)

    def events(self) -> tuple[MatchEvent, ...]:
        """The contiguous prefix, in sequence order."""
        return tuple(self._prefix)

    def digest(self, watermark: int) -> str:
        """Digest of the contiguous prefix ending at `watermark`."""
        if not 0 <= watermark <= self.watermark:
            raise ValueError(f"watermark {watermark} is beyond the contiguous prefix")
        return self._digests[watermark]

    def status(self) -> LogStatus:
        buffered = len(self._by_sequence) - self.watermark
        return LogStatus(
            match_id=self.match_id,
            watermark=self.watermark,
            highest_sequence=max(self._by_sequence, default=0),
            buffered=buffered,
            data_status=DataStatus.DATA_INCOMPLETE if buffered else DataStatus.CONTIGUOUS,
            accepted=self._counts[IngestOutcome.ACCEPTED],
            duplicates=self._counts[IngestOutcome.DUPLICATE],
            conflicts=self._counts[IngestOutcome.CONFLICT],
            rejected=self._counts[IngestOutcome.REJECTED],
        )
