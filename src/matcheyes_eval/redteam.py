"""Stage 5 red team: inject known faults, count what is detected and what reaches a reader.

Three fault layers, each aimed at a different defence:

* A - assessment faults: the reasoner proposes something wrong (FaultyReasoner). Defence: the
  verifier's gates.
* B - evidence tampering: a tool result is altered after the tool ran (TamperingToolBox swapped
  into the orchestrator). Defence: the verifier's provenance replay.
* C - final-insight tampering: the structured insight or the narrative is edited after
  verification. Defence: the independent auditor and the lineage audit.

A fault is *detected* if the verifier or auditor raised a signal about it, *contained* if it did
not change what a reader sees, and *missed* if it changed what a reader sees and nothing flagged
it. A verifier that rejects everything would detect every fault, so the same defences are also run
on clean investigations and on valid cases (false rejection).

The answer key is used only for decoy scoring, here, after investigations finish.
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from matcheyes.agents.casefile import build_case_file
from matcheyes.agents.contracts import (
    HYPOTHESIS_DESCRIPTIONS,
    Assessment,
    ClaimType,
    Comparator,
    EvidenceItem,
    EvidenceRequest,
    FactAssertion,
    FinalInsight,
    HypothesisKind,
    ProposedHypothesis,
    Status,
    ToolName,
    Verdict,
)
from matcheyes.agents.hypotheses import CONTRADICTIONS, SUPPORT_GROUPS, irrelevance
from matcheyes.agents.reasoning import AgentTask, RuleBasedReasoner
from matcheyes.agents.tools import MatchWorkspace, ToolBox, ToolError
from matcheyes.analytics.strength import EvidenceLevel
from matcheyes.domain.claims import ClaimStrength
from matcheyes.orchestration.audit import audit_insight, eligible_strength, valid_explanation
from matcheyes.orchestration.investigation import InvestigationRecord, Orchestrator
from matcheyes.orchestration.lineage import audit_lineage
from matcheyes_eval.scoring import Clock
from matcheyes_eval.stage2 import Case
from matcheyes_eval.stage4 import FAULTS, FaultyReasoner, Mutation, _escaped, _with

H = HypothesisKind
T = ToolName

# --- layer A: assessment faults (in addition to the eight Stage 4 faults) --------------------


def _contradiction_ignored(a: Assessment, _: AgentTask) -> Assessment | None:
    """Cite a material contradiction, then claim the explanation anyway."""
    target = next(
        (h for h in a.hypotheses if h.contradicting and h.kind is not H.NATURAL_VARIATION), None
    )
    if target is None:
        return None
    kept = _with(a, target.kind, status=Status.SUPPORTED)
    return kept.model_copy(
        update={"leading": target.kind, "proposed_strength": ClaimStrength.HYPOTHESISED}
    )


def _weakened(assertion: FactAssertion, item: EvidenceItem) -> FactAssertion:
    actual = item.facts.get(assertion.fact)
    if isinstance(actual, int | float) and not isinstance(actual, bool):
        return assertion.model_copy(update={"comparator": Comparator.GE, "value": actual - 1000})
    return assertion.model_copy(update={"comparator": Comparator.NE, "value": "__absent__"})


def _non_entailing(a: Assessment, task: AgentTask) -> Assessment | None:
    """Replace each supporting assertion with a true one that states too little to count."""
    if a.leading is None:
        return None
    pool = {e.evidence_id: e for e in task.evidence}
    lead = next(h for h in a.hypotheses if h.kind is a.leading)
    weak = tuple(
        _weakened(s, pool[s.evidence_id]) for s in lead.supporting if s.evidence_id in pool
    )
    if not weak:
        return None
    return _with(a, a.leading, supporting=weak)


def _true_facts(task: AgentTask, tools: frozenset[ToolName]) -> tuple[FactAssertion, ...]:
    return tuple(
        FactAssertion(evidence_id=e.evidence_id, fact=k, comparator=Comparator.EQ, value=v)
        for e in task.evidence
        if e.tool in tools
        for k, v in e.facts.items()
        if v is not None
    )[:12]


def _misattribute(kind: HypothesisKind, tools: frozenset[ToolName]) -> Mutation:
    """Claim `kind`, backed by every true fact the relevant tools returned."""

    def mutate(a: Assessment, task: AgentTask) -> Assessment | None:
        proposal = next((h for h in a.hypotheses if h.kind is kind), None)
        if proposal is None or proposal.status is Status.SUPPORTED:
            return None
        support = _true_facts(task, tools)
        if not support:
            return None
        claimed = _with(a, kind, status=Status.SUPPORTED, supporting=support, contradicting=())
        return claimed.model_copy(
            update={"leading": kind, "proposed_strength": ClaimStrength.SUPPORTED}
        )

    return mutate


def _player_attribution(a: Assessment, task: AgentTask) -> Assessment | None:
    """Attribute the change to a named substitute the evidence does not single out."""
    item = next(
        (e for e in task.evidence if isinstance(e.facts.get("top_substitute_id"), str)), None
    )
    proposal = next((h for h in a.hypotheses if h.kind is H.PERSONNEL_CHANGE), None)
    if item is None or proposal is None:
        return None
    forged = FactAssertion(
        evidence_id=item.evidence_id,
        fact="top_substitute_id",
        comparator=Comparator.EQ,
        value=f"{task.case.team_id}-99",
    )
    claimed = _with(
        a, H.PERSONNEL_CHANGE, status=Status.SUPPORTED, supporting=(*proposal.supporting, forged)
    )
    return claimed.model_copy(
        update={"leading": H.PERSONNEL_CHANGE, "proposed_strength": ClaimStrength.HYPOTHESISED}
    )


def _wrong_team(a: Assessment, task: AgentTask) -> Assessment | None:
    """Support the leading explanation with evidence about the other team."""
    if a.leading is None or a.leading is H.OPPONENT_DRIVEN:
        return None
    other = next((e for e in task.evidence if e.team_id not in (None, task.case.team_id)), None)
    if other is None:
        return None
    support = tuple(
        FactAssertion(evidence_id=other.evidence_id, fact=k, comparator=Comparator.EQ, value=v)
        for k, v in other.facts.items()
        if v is not None
    )[:6]
    lead = next(h for h in a.hypotheses if h.kind is a.leading)
    return _with(a, a.leading, supporting=(*lead.supporting, *support))


ASSESSMENT_FAULTS: dict[str, Mutation] = {
    **FAULTS,
    "contradiction_ignored": _contradiction_ignored,
    "non_entailing_comparator": _non_entailing,
    "score_state_misinterpretation": _misattribute(
        H.SCORE_STATE_RESPONSE, frozenset({T.CHECK_GAME_STATE_RESPONSE})
    ),
    "substitution_misattribution": _misattribute(
        H.PERSONNEL_CHANGE, frozenset({T.GET_KEY_EVENTS, T.GET_SUBSTITUTE_INVOLVEMENT})
    ),
    "unsupported_player_attribution": _player_attribution,
    "wrong_team_support": _wrong_team,
}

# --- layer B: evidence tampering ---------------------------------------------------------------

Tamper = Callable[[EvidenceItem, EvidenceRequest, "Donors"], EvidenceItem | None]


@dataclass(frozen=True)
class Donors:
    ws: MatchWorkspace
    other_match: MatchWorkspace | None
    twin: MatchWorkspace | None


def _altered_value(item: EvidenceItem, _: EvidenceRequest, __: Donors) -> EvidenceItem | None:
    for k, v in item.facts.items():
        if isinstance(v, int | float) and not isinstance(v, bool):
            bumped = v + 3 if isinstance(v, int) else round(v + 1.5, 4)
            return item.model_copy(update={"facts": {**item.facts, k: bumped}})
    return None


def _wrong_team_item(item: EvidenceItem, _: EvidenceRequest, d: Donors) -> EvidenceItem | None:
    teams = (d.ws.info.home.club.club_id, d.ws.info.away.club.club_id)
    if item.team_id not in teams:
        return None
    other = teams[1] if item.team_id == teams[0] else teams[0]
    return item.model_copy(update={"team_id": other})


def _wrong_event(item: EvidenceItem, _: EvidenceRequest, d: Donors) -> EvidenceItem | None:
    if not item.event_ids:
        return None
    spare = next((e for e in sorted(d.ws.events) if e not in item.event_ids), None)
    if spare is None:
        return None
    return item.model_copy(update={"event_ids": (spare, *item.event_ids[1:])})


def _fabricated_event(item: EvidenceItem, _: EvidenceRequest, __: Donors) -> EvidenceItem | None:
    return item.model_copy(update={"event_ids": (*item.event_ids, "m-fabricated-000001")})


def _outside_window(item: EvidenceItem, _: EvidenceRequest, d: Donors) -> EvidenceItem | None:
    if not item.event_ids or item.span is None:
        return None
    lo, hi = item.span
    far = [k.event_id for k in d.ws.key_events if k.bin_index < lo - 15 or k.bin_index > hi + 15]
    if not far:
        return None
    return item.model_copy(update={"event_ids": (far[0],)})


def _reversed_order(item: EvidenceItem, _: EvidenceRequest, d: Donors) -> EvidenceItem | None:
    """Point the trigger at a key event that happens after the change."""
    offset = item.facts.get("nearest_offset")
    trigger = item.facts.get("nearest_event_id")
    if not isinstance(offset, int) or not isinstance(trigger, str) or item.span is None:
        return None
    anchor = item.span[1]
    later = next((k for k in d.ws.key_events if k.bin_index > anchor + 2), None)
    if later is None:
        return None
    facts = {**item.facts, "nearest_event_id": later.event_id, "nearest_offset": abs(offset) + 3}
    return item.model_copy(update={"facts": facts})


def _copied(
    donor: MatchWorkspace | None, item: EvidenceItem, request: EvidenceRequest
) -> EvidenceItem | None:
    if donor is None:
        return None
    try:
        copy = ToolBox(donor).run(request, item.evidence_id)
    except ToolError:
        return None
    return None if copy == item else copy


def _other_match(item: EvidenceItem, request: EvidenceRequest, d: Donors) -> EvidenceItem | None:
    return _copied(d.other_match, item, request)


def _from_twin(item: EvidenceItem, request: EvidenceRequest, d: Donors) -> EvidenceItem | None:
    return _copied(d.twin, item, request)


def _other_candidate(
    item: EvidenceItem, request: EvidenceRequest, d: Donors
) -> EvidenceItem | None:
    if item.tool is not T.GET_CANDIDATE_ASSESSMENT:
        return None
    own = item.arguments.get("candidate_id")
    other = next((cid for cid in d.ws.candidates if cid != own), None)
    if other is None:
        return None
    swapped = request.model_copy(update={"arguments": {"candidate_id": other}})
    result = ToolBox(d.ws).run(swapped, item.evidence_id)
    return result.model_copy(update={"arguments": item.arguments})


EVIDENCE_FAULTS: dict[str, Tamper] = {
    "altered_metric_value": _altered_value,
    "wrong_team": _wrong_team_item,
    "wrong_event_id": _wrong_event,
    "fabricated_event": _fabricated_event,
    "event_outside_window": _outside_window,
    "reversed_temporal_order": _reversed_order,
    "evidence_from_another_candidate": _other_candidate,
    "evidence_from_another_match": _other_match,
    "evidence_from_the_twin": _from_twin,
}


class TamperingToolBox(ToolBox):
    """Runs the real tool, then corrupts the result. Records which evidence IDs were corrupted."""

    def __init__(self, donors: Donors, tamper: Tamper) -> None:
        super().__init__(donors.ws)
        self.donors, self.tamper = donors, tamper
        self.tampered: set[str] = set()

    def run(self, request: EvidenceRequest, evidence_id: str) -> EvidenceItem:
        item = super().run(request, evidence_id)
        forged = self.tamper(item, request, self.donors)
        if forged is None or forged == item:
            return item
        self.tampered.add(evidence_id)
        return forged


# --- layer C: final-insight and narrative tampering ---------------------------------------------

InsightTamper = Callable[[FinalInsight, MatchWorkspace], FinalInsight | None]


def _append(final: FinalInsight, text: str) -> FinalInsight:
    lines = final.narrative.splitlines()
    lines[-1] = f"{lines[-1]} {text}"
    return final.model_copy(update={"narrative": "\n".join(lines)})


def _causal_language(final: FinalInsight, _: MatchWorkspace) -> FinalInsight | None:
    if final.verdict is Verdict.EXPLAINED:
        return None
    return _append(final, "The substitution caused the change.")


def _upgraded_strength(final: FinalInsight, _: MatchWorkspace) -> FinalInsight | None:
    if final.verdict is not Verdict.TENTATIVE:
        return None
    claims = tuple(
        c.model_copy(update={"strength": ClaimStrength.SUPPORTED}) if c.hypothesis else c
        for c in final.claims
    )
    narrative = final.narrative.replace("[verified, hypothesised]", "[verified, supported]")
    return final.model_copy(
        update={"claims": claims, "strength": ClaimStrength.SUPPORTED, "narrative": narrative}
    )


def _causal_claim_text(final: FinalInsight, _: MatchWorkspace) -> FinalInsight | None:
    if final.verdict is not Verdict.TENTATIVE or final.leading is None:
        return None
    claims = tuple(
        c.model_copy(
            update={"text": HYPOTHESIS_DESCRIPTIONS[c.hypothesis], "claim_type": ClaimType.CAUSAL}
        )
        if c.hypothesis
        else c
        for c in final.claims
    )
    return final.model_copy(update={"claims": claims})


def _removed_alternatives(final: FinalInsight, _: MatchWorkspace) -> FinalInsight | None:
    for marker in ("Not ruled out: ", "Open: "):
        if marker in final.narrative:
            head, tail = final.narrative.split(marker, 1)
            rest = tail.split(".", 1)[1] if "." in tail else ""
            narrative = f"{head}{marker}none.{rest}"
            return (
                None
                if narrative == final.narrative
                else (final.model_copy(update={"narrative": narrative}))
            )
    return None


def _invented_fact(final: FinalInsight, _: MatchWorkspace) -> FinalInsight | None:
    return _append(final, "Pressing rose by 37 per cent.")


def _player_named(final: FinalInsight, ws: MatchWorkspace) -> FinalInsight | None:
    sheet = ws.info.home if ws.info.home.club.club_id == final.team_id else ws.info.away
    return _append(final, f"Led by {sheet.bench[0].name}.") if sheet.bench else None


def _certainty(final: FinalInsight, _: MatchWorkspace) -> FinalInsight | None:
    return _append(final, "This clearly shows what happened.")


def _intent(final: FinalInsight, _: MatchWorkspace) -> FinalInsight | None:
    return _append(final, "The manager decided to change the approach.")


def _opponent_named(final: FinalInsight, ws: MatchWorkspace) -> FinalInsight | None:
    other = ws.info.away if ws.info.home.club.club_id == final.team_id else ws.info.home
    return _append(final, f"{other.club.name} were responsible.")


def _forged_elimination(final: FinalInsight, _: MatchWorkspace) -> FinalInsight | None:
    open_ = [k for k, s in final.alternatives if s is not Status.CONTRADICTED]
    if not open_:
        return None
    alternatives = tuple(
        (k, Status.CONTRADICTED if k is open_[0] else s) for k, s in final.alternatives
    )
    return final.model_copy(update={"alternatives": alternatives})


def _unverified_citation(final: FinalInsight, _: MatchWorkspace) -> FinalInsight | None:
    lead = next((c for c in final.claims if c.hypothesis), None)
    if lead is None:
        return None
    extra = next(
        (
            e.evidence_id
            for e in final.evidence
            if e.evidence_id not in lead.supporting_evidence_ids
        ),
        None,
    )
    if extra is None:
        return None
    claims = tuple(
        c.model_copy(update={"supporting_evidence_ids": (*c.supporting_evidence_ids, extra)})
        if c is lead
        else c
        for c in final.claims
    )
    return final.model_copy(update={"claims": claims})


INSIGHT_FAULTS: dict[str, InsightTamper] = {
    "unsupported_causal_language": _causal_language,
    "upgraded_strength": _upgraded_strength,
    "causal_claim_text": _causal_claim_text,
    "omitted_alternatives": _removed_alternatives,
    "invented_fact": _invented_fact,
    "unsupported_player_attribution": _player_named,
    "certainty_inflation": _certainty,
    "invented_intent": _intent,
    "opponent_attribution": _opponent_named,
    "forged_elimination": _forged_elimination,
    "unverified_citation": _unverified_citation,
}

# --- results -------------------------------------------------------------------------------------


@dataclass
class FaultTally:
    injected: int = 0
    verifier_flagged: int = 0
    auditor_flagged: int = 0
    contained: int = 0
    missed: int = 0
    text_only_flagged: int = 0
    """Layer C only: flagged by the auditor without exact template conformance."""

    @property
    def detected(self) -> int:
        return self.injected - self.missed


@dataclass
class ValidTally:
    total: int = 0
    accepted: int = 0
    hand_built_accepted: int = 0
    audit_clean: int = 0


@dataclass
class RedTeamResults:
    split: str
    seeds: int
    matches: int = 0
    faults: dict[str, dict[str, FaultTally]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(FaultTally))
    )
    clean_investigations: int = 0
    clean_quarantined: int = 0
    clean_lineage_broken: int = 0
    clean_audit_flagged: int = 0
    clean_findings: Counter[str] = field(default_factory=Counter)
    reference_assertions: int = 0
    reference_rejected: int = 0
    valid: dict[str, ValidTally] = field(default_factory=lambda: defaultdict(ValidTally))
    decoys: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    integrity: Counter[str] = field(default_factory=Counter)
    """Layer B outcomes: the integrity state of each tampered investigation, and how its final
    insight compares with the clean run."""


def integrity_outcome(clean: FinalInsight, faulty: FinalInsight) -> str:
    """How a tampered investigation's insight compares with the clean one."""
    if faulty.strength.rank > clean.strength.rank:
        return "stronger"
    if (faulty.verdict, faulty.leading, faulty.strength) == (
        clean.verdict,
        clean.leading,
        clean.strength,
    ):
        return "unchanged"
    if faulty.strength.rank < clean.strength.rank or (
        faulty.leading is None and clean.leading is not None
    ):
        return "downgraded_or_withheld"
    return "different_conclusion_same_strength"


