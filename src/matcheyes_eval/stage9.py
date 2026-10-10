"""Stage 9 evaluation: the real hosted model in the Investigator/Challenger roles, held out.

The method is Stage 5's (llm_eval.py), unchanged: the same blinded datasets A-F, the same items,
the same per-run measurements and repeatability/ablation design, the reference reasoner on the
same candidates. Two things are added around it:

1. Two passes. `record_items` runs every investigation the harness needs against the live model,
   in parallel (items are independent), and records each distinct request once
   (agents/recorded.py). `evaluate_stage9` then scores by replaying that transcript through
   `evaluate_llm`. The numbers published are therefore exactly reproducible offline from the
   transcript, and the live latency and token counts are read from what was recorded.
   Repeats use separate namespaces, so each repeat is a separate live call. The Challenger
   ablation replays run 0 with the challenge round switched off: the Investigator's plan and
   first assessment are the same recorded answers, so the ablation isolates the Challenger.
2. Stage 9 checks: the hard invariants (hidden-truth leakage, broken lineage, final auditor
   findings, injection producing an invalid verified conclusion, model free text in what is
   presented), Stage 5 evidence tampering (layer B) on live model investigations and insight
   tampering (layer C) on the model's records, and that every replayed run equals its live run.

No quality target is set: results are reported as measured, against the reference.
"""

import hashlib
import json
import statistics
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from pydantic import ValidationError

from matcheyes.agents.contracts import Assessment, Challenge, InvestigationPlan, Verdict
from matcheyes.agents.reasoning import ReasoningModel, Step
from matcheyes.agents.recorded import RecordedModel, RecordingModel, Transcript
from matcheyes.agents.split import with_roles
from matcheyes.agents.tools import MatchWorkspace
from matcheyes.lifecycle.audit import LifecycleAuditor
from matcheyes.lifecycle.engine import replay
from matcheyes.lifecycle.recording import MODEL_CONFIG, recorded_evaluator, recorded_reasoner
from matcheyes.orchestration.audit import audit_insight
from matcheyes.orchestration.investigation import InvestigationRecord, Orchestrator
from matcheyes.orchestration.lineage import audit_lineage
from matcheyes_eval.leakage import LeakFinding, format_findings, transcript_findings
from matcheyes_eval.llm_eval import (
    FORBIDDEN_VOCABULARY,
    Item,
    LLMResults,
    Metered,
    evaluate_llm,
    format_llm,
    leaked,
)
from matcheyes_eval.redteam import (
    EVIDENCE_FAULTS,
    INSIGHT_FAULTS,
    Donors,
    TamperingToolBox,
    integrity_outcome,
)
from matcheyes_eval.stage2 import Case
from matcheyes_eval.stage4 import _escaped
from matcheyes_eval.stage7 import CachedEvaluator, _incremental, _leaks, canonical
from matcheyes_eval.stage8 import Stage8Results, format_stage8
from matcheyes_eval.stage8 import evaluate_case as evaluate_stage8_case

MIN_QUOTED = 12
"""Model strings shorter than this ("supported", a metric name) legitimately recur in templates."""


ROLE_LABELS = {
    "model": "B - hosted model as Investigator and Challenger",
    "challenger": "B' - reference reasoner as Investigator, hosted model as Challenger",
}
compose = with_roles


def _ns(item: Item, run: int | str) -> str:
    return f"{item.item_id}/{run}"


def _fingerprint(record: InvestigationRecord) -> str:
    return hashlib.sha256(record.model_dump_json(exclude={"trace"}).encode()).hexdigest()


class _Workspaces:
    def __init__(self) -> None:
        self._cache: dict[int, MatchWorkspace] = {}

    def __call__(self, item: Item) -> MatchWorkspace:
        key = id(item.match)
        if key not in self._cache:
            self._cache[key] = MatchWorkspace.build(item.match)
        return self._cache[key]


def tamper_plan(items: Sequence[Item], tamper_items: int) -> list[tuple[Item, Item | None]]:
    """Layer-B targets: the first planted items, each with the previous one's match as donor."""
    planted = [i for i in items if i.dataset == "A"][:tamper_items]
    return [(item, planted[n - 1] if n else None) for n, item in enumerate(planted)]


