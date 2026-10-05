"""Observable workload proxies: how long a team's outfield players have been on the pitch, and
how much defensive and on-ball activity the team has recently produced per outfield player.

These are *observable workload proxies*, not fatigue. Event data records actions, not distance,
speed, heart rate or recovery, so physical condition cannot be measured here. The proxies are
reported as context beside candidate evidence; they never change a candidate's evidence level
because no defensible dose-response between them and any metric can be derived from event data.
Accepted and rejected proxies: docs/contextual-evidence.md#workload.

Granularity is one minute bin. A substitute counts from the bin after the substitution and the
replaced player for the whole substitution bin, so minutes on the pitch can be off by one.
"""

from collections.abc import Sequence

from pydantic import Field

from matcheyes.analytics.metrics import ON_PITCH
from matcheyes.analytics.summary import DISMISSALS
from matcheyes.analytics.timeline import Timeline
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier, MatchInfo, Position
from matcheyes.domain.events import Card, MatchEvent, Pressure, Substitution

RECENT_WINDOW_BINS = 15
"""Recent activity is summed over the same 15-minute length as the Stage 2 after-window."""


class WorkloadSnapshot(DomainModel):
    """One team's observable workload at the start of a minute bin."""

    team_id: Identifier
    bin_index: int = Field(ge=0)
    outfield_players: int = Field(ge=0)
    mean_outfield_minutes: float = Field(ge=0, description="Mean minutes on the pitch so far.")
    recent_minutes: int = Field(ge=0, description="Length of the recent window actually used.")
    recent_pressures_per_player: float = Field(ge=0)
    recent_actions_per_player: float = Field(
        ge=0, description="On-pitch actions (on-ball, pressures, defensive actions) per player."
    )


def build_workload(
    events: Sequence[MatchEvent],
    timeline: Timeline,
    info: MatchInfo,
    window_bins: int = RECENT_WINDOW_BINS,
) -> dict[Identifier, tuple[WorkloadSnapshot, ...]]:
    n = len(timeline)
    positions = {p.player_id: p.position for s in (info.home, info.away) for p in s.squad}
    per_bin: list[list[MatchEvent]] = [[] for _ in range(n)]
    for event in events:
        per_bin[timeline.index_of_event(event)].append(event)

    result: dict[Identifier, list[WorkloadSnapshot]] = {}
    for team in info.team_ids:
        on_pitch = [p.player_id for p in info.sheet(team).starting_xi]
        minutes: dict[Identifier, int] = dict.fromkeys(on_pitch, 0)
        pressures = [0] * n
        actions = [0] * n
        snapshots: list[WorkloadSnapshot] = []
        for b in range(n):
            outfield = [p for p in on_pitch if positions[p] is not Position.GK]
            lo = max(0, b - window_bins)
            count = len(outfield)
            snapshots.append(
                WorkloadSnapshot(
                    team_id=team,
                    bin_index=b,
                    outfield_players=count,
                    mean_outfield_minutes=sum(minutes[p] for p in outfield) / count if count else 0,
                    recent_minutes=b - lo,
                    recent_pressures_per_player=sum(pressures[lo:b]) / count if count else 0,
                    recent_actions_per_player=sum(actions[lo:b]) / count if count else 0,
                )
            )
            for p in on_pitch:
                minutes[p] += 1
            for event in per_bin[b]:
                if isinstance(event, ON_PITCH) and event.team_id == team:
                    actions[b] += 1
                    pressures[b] += isinstance(event, Pressure)
                elif isinstance(event, Substitution) and event.team_id == team:
                    on_pitch.remove(event.player_id)
                    on_pitch.append(event.replacement_id)
                    minutes.setdefault(event.replacement_id, 0)
                elif (
                    isinstance(event, Card)
                    and event.card in DISMISSALS
                    and event.team_id == team
                    and event.player_id in on_pitch
                ):
                    on_pitch.remove(event.player_id)
        result[team] = snapshots
    return {team: tuple(s) for team, s in result.items()}