# --- clean runs, valid cases, decoys --------------------------------------------------------------


def _clean_checks(
    ws: MatchWorkspace, records: dict[str, InvestigationRecord], results: RedTeamResults
) -> None:
    for record in records.values():
        results.clean_investigations += 1
        v = record.verification
        if v is not None:
            results.clean_quarantined += int(bool(v.quarantined))
            for verdict in v.hypotheses:
                results.reference_assertions += len(verdict.checks)
                results.reference_rejected += sum(not c.accepted for c in verdict.checks)
        results.clean_lineage_broken += int(not audit_lineage(ws, record).ok)
        report = audit_insight(ws, record.final, v)
        results.clean_audit_flagged += int(not report.ok)
        results.clean_findings.update(f.check for f in report.findings)


def hand_built(
    ws: MatchWorkspace, candidate_id: str, pool: dict[str, EvidenceItem], kind: HypothesisKind
) -> Assessment:
    """A correct assessment written differently from the reference: every assertion is an exact
    equality on the actual value, every alternative gets its true status."""
    c = ws.candidates[candidate_id]
    case = build_case_file(ws, candidate_id, "inv-valid")
    hypotheses = []
    for k in case.plausible:
        relevant = [i for i in pool.values() if irrelevance(ws, c, k, i) is None]
        support = tuple(
            FactAssertion(
                evidence_id=i.evidence_id,
                fact=r.test.fact,
                comparator=Comparator.EQ,
                value=i.facts[r.test.fact],
            )
            for g in SUPPORT_GROUPS[k]
            for r in g
            for i in relevant
            if r.tool is i.tool and r.holds(i.facts) and r.test.fact in i.facts
        )
        contra = tuple(
            FactAssertion(
                evidence_id=i.evidence_id,
                fact=r.test.fact,
                comparator=Comparator.EQ,
                value=i.facts[r.test.fact],
            )
            for r in CONTRADICTIONS[k]
            for i in relevant
            if r.tool is i.tool and r.holds(i.facts) and r.test.fact in i.facts
        )
        if k is kind:
            status = Status.SUPPORTED
        elif contra:
            status = Status.CONTRADICTED
        else:
            status = Status.PARTIALLY_SUPPORTED if support else Status.INSUFFICIENT_EVIDENCE
        hypotheses.append(
            ProposedHypothesis(
                kind=k,
                status=status,
                statement=f"Hand-built {status.value} assessment.",
                supporting=support[:20] if status is not Status.CONTRADICTED else (),
                contradicting=contra[:20],
            )
        )
    statuses = {h.kind: h.status for h in hypotheses if h.kind is not kind}
    strength = eligible_strength(c, c.strength, case.plausible, kind, statuses)
    return Assessment(hypotheses=tuple(hypotheses), leading=kind, proposed_strength=strength)


