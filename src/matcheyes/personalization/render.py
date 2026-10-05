"""Audience views: templated sections built from the verified insight, never from agent text.

Every audience view carries the same mandatory core: the Stage 3 fact, any integrity
disclosure, and either the verified explanation (label, claim text, supporting evidence,
uncertainty) or a "no verified explanation" section. Audiences differ only in the optional
sections around that core (`policy.OPTIONAL_SECTIONS`) and in framing words that carry no claim.

* ANALYST: the canonical narrative, every claim's evidence, alternatives, downgrades,
  quarantined IDs, integrity state and the Stage 3 basis.
* BROADCASTER: minute and team, the verified explanation, and the verified trigger event.
* FAN: a plain verdict word, the verified explanation, what the metric measures, and the
  game state.

No view says why a change "mattered": nothing upstream establishes an effect on the match.
"""

from collections.abc import Callable

from matcheyes.agents.contracts import (
    EvidenceIntegrity,
    FinalInsight,
    HypothesisKind,
    Verdict,
)
from matcheyes.agents.hypotheses import TRIGGER_FACTS
from matcheyes.agents.tools import MatchWorkspace
from matcheyes.analytics.context import GameState
from matcheyes.analytics.moments import Names
from matcheyes.personalization.contracts import (
    EXCLUSION_TEXT,
    INTEGRITY_TEXT,
    NO_EXPLANATION_TEXT,
    Audience,
    PersonalizationProfile,
    PersonalizedInsight,
    SectionKind,
    ViewSection,
    factual_claim,
    fingerprint,
    lead_claim,
    verification_label,
)
from matcheyes.personalization.glossary import GLOSSARY
from matcheyes.personalization.involvement import Involvement, involvement
from matcheyes.personalization.policy import omitted, relevance

K = SectionKind

FAN_VERDICT = {
    Verdict.EXPLAINED: "Explained by the evidence.",
    Verdict.TENTATIVE: "Possible explanation, not confirmed.",
    Verdict.NATURAL_VARIATION: "Ordinary variation.",
}
GAME_STATE = {
    GameState.LEADING: "leading",
    GameState.DRAWING: "level",
    GameState.TRAILING: "trailing",
}
KEY_EVENT = {
    "goal": "a goal",
    "dismissal": "a dismissal",
    "substitution": "a substitution",
    "formation_change": "a formation change",
}
LABELS = {
    K.FACT: "FACT",
    K.CONTEXT: "FACT",
    K.TRIGGER: "FACT",
    K.INVOLVEMENT: "FACT",
    K.GLOSSARY: "DEFINITION",
    K.INTEGRITY: "ANALYSIS",
    K.INTERPRETATION: "AI INTERPRETATION",
    K.CAVEAT: "ANALYSIS",
    K.NO_INSIGHT: "ANALYSIS",
    K.ALTERNATIVES: "ANALYSIS",
    K.EVIDENCE: "EVIDENCE",
    K.QUARANTINE: "ANALYSIS",
    K.DETAIL: "ANALYSIS",
    K.NARRATIVE: "CANONICAL NARRATIVE",
}


def _label(kind: HypothesisKind) -> str:
    return kind.value.replace("_", " ")


def _cite(ids: tuple[str, ...]) -> str:
    return f" (evidence {', '.join(ids)})" if ids else ""


