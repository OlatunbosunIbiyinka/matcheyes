"""Stage 4 evaluation: investigations against planted truth, twins, controls and decoys.

The answer key is used only here, only after every investigation of a match has finished, and
never reaches the agents. Planted causes are mapped to the explanation an investigator should
reach (EXPECTED_EXPLANATIONS); that mapping is evaluator knowledge, not system knowledge.

Twins differ by trigger type, so they are reported separately:

* untriggered (manager instruction): the twin has no cause at all, so the same explanation at
  HYPOTHESISED or above is a false attribution;
* observable trigger (goal, card, substitution, formation change): the twin keeps the trigger
  and the generator's ordinary response to it, so the same explanation is not wrong there;
* fatigue: background fatigue exists in every match, twins included.

Verifier effectiveness is measured by fault injection: a reasoner that corrupts the reference
assessment in a known way, checked for whether the corruption reaches the final insight.
"""

import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from matcheyes.agents.contracts import (
    Assessment,
    Comparator,
    FactAssertion,
    FinalInsight,
    HypothesisKind,
    ProposedHypothesis,
    Status,
    ToolName,
    Verdict,
)
from matcheyes.agents.hypotheses import TRIGGERED
from matcheyes.agents.reasoning import AgentTask, RuleBasedReasoner, Step
from matcheyes.analytics.analysis import MatchAnalysis, analyse_match
from matcheyes.analytics.contextual import ContextualAnalysis, analyse_contextual
from matcheyes.domain.claims import ClaimStrength
from matcheyes.orchestration.investigation import (
    InvestigationConfig,
    InvestigationRecord,
    MatchInvestigation,
    investigate_match,
)
from matcheyes_eval.scoring import Clock
from matcheyes_eval.stage2 import CONTROL_SCENARIO, Case, Rate
from matcheyes_eval.stage3 import _has_metric_mechanism, _matches
from matcheyes_synth.truth import InterventionKind, InterventionTrigger, ScenarioSpec

H = HypothesisKind
HYPOTHESISED = ClaimStrength.HYPOTHESISED.rank

EXPECTED_EXPLANATIONS: dict[InterventionKind, frozenset[HypothesisKind]] = {
    InterventionKind.PRESS_SURGE: frozenset({H.TACTICAL_CHANGE}),
    InterventionKind.DEEP_BLOCK: frozenset({H.SCORE_STATE_RESPONSE}),
    InterventionKind.FATIGUE_DECAY: frozenset({H.LATE_MATCH_DECLINE}),
    InterventionKind.RED_CARD_REORGANISATION: frozenset({H.NUMERICAL_CHANGE, H.FORMATION_CHANGE}),
    InterventionKind.IMPACT_SUBSTITUTION: frozenset({H.PERSONNEL_CHANGE}),
    InterventionKind.FORMATION_SHIFT: frozenset({H.FORMATION_CHANGE}),
}
TWIN_GROUPS: dict[InterventionTrigger, str] = {
    InterventionTrigger.MANAGER_INSTRUCTION: "untriggered",
    InterventionTrigger.GAME_STATE: "observable trigger",
    InterventionTrigger.OBSERVABLE_EVENT: "observable trigger",
    InterventionTrigger.FATIGUE: "fatigue",
}
MEASURES = ("investigated", "considered", "discovered", "supported", "any_explanation")


@dataclass
class InsightRow:
    expected: str
    twin_group: str
    planted: dict[str, Rate] = field(default_factory=lambda: defaultdict(Rate))
    twin: dict[str, Rate] = field(default_factory=lambda: defaultdict(Rate))


@dataclass
class Stage4Results:
    split: str
    seeds: int
    model: str
    insights: dict[str, InsightRow] = field(default_factory=dict)
    verdicts: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    leading: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    matches: Counter[str] = field(default_factory=Counter)
    claims_per_match: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    decoys: dict[str, Rate] = field(default_factory=lambda: defaultdict(Rate))
    decoy_leading: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    decoy_personnel: Rate = field(default_factory=Rate)
    investigations: int = 0
    not_investigated: int = 0
    proposed_statuses: int = 0
    downgraded_statuses: int = 0
    assertions: int = 0
    rejected_assertions: int = 0
    proposed_strength_reduced: int = 0
    final_unsupported: int = 0
    final_claims: int = 0
    insufficient: int = 0
    untraceable: int = 0
    temporal_violations: int = 0
    over_ceiling: int = 0
    nondeterministic: int = 0
    determinism_checked: int = 0
    faults: dict[str, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))