VALID_CLASSES: dict[HypothesisKind, str] = {
    H.SCORE_STATE_RESPONSE: "score_state_response",
    H.PERSONNEL_CHANGE: "personnel_change",
    H.NUMERICAL_CHANGE: "contextual_shift",
    H.FORMATION_CHANGE: "contextual_shift",
    H.OPPONENT_DRIVEN: "contextual_shift",
    H.LATE_MATCH_DECLINE: "contextual_shift",
    H.TACTICAL_CHANGE: "untriggered_hypothesis",
}


def _valid_cases(
    ws: MatchWorkspace,
    orch: Orchestrator,
    records: dict[str, InvestigationRecord],
    results: RedTeamResults,
) -> None:
    for cid, record in records.items():
        c = ws.candidates[cid]
        final, v = record.final, record.verification
        report = audit_insight(ws, final, v)
        factual_clean = not any(f.claim == 0 for f in report.findings)
        if c.strength is ClaimStrength.ASSOCIATED:
            _tally(results.valid["associated_observation"], True, factual_clean, factual_clean)
        if c.level is EvidenceLevel.STRONG and c.persistence.persistence.value == "sustained":
            _tally(results.valid["sustained_change"], True, factual_clean, factual_clean)
        if c.pattern is not None and not c.pattern.contradicted:
            _tally(results.valid["multi_signal_pattern"], True, factual_clean, factual_clean)
        if v is None:
            continue
        pool = {e.evidence_id: e for e in final.evidence}
        case = build_case_file(ws, cid, final.investigation_id)
        for kind in case.plausible:
            if kind is H.NATURAL_VARIATION or valid_explanation(ws, c, kind, pool) is None:
                continue
            verdict = next((h for h in v.hypotheses if h.kind is kind), None)
            accepted = verdict is not None and verdict.verified is Status.SUPPORTED
            built = hand_built(ws, cid, pool, kind)
            check = orch.verifier.verify(case, final.evidence, built)
            built_ok = (
                next(h for h in check.hypotheses if h.kind is kind).verified is (Status.SUPPORTED)
                and check.strength is built.proposed_strength
            )
            _tally(results.valid[VALID_CLASSES[kind]], accepted, built_ok, report.ok)
            if built.proposed_strength is ClaimStrength.HYPOTHESISED:
                _tally(results.valid["hypothesised_claim"], accepted, built_ok, report.ok)


