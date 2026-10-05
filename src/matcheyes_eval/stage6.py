"""Stage 6 evaluation: audience views and feeds measured against the verified insights they present.

Personalization has no hidden truth of its own: its truth is the verified `FinalInsight`. So
nothing here reads the answer key. Every measure compares a view with the insight it wraps, or
views of one insight with each other:

* truth: views that fail construction (contract invariants), fingerprint mismatches, insights
  changed by personalizing, and findings from the independent view audit. Expected 0.
* audience: checklist coverage, compression against the canonical narrative, and depth.
* preference safety: what each preference shape changes against no preference.
* cross-audience consistency: one insight's mandatory core across the three audiences.
* feeds: placement, the secondary list, withheld insights, and compromised insights.
* presentation red team: known faults injected into genuine views; each must be caught by
  `audit_view`.

Compromised insights come from Stage 5 evidence tampering (`redteam.EVIDENCE_FAULTS`), run
through the real verifier, so the integrity states are produced by the pipeline, not written.

No targets are set: the results are reported as measured.
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pydantic import ValidationError

from matcheyes.agents.contracts import (
    HYPOTHESIS_HEDGED,
    EvidenceIntegrity,
    EvidenceItem,
    EvidenceRequest,
    FinalInsight,
    Verdict,
)
from matcheyes.agents.reasoning import RuleBasedReasoner
from matcheyes.agents.tools import MatchWorkspace
from matcheyes.analytics.moments import Names
from matcheyes.analytics.strength import EvidenceLevel
from matcheyes.domain.claims import ClaimStrength
from matcheyes.orchestration.investigation import InvestigationRecord, Orchestrator
from matcheyes.personalization.audit import audit_view
from matcheyes.personalization.contracts import (
    LABELLED,
    Audience,
    PersonalizationProfile,
    PersonalizedInsight,
    SectionKind,
    ViewSection,
    fingerprint,
    lead_claim,
)
from matcheyes.personalization.feed import build_feed
from matcheyes.personalization.involvement import actors
from matcheyes.personalization.policy import placement
from matcheyes.personalization.render import FAN_VERDICT, format_view, personalize, trigger
from matcheyes_eval.redteam import EVIDENCE_FAULTS, Donors, Tamper, TamperingToolBox
from matcheyes_eval.stage2 import Case

K = SectionKind
UNRELATED_CLUB = "nowhere-town"
ABSENT_PLAYER = "nowhere-town-09"
SHAPES = (
    "none",
    "club_team",
    "club_opponent",
    "club_unrelated",
    "metric",
    "player_cited",
    "player_uncited",
    "player_absent",
)

# --- preference shapes -------------------------------------------------------------------------


def cited_player(ws: MatchWorkspace, final: FinalInsight) -> str | None:
    return next(
        (p for e in final.event_ids if e in ws.events for p in sorted(actors(ws.events[e]))), None
    )


def uncited_player(ws: MatchWorkspace, final: FinalInsight) -> str:
    cited = {p for e in final.event_ids if e in ws.events for p in actors(ws.events[e])}
    return next(
        p.player_id
        for side in (ws.info.home, ws.info.away)
        for p in side.squad
        if p.player_id not in cited
    )


def preference_profiles(
    final: FinalInsight, ws: MatchWorkspace, audience: Audience
) -> dict[str, PersonalizationProfile]:
    """Every preference shape Stage 6 must stay truthful under, for one insight. A cited player
    exists only when the insight's events have an actor."""
    shapes: dict[str, dict[str, str]] = {
        "none": {},
        "club_team": {"favourite_club_id": final.team_id},
        "club_opponent": {"favourite_club_id": ws.info.opponent_of(final.team_id)},
        "club_unrelated": {"favourite_club_id": UNRELATED_CLUB},
        "metric": {"favourite_metric": ws.candidates[final.candidate_id].metric},
        "player_uncited": {"favourite_player_id": uncited_player(ws, final)},
        "player_absent": {"favourite_player_id": ABSENT_PLAYER},
    }
    cited = cited_player(ws, final)
    if cited is not None:
        shapes["player_cited"] = {"favourite_player_id": cited}
    return {
        name: PersonalizationProfile.model_validate({"audience": audience, **prefs})
        for name, prefs in shapes.items()
    }