def _core(final: FinalInsight, explanation: Callable[[str, str], str]) -> list[ViewSection]:
    """The mandatory sections, identical in content for every audience. `explanation` frames
    the label and verified claim text; it may add words around them, never replace them."""
    sections = [ViewSection(kind=K.FACT, text=factual_claim(final).text, mandatory=True)]
    if final.evidence_integrity is EvidenceIntegrity.COMPROMISED:
        sections.append(ViewSection(kind=K.INTEGRITY, text=INTEGRITY_TEXT, mandatory=True))
    elif final.quarantined:
        sections.append(ViewSection(kind=K.INTEGRITY, text=EXCLUSION_TEXT, mandatory=True))
    label, lead = verification_label(final), lead_claim(final)
    if label is None or lead is None:
        text = NO_EXPLANATION_TEXT.get(final.verdict, NO_EXPLANATION_TEXT[Verdict.UNAVAILABLE])
        return [*sections, ViewSection(kind=K.NO_INSIGHT, text=text, mandatory=True)]
    return [
        *sections,
        ViewSection(
            kind=K.INTERPRETATION,
            text=explanation(label, lead.text),
            evidence_ids=lead.supporting_evidence_ids,
            mandatory=True,
        ),
        ViewSection(kind=K.CAVEAT, text=lead.uncertainty, mandatory=True),
    ]


def _involvement(inv: Involvement) -> list[ViewSection]:
    if not inv.player_events or inv.player_name is None:
        return []
    n = len(inv.player_events)
    text = f"{inv.player_name} appears in {n} of the events behind this insight."
    return [ViewSection(kind=K.INVOLVEMENT, text=text)]


def trigger(final: FinalInsight, ws: MatchWorkspace) -> ViewSection | None:
    """The cause a verified triggered explanation cites, by type and minute, from its own
    supporting evidence. None when the explanation is not triggered or not verified."""
    lead = lead_claim(final)
    if lead is None or lead.hypothesis not in TRIGGER_FACTS:
        return None
    if final.verdict not in (Verdict.EXPLAINED, Verdict.TENTATIVE):
        return None
    pool = {e.evidence_id: e for e in final.evidence}
    names = Names(ws.info)
    for eid in lead.supporting_evidence_ids:
        item = pool.get(eid)
        value = item.facts.get(TRIGGER_FACTS[lead.hypothesis]) if item else None
        if not isinstance(value, str):
            continue
        key = next((k for k in ws.key_events if k.event_id == value), None)
        if key is not None and value in ws.events:
            minute = ws.events[value].instant.display_minute
            text = f"Preceded by {KEY_EVENT[key.kind]} at {minute}{_cite((eid,))}."
            return ViewSection(kind=K.TRIGGER, text=text, evidence_ids=(eid,))
        earlier = ws.candidates.get(value)
        if earlier is not None:
            text = (
                f"Preceded by an earlier change by {names.team(earlier.team_id)} at "
                f"{earlier.at.display_minute}{_cite((eid,))}."
            )
            return ViewSection(kind=K.TRIGGER, text=text, evidence_ids=(eid,))
    return None


def lead_ids(final: FinalInsight) -> tuple[str, ...]:
    lead = lead_claim(final)
    return lead.supporting_evidence_ids if lead else ()