def _tally(t: ValidTally, accepted: bool, built: bool, clean: bool) -> None:
    t.total += 1
    t.accepted += int(accepted)
    t.hand_built_accepted += int(built)
    t.audit_clean += int(clean)


def _decoys(
    case: Case, ws: MatchWorkspace, records: dict[str, InvestigationRecord], results: RedTeamResults
) -> None:
    """Raw ceiling breaches, then each breach audited: a legitimate observable consequence of the
    decoy's own event (a post-goal score-state response) is not a violation."""
    clock = Clock(case.match)
    for decoy in case.spec.decoys:
        lo, hi = clock.seconds(decoy.window_start), clock.seconds(decoy.window_end)
        key = f"{case.spec.scenario_id}/{decoy.decoy_id}"
        counts = results.decoys[key]
        counts["matches"] += 1
        above, violations = [], []
        for record in records.values():
            final = record.final
            if final.team_id != decoy.team_id or not lo <= clock.seconds(final.at) <= hi:
                continue
            if final.strength.rank <= decoy.max_claim_strength.rank:
                continue
            above.append(final)
            clean = audit_insight(ws, final, record.verification).ok
            legitimate = (
                clean
                and decoy.kind.value == "against_run_goal"
                and final.leading is H.SCORE_STATE_RESPONSE
            )
            if decoy.kind.value == "coincident_event" and final.leading is H.PERSONNEL_CHANGE:
                legitimate = False
            if not legitimate:
                violations.append(final)
            counts[f"claim:{final.leading.value if final.leading else 'none'}"] += 1
        counts["raw_above_ceiling"] += int(bool(above))
        counts["audited_violation"] += int(bool(violations))