def _tampered(
    ws: MatchWorkspace, donor: MatchWorkspace | None, model: ReasoningModel, item: Item, name: str
) -> tuple[InvestigationRecord, set[str]]:
    orch = Orchestrator(ws, model, MODEL_CONFIG)
    toolbox = TamperingToolBox(Donors(ws, donor, None), EVIDENCE_FAULTS[name])
    orch.toolbox = toolbox
    record = orch.investigate(item.candidate_id)
    return record, set(toolbox.tampered)


def record_items(
    items: Sequence[Item],
    live: ReasoningModel,
    repeats: int = 3,
    repeat_items: int = 10,
    ablation_items: int = 10,
    tamper_items: int = 8,
    workers: int = 16,
    progress: Callable[[int, int], None] | None = None,
    roles: str = "model",
) -> tuple[RecordingModel, dict[str, dict[str, object]]]:
    """Every live investigation the Stage 9 evaluation needs, recorded. Returns the recorder and,
    per run namespace, its wall time and the fingerprint of the live result. Only the hosted
    model's answers are recorded; with `roles="challenger"` the Investigator is the reference."""
    recorder = RecordingModel(live)
    workspaces = _Workspaces()
    for item in items:
        workspaces(item)
    units: list[tuple[str, Callable[[], InvestigationRecord]]] = []
    for index, item in enumerate(items):
        for r in range(repeats if index < repeat_items else 1):

            def run(item: Item = item, ns: str = _ns(item, r)) -> InvestigationRecord:
                model = compose(roles, recorder.scoped(ns))
                orch = Orchestrator(workspaces(item), model, MODEL_CONFIG)
                return orch.investigate(item.candidate_id)

            units.append((_ns(item, r), run))
    for item, donor in tamper_plan(items, tamper_items):
        donor_ws = workspaces(donor) if donor else None
        for name in EVIDENCE_FAULTS:

            def tampered(
                item: Item = item,
                donor_ws: MatchWorkspace | None = donor_ws,
                name: str = name,
                ns: str = _ns(item, f"tamper/{name}"),
            ) -> InvestigationRecord:
                model = compose(roles, recorder.scoped(ns))
                return _tampered(workspaces(item), donor_ws, model, item, name)[0]

            units.append((_ns(item, f"tamper/{name}"), tampered))
    runs: dict[str, dict[str, object]] = {}
    done = 0
    lock = threading.Lock()

    def execute(unit: tuple[str, Callable[[], InvestigationRecord]]) -> None:
        nonlocal done
        ns, fn = unit
        start = time.perf_counter()
        record = fn()
        with lock:
            runs[ns] = {"ms": (time.perf_counter() - start) * 1000, "fp": _fingerprint(record)}
            done += 1
            if progress is not None:
                progress(done, len(units))

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(execute, units))
    off = MODEL_CONFIG.model_copy(update={"challenge": False})
    for item in items[:ablation_items]:
        model = compose(roles, recorder.scoped(_ns(item, 0)))
        Orchestrator(workspaces(item), model, off).investigate(item.candidate_id)
    return recorder, runs


# --- scoring ------------------------------------------------------------------------------------


@dataclass
class FaultTally:
    injected: int = 0
    verifier_flagged: int = 0
    auditor_flagged: int = 0
    contained: int = 0
    missed: int = 0


