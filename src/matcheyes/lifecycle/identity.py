"""Storyline identity: is this snapshot's insight the same phenomenon as an earlier one?

A storyline is keyed by (match, team, metric, direction) and anchored at a timeline bin. Stage 3
candidate IDs embed their onset bin, and Stage 2 non-maximum suppression can move a change's
onset as more play arrives, so one phenomenon can carry different candidate IDs over time. An
insight therefore continues a storyline with the same key whose current anchor is within the
detector's own `suppression_bins` - the distance inside which Stage 2 itself treats two changes
as one. Ties go to the storyline whose latest candidate ID is identical, then the nearest anchor,
then the earliest created. Withdrawn storylines take part, so a returning phenomenon is
reinstated under its original ID.

Storyline IDs are derived from the key and the first anchor only; no input can supply one.
"""

from collections.abc import Collection, Sequence

from matcheyes.domain.entities import Identifier
from matcheyes.lifecycle.contracts import Direction, Storyline, StorylineState
from matcheyes.lifecycle.evaluate import InsightOutcome


def storyline_id(
    match_id: Identifier,
    team_id: Identifier,
    metric: str,
    direction: Direction,
    first_anchor: int,
    taken: Collection[Identifier] = (),
) -> Identifier:
    """Deterministic ID; a numeric suffix separates a later storyline with the same first anchor."""
    base = f"sl-{match_id}-{team_id}-{metric}-{direction}-{first_anchor}"
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _key(insight: InsightOutcome) -> tuple[Identifier, str, str]:
    return (insight.final.team_id, insight.metric, insight.direction)


def match_storylines(
    storylines: Sequence[Storyline], insights: Sequence[InsightOutcome], suppression_bins: int
) -> dict[int, Identifier]:
    """Insight index -> storyline it continues. One-to-one; unmatched insights are absent."""
    pairs = []
    for index, insight in enumerate(insights):
        for s in storylines:
            if (s.team_id, s.metric, s.direction) != _key(insight):
                continue
            distance = abs(s.anchor - insight.anchor_bin)
            if distance > suppression_bins:
                continue
            same = s.latest_insight.candidate_id == insight.final.candidate_id
            pairs.append((0 if same else 1, distance, s.ordinal, index, s.storyline_id))
    pairs.sort()
    assigned: dict[int, Identifier] = {}
    used: set[Identifier] = set()
    for *_, index, sid in pairs:
        if index not in assigned and sid not in used:
            assigned[index] = sid
            used.add(sid)
    return assigned


def opposite_storyline(
    storylines: Sequence[Storyline],
    insight: InsightOutcome,
    suppression_bins: int,
    excluded: Collection[Identifier],
) -> Storyline | None:
    """The open storyline an opposite-direction change at the same place replaces, if any."""
    team, metric, direction = _key(insight)
    rivals = [
        s
        for s in storylines
        if s.state is StorylineState.OPEN
        and s.storyline_id not in excluded
        and (s.team_id, s.metric) == (team, metric)
        and s.direction != direction
        and abs(s.anchor - insight.anchor_bin) <= suppression_bins
    ]
    rivals.sort(key=lambda s: (abs(s.anchor - insight.anchor_bin), s.ordinal))
    return rivals[0] if rivals else None