# --- fault runs ----------------------------------------------------------------------------------


def _flagged_by_verifier(clean: InvestigationRecord, faulty: InvestigationRecord) -> bool:
    v, c = faulty.verification, clean.verification
    if v is None:
        return True
    rejected = any(not chk.accepted for h in v.hypotheses for chk in h.checks)
    return bool(v.quarantined) or rejected or set(v.downgrades) != set(c.downgrades if c else ())


def _assessment_faults(
    ws: MatchWorkspace, clean: dict[str, InvestigationRecord], results: RedTeamResults
) -> None:
    for name, mutation in ASSESSMENT_FAULTS.items():
        model = FaultyReasoner(mutation)
        orch = Orchestrator(ws, model)
        for cid, base in clean.items():
            model.applied.discard(cid)
            record = orch.investigate(cid)
            if cid not in model.applied:
                continue
            t = results.faults["A"][name]
            t.injected += 1
            verifier = _flagged_by_verifier(base, record)
            auditor = not audit_insight(ws, record.final, record.verification).ok
            escaped = _escaped(base.final, record.final)
            t.verifier_flagged += int(verifier)
            t.auditor_flagged += int(auditor)
            t.contained += int(not escaped)
            t.missed += int(escaped and not auditor)


def _evidence_faults(
    donors: Donors, clean: dict[str, InvestigationRecord], results: RedTeamResults
) -> None:
    ws = donors.ws
    for name, tamper in EVIDENCE_FAULTS.items():
        orch = Orchestrator(ws, RuleBasedReasoner())
        toolbox = TamperingToolBox(donors, tamper)
        orch.toolbox = toolbox
        for cid, base in clean.items():
            toolbox.tampered = set()
            record = orch.investigate(cid)
            if not toolbox.tampered:
                continue
            t = results.faults["B"][name]
            t.injected += 1
            v = record.verification
            quarantined = set(v.quarantined) if v else set()
            reached = toolbox.tampered & {e.evidence_id for e in record.final.evidence}
            auditor = not audit_insight(ws, record.final, v).ok
            escaped = bool(reached) or _escaped(base.final, record.final)
            t.verifier_flagged += int(toolbox.tampered <= quarantined or v is None)
            t.auditor_flagged += int(auditor)
            t.contained += int(not escaped)
            t.missed += int(escaped and not auditor)
            state = record.final.evidence_integrity.value
            outcome = integrity_outcome(base.final, record.final)
            results.integrity[f"state:{state}"] += 1
            results.integrity[f"outcome:{outcome}"] += 1
            results.integrity[f"{state}:{outcome}"] += 1


