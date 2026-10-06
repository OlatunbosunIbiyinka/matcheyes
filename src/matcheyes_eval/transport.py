"""Simulated delivery of a match's events: what a real feed may do to them on the way in.

Each function returns the sequence in which events arrive at the lifecycle engine. All are
deterministic (seeded) so a run can be repeated exactly. None of them knows anything about the
match beyond its observable events.

* clean            - in sequence order.
* bounded_reorder  - shuffled within consecutive blocks (late by at most a block).
* shuffled         - any order.
* duplicated       - every k-th event delivered again, a little later.
* conflicting      - a changed payload under an existing event ID, after the original.
* reused_sequence  - a different event under an existing sequence number.
* withheld         - one event never arrives (a gap).
* late_fill        - the withheld event arrives after everything else.
* cross_match      - an event of another match is mixed in.
"""

import random
from collections.abc import Sequence

from matcheyes.domain.events import MatchEvent

Delivery = list[MatchEvent]


def clean(events: Sequence[MatchEvent]) -> Delivery:
    return list(events)


def bounded_reorder(events: Sequence[MatchEvent], block: int, seed: int) -> Delivery:
    rng = random.Random(seed)  # noqa: S311 - reproducible delivery order, not security
    out: Delivery = []
    for start in range(0, len(events), block):
        chunk = list(events[start : start + block])
        rng.shuffle(chunk)
        out += chunk
    return out


def shuffled(events: Sequence[MatchEvent], seed: int) -> Delivery:
    out = list(events)
    random.Random(seed).shuffle(out)  # noqa: S311 - reproducible delivery order, not security
    return out


def duplicated(events: Sequence[MatchEvent], every: int, delay: int) -> tuple[Delivery, int]:
    """Every `every`-th event is delivered again `delay` events later. Returns the delivery and
    the number of duplicates in it."""
    out: Delivery = []
    pending: dict[int, list[MatchEvent]] = {}
    count = 0
    for index, event in enumerate(events):
        out.append(event)
        if index % every == 0:
            pending.setdefault(index + delay, []).append(event)
            count += 1
        out += pending.pop(index, [])
    for late in sorted(pending):
        out += pending[late]
    return out, count


def altered(event: MatchEvent) -> MatchEvent:
    """The same event ID with a different payload (a "correction" of the clock)."""
    return event.model_copy(update={"clock_ms": event.clock_ms + 1})


def conflicting(events: Sequence[MatchEvent], index: int, delay: int = 0) -> Delivery:
    """The original, then `delay` events later a changed payload under its event ID."""
    out = list(events)
    out.insert(min(index + 1 + delay, len(out)), altered(events[index]))
    return out


def reused_sequence(events: Sequence[MatchEvent], index: int) -> Delivery:
    impostor = events[index].model_copy(update={"event_id": f"{events[index].event_id}-reused"})
    out = list(events)
    out.insert(index + 1, impostor)
    return out


def withheld(events: Sequence[MatchEvent], index: int) -> Delivery:
    return [e for i, e in enumerate(events) if i != index]


def late_fill(events: Sequence[MatchEvent], index: int) -> Delivery:
    return [*withheld(events, index), events[index]]


def cross_match(events: Sequence[MatchEvent], index: int, other_match_id: str) -> Delivery:
    foreign = events[index].model_copy(
        update={"match_id": other_match_id, "event_id": f"{other_match_id}-x{index:05d}"}
    )
    out = list(events)
    out.insert(index + 1, foreign)
    return out