def _group(case: Case) -> str:
    return CONTROL_SCENARIO if case.spec.scenario_id == CONTROL_SCENARIO else case.variant


def _strong(final: FinalInsight) -> bool:
    return final.strength.rank >= HYPOTHESISED


def _audit(record: InvestigationRecord, known: set[str], results: Stage4Results) -> None:
    """Integrity checks on one investigation: these should all be zero."""
    final, verification = record.final, record.verification
    evidence_ids = {e.evidence_id for e in final.evidence}
    if not set(final.event_ids) <= known or not final.event_ids:
        results.untraceable += 1
    results.final_claims += len(final.claims)
    results.insufficient += int(final.verdict is Verdict.INSUFFICIENT_EVIDENCE)
    for claim in final.claims:
        if not set(claim.supporting_evidence_ids) <= evidence_ids:
            results.final_unsupported += 1
        if claim.claim_type.value == "causal" and claim.status is not Status.SUPPORTED:
            results.final_unsupported += 1
    if final.strength.rank > ClaimStrength.SUPPORTED.rank:
        results.over_ceiling += 1
    if verification is None:
        return
    if final.strength.rank > verification.eligible_strength.rank and final.leading not in (
        None,
        H.NATURAL_VARIATION,
    ):
        results.final_unsupported += 1
    if final.leading in TRIGGERED and not verification.gates["temporal"]:
        results.temporal_violations += 1
    for verdict in verification.hypotheses:
        if verdict.proposed is not None and verdict.proposed is not Status.INSUFFICIENT_EVIDENCE:
            results.proposed_statuses += 1
            results.downgraded_statuses += int(verdict.verified is not verdict.proposed)
        results.assertions += len(verdict.checks)
        results.rejected_assertions += sum(1 for c in verdict.checks if not c.accepted)
    if verification.strength is not verification.proposed_strength:
        results.proposed_strength_reduced += 1


def _primary(spec: ScenarioSpec, cause: str | None) -> tuple[InterventionKind, str] | None:
    intervention = next((i for i in spec.interventions if i.intervention_id == cause), None)
    if intervention is None:
        return None
    return intervention.kind, TWIN_GROUPS[intervention.trigger]


def _score_insights(
    case: Case, inv: MatchInvestigation, stage3: ContextualAnalysis, results: Stage4Results
) -> None:
    clock = Clock(case.match)
    candidates = stage3.candidate_by_id()
    for insight in case.spec.expected_insights:
        primary = _primary(case.spec, insight.primary_cause)
        if primary is None or not _has_metric_mechanism(insight):
            continue
        kind, twin_group = primary
        expected = EXPECTED_EXPLANATIONS[kind]
        key = f"{case.spec.scenario_id}/{insight.insight_id}"
        row = results.insights.setdefault(
            key, InsightRow("/".join(sorted(e.value for e in expected)), twin_group)
        )
        rates = row.planted if case.variant == "planted" else row.twin
        matching = [
            r
            for r in inv.records
            if _matches(
                insight,
                clock,
                r.final.team_id,
                candidates[r.final.candidate_id].metric,
                candidates[r.final.candidate_id].direction,
                r.final.at,
            )
        ]
        considered = any(
            v.kind in expected and v.proposed is not None
            for r in matching
            if r.verification
            for v in r.verification.hypotheses
        )
        rates["investigated"].add(bool(matching))
        rates["considered"].add(considered)
        rates["discovered"].add(
            any(r.final.leading in expected and _strong(r.final) for r in matching)
        )
        rates["supported"].add(
            any(
                r.final.leading in expected and r.final.strength is ClaimStrength.SUPPORTED
                for r in matching
            )
        )
        rates["any_explanation"].add(any(_strong(r.final) for r in matching))


def _score_decoys(case: Case, inv: MatchInvestigation, results: Stage4Results) -> None:
    clock = Clock(case.match)
    for decoy in case.spec.decoys:
        lo, hi = clock.seconds(decoy.window_start), clock.seconds(decoy.window_end)
        inside = [
            r.final
            for r in inv.records
            if r.final.team_id == decoy.team_id and lo <= clock.seconds(r.final.at) <= hi
        ]
        above = [f for f in inside if f.strength.rank > decoy.max_claim_strength.rank]
        key = f"{case.spec.scenario_id}/{decoy.decoy_id}"
        results.decoys[key].add(bool(above))
        for f in above:
            results.decoy_leading[key][f.leading.value if f.leading else "none"] += 1
        if decoy.kind.value == "coincident_event":
            results.decoy_personnel.add(
                any(f.leading is H.PERSONNEL_CHANGE and _strong(f) for f in inside)
            )