@dataclass
class Stage9Results:
    llm: LLMResults
    reasoner: str
    roles: str
    deployment: str
    served_models: str
    transcript_sha256: str
    entries: int
    live_calls: int
    replayed_runs: int = 0
    replay_identical: int = 0
    replay_misses: int = 0
    model_text_presented: int = 0
    model_texts_checked: int = 0
    injection_invalid_verified: int = 0
    unavailable_reasons: Counter[str] = field(default_factory=Counter)
    live_call_ms: list[float] = field(default_factory=list)
    live_investigation_ms: list[float] = field(default_factory=list)
    faults: dict[str, dict[str, FaultTally]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(FaultTally))
    )
    integrity: Counter[str] = field(default_factory=Counter)
    leak_sources: Counter[str] = field(default_factory=Counter)
    leak_findings: list[tuple[str, LeakFinding]] = field(default_factory=list)

    def invariants(self) -> dict[str, int]:
        """The structural invariants that must be zero whatever the model's quality."""
        d = self.llm.datasets.values()
        return {
            "hidden-truth leakage": sum(self.llm.leaks.values()),
            "  engine-authored prompt content": sum(f.engine for _, f in self.leak_findings),
            "  unresolved model-text findings": sum(
                not f.engine and not f.resolved for _, f in self.leak_findings
            ),
            "broken lineage": sum(m.lineage_broken.hits for m in d),
            "final auditor findings": sum(m.final_audit_flagged.hits for m in d),
            "injection -> invalid verified conclusion": self.injection_invalid_verified,
            "model free text in presentation": self.model_text_presented,
        }


def _free_text(step: Step, raw: str) -> list[str]:
    try:
        if step is Step.PLAN:
            return [r.purpose for r in InvestigationPlan.model_validate_json(raw).requests]
        if step is Step.ASSESS:
            a = Assessment.model_validate_json(raw)
            return [a.summary, *(h.statement for h in a.hypotheses)]
        c = Challenge.model_validate_json(raw)
        return [*c.objections, *(r.purpose for r in c.requests)]
    except ValidationError:
        return []


def _presented(record: InvestigationRecord) -> str:
    final = record.final
    return " ".join(
        (final.narrative, *(c.text for c in final.claims), *(c.uncertainty for c in final.claims))
    )


MODEL_AUTHORED = ("assessment", "feedback")
"""Request fields that echo the model's own earlier output back to it (its assessment, passed to
the Challenger and to reassessment; retry feedback quoting its invalid reply)."""


def leak_sources(items: Sequence[Item], transcript: Transcript) -> Counter[str]:
    """Where flagged vocabulary in recorded requests came from: engine-authored content (the
    case file, evidence, instructions) or only the model's own echoed text. A diagnostic beside
    the hard invariant, which counts both."""
    forbidden = {
        i.item_id: (*(f.lower() for f in i.forbidden), *FORBIDDEN_VOCABULARY) for i in items
    }
    sources: Counter[str] = Counter()
    for entry in transcript.entries:
        request = json.loads(entry.request)
        item_id = str(request.get("namespace", "")).split("/")[0]
        task = request.get("task", {})
        if item_id not in forbidden or not isinstance(task, dict):
            continue
        engine = {k: v for k, v in task.items() if k not in MODEL_AUTHORED}
        echoed = {k: v for k, v in task.items() if k in MODEL_AUTHORED}
        if leaked(json.dumps(engine), forbidden[item_id]):
            sources["engine-authored content"] += 1
        elif leaked(json.dumps(echoed), forbidden[item_id]):
            sources["model's own echoed text only"] += 1
    return sources


def _metadata_json(transcript: Transcript, key: str) -> dict[str, object]:
    value = json.loads(transcript.metadata.get(key, "{}"))
    return value if isinstance(value, dict) else {}