def _analyst(final: FinalInsight, inv: Involvement, ws: MatchWorkspace) -> list[ViewSection]:
    factual = factual_claim(final)
    core = _core(final, lambda label, text: f"{label} {text}{_cite(lead_ids(final))}")
    core[0] = core[0].model_copy(update={"evidence_ids": factual.supporting_evidence_ids})
    sections = [ViewSection(kind=K.NARRATIVE, text=final.narrative), *core]
    if final.alternatives:
        listed = "; ".join(
            f"{_label(k)}: {s.value.replace('_', ' ')}" for k, s in final.alternatives
        )
        sections.append(ViewSection(kind=K.ALTERNATIVES, text=f"Alternatives: {listed}."))
    for claim in final.claims:
        role = "Explanation" if claim.hypothesis is not None else "Factual"
        ids = claim.supporting_evidence_ids + claim.contradicting_evidence_ids
        sections.append(
            ViewSection(
                kind=K.DETAIL,
                text=f"{role} claim: {claim.claim_type.value}, {claim.strength.value}, status "
                f"{claim.status.value.replace('_', ' ')}; supporting "
                f"{', '.join(claim.supporting_evidence_ids) or 'none'}; contradicting "
                f"{', '.join(claim.contradicting_evidence_ids) or 'none'}.",
                evidence_ids=tuple(dict.fromkeys(ids)),
            )
        )
    candidate = ws.candidates.get(final.candidate_id)
    detail = [f"Evidence integrity: {final.evidence_integrity.value}."]
    if candidate is not None:
        detail.append(
            f"Stage 3 level: {final.stage3_level}; basis: {'; '.join(candidate.level_basis)}."
        )
    detail.append(f"Downgrades: {'; '.join(final.downgrades) or 'none'}.")
    if final.failure:
        detail.append(f"Investigation did not complete: {final.failure}.")
    sections.append(ViewSection(kind=K.DETAIL, text=" ".join(detail)))
    for item in final.evidence:
        facts = ", ".join(f"{k}={v}" for k, v in item.facts.items())
        sections.append(
            ViewSection(
                kind=K.EVIDENCE,
                text=f"{item.evidence_id} ({item.tool.value}): {item.summary} Facts: {facts}.",
                evidence_ids=(item.evidence_id,),
            )
        )
    if final.quarantined:
        sections.append(
            ViewSection(
                kind=K.QUARANTINE,
                text=f"Excluded after failing provenance checks: {', '.join(final.quarantined)}.",
                evidence_ids=final.quarantined,
            )
        )
    return [*sections, *_involvement(inv)]


def _broadcaster(final: FinalInsight, inv: Involvement, ws: MatchWorkspace) -> list[ViewSection]:
    team = Names(ws.info).team(final.team_id)
    minute = final.at.display_minute
    context = ViewSection(kind=K.CONTEXT, text=f"{minute} | {team}")
    core = _core(final, lambda label, text: f"{text} {label}{_cite(lead_ids(final))}")
    found = trigger(final, ws)
    return [context, *core, *([found] if found else []), *_involvement(inv)]


def _fan(final: FinalInsight, inv: Involvement, ws: MatchWorkspace) -> list[ViewSection]:
    team = Names(ws.info).team(final.team_id)
    minute = final.at.display_minute
    candidate = ws.candidates.get(final.candidate_id)
    context = f"At {minute}, {team}"
    if candidate is not None:
        context += f" were {GAME_STATE[candidate.context.game_state]}"
    sections = [ViewSection(kind=K.CONTEXT, text=context + ".")]
    sections += _core(final, lambda label, text: f"{FAN_VERDICT[final.verdict]} {text} {label}")
    if candidate is not None:
        sections.append(ViewSection(kind=K.GLOSSARY, text=GLOSSARY[candidate.metric]))
    return [*sections, *_involvement(inv)]


BUILDERS: dict[
    Audience, Callable[[FinalInsight, Involvement, MatchWorkspace], list[ViewSection]]
] = {
    Audience.ANALYST: _analyst,
    Audience.BROADCASTER: _broadcaster,
    Audience.FAN: _fan,
}


def personalize(
    final: FinalInsight, profile: PersonalizationProfile, ws: MatchWorkspace
) -> PersonalizedInsight:
    """One audience view of one verified insight. Pure: the same inputs give the same view."""
    inv = involvement(final, profile, ws)
    score, basis = relevance(final, inv, profile.audience)
    return PersonalizedInsight(
        profile=profile,
        source=final,
        source_fingerprint=fingerprint(final),
        relevance=score,
        relevance_basis=basis,
        sections=tuple(BUILDERS[profile.audience](final, inv, ws)),
        omitted=omitted(profile.audience),
    )


def format_view(view: PersonalizedInsight, names: Names) -> str:
    final = view.source
    lines = [f"{final.at.display_minute:>6} {names.team(final.team_id)}"]
    for section in view.sections:
        if section.kind is K.NARRATIVE:
            lines.append(f"         {LABELS[section.kind]}:")
            lines += [f"           {line}" for line in section.text.splitlines()]
            continue
        lines.append(f"         {LABELS[section.kind]}: {section.text}")
    return "\n".join(lines)