def _fingerprint(inv: MatchInvestigation) -> str:
    return json.dumps([r.final.model_dump(mode="json") for r in inv.records], sort_keys=True)


def evaluate_stage4(
    cases: Iterable[Case],
    split: str,
    seeds: int,
    config: InvestigationConfig | None = None,
    determinism_cases: int = 10,
    fault_seeds: int = 0,
) -> Stage4Results:
    config = config or InvestigationConfig()
    model = RuleBasedReasoner()
    results = Stage4Results(split=split, seeds=seeds, model=model.name)
    fault_seed_set: set[int] = set()
    for index, case in enumerate(cases):
        stage2 = analyse_match(case.match)
        stage3 = analyse_contextual(case.match, stage2)
        inv = investigate_match(case.match, model, config, stage2, stage3)
        group = _group(case)
        known = {e.event_id for e in case.match.events}
        results.matches[group] += 1
        results.investigations += len(inv.records)
        results.not_investigated += len(inv.not_investigated)
        for record in inv.records:
            final = record.final
            results.verdicts[group][final.verdict.value] += 1
            if final.leading is not None and final.verdict is not Verdict.INSUFFICIENT_EVIDENCE:
                results.leading[group][f"{final.leading.value}/{final.strength.value}"] += 1
            results.claims_per_match[group]["hypothesised+"] += int(_strong(final))
            results.claims_per_match[group]["supported"] += int(
                final.strength is ClaimStrength.SUPPORTED
            )
            _audit(record, known, results)
        _score_insights(case, inv, stage3, results)
        if case.variant == "planted":
            _score_decoys(case, inv, results)
        if index < determinism_cases:
            again = investigate_match(case.match, RuleBasedReasoner(), config, stage2, stage3)
            results.determinism_checked += 1
            results.nondeterministic += int(_fingerprint(again) != _fingerprint(inv))
        sampled = len(fault_seed_set) < fault_seeds or case.seed in fault_seed_set
        if fault_seeds and case.variant == "planted" and sampled:
            fault_seed_set.add(case.seed)
            _inject_faults(case, inv, config, results, stage2, stage3)
    return results


# --- verifier fault injection -------------------------------------------------------------

Mutation = Callable[[Assessment, AgentTask], Assessment | None]


def _with(assessment: Assessment, kind: HypothesisKind, **update: object) -> Assessment:
    hypotheses = tuple(
        h.model_copy(update=update) if h.kind is kind else h for h in assessment.hypotheses
    )
    return assessment.model_copy(update={"hypotheses": hypotheses})


def _fabricated_id(a: Assessment, _: AgentTask) -> Assessment | None:
    if a.leading is None:
        return None
    lead = next(h for h in a.hypotheses if h.kind is a.leading)
    forged = tuple(s.model_copy(update={"evidence_id": "ev-99"}) for s in lead.supporting)
    return _with(a, a.leading, supporting=forged)


_FLIP = {
    Comparator.EQ: Comparator.NE,
    Comparator.NE: Comparator.EQ,
    Comparator.GE: Comparator.LT,
    Comparator.LT: Comparator.GE,
    Comparator.LE: Comparator.GT,
    Comparator.GT: Comparator.LE,
}


def _false_value(a: Assessment, _: AgentTask) -> Assessment | None:
    if a.leading is None:
        return None
    lead = next(h for h in a.hypotheses if h.kind is a.leading)
    flipped = tuple(
        s.model_copy(update={"comparator": _FLIP[s.comparator]}) for s in lead.supporting
    )
    return _with(a, a.leading, supporting=flipped)


def _promote(a: Assessment, _: AgentTask) -> Assessment | None:
    """Promote a hypothesis the reference did not support, keeping its (weak) evidence."""
    target = next(
        (
            h
            for h in a.hypotheses
            if h.status is not Status.SUPPORTED and h.kind is not H.NATURAL_VARIATION
        ),
        None,
    )
    if target is None:
        return None
    promoted = _with(a, target.kind, status=Status.SUPPORTED, contradicting=())
    return promoted.model_copy(
        update={"leading": target.kind, "proposed_strength": ClaimStrength.SUPPORTED}
    )


