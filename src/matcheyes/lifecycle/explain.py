"""Show your work: the structured record behind one revision of a storyline.

For a revision, the explainer rebuilds the revision's own snapshot, re-runs its investigation with
the same reasoner and configuration (for a recorded model: from the transcript; nothing live), and
runs the reference reasoner on the same candidate. It reports:

* the Stage 3 signal, the hypotheses the reasoner assessed and those the Challenger added;
* every evidence request (who asked, which tool, which arguments, what happened) and every result;
* for each hypothesis, the proposed status against the verified one, with every assertion check;
* the verifier's gates, downgrades, evidence integrity and quarantine; the claim audit findings;
* the reasoner's identity (and transcript hash), whether the re-run reproduced the revision, and
  the reference reasoner's verdict on the same snapshot.

It carries no model-written text: statements, summaries, objections and request purposes stay in
the transcript. Narrative and claim text are the deterministic templates of the verified insight.
The verifier's decision is the authority here as everywhere; a model proposal is shown only next
to what the verifier made of it.
"""

import json
import threading
from collections import OrderedDict
from typing import Literal

from pydantic import Field

from matcheyes.agents.contracts import AssertionCheck, Fact, FinalInsight, VerificationResult
from matcheyes.agents.reasoning import ReasoningModel, RuleBasedReasoner
from matcheyes.agents.tools import MatchWorkspace
from matcheyes.domain.base import DomainModel
from matcheyes.domain.match import ObservableMatch
from matcheyes.lifecycle.contracts import Revision
from matcheyes.lifecycle.recording import match_snapshots
from matcheyes.lifecycle.snapshot import Snapshot
from matcheyes.orchestration.investigation import (
    InvestigationConfig,
    InvestigationRecord,
    Orchestrator,
)
from matcheyes.personalization.contracts import fingerprint

CACHE_LIMIT = 128
Tone = Literal["verified", "proposed", "info", "warning"]
UNKNOWN = "(not a fact of this evidence)"
ROLES = frozenset({"investigator", "challenger"})
TOOL_DETAILS = {
    "already fetched": "already fetched",
    "request_id reused with new arguments": "request id reused with new arguments",
    "tool budget exhausted": "tool budget exhausted",
}
"""Fixed trace details that are safe to show; anything else (a toolbox refusal can quote the
requested arguments) is replaced by a fixed phrase."""


class ReasonerIdentity(DomainModel):
    """Who reasoned. Presentation metadata: never part of any insight, identity or fingerprint."""

    kind: Literal["reference", "recorded-model"]
    name: str
    deployment: str | None = None
    served_models: str | None = None
    transcript_sha256: str | None = None


REFERENCE = ReasonerIdentity(kind="reference", name=RuleBasedReasoner().name)


class ToolCallView(DomainModel):
    requested_by: str
    tool: str | None
    inputs: dict[str, int | float | str]
    status: str
    evidence_id: str | None
    detail: str


class CheckView(DomainModel):
    role: str
    evidence_id: str
    fact: str
    comparator: str
    value: Fact
    accepted: bool
    reason: str


class HypothesisView(DomainModel):
    kind: str
    proposed: str | None
    verified: str
    added_by_challenger: bool
    checks: tuple[CheckView, ...]
    notes: tuple[str, ...]


class EvidenceView(DomainModel):
    evidence_id: str
    tool: str
    arguments: dict[str, int | float | str]
    facts: dict[str, Fact]
    events: int
    summary: str


class Outcome(DomainModel):
    verdict: str
    leading: str | None
    strength: str


class RevisionExplanation(DomainModel):
    match_id: str
    storyline_id: str
    revision: int
    snapshot_id: str
    minute: str
    state: str
    change_kinds: tuple[str, ...]
    withdrawal_reason: str | None
    reasoner: ReasonerIdentity
    stage3_level: str | None = None
    outcome: Outcome | None = None
    narrative: str | None = None
    plausible: tuple[str, ...] = ()
    hypotheses: tuple[HypothesisView, ...] = ()
    challenger_added: tuple[str, ...] = ()
    tool_calls: tuple[ToolCallView, ...] = ()
    evidence: tuple[EvidenceView, ...] = ()
    proposed_strength: str | None = None
    eligible_strength: str | None = None
    gates: dict[str, bool] = Field(default_factory=dict)
    downgrades: tuple[str, ...] = ()
    evidence_integrity: str | None = None
    quarantined: tuple[str, ...] = ()
    audit_findings: tuple[str, ...] = ()
    model_attempts: int = 0
    model_retries: int = 0
    model_failures: int = 0
    reproduced: bool | None = None
    reference: Outcome | None = None
    agrees_with_reference: bool | None = None


class WorkRow(DomainModel):
    """One presentation row of an explanation. `tone` ranks authority: the verifier's decision is
    `verified`; a reasoner's proposal is `proposed` and is always shown below it."""

    section: str
    label: str
    text: str
    tone: Tone


def _value(value: Fact) -> str:
    return "-" if value is None else json.dumps(value)