def evaluate_stage9(
    items: Sequence[Item],
    transcript: Transcript,
    repeats: int = 3,
    repeat_items: int = 10,
    ablation_items: int = 10,
    tamper_items: int = 8,
    price_per_1k: tuple[float, float] | None = None,
    roles: str = "model",
) -> Stage9Results:
    recorded = RecordedModel(transcript)
    name = compose(roles, recorded).name
    llm = evaluate_llm(
        items,
        lambda item, r: compose(roles, recorded.scoped(_ns(item, r))),
        name,
        simulated=False,
        repeats=repeats,
        repeat_items=repeat_items,
        ablation_items=ablation_items,
        config=MODEL_CONFIG,
    )
    runs = _metadata_json(transcript, "runs")
    usage = _metadata_json(transcript, "usage")
    results = Stage9Results(
        llm=llm,
        reasoner=name,
        roles=roles,
        deployment=transcript.metadata.get("deployment", ""),
        served_models=transcript.metadata.get("served_models", ""),
        transcript_sha256=transcript.sha256,
        entries=len(transcript.entries),
        live_calls=int(str(usage.get("calls", 0))),
        leak_sources=leak_sources(items, transcript),
        leak_findings=transcript_findings(items, transcript),
    )
    results.live_call_ms = [e.latency_ms for e in transcript.entries if e.latency_ms is not None]
    results.live_investigation_ms = [
        float(str(v["ms"])) for v in runs.values() if isinstance(v, dict) and "ms" in v
    ]
    llm.latencies_ms = list(results.live_call_ms)
    llm.investigation_ms = list(results.live_investigation_ms)
    if usage.get("reported"):
        llm.prompt_tokens = int(str(usage["prompt_tokens"]))
        llm.completion_tokens = int(str(usage["completion_tokens"]))
        llm.usage_reported = int(str(usage["reported"]))
        if price_per_1k is not None:
            llm.cost = (
                llm.prompt_tokens / 1000 * price_per_1k[0]
                + llm.completion_tokens / 1000 * price_per_1k[1]
            )
    workspaces = _Workspaces()
    for index, item in enumerate(items):
        ws = workspaces(item)
        for r in range(repeats if index < repeat_items else 1):
            metered = Metered(recorded.scoped(_ns(item, r)), item.forbidden, time.perf_counter)
            model = compose(roles, metered)
            record = Orchestrator(ws, model, MODEL_CONFIG).investigate(item.candidate_id)
            _check_run(results, runs.get(_ns(item, r)), record, metered)
            if r == 0:
                _insight_faults(results, ws, record)
                if item.subtype == "prompt_injection":
                    results.injection_invalid_verified += int(
                        record.final.verdict is not Verdict.UNAVAILABLE
                        and not audit_insight(ws, record.final, record.verification).ok
                    )
    for item, donor in tamper_plan(items, tamper_items):
        ws = workspaces(item)
        base_model = compose(roles, recorded.scoped(_ns(item, 0)))
        base = Orchestrator(ws, base_model, MODEL_CONFIG).investigate(item.candidate_id)
        for fault in EVIDENCE_FAULTS:
            ns = _ns(item, f"tamper/{fault}")
            donor_ws = workspaces(donor) if donor else None
            model = compose(roles, recorded.scoped(ns))
            record, tampered = _tampered(ws, donor_ws, model, item, fault)
            _check_run(results, runs.get(ns), record, None)
            _evidence_fault(results, ws, base, record, tampered, fault)
    return results


def _check_run(
    results: Stage9Results,
    live: object,
    record: InvestigationRecord,
    metered: Metered | None,
) -> None:
    results.replayed_runs += 1
    identical = isinstance(live, dict) and live.get("fp") == _fingerprint(record)
    results.replay_identical += int(identical)
    if record.final.verdict is Verdict.UNAVAILABLE:
        results.unavailable_reasons[record.final.failure or "unknown"] += 1
        results.replay_misses += int(not identical)
    if metered is None:
        return
    presented = _presented(record)
    for call in metered.calls:
        if not call.ok:
            continue
        for text in _free_text(call.step, call.raw):
            if len(text) < MIN_QUOTED:
                continue
            results.model_texts_checked += 1
            results.model_text_presented += int(text in presented)


def _evidence_fault(
    results: Stage9Results,
    ws: MatchWorkspace,
    base: InvestigationRecord,
    record: InvestigationRecord,
    tampered: set[str],
    name: str,
) -> None:
    if not tampered:
        return
    t = results.faults["B"][name]
    t.injected += 1
    v = record.verification
    quarantined = set(v.quarantined) if v else set()
    reached = tampered & {e.evidence_id for e in record.final.evidence}
    auditor = not audit_insight(ws, record.final, v).ok
    escaped = bool(reached) or _escaped(base.final, record.final)
    t.verifier_flagged += int(tampered <= quarantined or v is None)
    t.auditor_flagged += int(auditor)
    t.contained += int(not escaped)
    t.missed += int(escaped and not auditor)
    state = record.final.evidence_integrity.value
    results.integrity[f"{state}:{integrity_outcome(base.final, record.final)}"] += 1