def _inflate(a: Assessment, _: AgentTask) -> Assessment | None:
    if a.leading is None or a.proposed_strength is ClaimStrength.SUPPORTED:
        return None
    return a.model_copy(update={"proposed_strength": ClaimStrength.SUPPORTED})


def _drop_alternatives(a: Assessment, _: AgentTask) -> Assessment | None:
    if a.leading is None or len(a.hypotheses) < 2:
        return None
    kept = tuple(h for h in a.hypotheses if h.kind is a.leading)
    return a.model_copy(update={"hypotheses": kept, "proposed_strength": ClaimStrength.SUPPORTED})


def _hide_contradiction(a: Assessment, _: AgentTask) -> Assessment | None:
    target = next(
        (h for h in a.hypotheses if h.contradicting and h.kind is not H.NATURAL_VARIATION), None
    )
    if target is None:
        return None
    hidden = _with(a, target.kind, status=Status.SUPPORTED, contradicting=())
    return hidden.model_copy(
        update={"leading": target.kind, "proposed_strength": ClaimStrength.HYPOTHESISED}
    )


def _immaterial_support(a: Assessment, task: AgentTask) -> Assessment | None:
    if a.leading is None or a.leading in (H.NATURAL_VARIATION, H.TACTICAL_CHANGE):
        return None
    item = next((e for e in task.evidence if e.tool is ToolName.GET_CANDIDATE_ASSESSMENT), None)
    if item is None:
        return None
    trivial = FactAssertion(
        evidence_id=item.evidence_id, fact="level_rank", comparator=Comparator.GE, value=0
    )
    return _with(a, a.leading, supporting=(trivial,))


def _forged_alternatives(a: Assessment, _: AgentTask) -> Assessment | None:
    """Mark every alternative contradicted without evidence, to fake an elimination."""
    if a.leading is None:
        return None
    hypotheses = tuple(
        h
        if h.kind is a.leading
        else ProposedHypothesis(
            kind=h.kind, status=Status.CONTRADICTED, statement=h.statement, supporting=()
        )
        for h in a.hypotheses
    )
    return a.model_copy(
        update={"hypotheses": hypotheses, "proposed_strength": ClaimStrength.SUPPORTED}
    )


FAULTS: dict[str, Mutation] = {
    "fabricated_evidence_id": _fabricated_id,
    "false_assertion": _false_value,
    "unsupported_promotion": _promote,
    "inflated_strength": _inflate,
    "dropped_alternatives": _drop_alternatives,
    "hidden_contradiction": _hide_contradiction,
    "immaterial_support": _immaterial_support,
    "forged_eliminations": _forged_alternatives,
}


class FaultyReasoner:
    """The reference reasoner with one corruption applied to every assessment it can affect."""

    def __init__(self, mutation: Mutation) -> None:
        self.base = RuleBasedReasoner()
        self.mutation = mutation
        self.applied: set[str] = set()
        self.name = "faulty-reference"

    def respond(self, task: AgentTask) -> str:
        raw = self.base.respond(task)
        if task.step is not Step.ASSESS:
            return raw
        mutated = self.mutation(Assessment.model_validate_json(raw), task)
        if mutated is None:
            return raw
        self.applied.add(task.case.candidate_id)
        return mutated.model_dump_json()


def _escaped(clean: FinalInsight, faulty: FinalInsight) -> bool:
    """The fault reached the reader: a stronger claim, or a different explanation claimed."""
    if faulty.strength.rank > clean.strength.rank:
        return True
    return _strong(faulty) and faulty.leading is not None and faulty.leading is not clean.leading


def _inject_faults(
    case: Case,
    clean: MatchInvestigation,
    config: InvestigationConfig,
    results: Stage4Results,
    stage2: MatchAnalysis,
    stage3: ContextualAnalysis,
) -> None:
    baseline = {r.final.candidate_id: r.final for r in clean.records}
    for name, mutation in FAULTS.items():
        model = FaultyReasoner(mutation)
        inv = investigate_match(case.match, model, config, stage2, stage3)
        for record in inv.records:
            if record.final.candidate_id not in model.applied:
                continue
            tally = results.faults[name]
            tally[1] += 1
            tally[0] += int(not _escaped(baseline[record.final.candidate_id], record.final))