def _outcome_text(o: Outcome) -> str:
    return f"{o.verdict}, leading {o.leading or 'none'}, strength {o.strength}"


def work_rows(e: RevisionExplanation) -> tuple[WorkRow, ...]:
    """The explanation as rows, in authority order: lifecycle, verified result, then what the
    reasoner proposed and requested, then identity. Deterministic; no model-written text."""
    rows: list[WorkRow] = []

    def add(section: str, label: str, text: str, tone: Tone = "info") -> None:
        rows.append(WorkRow(section=section, label=label, text=text, tone=tone))

    life = "Lifecycle"
    add(life, "Revision", f"{e.revision} ({e.state}) as of {e.minute}, snapshot {e.snapshot_id}")
    add(life, "Changes", ", ".join(e.change_kinds) or "none")
    if e.withdrawal_reason:
        add(life, "Withdrawn", e.withdrawal_reason, "warning")
    for finding in e.audit_findings:
        add(life, "Audit finding", finding, "warning")
    if e.outcome is None:
        add(life, "Insight", "no verified insight on this revision")
    else:
        result = "Verified result"
        add(result, "Outcome", _outcome_text(e.outcome), "verified")
        if e.narrative:
            add(result, "Verified text", e.narrative, "verified")
        add(result, "Strength", f"eligible {e.eligible_strength}", "verified")
        for gate, passed in sorted(e.gates.items()):
            add(result, "Gate", f"{gate}: {'passed' if passed else 'failed'}", "verified")
        for downgrade in e.downgrades:
            add(result, "Downgrade", downgrade, "warning")
        integrity = e.evidence_integrity or "-"
        add(result, "Evidence integrity", integrity, "info" if integrity == "intact" else "warning")
        for evidence_id in e.quarantined:
            add(result, "Quarantined", evidence_id, "warning")
        if e.stage3_level:
            add(result, "Stage 3 signal", e.stage3_level)
        if e.plausible:
            add(result, "Plausible", ", ".join(e.plausible), "verified")
        considered = "Explanations considered"
        for h in e.hypotheses:
            origin = " (added by the Challenger)" if h.added_by_challenger else ""
            add(considered, h.kind + origin, f"verified {h.verified}", "verified")
            add(considered, "proposed", h.proposed or "not assessed", "proposed")
            for c in h.checks:
                verdict = "accepted" if c.accepted else "rejected"
                add(
                    considered,
                    f"{c.role} check, {verdict}",
                    f"{c.evidence_id} {c.fact} {c.comparator} {_value(c.value)}: {c.reason}",
                    "verified" if c.accepted else "warning",
                )
        if e.proposed_strength:
            add(considered, "proposed strength", e.proposed_strength, "proposed")
        requested = "Evidence requested"
        for t in e.tool_calls:
            args = ", ".join(f"{k}={_value(v)}" for k, v in sorted(t.inputs.items()))
            ref = f" -> {t.evidence_id}" if t.evidence_id else ""
            add(requested, t.requested_by, f"{t.tool or '-'}({args}): {t.detail}{ref}")
        returned = "Evidence returned"
        for ev in e.evidence:
            add(returned, ev.evidence_id, f"{ev.tool}: {ev.summary} ({ev.events} event(s))")
    who = "Reasoner"
    r = e.reasoner
    add(who, "Kind", r.kind)
    add(who, "Name", r.name)
    if r.deployment:
        add(who, "Deployment", r.deployment)
    if r.served_models:
        add(who, "Served model", r.served_models)
    if r.transcript_sha256:
        add(who, "Transcript sha256", r.transcript_sha256)
    if e.outcome is not None:
        attempts = f"{e.model_attempts} attempt(s), {e.model_retries} retried, "
        add(who, "Attempts", attempts + f"{e.model_failures} failed")
        add(
            who,
            "Reproduced",
            "yes" if e.reproduced else "no",
            "info" if e.reproduced else "warning",
        )
        if e.reference is not None:
            add(who, "Reference reasoner", _outcome_text(e.reference))
            agree = "yes" if e.agrees_with_reference else "no"
            add(who, "Agrees with reference", agree)
    return tuple(rows)


def _outcome(final: FinalInsight) -> Outcome:
    return Outcome(
        verdict=final.verdict.value,
        leading=final.leading.value if final.leading else None,
        strength=final.strength.value,
    )


def _check(c: AssertionCheck, facts: dict[str, dict[str, Fact]]) -> CheckView:
    known = c.assertion.fact in facts.get(c.assertion.evidence_id, {})
    shown = c.accepted or (known and not isinstance(c.assertion.value, str))
    reason = c.reason
    if not known and " reports no fact " in reason:
        reason = reason.split(" reports no fact ")[0] + " reports no such fact"
    return CheckView(
        role=c.role,
        evidence_id=c.assertion.evidence_id,
        fact=c.assertion.fact if known else UNKNOWN,
        comparator=c.assertion.comparator.value,
        value=c.assertion.value if shown else None,
        accepted=c.accepted,
        reason=reason,
    )


