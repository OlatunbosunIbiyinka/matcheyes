"""Opt-in smoke test: ONE real model-backed investigation against the configured deployment.

Skipped unless MATCHEYES_LLM_* is configured (see .env.example). Never mocked: a failure here
is a real failure of the live path and must be reported, not worked around. Run with

    uv run pytest tests/agents/test_foundry_smoke.py -s

It proves: the endpoint is reachable and accepts the strict wire schema; replies validate as wire
and contract models; the ToolBox and verifier run on the model's requests; the prompt carries no
hidden truth; no model free text reaches the presented insight; the model identity is recorded.
"""

import json
import os
import time
from dataclasses import asdict
from pathlib import Path

import pytest
from pydantic import ValidationError

from matcheyes.agents.contracts import Assessment, Challenge, InvestigationPlan, Verdict
from matcheyes.agents.llm import LLMSettings, live_model
from matcheyes.agents.reasoning import Step
from matcheyes.lifecycle.evaluate import audit_findings
from matcheyes.lifecycle.recording import MODEL_CONFIG
from matcheyes.orchestration.investigation import Orchestrator
from matcheyes_eval.leakage import classify, exchange_prompts, format_findings
from matcheyes_eval.llm_eval import Metered, truth_terms
from matcheyes_eval.stage2 import Case
from matcheyes_synth.scenarios import scenario
from tests.agents.support import RED_CARD, strongest, workspace
from tests.synth.generated import generated

SETTINGS = LLMSettings.from_env(os.environ)
CAPTURE = Path("data/local/foundry-smoke-capture.json")
"""Every prompt field by field, every reply and every finding of the run (gitignored)."""

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(SETTINGS is None, reason="live model not configured (MATCHEYES_LLM_*)"),
]

MIN_QUOTED = 12
"""Shorter fragments ("supported", a metric name) legitimately appear in templated text."""


def _free_text(step: Step, raw: str) -> list[str]:
    """Model-authored strings of one reply (none if the roles rejected the reply)."""
    try:
        if step is Step.PLAN:
            plan = InvestigationPlan.model_validate_json(raw)
            return [r.purpose for r in plan.requests]
        if step is Step.ASSESS:
            assessment = Assessment.model_validate_json(raw)
            return [assessment.summary, *(h.statement for h in assessment.hypotheses)]
        challenge = Challenge.model_validate_json(raw)
        return [*challenge.objections, *(r.purpose for r in challenge.requests)]
    except ValidationError:
        return []


def test_one_real_model_backed_investigation() -> None:
    assert SETTINGS is not None
    live = live_model(SETTINGS)
    ws = workspace(RED_CARD)
    candidate = strongest(ws)
    metered = Metered(live, [RED_CARD], time.perf_counter)
    started = time.perf_counter()
    # The configuration every model-backed path uses: the reference's 60 s deadline is shorter
    # than three hosted calls and would skip the challenge round.
    record = Orchestrator(ws, metered, MODEL_CONFIG).investigate(candidate.candidate_id)
    elapsed = time.perf_counter() - started

    final = record.final
    summary = {
        "deployment": SETTINGS.model,
        "reasoner": live.name,
        "served_models": sorted(live.usage.served),
        "calls": [
            {"step": c.step.value, "ok": c.ok, "ms": round(c.latency_ms)} for c in metered.calls
        ],
        "verdict": final.verdict.value,
        "failure": final.failure,
        "leading": final.leading.value if final.leading else None,
        "strength": final.strength.value,
        "evidence_items": len(final.evidence),
        "quarantined": list(final.quarantined),
        "downgrades": list(final.downgrades),
        "prompt_tokens": live.usage.prompt_tokens,
        "completion_tokens": live.usage.completion_tokens,
        "usage_reported": f"{live.usage.reported}/{live.usage.calls}",
        "seconds": round(elapsed, 1),
        "failed_trace": [
            f"{t.component}:{t.action}:{t.detail}" for t in record.trace if t.status == "failed"
        ],
    }
    print("\nFOUNDRY SMOKE", json.dumps(summary, indent=2))
    prompts = exchange_prompts(metered.exchanges)
    spec = scenario(RED_CARD)
    truth = truth_terms(Case(spec, spec.default_seed, "planted", generated(RED_CARD).observable))
    findings = classify(prompts, [RED_CARD], truth)
    CAPTURE.parent.mkdir(parents=True, exist_ok=True)
    CAPTURE.write_text(
        json.dumps(
            {
                "combined_prompt_hits": metered.leaks,
                "findings": [asdict(f) for f in findings],
                "prompts": [asdict(p) for p in prompts],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"combined prompt hits {metered.leaks}; capture {CAPTURE}")
    print("\n".join(format_findings([("smoke", f) for f in findings], limit=50)) or "no findings")

    # Reachable, strict schema accepted, wire + contract valid: every role produced a valid output.
    assert final.verdict is not Verdict.UNAVAILABLE, final.failure
    assert not any(t.action == "deadline" for t in record.trace)
    ok_steps = {c.step for c in metered.calls if c.ok}
    assert {Step.PLAN, Step.ASSESS, Step.CHALLENGE} <= ok_steps
    # ToolBox and verifier ran on the model's requests; the claim audit and lineage are clean.
    assert record.verification is not None
    assert final.evidence
    assert audit_findings(ws, record) == ()
    # No hidden truth reached the model: no forbidden term in any engine-authored prompt field,
    # and every hit in echoed model text traced to the model's own earlier reply, never present in
    # engine content and not naming this match's hidden truth (matcheyes_eval.leakage).
    engine = [f for f in findings if f.engine]
    unresolved = [f for f in findings if not f.engine and not f.resolved]
    assert engine == [], engine
    assert unresolved == [], unresolved
    # No model free text reaches what is presented.
    presented = " ".join(
        (final.narrative, *(c.text for c in final.claims), *(c.uncertainty for c in final.claims))
    )
    quoted = [
        text
        for call in metered.calls
        if call.ok
        for text in _free_text(call.step, call.raw)
        if len(text) >= MIN_QUOTED and text in presented
    ]
    assert quoted == []
    # Model identity is recorded.
    assert live.name == f"openai-compatible:{SETTINGS.model}"
    assert live.usage.calls == len(metered.calls)
