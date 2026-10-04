"""Entry point: observable match in, structured analysis out. Deterministic and pure."""

from pydantic import Field

from matcheyes.analytics.changepoints import detect_shifts
from matcheyes.analytics.evidence import CandidateMoment, Evidence, KeyEventType
from matcheyes.analytics.metrics import METRICS, MetricSpec, Series, compute_series
from matcheyes.analytics.moments import (
    AnalysisConfig,
    Names,
    build_moments,
    involvement_evidence,
    key_event_evidence,
    run_of_play_evidence,
    shift_evidence,
)
from matcheyes.analytics.possessions import Possession, build_possessions
from matcheyes.analytics.summary import (
    PlayerInvolvement,
    TeamSummary,
    player_involvement,
    team_summaries,
)
from matcheyes.analytics.timeline import Timeline
from matcheyes.domain.base import DomainModel
from matcheyes.domain.entities import Identifier
from matcheyes.domain.match import ObservableMatch

ANALYTICS_VERSION = "0.1.0"


class MatchAnalysis(DomainModel):
    analytics_version: str = ANALYTICS_VERSION
    match_id: Identifier
    team_ids: tuple[Identifier, Identifier]
    config: AnalysisConfig
    bin_labels: tuple[str, ...] = Field(description="Display minute of each timeline bin.")
    metrics: tuple[MetricSpec, ...]
    summaries: tuple[TeamSummary, ...]
    players: tuple[PlayerInvolvement, ...]
    possessions: tuple[Possession, ...]
    series: tuple[Series, ...]
    evidence: tuple[Evidence, ...]
    moments: tuple[CandidateMoment, ...]

    def evidence_by_id(self) -> dict[Identifier, Evidence]:
        return {e.evidence_id: e for e in self.evidence}


def analyse_match(match: ObservableMatch, config: AnalysisConfig | None = None) -> MatchAnalysis:
    config = config or AnalysisConfig()
    info, events = match.info, match.events
    timeline = Timeline(events)
    possessions = build_possessions(events)
    series = compute_series(events, possessions, timeline, info.team_ids)
    names = Names(info)

    evidence: list[Evidence] = []
    for key in sorted(series):
        for shift in detect_shifts(series[key], config.detection):
            evidence.append(shift_evidence(shift, series[key], timeline, names))
    for key_event in key_event_evidence(events, info):
        evidence.append(key_event)
        if key_event.event_type is KeyEventType.GOAL:
            evidence.append(
                run_of_play_evidence(key_event, series, timeline, info, config.run_of_play_bins)
            )
        elif key_event.event_type is KeyEventType.SUBSTITUTION:
            evidence.append(
                involvement_evidence(
                    key_event, events, series, timeline, info, config.involvement_bins
                )
            )

    return MatchAnalysis(
        match_id=info.match_id,
        team_ids=info.team_ids,
        config=config,
        bin_labels=tuple(b.label for b in timeline.bins),
        metrics=METRICS,
        summaries=team_summaries(events, possessions, series, info),
        players=player_involvement(events, info),
        possessions=possessions,
        series=tuple(series[k] for k in sorted(series)),
        evidence=tuple(evidence),
        moments=build_moments(
            evidence, timeline, info, config.merge_bins, config.moment_min_statistic
        ),
    )