def _hypotheses(
    v: VerificationResult, added: set[str], final: FinalInsight
) -> tuple[HypothesisView, ...]:
    facts = {e.evidence_id: e.facts for e in final.evidence}
    return tuple(
        HypothesisView(
            kind=h.kind.value,
            proposed=h.proposed.value if h.proposed else None,
            verified=h.verified.value,
            added_by_challenger=h.kind.value in added,
            checks=tuple(_check(c, facts) for c in h.checks),
            notes=h.notes,
        )
        for h in v.hypotheses
    )


def _tool_calls(record: InvestigationRecord) -> tuple[ToolCallView, ...]:
    views = []
    for t in record.trace:
        if t.action != "tool_call":
            continue
        by = t.detail.removeprefix("requested by ").split(";")[0].split(":")[0]
        ok = t.status == "ok"
        views.append(
            ToolCallView(
                requested_by=by if by in ROLES else "-",
                tool=t.tool,
                inputs=t.inputs if ok else {},
                status=t.status,
                evidence_id=t.result_ref,
                detail="evidence returned"
                if ok
                else TOOL_DETAILS.get(t.detail, "refused by the toolbox"),
            )
        )
    return tuple(views)


class Explainer:
    """Explains revisions of one match. Thread-safe; results are cached by revision."""

    def __init__(
        self,
        match: ObservableMatch,
        model: ReasoningModel,
        identity: ReasonerIdentity,
        config: InvestigationConfig | None = None,
    ) -> None:
        self.match = match
        self.model = model
        self.identity = identity
        self.config = config or InvestigationConfig()
        self._lock = threading.Lock()
        self._snapshots: dict[str, Snapshot] | None = None
        self._cache: OrderedDict[tuple[str, int], RevisionExplanation] = OrderedDict()

    def _snapshot(self, snapshot_id: str) -> Snapshot | None:
        if self._snapshots is None:
            self._snapshots = {s.header.snapshot_id: s for s in match_snapshots(self.match)}
        return self._snapshots.get(snapshot_id)

    def __call__(self, revision: Revision) -> RevisionExplanation:
        key = (revision.storyline_id, revision.number)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            explanation = self._explain(revision)
            self._cache[key] = explanation
            if len(self._cache) > CACHE_LIMIT:
                self._cache.popitem(last=False)
            return explanation

    def _explain(self, revision: Revision) -> RevisionExplanation:
        base = RevisionExplanation(
            match_id=self.match.info.match_id,
            storyline_id=revision.storyline_id,
            revision=revision.number,
            snapshot_id=revision.snapshot_id,
            minute=revision.as_of.display_minute,
            state=revision.state.value,
            change_kinds=tuple(k.value for k in revision.change_kinds),
            withdrawal_reason=revision.withdrawal_reason.value
            if revision.withdrawal_reason
            else None,
            reasoner=self.identity,
            audit_findings=revision.audit_findings,
        )
        final, snapshot = revision.final, self._snapshot(revision.snapshot_id)
        if final is None or snapshot is None:
            return base
        ws = MatchWorkspace.build(snapshot.match)
        record = Orchestrator(ws, self.model, self.config).investigate(final.candidate_id)
        reference = Orchestrator(ws, RuleBasedReasoner()).investigate(final.candidate_id)
        added = {
            t.hypothesis
            for t in record.trace
            if t.component == "challenger" and t.action == "alternative" and t.hypothesis
        }
        attempts = [t for t in record.trace if t.component in ("investigator", "challenger")]
        attempts = [t for t in attempts if t.action in ("plan", "assess", "reassess", "challenge")]
        update: dict[str, object] = {
            "stage3_level": final.stage3_level,
            "outcome": _outcome(final),
            "narrative": final.narrative,
            "challenger_added": tuple(sorted(added)),
            "tool_calls": _tool_calls(record),
            "evidence": tuple(
                EvidenceView(
                    evidence_id=e.evidence_id,
                    tool=e.tool.value,
                    arguments=e.arguments,
                    facts=e.facts,
                    events=len(e.event_ids),
                    summary=e.summary,
                )
                for e in final.evidence
            ),
            "model_attempts": len(attempts),
            "model_retries": sum(t.status == "retry" for t in attempts),
            "model_failures": sum(t.status == "failed" for t in attempts),
            "reproduced": fingerprint(record.final) == revision.insight_fingerprint,
            "reference": _outcome(reference.final),
            "agrees_with_reference": (final.verdict, final.leading, final.strength)
            == (reference.final.verdict, reference.final.leading, reference.final.strength),
            "evidence_integrity": final.evidence_integrity.value,
            "quarantined": final.quarantined,
        }
        v = revision.verification
        if v is not None:
            update |= {
                "plausible": tuple(k.value for k in v.plausible),
                "hypotheses": _hypotheses(v, added, final),
                "proposed_strength": v.proposed_strength.value,
                "eligible_strength": v.eligible_strength.value,
                "gates": dict(v.gates),
                "downgrades": v.downgrades,
            }
        return base.model_copy(update=update)