def format_stage4(results: Stage4Results) -> str:
    lines = [
        f"Stage 4 evaluation - {results.split} split, {results.seeds} seeds per scenario; "
        f"reasoning: {results.model}",
        "",
        "planted insights (metric mechanisms; primary cause): planted vs counterfactual twin",
        f"{'insight':<34} {'expected':<34} {'twin type':<19} "
        + " ".join(f"{m:>17}" for m in MEASURES),
    ]
    for key in sorted(results.insights):
        row = results.insights[key]
        cells = []
        for m in MEASURES:
            p, t = row.planted[m], row.twin[m]
            cells.append(
                f"{p.value:>7.0%} / {t.value:>5.0%}" if t.total else f"{p.value:>7.0%} /   n/a"
            )
        lines.append(
            f"{key:<34} {row.expected:<34} {row.twin_group:<19} "
            + " ".join(f"{c:>17}" for c in cells)
        )
    for group in ("untriggered", "observable trigger", "fatigue"):
        rows = [r for r in results.insights.values() if r.twin_group == group]
        if not rows:
            continue
        lines.append(f"  totals, {group}:")
        for m in ("discovered", "supported", "any_explanation"):
            p, t = Rate(), Rate()
            for r in rows:
                p.hits += r.planted[m].hits
                p.total += r.planted[m].total
                t.hits += r.twin[m].hits
                t.total += r.twin[m].total
            lines.append(f"    {m:<16} planted {p!s:>14}   twin {t!s:>14}")
    lines += ["", "claims per match (hypothesised or above / supported):"]
    for group in sorted(results.matches):
        n = results.matches[group]
        c = results.claims_per_match[group]
        lines.append(
            f"  {group:<22} matches {n:<4} hypothesised+ {c['hypothesised+'] / n:5.2f}   "
            f"supported {c['supported'] / n:5.2f}"
        )
    lines += ["", "verdicts:"]
    for group in sorted(results.verdicts):
        counts = results.verdicts[group]
        total = sum(counts.values())
        parts = ", ".join(f"{k} {v / total:.0%}" for k, v in sorted(counts.items()))
        lines.append(f"  {group:<22} n={total:<5} {parts}")
    lines += ["", "leading explanations claimed (explanation/strength):"]
    for group in sorted(results.leading):
        top = results.leading[group].most_common(8)
        lines.append(f"  {group:<22} " + ", ".join(f"{k} {v}" for k, v in top))
    lines += ["", "decoys (share of matches with a claim above the decoy ceiling, team in window):"]
    for key in sorted(results.decoys):
        claims = ", ".join(f"{k} {v}" for k, v in results.decoy_leading[key].most_common())
        lines.append(f"  {key:<34} {results.decoys[key]!s:>14}   {claims}")
    lines.append(f"  S08 personnel attributed at hypothesised+: {results.decoy_personnel!s:>14}")
    inv = max(results.investigations, 1)
    lines += [
        "",
        f"investigations {results.investigations}; candidates below WEAK not investigated "
        f"{results.not_investigated}",
        f"proposed statuses downgraded by the verifier: {results.downgraded_statuses} of "
        f"{results.proposed_statuses} "
        f"({results.downgraded_statuses / max(results.proposed_statuses, 1):.1%})",
        f"cited assertions rejected: {results.rejected_assertions} of {results.assertions}",
        f"proposed strength reduced: {results.proposed_strength_reduced} "
        f"({results.proposed_strength_reduced / inv:.1%} of investigations)",
        f"unsupported-claim rate: {results.final_unsupported} of {results.final_claims} final "
        f"claims ({results.final_unsupported / max(results.final_claims, 1):.1%})",
        f"insufficient-evidence rate: {results.insufficient} of {results.investigations} "
        f"investigations ({results.insufficient / inv:.1%})",
        f"final unsupported claims: {results.final_unsupported}; untraceable: "
        f"{results.untraceable}; temporal violations: {results.temporal_violations}; "
        f"above ceiling: {results.over_ceiling}",
        f"determinism: {results.nondeterministic} of {results.determinism_checked} re-runs differ",
    ]
    if results.faults:
        lines += ["", "verifier fault injection (corruptions kept out of the final insight):"]
        for name in FAULTS:
            caught, total = results.faults.get(name, [0, 0])
            rate = f"{caught / total:.1%}" if total else "n/a"
            lines.append(f"  {name:<24} {caught:>5} / {total:<5} {rate:>7}")
    return "\n".join(lines)