def _insight_faults(
    ws: MatchWorkspace, clean: dict[str, InvestigationRecord], results: RedTeamResults
) -> None:
    for name, tamper in INSIGHT_FAULTS.items():
        for record in clean.values():
            forged = tamper(record.final, ws)
            if forged is None or forged == record.final:
                continue
            t = results.faults["C"][name]
            t.injected += 1
            tampered = record.model_copy(update={"final": forged})
            auditor = not audit_insight(ws, forged, record.verification).ok
            lineage = not audit_lineage(ws, tampered).ok
            text_only = not audit_insight(ws, forged, record.verification, template=False).ok
            t.auditor_flagged += int(auditor or lineage)
            t.text_only_flagged += int(text_only)
            t.missed += int(not (auditor or lineage))


def evaluate_redteam(
    cases: Sequence[Case], split: str, seeds: int, fault_matches: int | None = None
) -> RedTeamResults:
    """Clean checks, valid cases and decoys on every case; faults on the first `fault_matches`
    planted cases (all if None)."""
    results = RedTeamResults(split=split, seeds=seeds)
    twins = {(c.spec.scenario_id, c.seed): c for c in cases if c.variant == "twin"}
    previous: MatchWorkspace | None = None
    injected = 0
    for case in cases:
        ws = MatchWorkspace.build(case.match)
        orch = Orchestrator(ws, RuleBasedReasoner())
        eligible = [
            c.candidate_id for c in ws.stage3.candidates if c.level.rank >= EvidenceLevel.WEAK.rank
        ]
        clean = {cid: orch.investigate(cid) for cid in eligible}
        results.matches += 1
        _clean_checks(ws, clean, results)
        _valid_cases(ws, orch, clean, results)
        if case.variant != "planted":
            continue
        _decoys(case, ws, clean, results)
        if fault_matches is not None and injected >= fault_matches:
            previous = ws
            continue
        injected += 1
        twin_case = twins.get((case.spec.scenario_id, case.seed))
        twin = MatchWorkspace.build(twin_case.match) if twin_case else None
        _assessment_faults(ws, clean, results)
        _evidence_faults(Donors(ws, previous, twin), clean, results)
        _insight_faults(ws, clean, results)
        previous = ws
    return results