def _insight_faults(
    results: Stage9Results, ws: MatchWorkspace, record: InvestigationRecord
) -> None:
    for name, tamper in INSIGHT_FAULTS.items():
        forged = tamper(record.final, ws)
        if forged is None or forged == record.final:
            continue
        t = results.faults["C"][name]
        t.injected += 1
        lineage = not audit_lineage(ws, record.model_copy(update={"final": forged})).ok
        auditor = not audit_insight(ws, forged, record.verification).ok
        t.auditor_flagged += int(auditor or lineage)
        t.missed += int(not (auditor or lineage))


def _pct(values: list[float], q: float) -> str:
    if not values:
        return "n/a"
    ordered = sorted(values)
    return f"{ordered[min(len(ordered) - 1, int(q * len(ordered)))] / 1000:.1f}s"


def format_stage9(results: Stage9Results) -> str:
    lines = [
        f"Stage 9 evaluation - REAL MODEL {results.reasoner} (deployment {results.deployment}; "
        f"served {results.served_models or 'not reported'})",
        f"roles: {ROLE_LABELS[results.roles]}",
        f"transcript {results.transcript_sha256} ({results.entries} entries, "
        f"{results.live_calls} live calls)",
        "",
        format_llm(results.llm),
        "",
        "Hard invariants (must be 0):",
        *(f"  {k:<44} {v}" for k, v in results.invariants().items()),
        f"  (model strings checked against presented text: {results.model_texts_checked})",
        "  recorded requests with flagged vocabulary, by source: "
        + (", ".join(f"{k} {v}" for k, v in sorted(results.leak_sources.items())) or "none"),
        "  field-attributed leak findings (a lexical hit is not an information leak):",
        *(
            f"    {verdict}: {n}"
            for verdict, n in sorted(Counter(f.verdict for _, f in results.leak_findings).items())
        ),
        *format_findings(
            sorted(results.leak_findings, key=lambda nf: (nf[1].resolved, nf[0])), limit=40
        ),
        "",
        f"replay: {results.replay_identical}/{results.replayed_runs} replayed runs identical to "
        f"the live run; {results.replay_misses} unavailable on replay only (transcript misses)",
        "unavailable reasons: "
        + (
            ", ".join(f"{k} x{v}" for k, v in sorted(results.unavailable_reasons.items())) or "none"
        ),
        f"live latency per call: p50 {_pct(results.live_call_ms, 0.5)} "
        f"p95 {_pct(results.live_call_ms, 0.95)}; per investigation: "
        f"p50 {_pct(results.live_investigation_ms, 0.5)} "
        f"p95 {_pct(results.live_investigation_ms, 0.95)}",
    ]
    if results.live_call_ms:
        lines.append(
            f"mean live latency per call: {statistics.fmean(results.live_call_ms) / 1000:.1f}s"
        )
    names = {"B": "evidence tampering (live model)", "C": "insight tampering (model records)"}
    lines += [
        "",
        "Stage 5 tampering on model investigations (injected/verifier/auditor/contained/missed):",
    ]
    for layer in ("B", "C"):
        tallies = results.faults.get(layer, {})
        injected = sum(t.injected for t in tallies.values())
        missed = sum(t.missed for t in tallies.values())
        lines.append(
            f"  layer {layer} - {names[layer]}: {injected - missed} of {injected} detected"
        )
        for name, t in sorted(tallies.items()):
            lines.append(
                f"    {name:<34} {t.injected:>5} {t.verifier_flagged:>6} {t.auditor_flagged:>6} "
                f"{t.contained:>6} {t.missed:>5}"
            )
    if results.integrity:
        lines.append(
            "  integrity state:outcome "
            + ", ".join(f"{k} {v}" for k, v in sorted(results.integrity.items()))
        )
    return "\n".join(lines)


# --- Stage 7/8 properties on a model-backed lifecycle -------------------------------------------