# --- presentation faults -----------------------------------------------------------------------

ViewFault = Callable[[PersonalizedInsight, MatchWorkspace], PersonalizedInsight | None]


def _sections(view: PersonalizedInsight, sections: Sequence[ViewSection]) -> PersonalizedInsight:
    return view.model_copy(update={"sections": tuple(sections)})


def _edit(
    view: PersonalizedInsight,
    kinds: tuple[SectionKind, ...],
    edit: Callable[[ViewSection], ViewSection],
) -> PersonalizedInsight | None:
    """Apply `edit` to the first section of the first kind present, or None."""
    for kind in kinds:
        for i, s in enumerate(view.sections):
            if s.kind is kind:
                edited = edit(s)
                if edited == s:
                    return None
                return _sections(view, [*view.sections[:i], edited, *view.sections[i + 1 :]])
    return None


def _text(s: ViewSection, text: str) -> ViewSection:
    return s.model_copy(update={"text": text})


def _swap_source(view: PersonalizedInsight, final: FinalInsight) -> PersonalizedInsight | None:
    if final == view.source:
        return None
    return view.model_copy(update={"source": final, "source_fingerprint": fingerprint(final)})


_BODY = (K.INTERPRETATION, K.NO_INSIGHT)


def _dropped_warning(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    kept = [s for s in view.sections if s.kind is not K.INTEGRITY]
    return None if len(kept) == len(view.sections) else _sections(view, kept)


def _softened_warning(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    return _edit(view, (K.INTEGRITY,), lambda s: _text(s, "Some data was double-checked."))


def _optional_warning(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    return _edit(view, (K.INTEGRITY,), lambda s: s.model_copy(update={"mandatory": False}))


def _raised_label(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    lead = lead_claim(view.source)
    if lead is None or view.source.verdict not in LABELLED:
        return None
    ladder = sorted(ClaimStrength, key=lambda s: s.rank)
    if lead.strength is ladder[-1]:
        return None
    higher = ladder[ladder.index(lead.strength) + 1].value
    return _edit(
        view,
        (K.INTERPRETATION,),
        lambda s: _text(s, s.text.replace(f", {lead.strength.value}]", f", {higher}]")),
    )


def _full_verification(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    marker = "[verified on remaining evidence,"
    return _edit(
        view, (K.INTERPRETATION,), lambda s: _text(s, s.text.replace(marker, "[verified,"))
    )


def _causal_wording(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    lead = lead_claim(view.source)
    if lead is not None and lead.strength is ClaimStrength.SUPPORTED:
        return _edit(
            view, (K.CONTEXT, K.DETAIL), lambda s: _text(s, f"{s.text} The goal caused it.")
        )
    return _edit(view, _BODY, lambda s: _text(s, f"{s.text} The goal caused this change."))


def _certainty(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    return _edit(view, _BODY, lambda s: _text(s, f"{s.text} This clearly settles it."))


def _intent(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    return _edit(view, _BODY, lambda s: _text(s, f"{s.text} The manager decided this."))


def _invented_player(view: PersonalizedInsight, ws: MatchWorkspace) -> PersonalizedInsight | None:
    player = uncited_player(ws, view.source)
    name = next(
        p.name for side in (ws.info.home, ws.info.away) for p in side.squad if p.player_id == player
    )
    line = ViewSection(
        kind=K.INVOLVEMENT, text=f"{name} appears in 2 of the events behind this insight."
    )
    return _sections(view, [*view.sections, line])


def _credited_favourite(
    view: PersonalizedInsight, ws: MatchWorkspace
) -> PersonalizedInsight | None:
    """The favourite player is in the squad but not in the cited events, yet is credited."""
    player = uncited_player(ws, view.source)
    name = next(
        p.name for side in (ws.info.home, ws.info.away) for p in side.squad if p.player_id == player
    )
    profile = view.profile.model_copy(update={"favourite_player_id": player})
    line = ViewSection(
        kind=K.INVOLVEMENT, text=f"{name} appears in 1 of the events behind this insight."
    )
    return view.model_copy(update={"profile": profile, "sections": (*view.sections, line)})


def _player_agency(view: PersonalizedInsight, ws: MatchWorkspace) -> PersonalizedInsight | None:
    player = uncited_player(ws, view.source)
    name = next(
        p.name for side in (ws.info.home, ws.info.away) for p in side.squad if p.player_id == player
    )
    return _edit(view, _BODY, lambda s: _text(s, f"{s.text} {name} drove it."))


def _invented_club(view: PersonalizedInsight, ws: MatchWorkspace) -> PersonalizedInsight | None:
    other = ws.info.sheet(ws.info.opponent_of(view.source.team_id)).club.name
    return _edit(view, _BODY, lambda s: _text(s, f"{s.text} Seen against {other}."))


def _unrelated_club(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    profile = view.profile.model_copy(update={"favourite_club_id": UNRELATED_CLUB})
    edited = _edit(view, _BODY, lambda s: _text(s, f"{s.text} Nowhere Town fans take note."))
    return None if edited is None else edited.model_copy(update={"profile": profile})


def _dropped_not_ruled_out(
    view: PersonalizedInsight, _: MatchWorkspace
) -> PersonalizedInsight | None:
    marker = "Not ruled out: "

    def drop(s: ViewSection) -> ViewSection:
        if marker not in s.text:
            return s
        return _text(s, "Every alternative was weakened by evidence.")

    return _edit(view, (K.CAVEAT,), drop)


def _quarantined_as_valid(
    view: PersonalizedInsight, _: MatchWorkspace
) -> PersonalizedInsight | None:
    if not view.source.quarantined:
        return None
    q = view.source.quarantined[0]
    return _edit(
        view,
        (K.INTERPRETATION, K.FACT),
        lambda s: s.model_copy(update={"evidence_ids": (*s.evidence_ids, q)}),
    )


def _invented_number(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    return _edit(view, _BODY, lambda s: _text(s, f"{s.text} Pressing rose by 317 per cent."))


def _changed_claim(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    lead = lead_claim(view.source)
    if lead is None or lead.hypothesis is None or view.source.verdict not in LABELLED:
        return None
    other = next(
        t for k, t in HYPOTHESIS_HEDGED.items() if k is not lead.hypothesis and t != lead.text
    )
    return _edit(view, (K.INTERPRETATION,), lambda s: _text(s, s.text.replace(lead.text, other)))


def _changed_evidence(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    lead = lead_claim(view.source)
    if lead is None:
        return None
    spare = [
        e.evidence_id
        for e in view.source.evidence
        if e.evidence_id not in lead.supporting_evidence_ids
    ]
    swapped = (spare[0],) if spare else ("ev-99",)
    return _edit(
        view, (K.INTERPRETATION,), lambda s: s.model_copy(update={"evidence_ids": swapped})
    )


def _changed_verdict(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    final = view.source
    verdict = Verdict.EXPLAINED if final.verdict is not Verdict.EXPLAINED else Verdict.TENTATIVE
    return _swap_source(view, final.model_copy(update={"verdict": verdict}))


def _verdict_framing(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    if view.source.verdict is Verdict.EXPLAINED:
        return None
    return _edit(
        view, (K.INTERPRETATION,), lambda s: _text(s, f"Explained by the evidence. {s.text}")
    )


def _changed_integrity(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    final = view.source
    flipped = (
        EvidenceIntegrity.INTACT
        if final.evidence_integrity is EvidenceIntegrity.COMPROMISED
        else EvidenceIntegrity.COMPROMISED
    )
    return _swap_source(view, final.model_copy(update={"evidence_integrity": flipped}))


def _preference_alters_truth(
    view: PersonalizedInsight, _: MatchWorkspace
) -> PersonalizedInsight | None:
    """A favourite club 'earns' its team a stronger claim; the forgery is re-fingerprinted."""
    final = view.source
    lead = lead_claim(final)
    if lead is None or lead.strength is ClaimStrength.SUPPORTED:
        return None
    claims = tuple(
        c.model_copy(update={"strength": ClaimStrength.SUPPORTED}) if c is lead else c
        for c in final.claims
    )
    forged = _swap_source(
        view, final.model_copy(update={"claims": claims, "strength": ClaimStrength.SUPPORTED})
    )
    if forged is None:
        return None
    profile = view.profile.model_copy(update={"favourite_club_id": final.team_id})
    return forged.model_copy(update={"profile": profile})


def _stale_fingerprint(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    return view.model_copy(update={"source_fingerprint": "0" * 64})


def _altered_narrative(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    return _edit(view, (K.NARRATIVE,), lambda s: _text(s, s.text + "\nANALYSIS: Decisive."))


def _hidden_alternatives(
    view: PersonalizedInsight, _: MatchWorkspace
) -> PersonalizedInsight | None:
    kept = [s for s in view.sections if s.kind is not K.ALTERNATIVES]
    return None if len(kept) == len(view.sections) else _sections(view, kept)


def _hidden_evidence(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    evidence = [i for i, s in enumerate(view.sections) if s.kind is K.EVIDENCE]
    if not evidence:
        return None
    return _sections(view, [s for i, s in enumerate(view.sections) if i != evidence[0]])


def _inflated_relevance(view: PersonalizedInsight, _: MatchWorkspace) -> PersonalizedInsight | None:
    return view.model_copy(update={"relevance": view.relevance + 50})


PRESENTATION_FAULTS: dict[str, tuple[frozenset[str], ViewFault]] = {
    "dropped_warning": (frozenset({"integrity"}), _dropped_warning),
    "softened_warning": (frozenset({"integrity"}), _softened_warning),
    "optional_warning": (frozenset({"integrity"}), _optional_warning),
    "raised_label": (frozenset({"label"}), _raised_label),
    "full_verification_claimed": (frozenset({"label"}), _full_verification),
    "added_causal_wording": (frozenset({"language"}), _causal_wording),
    "certainty_wording": (frozenset({"language"}), _certainty),
    "intent_wording": (frozenset({"language"}), _intent),
    "invented_player": (frozenset({"players"}), _invented_player),
    "uninvolved_favourite_credited": (frozenset({"players"}), _credited_favourite),
    "player_agency": (frozenset({"players", "language"}), _player_agency),
    "invented_club": (frozenset({"clubs"}), _invented_club),
    "unrelated_favourite_club": (frozenset({"clubs"}), _unrelated_club),
    "dropped_not_ruled_out": (frozenset({"caveat"}), _dropped_not_ruled_out),
    "quarantined_shown_as_valid": (frozenset({"evidence"}), _quarantined_as_valid),
    "invented_number": (frozenset({"numbers"}), _invented_number),
    "changed_claim": (frozenset({"claim"}), _changed_claim),
    "changed_evidence_id": (frozenset({"claim", "evidence"}), _changed_evidence),
    "changed_verdict": (frozenset({"source"}), _changed_verdict),
    "verdict_framing": (frozenset({"verdict"}), _verdict_framing),
    "changed_integrity": (frozenset({"source"}), _changed_integrity),
    "preference_alters_truth": (frozenset({"source"}), _preference_alters_truth),
    "stale_fingerprint": (frozenset({"source"}), _stale_fingerprint),
    "altered_narrative": (frozenset({"evidence", "audience"}), _altered_narrative),
    "hidden_alternatives": (frozenset({"audience"}), _hidden_alternatives),
    "hidden_evidence": (frozenset({"audience"}), _hidden_evidence),
    "inflated_relevance": (frozenset({"relevance"}), _inflated_relevance),
}
"""Each fault, the audit check(s) that must catch it, and how it is injected (`model_copy`,
which skips the construction validators, so only `audit_view` stands in the way)."""


def caught(findings: list[str], expected: frozenset[str]) -> bool:
    return any(f.split(":", 1)[0] in expected for f in findings)


# --- audience measures ---------------------------------------------------------------------------


def checklist(view: PersonalizedInsight, ws: MatchWorkspace) -> list[bool]:
    """What this audience's view should contain, item by item, from the discovery requirements."""
    final, kinds = view.source, [s.kind for s in view.sections]
    texts = {k: [s.text for s in view.sections if s.kind is k] for k in K}
    flagged = final.evidence_integrity is EvidenceIntegrity.COMPROMISED or bool(final.quarantined)
    explained = K.INTERPRETATION in kinds or K.NO_INSIGHT in kinds
    items = [K.FACT in kinds, explained, (K.INTEGRITY in kinds) == flagged]
    if view.profile.audience is Audience.ANALYST:
        shown = [s.evidence_ids for s in view.sections if s.kind is K.EVIDENCE]
        items += [
            K.NARRATIVE in kinds,
            shown == [(e.evidence_id,) for e in final.evidence],
            bool(texts[K.ALTERNATIVES]) == bool(final.alternatives),
            bool(texts[K.QUARANTINE]) == bool(final.quarantined),
            any("Stage 3 level" in t for t in texts[K.DETAIL]),
            all(any(d in t for t in texts[K.DETAIL]) for d in final.downgrades),
        ]
    elif view.profile.audience is Audience.BROADCASTER:
        items += [
            any(final.at.display_minute in t for t in texts[K.CONTEXT]),
            bool(texts[K.TRIGGER]) == (trigger(final, ws) is not None),
        ]
    else:
        word = FAN_VERDICT.get(final.verdict)
        items += [
            word is None or any(t.startswith(word) for t in texts[K.INTERPRETATION]),
            bool(texts[K.GLOSSARY]),
            any(t.endswith((" leading.", " level.", " trailing.")) for t in texts[K.CONTEXT]),
        ]
    return items


def core(view: PersonalizedInsight) -> tuple[tuple[str, str | None, tuple[str, ...]], ...]:
    """The mandatory content every audience shares: kinds, texts (the explanation by its claim
    text and label only) and the explanation's evidence."""
    lead = lead_claim(view.source)
    out = []
    for s in view.sections:
        if not s.mandatory:
            continue
        if s.kind is K.INTERPRETATION and lead is not None:
            out.append((s.kind.value, lead.text if lead.text in s.text else None, s.evidence_ids))
        else:
            out.append((s.kind.value, s.text, () if s.kind is K.FACT else s.evidence_ids))
    return tuple(out)


@dataclass
class AudienceRow:
    views: int = 0
    construction_failures: int = 0
    fingerprint_mismatches: int = 0
    source_changed: int = 0
    audit_flagged: int = 0
    checklist_met: int = 0
    checklist_items: int = 0
    characters: int = 0
    narrative_characters: int = 0
    sections: int = 0
    evidence_ids_shown: int = 0
    findings: Counter[str] = field(default_factory=Counter)


@dataclass
class ShapeRow:
    applicable: int = 0
    truth_changed: int = 0
    """Sections other than the involvement line differ from the no-preference view."""
    placement_changed: int = 0
    involvement_lines: int = 0
    involvement_without_events: int = 0
    relevance_raised: int = 0
    audit_flagged: int = 0


@dataclass
class FeedRow:
    feeds: int = 0
    primary: int = 0
    secondary: int = 0
    withheld: int = 0
    empty_primary: int = 0
    compromised: int = 0
    compromised_primary: int = 0
    compromised_warned: int = 0


@dataclass
class FaultRow:
    injected: int = 0
    caught: int = 0
    caught_by_expected: int = 0


@dataclass
class Stage6Results:
    split: str
    seeds: int
    matches: int = 0
    insights: int = 0
    verdicts: Counter[str] = field(default_factory=Counter)
    integrity: Counter[str] = field(default_factory=Counter)
    audiences: dict[str, AudienceRow] = field(default_factory=lambda: defaultdict(AudienceRow))
    shapes: dict[str, dict[str, ShapeRow]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(ShapeRow))
    )
    inconsistent_cores: int = 0
    feeds: dict[str, FeedRow] = field(default_factory=lambda: defaultdict(FeedRow))
    faults: dict[str, FaultRow] = field(default_factory=lambda: defaultdict(FaultRow))


def _measure_insight(
    record: InvestigationRecord, ws: MatchWorkspace, results: Stage6Results
) -> None:
    final = record.final
    before = fingerprint(final)
    names = Names(ws.info)
    results.insights += 1
    results.verdicts[final.verdict.value] += 1
    results.integrity[final.evidence_integrity.value] += 1
    cores = set()
    for audience in Audience:
        row = results.audiences[audience.value]
        baseline: PersonalizedInsight | None = None
        for shape, profile in preference_profiles(final, ws, audience).items():
            row.views += 1
            try:
                view = personalize(final, profile, ws)
            except (ValueError, ValidationError):
                row.construction_failures += 1
                continue
            row.fingerprint_mismatches += int(view.source_fingerprint != before)
            findings = audit_view(view, ws, final)
            row.audit_flagged += int(bool(findings))
            row.findings.update(f.split(":", 1)[0] for f in findings)
            s = results.shapes[audience.value][shape]
            s.applicable += 1
            s.audit_flagged += int(bool(findings))
            lines = [x for x in view.sections if x.kind is K.INVOLVEMENT]
            s.involvement_lines += len(lines)
            if shape == "none":
                baseline = view
                items = checklist(view, ws)
                row.checklist_met += sum(items)
                row.checklist_items += len(items)
                row.characters += len(format_view(view, names))
                row.narrative_characters += len(final.narrative)
                row.sections += len(view.sections)
                row.evidence_ids_shown += len({e for x in view.sections for e in x.evidence_ids})
                cores.add(core(view))
                if audience is Audience.ANALYST:
                    _inject(view, ws, final, results)
                continue
            if baseline is None:
                continue
            without = tuple(x for x in view.sections if x.kind is not K.INVOLVEMENT)
            s.truth_changed += int(without != baseline.sections)
            s.placement_changed += int(
                placement(view.source, audience) != placement(baseline.source, audience)
            )
            s.relevance_raised += int(view.relevance > baseline.relevance)
            s.involvement_without_events += int(bool(lines) and shape != "player_cited")
    results.inconsistent_cores += int(len(cores) > 1)
    if fingerprint(final) != before:
        results.audiences["all"].source_changed += 1


def _inject(
    analyst: PersonalizedInsight, ws: MatchWorkspace, final: FinalInsight, results: Stage6Results
) -> None:
    """Inject every presentation fault into the genuine views of this insight."""
    for audience in Audience:
        view = (
            analyst
            if audience is Audience.ANALYST
            else personalize(final, PersonalizationProfile(audience=audience), ws)
        )
        for name, (expected, fault) in PRESENTATION_FAULTS.items():
            forged = fault(view, ws)
            if forged is None or forged == view:
                continue
            findings = audit_view(forged, ws, final)
            row = results.faults[name]
            row.injected += 1
            row.caught += int(bool(findings))
            row.caught_by_expected += int(caught(findings, expected))


def _measure_feeds(
    records: Sequence[InvestigationRecord], ws: MatchWorkspace, results: Stage6Results, group: str
) -> None:
    for audience in Audience:
        feed = build_feed(records, PersonalizationProfile(audience=audience), ws)
        row = results.feeds[f"{group}/{audience.value}"]
        row.feeds += 1
        row.primary += len(feed.primary)
        row.secondary += len(feed.secondary)
        row.withheld += len(feed.withheld)
        row.empty_primary += int(not feed.primary)
        flagged = [
            r for r in records if r.final.evidence_integrity is EvidenceIntegrity.COMPROMISED
        ]
        row.compromised += len(flagged)
        for view in feed.primary:
            if view.source.evidence_integrity is EvidenceIntegrity.COMPROMISED:
                row.compromised_primary += 1
                row.compromised_warned += int(
                    any(s.kind is K.INTEGRITY and s.mandatory for s in view.sections)
                )


ONE_ITEM = "ev-03"
"""The single item altered in the "one" tampering mode: early in every investigation, and not
the candidate assessment, so some explanations survive on the remaining evidence."""


def _only(evidence_id: str, tamper: Tamper) -> Tamper:
    def apply(item: EvidenceItem, request: EvidenceRequest, donors: Donors) -> EvidenceItem | None:
        return tamper(item, request, donors) if item.evidence_id == evidence_id else None

    return apply


def _tampered(
    ws: MatchWorkspace, candidate_ids: Sequence[str], mode: str
) -> list[InvestigationRecord]:
    """Investigations with an evidence value altered after the tool ran (Stage 5, layer B): every
    item ("all"), or only `ONE_ITEM` ("one"). Only investigations whose integrity changed."""
    tamper = EVIDENCE_FAULTS["altered_metric_value"]
    orch = Orchestrator(ws, RuleBasedReasoner())
    orch.toolbox = TamperingToolBox(
        Donors(ws, None, None), tamper if mode == "all" else _only(ONE_ITEM, tamper)
    )
    records = [orch.investigate(cid) for cid in candidate_ids]
    return [r for r in records if r.final.evidence_integrity is not EvidenceIntegrity.INTACT]


def evaluate_stage6(
    cases: Sequence[Case], split: str, seeds: int, tamper_matches: int | None = None
) -> Stage6Results:
    """Every case's insights at weak or above, every audience and preference shape; tampered
    investigations on the first `tamper_matches` cases (all if None)."""
    results = Stage6Results(split=split, seeds=seeds)
    for index, case in enumerate(cases):
        ws = MatchWorkspace.build(case.match)
        orch = Orchestrator(ws, RuleBasedReasoner())
        eligible = [
            c.candidate_id for c in ws.stage3.candidates if c.level.rank >= EvidenceLevel.WEAK.rank
        ]
        records = [orch.investigate(cid) for cid in eligible]
        results.matches += 1
        for record in records:
            _measure_insight(record, ws, results)
        _measure_feeds(records, ws, results, "clean")
        if tamper_matches is not None and index >= tamper_matches:
            continue
        for mode in ("all", "one"):
            tampered = _tampered(ws, eligible, mode)
            for record in tampered:
                _measure_insight(record, ws, results)
            if tampered:
                _measure_feeds(tampered, ws, results, f"tampered-{mode}")
    return results


def _pct(n: int, d: int) -> str:
    return f"{n / d:.1%}" if d else "n/a"


def format_stage6(results: Stage6Results) -> str:
    lines = [
        f"Stage 6 personalization - {results.split} split, {results.seeds} seeds per scenario, "
        f"{results.matches} matches, {results.insights} insights",
        f"  verdicts: {dict(sorted(results.verdicts.items()))}",
        f"  evidence integrity: {dict(sorted(results.integrity.items()))}",
        "",
        "truth (expected 0): views / construction failures / fingerprint mismatches / "
        "audit-flagged",
    ]
    for audience in Audience:
        row = results.audiences[audience.value]
        lines.append(
            f"  {audience.value:<12} {row.views:>6} {row.construction_failures:>6} "
            f"{row.fingerprint_mismatches:>6} {row.audit_flagged:>6} {dict(row.findings)}"
        )
    lines.append(f"  insights changed by personalizing: {results.audiences['all'].source_changed}")
    lines.append(f"  inconsistent mandatory core across audiences: {results.inconsistent_cores}")
    lines += ["", "audience (no preference): checklist / chars vs narrative / sections / IDs shown"]
    for audience in Audience:
        row = results.audiences[audience.value]
        n = max(results.insights, 1)
        lines.append(
            f"  {audience.value:<12} {_pct(row.checklist_met, row.checklist_items):>7}   "
            f"{row.characters / max(row.narrative_characters, 1):>5.2f}x   "
            f"{row.sections / n:>5.1f}   {row.evidence_ids_shown / n:>5.1f}"
        )
    lines += [
        "",
        "preference safety: applicable / truth changed / placement changed / involvement lines / "
        "involvement without events / relevance raised / audit-flagged",
    ]
    for audience in Audience:
        for shape in SHAPES:
            s = results.shapes[audience.value].get(shape)
            if s is None:
                continue
            lines.append(
                f"  {audience.value:<12} {shape:<15} {s.applicable:>6} {s.truth_changed:>4} "
                f"{s.placement_changed:>4} {s.involvement_lines:>5} "
                f"{s.involvement_without_events:>4} {s.relevance_raised:>6} {s.audit_flagged:>4}"
            )
    lines += [
        "",
        "feeds: feeds / primary / secondary / withheld / empty primary / compromised / "
        "compromised primary / warned",
    ]
    for key, f in sorted(results.feeds.items()):
        lines.append(
            f"  {key:<26} {f.feeds:>4} {f.primary:>6} {f.secondary:>6} {f.withheld:>4} "
            f"{f.empty_primary:>4} {f.compromised:>5} {f.compromised_primary:>5} "
            f"{f.compromised_warned:>5}"
        )
    injected = sum(f.injected for f in results.faults.values())
    missed = sum(f.injected - f.caught for f in results.faults.values())
    lines += [
        "",
        f"presentation red team: {injected - missed} of {injected} caught "
        "(injected / caught / caught by the expected check)",
    ]
    for name, fault in sorted(results.faults.items()):
        lines.append(
            f"  {name:<30} {fault.injected:>6} {fault.caught:>6} {fault.caught_by_expected:>6}"
        )
    return "\n".join(lines)