def format_redteam(results: RedTeamResults) -> str:
    lines = [
        f"Stage 5 red team - {results.split} split, {results.seeds} seeds per scenario, "
        f"{results.matches} matches",
        "",
        "fault injection (injected / verifier-flagged / auditor-flagged / contained / missed):",
    ]
    names = {"A": "assessment", "B": "evidence tampering", "C": "insight/narrative tampering"}
    for layer in ("A", "B", "C"):
        tallies = results.faults.get(layer, {})
        injected = sum(t.injected for t in tallies.values())
        missed = sum(t.missed for t in tallies.values())
        lines.append(
            f"  layer {layer} - {names[layer]}: {injected - missed} of {injected} detected"
        )
        for name, t in sorted(tallies.items()):
            text = f"   text-only {t.text_only_flagged}" if layer == "C" else ""
            lines.append(
                f"    {name:<34} {t.injected:>5} {t.verifier_flagged:>6} {t.auditor_flagged:>6} "
                f"{t.contained:>6} {t.missed:>5}{text}"
            )
    n = max(results.clean_investigations, 1)
    lines += [
        "",
        f"clean reference investigations: {results.clean_investigations}",
        f"  quarantined by provenance: {results.clean_quarantined} "
        f"({results.clean_quarantined / n:.1%})",
        f"  lineage broken: {results.clean_lineage_broken}",
        f"  flagged by the auditor: {results.clean_audit_flagged} "
        f"({results.clean_audit_flagged / n:.1%}) {dict(results.clean_findings)}",
        f"  reference assertions rejected: {results.reference_rejected} of "
        f"{results.reference_assertions}",
        "",
        "valid cases (total / verifier accepted / hand-built accepted / audit clean):",
    ]
    for name, v in sorted(results.valid.items()):
        lines.append(
            f"  {name:<26} {v.total:>5} {v.accepted:>6} {v.hand_built_accepted:>6} "
            f"{v.audit_clean:>6}"
        )
    lines += ["", "decoys (matches / raw above ceiling / audited violation; claims):"]
    for key, counts in sorted(results.decoys.items()):
        claims = ", ".join(
            f"{k[6:]} {v}" for k, v in sorted(counts.items()) if k.startswith("claim:")
        )
        lines.append(
            f"  {key:<34} {counts['matches']:>4} {counts['raw_above_ceiling']:>4} "
            f"{counts['audited_violation']:>4}   {claims}"
        )
    lines += ["", "evidence integrity under layer B tampering (per tampered investigation):"]
    for key, count in sorted(results.integrity.items()):
        lines.append(f"  {key:<48} {count:>6}")
    return "\n".join(lines)