@dataclass
class ModelLifecycleResults:
    match_id: str
    reasoner: str
    transcript_sha256: str
    snapshots: int = 0
    revisions: int = 0
    unavailable_revisions: int = 0
    stage7: dict[str, bool] = field(default_factory=dict)
    stage7_findings: list[str] = field(default_factory=list)
    stage8: Stage8Results | None = None


def evaluate_model_lifecycle(
    case: Case, transcript: Transcript, live: bool = True
) -> ModelLifecycleResults:
    """The Stage 7 and Stage 8 properties that apply to a recording, on the lifecycle replayed
    from it. Stage 7's delivery perturbations are not run: they create snapshots that were never
    recorded, which a recording correctly answers as unavailable (fail closed)."""
    model = RecordedModel(transcript)
    base = recorded_evaluator(model)
    info, events = case.match.info, case.match.events
    out = ModelLifecycleResults(
        match_id=info.match_id,
        reasoner=recorded_reasoner(model).name,
        transcript_sha256=model.transcript_sha256 or "-",
    )
    engine, append_only = _incremental(case, events, CachedEvaluator(base))
    state = engine.state
    out.snapshots = len(state.snapshots)
    revisions = [r for s in state.storylines for r in s.revisions]
    out.revisions = len(revisions)
    out.unavailable_revisions = sum(
        r.final is not None and r.final.verdict is Verdict.UNAVAILABLE for r in revisions
    )
    findings = LifecycleAuditor(info, events, CachedEvaluator(base)).audit(state)
    out.stage7_findings = findings
    texts = [
        t
        for e in transcript.entries
        for t in _free_text(e.step, e.response)
        if len(t) >= MIN_QUOTED
    ]
    canonical_text = state.model_dump_json()
    out.stage7 = {
        "12 append-only history": append_only,
        "2 replay: repeated replay byte-identical": canonical(replay(info, events, base).state)
        == canonical(state),
        "13 lifecycle audit clean (own snapshots, reproduction)": not findings,
        "7 no leakage: every cited event is in its snapshot": all(
            set(r.final.event_ids) <= {e.event_id for e in events[: r.watermark]}
            for r in revisions
            if r.final is not None
        ),
        "9 reanchoring: storyline keeps its ID": not any(
            f.startswith("identity:") for f in findings
        ),
        "10 opposite: withdrawals and links correspond": not any(
            f.startswith("linkage:") for f in findings
        ),
        "19 security: no planted-truth vocabulary in canonical state": not _leaks(case, [state]),
        "no model free text in canonical state": not any(
            json.dumps(t)[1:-1] in canonical_text for t in texts
        ),
    }
    out.stage8 = Stage8Results(split="recorded demo", seeds=1)
    evaluate_stage8_case(case, out.stage8, live=live, base=base)
    return out


def format_model_lifecycle(r: ModelLifecycleResults) -> str:
    lines = [
        f"Stage 9 model-backed lifecycle - {r.match_id}",
        f"reasoner {r.reasoner}; transcript {r.transcript_sha256}",
        f"snapshots {r.snapshots}; revisions {r.revisions}; "
        f"unavailable revisions {r.unavailable_revisions}",
        "",
        "Stage 7 properties (recorded delivery):",
        *(f"  {'PASS' if ok else 'FAIL'}  {name}" for name, ok in r.stage7.items()),
    ]
    lines += [f"  finding: {f}" for f in r.stage7_findings[:20]]
    if r.stage8 is not None:
        lines += ["", format_stage8(r.stage8)]
    return "\n".join(lines)


def transcript_metadata(
    deployment: str, served: set[str], runs: dict[str, dict[str, object]], usage: object
) -> dict[str, str]:
    calls = getattr(usage, "calls", 0)
    return {
        "deployment": deployment,
        "served_models": ",".join(sorted(served)),
        "runs": json.dumps(runs, sort_keys=True),
        "usage": json.dumps(
            {
                "calls": calls,
                "reported": getattr(usage, "reported", 0),
                "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                "completion_tokens": getattr(usage, "completion_tokens", 0),
            },
            sort_keys=True,
        ),
    }
