"""Field-attributed leakage findings for model prompts.

The combined hidden-truth invariant (`Metered.leaks`) counts every forbidden term in a prompt,
whoever wrote it. This module keeps every prompt field by field and attributes each hit:

* engine-authored fields (instructions, step, case file, evidence, hypotheses under test) must
  never contain a forbidden term: a hit there is a confirmed exposure of hidden vocabulary;
* echo fields (`assessment`, `feedback`) carry text a model wrote earlier. A hit there is traced
  to the model reply that produced it. It is a lexical hit, not an information leak, only when
  that reply exists, no engine field of the investigation ever carried the term, and the term
  does not name this item's hidden truth. Anything else is unresolved.

A lexical hit and a confirmed information leak are reported as different findings, and nothing
is dropped: unresolved findings stay visible until someone resolves them.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from matcheyes.agents.llm import COMMON_RULES, ROLE_INSTRUCTIONS
from matcheyes.agents.reasoning import AgentTask, Step
from matcheyes.agents.recorded import Transcript
from matcheyes_eval.llm_eval import FORBIDDEN_VOCABULARY, Item, leaked

ENGINE_FIELDS = ("instructions", "step", "case", "evidence", "tested")
ECHO_FIELDS = ("assessment", "feedback")
CONTEXT_CHARS = 120

ENGINE_EXPOSURE = "confirmed exposure: forbidden term in engine-authored prompt content"
LEXICAL = "lexical: model-originated term echoed back; the model never received it"
UNTRACED = "unresolved: echoed term found in no model reply"
ENGINE_DERIVED = "unresolved: echoed term also present in engine-authored content"
NAMES_TRUTH = "unresolved: model text names this item's hidden truth"
UNATTRIBUTED = "unresolved: term in the prompt not attributable to a field"
RESOLVED = frozenset({LEXICAL})


def fields_of(task: Mapping[str, object]) -> dict[str, str]:
    """Every part of a prompt, by author, from an AgentTask dumped as JSON."""
    step = Step(str(task["step"]))
    fields = {"instructions": COMMON_RULES + "\n" + ROLE_INSTRUCTIONS[step]}
    for name in ("step", "case", "evidence", "tested", "assessment"):
        fields[name] = json.dumps(task.get(name), sort_keys=True)
    feedback = task.get("feedback")
    fields["feedback"] = feedback if isinstance(feedback, str) else ""
    return fields


@dataclass(frozen=True)
class Prompt:
    call: int
    step: str
    fields: dict[str, str]
    reply: str


def exchange_prompts(exchanges: Sequence[tuple[AgentTask, str]]) -> list[Prompt]:
    """Prompts of one live investigation, in call order (`Metered.exchanges`)."""
    return [
        Prompt(i, task.step.value, fields_of(task.model_dump(mode="json")), reply)
        for i, (task, reply) in enumerate(exchanges)
    ]


@dataclass(frozen=True)
class LeakFinding:
    call: int
    step: str
    field: str
    term: str
    context: str
    origin_calls: tuple[int, ...]
    verdict: str

    @property
    def resolved(self) -> bool:
        return self.verdict in RESOLVED

    @property
    def engine(self) -> bool:
        return self.verdict == ENGINE_EXPOSURE


def _context(text: str, term: str) -> str:
    i = text.lower().find(term)
    return text[max(0, i - CONTEXT_CHARS) : i + len(term) + CONTEXT_CHARS // 2]


def classify(
    prompts: Sequence[Prompt],
    forbidden: Sequence[str],
    truth: Sequence[str] = (),
    ordered: bool = True,
) -> list[LeakFinding]:
    """All findings of one investigation. With `ordered`, an echoed term must come from an
    earlier reply; recorded transcripts keep no call order, so there it may be any other reply."""
    forbidden = tuple(dict.fromkeys((*(f.lower() for f in forbidden), *FORBIDDEN_VOCABULARY)))
    truth_set = {t.lower() for t in truth}
    exposed = {t for p in prompts for f in ENGINE_FIELDS for t in leaked(p.fields[f], forbidden)}
    findings: list[LeakFinding] = []
    for p in prompts:
        attributed: set[str] = set()
        for name, text in p.fields.items():
            for term in leaked(text, forbidden):
                attributed.add(term)
                origins: tuple[int, ...] = ()
                if name in ENGINE_FIELDS:
                    verdict = ENGINE_EXPOSURE
                else:
                    origins = tuple(
                        q.call
                        for q in prompts
                        if q.call != p.call
                        and (q.call < p.call or not ordered)
                        and leaked(q.reply, (term,))
                    )
                    if not origins:
                        verdict = UNTRACED
                    elif term in exposed:
                        verdict = ENGINE_DERIVED
                    elif term in truth_set:
                        verdict = NAMES_TRUTH
                    else:
                        verdict = LEXICAL
                findings.append(
                    LeakFinding(p.call, p.step, name, term, _context(text, term), origins, verdict)
                )
        whole = " ".join(p.fields.values())
        for term in leaked(whole, forbidden):
            if term not in attributed:
                findings.append(
                    LeakFinding(p.call, p.step, "(whole prompt)", term, "", (), UNATTRIBUTED)
                )
    return findings


def transcript_findings(
    items: Sequence[Item], transcript: Transcript
) -> list[tuple[str, LeakFinding]]:
    """(namespace, finding) for every recorded investigation of these items. One namespace is one
    investigation; recorded entries keep no call order, so tracing is unordered."""
    by_id = {i.item_id: i for i in items}
    prompts: dict[str, list[Prompt]] = {}
    for entry in transcript.entries:
        request = json.loads(entry.request)
        namespace = str(request.get("namespace", ""))
        task = request.get("task")
        if namespace.split("/")[0] not in by_id or not isinstance(task, dict):
            continue
        calls = prompts.setdefault(namespace, [])
        calls.append(Prompt(len(calls), str(task.get("step")), fields_of(task), entry.response))
    out: list[tuple[str, LeakFinding]] = []
    for namespace, calls in sorted(prompts.items()):
        item = by_id[namespace.split("/")[0]]
        for finding in classify(calls, item.forbidden, item.truth, ordered=False):
            out.append((namespace, finding))
    return out


def format_findings(findings: Sequence[tuple[str, LeakFinding]], limit: int = 20) -> list[str]:
    lines = []
    for where, f in findings[:limit]:
        origin = f" from reply {', '.join(map(str, f.origin_calls))}" if f.origin_calls else ""
        lines.append(
            f"  {where} call {f.call} {f.step} [{f.field}] {f.term!r}{origin}: {f.verdict}"
        )
        if f.context:
            lines.append(f"    context: {f.context!r}")
    if len(findings) > limit:
        lines.append(f"  ... {len(findings) - limit} more")
    return lines
