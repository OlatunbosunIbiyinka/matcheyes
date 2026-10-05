"""Audience feeds: every investigated insight is placed, listed secondary, or withheld, never lost.

An insight reaches a feed only if the claim auditor passes it (`orchestration.audit`), its view
constructs (the contract invariants hold), and the view auditor passes the view. Anything that
fails is withheld and listed by ID, so a defect fails safe and stays visible.

Fans and broadcasters see verified explanations first. Insights with no verified explanation go
to an explicit secondary list. A compromised insight always stays in the primary feed, warning
included, ranked below an intact equivalent (`policy.COMPROMISED_PENALTY`). Analysts see
everything on one timeline.
"""

from collections.abc import Sequence

from matcheyes.agents.tools import MatchWorkspace
from matcheyes.analytics.moments import Names
from matcheyes.domain.entities import Identifier
from matcheyes.orchestration.audit import audit_insight
from matcheyes.orchestration.investigation import InvestigationRecord
from matcheyes.personalization.audit import audit_view
from matcheyes.personalization.contracts import (
    NO_VERIFIED_INSIGHT,
    AudienceFeed,
    PersonalizationProfile,
    PersonalizedInsight,
)
from matcheyes.personalization.policy import Placement, order_key, placement
from matcheyes.personalization.render import format_view, personalize


def build_feed(
    records: Sequence[InvestigationRecord], profile: PersonalizationProfile, ws: MatchWorkspace
) -> AudienceFeed:
    """One audience's feed of the current verified insights. Pure: no state is kept between
    calls, so a new feed is built whenever the insights change."""
    placed: dict[Placement, list[PersonalizedInsight]] = {p: [] for p in Placement}
    withheld: list[Identifier] = []
    for record in records:
        final = record.final
        if not audit_insight(ws, final, record.verification).ok:
            withheld.append(final.investigation_id)
            continue
        try:
            view = personalize(final, profile, ws)
        except ValueError:
            withheld.append(final.investigation_id)
            continue
        if audit_view(view, ws, final):
            withheld.append(final.investigation_id)
            continue
        placed[placement(final, profile.audience)].append(view)
    for views in placed.values():
        views.sort(key=lambda v: order_key(v.source, v.relevance, profile.audience))
    primary = tuple(placed[Placement.PRIMARY])
    return AudienceFeed(
        profile=profile,
        primary=primary,
        secondary=tuple(placed[Placement.SECONDARY]),
        withheld=tuple(withheld),
        notice=None if primary else NO_VERIFIED_INSIGHT,
    )


def format_feed(feed: AudienceFeed, names: Names) -> str:
    profile = feed.profile
    preferences = [
        f"{label} {value}"
        for label, value in (
            ("club", profile.favourite_club_id),
            ("player", profile.favourite_player_id),
            ("metric", profile.favourite_metric),
        )
        if value is not None
    ]
    lines = [
        f"Audience: {profile.audience.value}"
        + (f" ({'; '.join(preferences)})" if preferences else "")
        + " - presentation only; verified truth is unchanged"
    ]
    if feed.notice:
        lines += ["", feed.notice]
    for view in feed.primary:
        lines += ["", format_view(view, names)]
    if feed.secondary:
        lines += ["", f"No verified explanation ({len(feed.secondary)}):"]
        lines += [format_view(view, names) for view in feed.secondary]
    if feed.withheld:
        lines += ["", f"Withheld after failing audit: {', '.join(feed.withheld)}"]
    return "\n".join(lines)
